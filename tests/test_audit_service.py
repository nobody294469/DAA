"""Comprehensive tests for Phase 8.4 Step 2: Cryptographic Hash-Chained Audit Service.

Authority:
  - PRODUCTIZATION_SPEC.md §4.2, §5 (T-20), §9.4, §16
  - PRODUCTIZATION_DECISIONS.md PD-012

Tests:
  - First audit event (genesis)
  - Subsequent chained events
  - Deterministic hash calculation and canonical sorting
  - Chain verification success
  - Tampered current hash detection
  - Broken previous-event hash link detection
  - Sequence/order discontinuity detection
  - PHI exclusion security guards (forbidden keys)
  - Fail-closed transactional rollback behavior
  - Multi-threaded concurrent append chain integrity
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from clindoc.api.audit import (
    AuditEventValidationError,
    AuditPHISecurityError,
    AuditService,
    compute_audit_event_hash,
)
from clindoc.db import (
    AuditEvent,
    Base,
    Document,
    DocumentStatus,
    User,
)


@pytest.fixture
def db_engine():
    """In-memory SQLite database with StaticPool so all threads share the same database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    """Yields a database session."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def audit_service():
    """Returns an AuditService instance."""
    return AuditService()


# ─── 1. FIRST AUDIT EVENT (GENESIS) ──────────────────────────────────────────

def test_first_audit_event_genesis(db_session: Session, audit_service: AuditService):
    """Assert genesis event gets sequence 1, null previous hash, and valid current hash."""
    ts = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
    ev = audit_service.record_event(
        session=db_session,
        actor_id="user_admin_01",
        event_type="SYSTEM_INITIALIZATION",
        entity_type="system",
        entity_id="global",
        action="INIT",
        metadata_json={"version": "1.0.0", "status": "nominal"},
        source_ip="127.0.0.1",
        correlation_id="corr-init-01",
        timestamp=ts,
    )
    db_session.commit()

    assert ev.sequence_number == 1
    assert ev.previous_event_hash is None
    assert ev.actor_id == "user_admin_01"
    assert ev.action == "INIT"
    assert ev.source_ip == "127.0.0.1"

    expected_hash = compute_audit_event_hash(
        sequence_number=1,
        timestamp=ts,
        actor_id="user_admin_01",
        event_type="SYSTEM_INITIALIZATION",
        entity_type="system",
        entity_id="global",
        action="INIT",
        metadata_json={"status": "nominal", "version": "1.0.0"},
        previous_event_hash=None,
    )
    assert ev.current_event_hash == expected_hash

    # Chain verification on genesis event
    res = audit_service.verify_audit_chain(db_session)
    assert res.is_valid is True
    assert res.total_events == 1
    assert len(res.errors) == 0


# ─── 2. SUBSEQUENT CHAINED EVENTS ────────────────────────────────────────────

def test_subsequent_chained_events(db_session: Session, audit_service: AuditService):
    """Assert subsequent events increment sequence numbers and link to previous hashes."""
    ev1 = audit_service.record_event(
        session=db_session,
        actor_id="user_01",
        event_type="DOCUMENT_UPLOAD",
        entity_type="document",
        entity_id="doc-001",
        action="CREATE",
        metadata_json={"file_size": 1024, "mime_type": "application/pdf"},
    )
    db_session.commit()

    ev2 = audit_service.record_event(
        session=db_session,
        actor_id="worker_ocr",
        event_type="OCR_COMPLETE",
        entity_type="document",
        entity_id="doc-001",
        action="PROCESS",
        metadata_json={"page_count": 1, "tokens": 150},
    )
    db_session.commit()

    ev3 = audit_service.record_event(
        session=db_session,
        actor_id="reviewer_01",
        event_type="CLAIM_REVIEWED",
        entity_type="claim",
        entity_id="claim-001",
        action="UPDATE",
        metadata_json={"review_state": "ACCEPTED"},
    )
    db_session.commit()

    assert ev1.sequence_number == 1
    assert ev1.previous_event_hash is None

    assert ev2.sequence_number == 2
    assert ev2.previous_event_hash == ev1.current_event_hash

    assert ev3.sequence_number == 3
    assert ev3.previous_event_hash == ev2.current_event_hash

    # Verification sweep across all 3 events
    res = audit_service.verify_audit_chain(db_session)
    assert res.is_valid is True
    assert res.total_events == 3
    assert len(res.errors) == 0


# ─── 3. DETERMINISTIC HASH CALCULATION ───────────────────────────────────────

def test_deterministic_hash_calculation():
    """Assert SHA-256 calculation is deterministic and invariant to dictionary key order."""
    ts = datetime(2026, 10, 4, 14, 30, 0, tzinfo=timezone.utc)
    meta_order_a = {"b": 2, "a": 1, "z": [3, 2, 1]}
    meta_order_b = {"a": 1, "z": [3, 2, 1], "b": 2}

    hash_a = compute_audit_event_hash(
        sequence_number=10,
        timestamp=ts,
        actor_id="actor_x",
        event_type="EVENT_X",
        entity_type="entity_y",
        entity_id="id_123",
        action="EXECUTE",
        metadata_json=meta_order_a,
        previous_event_hash="prevhash123",
    )

    hash_b = compute_audit_event_hash(
        sequence_number=10,
        timestamp=ts,
        actor_id="actor_x",
        event_type="EVENT_X",
        entity_type="entity_y",
        entity_id="id_123",
        action="EXECUTE",
        metadata_json=meta_order_b,
        previous_event_hash="prevhash123",
    )

    # Dictionary key ordering must not change canonical JSON hash
    assert hash_a == hash_b

    # Altering sequence number must change hash
    hash_diff_seq = compute_audit_event_hash(
        sequence_number=11,
        timestamp=ts,
        actor_id="actor_x",
        event_type="EVENT_X",
        entity_type="entity_y",
        entity_id="id_123",
        action="EXECUTE",
        metadata_json=meta_order_a,
        previous_event_hash="prevhash123",
    )
    assert hash_a != hash_diff_seq

    # Altering actor must change hash
    hash_diff_actor = compute_audit_event_hash(
        sequence_number=10,
        timestamp=ts,
        actor_id="actor_different",
        event_type="EVENT_X",
        entity_type="entity_y",
        entity_id="id_123",
        action="EXECUTE",
        metadata_json=meta_order_a,
        previous_event_hash="prevhash123",
    )
    assert hash_a != hash_diff_actor


# ─── 4. TAMPERED CURRENT HASH DETECTION ─────────────────────────────────────

def test_chain_verification_tampered_current_hash_detection(
    db_session: Session,
    audit_service: AuditService,
):
    """Assert tampering with audit row contents without updating hash is detected."""
    audit_service.record_event(
        session=db_session,
        actor_id="user_1",
        event_type="EVENT_1",
        entity_type="doc",
        entity_id="1",
        action="CREATE",
    )
    audit_service.record_event(
        session=db_session,
        actor_id="user_2",
        event_type="EVENT_2",
        entity_type="doc",
        entity_id="1",
        action="UPDATE",
    )
    db_session.commit()

    # Tamper with row 2's action directly in the DB
    ev2 = db_session.query(AuditEvent).filter(AuditEvent.sequence_number == 2).one()
    ev2.action = "TAMPERED_ACTION"
    db_session.commit()

    res = audit_service.verify_audit_chain(db_session)
    assert res.is_valid is False
    assert any(err.error_type == "INVALID_HASH" and err.sequence_number == 2 for err in res.errors)


# ─── 5. BROKEN PREVIOUS HASH DETECTION ──────────────────────────────────────

def test_chain_verification_broken_previous_hash_detection(
    db_session: Session,
    audit_service: AuditService,
):
    """Assert broken link to preceding event's hash is detected."""
    audit_service.record_event(
        session=db_session,
        actor_id="user_1",
        event_type="EVENT_1",
        entity_type="doc",
        entity_id="1",
        action="CREATE",
    )
    audit_service.record_event(
        session=db_session,
        actor_id="user_2",
        event_type="EVENT_2",
        entity_type="doc",
        entity_id="1",
        action="UPDATE",
    )
    db_session.commit()

    # Tamper with row 2's previous_event_hash
    ev2 = db_session.query(AuditEvent).filter(AuditEvent.sequence_number == 2).one()
    ev2.previous_event_hash = "deadbeef" * 8
    # Also update current hash so invalid hash doesn't mask broken link
    ev2.current_event_hash = compute_audit_event_hash(
        sequence_number=2,
        timestamp=ev2.timestamp,
        actor_id=ev2.actor_id,
        event_type=ev2.event_type,
        entity_type=ev2.entity_type,
        entity_id=ev2.entity_id,
        action=ev2.action,
        metadata_json=ev2.metadata_json,
        previous_event_hash=ev2.previous_event_hash,
    )
    db_session.commit()

    res = audit_service.verify_audit_chain(db_session)
    assert res.is_valid is False
    assert any(err.error_type == "BROKEN_LINK" and err.sequence_number == 2 for err in res.errors)


