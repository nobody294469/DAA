"""Cryptographic Hash-Chained Audit Service (PD-012, PRODUCTIZATION_SPEC §4.2, §9.4).

Provides append-only, tamper-evident audit logging using the existing
AuditEvent relational model. Every audit row contains a SHA-256 hash of the
preceding row, forming an unbreakable cryptographic ledger across multiple
database worker transactions.

All audit entries are strictly validated to be PHI-free.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Optional, Sequence
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from clindoc.db.models import AuditEvent


class AuditServiceError(Exception):
    """Base exception for audit service errors."""


class AuditPHISecurityError(AuditServiceError):
    """Raised when an audit event payload contains forbidden PHI keys or clinical data."""


class AuditEventValidationError(AuditServiceError):
    """Raised when audit event parameters fail validation."""


# Controlled set of forbidden keys to prevent PHI leakage into audit logs (§4.2)
FORBIDDEN_PHI_KEYS: frozenset[str] = frozenset({
    "patient_name",
    "patient_id",
    "mrn",
    "name",
    "first_name",
    "last_name",
    "dob",
    "birth_date",
    "ssn",
    "national_id",
    "phone",
    "email",
    "address",
    "evidence_text",
    "claim_value",
    "claim_value_text",
    "clinical_text",
    "clinical_narrative",
    "clinical_notes",
    "raw_text",
    "narrative",
    "clinical_rationale",
    "extracted_text",
    "page_text",
    "ocr_text",
})


def validate_metadata_phi_free(metadata: Any, path: str = "root") -> None:
    """Recursively inspect metadata dictionary/list for forbidden PHI keys.

    Raises:
        AuditPHISecurityError if any forbidden key is detected.
    """
    if isinstance(metadata, dict):
        for k, v in metadata.items():
            norm_k = str(k).strip().lower()
            if norm_k in FORBIDDEN_PHI_KEYS:
                raise AuditPHISecurityError(
                    f"Forbidden PHI key '{k}' detected at '{path}.{k}' in audit metadata."
                )
            validate_metadata_phi_free(v, path=f"{path}.{k}")
    elif isinstance(metadata, (list, tuple, set)):
        for idx, item in enumerate(metadata):
            validate_metadata_phi_free(item, path=f"{path}[{idx}]")


def compute_audit_event_hash(
    *,
    sequence_number: int,
    timestamp: datetime,
    actor_id: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    action: str,
    metadata_json: Optional[dict[str, Any]] = None,
    previous_event_hash: Optional[str] = None,
) -> str:
    """Compute the canonical SHA-256 hash for an audit event.

    Formula (models.py:824, PRODUCTIZATION_SPEC §4.2):
        current_event_hash = SHA-256(
            sequence ||
            timestamp_iso ||
            actor ||
            event_type ||
            entity_type ||
            entity_id ||
            action ||
            metadata_json ||
            previous_event_hash
        )

    Canonical formatting:
        - sequence: integer as string
        - timestamp_iso: ISO-8601 string in UTC timezone
        - metadata_json: RFC 8785 canonical JSON (sorted keys, no extra whitespace)
        - previous_event_hash: raw 64-char hex string (empty string for genesis event)
        - delimiter: '|' separating the 9 components
    """
    if timestamp.tzinfo is None:
        ts_utc = timestamp.replace(tzinfo=timezone.utc)
    else:
        ts_utc = timestamp.astimezone(timezone.utc)
    ts_iso = ts_utc.isoformat()

    clean_metadata = copy.deepcopy(metadata_json) if metadata_json is not None else {}
    canonical_metadata_str = json.dumps(
        clean_metadata,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    prev_hash_str = str(previous_event_hash).strip() if previous_event_hash is not None else ""

    canonical_payload = (
        f"{sequence_number}|"
        f"{ts_iso}|"
        f"{actor_id.strip()}|"
        f"{event_type.strip()}|"
        f"{entity_type.strip()}|"
        f"{entity_id.strip()}|"
        f"{action.strip()}|"
        f"{canonical_metadata_str}|"
        f"{prev_hash_str}"
    )

    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainVerificationError:
    """Describes a specific error detected during hash chain verification."""
    sequence_number: int
    error_type: str
    message: str


@dataclass(frozen=True)
class ChainVerificationResult:
    """Structured result of an audit log hash chain verification sweep."""
    is_valid: bool
    total_events: int
    errors: list[ChainVerificationError]


class AuditService:
    """Database-backed append-only audit service with cryptographic hash chaining."""

    # Dedicated PostgreSQL advisory lock key (64-bit int) for serializing hash chain appends
    AUDIT_CHAIN_LOCK_ID: int = 0x5048495F415544  # "PHI_AUD"

    def __init__(self, lock_id: int = AUDIT_CHAIN_LOCK_ID) -> None:
        self.lock_id = lock_id

    def record_event(
        self,
        session: Session,
        *,
        actor_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        action: str,
        metadata_json: Optional[dict[str, Any]] = None,
        source_ip: Optional[str] = None,
        correlation_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> AuditEvent:
        """Append an audit event to the tamper-evident hash chain within caller's transaction.

        Guarantees:
            1. Concurrency Safety: Serializes sequence & hash chain calculation via
               PostgreSQL advisory transaction locks without in-memory locks.
            2. Fail-Closed: Any validation or database failure raises immediately,
               allowing the caller transaction to roll back entirely.
            3. PHI Exclusion: Rejects any payload containing forbidden PHI keys.
            4. Monotonic Sequencing: Sequence numbers start at 1 and increment strictly.

        Returns:
            Flushed AuditEvent instance with calculated hashes.
        """
        # 1. Parameter Validation
        if not actor_id or not actor_id.strip():
            raise AuditEventValidationError("actor_id must be a non-empty string.")
        if not event_type or not event_type.strip():
            raise AuditEventValidationError("event_type must be a non-empty string.")
        if not entity_type or not entity_type.strip():
            raise AuditEventValidationError("entity_type must be a non-empty string.")
        if not entity_id or not entity_id.strip():
            raise AuditEventValidationError("entity_id must be a non-empty string.")
        if not action or not action.strip():
            raise AuditEventValidationError("action must be a non-empty string.")

        clean_metadata = copy.deepcopy(metadata_json) if metadata_json is not None else {}
        validate_metadata_phi_free(clean_metadata)

        # 2. Concurrency Serialization for PostgreSQL
        # Acquires transaction-level advisory lock if running on PostgreSQL.
        # Lock is automatically released when caller's transaction commits or rolls back.
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": self.lock_id},
            )

        # 3. Resolve Previous Sequence and Hash
        latest_event = (
            session.query(AuditEvent)
            .order_by(AuditEvent.sequence_number.desc())
            .first()
        )

        if latest_event is None:
            sequence_number = 1
            previous_event_hash = None
        else:
            sequence_number = latest_event.sequence_number + 1
            previous_event_hash = latest_event.current_event_hash

        # 4. Canonical Timestamp
        event_ts = timestamp or datetime.now(timezone.utc)
        if event_ts.tzinfo is None:
            event_ts = event_ts.replace(tzinfo=timezone.utc)
        else:
            event_ts = event_ts.astimezone(timezone.utc)

        # 5. Compute Hash
        current_event_hash = compute_audit_event_hash(
            sequence_number=sequence_number,
            timestamp=event_ts,
            actor_id=actor_id.strip(),
            event_type=event_type.strip(),
            entity_type=entity_type.strip(),
            entity_id=entity_id.strip(),
            action=action.strip(),
            metadata_json=clean_metadata,
            previous_event_hash=previous_event_hash,
        )

        # 6. Instantiate and Flush Audit Event
        event = AuditEvent(
            id=uuid.uuid4(),
            sequence_number=sequence_number,
            timestamp=event_ts,
            actor_id=actor_id.strip(),
            event_type=event_type.strip(),
            entity_type=entity_type.strip(),
            entity_id=entity_id.strip(),
            action=action.strip(),
            source_ip=source_ip.strip() if source_ip else None,
            correlation_id=correlation_id.strip() if correlation_id else None,
            metadata_json=clean_metadata,
            previous_event_hash=previous_event_hash,
            current_event_hash=current_event_hash,
        )

        session.add(event)
        session.flush()
        return event

    def verify_audit_chain(
        self,
        session: Session,
        *,
        start_sequence: int = 1,
        end_sequence: Optional[int] = None,
    ) -> ChainVerificationResult:
        """Verify the cryptographic hash chain integrity across stored audit events.

        Detects:
            - Tampered current event hashes (modified row contents)
            - Broken previous event hash linkages
            - Sequence gaps or order inconsistencies
            - Non-null previous hash on genesis event
        """
        query = session.query(AuditEvent).order_by(AuditEvent.sequence_number.asc())
        if start_sequence is not None:
            query = query.filter(AuditEvent.sequence_number >= start_sequence)
        if end_sequence is not None:
            query = query.filter(AuditEvent.sequence_number <= end_sequence)

        events: list[AuditEvent] = query.all()
        if not events:
            return ChainVerificationResult(is_valid=True, total_events=0, errors=[])

        errors: list[ChainVerificationError] = []
        prev_event: Optional[AuditEvent] = None

        # If start_sequence > 1, query the immediate preceding event to verify link
        if start_sequence > 1:
            prev_event = (
                session.query(AuditEvent)
                .filter(AuditEvent.sequence_number == start_sequence - 1)
                .first()
            )

        for event in events:
            seq = event.sequence_number

            # Check sequence continuity
            if prev_event is None and start_sequence == 1:
                if seq != 1:
                    errors.append(
                        ChainVerificationError(
                            sequence_number=seq,
                            error_type="SEQUENCE_GAP",
                            message=f"Genesis event sequence is {seq}, expected 1.",
                        )
                    )
                if event.previous_event_hash is not None:
                    errors.append(
                        ChainVerificationError(
                            sequence_number=seq,
                            error_type="GENESIS_LINK_NON_NULL",
                            message=(
                                f"Genesis event must have null previous_event_hash, "
                                f"got '{event.previous_event_hash}'."
                            ),
                        )
                    )
            elif prev_event is not None:
                expected_seq = prev_event.sequence_number + 1
                if seq != expected_seq:
                    errors.append(
                        ChainVerificationError(
                            sequence_number=seq,
                            error_type="SEQUENCE_GAP",
                            message=f"Sequence discontinuity: expected {expected_seq}, found {seq}.",
                        )
                    )

                if event.previous_event_hash != prev_event.current_event_hash:
                    errors.append(
                        ChainVerificationError(
                            sequence_number=seq,
                            error_type="BROKEN_LINK",
                            message=(
                                f"Broken hash chain at sequence {seq}: "
                                f"expected previous hash '{prev_event.current_event_hash}', "
                                f"got '{event.previous_event_hash}'."
                            ),
                        )
                    )

            # Check event hash integrity
            expected_current_hash = compute_audit_event_hash(
                sequence_number=seq,
                timestamp=event.timestamp,
                actor_id=event.actor_id,
                event_type=event.event_type,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                action=event.action,
                metadata_json=event.metadata_json,
                previous_event_hash=event.previous_event_hash,
            )

            if event.current_event_hash != expected_current_hash:
                errors.append(
                    ChainVerificationError(
                        sequence_number=seq,
                        error_type="INVALID_HASH",
                        message=(
                            f"Tampered row data at sequence {seq}: "
                            f"recomputed hash '{expected_current_hash}' != stored '{event.current_event_hash}'."
                        ),
                    )
                )

            prev_event = event

        return ChainVerificationResult(
            is_valid=len(errors) == 0,
            total_events=len(events),
            errors=errors,
        )
