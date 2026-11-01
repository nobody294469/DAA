"""Claim-level human review service with inline re-verification (PRODUCTIZATION_SPEC §7.3, PD-002, PD-010).

Authority:
  - PRODUCTIZATION_SPEC.md §4.3 (claims, review_actions, verification_results logical models)
  - PRODUCTIZATION_SPEC.md §7.3 (Reviewer Actions, Claim versioning, Inline re-verification)
  - PRODUCTIZATION_DECISIONS.md PD-002 (Mode 0: 100% human review, no autonomous export)
  - PRODUCTIZATION_DECISIONS.md PD-004 / PD-009 (Four-Eyes principle: distinct actors for high-risk claims)
  - PRODUCTIZATION_DECISIONS.md PD-010 (Immutable claim history: edits create new Claim versions)
  - PRODUCTIZATION_DECISIONS.md PD-012 (Append-only hash-chained audit logging, fail-closed atomic)
  - phase523_milestone420_review_contract.md (Canonical state machine, Four-Eyes, Gate 9 binding)
  - phase523_milestone4201_audit_integrity_addendum.md (Offline HMAC trust anchor, canonical envelope)

Core Invariants:
  1. Immutable claim history: Existing claims are never overwritten in-place. Edits create
     a new Claim version linked via supersedes_claim_id, incrementing version, setting
     origin='reviewer' and preserving evidence linkage.
  2. Optimistic locking: Updates validate expected_version against current Claim.version
     to prevent concurrent reviewers from silently overwriting each other.
  3. Inline re-verification: Edited claims immediately execute the existing VerificationCascade
     (Stages A/B only, unless explicit escalation criteria are met), persisting the new
     VerificationResult.
  4. Triage re-evaluation: Edited claims re-run the Mode 0 TriagePolicy engine. The edited
     claim does not become automatically accepted; its verification and triage verdict
     are respected, maintaining review_state=PENDING under Mode 0.
  5. State lifecycle validation: Enforces valid state transitions:
     - PENDING -> ACCEPTED (ACCEPT)
     - PENDING -> EDITED (EDIT; creates new version in PENDING)
     - PENDING -> REJECTED (REJECT)
     - PENDING -> ESCALATED (ESCALATE)
     - ACCEPTED / REJECTED / ESCALATED -> PENDING (REOPEN)
     - ESCALATED -> ACCEPTED / REJECTED / EDITED (Supervisory resolution)
     All other transitions are rejected.
  6. Audit integration: Every action creates a ReviewAction record and appends a PHI-free
     AuditEvent using AuditService within the same atomic transaction. Audit failures
     force a rollback of all review mutations.
  7. Four-Eyes separation of duties: Enforces that the same clinician who edits a claim cannot
     accept the edited successor claim. High-risk claims enforce reviewer != operator/creator/modifier.
  8. Offline JSONL audit ledger: Supports RFC 8785 canonical event envelopes, SHA-256 hash chaining,
     and HMAC-protected checkpoints with independent key storage.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
import uuid

from sqlalchemy.orm import Session

from clindoc.api.audit.service import (
    AuditService,
    AuditServiceError,
    compute_audit_event_hash,
    validate_metadata_phi_free,
)
from clindoc.audit.locking import (
    AuditFileLock,
    atomic_write_file,
    flush_and_fsync,
    locked_audit_ledger,
)
from clindoc.audit.projection import (
    AuditLedgerMissingError,
    AuditLedgerTamperedError,
    AuditProjectionEngine,
    ProjectionResult,
    SplitBrainLedgerError,
)
from clindoc.db.enums import (
    ClaimOrigin,
    ClaimReviewState,
    ContradictionType as DBContradictionType,
    ReviewActionType,
    VerificationLabel as DBVerificationLabel,
    VerificationStage as DBVerificationStage,
)
from clindoc.db.models import (
    AuditEvent,
    Claim,
    Document,
    Evidence,
    ReviewAction,
    TriagePolicy,
    User,
    UserRole,
    VerificationResult as DBVerificationResult,
)
from clindoc.extract.contracts import (
    BBoxModel,
    Claim as ContractClaim,
    Evidence as ContractEvidence,
)
from clindoc.review.triage import (
    ClaimTriageInput,
    ClaimTriageResult,
    TriageEngine,
)
from clindoc.verify.cascade import VerificationCascade

logger = logging.getLogger(__name__)


# ─── EXCEPTIONS ───────────────────────────────────────────────────────────────

class ClaimReviewServiceError(Exception):
    """Base exception for claim review service operations."""


class ClaimNotFoundError(ClaimReviewServiceError, KeyError):
    """Raised when a specified claim does not exist in the database."""


class InvalidStateTransitionError(ClaimReviewServiceError, ValueError):
    """Raised when an action is illegal from the claim's current review_state."""


class OptimisticLockError(ClaimReviewServiceError, ValueError):
    """Raised when the expected version does not match the current claim version."""


class MissingReasonCodeError(ClaimReviewServiceError, ValueError):
    """Raised when an action omits a required reason code."""


class AuditIntegrationError(ClaimReviewServiceError):
    """Raised when audit event persistence fails, causing review rollback."""


class UnauthorizedReviewerError(ClaimReviewServiceError, PermissionError):
    """Raised when an actor lacks authority, is inactive, or lacks required review roles."""


class FourEyesViolationError(ClaimReviewServiceError, PermissionError):
    """Raised when a reviewer is also the creator, modifier, or operator of a high-risk claim."""


class CorruptedClaimAncestryError(ClaimReviewServiceError, ValueError):
    """Raised when claim ancestry is broken, non-contiguous, or corrupted."""


class BranchingProhibitedError(ClaimReviewServiceError, ValueError):
    """Raised when a non-linear branch or multiple superseding children are detected."""


class ActorProvenanceMismatchError(ClaimReviewServiceError, PermissionError):
    """Raised when reviewer actor identity fails equivalence or presence checks."""


class UnresolvableActorError(ClaimReviewServiceError, PermissionError):
    """Raised when an actor cannot be resolved to a valid User in the database."""


class UnverifiableHistoricalActorError(ClaimReviewServiceError, PermissionError):
    """Raised when a historical deactivated user lacks verifiable immutable audit provenance."""


class DeactivatedUserActionError(UnauthorizedReviewerError):
    """Raised when a deactivated user attempts to perform a review action."""


class InvalidClaimLifecycleStateError(InvalidStateTransitionError):
    """Raised when an operation targets a non-leaf or illegally state-transitioned claim."""


class StaleDocumentVersionError(ClaimReviewServiceError, ValueError):
    """Raised when a review action references a stale document content hash."""


class AuditLedgerVerificationError(ClaimReviewServiceError, ValueError):
    """Base error for audit ledger corruption, tampering, or integrity failure."""


class AuditLedgerTamperDetectedError(AuditLedgerVerificationError):
    """Raised when hash-chain or HMAC signature verification indicates tampering."""


class AuditAnchorMissingError(AuditLedgerVerificationError):
    """Raised when an expected HMAC anchor or secret key is missing."""


class SequenceGapError(AuditLedgerVerificationError):
    """Raised when sequence numbers contain a gap or are not strictly monotonic."""


class DuplicateAuditEventError(AuditLedgerVerificationError):
    """Raised when duplicate sequence numbers or duplicate event hashes are encountered."""


# ─── HIGH-RISK CRITERIA & ROLE DEFINITIONS ───────────────────────────────────

HIGH_POTENCY_DRUGS: frozenset[str] = frozenset({
    "heparin", "warfarin", "enoxaparin", "apixaban", "rivaroxaban", "dabigatran",
    "insulin", "morphine", "fentanyl", "oxycodone", "hydromorphone", "methadone",
    "doxorubicin", "cisplatin", "paclitaxel", "methotrexate", "fluorouracil"
})

AUTHORIZED_REVIEWER_ROLES: frozenset[str] = frozenset({
    "reviewer", "approver", "admin", "clinical_reviewer", "chief_medical_officer"
})

SUPERVISORY_ROLES: frozenset[str] = frozenset({
    "approver", "admin", "supervisory_reviewer", "chief_medical_officer"
})


# ─── CANONICAL SERIALIZATION & HASH ENVELOPE (RFC 8785) ─────────────────────