# ─── 6. SEQUENCE GAP DETECTION ──────────────────────────────────────────────

def test_chain_verification_sequence_gap_detection(
    db_session: Session,
    audit_service: AuditService,
):
    """Assert deleting a row creates a sequence discontinuity that is detected."""
    for i in range(1, 4):
        audit_service.record_event(
            session=db_session,
            actor_id="user_1",
            event_type=f"EVENT_{i}",
            entity_type="doc",
            entity_id=str(i),
            action="CREATE",
        )
    db_session.commit()

    # Delete intermediate event (sequence 2)
    db_session.query(AuditEvent).filter(AuditEvent.sequence_number == 2).delete()
    db_session.commit()

    res = audit_service.verify_audit_chain(db_session)
    assert res.is_valid is False
    assert any(err.error_type == "SEQUENCE_GAP" for err in res.errors)


# ─── 7. PHI EXCLUSION SECURITY GUARD ────────────────────────────────────────

def test_phi_exclusion_security_guard(db_session: Session, audit_service: AuditService):
    """Assert audit metadata containing clinical or patient PHI keys is rejected."""
    forbidden_samples = [
        {"patient_name": "John Doe"},
        {"mrn": "12345"},
        {"evidence_text": "Glucose is 95 mg/dL"},
        {"claim_value": "95"},
        {"clinical_narrative": "Patient reports fever"},
        {"nested": {"patient_id": "P-999"}},
    ]

    for bad_meta in forbidden_samples:
        with pytest.raises(AuditPHISecurityError):
            audit_service.record_event(
                session=db_session,
                actor_id="operator_01",
                event_type="TEST_PHI_EVENT",
                entity_type="test",
                entity_id="1",
                action="TEST",
                metadata_json=bad_meta,
            )

    # Verify no events were written
    assert db_session.query(AuditEvent).count() == 0


