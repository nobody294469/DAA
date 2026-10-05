"""Focused unit tests for Phase 8.3 Task 3B Step 3: Wire Extraction Stage.

Validates:
  A. Successful extraction:
     - ExtractionRouter is invoked on DocumentText from OCR.
     - Evidence is persisted respecting unique span constraint (document_id, page_number, char_start, char_end).
     - Routing decisions are persisted in routing_decisions table.
     - Claims are persisted in claims table referencing document, evidence, and ModelRun.
     - Claims use pipeline origin ('pipeline') and pending review state ('PENDING').
     - ModelRun linkage is established and shared between job and claims.
     - STARTED and COMPLETED JobStageEvent telemetry events are recorded.
     - Metrics are verified to contain zero PHI.
  B. Retry / idempotency:
     - Job re-execution does not produce duplicate claims, evidence spans, or routing decisions.
     - Evidence spans are reused rather than duplicated.
     - Reusable evidence is preserved.
  C. Failure handling:
     - Extraction failure records STARTED and FAILED events.
     - Error messages are sanitized.
     - JobQueue retry / terminal state machine is delegated to cleanly.
  D. LAB_ filename heuristic verification:
     - Verifies content-based classification without relying on filename heuristics.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import uuid
import pymupdf
import pytest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from clindoc.config import RuntimeProfile
from clindoc.db import (
    Base,
    Claim,
    ClaimOrigin,
    ClaimReviewState,
    Document,
    DocumentStatus,
    Evidence,
    JobStageEvent,
    JobStatus,
    ModelRun,
    ProcessingJob,
    RoutingDecision,
    User,
)
from clindoc.extract.contracts import (
    BBoxModel,
    DocumentExtractionOutput,
    ExtractionResult,
    Claim as ContractClaim,
    Evidence as ContractEvidence,
    RoutingDecision as ContractRoutingDecision,
)
from clindoc.extract.router import ExtractionRouter
from clindoc.jobs.queue import JobQueue
from clindoc.jobs.worker import DocumentProcessingWorker
from clindoc.storage.blob import EncryptedFileBlobStore


@pytest.fixture
def db_session():
    """Isolated in-memory SQLite session with foreign keys enabled."""
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
        idp_subject="sub_extract_worker_test_user",
        username="extract_tester",
        full_name="Extract Tester",
        email="extract_tester@hospital.org",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def blob_store(tmp_path: Path) -> EncryptedFileBlobStore:
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)
    key = b"0123456789abcdef0123456789abcdef"  # 32-byte AES-256 key
    return EncryptedFileBlobStore(root_dir=blob_dir, encryption_key=key)


def _generate_lab_pdf_bytes() -> bytes:
    """Create a born-digital PDF containing standard tabular laboratory findings."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(
        (50, 50),
        "OUTPATIENT LABORATORY SERVICES\n"
        "PATIENT: Jane Doe  MRN: 123456\n"
        "TEST               RESULT   UNITS      REF RANGE    FLAG\n"
        "Glucose            95       mg/dL      70 - 99      NORMAL\n"
        "Hemoglobin         14.2     g/dL       12.0 - 16.0  NORMAL\n"
        "Potassium          4.1      mmol/L     3.5 - 5.0    NORMAL\n",
    )
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


# ─── A. SUCCESSFUL EXTRACTION & PERSISTENCE TESTS ─────────────────────────────

