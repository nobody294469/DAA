"""Transactional-Outbox Projection Engine for Clinical Audit Ledgers.

Conforms to CLINICALDOC_STEP3_DEF003_IMPLEMENTATION_EXECUTION_PLAN.md (§4, §6).

Guarantees:
1. PostgreSQL-Authoritative Outbox: PostgreSQL `audit_events` is the single source of truth.
2. Safe Outbox Replay: Synchronizes lagging `<ledger>.jsonl` from committed database events under `<ledger>.lock`.
3. Crash Consistency & Idempotency: Multiple sequential or concurrent runs yield identical state with zero duplicate appends.
4. Monotonic Cursor: Strict contiguous sequence progression from S_file to S_db.
5. Fail-Closed Boundary Invariants:
   - Split-brain (S_file > S_db) raises SplitBrainLedgerError.
   - Missing ledger with committed DB history (S_db > 0, file missing) raises AuditLedgerMissingError.
   - Hash chain discontinuity between JSONL and DB raises AuditLedgerTamperedError.
6. Atomic Checkpoint Durability: Checkpoints are updated via atomic temporary file replacement with fsync.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy.orm import Session

from clindoc.api.audit.service import (
    compute_audit_event_hash,
    validate_metadata_phi_free,
)
from clindoc.audit.locking import (
    AuditFileLock,
    atomic_write_file,
    flush_and_fsync,
    fsync_directory,
    locked_audit_ledger,
)
from clindoc.db.models import AuditEvent

logger = logging.getLogger(__name__)


# ─── EXCEPTIONS ───────────────────────────────────────────────────────────────

class AuditProjectionError(Exception):
    """Base exception for outbox projection failures."""


class SplitBrainLedgerError(AuditProjectionError):
    """Raised when the JSONL ledger contains sequences higher than the database (B-ANOM-1)."""


class AuditLedgerMissingError(AuditProjectionError):
    """Raised when the JSONL file is missing but database history exists (B-ANOM-0)."""


class AuditLedgerTamperedError(AuditProjectionError):
    """Raised when an unrecoverable hash chain or payload mismatch is detected (B-ANOM-3)."""


# ─── DATA STRUCTURES ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProjectionResult:
    """Outcome of an outbox projection reconciliation sweep."""
    projected_count: int
    initial_sequence: int
    final_sequence: int
    terminal_hash: str
    checkpoint_updated: bool


# ─── CANONICAL HELPERS ────────────────────────────────────────────────────────

def _serialize_canonical_json(obj: Any) -> bytes:
    """Serialize Python object to RFC 8785 Canonical JSON bytes."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _build_checkpoint_payload(
    *,
    document_id: str,
    document_sha256: str,
    checkpoint_sequence: int,
    terminal_hash: str,
    key_id: str,
    timestamp: Optional[datetime] = None,
) -> dict[str, Any]:
    """Construct canonical checkpoint payload dictionary."""
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


def _compute_checkpoint_signature(payload: dict[str, Any], key_bytes: bytes) -> str:
    """Compute HMAC-SHA256 signature over canonical checkpoint JSON."""
    payload_bytes = _serialize_canonical_json(payload)
    return hmac.new(key_bytes, payload_bytes, hashlib.sha256).hexdigest()


# ─── PROJECTION ENGINE ────────────────────────────────────────────────────────