def test_audit_event_validation_error(db_session: Session, audit_service: AuditService):
    """Assert blank or missing required fields raise AuditEventValidationError."""
    with pytest.raises(AuditEventValidationError):
        audit_service.record_event(
            session=db_session,
            actor_id=" ",  # Empty
            event_type="EVENT",
            entity_type="entity",
            entity_id="1",
            action="CREATE",
        )


# ─── 8. FAIL-CLOSED BEHAVIOR ────────────────────────────────────────────────

def test_fail_closed_transaction_behavior(db_session: Session, audit_service: AuditService):
    """Assert if audit recording fails, the surrounding business transaction rolls back cleanly."""
    u = User(
        id=uuid.uuid4(),
        idp_subject="sub-test-01",
        username="uploader_test",
        full_name="Uploader Test",
        email="uploader@example.com",
    )
    db_session.add(u)
    db_session.commit()

    # Simulate business operation: Document creation + Audit logging in 1 transaction
    try:
        with db_session.begin_nested():
            doc = Document(
                id=uuid.uuid4(),
                sha256="a" * 64,
                storage_ref="blob/123",
                original_filename="report.pdf",
                mime_type="application/pdf",
                file_size_bytes=1024,
                page_count=1,
                source_type="born_digital",
                uploaded_by=u.id,
            )
            db_session.add(doc)

            # Trigger an intentional audit failure (e.g. PHI violation)
            audit_service.record_event(
                session=db_session,
                actor_id=str(u.id),
                event_type="DOCUMENT_UPLOAD",
                entity_type="document",
                entity_id=str(doc.id),
                action="CREATE",
                metadata_json={"patient_name": "SHOULD_FAIL"},
            )
    except AuditPHISecurityError:
        db_session.rollback()

    # Assert neither Document nor AuditEvent persisted (fail-closed!)
    assert db_session.query(Document).count() == 0
    assert db_session.query(AuditEvent).count() == 0


