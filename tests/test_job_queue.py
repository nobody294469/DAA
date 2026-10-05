"""Unit tests for Phase 8.3 Task 3A: Database-Backed Job Queue, Leasing & State Machine.

Authority:
  - PRODUCTIZATION_SPEC.md §2, §4.1 (R-3), §4.2, §4.3, §9.2, §13 (F-17), §16
  - PRODUCTIZATION_DECISIONS.md PD-001, PD-007, §7 Item 3

Validates:
  A. Job enqueueing (PENDING status, document and model_run linkage)
  B. Atomic job claiming (status -> RUNNING, worker identity, attempts incremented, lease stamped)
  C. PostgreSQL claim SQL compilation (`FOR UPDATE SKIP LOCKED`)
  D. SQLite compatibility fallback semantics
  E. Lease expiry and crash recovery / reaper (F-17)
  F. State machine transition validity and rejection of illegal transitions
  G. Retry accounting and max_attempts exhaustion
  H. Terminal failure recording (error_code, error_message)
  I. Terminal job idempotency (cannot be claimed or transitioned once completed/failed)
  J. Job stage event telemetry recording
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
import pytest

from sqlalchemy import create_engine, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from clindoc.db import (
    Base,
    Document,
    DocumentStatus,
    JobStatus,
    ModelRun,
    ProcessingJob,
    User,
)
from clindoc.jobs.queue import (
    InvalidStateTransitionError,
    JobNotFoundError,
    JobQueue,
)


@pytest.fixture
def db_session():
    """Provide an isolated in-memory SQLite database for queue testing."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def test_user(db_session: Session) -> User:
    user = User(
        idp_subject="sub_queue_test_user",
        username="queue_operator",
        full_name="Queue Test Operator",
        email="queue_op@hospital.org",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def test_document(db_session: Session, test_user: User) -> Document:
    doc = Document(
        sha256="b" * 64,
        storage_ref="vault://documents/test_doc_queue.pdf",
        original_filename="inbound_lab_report.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024 * 100,
        page_count=2,
        source_type="scanned_pdf",
        uploaded_by=test_user.id,
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()
    return doc


@pytest.fixture
def test_model_run(db_session: Session) -> ModelRun:
    run = ModelRun(
        is_qualified=True,
        runtime_profile="dev",
        stage_b_model_id="BioClinical-ModernBERT-base",
        stage_b_checkpoint_hash="c" * 64,
        stage_b_temperature=1.146522,
        stage_b_escalation_threshold=0.60,
        stage_b_autonomous_threshold=0.85,
        stage_c_model_id="Qwen/Qwen2.5-7B-Instruct-AWQ",
        stage_c_prompt_version="V8_STAGE_C_LABFLAG_PROMPT_V1",
        stage_c_prompt_hash="d" * 64,
        stage_c_serving_engine="vllm",
        calibrator_version="calibrator_v3",
    )
    db_session.add(run)
    db_session.commit()
    return run


@pytest.fixture
def job_queue() -> JobQueue:
    return JobQueue(default_lease_seconds=300)


# ==============================================================================
# A. Enqueue Tests
# ==============================================================================

def test_enqueue_creates_pending_job_with_correct_linkage(
    db_session: Session,
    job_queue: JobQueue,
    test_document: Document,
    test_model_run: ModelRun,
):
    """Assert enqueue creates a job with PENDING status, zero attempts, and correct foreign keys."""
    job = job_queue.enqueue(
        session=db_session,
        document_id=test_document.id,
        model_run_id=test_model_run.id,
        max_attempts=3,
    )
    db_session.commit()

    assert job.id is not None
    assert job.status == JobStatus.PENDING
    assert job.document_id == test_document.id
    assert job.model_run_id == test_model_run.id
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.worker_id is None
    assert job.started_at is None
    assert job.completed_at is None
    assert job.lease_expires_at is None
    assert job.created_at is not None


# ==============================================================================
# B. Claim Tests (State & Metadata Mutation)
# ==============================================================================

def test_claim_next_populates_runtime_metadata_and_advances_state(
    db_session: Session,
    job_queue: JobQueue,
    test_document: Document,
):
    """Assert claiming transitions PENDING -> RUNNING and stamps worker, attempts, and lease."""
    job = job_queue.enqueue(session=db_session, document_id=test_document.id)
    db_session.commit()

    worker_identity = "worker-node-01:pid-1234"
    claimed = job_queue.claim_next(session=db_session, worker_id=worker_identity, lease_seconds=120)
    db_session.commit()

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.worker_id == worker_identity
    assert claimed.attempts == 1
    assert claimed.started_at is not None
    assert claimed.lease_expires_at is not None

    # Check lease duration is approximately 120s
    lease_delta = (claimed.lease_expires_at - claimed.started_at).total_seconds()
    assert pytest.approx(lease_delta, abs=2.0) == 120.0


def test_claim_next_when_empty_returns_none(db_session: Session, job_queue: JobQueue):
    """Assert claiming from an empty queue cleanly returns None."""
    claimed = job_queue.claim_next(session=db_session, worker_id="worker-01")
    assert claimed is None


# ==============================================================================
# C. PostgreSQL Locking Semantics (SQL Compilation Inspection)
# ==============================================================================

def test_claim_query_compilation_for_postgresql():
    """Assert PostgreSQL query compilation explicitly includes `FOR UPDATE SKIP LOCKED`.

    Per PD-001, PD-007, and PRODUCTIZATION_SPEC §4.2, PostgreSQL queue claiming must use
    atomic row-level locking with SKIP LOCKED to prevent worker lock contention.
    """
    stmt = JobQueue.build_claim_query(dialect_name="postgresql")
    compiled_sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE" in compiled_sql
    assert "SKIP LOCKED" in compiled_sql
    assert "WHERE processing_jobs.status = " in compiled_sql
    assert "ORDER BY processing_jobs.created_at" in compiled_sql
    assert "LIMIT" in compiled_sql


# ==============================================================================
# D. SQLite Compatibility Fallback
# ==============================================================================

def test_sqlite_fallback_query_omits_for_update():
    """Assert SQLite fallback query omits FOR UPDATE (which SQLite compiler rejects)."""
    stmt = JobQueue.build_claim_query(dialect_name="sqlite")
    # For SQLite, with_for_update is not attached
    assert stmt._for_update_arg is None


def test_sqlite_fallback_basic_claim_lifecycle(
    db_session: Session,
    job_queue: JobQueue,
    test_document: Document,
):
    """Assert complete state machine works deterministically under SQLite in-memory engine."""
    job = job_queue.enqueue(session=db_session, document_id=test_document.id)
    db_session.commit()

    claimed = job_queue.claim_next(session=db_session, worker_id="sqlite-worker")
    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING

    completed = job_queue.mark_completed(session=db_session, job_id=claimed.id)
    assert completed.status == JobStatus.COMPLETED
    assert completed.completed_at is not None


# ==============================================================================
# E. Lease Expiry & Crash Recovery / Reaper (F-17)
# ==============================================================================

def test_lease_expiry_requeues_job_when_attempts_remain(
    db_session: Session,
    job_queue: JobQueue,
    test_document: Document,
):
    """Assert worker crash simulation (lease expiry) returns job to PENDING if attempts < max_attempts."""
    job = job_queue.enqueue(session=db_session, document_id=test_document.id, max_attempts=3)
    db_session.commit()

    # Claim job (attempt 1)
    claimed = job_queue.claim_next(session=db_session, worker_id="crashed-worker", lease_seconds=60)
    db_session.commit()
    assert claimed.attempts == 1
    assert claimed.status == JobStatus.RUNNING

    # Fast forward time beyond lease expiration
    past_lease = claimed.lease_expires_at + timedelta(seconds=10)
    reclaimed = job_queue.reclaim_expired_leases(session=db_session, reference_time=past_lease)
    db_session.commit()

    assert len(reclaimed) == 1
    reclaimed_job = reclaimed[0]
    assert reclaimed_job.id == job.id
    assert reclaimed_job.status == JobStatus.PENDING
    assert reclaimed_job.worker_id is None
    assert reclaimed_job.lease_expires_at is None
    assert reclaimed_job.attempts == 1  # Attempts preserved, NOT reset
    assert "re-queued" in reclaimed_job.error_message

    # Job is now available to be claimed by another worker (attempt 2)
    new_worker_job = job_queue.claim_next(session=db_session, worker_id="recovery-worker")
    assert new_worker_job is not None
    assert new_worker_job.id == job.id
    assert new_worker_job.attempts == 2
    assert new_worker_job.worker_id == "recovery-worker"


def test_lease_expiry_terminally_fails_job_when_max_attempts_exhausted(
    db_session: Session,
    job_queue: JobQueue,
    test_document: Document,
):
    """Assert expired job transitions to FAILED once max_attempts is reached."""
    # max_attempts = 1
    job = job_queue.enqueue(session=db_session, document_id=test_document.id, max_attempts=1)
    db_session.commit()

    claimed = job_queue.claim_next(session=db_session, worker_id="doomed-worker", lease_seconds=30)
    db_session.commit()
    assert claimed.attempts == 1

    past_lease = claimed.lease_expires_at + timedelta(seconds=5)
    reclaimed = job_queue.reclaim_expired_leases(session=db_session, reference_time=past_lease)
    db_session.commit()

    assert len(reclaimed) == 1
    failed_job = reclaimed[0]
    assert failed_job.status == JobStatus.FAILED
    assert failed_job.error_code == "LEASE_EXPIRED_MAX_ATTEMPTS"
    assert failed_job.completed_at is not None
    assert failed_job.lease_expires_at is None


# ==============================================================================
# F. State Machine Transition Integrity
# ==============================================================================

def test_invalid_transitions_rejected(
    db_session: Session,
    job_queue: JobQueue,
    test_document: Document,
):
    """Assert illegal transitions raise InvalidStateTransitionError."""
    job = job_queue.enqueue(session=db_session, document_id=test_document.id)
    db_session.commit()

    # PENDING -> COMPLETED directly is illegal
    with pytest.raises(InvalidStateTransitionError, match="Illegal job state transition"):
        job_queue.mark_completed(session=db_session, job_id=job.id)

    # Claim job: PENDING -> RUNNING (legal)
    job_queue.claim_next(session=db_session, worker_id="worker-valid")
    db_session.commit()

    # Complete job: RUNNING -> COMPLETED (legal)
    job_queue.mark_completed(session=db_session, job_id=job.id)
    db_session.commit()

    # COMPLETED is terminal: COMPLETED -> RUNNING and FAILED -> RUNNING are illegal
    with pytest.raises(InvalidStateTransitionError, match="Illegal job state transition"):
        JobQueue.validate_transition(JobStatus.COMPLETED, JobStatus.RUNNING, job_id=job.id)

    with pytest.raises(InvalidStateTransitionError, match="Illegal job state transition"):
        JobQueue.validate_transition(JobStatus.FAILED, JobStatus.RUNNING, job_id=job.id)

    # COMPLETED -> FAILED is illegal
    with pytest.raises(InvalidStateTransitionError, match="Illegal job state transition"):
        job_queue.mark_failed(session=db_session, job_id=job.id, error_code="ERR")



# ==============================================================================
# G. Retry Accounting & Max Attempts
# ==============================================================================

def test_release_or_requeue_increments_and_respects_max_attempts(
    db_session: Session,
    job_queue: JobQueue,
    test_document: Document,
):
    """Assert explicit worker release re-queues until max_attempts is reached."""
    job = job_queue.enqueue(session=db_session, document_id=test_document.id, max_attempts=2)
    db_session.commit()

    # Attempt 1
    c1 = job_queue.claim_next(session=db_session, worker_id="w1")
    assert c1.attempts == 1
    job_queue.release_or_requeue(session=db_session, job_id=c1.id, error_message="Transient network timeout")
    db_session.commit()
    assert c1.status == JobStatus.PENDING

    # Attempt 2
    c2 = job_queue.claim_next(session=db_session, worker_id="w2")
    assert c2.attempts == 2
    assert c2.id == job.id

    # Second failure should exhaust max_attempts and become terminal FAILED
    failed = job_queue.release_or_requeue(session=db_session, job_id=c2.id, error_message="Second transient error")
    db_session.commit()

    assert failed.status == JobStatus.FAILED
    assert failed.completed_at is not None


# ==============================================================================
# H. Terminal Failure Recording
# ==============================================================================

def test_mark_failed_terminal_recording(
    db_session: Session,
    job_queue: JobQueue,
    test_document: Document,
):
    """Assert non-retryable failure immediately transitions to FAILED with error codes."""
    job = job_queue.enqueue(session=db_session, document_id=test_document.id, max_attempts=5)
    db_session.commit()

    claimed = job_queue.claim_next(session=db_session, worker_id="worker-fail")
    failed = job_queue.mark_failed(
        session=db_session,
        job_id=claimed.id,
        error_code="CORRUPT_PDF_HEADER",
        error_message="Non-standard PDF trailer missing %%EOF",
        retryable=False,
    )
    db_session.commit()

    assert failed.status == JobStatus.FAILED
    assert failed.error_code == "CORRUPT_PDF_HEADER"
    assert "%%EOF" in failed.error_message
    assert failed.completed_at is not None
    assert failed.lease_expires_at is None


# ==============================================================================
# I. Idempotency of Terminal States
# ==============================================================================

def test_terminal_jobs_cannot_be_reclaimed(
    db_session: Session,
    job_queue: JobQueue,
    test_document: Document,
):
    """Assert once a job is COMPLETED or FAILED, it is excluded from future claims."""
    job = job_queue.enqueue(session=db_session, document_id=test_document.id)
    db_session.commit()

    claimed = job_queue.claim_next(session=db_session, worker_id="w-done")
    job_queue.mark_completed(session=db_session, job_id=claimed.id)
    db_session.commit()

    # Subsequent claim should return None (job is terminal)
    next_claim = job_queue.claim_next(session=db_session, worker_id="w-other")
    assert next_claim is None


# ==============================================================================
# J. Stage Event Telemetry Recording Primitives
# ==============================================================================

def test_record_stage_event(
    db_session: Session,
    job_queue: JobQueue,
    test_document: Document,
):
    """Assert job stage telemetry can be recorded to job_stage_events."""
    job = job_queue.enqueue(session=db_session, document_id=test_document.id)
    claimed = job_queue.claim_next(session=db_session, worker_id="telemetry-w")

    event = job_queue.record_stage_event(
        session=db_session,
        job_id=claimed.id,
        stage_name="OCR",
        status="COMPLETED",
        duration_ms=450,
        metrics={"page_count": 2, "ocr_confidence_mean": 0.98},
    )
    db_session.commit()

    assert event.id is not None
    assert event.job_id == claimed.id
    assert event.stage_name == "OCR"
    assert event.status == "COMPLETED"
    assert event.duration_ms == 450
    assert event.metrics["page_count"] == 2