class AuditProjectionEngine:
    """Centralized engine for projecting committed PostgreSQL audit events to JSONL projections."""

    @classmethod
    def validate_ledger_prefix(
        cls,
        session: Session,
        ledger_path: Path,
        max_db_sequence: int,
    ) -> Tuple[int, str]:
        """Validate every existing JSONL record from sequence 1 through S_file against PostgreSQL.

        Verifies:
          1. Valid JSON structure and non-empty content on every line.
          2. Strict monotonic sequence numbers: 1, 2, ..., S_file with no gaps or duplicates.
          3. Unbroken hash chain linkage: sequence 1 has previous_event_hash == "",
             and line[k] previous_event_hash == line[k-1] current_event_hash.
          4. Complete 1-to-1 correspondence with committed PostgreSQL audit_events:
             - Sequence number matches.
             - current_event_hash matches DB record current_event_hash.
             - previous_event_hash matches (db_event.previous_event_hash or "").
             - actor_id, event_type, entity_type, entity_id, action match DB record.
             - metadata matches (db_event.metadata_json or {}).
             - current_event_hash matches recomputed compute_audit_event_hash.
          5. Rejects split-brain: S_file > max_db_sequence raises SplitBrainLedgerError.
          6. Rejects any tampering, modification, reordering, or corruption:
             raises AuditLedgerTamperedError and fails closed.

        Returns:
            Tuple of (s_file, tail_hash). Returns (0, "") if file is empty or missing.
        """
        if not ledger_path.exists() or ledger_path.stat().st_size == 0:
            return 0, ""

        db_events: Dict[int, AuditEvent] = {}
        if max_db_sequence > 0:
            events = (
                session.query(AuditEvent)
                .filter(AuditEvent.sequence_number <= max_db_sequence)
                .order_by(AuditEvent.sequence_number.asc())
                .all()
            )
            db_events = {e.sequence_number: e for e in events}

        expected_seq = 1
        expected_prev_hash = ""
        tail_hash = ""
        has_lines = False

        with open(ledger_path, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f, start=1):
                line_s = line.strip()
                if not line_s:
                    continue
                has_lines = True

                try:
                    record = json.loads(line_s)
                except Exception as exc:
                    raise AuditLedgerTamperedError(
                        f"Line {line_idx} in audit ledger {ledger_path} is malformed JSON: {exc}"
                    ) from exc

                seq = record.get("sequence_number")
                if not isinstance(seq, int):
                    raise AuditLedgerTamperedError(
                        f"Line {line_idx} in audit ledger {ledger_path} has invalid sequence_number: {seq}"
                    )

                if seq > max_db_sequence:
                    raise SplitBrainLedgerError(
                        f"Split-brain detected: JSONL ledger sequence {seq} at line {line_idx} exceeds "
                        f"authoritative database sequence {max_db_sequence} (B-ANOM-1)."
                    )

                if seq != expected_seq:
                    raise AuditLedgerTamperedError(
                        f"Sequence discontinuity in audit ledger {ledger_path} at line {line_idx}: "
                        f"expected {expected_seq}, found {seq}."
                    )

                prev_h = str(record.get("previous_event_hash") or "").strip()
                if prev_h != expected_prev_hash:
                    raise AuditLedgerTamperedError(
                        f"Hash chain linkage break in audit ledger {ledger_path} at sequence {seq}: "
                        f"expected previous '{expected_prev_hash}', found '{prev_h}'."
                    )

                curr_h = str(record.get("current_event_hash") or "").strip()
                if not curr_h or len(curr_h) != 64:
                    raise AuditLedgerTamperedError(
                        f"Invalid current_event_hash in audit ledger {ledger_path} at sequence {seq}: '{curr_h}'."
                    )

                db_evt = db_events.get(seq)
                if db_evt is None:
                    raise SplitBrainLedgerError(
                        f"Split-brain detected: JSONL ledger sequence {seq} does not exist in authoritative "
                        f"PostgreSQL audit log (B-ANOM-1)."
                    )

                # Authoritative field correspondence
                if curr_h != db_evt.current_event_hash:
                    raise AuditLedgerTamperedError(
                        f"Audit hash mismatch at sequence {seq}: JSONL current_event_hash '{curr_h}' "
                        f"does not match authoritative database hash '{db_evt.current_event_hash}'."
                    )

                db_prev = str(db_evt.previous_event_hash or "").strip()
                if prev_h != db_prev:
                    raise AuditLedgerTamperedError(
                        f"Audit previous_event_hash mismatch at sequence {seq}: JSONL previous '{prev_h}' "
                        f"does not match database previous '{db_prev}'."
                    )

                if str(record.get("actor_id", "")).strip() != str(db_evt.actor_id or "").strip():
                    raise AuditLedgerTamperedError(
                        f"Audit payload actor_id mismatch at sequence {seq}."
                    )
                if str(record.get("event_type", "")).strip() != str(db_evt.event_type or "").strip():
                    raise AuditLedgerTamperedError(
                        f"Audit payload event_type mismatch at sequence {seq}."
                    )
                if str(record.get("entity_type", "")).strip() != str(db_evt.entity_type or "").strip():
                    raise AuditLedgerTamperedError(
                        f"Audit payload entity_type mismatch at sequence {seq}."
                    )
                if str(record.get("entity_id", "")).strip() != str(db_evt.entity_id or "").strip():
                    raise AuditLedgerTamperedError(
                        f"Audit payload entity_id mismatch at sequence {seq}."
                    )
                if str(record.get("action", "")).strip() != str(db_evt.action or "").strip():
                    raise AuditLedgerTamperedError(
                        f"Audit payload action mismatch at sequence {seq}."
                    )

                line_meta = record.get("metadata") if record.get("metadata") is not None else {}
                db_meta = db_evt.metadata_json if db_evt.metadata_json is not None else {}
                if line_meta != db_meta:
                    raise AuditLedgerTamperedError(
                        f"Audit payload metadata mismatch at sequence {seq}."
                    )

                # Authoritative cryptographic recalculation
                recomputed_db_hash = compute_audit_event_hash(
                    sequence_number=seq,
                    timestamp=db_evt.timestamp,
                    actor_id=db_evt.actor_id,
                    event_type=db_evt.event_type,
                    entity_type=db_evt.entity_type,
                    entity_id=db_evt.entity_id,
                    action=db_evt.action,
                    metadata_json=db_evt.metadata_json,
                    previous_event_hash=db_evt.previous_event_hash,
                )
                if curr_h != recomputed_db_hash:
                    raise AuditLedgerTamperedError(
                        f"Authoritative cryptographic recalculation failed at sequence {seq}."
                    )

                expected_prev_hash = curr_h
                tail_hash = curr_h
                expected_seq += 1

        if not has_lines:
            return 0, ""

        return expected_seq - 1, tail_hash

    @staticmethod
    def inspect_ledger_tail(ledger_path: Path) -> Tuple[int, str]:
        """Inspect the last valid event line in the JSONL ledger file.

        Returns:
            Tuple of (tail_sequence_number, tail_current_event_hash).
            Returns (0, "") if the file is missing or empty.
        """
        if not ledger_path.exists() or ledger_path.stat().st_size == 0:
            return 0, ""

        last_valid_line = None
        with open(ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line_s = line.strip()
                if line_s:
                    last_valid_line = line_s

        if not last_valid_line:
            return 0, ""

        try:
            record = json.loads(last_valid_line)
            seq = int(record.get("sequence_number", 0))
            curr_hash = str(record.get("current_event_hash", "")).strip()
            return seq, curr_hash
        except Exception as exc:
            raise AuditLedgerTamperedError(
                f"Trailing line in audit ledger {ledger_path} is corrupted: {exc}"
            ) from exc

    @classmethod
    def recover_torn_tail(
        cls,
        ledger_path: Union[str, Path],
        timeout: float = 10.0,
        _force_posix_mode: bool = False,
    ) -> Path:
        """Safely quarantine corrupted ledger and truncate torn tail under file lock."""
        from clindoc.audit.quarantine import recover_torn_tail
        return recover_torn_tail(ledger_path, timeout=timeout, _force_posix_mode=_force_posix_mode)

    @classmethod
    def reconcile_outbox(
        cls,
        session: Session,
        ledger_path: Union[str, Path],
        checkpoint_path: Optional[Union[str, Path]] = None,
        keyring: Optional[Dict[str, bytes]] = None,
        key_id: str = "default",
        hmac_key: Optional[bytes] = None,
        document_id: Optional[str] = None,
        document_sha256: Optional[str] = None,
        timeout: float = 10.0,
    ) -> ProjectionResult:
        """Synchronize committed PostgreSQL audit events to the JSONL ledger file.

        Lifecycle (Revision 2.7 §6.2):
          1. Acquire OS file lock `<ledger>.lock`.
          2. Query committed DB sequence max: `S_db = MAX(sequence_number)`.
          3. Inspect and validate existing projection prefix (1..S_file) against DB.
          4. Check split-brain: if S_file > S_db, raise SplitBrainLedgerError.
          5. Check missing file: if S_db > 0 and ledger does not exist, raise AuditLedgerMissingError.
          6. If S_db == S_file, ensure checkpoint is current, return no-op.
          7. Fetch all missing committed events: `sequence_number > S_file` ordered by ASC.
          8. Format authoritative JSON lines from PostgreSQL records with hash_algorithm marker.
          9. Append lines to `<ledger>.jsonl`.
          10. Call `flush()` and `os.fsync()`.
          11. Advance HMAC checkpoint to S_db using atomic temporary file replace.
          12. Release lock and return ProjectionResult.
        """
        l_path = Path(ledger_path)
        c_path = Path(checkpoint_path) if checkpoint_path is not None else l_path.with_suffix(".chkpt")

        with locked_audit_ledger(l_path, timeout=timeout):
            # 1. Inspect DB Tail
            latest_db_event = (
                session.query(AuditEvent)
                .order_by(AuditEvent.sequence_number.desc())
                .first()
            )
            s_db = latest_db_event.sequence_number if latest_db_event is not None else 0
            db_terminal_hash = latest_db_event.current_event_hash if latest_db_event is not None else ""

            # 2. Anomaly Verification: Missing file with committed DB history (B-ANOM-0)
            file_exists = l_path.exists()
            if not file_exists and s_db > 0:
                raise AuditLedgerMissingError(
                    f"Audit ledger file '{l_path}' is missing but PostgreSQL contains {s_db} "
                    f"committed audit events. Automatic silent recreation is strictly prohibited (B-ANOM-0)."
                )

            # 3. Anomaly Verification: Split-Brain (B-ANOM-1)
            tail_seq, _ = cls.inspect_ledger_tail(l_path)
            if tail_seq > s_db:
                raise SplitBrainLedgerError(
                    f"Split-brain detected: JSONL ledger sequence {tail_seq} exceeds "
                    f"authoritative database sequence {s_db} (B-ANOM-1)."
                )

            # 4. Complete Prefix Verification of Existing Ledger (1..S_file) against PostgreSQL
            s_file, tail_hash = cls.validate_ledger_prefix(session, l_path, max_db_sequence=s_db)

            # 5. Up-to-Date Idempotency Check (TC-16)
            if s_db == s_file:
                # File matches DB and prefix was 100% verified; ensure checkpoint is written if key available
                checkpoint_updated = cls._ensure_checkpoint(
                    checkpoint_path=c_path,
                    sequence_number=s_file,
                    terminal_hash=tail_hash,
                    keyring=keyring,
                    key_id=key_id,
                    hmac_key=hmac_key,
                    document_id=document_id,
                    document_sha256=document_sha256,
                )
                return ProjectionResult(
                    projected_count=0,
                    initial_sequence=s_file,
                    final_sequence=s_file,
                    terminal_hash=tail_hash,
                    checkpoint_updated=checkpoint_updated,
                )

            # 6. Fetch Missing Committed Events
            missing_events: List[AuditEvent] = (
                session.query(AuditEvent)
                .filter(AuditEvent.sequence_number > s_file)
                .order_by(AuditEvent.sequence_number.asc())
                .all()
            )

            if not missing_events:
                return ProjectionResult(
                    projected_count=0,
                    initial_sequence=s_file,
                    final_sequence=s_file,
                    terminal_hash=tail_hash,
                    checkpoint_updated=False,
                )

            # Verify continuity between file tail and first DB event
            first_missing = missing_events[0]
            if first_missing.sequence_number != s_file + 1:
                raise AuditLedgerTamperedError(
                    f"Sequence discontinuity during outbox projection: expected {s_file + 1}, "
                    f"found {first_missing.sequence_number}."
                )

            if s_file > 0 and first_missing.previous_event_hash != tail_hash:
                raise AuditLedgerTamperedError(
                    f"Hash linkage mismatch during outbox projection: JSONL tail hash is '{tail_hash}', "
                    f"but DB sequence {first_missing.sequence_number} previous hash is '{first_missing.previous_event_hash}'."
                )

            # 7. Format and Append Lines to JSONL
            l_path.parent.mkdir(parents=True, exist_ok=True)
            new_terminal_hash = tail_hash

            with open(l_path, "a", encoding="utf-8") as f:
                for event in missing_events:
                    ts = event.timestamp
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    else:
                        ts = ts.astimezone(timezone.utc)
                    ts_iso = ts.isoformat()

                    meta = copy.deepcopy(event.metadata_json) if event.metadata_json is not None else {}
                    validate_metadata_phi_free(meta)

                    line_record = {
                        "action": str(event.action).strip(),
                        "actor_id": str(event.actor_id).strip(),
                        "current_event_hash": str(event.current_event_hash).strip(),
                        "entity_id": str(event.entity_id).strip(),
                        "entity_type": str(event.entity_type).strip(),
                        "event_type": str(event.event_type).strip(),
                        "hash_algorithm": "sha256_audit_v1",
                        "metadata": meta,
                        "previous_event_hash": str(event.previous_event_hash or "").strip(),
                        "sequence_number": int(event.sequence_number),
                        "timestamp": ts_iso,
                    }

                    line_json = json.dumps(
                        line_record,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                    f.write(line_json + "\n")
                    new_terminal_hash = line_record["current_event_hash"]

                # 8. Durability Flush
                flush_and_fsync(f)

            # 9. Update Checkpoint via Atomic Temporary Replace
            checkpoint_updated = cls._ensure_checkpoint(
                checkpoint_path=c_path,
                sequence_number=s_db,
                terminal_hash=new_terminal_hash,
                keyring=keyring,
                key_id=key_id,
                hmac_key=hmac_key,
                document_id=document_id,
                document_sha256=document_sha256,
            )

            return ProjectionResult(
                projected_count=len(missing_events),
                initial_sequence=s_file,
                final_sequence=s_db,
                terminal_hash=new_terminal_hash,
                checkpoint_updated=checkpoint_updated,
            )

    @classmethod
    def _ensure_checkpoint(
        cls,
        checkpoint_path: Path,
        sequence_number: int,
        terminal_hash: str,
        keyring: Optional[Dict[str, bytes]] = None,
        key_id: str = "default",
        hmac_key: Optional[bytes] = None,
        document_id: Optional[str] = None,
        document_sha256: Optional[str] = None,
    ) -> bool:
        """Write or advance HMAC checkpoint if signing key is available."""
        if sequence_number <= 0:
            return False

        secret = hmac_key
        if secret is None and keyring and key_id in keyring:
            secret = keyring[key_id]
        if secret is None:
            env_key = os.getenv(f"CLINDOC_AUDIT_HMAC_KEY_{key_id.upper()}") or os.getenv("CLINDOC_AUDIT_HMAC_KEY")
            if env_key:
                secret = env_key.encode("utf-8")

        if not secret:
            logger.debug("HMAC key '%s' not available; checkpoint update omitted.", key_id)
            return False

        # If existing checkpoint is already at or beyond this sequence, check if update needed
        if checkpoint_path.exists():
            try:
                curr_chkpt = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                curr_seq = int(curr_chkpt.get("payload", {}).get("checkpoint_sequence", 0))
                if curr_seq >= sequence_number:
                    return False
            except Exception:
                pass

        payload = _build_checkpoint_payload(
            document_id=document_id or "unknown",
            document_sha256=document_sha256 or "unknown",
            checkpoint_sequence=sequence_number,
            terminal_hash=terminal_hash,
            key_id=key_id,
        )
        sig = _compute_checkpoint_signature(payload, secret)
        chkpt_data = {
            "payload": payload,
            "signature": sig,
        }

        content_bytes = json.dumps(chkpt_data, indent=2, sort_keys=True).encode("utf-8")
        atomic_write_file(checkpoint_path, content_bytes, sync_dir=True)
        return True

    @classmethod
    def verify_bundle_complete_attestation(
        cls,
        session: Session,
        bundle_dict: dict[str, Any],
        ledger_path: Union[str, Path],
        checkpoint_path: Optional[Union[str, Path]] = None,
        trust_store: Optional[Dict[str, Any]] = None,
        revoked_key_ids: Optional[Set[str]] = None,
        keyring: Optional[Dict[str, bytes]] = None,
        key_id: str = "default",
        hmac_key: Optional[bytes] = None,
        timeout: float = 10.0,
    ) -> Tuple[bytes, str]:
        """Complete Attestation Gateway (Revision 2.7 §7.2).

        Verifies:
          1. Embedded audit provenance extension (auditSequenceNumber, auditEventHash, clinicalContentDigest).
          2. PostgreSQL committed audit event at auditSequenceNumber matches hash and clinical digest.
          3. Cryptographic Bundle.signature against trusted keys (Tier 2).
          4. Recomputed RFC 8785 canonical clinical content digest matches extension (Tier 1).
          5. OS File-Locked Ledger Attestation:
             - JSONL ledger is synchronized to at least auditSequenceNumber (S_file >= S_bundle).
             - Full prefix verification from sequence 1 to S_file against PostgreSQL.
             - JSONL record at auditSequenceNumber matches DB record bitwise/canonically.
             - HMAC Checkpoint covers at least auditSequenceNumber (S_chkpt >= S_bundle) with valid HMAC signature.
          6. Returns immutable transport byte buffer and verified transport_sha256 (Tier 3).
        """
        from clindoc.fhir.signing import (
            extract_audit_provenance,
            verify_bundle_signature,
            compute_clinical_content_digest,
            serialize_transport_payload,
            IncompleteAttestationError,
            AuditProvenanceBindingMismatchError,
            AttestationEquivalenceMismatchError,
            MissingBundleSignatureError,
        )

        # 1. Extract Audit Provenance Extension
        audit_seq, audit_hash, clinical_digest = extract_audit_provenance(bundle_dict)

        # 2. Verify Authoritative PostgreSQL Audit Event
        db_evt = (
            session.query(AuditEvent)
            .filter(AuditEvent.sequence_number == audit_seq)
            .first()
        )
        if db_evt is None:
            raise IncompleteAttestationError(
                f"Bundle audit sequence {audit_seq} is not committed in PostgreSQL."
            )
        if db_evt.current_event_hash != audit_hash:
            raise AuditProvenanceBindingMismatchError(
                f"Bundle auditEventHash '{audit_hash}' does not match PostgreSQL event {audit_seq} hash '{db_evt.current_event_hash}'."
            )

        db_meta = db_evt.metadata_json or {}
        db_clin_digest = db_meta.get("clinical_content_sha256")
        if db_clin_digest and db_clin_digest != clinical_digest:
            raise AuditProvenanceBindingMismatchError(
                f"Clinical content digest '{clinical_digest}' does not match DB audit event metadata '{db_clin_digest}'."
            )

        # 3. Verify Tier 2 Cryptographic Signature if signature present or trust_store provided
        if trust_store is not None or "signature" in bundle_dict:
            if "signature" not in bundle_dict:
                raise MissingBundleSignatureError("Bundle lacks mandatory Bundle.signature element.")
            if trust_store is not None:
                verify_bundle_signature(bundle_dict, trust_store, revoked_key_ids)

        # 4. Verify Tier 1 Recomputed Clinical Content Digest
        recomputed_digest = compute_clinical_content_digest(bundle_dict)
        if recomputed_digest != clinical_digest:
            raise AttestationEquivalenceMismatchError(
                f"Recomputed clinical content digest '{recomputed_digest}' does not match bundle provenance extension '{clinical_digest}'."
            )

        # 5. Acquire OS File Lock & Verify Ledger + Checkpoint Attestation
        l_path = Path(ledger_path)
        c_path = Path(checkpoint_path) if checkpoint_path is not None else l_path.with_suffix(".chkpt")

        with locked_audit_ledger(l_path, timeout=timeout):
            if not l_path.exists() or l_path.stat().st_size == 0:
                raise IncompleteAttestationError(f"Incomplete Attestation: Audit ledger file '{l_path}' is missing or empty.")

            s_file, tail_h = cls.inspect_ledger_tail(l_path)
            if s_file < audit_seq:
                # Incomplete attestation: ledger lagging behind committed DB bundle
                raise IncompleteAttestationError(
                    f"Incomplete Attestation: JSONL ledger sequence {s_file} is behind bundle sequence {audit_seq}."
                )

            # Validate complete ledger prefix up to s_file
            cls.validate_ledger_prefix(session, l_path, s_file)

            # Verify Checkpoint
            if not c_path.exists() or c_path.stat().st_size == 0:
                raise IncompleteAttestationError(f"Incomplete Attestation: HMAC checkpoint file '{c_path}' is missing or empty.")

            try:
                chkpt_json = json.loads(c_path.read_text(encoding="utf-8"))
                payload = chkpt_json.get("payload", {})
                sig = chkpt_json.get("signature", "")
            except Exception as exc:
                raise IncompleteAttestationError(f"Incomplete Attestation: HMAC checkpoint file '{c_path}' is unparseable: {exc}") from exc

            chkpt_seq = int(payload.get("checkpoint_sequence", 0))
            if chkpt_seq < audit_seq:
                raise IncompleteAttestationError(
                    f"Incomplete Attestation: Checkpoint sequence {chkpt_seq} is behind bundle sequence {audit_seq}."
                )

            secret = hmac_key
            if secret is None and keyring and key_id in keyring:
                secret = keyring[key_id]
            if secret is None:
                env_key = os.getenv(f"CLINDOC_AUDIT_HMAC_KEY_{key_id.upper()}") or os.getenv("CLINDOC_AUDIT_HMAC_KEY")
                if env_key:
                    secret = env_key.encode("utf-8")

            if secret:
                expected_sig = _compute_checkpoint_signature(payload, secret)
                if sig != expected_sig:
                    raise IncompleteAttestationError("HMAC checkpoint signature verification failed.")

        # 6. Tier 3 Immutable Serialization
        exact_bytes, transport_sha256 = serialize_transport_payload(bundle_dict)
        return exact_bytes, transport_sha256
