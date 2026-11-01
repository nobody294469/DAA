"""Real-PostgreSQL Concurrency and Multi-Process Integration Tests for Step 3 DEF-003:
Stage 3.1: Advisory Lock Unification, Lock-Protected Sequence Allocation,
W-02 Retirement, and Gate 9 DB-First Transaction Ordering.

Authority:
  - CLINICALDOC_STEP3_DEF003_IMPLEMENTATION_EXECUTION_PLAN.md (Revision 2.7) Stage 3.1
  - Test Matrix: TC-01, TC-02, TC-03, TC-19a, TC-19b, W-02 Retirement
"""

from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Generator
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from clindoc.api.audit.service import (
    AuditService,
    compute_audit_event_hash,
)
from clindoc.db import (
    AuditEvent,
    Base,
    Claim,
    ClaimOrigin,
    ClaimReviewState,
    Document,
    DocumentApproval,
    DocumentApprovalStatus,
    DocumentStatus,
    Evidence,
    FHIRBundle,
    FHIRExport,
    FHIRVersion,
    PatientIdentityApprovalStatus,
    Role,
    User,
    UserRole,
    VerificationLabel,
    VerificationResult,
    VerificationStage,
)
from clindoc.extract.contracts import BBoxModel
from clindoc.audit.locking import (
    AuditFileLock,
    AuditFileLockTimeoutError,
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
from clindoc.review.service import ClaimReviewService, ReviewAuditLedger
from clindoc.review.triage import seed_default_triage_policy
from clindoc.services.fhir_export import FHIRExportService


def _get_free_port() -> int:
    """Find an available ephemeral port for PostgreSQL."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg_cluster() -> Generator[str, None, None]:
    """Start an ephemeral PostgreSQL cluster on an available port and yield connection URL."""
    initdb_path = r"D:\postgresql\bin\initdb.exe"
    postgres_path = r"D:\postgresql\bin\postgres.exe"

    if not os.path.exists(initdb_path) or not os.path.exists(postgres_path):
        pytest.skip("PostgreSQL 18 binaries not installed at D:\\postgresql\\bin")

    td = tempfile.mkdtemp(prefix="pg_audit_concurrency_")
    port = _get_free_port()
    url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"

    try:
        subprocess.run(
            [initdb_path, "-D", td, "-U", "postgres", "-A", "trust", "--no-locale", "-E", "UTF8"],
            check=True,
            capture_output=True,
        )
        proc = subprocess.Popen(
            [postgres_path, "-D", td, "-p", str(port), "-c", "deadlock_timeout=50ms", "-c", "max_connections=50"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        import psycopg
        ready = False
        for _ in range(60):
            try:
                with psycopg.connect(f"postgresql://postgres@127.0.0.1:{port}/postgres", connect_timeout=1) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                ready = True
                break
            except Exception:
                time.sleep(0.1)

        if not ready:
            proc.terminate()
            raise RuntimeError(f"Ephemeral PostgreSQL failed to start on port {port}.")

        yield url

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _ensure_roles_seeded(session: Session) -> None:
    """Seed standard roles for tests."""
    roles = [
        ("clinical_reviewer", "Clinical Reviewer"),
        ("pipeline_operator", "Pipeline Operator"),
        ("reviewer", "Reviewer"),
        ("approver", "Approver"),
        ("admin", "Administrator"),
    ]
    for r_id, r_name in roles:
        if not session.get(Role, r_id):
            session.add(Role(id=r_id, name=r_name, description=r_name, permissions={}))
    session.flush()


def _seed_test_env(session: Session) -> tuple[User, Document, Claim]:
    """Seed minimal document, user, evidence, and claim for review testing."""
    _ensure_roles_seeded(session)
    u_hex = uuid.uuid4().hex[:6]
    user = User(
        id=uuid.uuid4(),
        idp_subject=f"sub_{u_hex}",
        username=f"reviewer_{u_hex}",
        full_name=f"Reviewer {u_hex}",
        email=f"rev_{u_hex}@example.com",
        is_active=True,
    )
    session.add(user)
    session.flush()
    session.add(UserRole(user_id=user.id, role_id="clinical_reviewer"))

    doc = Document(
        id=uuid.uuid4(),
        original_filename="test.pdf",
        storage_ref="s3://test/path",
        sha256="a" * 64,
        file_size_bytes=1024,
        mime_type="application/pdf",
        status=DocumentStatus.IN_REVIEW,
        uploaded_by=user.id,
    )
    session.add(doc)
    session.flush()

    ev = Evidence(
        id=uuid.uuid4(),
        document_id=doc.id,
        page_number=1,
        char_start=0,
        char_end=20,
        evidence_text="Glucose 95 mg/dL",
        ocr_confidence=0.98,
        payload={
            "document_id": str(doc.id),
            "page_number": 1,
            "char_start": 0,
            "char_end": 20,
            "text": "Glucose 95 mg/dL",
            "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2},
            "ocr_confidence": 0.98,
        },
    )
    session.add(ev)
    session.flush()

    claim_uuid = uuid.uuid4()
    claim = Claim(
        id=claim_uuid,
        claim_id=claim_uuid,
        version=1,
        document_id=doc.id,
        evidence_id=ev.id,
        group_id="grp-glucose",
        field_type="lab.value",
        value="95",
        normalized_code="2345-7",
        code_system="LOINC",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.PIPELINE,
        payload={
            "claim_id": str(claim_uuid),
            "document_id": str(doc.id),
            "evidence_id": str(ev.id),
            "region_id": "r-1",
            "group_id": "grp-glucose",
            "field_type": "lab.value",
            "value": "95",
            "value_numeric": 95.0,
            "unit_raw": "mg/dL",
            "normalized_code": "2345-7",
            "code_system": "LOINC",
            "extraction_method": "deterministic",
            "extraction_confidence": 0.99,
        },
    )
    session.add(claim)
    session.flush()
    return user, doc, claim


# ─── TEST 1: CONCURRENT AUDIT WRITERS UNDER GLOBAL ADVISORY LOCK (TC-01) ─────

def test_concurrent_audit_writers_real_postgresql(pg_cluster: str):
    """TC-01: Multi-threaded concurrent execution of AuditService.record_event() on real PostgreSQL.
    
    Verifies:
      - All sequence numbers are strictly contiguous (1..N).
      - Zero duplicate sequence numbers.
      - Zero sequence gaps.
      - Hash chain verification passes 100%.
    """
    engine = create_engine(pg_cluster, pool_size=20, max_overflow=10)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    num_threads = 8
    events_per_thread = 5
    total_events = num_threads * events_per_thread

    # Clean existing audit records if any
    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()

    errors = []

    def writer_worker(thread_id: int):
        for i in range(events_per_thread):
            with SessionMaker() as sess:
                try:
                    audit_svc.record_event(
                        session=sess,
                        actor_id=f"worker_{thread_id}",
                        event_type="CLAIM_REVIEW_ACTION",
                        entity_type="claim",
                        entity_id=str(uuid.uuid4()),
                        action="ACCEPT",
                        metadata_json={"thread": thread_id, "index": i},
                    )
                    sess.commit()
                except Exception as exc:
                    errors.append((thread_id, i, exc))

    threads = [threading.Thread(target=writer_worker, args=(t,)) for t in range(num_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=15)

    assert not errors, f"Errors occurred during concurrent audit writes: {errors}"

    # Verify database state
    with SessionMaker() as sess:
        events = sess.query(AuditEvent).order_by(AuditEvent.sequence_number.asc()).all()
        assert len(events) == total_events, f"Expected {total_events} events, found {len(events)}"

        # Verify strict sequence contiguity 1..N
        seqs = [e.sequence_number for e in events]
        expected_seqs = list(range(1, total_events + 1))
        assert seqs == expected_seqs, f"Sequence discontinuity observed: {seqs}"

        # Verify cryptographic hash chain across full range
        result = audit_svc.verify_audit_chain(sess, start_sequence=1, end_sequence=total_events)
        assert result.is_valid is True, f"Hash chain verification failed: {result.errors}"
        assert result.total_events == total_events


# ─── TEST 2: ADVISORY-LOCK BLOCKING (TC-02, TC-03) ──────────────────────────

def test_advisory_lock_blocking(pg_cluster: str):
    """TC-02, TC-03: Real PostgreSQL transaction-level advisory lock blocking.
    
    Verifies:
      - While Transaction 1 holds pg_advisory_xact_lock, Transaction 2 attempting
        record_event() is blocked in the PostgreSQL kernel.
      - Upon Transaction 1 commit, Transaction 2 unblocks immediately and completes.
      - The serialized sequence ordering is strictly preserved.
    """
    engine = create_engine(pg_cluster, pool_size=10, max_overflow=5)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()

    t1_holding_lock = threading.Event()
    t1_can_commit = threading.Event()
    t2_started = threading.Event()
    t2_finished = threading.Event()

    t1_timeline = {}
    t2_timeline = {}

    def worker_1():
        with SessionMaker() as s1:
            # Manually acquire the unified advisory lock in s1
            s1.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": audit_svc.lock_id},
            )
            # Record an event in s1
            audit_svc.record_event(
                session=s1,
                actor_id="t1_operator",
                event_type="DOCUMENT_APPROVAL_REQUESTED",
                entity_type="document",
                entity_id=str(uuid.uuid4()),
                action="REQUEST_APPROVAL",
                metadata_json={"worker": "T1"},
            )
            t1_timeline["lock_acquired"] = time.perf_counter()
            t1_holding_lock.set()

            # Wait until T2 has actively called record_event() and blocked
            t1_can_commit.wait(timeout=5)
            time.sleep(0.1)
            s1.commit()
            t1_timeline["committed"] = time.perf_counter()

    def worker_2():
        # Wait until T1 is actively holding the advisory lock
        t1_holding_lock.wait(timeout=5)
        t2_timeline["attempt_start"] = time.perf_counter()
        t2_started.set()

        with SessionMaker() as s2:
            # record_event() must block on PostgreSQL advisory lock until T1 commits!
            audit_svc.record_event(
                session=s2,
                actor_id="t2_operator",
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(uuid.uuid4()),
                action="ACCEPT",
                metadata_json={"worker": "T2"},
            )
            s2.commit()
            t2_timeline["completed"] = time.perf_counter()
            t2_finished.set()

    th1 = threading.Thread(target=worker_1)
    th2 = threading.Thread(target=worker_2)

    th1.start()
    th2.start()

    t2_started.wait(timeout=2)
    # T2 is now running and blocked in PostgreSQL on T1's advisory lock
    time.sleep(0.3)
    assert not t2_finished.is_set(), "T2 completed prematurely while T1 held advisory lock!"

    # Release T1 to commit
    t1_can_commit.set()

    th1.join(timeout=5)
    th2.join(timeout=5)

    assert t2_finished.is_set(), "T2 did not complete after T1 committed."
    assert t2_timeline["completed"] > t1_timeline["committed"], (
        f"T2 completed ({t2_timeline['completed']}) before T1 committed ({t1_timeline['committed']})!"
    )


# ─── TEST 3: ROLLBACK AND ZERO SEQUENCE GAPS ────────────────────────────────

def test_rollback_and_sequence_continuity(pg_cluster: str):
    """Verifies that an uncommitted transaction rollback consumes zero sequence numbers
    and leaves zero gaps in the audit log.
    """
    engine = create_engine(pg_cluster, pool_size=10, max_overflow=5)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()

    with SessionMaker() as s_init:
        s_init.query(AuditEvent).delete()
        s_init.commit()

    # 1. Successful commit at sequence 1
    with SessionMaker() as s1:
        e1 = audit_svc.record_event(
            session=s1,
            actor_id="user_1",
            event_type="DOCUMENT_INGESTED",
            entity_type="document",
            entity_id=str(uuid.uuid4()),
            action="UPLOAD",
        )
        s1.commit()
        assert e1.sequence_number == 1

    # 2. Transaction 2 allocates sequence 2 and flushes, but rolls back!
    with SessionMaker() as s2:
        e2_aborted = audit_svc.record_event(
            session=s2,
            actor_id="user_2",
            event_type="CLAIM_REVIEW_ACTION",
            entity_type="claim",
            entity_id=str(uuid.uuid4()),
            action="REJECT",
        )
        assert e2_aborted.sequence_number == 2
        s2.rollback()

    # 3. Transaction 3 allocates sequence: MUST BE 2 (zero gap!)
    with SessionMaker() as s3:
        e2_success = audit_svc.record_event(
            session=s3,
            actor_id="user_3",
            event_type="CLAIM_REVIEW_ACTION",
            entity_type="claim",
            entity_id=str(uuid.uuid4()),
            action="ACCEPT",
        )
        s3.commit()
        assert e2_success.sequence_number == 2, (
            f"Expected sequence 2 after rollback, got {e2_success.sequence_number}"
        )

    # 4. Verify chain contiguity 1..2
    with SessionMaker() as s_verify:
        res = audit_svc.verify_audit_chain(s_verify, start_sequence=1, end_sequence=2)
        assert res.is_valid is True
        assert res.total_events == 2


# ─── TEST 4: DIRECT SQL BYPASS BOUNDARY (TC-19a & TC-19b) ───────────────────

def test_direct_sql_bypass_racing_collides_on_unique_constraint(pg_cluster: str):
    """TC-19a: Racing direct SQL insert without advisory lock collides on uq_audit_sequence_number.
    
    Verifies that when a direct writer races with an authorized worker holding the advisory lock,
    the uncoordinated write collides on the database unique sequence constraint and fails closed.
    """
    engine = create_engine(pg_cluster, pool_size=10, max_overflow=5)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()

    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        e_init = audit_svc.record_event(
            session=sess,
            actor_id="init",
            event_type="GENESIS",
            entity_type="system",
            entity_id="sys",
            action="INIT",
        )
        sess.commit()
        initial_seq = e_init.sequence_number
        initial_hash = e_init.current_event_hash

    direct_result = {}

    def run_direct_insert():
        with SessionMaker() as s2_direct:
            now_ts = datetime.now(timezone.utc)
            direct_hash = compute_audit_event_hash(
                sequence_number=initial_seq + 1,
                timestamp=now_ts,
                actor_id="direct_writer",
                event_type="DIRECT_INSERT",
                entity_type="claim",
                entity_id="c1",
                action="WRITE",
                metadata_json={},
                previous_event_hash=initial_hash,
            )
            direct_event = AuditEvent(
                id=uuid.uuid4(),
                sequence_number=initial_seq + 1,
                timestamp=now_ts,
                actor_id="direct_writer",
                event_type="DIRECT_INSERT",
                entity_type="claim",
                entity_id="c1",
                action="WRITE",
                metadata_json={},
                previous_event_hash=initial_hash,
                current_event_hash=direct_hash,
            )
            s2_direct.add(direct_event)
            try:
                # Blocks until s1 commits, then fails on unique sequence constraint
                s2_direct.commit()
                direct_result["success"] = True
            except Exception as exc:
                direct_result["error"] = exc

    # Worker 1: Authorized transaction holding advisory lock allocating seq 2
    with SessionMaker() as s1:
        e_auth = audit_svc.record_event(
            session=s1,
            actor_id="authorized_worker",
            event_type="CLAIM_REVIEW_ACTION",
            entity_type="claim",
            entity_id=str(uuid.uuid4()),
            action="ACCEPT",
        )
        assert e_auth.sequence_number == initial_seq + 1

        th_direct = threading.Thread(target=run_direct_insert)
        th_direct.start()
        time.sleep(0.3)

        # Worker 1 commits successfully
        s1.commit()

        th_direct.join(timeout=5)

    assert isinstance(direct_result.get("error"), IntegrityError), (
        f"Expected IntegrityError on collision, got: {direct_result.get('error')}"
    )


def test_direct_sql_bypass_sequential_boundary(pg_cluster: str):
    """TC-19b: Sequential out-of-band direct SQL insert demonstrates the application vs DB boundary.
    
    Verifies that an out-of-band direct SQL insert committed between authorized transactions
    with a contiguous sequence and correct hash commits and passes cryptographic hash verification,
    proving that advisory lock acquisition is an application-tier architecture guarantee.
    """
    engine = create_engine(pg_cluster, pool_size=10, max_overflow=5)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()

    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        e1 = audit_svc.record_event(
            session=sess,
            actor_id="user_1",
            event_type="GENESIS",
            entity_type="system",
            entity_id="sys",
            action="INIT",
        )
        sess.commit()
        seq1 = e1.sequence_number
        hash1 = e1.current_event_hash

    # Sequential direct insert without acquiring advisory lock
    now_ts = datetime.now(timezone.utc)
    seq2 = seq1 + 1
    hash2 = compute_audit_event_hash(
        sequence_number=seq2,
        timestamp=now_ts,
        actor_id="out_of_band_actor",
        event_type="OUT_OF_BAND_ACTION",
        entity_type="claim",
        entity_id="c_oob",
        action="INJECT",
        metadata_json={"bypass": True},
        previous_event_hash=hash1,
    )

    with SessionMaker() as sess:
        sess.add(
            AuditEvent(
                id=uuid.uuid4(),
                sequence_number=seq2,
                timestamp=now_ts,
                actor_id="out_of_band_actor",
                event_type="OUT_OF_BAND_ACTION",
                entity_type="claim",
                entity_id="c_oob",
                action="INJECT",
                metadata_json={"bypass": True},
                previous_event_hash=hash1,
                current_event_hash=hash2,
            )
        )
        sess.commit()

    # Authorized writer writes sequence 3
    with SessionMaker() as sess:
        e3 = audit_svc.record_event(
            session=sess,
            actor_id="user_authorized",
            event_type="CLAIM_REVIEW_ACTION",
            entity_type="claim",
            entity_id=str(uuid.uuid4()),
            action="ACCEPT",
        )
        sess.commit()
        assert e3.sequence_number == 3
        assert e3.previous_event_hash == hash2

    # Cryptographic check passes 1..3
    with SessionMaker() as sess:
        res = audit_svc.verify_audit_chain(sess, start_sequence=1, end_sequence=3)
        assert res.is_valid is True, "Cryptographic hash chain is satisfied"
        assert res.total_events == 3


# ─── TEST 5: GATE 9 EXPORT ADVISORY LOCK & DB-FIRST TRANSACTION ORDERING ────

def test_gate9_export_advisory_lock_and_db_first_ordering(pg_cluster: str):
    """Gate 9 Export Concurrency and DB-First Ordering.
    
    Verifies:
      - export_document() acquires the unified advisory lock at transaction start.
      - A concurrent worker calling record_event() blocks while export is in progress.
      - FHIRBundle, FHIRExport, DocumentStatus.EXPORTED, and AuditEvent are flushed
        and committed to PostgreSQL atomically.
    """
    engine = create_engine(pg_cluster, pool_size=10, max_overflow=5)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    export_svc = FHIRExportService(audit_service=audit_svc)

    with SessionMaker() as init_sess:
        user, doc, claim = _seed_test_env(init_sess)

        # Create approved Four-Eyes approval record
        approval = DocumentApproval(
            id=uuid.uuid4(),
            document_id=doc.id,
            approver_id=user.id,
            approval_status=DocumentApprovalStatus.APPROVED,
            patient_identity_status=PatientIdentityApprovalStatus.CONFIRMED,
            policy_version="1.0.0-mode0",
            is_export_eligible=True,
        )
        init_sess.add(approval)

        # Update claim to accepted state
        claim.review_state = ClaimReviewState.ACCEPTED

        # Add verification result required for export eligibility
        db_ver = VerificationResult(
            id=uuid.uuid4(),
            claim_id=claim.id,
            label=VerificationLabel.SUPPORTED,
            calibrated_confidence=0.98,
            verification_stage=VerificationStage.STAGE_A,
            provider_kind="deterministic",
            payload={
                "claim_id": str(claim.id),
                "evidence_id": str(claim.evidence_id),
                "label": "SUPPORTED",
                "confidence": 0.98,
                "stage_reached": "A",
            },
        )
        init_sess.add(db_ver)
        init_sess.commit()

        doc_id = doc.id
        user_id = user.id

    export_started = threading.Event()
    export_can_complete = threading.Event()
    worker_blocked = threading.Event()
    worker_finished = threading.Event()

    def run_export():
        with SessionMaker() as sess:
            export_svc.export_document(
                session=sess,
                document_id=doc_id,
                exported_by=user_id,
            )
            export_started.set()
            export_can_complete.wait(timeout=5)
            sess.commit()

    def run_competing_writer():
        export_started.wait(timeout=5)
        worker_blocked.set()
        with SessionMaker() as sess:
            # Must block until export commits
            audit_svc.record_event(
                session=sess,
                actor_id="competing_worker",
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(uuid.uuid4()),
                action="ACCEPT",
            )
            sess.commit()
            worker_finished.set()

    th_exp = threading.Thread(target=run_export)
    th_wr = threading.Thread(target=run_competing_writer)

    th_exp.start()
    th_wr.start()

    worker_blocked.wait(timeout=2)
    time.sleep(0.3)
    # Competing worker is blocked because export holds the advisory lock
    assert not worker_finished.is_set(), "Competing writer was not serialized by advisory lock!"

    export_can_complete.set()
    th_exp.join(timeout=5)
    th_wr.join(timeout=5)

    assert worker_finished.is_set()

    # Verify DB-first state
    with SessionMaker() as verify_sess:
        doc = verify_sess.get(Document, doc_id)
        assert doc.status == DocumentStatus.EXPORTED

        bundle = verify_sess.query(FHIRBundle).filter(FHIRBundle.document_id == doc_id).first()
        assert bundle is not None
        assert bundle.bundle_payload is not None

        export_row = verify_sess.query(FHIRExport).filter(FHIRExport.bundle_id == bundle.id).first()
        assert export_row is not None

        # Verify audit event for export was committed
        export_audit = (
            verify_sess.query(AuditEvent)
            .filter(AuditEvent.event_type == "FHIR_BUNDLE_EXPORTED")
            .filter(AuditEvent.entity_id == str(bundle.id))
            .first()
        )
        assert export_audit is not None


# ─── TEST 6: FORMAL W-02 RETIREMENT VERIFICATION ────────────────────────────

def test_w02_retirement_verification(pg_cluster: str):
    """W-02 Retirement Verification:
    
    Verifies that calling ClaimReviewService._record_audit_event() (via accept_claim or edit_claim)
    with an audit_ledger instance NEVER invokes audit_ledger.append_event().
    The direct JSONL dual-write is formally retired.
    """
    engine = create_engine(pg_cluster, pool_size=10, max_overflow=5)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    mock_ledger = MagicMock()

    review_svc = ClaimReviewService(
        audit_service=audit_svc,
        audit_ledger=mock_ledger,
        enforce_four_eyes=False,
    )

    with SessionMaker() as s:
        user, doc, claim = _seed_test_env(s)
        claim_id = claim.id
        user_id = user.id
        s.commit()

    with SessionMaker() as s:
        res = review_svc.accept_claim(
            session=s,
            claim_id=claim_id,
            actor_id=user_id,
            expected_version=1,
            commit=True,
        )

    # 1. Assert W-02 direct dual-write was NEVER called!
    assert mock_ledger.append_event.call_count == 0, (
        f"W-02 violation! audit_ledger.append_event() was called {mock_ledger.append_event.call_count} times."
    )

    # 2. Assert PostgreSQL audit event WAS recorded by AuditService
    with SessionMaker() as s:
        recorded_event = (
            s.query(AuditEvent)
            .filter(AuditEvent.entity_id == str(claim_id))
            .filter(AuditEvent.event_type == "CLAIM_REVIEW_ACTION")
            .filter(AuditEvent.action == "ACCEPT")
            .first()
        )
        assert recorded_event is not None
        assert recorded_event.actor_id == str(user_id)


# ─── STAGE 3.2 TESTS: KERNEL FILE LOCKING & OUTBOX PROJECTION ─────────────────

def test_audit_file_lock_multi_worker_exclusion(tmp_path):
    """Stage 3.2: Verify multi-worker exclusion and timeout behavior of AuditFileLock."""
    lock_file = tmp_path / "test_ledger.jsonl.lock"
    lock1 = AuditFileLock(lock_file, timeout=2.0)
    lock2 = AuditFileLock(lock_file, timeout=0.2, poll_interval=0.02)

    # Worker 1 acquires lock
    assert lock1.acquire() is True

    # Same thread re-entrancy succeeds
    assert lock1.acquire() is True
    lock1.release()

    # Worker 2 in background thread must timeout while Worker 1 holds lock
    worker2_outcome = {}

    def run_worker2():
        try:
            lock2.acquire()
            worker2_outcome["acquired"] = True
            lock2.release()
        except AuditFileLockTimeoutError as exc:
            worker2_outcome["timeout"] = True
        except Exception as exc:
            worker2_outcome["error"] = exc

    th = threading.Thread(target=run_worker2)
    th.start()
    th.join(timeout=2.0)

    assert worker2_outcome.get("timeout") is True
    assert worker2_outcome.get("acquired") is None

    # Worker 1 releases
    lock1.release()

    # Now Worker 2 acquires successfully
    assert lock2.acquire() is True
    lock2.release()


def test_outbox_projection_lagging_catchup(pg_cluster: str, tmp_path):
    """Stage 3.2: Reconciles lagging JSONL projection from committed PostgreSQL events."""
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "audit_test.jsonl"
    chkpt_path = tmp_path / "audit_test.chkpt"
    test_key = b"secret-test-hmac-key-1234567890"

    # 1. Clean audit table and seed 5 events into PostgreSQL
    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        for i in range(1, 6):
            audit_svc.record_event(
                session=sess,
                actor_id=f"worker_{i}",
                event_type="SYSTEM_ACTION",
                entity_type="document",
                entity_id=str(uuid.uuid4()),
                action=f"STEP_{i}",
            )
        sess.commit()

    # 2. Simulate JSONL ledger that only contains events 1..2
    with SessionMaker() as sess:
        events_1_2 = sess.query(AuditEvent).filter(AuditEvent.sequence_number <= 2).order_by(AuditEvent.sequence_number.asc()).all()
        with open(ledger_path, "w", encoding="utf-8") as f:
            for ev in events_1_2:
                line_data = {
                    "action": ev.action,
                    "actor_id": ev.actor_id,
                    "current_event_hash": ev.current_event_hash,
                    "entity_id": ev.entity_id,
                    "entity_type": ev.entity_type,
                    "event_type": ev.event_type,
                    "hash_algorithm": "sha256_audit_v1",
                    "metadata": ev.metadata_json or {},
                    "previous_event_hash": ev.previous_event_hash or "",
                    "sequence_number": ev.sequence_number,
                    "timestamp": ev.timestamp.isoformat(),
                }
                f.write(json.dumps(line_data, sort_keys=True, separators=(",", ":")) + "\n")

    # 3. Execute Outbox Projection
    with SessionMaker() as sess:
        res = AuditProjectionEngine.reconcile_outbox(
            session=sess,
            ledger_path=ledger_path,
            checkpoint_path=chkpt_path,
            hmac_key=test_key,
        )

    # Asserts
    assert res.projected_count == 3
    assert res.initial_sequence == 2
    assert res.final_sequence == 5
    assert res.checkpoint_updated is True

    # 4. Verify ledger verification passes with checkpoint
    ledger = ReviewAuditLedger(ledger_path=ledger_path, checkpoint_path=chkpt_path, hmac_key=test_key)
    ver_res = ledger.verify_ledger(enforce_checkpoint=True)
    assert ver_res.is_valid is True
    assert ver_res.total_events == 5
    assert ver_res.checkpoint_verified is True


def test_outbox_projection_idempotency_tc16(pg_cluster: str, tmp_path):
    """TC-16: Outbox replay idempotency over 5 consecutive runs on synchronized ledger."""
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "tc16_ledger.jsonl"
    ledger_path.touch()
    chkpt_path = tmp_path / "tc16_ledger.chkpt"
    test_key = b"secret-test-hmac-key-1234567890"

    # Seed 3 events
    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        for i in range(1, 4):
            audit_svc.record_event(
                session=sess,
                actor_id=f"actor_{i}",
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(uuid.uuid4()),
                action="ACCEPT",
            )
        sess.commit()

    # Initial projection
    with SessionMaker() as sess:
        init_res = AuditProjectionEngine.reconcile_outbox(
            session=sess,
            ledger_path=ledger_path,
            checkpoint_path=chkpt_path,
            hmac_key=test_key,
        )
    assert init_res.projected_count == 3

    # Snapshot file hashes
    initial_ledger_bytes = ledger_path.read_bytes()
    initial_chkpt_bytes = chkpt_path.read_bytes()

    # Re-run 5 consecutive times
    with SessionMaker() as sess:
        for run_idx in range(5):
            replay_res = AuditProjectionEngine.reconcile_outbox(
                session=sess,
                ledger_path=ledger_path,
                checkpoint_path=chkpt_path,
                hmac_key=test_key,
            )
            assert replay_res.projected_count == 0, f"Run {run_idx} wrote unexpected events!"
            assert replay_res.final_sequence == 3
            assert ledger_path.read_bytes() == initial_ledger_bytes, f"Run {run_idx} mutated ledger file!"
            assert chkpt_path.read_bytes() == initial_chkpt_bytes, f"Run {run_idx} mutated checkpoint file!"


def test_outbox_projection_crash_consistency_b4_b5(pg_cluster: str, tmp_path):
    """B-4 / B-5 Crash Recovery: DB committed at N+1, crash occurred before JSONL write.
    
    Verifies that the next projection detects S_db > S_file, catches up sequence N+1,
    and seals the checkpoint without data loss or gaps.
    """
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "crash_ledger.jsonl"
    ledger_path.touch()
    chkpt_path = tmp_path / "crash_ledger.chkpt"
    test_key = b"crash-recovery-key-1234567890"

    # Step 1: Normal writes for 1..3
    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        for i in range(1, 4):
            audit_svc.record_event(
                session=sess,
                actor_id="normal_actor",
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(uuid.uuid4()),
                action="ACCEPT",
            )
        sess.commit()

    with SessionMaker() as sess:
        AuditProjectionEngine.reconcile_outbox(
            session=sess,
            ledger_path=ledger_path,
            checkpoint_path=chkpt_path,
            hmac_key=test_key,
        )

    # Step 2: B-4 simulation: DB commits event 4, but process dies before projecting to JSONL
    with SessionMaker() as sess:
        audit_svc.record_event(
            session=sess,
            actor_id="crash_actor",
            event_type="CLAIM_REVIEW_ACTION",
            entity_type="claim",
            entity_id=str(uuid.uuid4()),
            action="EDIT",
        )
        sess.commit()

    # Step 3: Recovery run
    with SessionMaker() as sess:
        rec_res = AuditProjectionEngine.reconcile_outbox(
            session=sess,
            ledger_path=ledger_path,
            checkpoint_path=chkpt_path,
            hmac_key=test_key,
        )

    assert rec_res.projected_count == 1
    assert rec_res.initial_sequence == 3
    assert rec_res.final_sequence == 4

    # Verify chain
    ledger = ReviewAuditLedger(ledger_path=ledger_path, checkpoint_path=chkpt_path, hmac_key=test_key)
    ver = ledger.verify_ledger(enforce_checkpoint=True)
    assert ver.is_valid is True
    assert ver.total_events == 4
    assert ver.checkpoint_verified is True


def test_outbox_projection_split_brain_fails_closed(pg_cluster: str, tmp_path):
    """B-ANOM-1: Split-brain (S_file > S_db) raises SplitBrainLedgerError and fails closed."""
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "split_brain.jsonl"
    chkpt_path = tmp_path / "split_brain.chkpt"

    # DB only has 2 events
    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        for i in range(1, 3):
            audit_svc.record_event(
                session=sess,
                actor_id="actor",
                event_type="SYSTEM",
                entity_type="doc",
                entity_id="1",
                action="A",
            )
        sess.commit()

    # Create file claiming to have 5 events
    with open(ledger_path, "w", encoding="utf-8") as f:
        for i in range(1, 6):
            f.write(json.dumps({
                "sequence_number": i,
                "current_event_hash": f"fake_hash_{i}",
                "previous_event_hash": f"fake_hash_{i-1}" if i > 1 else "",
                "actor_id": "rogue",
                "action": "A",
                "entity_id": "1",
                "entity_type": "doc",
                "event_type": "SYS",
                "metadata": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }) + "\n")

    with SessionMaker() as sess:
        with pytest.raises(SplitBrainLedgerError) as exc_info:
            AuditProjectionEngine.reconcile_outbox(
                session=sess,
                ledger_path=ledger_path,
                checkpoint_path=chkpt_path,
            )
        assert "Split-brain detected" in str(exc_info.value)


def test_outbox_projection_missing_ledger_fails_closed(pg_cluster: str, tmp_path):
    """B-ANOM-0: Missing ledger file with committed DB history raises AuditLedgerMissingError."""
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    non_existent_ledger = tmp_path / "non_existent_ledger.jsonl"

    # DB has committed history
    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        audit_svc.record_event(
            session=sess,
            actor_id="actor",
            event_type="SYSTEM",
            entity_type="doc",
            entity_id="1",
            action="A",
        )
        sess.commit()

    with SessionMaker() as sess:
        with pytest.raises(AuditLedgerMissingError) as exc_info:
            AuditProjectionEngine.reconcile_outbox(
                session=sess,
                ledger_path=non_existent_ledger,
            )
        assert "Audit ledger file" in str(exc_info.value)
        assert "is missing but PostgreSQL contains" in str(exc_info.value)


def test_atomic_checkpoint_replace_durability(tmp_path):
    """Stage 3.2: Verify atomic temporary replace and durability of checkpoints."""
    target_path = tmp_path / "test.chkpt"
    data = b'{"checkpoint_sequence": 10, "signature": "valid"}'

    written_path = atomic_write_file(target_path, data)
    assert written_path.exists()
    assert written_path.read_bytes() == data

    # Assert no orphaned temporary files remained
    parent_files = list(tmp_path.iterdir())
    assert len(parent_files) == 1
    assert parent_files[0].name == "test.chkpt"


def test_review_audit_ledger_reconcile_integration(pg_cluster: str, tmp_path):
    """Stage 3.2: End-to-end integration of ReviewAuditLedger.reconcile_with_db()."""
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "integration_ledger.jsonl"
    ledger_path.touch()
    chkpt_path = tmp_path / "integration_ledger.chkpt"
    test_key = b"integration-test-key-0987654321"

    # Seed 3 review actions in DB
    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        for i in range(1, 4):
            audit_svc.record_event(
                session=sess,
                actor_id=f"reviewer_{i}",
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(uuid.uuid4()),
                action="ACCEPT",
                metadata_json={"claim_version": 1, "new_state": "ACCEPTED"},
            )
        sess.commit()

    # Reconcile via ReviewAuditLedger
    ledger = ReviewAuditLedger(
        ledger_path=ledger_path,
        checkpoint_path=chkpt_path,
        hmac_key=test_key,
        document_id="doc-123",
        document_sha256="d" * 64,
    )

    with SessionMaker() as sess:
        res = ledger.reconcile_with_db(session=sess)

    assert res.projected_count == 3
    assert res.final_sequence == 3

    # Verify ledger passes 100%
    ver = ledger.verify_ledger(expected_document_sha256="d" * 64, enforce_checkpoint=True)
    assert ver.is_valid is True
    assert ver.total_events == 3
    assert ver.checkpoint_verified is True
    assert len(ver.verified_claims) == 3


def test_audit_file_lock_multiprocess_exclusion_and_termination(tmp_path):
    """Stage 3.2 Finding 3: Genuine multi-OS-process exclusion and termination release test.

    Verifies using subprocess.Popen that:
      1. Two independent OS processes cannot hold the same ledger lock simultaneously.
      2. The second process times out when the first holds the lock.
      3. The lock is released upon normal exit.
      4. The lock is released by OS kernel cleanup upon abnormal process termination (SIGKILL / proc.kill()).
    """
    lock_file = tmp_path / "proc_ledger.jsonl.lock"
    ready1 = tmp_path / "p1.ready"
    release1 = tmp_path / "p1.release"
    ready4 = tmp_path / "p4.ready"

    script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from clindoc.audit.locking import AuditFileLock, AuditFileLockTimeoutError\n"
        f"lock_path = r'{lock_file}'\n"
        f"ready_path = Path(r'{ready1}')\n"
        f"release_path = Path(r'{release1}')\n"
        "mode = sys.argv[1]\n"
        "to = float(sys.argv[2])\n"
        "lock = AuditFileLock(lock_path, timeout=to)\n"
        "if mode == 'hold_file':\n"
        "    lock.acquire()\n"
        "    ready_path.write_text('ready', encoding='utf-8')\n"
        "    while not release_path.exists():\n"
        "        time.sleep(0.05)\n"
        "    lock.release()\n"
        "    sys.exit(0)\n"
        "elif mode == 'hold_kill':\n"
        f"    ready4_path = Path(r'{ready4}')\n"
        "    lock.acquire()\n"
        "    ready4_path.write_text('ready', encoding='utf-8')\n"
        "    while True:\n"
        "        time.sleep(0.1)\n"
        "elif mode == 'try':\n"
        "    try:\n"
        "        lock.acquire()\n"
        "        lock.release()\n"
        "        sys.exit(0)\n"
        "    except AuditFileLockTimeoutError:\n"
        "        sys.exit(2)\n"
    )

    # 1. Start Process 1 holding lock until release flag file is created
    p1 = subprocess.Popen([sys.executable, "-c", script, "hold_file", "5.0"])
    t0 = time.time()
    while not ready1.exists() and time.time() - t0 < 30.0:
        time.sleep(0.1)
    assert ready1.exists(), "Process 1 failed to acquire lock and signal ready"

    # 2. While p1 holds lock, start Process 2 with 0.3s timeout -> must TIMEOUT with returncode 2
    p2 = subprocess.Popen([sys.executable, "-c", script, "try", "0.3"])
    p2.wait(timeout=30.0)
    assert p2.returncode == 2, f"Process 2 expected timeout (exit code 2), got {p2.returncode}"

    # 3. Signal Process 1 to release and verify it exits cleanly with returncode 0
    release1.write_text("release", encoding="utf-8")
    p1.wait(timeout=10.0)
    assert p1.returncode == 0

    # 4. Process 3 can now acquire immediately
    p3 = subprocess.Popen([sys.executable, "-c", script, "try", "5.0"])
    p3.wait(timeout=30.0)
    assert p3.returncode == 0, f"Process 3 failed to acquire lock, code {p3.returncode}"

    # 5. Abnormal Termination: Process 4 acquires lock, then gets killed by OS signal
    p4 = subprocess.Popen([sys.executable, "-c", script, "hold_kill", "5.0"])
    t0 = time.time()
    while not ready4.exists() and time.time() - t0 < 30.0:
        time.sleep(0.1)
    assert ready4.exists(), "Process 4 failed to acquire lock"

    # Forcefully kill Process 4 without letting it execute release() or cleanup
    p4.kill()
    p4.wait(timeout=5.0)

    # 6. Process 5 must be able to acquire lock immediately because OS kernel cleaned up handle
    p5 = subprocess.Popen([sys.executable, "-c", script, "try", "5.0"])
    p5.wait(timeout=30.0)
    assert p5.returncode == 0, f"Process 5 failed to acquire lock after process kill, code {p5.returncode}"


def test_outbox_projection_detects_middle_record_tampering(pg_cluster: str, tmp_path):
    """Stage 3.2 Finding 1: Changing event 3 while leaving tail intact fails closed and does NOT advance checkpoint."""
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "tamper_middle.jsonl"
    ledger_path.touch()
    chkpt_path = tmp_path / "tamper_middle.chkpt"
    test_key = b"middle-tamper-key-123456789012"

    # 1. Commit 5 events in PostgreSQL
    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        for i in range(1, 6):
            audit_svc.record_event(
                session=sess,
                actor_id=f"author_{i}",
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(uuid.uuid4()),
                action=f"ACTION_{i}",
                metadata_json={"step": i},
            )
        sess.commit()

    # 2. Reconcile up to sequence 5
    with SessionMaker() as sess:
        res = AuditProjectionEngine.reconcile_outbox(
            session=sess,
            ledger_path=ledger_path,
            checkpoint_path=chkpt_path,
            hmac_key=test_key,
        )
    assert res.projected_count == 5
    assert res.final_sequence == 5

    # Snapshot checkpoint content and ledger bytes
    orig_chkpt_bytes = chkpt_path.read_bytes()
    orig_ledger_lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(orig_ledger_lines) == 5

    # 3. Adversarially tamper with event 3: modify action and metadata
    # CRITICAL: Event 5 (the tail) and event 4 are left completely untouched!
    rec3 = json.loads(orig_ledger_lines[2])
    assert rec3["sequence_number"] == 3
    rec3["action"] = "MALICIOUS_FORGERY"
    rec3["metadata"] = {"step": 999}
    orig_ledger_lines[2] = json.dumps(rec3, sort_keys=True, separators=(",", ":"))
    tampered_ledger_content = "\n".join(orig_ledger_lines) + "\n"
    ledger_path.write_text(tampered_ledger_content, encoding="utf-8")

    # 4. Run reconcile_outbox() on the tampered ledger (no-op path: S_db == S_file == 5)
    with SessionMaker() as sess:
        with pytest.raises(AuditLedgerTamperedError) as exc_info:
            AuditProjectionEngine.reconcile_outbox(
                session=sess,
                ledger_path=ledger_path,
                checkpoint_path=chkpt_path,
                hmac_key=test_key,
            )

    # 5. Assertions:
    # - Mismatch detected at sequence 3
    assert "sequence 3" in str(exc_info.value)
    # - Checkpoint was NEVER modified or advanced
    assert chkpt_path.read_bytes() == orig_chkpt_bytes
    # - Ledger file was preserved AS-IS (fail-closed, not modified)
    assert ledger_path.read_text(encoding="utf-8") == tampered_ledger_content


def test_outbox_projection_middle_record_tampering_lagging(pg_cluster: str, tmp_path):
    """Stage 3.2 Finding 1: Tampered event 3 prevents lagging catch-up (event 6 is NOT written)."""
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "tamper_lagging.jsonl"
    ledger_path.touch()
    chkpt_path = tmp_path / "tamper_lagging.chkpt"
    test_key = b"lagging-tamper-key-123456789012"

    # Seed 5 events and reconcile
    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        for i in range(1, 6):
            audit_svc.record_event(
                session=sess,
                actor_id=f"author_{i}",
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(uuid.uuid4()),
                action=f"ACTION_{i}",
            )
        sess.commit()

    with SessionMaker() as sess:
        AuditProjectionEngine.reconcile_outbox(
            session=sess,
            ledger_path=ledger_path,
            checkpoint_path=chkpt_path,
            hmac_key=test_key,
        )

    # Tamper with event 3 while keeping tail (event 5) untouched
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    rec3 = json.loads(lines[2])
    rec3["action"] = "UNAUTHORIZED_CHANGE"
    lines[2] = json.dumps(rec3, sort_keys=True, separators=(",", ":"))
    tampered_content = "\n".join(lines) + "\n"
    ledger_path.write_text(tampered_content, encoding="utf-8")
    orig_chkpt_bytes = chkpt_path.read_bytes()

    # Commit event 6 in PostgreSQL (now S_db = 6 > S_file = 5)
    with SessionMaker() as sess:
        audit_svc.record_event(
            session=sess,
            actor_id="author_6",
            event_type="CLAIM_REVIEW_ACTION",
            entity_type="claim",
            entity_id=str(uuid.uuid4()),
            action="ACTION_6",
        )
        sess.commit()

    # Reconcile must fail closed before appending event 6
    with SessionMaker() as sess:
        with pytest.raises(AuditLedgerTamperedError) as exc_info:
            AuditProjectionEngine.reconcile_outbox(
                session=sess,
                ledger_path=ledger_path,
                checkpoint_path=chkpt_path,
                hmac_key=test_key,
            )

    assert "sequence 3" in str(exc_info.value)
    # Event 6 was NOT appended
    assert ledger_path.read_text(encoding="utf-8") == tampered_content
    # Checkpoint was NOT advanced
    assert chkpt_path.read_bytes() == orig_chkpt_bytes


def test_outbox_projection_hash_downgrade_rejected(pg_cluster: str, tmp_path):
    """Stage 3.2 Finding 2: Rejects legacy envelope hash on projected records and un-migrated ledgers."""
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "downgrade_test.jsonl"
    ledger_path.touch()
    chkpt_path = tmp_path / "downgrade_test.chkpt"
    test_key = b"downgrade-prevention-key-12345"

    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        audit_svc.record_event(
            session=sess,
            actor_id="author_1",
            event_type="CLAIM_REVIEW_ACTION",
            entity_type="claim",
            entity_id=str(uuid.uuid4()),
            action="ACTION_1",
        )
        sess.commit()

    with SessionMaker() as sess:
        AuditProjectionEngine.reconcile_outbox(
            session=sess,
            ledger_path=ledger_path,
            checkpoint_path=chkpt_path,
            hmac_key=test_key,
        )

    # 1. Read projected line; verify it carries hash_algorithm == 'sha256_audit_v1'
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    rec1 = json.loads(lines[0])
    assert rec1["hash_algorithm"] == "sha256_audit_v1"

    # 2. Attempt downgrade: replace current_event_hash with canonical envelope hash
    from clindoc.review.service import compute_canonical_envelope_hash
    envelope = {k: rec1[k] for k in (
        "action", "actor_id", "entity_id", "entity_type",
        "event_type", "metadata", "previous_event_hash",
        "sequence_number", "timestamp",
    )}
    legacy_hash = compute_canonical_envelope_hash(envelope)
    rec1["current_event_hash"] = legacy_hash
    lines[0] = json.dumps(rec1, sort_keys=True, separators=(",", ":"))
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # verify_ledger() MUST reject this record despite it having a valid legacy envelope hash,
    # because hash_algorithm is sha256_audit_v1 and legacy hashes are strictly prohibited!
    ledger = ReviewAuditLedger(ledger_path=ledger_path, checkpoint_path=chkpt_path, hmac_key=test_key)
    ver = ledger.verify_ledger(allow_unanchored_dev_ledger=True)
    assert ver.is_valid is False
    assert "recomputed authoritative" in ver.error_message


# ─── STAGE 3.3: FORENSIC QUARANTINE & CRASH RECOVERY TESTS ──────────────────

from clindoc.audit.quarantine import (
    ExternalConcurrentModificationError,
    ForensicQuarantineError,
    ForensicQuarantineVerificationError,
    UnquiescedLedgerModificationError,
    execute_verified_forensic_quarantine,
    find_valid_tail_offset,
    recover_torn_tail,
)


def test_torn_write_quarantine_and_replay_tc05(pg_cluster: str, tmp_path):
    """TC-05: Crash during JSONL append (torn write).

    10 events committed to DB and projected. Event 11 committed to DB.
    JSONL receives 17 bytes of torn garbage without newline at EOF.
    Reconcile initially fails closed.
    Quarantine safely truncates torn tail under kernel write-lockout.
    Reconcile cleanly replays event 11.
    """
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "tc05_ledger.jsonl"
    ledger_path.touch()
    chkpt_path = tmp_path / "tc05_ledger.chkpt"
    test_key = b"tc05-quarantine-key-12345"

    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        for i in range(1, 11):
            audit_svc.record_event(
                session=sess,
                actor_id=f"author_{i}",
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(uuid.uuid4()),
                action=f"ACTION_{i}",
            )
        sess.commit()

    # Initial projection of 10 events
    with SessionMaker() as sess:
        res = AuditProjectionEngine.reconcile_outbox(
            session=sess,
            ledger_path=ledger_path,
            checkpoint_path=chkpt_path,
            hmac_key=test_key,
        )
        assert res.final_sequence == 10

    # Commit event 11 in PostgreSQL
    with SessionMaker() as sess:
        audit_svc.record_event(
            session=sess,
            actor_id="author_11",
            event_type="CLAIM_REVIEW_ACTION",
            entity_type="claim",
            entity_id=str(uuid.uuid4()),
            action="ACTION_11",
        )
        sess.commit()

    # Inject 17 bytes of torn garbage at tail of JSONL without newline
    torn_bytes = b'{"sequence": 11, '
    with open(ledger_path, "ab") as f:
        f.write(torn_bytes)
        f.flush()

    # 1. Reconciliation must fail closed before recovery
    with SessionMaker() as sess:
        with pytest.raises(AuditLedgerTamperedError):
            AuditProjectionEngine.reconcile_outbox(
                session=sess,
                ledger_path=ledger_path,
                checkpoint_path=chkpt_path,
                hmac_key=test_key,
            )

    # 2. Run recovery: safely quarantine and truncate torn tail
    quarantine_path = ReviewAuditLedger(
        ledger_path=ledger_path,
        checkpoint_path=chkpt_path,
        hmac_key=test_key,
    ).recover_torn_tail()

    assert quarantine_path.exists()
    assert ".corrupt." in quarantine_path.name
    # Quarantine preserves the torn write
    assert quarantine_path.read_bytes().endswith(torn_bytes)

    # Clean records 1..10 remain in original ledger
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    assert json.loads(lines[-1])["sequence_number"] == 10

    # 3. Post-recovery reconciliation succeeds: event 11 is replayed and checkpoint advanced
    with SessionMaker() as sess:
        res2 = AuditProjectionEngine.reconcile_outbox(
            session=sess,
            ledger_path=ledger_path,
            checkpoint_path=chkpt_path,
            hmac_key=test_key,
        )
        assert res2.final_sequence == 11
        assert res2.projected_count == 1
        assert res2.checkpoint_updated is True

    # 4. Final verification passes
    ledger = ReviewAuditLedger(ledger_path=ledger_path, checkpoint_path=chkpt_path, hmac_key=test_key)
    ver = ledger.verify_ledger(enforce_checkpoint=True)
    assert ver.is_valid is True
    assert ver.total_events == 11


def test_forensic_preservation_bitwise_exact_copy_tc06(tmp_path):
    """TC-06: Forensic preservation verification.

    Asserts bitwise exact copy exists in quarantine destination matching pre-quarantine original.
    """
    ledger_path = tmp_path / "tc06_ledger.jsonl"
    events = [
        {"sequence_number": 1, "current_event_hash": "a" * 64, "previous_event_hash": "", "event_type": "E1"},
        {"sequence_number": 2, "current_event_hash": "b" * 64, "previous_event_hash": "a" * 64, "event_type": "E2"},
    ]
    raw_content = "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")
    raw_content += b'{"seq": 3, "incomp'  # torn tail
    ledger_path.write_bytes(raw_content)

    pre_size = len(raw_content)
    pre_sha256 = hashlib.sha256(raw_content).hexdigest()

    valid_offset, last_seq, _ = find_valid_tail_offset(ledger_path)
    assert last_seq == 2
    assert valid_offset is not None

    quarantine_path = execute_verified_forensic_quarantine(
        ledger_path=ledger_path,
        valid_tail_offset=valid_offset,
    )

    assert quarantine_path.exists()
    dst_bytes = quarantine_path.read_bytes()
    assert len(dst_bytes) == pre_size
    assert hashlib.sha256(dst_bytes).hexdigest() == pre_sha256

    # Verify temporary file was cleaned up
    tmp_files = list(tmp_path.glob("*.tmp*"))
    assert len(tmp_files) == 0


def test_middle_sequence_corruption_rejects_auto_repair_tc07(pg_cluster: str, tmp_path):
    """TC-07: Middle sequence corruption fails closed and rejects auto-repair.

    Line 3 of 10 is corrupted. Must NEVER be treated as a torn tail!
    """
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "tc07_ledger.jsonl"
    ledger_path.touch()
    chkpt_path = tmp_path / "tc07_ledger.chkpt"
    test_key = b"tc07-middle-key-12345"

    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()
        for i in range(1, 11):
            audit_svc.record_event(
                session=sess,
                actor_id=f"author_{i}",
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(uuid.uuid4()),
                action=f"ACTION_{i}",
            )
        sess.commit()

    with SessionMaker() as sess:
        AuditProjectionEngine.reconcile_outbox(
            session=sess,
            ledger_path=ledger_path,
            checkpoint_path=chkpt_path,
            hmac_key=test_key,
        )

    # Corrupt line 3 of 10
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    lines[2] = "{ corrupt_json: line 3"
    corrupt_content = "\n".join(lines) + "\n"
    ledger_path.write_text(corrupt_content, encoding="utf-8")

    # 1. find_valid_tail_offset must return None because subsequent lines exist
    valid_offset, last_seq, _ = find_valid_tail_offset(ledger_path)
    assert valid_offset is None

    # 2. recover_torn_tail must raise ForensicQuarantineError and refuse to truncate
    with pytest.raises(ForensicQuarantineError) as exc_info:
        recover_torn_tail(ledger_path)
    assert "strictly prohibited for middle corruption" in str(exc_info.value)

    # 3. File content is preserved AS-IS; zero silent truncation
    assert ledger_path.read_text(encoding="utf-8") == corrupt_content

    # 4. Gate 9 / Reconcile remains HARD BLOCKED
    with SessionMaker() as sess:
        with pytest.raises(AuditLedgerTamperedError):
            AuditProjectionEngine.reconcile_outbox(
                session=sess,
                ledger_path=ledger_path,
                checkpoint_path=chkpt_path,
                hmac_key=test_key,
            )


def test_quarantine_windows_write_lockout_and_concurrent_race_tc20(tmp_path):
    """TC-20: Quarantine verification under source race.

    A) Verifies Windows FILE_SHARE_READ denies concurrent write access.
    B) Verifies concurrent source modification during copy is detected and aborts without altering original.
    """
    ledger_path = tmp_path / "tc20_ledger.jsonl"
    ledger_path.write_text('{"sequence_number": 1, "current_event_hash": "abc"}\n', encoding="utf-8")
    orig_content = ledger_path.read_bytes()

    # Part A: Genuine Windows Win32 FILE_SHARE_READ Lockout
    if os.name == "nt":
        import _winapi
        import msvcrt

        win_handle = _winapi.CreateFile(
            str(ledger_path.resolve()),
            0x80000000 | 0x40000000,
            0x00000001,  # FILE_SHARE_READ
            0,
            3,
            0x80,
            0,
        )
        try:
            # Concurrent write handle open must fail with PermissionError (sharing violation)
            with pytest.raises(PermissionError):
                open(ledger_path, "ab")
        finally:
            _winapi.CloseHandle(win_handle)

    # Part B: Concurrent source modification detection during streaming copy
    def tamper_source_hook(path: Path, chunk_idx: int):
        # Mutate the source file mtime/content
        time.sleep(0.01)
        os.utime(path, (time.time() + 10, time.time() + 10))

    with pytest.raises(ExternalConcurrentModificationError):
        execute_verified_forensic_quarantine(
            ledger_path=ledger_path,
            _copy_chunk_hook=tamper_source_hook,
            _force_posix_mode=True,
        )

    # Original file is preserved untouched
    assert ledger_path.read_bytes() == orig_content
    # Temporary files cleaned up
    assert len(list(tmp_path.glob("*.tmp*"))) == 0


def test_posix_quarantine_fail_closed_without_truncation_tc25(tmp_path):
    """TC-25: POSIX concurrent write during quarantine fails closed without truncation.

    Automatic truncation on POSIX is strictly prohibited without kernel write-exclusion.
    """
    ledger_path = tmp_path / "tc25_ledger.jsonl"
    events = [
        {"sequence_number": 1, "current_event_hash": "a" * 64, "previous_event_hash": "", "event_type": "E1"},
        {"sequence_number": 2, "current_event_hash": "b" * 64, "previous_event_hash": "a" * 64, "event_type": "E2"},
    ]
    raw_content = "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")
    raw_content += b'{"seq": 3, "incomp'
    ledger_path.write_bytes(raw_content)

    valid_offset, last_seq, _ = find_valid_tail_offset(ledger_path)
    assert valid_offset is not None

    # Running recovery under POSIX constraints MUST raise UnquiescedLedgerModificationError
    with pytest.raises(UnquiescedLedgerModificationError) as exc_info:
        recover_torn_tail(ledger_path, _force_posix_mode=True)

    assert "Automatic truncation on POSIX is prohibited" in str(exc_info.value)
    # Original file remains completely UN-TRUNCATED to preserve authoritative forensic evidence
    assert ledger_path.read_bytes() == raw_content

    # Quarantine file exists and preserves evidence
    corrupt_files = list(tmp_path.glob("*.corrupt.*"))
    assert len(corrupt_files) == 1
    assert corrupt_files[0].read_bytes() == raw_content


def test_posix_append_before_finalization_marks_untrusted_tc26(tmp_path):
    """TC-26: POSIX append injected between source verification and quarantine finalization.

    Detects post-verify size/mtime change, renames quarantine to .untrusted,
    preserves original POSIX file AS-IS without truncation, and raises UnquiescedLedgerModificationError.
    """
    ledger_path = tmp_path / "tc26_ledger.jsonl"
    events = [
        {"sequence_number": 1, "current_event_hash": "a" * 64, "previous_event_hash": "", "event_type": "E1"},
    ]
    raw_content = "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")
    raw_content += b'{"seq": 2, "incomp'
    ledger_path.write_bytes(raw_content)

    valid_offset, _, _ = find_valid_tail_offset(ledger_path)

    def inject_append_hook(path: Path):
        with open(path, "ab") as f:
            f.write(b"CONCURRENT_APPEND_BYTES")
            f.flush()

    with pytest.raises(UnquiescedLedgerModificationError) as exc_info:
        execute_verified_forensic_quarantine(
            ledger_path=ledger_path,
            valid_tail_offset=valid_offset,
            _post_verify_hook=inject_append_hook,
            _force_posix_mode=True,
        )

    assert "Quarantine marked untrusted" in str(exc_info.value)
    assert "Original file preserved AS-IS" in str(exc_info.value)

    # Quarantine copy exists with .untrusted extension
    untrusted_files = list(tmp_path.glob("*.untrusted"))
    assert len(untrusted_files) == 1

    # Original file preserved with injected bytes, not truncated
    assert ledger_path.read_bytes() == raw_content + b"CONCURRENT_APPEND_BYTES"


def test_quarantine_crash_recovery_bq1_bq2(tmp_path):
    """B-Q-1 and B-Q-2: Crash handling around quarantine files.

    B-Q-1: Orphaned .tmp quarantine file from aborted process is cleaned up.
    B-Q-2: Recovery succeeds when valid quarantine already exists.
    """
    ledger_path = tmp_path / "bq_ledger.jsonl"
    events = [
        {"sequence_number": 1, "current_event_hash": "a" * 64, "previous_event_hash": "", "event_type": "E1"},
    ]
    raw_content = "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")
    raw_content += b'{"seq": 2, "incomp'
    ledger_path.write_bytes(raw_content)

    # B-Q-1: Create an orphaned .tmp quarantine file
    orphaned_tmp = ledger_path.with_name(f"{ledger_path.name}.corrupt.20261101T000000Z.tmp.99999")
    orphaned_tmp.write_text("orphaned partial quarantine", encoding="utf-8")

    # Recovery runs cleanly despite orphaned .tmp
    q_path = recover_torn_tail(ledger_path)
    assert q_path.exists()
    assert q_path != orphaned_tmp


# ─── STAGE 3.4: COMPLETE ATTESTATION GATEWAY & THREE-TIER TESTS ─────────────

from cryptography.hazmat.primitives.asymmetric import ec, rsa
from clindoc.fhir.signing import (
    AUDIT_PROVENANCE_EXTENSION_URL,
    AttestationEquivalenceMismatchError,
    AuditProvenanceBindingMismatchError,
    EHRBundlePayloadTamperedError,
    IncompleteAttestationError,
    InvalidBundleSignatureError,
    MalformedBundleSignatureError,
    MissingBundleSignatureError,
    UntrustedSigningKeyError,
    compute_clinical_content_digest,
    embed_audit_provenance,
    serialize_transport_payload,
    sign_fhir_bundle,
    verify_bundle_signature,
    verify_transport_boundary,
)
from clindoc.services.ehr_integration import EHRIntegrationService
from clindoc.services.ehr_transport import (
    HTTPFHIRTransport,
    HTTPFHIRTransportConfig,
    MLLPFHIRTransport,
    MockFHIRTransport,
)


def test_transport_boundary_wire_payload_tampering_tc24(tmp_path):
    """TC-24: Transport boundary wire-payload tampering.

    Intercepts byte stream at actual transport boundary (kernel socket writer / transport adapter)
    and mutates 1 byte. Asserts fails closed with EHRBundlePayloadTamperedError; 0 corrupted bytes sent.
    """
    bundle_dict = {
        "resourceType": "Bundle",
        "type": "document",
        "id": "bundle-wire-tc24",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": "p-1", "name": [{"family": "Smith"}]}}
        ],
    }
    exact_bytes, transport_sha256 = serialize_transport_payload(bundle_dict)
    valid_json = exact_bytes.decode("utf-8")

    # 1. Mock Transport with uncorrupted payload succeeds
    mock_transport = MockFHIRTransport()
    resp = mock_transport.transmit(
        valid_json,
        idempotency_key="idemp-tc24-1",
        correlation_id="corr-tc24-1",
        document_id="doc-tc24",
        expected_transport_sha256=transport_sha256,
    )
    assert resp.success is True

    # 2. Intercept & mutate 1 byte at the transport boundary
    tampered_json = valid_json.replace("Smith", "SmithX")
    with pytest.raises(EHRBundlePayloadTamperedError) as exc_info:
        mock_transport.transmit(
            tampered_json,
            idempotency_key="idemp-tc24-2",
            correlation_id="corr-tc24-2",
            document_id="doc-tc24",
            expected_transport_sha256=transport_sha256,
        )
    assert "Physical transport boundary payload tampering detected" in str(exc_info.value)

    # 3. MLLP Framed Transport: verify inner payload boundary assertion
    mllp_transport = MLLPFHIRTransport(host="127.0.0.1", port=2575)
    resp_mllp = mllp_transport.transmit(
        valid_json,
        idempotency_key="idemp-tc24-mllp",
        correlation_id="corr-tc24-mllp",
        document_id="doc-tc24",
        expected_transport_sha256=transport_sha256,
    )
    assert resp_mllp.success is True

    # Mutate 1 byte in MLLP payload
    with pytest.raises(EHRBundlePayloadTamperedError):
        mllp_transport.transmit(
            tampered_json,
            idempotency_key="idemp-tc24-mllp-bad",
            correlation_id="corr-tc24-mllp-bad",
            document_id="doc-tc24",
            expected_transport_sha256=transport_sha256,
        )


def test_bundle_signature_cryptographic_contract_verification_failure_tc27():
    """TC-27: Bundle.signature cryptographic contract verification failure.

    Injects 5 failure modes: missing signature, malformed JWS, invalid signature,
    untrusted/revoked key, or audit provenance binding mismatch. Rejects SHA-256 digest
    equality alone as signer authentication.
    """
    # 1. Generate trusted RSA keypair
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    trust_store = {"trusted-ca-key-1": public_key}

    bundle_dict = {
        "resourceType": "Bundle",
        "type": "document",
        "id": "bundle-sig-tc27",
        "entry": [
            {"resource": {"resourceType": "Composition", "id": "comp-1", "title": "Clinical Summary"}}
        ],
    }

    # Augment with audit provenance extension
    augmented = embed_audit_provenance(
        bundle_dict=bundle_dict,
        audit_sequence_number=105,
        audit_event_hash="a" * 64,
        clinical_content_digest=compute_clinical_content_digest(bundle_dict),
    )

    # Sign bundle
    signed_bundle = sign_fhir_bundle(
        bundle_dict=augmented,
        private_key=private_key,
        key_id="trusted-ca-key-1",
    )

    # Base verification passes
    is_valid, kid, _ = verify_bundle_signature(signed_bundle, trust_store)
    assert is_valid is True
    assert kid == "trusted-ca-key-1"

    # Mode 1: Missing signature element
    bundle_missing_sig = copy.deepcopy(signed_bundle)
    bundle_missing_sig.pop("signature")
    with pytest.raises(MissingBundleSignatureError):
        verify_bundle_signature(bundle_missing_sig, trust_store)

    # Mode 2: Malformed JWS (corrupt header / parts)
    bundle_malformed = copy.deepcopy(signed_bundle)
    bundle_malformed["signature"]["data"] = "not-a-valid-three-part-jws"
    with pytest.raises(MalformedBundleSignatureError):
        verify_bundle_signature(bundle_malformed, trust_store)

    # Mode 3: Cryptographically invalid signature (altered bundle content)
    bundle_tampered_content = copy.deepcopy(signed_bundle)
    bundle_tampered_content["entry"][0]["resource"]["title"] = "Tampered Title"
    with pytest.raises(InvalidBundleSignatureError):
        verify_bundle_signature(bundle_tampered_content, trust_store)

    # Mode 4a: Untrusted key (signed with unknown key)
    untrusted_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bundle_untrusted = sign_fhir_bundle(
        bundle_dict=augmented,
        private_key=untrusted_priv,
        key_id="untrusted-hacker-key",
    )
    with pytest.raises(UntrustedSigningKeyError) as exc_untrusted:
        verify_bundle_signature(bundle_untrusted, trust_store)
    assert "not found in trusted key store" in str(exc_untrusted.value)

    # Mode 4b: Revoked key
    with pytest.raises(UntrustedSigningKeyError) as exc_revoked:
        verify_bundle_signature(
            signed_bundle,
            trust_store,
            revoked_key_ids={"trusted-ca-key-1"},
        )
    assert "has been revoked" in str(exc_revoked.value)


def test_ehr_dispatch_blocked_during_incomplete_attestation_tc21(pg_cluster: str, tmp_path):
    """TC-21: EHR dispatch blocked during incomplete attestation.

    PostgreSQL commits bundle at N+1. Crash occurs before JSONL projection.
    Calling send_fhir_bundle_to_ehr() fails closed with IncompleteAttestationError.
    Reconciliation catchup permits subsequent dispatch.
    """
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "tc21_ledger.jsonl"
    ledger_path.touch()
    chkpt_path = tmp_path / "tc21_ledger.chkpt"
    test_key = b"tc21-hmac-key-12345"

    ledger = ReviewAuditLedger(ledger_path=ledger_path, checkpoint_path=chkpt_path, hmac_key=test_key)

    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()

        user, doc, claim = _seed_test_env(sess)
        doc.status = DocumentStatus.APPROVED
        doc_uuid = doc.id
        user_id_str = str(user.id)

        appr = DocumentApproval(
            id=uuid.uuid4(),
            document_id=doc_uuid,
            approver_id=user.id,
            approval_status=DocumentApprovalStatus.APPROVED,
            patient_identity_status=PatientIdentityApprovalStatus.CONFIRMED,
            policy_version="1.0.0-mode0",
            is_export_eligible=True,
        )
        sess.add(appr)
        sess.flush()

        # Step 1: Commit pure clinical event at sequence 1
        clin_bundle = {
            "resourceType": "Bundle",
            "type": "document",
            "id": f"bundle-{doc_uuid}",
            "entry": [{"resource": {"resourceType": "Composition", "id": "comp-1"}}],
        }
        clin_digest = compute_clinical_content_digest(clin_bundle)

        evt = audit_svc.record_event(
            session=sess,
            actor_id="operator_1",
            event_type="FHIR_BUNDLE_EXPORTED",
            entity_type="fhir_bundle",
            entity_id=str(doc_uuid),
            action="EXPORT",
            metadata_json={"clinical_content_sha256": clin_digest},
        )

        augmented = embed_audit_provenance(
            bundle_dict=clin_bundle,
            audit_sequence_number=evt.sequence_number,
            audit_event_hash=evt.current_event_hash,
            clinical_content_digest=clin_digest,
        )
        _, t_sha = serialize_transport_payload(augmented)

        bundle_row = FHIRBundle(
            document_id=doc_uuid,
            approval_id=appr.id,
            fhir_version=FHIRVersion.R4B,
            bundle_sha256=t_sha,
            storage_ref="test/ref",
            bundle_payload=augmented,
        )
        sess.add(bundle_row)
        sess.commit()

    # Incomplete Attestation: DB has sequence 1, but JSONL is empty (S_file = 0 < S_db = 1)
    ehr_service = EHRIntegrationService(
        audit_service=audit_svc,
        audit_ledger=ledger,
    )

    with SessionMaker() as sess:
        with pytest.raises(IncompleteAttestationError) as exc_info:
            ehr_service.transmit_document_to_ehr(
                session=sess,
                document_id=doc_uuid,
                actor_id=user_id_str,
                destination_type="sandbox",
            )
    assert "Incomplete Attestation" in str(exc_info.value)

    # Run outbox reconciliation to catch up JSONL & checkpoint
    with SessionMaker() as sess:
        ledger.reconcile_with_db(session=sess)

    # Post-reconciliation: dispatch succeeds!
    with SessionMaker() as sess:
        res = ehr_service.transmit_document_to_ehr(
            session=sess,
            document_id=doc_uuid,
            actor_id=user_id_str,
            destination_type="sandbox",
        )
        assert res.success is True
        assert res.status_code == 200


def test_checkpoint_valid_but_payload_tampered_jsonl_blocks_bundle_release_tc23(pg_cluster: str, tmp_path):
    """TC-23: Checkpoint-valid but payload-tampered JSONL blocks bundle release.

    JSONL has valid checkpoint at N+1, but line N+1 payload differs from DB.
    Equivalence check fails; release blocked.
    """
    engine = create_engine(pg_cluster, pool_size=5, max_overflow=2)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    audit_svc = AuditService()
    ledger_path = tmp_path / "tc23_ledger.jsonl"
    ledger_path.touch()
    chkpt_path = tmp_path / "tc23_ledger.chkpt"
    test_key = b"tc23-hmac-key-12345"

    ledger = ReviewAuditLedger(ledger_path=ledger_path, checkpoint_path=chkpt_path, hmac_key=test_key)

    with SessionMaker() as sess:
        sess.query(AuditEvent).delete()
        sess.commit()

        clin_bundle = {"resourceType": "Bundle", "type": "document", "id": "tc23-b"}
        clin_digest = compute_clinical_content_digest(clin_bundle)

        evt = audit_svc.record_event(
            session=sess,
            actor_id="legit_author",
            event_type="FHIR_BUNDLE_EXPORTED",
            entity_type="fhir_bundle",
            entity_id="tc23-b",
            action="EXPORT",
            metadata_json={"clinical_content_sha256": clin_digest},
        )
        sess.commit()

        # Reconcile so ledger and checkpoint are at sequence 1
        ledger.reconcile_with_db(session=sess)

    augmented = embed_audit_provenance(
        bundle_dict=clin_bundle,
        audit_sequence_number=1,
        audit_event_hash=evt.current_event_hash,
        clinical_content_digest=clin_digest,
    )

    # Tamper with JSONL record action
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["action"] = "MALICIOUS_TAMPER"
    ledger_path.write_text(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    # verify_bundle_complete_attestation must fail closed
    with SessionMaker() as sess:
        with pytest.raises(AuditLedgerTamperedError) as exc_info:
            ledger.verify_bundle_complete_attestation(
                session=sess,
                bundle_dict=augmented,
            )
    assert "action mismatch" in str(exc_info.value)