# ─── 9. CONCURRENT MULTI-THREADED APPEND INTEGRITY ──────────────────────────

def test_concurrent_multi_threaded_append_integrity(tmp_path):
    """Assert multiple concurrent threads appending audit events create an unbroken, valid hash chain."""
    import time
    db_file = tmp_path / "test_concurrent_audit.db"
    file_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"timeout": 30},
    )
    Base.metadata.create_all(file_engine)
    SessionLocal = sessionmaker(bind=file_engine, autoflush=False)
    audit_svc = AuditService()

    def append_event(worker_id: int):
        for attempt in range(20):
            session = SessionLocal()
            try:
                audit_svc.record_event(
                    session=session,
                    actor_id=f"worker_{worker_id}",
                    event_type="CONCURRENT_TASK",
                    entity_type="task",
                    entity_id=str(worker_id),
                    action="EXECUTE",
                    metadata_json={"worker_index": worker_id},
                )
                session.commit()
                break
            except Exception:
                session.rollback()
                if attempt == 19:
                    raise
                time.sleep(0.01 * (attempt + 1))
            finally:
                session.close()

    # Run 10 concurrent appends
    num_events = 10
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(append_event, i) for i in range(num_events)]
        for f in futures:
            f.result()

    verify_session = SessionLocal()
    try:
        res = audit_svc.verify_audit_chain(verify_session)
        assert res.is_valid is True
        assert res.total_events == num_events
        assert len(res.errors) == 0

        # Verify all sequences are contiguous 1..10
        events = verify_session.query(AuditEvent).order_by(AuditEvent.sequence_number.asc()).all()
        assert [e.sequence_number for e in events] == list(range(1, num_events + 1))
    finally:
        verify_session.close()


def test_postgresql_advisory_lock_query_compilation():
    """Assert PostgreSQL advisory lock query compiles and executes correctly under postgres dialect."""
    from unittest.mock import MagicMock
    audit_svc = AuditService(lock_id=12345678)

    mock_session = MagicMock(spec=Session)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.get_bind.return_value = mock_bind
    mock_session.query.return_value.order_by.return_value.first.return_value = None

    audit_svc.record_event(
        session=mock_session,
        actor_id="test_actor",
        event_type="TEST",
        entity_type="test",
        entity_id="1",
        action="TEST",
    )

    # Verify pg_advisory_xact_lock was invoked
    calls = mock_session.execute.call_args_list
    assert len(calls) >= 1
    sql_text = str(calls[0][0][0])
    assert "pg_advisory_xact_lock" in sql_text
    assert calls[0][0][1] == {"lock_id": 12345678}