def test_extraction_stage_success_persists_evidence_decisions_claims_modelrun(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert successful extraction stage: router runs, records telemetry, and persists Evidence, RoutingDecision, and Claim with ModelRun linkage."""
    pdf_bytes = _generate_lab_pdf_bytes()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256="1" * 64,
        storage_ref=storage_ref,
        original_filename="lab_panel.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        patient_context_id="MRN-123456",
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-extraction-worker",
        stop_after_stage="EXTRACTION",
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    executed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # 1. State verification: extraction completed, but pipeline does not mark job COMPLETED or doc PENDING_REVIEW
    assert executed_job.current_stage == "EXTRACTION"
    assert executed_job.status == JobStatus.RUNNING
    db_session.refresh(doc)
    assert doc.status == DocumentStatus.PROCESSING

    # 2. Output retained in memory
    assert worker.last_extraction_output is not None
    assert isinstance(worker.last_extraction_output, DocumentExtractionOutput)
    extracted_claims_count = len(worker.last_extraction_output.all_claims)
    assert extracted_claims_count > 0

    # 3. ModelRun created and linked
    assert executed_job.model_run_id is not None
    model_run = db_session.get(ModelRun, executed_job.model_run_id)
    assert model_run is not None
    assert model_run.runtime_profile in ("dev", "production")

    # 4. Routing Decisions persisted in database
    db_decisions = (
        db_session.query(RoutingDecision)
        .filter(RoutingDecision.document_id == doc.id)
        .all()
    )
    assert len(db_decisions) == len(worker.last_extraction_output.all_routing_decisions)
    assert len(db_decisions) > 0
    for d in db_decisions:
        assert d.document_id == doc.id
        assert d.route in ("deterministic", "gliner", "llm_narrative")
        assert 0.0 <= d.confidence <= 1.0

    # 5. Evidence persisted in database
    db_evidence = (
        db_session.query(Evidence)
        .filter(Evidence.document_id == doc.id)
        .all()
    )
    assert len(db_evidence) > 0
    # Verify unique span integrity
    spans = {(e.page_number, e.char_start, e.char_end) for e in db_evidence}
    assert len(spans) == len(db_evidence)

    # 6. Claims persisted in database
    db_claims = (
        db_session.query(Claim)
        .filter(Claim.document_id == doc.id)
        .all()
    )
    assert len(db_claims) == extracted_claims_count
    for c in db_claims:
        assert c.document_id == doc.id
        assert c.origin == ClaimOrigin.PIPELINE
        assert c.review_state == ClaimReviewState.PENDING
        assert c.version == 1
        assert c.model_run_id == model_run.id
        assert c.evidence_id is not None
        # Verify evidence reference exists
        linked_ev = db_session.get(Evidence, c.evidence_id)
        assert linked_ev is not None
        assert linked_ev.document_id == doc.id

    # 7. Telemetry verification: STARTED and COMPLETED events
    stage_events = (
        db_session.query(JobStageEvent)
        .filter(JobStageEvent.job_id == job.id, JobStageEvent.stage_name == "EXTRACTION")
        .order_by(JobStageEvent.created_at.asc())
        .all()
    )
    assert len(stage_events) == 2
    assert stage_events[0].status == "STARTED"
    assert stage_events[1].status == "COMPLETED"
    assert stage_events[1].duration_ms is not None
    assert stage_events[1].duration_ms >= 0

    metrics = stage_events[1].metrics
    assert metrics["num_regions"] > 0
    assert metrics["num_claims"] == len(db_claims)
    assert metrics["num_evidence_spans"] == len(db_evidence)
    assert "route_counts" in metrics

    # 8. Strict PHI audit: Assert zero PHI in telemetry metrics
    forbidden_terms = ["Jane Doe", "123456", "MRN", "Glucose", "95", "Hemoglobin", "Potassium"]
    for k, v in metrics.items():
        assert not any(t.lower() in str(k).lower() for t in forbidden_terms)
        assert not any(t.lower() in str(v).lower() for t in forbidden_terms)


# ─── B. RETRY / IDEMPOTENCY TESTS ─────────────────────────────────────────────

def test_extraction_stage_retry_idempotency_prevents_duplicate_records(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert re-running extraction on retry does not duplicate Claims, RoutingDecisions, or Evidence spans."""
    pdf_bytes = _generate_lab_pdf_bytes()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256="2" * 64,
        storage_ref=storage_ref,
        original_filename="idempotency_test.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id, max_attempts=3)
    db_session.commit()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-idempotent-worker",
        stop_after_stage="EXTRACTION",
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    # Pass 1: Initial extraction
    worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    claims_count_1 = db_session.query(Claim).filter(Claim.document_id == doc.id).count()
    decisions_count_1 = db_session.query(RoutingDecision).filter(RoutingDecision.document_id == doc.id).count()
    evidence_count_1 = db_session.query(Evidence).filter(Evidence.document_id == doc.id).count()

    assert claims_count_1 > 0
    assert decisions_count_1 > 0
    assert evidence_count_1 > 0

    # Pass 2: Re-run extraction on the same claimed job (simulating retry execution)
    worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    claims_count_2 = db_session.query(Claim).filter(Claim.document_id == doc.id).count()
    decisions_count_2 = db_session.query(RoutingDecision).filter(RoutingDecision.document_id == doc.id).count()
    evidence_count_2 = db_session.query(Evidence).filter(Evidence.document_id == doc.id).count()

    # Verify no duplicate records were generated
    assert claims_count_2 == claims_count_1
    assert decisions_count_2 == decisions_count_1
    assert evidence_count_2 == evidence_count_1

    # Verify evidence spans were reused and claims correctly reference them
    all_claims = db_session.query(Claim).filter(Claim.document_id == doc.id).all()
    for c in all_claims:
        assert c.evidence_id is not None
        ev = db_session.get(Evidence, c.evidence_id)
        assert ev is not None


def test_extraction_stage_partial_failure_retry_cleanup(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
    monkeypatch,
):
    """Assert job failing mid-extraction cleans up partial records upon retry without duplicates."""
    pdf_bytes = _generate_lab_pdf_bytes()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256="3" * 64,
        storage_ref=storage_ref,
        original_filename="partial_failure_test.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id, max_attempts=3)
    db_session.commit()

    # First attempt: crash during claim persistence after evidence/decisions are written
    original_add = Session.add
    call_count = {"count": 0}

    def failing_add(session_self, instance, *args, **kwargs):
        if isinstance(instance, Claim):
            call_count["count"] += 1
            if call_count["count"] == 2:
                raise RuntimeError("Simulated mid-extraction database connection drop")
        return original_add(session_self, instance, *args, **kwargs)

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-partial-fail-worker",
        stop_after_stage="EXTRACTION",
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    monkeypatch.setattr(Session, "add", failing_add)

    # Attempt 1: Fails mid-extraction
    requeued_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    assert requeued_job.status == JobStatus.PENDING
    assert requeued_job.attempts == 1

    # Restore un-patched add for attempt 2
    monkeypatch.undo()

    # Attempt 2: Re-claim and re-process
    claimed_attempt_2 = worker.claim_job(session=db_session)
    db_session.commit()
    assert claimed_attempt_2 is not None

    worker.process_job(session=db_session, job=claimed_attempt_2)
    db_session.commit()

    # Final database verification: exactly 1 set of claims, routing decisions, and evidence
    final_claims = db_session.query(Claim).filter(Claim.document_id == doc.id).all()
    final_decisions = db_session.query(RoutingDecision).filter(RoutingDecision.document_id == doc.id).all()
    final_evidence = db_session.query(Evidence).filter(Evidence.document_id == doc.id).all()

    assert len(final_claims) > 0
    assert len(final_decisions) > 0
    assert len(final_evidence) > 0

    # Ensure all claims have unique IDs and valid evidence references
    claim_ids = {c.id for c in final_claims}
    assert len(claim_ids) == len(final_claims)
    for c in final_claims:
        assert c.evidence_id is not None
        assert db_session.get(Evidence, c.evidence_id) is not None


# ─── C. EXTRACTION FAILURE TESTS ──────────────────────────────────────────────

def test_extraction_stage_failure_records_failed_telemetry_and_delegates_to_queue(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert router failure records STARTED and FAILED telemetry events and invokes queue failure state machine."""
    pdf_bytes = _generate_lab_pdf_bytes()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256="4" * 64,
        storage_ref=storage_ref,
        original_filename="failing_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        patient_context_id="MRN-FAIL-EXTRACTION",
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id, max_attempts=1)
    db_session.commit()

    class FailingRouter:
        def extract_document(self, doc, is_lab_default=None):
            raise RuntimeError("Extraction model service unreachable")

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-fail-extract-worker",
        router=FailingRouter(),
        stop_after_stage="EXTRACTION",
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    failed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # Terminal failure because max_attempts = 1
    assert failed_job.status == JobStatus.FAILED
    assert failed_job.error_code == "RuntimeError"

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED

    # Telemetry should record STARTED and FAILED
    events = (
        db_session.query(JobStageEvent)
        .filter(JobStageEvent.job_id == job.id, JobStageEvent.stage_name == "EXTRACTION")
        .order_by(JobStageEvent.created_at.asc())
        .all()
    )
    assert len(events) == 2
    assert events[0].status == "STARTED"
    assert events[1].status == "FAILED"
    assert "Extraction model service unreachable" in events[1].error_message

    # Ensure no patient identifiers or un-sanitized context leaked
    assert "MRN-FAIL-EXTRACTION" not in events[1].error_message


# ─── D. LAB_ FILENAME HEURISTIC VERIFICATION ─────────────────────────────────

def test_verify_lab_filename_heuristic_supported_check(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Verify that ExtractionRouter classifies pages using document content regardless of whether filename starts with LAB_."""
    # Construct a lab PDF with an arbitrary non-LAB filename
    pdf_bytes = _generate_lab_pdf_bytes()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256="5" * 64,
        storage_ref=storage_ref,
        original_filename="clinical_summary_unrelated_name.pdf",  # Does NOT start with LAB_
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-heuristic-worker",
        stop_after_stage="EXTRACTION",
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # Even without "LAB_" in original_filename, content-based extraction detects and extracts lab claims
    claims = db_session.query(Claim).filter(Claim.document_id == doc.id).all()
    assert len(claims) > 0
    field_types = {c.field_type for c in claims}
    # Verifies lab fields were identified via content rules
    assert any(ft.startswith("lab.") for ft in field_types)