def serialize_canonical_json(obj: Any) -> bytes:
    """Serialize Python object to RFC 8785 Canonical JSON bytes (sorted keys, no whitespace)."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def build_canonical_event_envelope(
    *,
    sequence_number: int,
    timestamp: datetime | str,
    actor_id: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    action: str,
    metadata: Optional[dict[str, Any]] = None,
    previous_event_hash: Optional[str] = None,
) -> dict[str, Any]:
    """Construct the standardized 8-key canonical event envelope (Addendum §3.1)."""
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            ts_utc = timestamp.replace(tzinfo=timezone.utc)
        else:
            ts_utc = timestamp.astimezone(timezone.utc)
        ts_iso = ts_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        ts_iso = str(timestamp).strip()

    clean_metadata = copy.deepcopy(metadata) if metadata is not None else {}
    validate_metadata_phi_free(clean_metadata)

    return {
        "action": str(action).strip(),
        "actor_id": str(actor_id).strip(),
        "entity_id": str(entity_id).strip(),
        "entity_type": str(entity_type).strip(),
        "event_type": str(event_type).strip(),
        "metadata": clean_metadata,
        "previous_event_hash": str(previous_event_hash or "").strip(),
        "sequence_number": int(sequence_number),
        "timestamp": ts_iso,
    }


def compute_canonical_envelope_hash(envelope: dict[str, Any]) -> str:
    """Compute the SHA-256 hash over the canonical JSON bytes of the envelope."""
    payload_bytes = serialize_canonical_json(envelope)
    return hashlib.sha256(payload_bytes).hexdigest()


def build_checkpoint_payload(
    *,
    document_id: str,
    document_sha256: str,
    checkpoint_sequence: int,
    terminal_hash: str,
    key_id: str,
    timestamp: Optional[datetime] = None,
) -> dict[str, Any]:
    """Construct the canonical checkpoint payload envelope."""
    ts = timestamp or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {
        "checkpoint_sequence": int(checkpoint_sequence),
        "document_id": str(document_id).strip(),
        "document_sha256": str(document_sha256).strip(),
        "key_id": str(key_id).strip(),
        "terminal_hash": str(terminal_hash).strip(),
        "timestamp": ts_iso,
    }


def compute_checkpoint_signature(payload: dict[str, Any], key_bytes: bytes) -> str:
    """Compute the HMAC-SHA256 signature over the canonical JSON bytes of the checkpoint payload."""
    payload_bytes = serialize_canonical_json(payload)
    return hmac.new(key_bytes, payload_bytes, hashlib.sha256).hexdigest()


# ─── DATA STRUCTURES ──────────────────────────────────────────────────────────

@dataclass
class EditClaimResult:
    """Encapsulates the output of an EDIT action on a clinical claim."""

    new_claim: Claim
    previous_claim: Claim
    verification_result: DBVerificationResult
    triage_result: ClaimTriageResult
    review_action: ReviewAction


@dataclass
class VerifiedClaimReviewState:
    """Verified claim review verdict extracted from a cryptographically valid audit ledger."""

    claim_id: str
    review_state: str
    action: str
    actor_id: str
    claim_version: int
    document_id: str
    document_sha256: str
    reason_code: Optional[str] = None
    sequence_number: int = 1
    timestamp: Optional[str] = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LedgerVerificationResult:
    """Structured result of an audit ledger hash chain verification sweep."""

    is_valid: bool
    total_events: int = 0
    terminal_hash: Optional[str] = None
    verified_claims: dict[str, VerifiedClaimReviewState] = field(default_factory=dict)
    error_message: Optional[str] = None
    is_unanchored_dev_mode: bool = False
    checkpoint_verified: bool = False


# ─── OFFLINE JSONL AUDIT LEDGER ──────────────────────────────────────────────

class ReviewAuditLedger:
    """Offline JSONL audit ledger with RFC 8785 canonical serialization and HMAC checkpoints."""

    def __init__(
        self,
        ledger_path: Union[str, Path],
        checkpoint_path: Optional[Union[str, Path]] = None,
        document_id: Optional[str] = None,
        document_sha256: Optional[str] = None,
        signing_key_id: str = "default",
        keyring: Optional[dict[str, bytes]] = None,
        hmac_key: Optional[bytes] = None,
    ) -> None:
        self.ledger_path = Path(ledger_path)
        if checkpoint_path is not None:
            self.checkpoint_path = Path(checkpoint_path)
        else:
            self.checkpoint_path = self.ledger_path.with_suffix(".chkpt")
        self.document_id = str(document_id) if document_id else ""
        self.document_sha256 = str(document_sha256) if document_sha256 else ""
        self.signing_key_id = signing_key_id
        self.keyring: dict[str, bytes] = dict(keyring or {})
        if hmac_key:
            self.keyring[signing_key_id] = hmac_key

    def _resolve_key(self, key_id: str, keyring: Optional[dict[str, bytes]] = None) -> Optional[bytes]:
        """Resolve HMAC key from parameter keyring, instance keyring, or environment."""
        if keyring and key_id in keyring:
            return keyring[key_id]
        if key_id in self.keyring:
            return self.keyring[key_id]
        env_key = os.getenv(f"CLINDOC_AUDIT_HMAC_KEY_{key_id.upper()}") or os.getenv("CLINDOC_AUDIT_HMAC_KEY")
        if env_key:
            return env_key.encode("utf-8")
        return None

    def append_event(
        self,
        *,
        actor_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        action: str,
        metadata: Optional[dict[str, Any]] = None,
        timestamp: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Append an audit event to the JSONL ledger, recalculating the SHA-256 hash chain under file lock."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

        with locked_audit_ledger(self.ledger_path):
            seq_num = 1
            prev_hash = ""
            if self.ledger_path.exists() and self.ledger_path.stat().st_size > 0:
                with open(self.ledger_path, "r", encoding="utf-8") as f:
                    last_line = None
                    for line in f:
                        line_s = line.strip()
                        if line_s:
                            last_line = line_s
                    if last_line:
                        try:
                            parsed = json.loads(last_line)
                            seq_num = parsed.get("sequence_number", 0) + 1
                            prev_hash = parsed.get("current_event_hash", "")
                        except Exception as exc:
                            raise AuditLedgerVerificationError(f"Corrupted trailing line in audit ledger: {exc}") from exc

            envelope = build_canonical_event_envelope(
                sequence_number=seq_num,
                timestamp=timestamp or datetime.now(timezone.utc),
                actor_id=actor_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                metadata=metadata,
                previous_event_hash=prev_hash,
            )
            curr_hash = compute_canonical_envelope_hash(envelope)

            line_record = dict(envelope)
            line_record["current_event_hash"] = curr_hash

            line_json = json.dumps(line_record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(line_json + "\n")
                flush_and_fsync(f)

            return line_record

    def create_checkpoint(
        self,
        key_id: Optional[str] = None,
        key_bytes: Optional[bytes] = None,
        document_id: Optional[str] = None,
        document_sha256: Optional[str] = None,
    ) -> dict[str, Any]:
        """Write an HMAC-protected checkpoint sealing the ledger state with atomic replace & fsync."""
        with locked_audit_ledger(self.ledger_path):
            if not self.ledger_path.exists() or self.ledger_path.stat().st_size == 0:
                raise AuditLedgerVerificationError("Cannot create checkpoint for an empty or missing audit ledger.")

            terminal_hash = ""
            last_seq = 0
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    line_s = line.strip()
                    if line_s:
                        parsed = json.loads(line_s)
                        terminal_hash = parsed.get("current_event_hash", "")
                        last_seq = parsed.get("sequence_number", 0)

            active_key_id = key_id or self.signing_key_id
            secret = key_bytes or self._resolve_key(active_key_id)
            if not secret:
                raise AuditAnchorMissingError(f"HMAC signing key '{active_key_id}' not found in keyring or environment.")

            doc_id = document_id or self.document_id or "unknown"
            doc_sha = document_sha256 or self.document_sha256 or "unknown"

            payload = build_checkpoint_payload(
                document_id=doc_id,
                document_sha256=doc_sha,
                checkpoint_sequence=last_seq,
                terminal_hash=terminal_hash,
                key_id=active_key_id,
            )
            sig = compute_checkpoint_signature(payload, secret)

            chkpt_data = {
                "payload": payload,
                "signature": sig,
            }
            content_bytes = json.dumps(chkpt_data, indent=2, sort_keys=True).encode("utf-8")
            atomic_write_file(self.checkpoint_path, content_bytes, sync_dir=True)
            return chkpt_data

    def reconcile_with_db(
        self,
        session: Session,
        key_id: Optional[str] = None,
        key_bytes: Optional[bytes] = None,
        document_id: Optional[str] = None,
        document_sha256: Optional[str] = None,
        timeout: float = 10.0,
    ) -> ProjectionResult:
        """Reconcile the offline JSONL projection with authoritative PostgreSQL audit events."""
        active_key_id = key_id or self.signing_key_id
        secret = key_bytes or self._resolve_key(active_key_id)
        return AuditProjectionEngine.reconcile_outbox(
            session=session,
            ledger_path=self.ledger_path,
            checkpoint_path=self.checkpoint_path,
            keyring=self.keyring,
            key_id=active_key_id,
            hmac_key=secret,
            document_id=document_id or self.document_id,
            document_sha256=document_sha256 or self.document_sha256,
            timeout=timeout,
        )

    def recover_torn_tail(self, timeout: float = 10.0, _force_posix_mode: bool = False) -> Path:
        """Safely quarantine corrupted ledger and truncate torn tail under file lock."""
        return AuditProjectionEngine.recover_torn_tail(
            self.ledger_path,
            timeout=timeout,
            _force_posix_mode=_force_posix_mode,
        )

    def verify_bundle_complete_attestation(
        self,
        session: Session,
        bundle_dict: dict[str, Any],
        trust_store: Optional[Dict[str, Any]] = None,
        revoked_key_ids: Optional[Set[str]] = None,
        timeout: float = 10.0,
    ) -> Tuple[bytes, str]:
        """Verify complete DB-to-JSONL attestation, HMAC checkpoint, and signatures for a bundle."""
        return AuditProjectionEngine.verify_bundle_complete_attestation(
            session=session,
            bundle_dict=bundle_dict,
            ledger_path=self.ledger_path,
            checkpoint_path=self.checkpoint_path,
            trust_store=trust_store,
            revoked_key_ids=revoked_key_ids,
            keyring=self.keyring,
            key_id=self.signing_key_id,
            timeout=timeout,
        )

    def verify_ledger(
        self,
        expected_document_sha256: Optional[str] = None,
        keyring: Optional[dict[str, bytes]] = None,
        allow_unanchored_dev_ledger: bool = False,
        enforce_checkpoint: bool = False,
    ) -> LedgerVerificationResult:
        """Verify the cryptographic hash chain and HMAC checkpoint of the offline ledger."""
        if not self.ledger_path.exists():
            return LedgerVerificationResult(is_valid=False, error_message="Audit ledger file does not exist.")
        if self.ledger_path.stat().st_size == 0:
            return LedgerVerificationResult(is_valid=False, error_message="Audit ledger file is empty.")

        seen_seqs: Set[int] = set()
        seen_hashes: Set[str] = set()
        expected_seq = 1
        expected_prev_hash = ""
        verified_claims: Dict[str, VerifiedClaimReviewState] = {}

        try:
            with locked_audit_ledger(self.ledger_path), open(self.ledger_path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f, start=1):
                    line_s = line.strip()
                    if not line_s:
                        continue
                    try:
                        record = json.loads(line_s)
                    except json.JSONDecodeError as exc:
                        return LedgerVerificationResult(
                            is_valid=False,
                            error_message=f"Partial or corrupted JSON line at line {line_idx}: {exc}",
                        )

                    required_keys = (
                        "action", "actor_id", "entity_id", "entity_type",
                        "event_type", "metadata", "previous_event_hash",
                        "sequence_number", "timestamp", "current_event_hash",
                    )
                    for rk in required_keys:
                        if rk not in record:
                            return LedgerVerificationResult(
                                is_valid=False,
                                error_message=f"Missing required envelope key '{rk}' at line {line_idx}.",
                            )

                    seq = record["sequence_number"]
                    if seq in seen_seqs:
                        return LedgerVerificationResult(
                            is_valid=False,
                            error_message=f"Duplicate sequence number {seq} detected at line {line_idx}.",
                        )
                    if seq != expected_seq:
                        return LedgerVerificationResult(
                            is_valid=False,
                            error_message=f"Sequence gap at line {line_idx}: expected {expected_seq}, found {seq}.",
                        )
                    seen_seqs.add(seq)

                    prev_h = record["previous_event_hash"]
                    if prev_h != expected_prev_hash:
                        return LedgerVerificationResult(
                            is_valid=False,
                            error_message=(
                                f"Hash chain linkage break at sequence {seq}: "
                                f"expected previous '{expected_prev_hash}', found '{prev_h}'."
                            ),
                        )

                    # Strict Format-Aware Hash Verification (Finding 2):
                    # 1. Projected PostgreSQL records carry hash_algorithm == 'sha256_audit_v1'
                    #    and MUST verify against authoritative compute_audit_event_hash() ONLY.
                    # 2. Historical JSONL records carry hash_algorithm is None
                    #    and MUST verify against compute_canonical_envelope_hash(envelope) ONLY.
                    stored_hash = record["current_event_hash"]
                    alg = record.get("hash_algorithm")

                    if alg == "sha256_audit_v1":
                        db_hash = None
                        try:
                            raw_ts = record.get("timestamp")
                            if isinstance(raw_ts, str):
                                raw_ts_str = raw_ts.strip()
                                if raw_ts_str.endswith("Z"):
                                    raw_ts_str = raw_ts_str[:-1] + "+00:00"
                                ts_dt = datetime.fromisoformat(raw_ts_str)
                            elif isinstance(raw_ts, datetime):
                                ts_dt = raw_ts
                            else:
                                ts_dt = None

                            if ts_dt is not None:
                                db_hash = compute_audit_event_hash(
                                    sequence_number=seq,
                                    timestamp=ts_dt,
                                    actor_id=str(record.get("actor_id", "")).strip(),
                                    event_type=str(record.get("event_type", "")).strip(),
                                    entity_type=str(record.get("entity_type", "")).strip(),
                                    entity_id=str(record.get("entity_id", "")).strip(),
                                    action=str(record.get("action", "")).strip(),
                                    metadata_json=record.get("metadata", {}),
                                    previous_event_hash=record.get("previous_event_hash") or None,
                                )
                        except Exception:
                            pass

                        if not db_hash or stored_hash != db_hash:
                            return LedgerVerificationResult(
                                is_valid=False,
                                error_message=(
                                    f"Tampered audit payload at sequence {seq}: "
                                    f"stored '{stored_hash}', recomputed authoritative '{db_hash}'."
                                ),
                            )
                    elif alg is None:
                        envelope = {k: record[k] for k in (
                            "action", "actor_id", "entity_id", "entity_type",
                            "event_type", "metadata", "previous_event_hash",
                            "sequence_number", "timestamp",
                        )}
                        envelope_hash = compute_canonical_envelope_hash(envelope)
                        if stored_hash != envelope_hash:
                            return LedgerVerificationResult(
                                is_valid=False,
                                error_message=(
                                    f"Tampered audit payload at sequence {seq}: "
                                    f"stored '{stored_hash}', recomputed envelope '{envelope_hash}'."
                                ),
                            )
                    else:
                        return LedgerVerificationResult(
                            is_valid=False,
                            error_message=f"Unsupported hash algorithm '{alg}' at sequence {seq}.",
                        )

                    computed_hash = stored_hash
                    if computed_hash in seen_hashes:
                        return LedgerVerificationResult(
                            is_valid=False,
                            error_message=f"Replayed or duplicate event hash detected at sequence {seq}: {computed_hash}.",
                        )

                    seen_hashes.add(computed_hash)
                    expected_prev_hash = stored_hash
                    expected_seq += 1

                    # Extract latest claim state
                    if record.get("entity_type") == "claim":
                        c_id = record["entity_id"]
                        meta = record.get("metadata", {})
                        verified_claims[c_id] = VerifiedClaimReviewState(
                            claim_id=c_id,
                            review_state=meta.get("new_state", "UNKNOWN"),
                            action=record["action"],
                            actor_id=record["actor_id"],
                            claim_version=int(meta.get("claim_version", 1)),
                            document_id=meta.get("document_id", self.document_id),
                            document_sha256=meta.get("document_sha256", self.document_sha256),
                            reason_code=meta.get("reason_code"),
                            sequence_number=seq,
                            timestamp=record.get("timestamp"),
                            extra_metadata=meta,
                        )

        except Exception as exc:
            return LedgerVerificationResult(is_valid=False, error_message=f"Ledger file read error: {exc}")

        # Checkpoint verification
        checkpoint_verified = False
        is_prod = os.getenv("CLINDOC_ENV", "").lower() == "production"

        if self.checkpoint_path.exists():
            try:
                chkpt_raw = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
                payload = chkpt_raw.get("payload", {})
                stored_sig = chkpt_raw.get("signature", "")
                k_id = payload.get("key_id", "default")

                key_bytes = self._resolve_key(k_id, keyring=keyring)
                if not key_bytes:
                    return LedgerVerificationResult(
                        is_valid=False,
                        error_message=f"HMAC key '{k_id}' required for checkpoint verification not found.",
                    )

                computed_sig = compute_checkpoint_signature(payload, key_bytes)
                if not hmac.compare_digest(computed_sig, stored_sig):
                    return LedgerVerificationResult(
                        is_valid=False,
                        error_message="HMAC signature verification failed on checkpoint (tamper detected).",
                    )

                chkpt_seq = payload.get("checkpoint_sequence")
                chkpt_hash = payload.get("terminal_hash")
                chkpt_doc_sha = payload.get("document_sha256")

                if chkpt_seq != (expected_seq - 1):
                    return LedgerVerificationResult(
                        is_valid=False,
                        error_message=(
                            f"Checkpoint sequence mismatch: checkpoint has {chkpt_seq}, "
                            f"ledger has {expected_seq - 1}."
                        ),
                    )
                if chkpt_hash != expected_prev_hash:
                    return LedgerVerificationResult(
                        is_valid=False,
                        error_message=(
                            f"Checkpoint terminal hash mismatch: checkpoint has '{chkpt_hash}', "
                            f"ledger has '{expected_prev_hash}'."
                        ),
                    )
                if expected_document_sha256 and chkpt_doc_sha != expected_document_sha256:
                    return LedgerVerificationResult(
                        is_valid=False,
                        error_message=(
                            f"Checkpoint document_sha256 mismatch: expected '{expected_document_sha256}', "
                            f"checkpoint has '{chkpt_doc_sha}'."
                        ),
                    )

                checkpoint_verified = True

            except Exception as exc:
                return LedgerVerificationResult(is_valid=False, error_message=f"Checkpoint read/verification error: {exc}")
        else:
            if enforce_checkpoint or is_prod:
                return LedgerVerificationResult(
                    is_valid=False,
                    error_message="HMAC checkpoint missing in production / strict export mode.",
                )
            if not allow_unanchored_dev_ledger:
                return LedgerVerificationResult(
                    is_valid=False,
                    error_message="Unanchored audit ledger rejected (checkpoint missing and allow_unanchored_dev_ledger=False).",
                )

        return LedgerVerificationResult(
            is_valid=True,
            total_events=len(seen_seqs),
            terminal_hash=expected_prev_hash,
            verified_claims=verified_claims,
            is_unanchored_dev_mode=(not checkpoint_verified),
            checkpoint_verified=checkpoint_verified,
        )


# ─── FIELD NORMALIZATION & ENUM MAPPING HELPERS ──────────────────────────────

def _normalize_field_type(ft: str) -> str:
    """Normalize relational/legacy field type strings to canonical FieldType literal."""
    mapping = {
        "medication.name": "med.name",
        "medication.dose": "med.dose",
        "medication.route": "med.route",
        "medication.frequency": "med.frequency",
        "medication.duration": "med.duration",
        "laboratory.value": "lab.value",
        "laboratory.unit": "lab.unit",
        "laboratory.flag": "lab.flag",
        "laboratory.reference_range": "lab.reference_range",
        "laboratory.test_name": "lab.test_name",
    }
    return mapping.get(ft, ft)


def _map_verification_label(label: str) -> DBVerificationLabel:
    """Map verification label string to relational DBVerificationLabel."""
    if label == "SUPPORTED":
        return DBVerificationLabel.SUPPORTED
    if label == "INSUFFICIENT_EVIDENCE":
        return DBVerificationLabel.INSUFFICIENT_EVIDENCE
    try:
        return DBVerificationLabel(label)
    except ValueError:
        return DBVerificationLabel.REFUTED


def _map_verification_stage(stage_letter: str) -> DBVerificationStage:
    """Map stage letter to relational DBVerificationStage."""
    mapping = {
        "A": DBVerificationStage.STAGE_A,
        "B": DBVerificationStage.STAGE_B,
        "C": DBVerificationStage.STAGE_C,
    }
    return mapping.get(stage_letter, DBVerificationStage.STAGE_A)


def _map_contradiction_type(c_type: Optional[str]) -> Optional[DBContradictionType]:
    """Map cascade contradiction type to relational DBContradictionType."""
    if not c_type:
        return None
    mapping = {
        "value_mismatch": DBContradictionType.NUMERICAL_MISMATCH,
        "dose_mismatch": DBContradictionType.NUMERICAL_MISMATCH,
        "unit_mismatch": DBContradictionType.NUMERICAL_MISMATCH,
        "negation_mismatch": DBContradictionType.NEGATION,
        "wrong_entity": DBContradictionType.ENTITY_MISMATCH,
        "code_mismatch": DBContradictionType.ENTITY_MISMATCH,
        "temporal_mismatch": DBContradictionType.TEMPORAL_MISMATCH,
    }
    return mapping.get(c_type, DBContradictionType.OTHER)


# ─── REVIEW SERVICE ──────────────────────────────────────────────────────────

class ClaimReviewService:
    """Service orchestrating claim review lifecycle actions, inline re-verification, and audit."""

    def __init__(
        self,
        audit_service: Optional[AuditService] = None,
        cascade: Optional[VerificationCascade] = None,
        triage_engine: Optional[TriageEngine] = None,
        audit_ledger: Optional[ReviewAuditLedger] = None,
        strict_lifecycle: bool = False,
        enforce_role_rbac: bool = False,
        enforce_four_eyes: bool = False,
    ) -> None:
        self.audit_service = audit_service or AuditService()
        self.cascade = cascade or VerificationCascade()
        self.triage_engine = triage_engine
        self.audit_ledger = audit_ledger
        self.strict_lifecycle = strict_lifecycle
        self.enforce_role_rbac = enforce_role_rbac
        self.enforce_four_eyes = (
            enforce_four_eyes
            or strict_lifecycle
            or (os.getenv("CLINDOC_ENFORCE_FOUR_EYES") == "1")
        )

    @staticmethod
    def _parse_actor_uuid(actor_id: Any) -> uuid.UUID:
        """Parse actor ID into a UUID, failing closed on missing, null, or invalid identities."""
        if actor_id is None or str(actor_id).strip().lower() in ("", "none", "anonymous", "null"):
            raise UnauthorizedReviewerError("Reviewer actor ID is missing, null, or anonymous.")
        if isinstance(actor_id, uuid.UUID):
            return actor_id
        try:
            return uuid.UUID(str(actor_id).strip())
        except Exception as exc:
            raise UnauthorizedReviewerError(f"Invalid reviewer UUID '{actor_id}': {exc}") from exc

    def _resolve_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        expected_version: Optional[int] = None,
    ) -> Claim:
        """Resolve a Claim by primary key row ID or stable clinical entity UUID."""
        claim_uuid = uuid.UUID(str(claim_id)) if not isinstance(claim_id, uuid.UUID) else claim_id

        claim = session.get(Claim, claim_uuid)
        if claim is None:
            q = session.query(Claim).filter(Claim.claim_id == claim_uuid)
            if expected_version is not None:
                claim = q.filter(Claim.version == expected_version).first()
            else:
                claim = q.order_by(Claim.version.desc()).first()

        if claim is None:
            raise ClaimNotFoundError(f"Claim '{claim_uuid}' not found.")
        return claim

    def _check_optimistic_lock(self, claim: Claim, expected_version: int) -> None:
        """Validate optimistic locking against concurrent edits."""
        if claim.version != expected_version:
            raise OptimisticLockError(
                f"Optimistic lock conflict for claim '{claim.claim_id}': "
                f"expected version {expected_version}, but found version {claim.version}."
            )

    def _check_actor_authorization(
        self,
        session: Session,
        actor_uuid: uuid.UUID,
        required_supervisory: bool = False,
    ) -> None:
        """Verify reviewer identity and role permissions (Addendum §4.3)."""
        if not actor_uuid:
            raise UnauthorizedReviewerError("Reviewer actor ID is missing or null.")
        actor_str = str(actor_uuid).strip().lower()
        if actor_str in ("", "none", "anonymous", "null"):
            raise UnauthorizedReviewerError(f"Invalid reviewer identity '{actor_uuid}'.")

        user = None
        try:
            user = session.get(User, actor_uuid)
        except Exception:
            user = None

        if user is not None:
            if not user.is_active:
                raise DeactivatedUserActionError(f"Reviewer user '{actor_uuid}' is inactive.")
            if user.roles:
                user_role_ids = {r.role_id.lower() for r in user.roles}
                if required_supervisory:
                    if not (user_role_ids & SUPERVISORY_ROLES):
                        raise UnauthorizedReviewerError(
                            f"User '{actor_uuid}' with roles {user_role_ids} lacks supervisory authorization."
                        )
                else:
                    if not (user_role_ids & AUTHORIZED_REVIEWER_ROLES):
                        raise UnauthorizedReviewerError(
                            f"User '{actor_uuid}' with roles {user_role_ids} lacks clinical reviewer authorization."
                        )
        elif self.enforce_role_rbac:
            raise UnauthorizedReviewerError(f"User '{actor_uuid}' not found in authorization directory.")

    def _lock_and_validate_lineage(
        self,
        session: Session,
        target_claim: Claim,
        actor_uuid: uuid.UUID,
        is_accept: bool = False,
    ) -> list[Claim]:
        """Acquire root lock on v1, validate complete linear single-parent chain,
        validate actor provenance, enforce leaf-only operations, and check multi-hop Four-Eyes.
        """
        # 1. Verify that current actor is an active user (deactivated users cannot perform new actions)
        current_user = None
        try:
            current_user = session.get(User, actor_uuid)
        except Exception:
            current_user = None
        if current_user is not None and not getattr(current_user, "is_active", True):
            raise DeactivatedUserActionError(
                f"Actor '{actor_uuid}' is deactivated and cannot perform review actions."
            )

        # Capture in-memory payload and custom attributes from target_claim
        target_in_mem_payload = copy.deepcopy(target_claim.payload) if target_claim.payload else {}
        target_in_mem_assoc = getattr(target_claim, "association_status", None)

        # 2. Universal Lineage Root Mutex on v1:
        # In SQLite with_for_update() is a no-op; in PostgreSQL it executes SELECT ... FOR UPDATE
        root_claim = (
            session.query(Claim)
            .filter(Claim.claim_id == target_claim.claim_id, Claim.version == 1)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if root_claim is None:
            raise CorruptedClaimAncestryError(
                f"Corrupted claim ancestry: Genesis version 1 for lineage '{target_claim.claim_id}' not found."
            )

        # 3. Retrieve all versions for this lineage under the root lock
        all_versions = (
            session.query(Claim)
            .filter(Claim.claim_id == target_claim.claim_id)
            .order_by(Claim.version.asc())
            .populate_existing()
            .all()
        )
        k = len(all_versions)
        if k == 0:
            raise CorruptedClaimAncestryError(f"No versions found for lineage '{target_claim.claim_id}'.")

        # Re-apply any in-memory test/caller mutations on target_claim
        for v in all_versions:
            if v.id == target_claim.id:
                if target_in_mem_assoc is not None:
                    v.association_status = target_in_mem_assoc
                if target_in_mem_payload:
                    merged = dict(v.payload or {})
                    merged.update(target_in_mem_payload)
                    v.payload = merged

        # 4. Check version contiguity [1, 2, ..., k] and document identity
        for idx, v in enumerate(all_versions, start=1):
            if v.version != idx:
                raise CorruptedClaimAncestryError(
                    f"Corrupted claim ancestry for lineage '{target_claim.claim_id}': "
                    f"version sequence gap or duplication at index {idx}, expected {idx} but found {v.version}."
                )
            if str(v.document_id) != str(root_claim.document_id):
                raise CorruptedClaimAncestryError(
                    f"Corrupted claim ancestry: Document ID mismatch in version {v.version} "
                    f"('{v.document_id}' vs root '{root_claim.document_id}')."
                )

        # 5. Check parent links and intermediate states
        if all_versions[0].supersedes_claim_id is not None:
            raise CorruptedClaimAncestryError(
                f"Genesis version 1 for '{target_claim.claim_id}' illegally has supersedes_claim_id."
            )

        for i in range(1, k):
            parent = all_versions[i - 1]
            curr = all_versions[i]
            if curr.supersedes_claim_id != parent.id:
                raise CorruptedClaimAncestryError(
                    f"Corrupted claim ancestry: version {curr.version} supersedes '{curr.supersedes_claim_id}', "
                    f"expected parent id '{parent.id}' (version {parent.version})."
                )
            parent_state = parent.review_state.value if hasattr(parent.review_state, "value") else str(parent.review_state)
            if parent_state != ClaimReviewState.EDITED.value:
                raise CorruptedClaimAncestryError(
                    f"Corrupted claim ancestry: intermediate version {parent.version} has state '{parent_state}', "
                    f"expected '{ClaimReviewState.EDITED.value}'."
                )
            child_count = session.query(Claim).filter(Claim.supersedes_claim_id == parent.id).count()
            if child_count > 1:
                raise BranchingProhibitedError(
                    f"Branching prohibited: intermediate version {parent.version} has {child_count} children."
                )

        # 6. Leaf-only operation enforcement on target claim
        if target_claim.version != k or target_claim.id != all_versions[-1].id:
            raise InvalidClaimLifecycleStateError(
                f"Non-leaf review prohibited: claim version {target_claim.version} is not the terminal leaf (latest is {k})."
            )

        leaf_children_count = session.query(Claim).filter(Claim.supersedes_claim_id == target_claim.id).count()
        if leaf_children_count > 0:
            raise BranchingProhibitedError(
                f"Branching prohibited: target claim '{target_claim.id}' has already been superseded by {leaf_children_count} child claim(s)."
            )

        # 7. Actor Provenance Resolution & Historical Actor Verification (Option B)
        for v in all_versions:
            origin_val = v.origin.value if hasattr(v.origin, "value") else str(v.origin)
            if v.version > 1 or origin_val == ClaimOrigin.REVIEWER.value or v.created_by is not None:
                v_payload = v.payload or {}
                edited_by = v_payload.get("edited_by")
                if v.created_by is None or not edited_by:
                    raise ActorProvenanceMismatchError(
                        f"Actor provenance missing for claim version {v.version}: "
                        f"created_by={v.created_by}, payload['edited_by']={edited_by}."
                    )
                if str(v.created_by) != str(edited_by):
                    raise ActorProvenanceMismatchError(
                        f"Actor provenance mismatch for claim version {v.version}: "
                        f"created_by '{v.created_by}' != payload['edited_by'] '{edited_by}'."
                    )

                creator = None
                try:
                    creator = session.get(User, v.created_by)
                except Exception:
                    creator = None
                if creator is None:
                    raise UnresolvableActorError(
                        f"Actor '{v.created_by}' for claim version {v.version} cannot be resolved to a User."
                    )

                if not getattr(creator, "is_active", True):
                    # Option B: Historical deactivated user check via immutable audit evidence
                    self._verify_historical_actor_audit_evidence(session, v)

        # 8. Four-Eyes check for accept action
        if is_accept:
            self._check_four_eyes(target_claim, actor_uuid, session, all_versions=all_versions)

        return all_versions

    def _verify_historical_actor_audit_evidence(
        self,
        session: Session,
        v: Claim,
    ) -> None:
        """Verify immutable, tamper-evident audit evidence for historical deactivated actor (Option B).

        Requirements (F-12 Check 7):
          1. Exact claim-version provenance binding:
             - actor_id matches historical creator/editor (str(v.created_by))
             - entity_id matches exact claim-version record (str(v.id))
             - metadata claim_version matches v.version
             - metadata stable_claim_id matches str(v.claim_id)
             - action type is an authorized creation/modification action for that version ("EDIT", "CREATE")
             - timestamp is present, valid, and not in the future
          2. Cryptographic hash chain verification:
             - sequence_number > 0
             - current_event_hash matches canonical recomputed compute_audit_event_hash(...)
             - previous-event hash chain linkage:
               - sequence 1: previous_event_hash must be None or empty
               - sequence > 1: previous event at (sequence - 1) must exist, match previous_event_hash,
                 and its own hash must verify against compute_audit_event_hash(...)
          3. ReviewAction alone lacks cryptographic integrity guarantees and cannot substitute for
             a cryptographically verified AuditEvent (fails closed).
          4. Missing, malformed, mismatched, or broken provenance fails closed with UnverifiableHistoricalActorError.
        """
        actor_id_str = str(v.created_by)
        claim_id_str = str(v.id)
        stable_claim_id_str = str(v.claim_id)

        events: list[AuditEvent] = (
            session.query(AuditEvent)
            .filter(
                AuditEvent.actor_id == actor_id_str,
                AuditEvent.entity_id == claim_id_str,
            )
            .all()
        )

        if not events:
            raise UnverifiableHistoricalActorError(
                f"Historical actor '{v.created_by}' is deactivated and lacks verifiable "
                f"audit evidence for claim version {v.version}. Failing closed."
            )

        now_utc = datetime.now(timezone.utc)
        valid_event_found = False

        for evt in events:
            # 1. Entity type check
            if getattr(evt, "entity_type", "") != "claim":
                continue

            # 2. Metadata validation
            meta = evt.metadata_json or {}
            if not isinstance(meta, dict):
                continue

            # Version binding: claim_version must match v.version
            if meta.get("claim_version") != v.version:
                continue

            # Stable claim ID binding
            if str(meta.get("stable_claim_id") or "") != stable_claim_id_str:
                continue

            # Exact action binding: version 1 must be CREATE; version > 1 must be EDIT
            # Both evt.action and metadata["action"] must strictly match expected_action
            expected_action = "CREATE" if v.version == 1 else "EDIT"
            evt_action = str(evt.action or "").strip().upper()
            meta_action = str(meta.get("action") or "").strip().upper()
            if evt_action != expected_action or meta_action != expected_action:
                continue

            # 3. Timestamp verification
            if not evt.timestamp:
                continue
            evt_ts = evt.timestamp if evt.timestamp.tzinfo else evt.timestamp.replace(tzinfo=timezone.utc)
            if evt_ts > now_utc:
                continue

            # 4. Sequence number verification
            seq = getattr(evt, "sequence_number", 0)
            if seq is None or seq <= 0:
                continue

            # 5. Cryptographic hash integrity check of current event
            stored_hash = str(evt.current_event_hash or "").strip().lower()
            if len(stored_hash) != 64 or not all(c in "0123456789abcdef" for c in stored_hash):
                continue

            expected_hash = compute_audit_event_hash(
                sequence_number=seq,
                timestamp=evt.timestamp,
                actor_id=evt.actor_id,
                event_type=evt.event_type,
                entity_type=evt.entity_type,
                entity_id=evt.entity_id,
                action=evt.action,
                metadata_json=evt.metadata_json,
                previous_event_hash=evt.previous_event_hash,
            ).lower()

            if stored_hash != expected_hash:
                continue

            # 6. Complete Hash Chain verification back to genesis sequence 1
            chain_result = self.audit_service.verify_audit_chain(
                session,
                start_sequence=1,
                end_sequence=seq,
            )
            if not chain_result.is_valid or chain_result.total_events != seq or chain_result.total_events == 0:
                continue

            # 7. Offline ledger HMAC verification if audit_ledger is configured
            if self.audit_ledger is not None:
                ledger_res = self.audit_ledger.verify_ledger(enforce_checkpoint=True)
                if not ledger_res.is_valid:
                    continue

            valid_event_found = True
            break

        if not valid_event_found:
            raise UnverifiableHistoricalActorError(
                f"Historical actor '{v.created_by}' is deactivated and its audit evidence "
                f"for claim version {v.version} failed integrity or provenance verification. Failing closed."
            )

    def _check_four_eyes(
        self,
        claim: Claim,
        actor_uuid: uuid.UUID,
        session: Session,
        all_versions: Optional[Sequence[Claim]] = None,
    ) -> None:
        """Enforce Four-Eyes separation of duties across complete multi-hop ancestry."""
        if not self.enforce_four_eyes:
            return

        is_high_risk = False
        payload = claim.payload or {}
        assoc_status = payload.get("association_status") or getattr(claim, "association_status", None)
        if assoc_status in ("PARALLEL_COORDINATE_BIJECTION", "AMBIGUOUS_SCOPE_MULTI_ENTITY", "AMBIGUOUS_SLOT_CONFLICT", "UNANCHORED_ORPHAN"):
            is_high_risk = True

        if payload.get("slot_conflict") or getattr(claim, "slot_conflict", False):
            is_high_risk = True

        if payload.get("is_conditional") or getattr(claim, "is_conditional", False):
            is_high_risk = True

        origin_val = claim.origin.value if hasattr(claim.origin, "value") else str(claim.origin)
        if origin_val == ClaimOrigin.REVIEWER.value or claim.created_by is not None:
            is_high_risk = True

        claim_val = (claim.value or "").lower()
        norm_code = (claim.normalized_code or "").lower()
        for drug in HIGH_POTENCY_DRUGS:
            if drug in claim_val or drug in norm_code:
                is_high_risk = True
                break

        if not is_high_risk:
            return

        actor_str = str(actor_uuid)

        # 1. Creator or direct editor of this claim version cannot accept
        if claim.created_by is not None and str(claim.created_by) == actor_str:
            raise FourEyesViolationError(
                f"Four-Eyes Violation: Clinician '{actor_uuid}' who created or edited claim '{claim.claim_id}' cannot accept it."
            )
        if claim.payload and claim.payload.get("edited_by") == actor_str:
            raise FourEyesViolationError(
                f"Four-Eyes Violation: Clinician '{actor_uuid}' who edited claim '{claim.claim_id}' cannot accept it."
            )

        # 2. Complete Multi-Hop Ancestry Traversal: No ancestor creator/modifier can accept
        ancestor_actors: set[str] = set()
        if all_versions is not None:
            ancestors = [v for v in all_versions if v.id != claim.id]
        elif claim.claim_id is not None:
            ancestors = (
                session.query(Claim)
                .filter(Claim.claim_id == claim.claim_id, Claim.id != claim.id)
                .all()
            )
        else:
            ancestors = []

        for anc in ancestors:
            if anc.created_by is not None:
                ancestor_actors.add(str(anc.created_by))
            if anc.payload and anc.payload.get("edited_by"):
                ancestor_actors.add(str(anc.payload["edited_by"]))

        if actor_str in ancestor_actors:
            raise FourEyesViolationError(
                f"Four-Eyes Violation: Clinician '{actor_uuid}' who created or modified prior version of claim '{claim.claim_id}' cannot accept it."
            )

        # 3. Document uploader (operator) cannot accept high-risk claim
        if claim.document_id is not None:
            try:
                doc = session.get(Document, claim.document_id)
            except Exception:
                doc = None
            if doc is not None and doc.uploaded_by is not None and str(doc.uploaded_by) == actor_str:
                raise FourEyesViolationError(
                    f"Four-Eyes Violation: Pipeline operator '{actor_uuid}' who uploaded document '{doc.id}' cannot accept high-risk claim '{claim.claim_id}'."
                )

    def _check_document_binding(
        self,
        claim: Claim,
        session: Session,
        expected_document_sha256: Optional[str] = None,
    ) -> None:
        """Validate that the review action binds to the active document content SHA-256 (Addendum §4.5)."""
        if expected_document_sha256 is None:
            return
        if claim.document_id is not None:
            try:
                doc = session.get(Document, claim.document_id)
            except Exception:
                doc = None
            if doc is not None:
                actual_sha = getattr(doc, "sha256", None) or getattr(doc, "document_sha256", None)
                if actual_sha and str(actual_sha).strip() != str(expected_document_sha256).strip():
                    raise StaleDocumentVersionError(
                        f"Stale document hash mismatch for document '{doc.id}': "
                        f"expected {expected_document_sha256}, but active document has {actual_sha}."
                    )

    def _record_audit_event(
        self,
        session: Session,
        *,
        actor_id: uuid.UUID,
        target_claim: Claim,
        action: str,
        previous_state: str,
        new_state: str,
        reason_code: Optional[str] = None,
        extra_metadata: Optional[dict[str, Any]] = None,
        document_sha256: Optional[str] = None,
    ) -> None:
        """Append an audit event ensuring strictly PHI-free metadata across relational and offline modes."""
        doc_sha = document_sha256
        if not doc_sha and target_claim.document_id:
            try:
                doc = session.get(Document, target_claim.document_id)
                if doc is not None:
                    doc_sha = getattr(doc, "sha256", None) or getattr(doc, "document_sha256", None)
            except Exception:
                pass

        audit_metadata: dict[str, Any] = {
            "action": action,
            "previous_state": str(previous_state),
            "new_state": str(new_state),
            "reason_code": reason_code,
            "claim_version": target_claim.version,
            "claim_id": str(target_claim.id),
            "stable_claim_id": str(target_claim.claim_id),
            "document_id": str(target_claim.document_id),
            "document_sha256": str(doc_sha or ""),
            "field_type": target_claim.field_type,
            "origin": target_claim.origin.value if hasattr(target_claim.origin, "value") else str(target_claim.origin),
        }
        if target_claim.supersedes_claim_id:
            audit_metadata["supersedes_claim_id"] = str(target_claim.supersedes_claim_id)

        if extra_metadata:
            for k, v in extra_metadata.items():
                if k not in audit_metadata:
                    audit_metadata[k] = v

        validate_metadata_phi_free(audit_metadata)

        # 1. Relational persistence
        try:
            self.audit_service.record_event(
                session=session,
                actor_id=str(actor_id),
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(target_claim.id),
                action=action,
                metadata_json=audit_metadata,
            )
        except Exception as exc:
            logger.error("AuditService recording failed for claim %s: %s", target_claim.id, exc)
            raise AuditIntegrationError(f"Audit event recording failed: {exc}") from exc

        # 2. W-02 FORMALLY RETIRED (Stage 3.1):
        # Direct dual-write append to offline JSONL ledger is eliminated to prevent split-brain state.
        # Audit persistence is 100% authoritative in PostgreSQL via AuditService.record_event().
        # Projection to JSONL is centralized in AuditProjectionEngine post-commit.

    def accept_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        expected_version: int,
        reason_code: Optional[str] = None,
        notes: Optional[str] = None,
        document_sha256: Optional[str] = None,
        commit: bool = True,
    ) -> Claim:
        """Transition a claim from PENDING or ESCALATED to ACCEPTED with Four-Eyes and binding checks."""
        actor_uuid = self._parse_actor_uuid(actor_id)

        try:
            claim = self._resolve_claim(session, claim_id, expected_version)
            self._check_optimistic_lock(claim, expected_version)
            self._check_document_binding(claim, session, expected_document_sha256=document_sha256)

            current_state = claim.review_state.value if hasattr(claim.review_state, "value") else str(claim.review_state)
            is_escalated = (current_state == ClaimReviewState.ESCALATED.value)

            if current_state not in (ClaimReviewState.PENDING.value, ClaimReviewState.ESCALATED.value):
                raise InvalidStateTransitionError(
                    f"Cannot ACCEPT claim from '{current_state}'. Allowed states are PENDING or ESCALATED."
                )

            self._check_actor_authorization(session, actor_uuid, required_supervisory=is_escalated)
            self._lock_and_validate_lineage(session, claim, actor_uuid, is_accept=True)

            before_payload = copy.deepcopy(claim.payload) if claim.payload else {}
            claim.review_state = ClaimReviewState.ACCEPTED
            after_payload = dict(before_payload)
            after_payload["review_state"] = ClaimReviewState.ACCEPTED.value
            after_payload["accepted_by"] = str(actor_uuid)
            claim.payload = after_payload

            action = ReviewAction(
                id=uuid.uuid4(),
                claim_id=claim.id,
                actor_id=actor_uuid,
                action_type=ReviewActionType.ACCEPT,
                previous_state=current_state,
                new_state=ClaimReviewState.ACCEPTED.value,
                reason_code=reason_code,
                notes=notes,
                before_payload=before_payload,
                after_payload=after_payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(action)
            session.flush()

            self._record_audit_event(
                session,
                actor_id=actor_uuid,
                target_claim=claim,
                action=ReviewActionType.ACCEPT.value,
                previous_state=current_state,
                new_state=ClaimReviewState.ACCEPTED.value,
                reason_code=reason_code,
                document_sha256=document_sha256,
            )

            if commit:
                session.commit()
            return claim

        except Exception:
            session.rollback()
            raise

    def reject_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        reason_code: str,
        expected_version: int,
        notes: Optional[str] = None,
        document_sha256: Optional[str] = None,
        commit: bool = True,
    ) -> Claim:
        """Transition a claim from PENDING or ESCALATED to REJECTED. Requires mandatory reason_code."""
        if not reason_code or not str(reason_code).strip():
            raise MissingReasonCodeError("Reason code is required for REJECT action.")

        actor_uuid = self._parse_actor_uuid(actor_id)

        try:
            claim = self._resolve_claim(session, claim_id, expected_version)
            self._check_optimistic_lock(claim, expected_version)
            self._check_document_binding(claim, session, expected_document_sha256=document_sha256)

            current_state = claim.review_state.value if hasattr(claim.review_state, "value") else str(claim.review_state)
            is_escalated = (current_state == ClaimReviewState.ESCALATED.value)

            if current_state not in (ClaimReviewState.PENDING.value, ClaimReviewState.ESCALATED.value):
                raise InvalidStateTransitionError(
                    f"Cannot REJECT claim from '{current_state}'. Allowed states are PENDING or ESCALATED."
                )

            self._check_actor_authorization(session, actor_uuid, required_supervisory=is_escalated)
            self._lock_and_validate_lineage(session, claim, actor_uuid, is_accept=False)

            before_payload = copy.deepcopy(claim.payload) if claim.payload else {}
            claim.review_state = ClaimReviewState.REJECTED
            after_payload = dict(before_payload)
            after_payload["review_state"] = ClaimReviewState.REJECTED.value
            after_payload["rejection_reason"] = reason_code.strip()
            after_payload["rejected_by"] = str(actor_uuid)
            claim.payload = after_payload

            action = ReviewAction(
                id=uuid.uuid4(),
                claim_id=claim.id,
                actor_id=actor_uuid,
                action_type=ReviewActionType.REJECT,
                previous_state=current_state,
                new_state=ClaimReviewState.REJECTED.value,
                reason_code=reason_code.strip(),
                notes=notes,
                before_payload=before_payload,
                after_payload=after_payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(action)
            session.flush()

            self._record_audit_event(
                session,
                actor_id=actor_uuid,
                target_claim=claim,
                action=ReviewActionType.REJECT.value,
                previous_state=current_state,
                new_state=ClaimReviewState.REJECTED.value,
                reason_code=reason_code.strip(),
                document_sha256=document_sha256,
            )

            if commit:
                session.commit()
            return claim

        except Exception:
            session.rollback()
            raise

    def escalate_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        expected_version: int,
        reason_code: Optional[str] = None,
        notes: Optional[str] = None,
        document_sha256: Optional[str] = None,
        require_reason_code: bool = False,
        commit: bool = True,
    ) -> Claim:
        """Transition a claim from PENDING to ESCALATED."""
        if (self.strict_lifecycle or require_reason_code) and (not reason_code or not str(reason_code).strip()):
            raise MissingReasonCodeError("Reason code is required for ESCALATE action under strict lifecycle.")

        actor_uuid = self._parse_actor_uuid(actor_id)

        try:
            claim = self._resolve_claim(session, claim_id, expected_version)
            self._check_optimistic_lock(claim, expected_version)
            self._check_document_binding(claim, session, expected_document_sha256=document_sha256)

            current_state = claim.review_state.value if hasattr(claim.review_state, "value") else str(claim.review_state)
            if current_state != ClaimReviewState.PENDING.value:
                raise InvalidStateTransitionError(
                    f"Cannot ESCALATE claim from '{current_state}'. Allowed origin state is PENDING."
                )

            self._check_actor_authorization(session, actor_uuid)
            self._lock_and_validate_lineage(session, claim, actor_uuid, is_accept=False)

            before_payload = copy.deepcopy(claim.payload) if claim.payload else {}
            claim.review_state = ClaimReviewState.ESCALATED
            after_payload = dict(before_payload)
            after_payload["review_state"] = ClaimReviewState.ESCALATED.value
            after_payload["escalated_by"] = str(actor_uuid)
            claim.payload = after_payload

            action = ReviewAction(
                id=uuid.uuid4(),
                claim_id=claim.id,
                actor_id=actor_uuid,
                action_type=ReviewActionType.ESCALATE,
                previous_state=current_state,
                new_state=ClaimReviewState.ESCALATED.value,
                reason_code=reason_code,
                notes=notes,
                before_payload=before_payload,
                after_payload=after_payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(action)
            session.flush()

            self._record_audit_event(
                session,
                actor_id=actor_uuid,
                target_claim=claim,
                action=ReviewActionType.ESCALATE.value,
                previous_state=current_state,
                new_state=ClaimReviewState.ESCALATED.value,
                reason_code=reason_code,
                document_sha256=document_sha256,
            )

            if commit:
                session.commit()
            return claim

        except Exception:
            session.rollback()
            raise

    def reopen_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        expected_version: int,
        reason_code: Optional[str] = None,
        notes: Optional[str] = None,
        document_sha256: Optional[str] = None,
        require_reason_code: bool = False,
        commit: bool = True,
    ) -> Claim:
        """Reopen a reviewed claim back to PENDING. Allowed origin states: ACCEPTED, REJECTED, ESCALATED."""
        if (self.strict_lifecycle or require_reason_code) and (not reason_code or not str(reason_code).strip()):
            raise MissingReasonCodeError("Reason code is required for REOPEN action under strict lifecycle.")

        actor_uuid = self._parse_actor_uuid(actor_id)

        try:
            claim = self._resolve_claim(session, claim_id, expected_version)
            self._check_optimistic_lock(claim, expected_version)
            self._check_document_binding(claim, session, expected_document_sha256=document_sha256)

            current_state = claim.review_state.value if hasattr(claim.review_state, "value") else str(claim.review_state)
            reopenable_states = {
                ClaimReviewState.ACCEPTED.value,
                ClaimReviewState.REJECTED.value,
                ClaimReviewState.ESCALATED.value,
            }
            if current_state not in reopenable_states:
                raise InvalidStateTransitionError(
                    f"Cannot REOPEN claim from '{current_state}'. Allowed states are ACCEPTED, REJECTED, ESCALATED."
                )

            is_escalated = (current_state == ClaimReviewState.ESCALATED.value)
            self._check_actor_authorization(session, actor_uuid, required_supervisory=is_escalated)
            self._lock_and_validate_lineage(session, claim, actor_uuid, is_accept=False)

            before_payload = copy.deepcopy(claim.payload) if claim.payload else {}
            claim.review_state = ClaimReviewState.PENDING
            after_payload = dict(before_payload)
            after_payload["review_state"] = ClaimReviewState.PENDING.value
            after_payload["reopened_by"] = str(actor_uuid)
            claim.payload = after_payload

            action = ReviewAction(
                id=uuid.uuid4(),
                claim_id=claim.id,
                actor_id=actor_uuid,
                action_type=ReviewActionType.REOPEN,
                previous_state=current_state,
                new_state=ClaimReviewState.PENDING.value,
                reason_code=reason_code,
                notes=notes,
                before_payload=before_payload,
                after_payload=after_payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(action)
            session.flush()

            self._record_audit_event(
                session,
                actor_id=actor_uuid,
                target_claim=claim,
                action=ReviewActionType.REOPEN.value,
                previous_state=current_state,
                new_state=ClaimReviewState.PENDING.value,
                reason_code=reason_code,
                document_sha256=document_sha256,
            )

            if commit:
                session.commit()
            return claim

        except Exception:
            session.rollback()
            raise

    def edit_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        new_value: str,
        reason_code: str,
        expected_version: int,
        new_normalized_code: Optional[str] = None,
        new_code_system: Optional[str] = None,
        notes: Optional[str] = None,
        page_text: Optional[str] = None,
        document_sha256: Optional[str] = None,
        commit: bool = True,
    ) -> EditClaimResult:
        """Edit a clinical claim creating a new immutable version with immediate re-verification."""
        if not new_value or not str(new_value).strip():
            raise ValueError("Claim value must not be empty.")
        if not reason_code or not str(reason_code).strip():
            raise MissingReasonCodeError("Reason code is required for EDIT action.")

        actor_uuid = self._parse_actor_uuid(actor_id)

        try:
            previous_claim = self._resolve_claim(session, claim_id, expected_version)
            self._check_optimistic_lock(previous_claim, expected_version)
            self._check_document_binding(previous_claim, session, expected_document_sha256=document_sha256)

            current_state = (
                previous_claim.review_state.value
                if hasattr(previous_claim.review_state, "value")
                else str(previous_claim.review_state)
            )
            is_escalated = (current_state == ClaimReviewState.ESCALATED.value)

            if current_state not in (ClaimReviewState.PENDING.value, ClaimReviewState.ESCALATED.value):
                raise InvalidStateTransitionError(
                    f"Cannot EDIT claim from '{current_state}'. Allowed origin states are PENDING or ESCALATED."
                )

            self._check_actor_authorization(session, actor_uuid, required_supervisory=is_escalated)
            self._lock_and_validate_lineage(session, previous_claim, actor_uuid, is_accept=False)

            # 1. Update previous claim's review_state to EDITED (superseded and permanently ineligible for export)
            previous_claim.review_state = ClaimReviewState.EDITED

            # 2. Assemble new claim version
            new_version = previous_claim.version + 1
            canonical_field_type = _normalize_field_type(previous_claim.field_type)
            norm_code = new_normalized_code if new_normalized_code is not None else previous_claim.normalized_code
            raw_cs = str(new_code_system if new_code_system is not None else (previous_claim.code_system or "")).strip()
            if "rxnorm" in raw_cs.lower():
                code_sys = "RxNorm"
            elif "loinc" in raw_cs.lower():
                code_sys = "LOINC"
            elif "ucum" in raw_cs.lower():
                code_sys = "UCUM"
            else:
                code_sys = raw_cs or None

            new_payload = copy.deepcopy(previous_claim.payload) if previous_claim.payload else {}
            new_payload.update({
                "claim_id": str(previous_claim.claim_id),
                "document_id": str(previous_claim.document_id),
                "group_id": previous_claim.group_id,
                "field_type": canonical_field_type,
                "value": new_value.strip(),
                "normalized_code": norm_code,
                "code_system": code_sys,
                "version": new_version,
                "origin": ClaimOrigin.REVIEWER.value,
                "review_state": ClaimReviewState.PENDING.value,
                "supersedes_claim_id": str(previous_claim.id),
                "edited_by": str(actor_uuid),
                "edit_reason_code": reason_code.strip(),
            })

            new_claim = Claim(
                id=uuid.uuid4(),
                claim_id=previous_claim.claim_id,
                document_id=previous_claim.document_id,
                group_id=previous_claim.group_id,
                field_type=canonical_field_type,
                value=new_value.strip(),
                normalized_code=norm_code,
                code_system=code_sys,
                version=new_version,
                supersedes_claim_id=previous_claim.id,
                origin=ClaimOrigin.REVIEWER,
                review_state=ClaimReviewState.PENDING,
                created_by=actor_uuid,
                created_at=datetime.now(timezone.utc),
                evidence_id=previous_claim.evidence_id,
                model_run_id=None,
                payload=new_payload,
            )
            session.add(new_claim)
            session.flush()

            # 3. Resolve Grounding Evidence for Inline Re-Verification
            db_evidence: Optional[Evidence] = None
            contract_evidence: Optional[ContractEvidence] = None
            if new_claim.evidence_id:
                db_evidence = session.get(Evidence, new_claim.evidence_id)
                if db_evidence and db_evidence.evidence_text and db_evidence.evidence_text.strip():
                    bbox_data = db_evidence.bounding_box or [0.0, 0.0, 1.0, 1.0]
                    if isinstance(bbox_data, list) and len(bbox_data) == 4:
                        bbox_model = BBoxModel.from_list(bbox_data)
                    elif isinstance(bbox_data, dict) and "x0" in bbox_data:
                        bbox_model = BBoxModel(**bbox_data)
                    else:
                        bbox_model = BBoxModel(x0=0.0, y0=0.0, x1=1.0, y1=1.0)

                    contract_evidence = ContractEvidence(
                        evidence_id=str(db_evidence.id),
                        document_id=str(db_evidence.document_id),
                        page_number=max(1, db_evidence.page_number),
                        text=db_evidence.evidence_text.strip(),
                        char_start=max(0, db_evidence.char_start),
                        char_end=max(max(0, db_evidence.char_start), db_evidence.char_end),
                        bbox=bbox_model,
                        ocr_confidence=db_evidence.ocr_confidence,
                    )

            # 4. Construct ContractClaim for Cascade
            extraction_method = (
                new_payload.get("extraction_method")
                if new_payload.get("extraction_method") in ("deterministic", "gliner", "llm_narrative")
                else "deterministic"
            )
            contract_claim = ContractClaim(
                claim_id=str(new_claim.claim_id),
                document_id=str(new_claim.document_id),
                group_id=new_claim.group_id or "default",
                field_type=canonical_field_type,  # type: ignore[arg-type]
                value=new_claim.value,
                evidence_id=str(new_claim.evidence_id) if new_claim.evidence_id else "missing",
                extraction_method=extraction_method,
                extraction_confidence=float(new_payload.get("extraction_confidence", 1.0)),
                region_id=str(new_payload.get("region_id", new_claim.group_id or "default")),
                normalized_code=new_claim.normalized_code,
                code_system=new_claim.code_system,  # type: ignore[arg-type]
            )

            # 5. Inline Re-Verification via VerificationCascade (Stages A/B)
            cascade_res = self.cascade.verify_claim(
                claim=contract_claim,
                evidence=contract_evidence,
                page_text=page_text,
            )

            db_vr = DBVerificationResult(
                id=uuid.uuid4(),
                claim_id=new_claim.id,
                label=_map_verification_label(cascade_res.label),
                calibrated_confidence=float(cascade_res.confidence),
                verification_stage=_map_verification_stage(cascade_res.stage_reached),
                escalated=bool(cascade_res.escalated),
                is_fallback=False,
                provider_kind="cascade_reverification",
                model_run_id=None,
                contradiction_type=_map_contradiction_type(cascade_res.contradiction_type),
                rationale=cascade_res.rationale,
                stage_a_details={"label": cascade_res.stage_a_label} if cascade_res.stage_a_label else None,
                stage_b_details={"label": cascade_res.stage_b_label, "raw_confidence": cascade_res.stage_b_raw_confidence} if cascade_res.stage_b_label else None,
                stage_c_details={"rationale": cascade_res.rationale} if cascade_res.rationale else None,
                payload=cascade_res.model_dump(mode="json"),
            )
            session.add(db_vr)
            session.flush()

            # 6. Re-evaluate Triage Policy (Mode 0)
            triage_input = ClaimTriageInput.from_orm(
                claim=new_claim,
                verification_result=db_vr,
                evidence=db_evidence,
                flags={"has_injection_flag": False, "has_identity_mismatch": False},
            )

            if self.triage_engine is not None:
                engine = self.triage_engine
            else:
                active_policy = session.query(TriagePolicy).filter(TriagePolicy.is_active.is_(True)).first()
                engine = TriageEngine(policy=active_policy)

            triage_result = engine.classify_claim(triage_input)

            # 7. Persist ReviewAction (linking to the newly created version)
            action = ReviewAction(
                id=uuid.uuid4(),
                claim_id=new_claim.id,
                actor_id=actor_uuid,
                action_type=ReviewActionType.EDIT,
                previous_state=current_state,
                new_state=ClaimReviewState.EDITED.value,
                reason_code=reason_code.strip(),
                notes=notes,
                before_payload=previous_claim.payload,
                after_payload=new_claim.payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(action)
            session.flush()

            # 8. Cryptographic Hash-Chained Audit Logging
            audit_metadata = {
                "previous_claim_id": str(previous_claim.id),
                "new_claim_id": str(new_claim.id),
                "verification_verdict": db_vr.label.value if hasattr(db_vr.label, "value") else str(db_vr.label),
                "verification_stage": db_vr.verification_stage.value if hasattr(db_vr.verification_stage, "value") else str(db_vr.verification_stage),
                "triage_category": triage_result.category.value if hasattr(triage_result.category, "value") else str(triage_result.category),
                "triage_risk_tier": triage_result.risk_tier.value if hasattr(triage_result.risk_tier, "value") else str(triage_result.risk_tier),
                "triage_priority_rank": triage_result.priority_rank,
            }
            self._record_audit_event(
                session,
                actor_id=actor_uuid,
                target_claim=new_claim,
                action=ReviewActionType.EDIT.value,
                previous_state=current_state,
                new_state=ClaimReviewState.EDITED.value,
                reason_code=reason_code.strip(),
                extra_metadata=audit_metadata,
                document_sha256=document_sha256,
            )

            if commit:
                session.commit()

            return EditClaimResult(
                new_claim=new_claim,
                previous_claim=previous_claim,
                verification_result=db_vr,
                triage_result=triage_result,
                review_action=action,
            )

        except Exception:
            session.rollback()
            raise

    def resolve_escalation(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        resolution_action: str,  # "ACCEPT", "REJECT", "EDIT"
        expected_version: int,
        reason_code: str,
        notes: Optional[str] = None,
        document_sha256: Optional[str] = None,
        new_value: Optional[str] = None,
        new_normalized_code: Optional[str] = None,
        new_code_system: Optional[str] = None,
        page_text: Optional[str] = None,
        commit: bool = True,
    ) -> Claim | EditClaimResult:
        """Resolve an ESCALATED claim with supervisory authorization."""
        actor_uuid = self._parse_actor_uuid(actor_id)
        self._check_actor_authorization(session, actor_uuid, required_supervisory=True)

        action_upper = resolution_action.strip().upper()
        if action_upper == "ACCEPT":
            return self.accept_claim(
                session=session,
                claim_id=claim_id,
                actor_id=actor_uuid,
                expected_version=expected_version,
                reason_code=reason_code,
                notes=notes,
                document_sha256=document_sha256,
                commit=commit,
            )
        elif action_upper == "REJECT":
            return self.reject_claim(
                session=session,
                claim_id=claim_id,
                actor_id=actor_uuid,
                reason_code=reason_code,
                expected_version=expected_version,
                notes=notes,
                document_sha256=document_sha256,
                commit=commit,
            )
        elif action_upper == "EDIT":
            if not new_value:
                raise ValueError("new_value is required when resolving escalation with EDIT.")
            return self.edit_claim(
                session=session,
                claim_id=claim_id,
                actor_id=actor_uuid,
                new_value=new_value,
                reason_code=reason_code,
                expected_version=expected_version,
                new_normalized_code=new_normalized_code,
                new_code_system=new_code_system,
                notes=notes,
                page_text=page_text,
                document_sha256=document_sha256,
                commit=commit,
            )
        else:
            raise InvalidStateTransitionError(
                f"Invalid escalation resolution action '{resolution_action}'. Must be ACCEPT, REJECT, or EDIT."
            )

    def batch_review_claims(
        self,
        session: Session,
        reviews: Sequence[dict[str, Any]],
        actor_id: Union[uuid.UUID, str],
        commit: bool = True,
    ) -> list[Union[Claim, EditClaimResult]]:
        """Atomically review a batch of claims with canonical root lock ordering to guarantee deadlock freedom."""
        actor_uuid = self._parse_actor_uuid(actor_id)
        if not reviews:
            return []

        try:
            # 1. Resolve all claims to discover their stable claim_id
            resolved_items = []
            for item in reviews:
                raw_cid = item.get("claim_id")
                exp_ver = item.get("expected_version")
                target = self._resolve_claim(session, raw_cid, exp_ver)
                resolved_items.append((target, item))

            # 2. Extract unique stable lineage IDs and sort alphanumerically to prevent deadlocks
            unique_lineages = sorted(
                list({str(target.claim_id) for target, _ in resolved_items})
            )

            # 3. Pre-acquire root locks on genesis version 1 for all lineages in deterministic order
            for lin_id_str in unique_lineages:
                lin_uuid = uuid.UUID(lin_id_str)
                root_claim = (
                    session.query(Claim)
                    .filter(Claim.claim_id == lin_uuid, Claim.version == 1)
                    .with_for_update()
                    .populate_existing()
                    .one_or_none()
                )
                if root_claim is None:
                    raise CorruptedClaimAncestryError(
                        f"Corrupted claim ancestry: Genesis version 1 for lineage '{lin_uuid}' not found."
                    )

            # 4. Execute each review action within the transaction
            results: list[Union[Claim, EditClaimResult]] = []
            for target, item in resolved_items:
                action_type = str(item.get("action", "")).upper()
                c_id = item.get("claim_id")
                e_ver = item.get("expected_version")
                r_code = item.get("reason_code")
                notes = item.get("notes")
                doc_sha = item.get("document_sha256")

                if action_type == "ACCEPT":
                    res = self.accept_claim(
                        session=session,
                        claim_id=c_id,
                        actor_id=actor_uuid,
                        expected_version=e_ver,
                        reason_code=r_code,
                        notes=notes,
                        document_sha256=doc_sha,
                        commit=False,
                    )
                elif action_type == "REJECT":
                    res = self.reject_claim(
                        session=session,
                        claim_id=c_id,
                        actor_id=actor_uuid,
                        reason_code=r_code,
                        expected_version=e_ver,
                        notes=notes,
                        document_sha256=doc_sha,
                        commit=False,
                    )
                elif action_type == "ESCALATE":
                    res = self.escalate_claim(
                        session=session,
                        claim_id=c_id,
                        actor_id=actor_uuid,
                        expected_version=e_ver,
                        reason_code=r_code,
                        notes=notes,
                        document_sha256=doc_sha,
                        commit=False,
                    )
                elif action_type == "REOPEN":
                    res = self.reopen_claim(
                        session=session,
                        claim_id=c_id,
                        actor_id=actor_uuid,
                        expected_version=e_ver,
                        reason_code=r_code,
                        notes=notes,
                        document_sha256=doc_sha,
                        commit=False,
                    )
                elif action_type == "EDIT":
                    res = self.edit_claim(
                        session=session,
                        claim_id=c_id,
                        actor_id=actor_uuid,
                        new_value=item["new_value"],
                        reason_code=r_code,
                        expected_version=e_ver,
                        new_normalized_code=item.get("new_normalized_code"),
                        new_code_system=item.get("new_code_system"),
                        notes=notes,
                        page_text=item.get("page_text"),
                        document_sha256=doc_sha,
                        commit=False,
                    )
                else:
                    raise ValueError(f"Unsupported review action '{action_type}' in batch_review_claims.")
                results.append(res)

            if commit:
                session.commit()
            return results

        except Exception:
            session.rollback()
            raise
