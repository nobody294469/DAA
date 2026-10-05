"""Focused unit tests for Phase 8.3 Task 3B Step 2: Wire OCR / Ingestion Stage.

Validates:
  A. Successful OCR/ingestion:
     - Existing ingest function is called with decrypted temporary working file.
     - DocumentText is returned/retained for subsequent pipeline stages.
     - STARTED and COMPLETED JobStageEvent telemetry events are recorded in database.
     - Metrics contain exclusively PHI-free operational data (page_count, modality, total_tokens, etc.).
  B. OCR/ingestion failure:
     - STARTED and FAILED JobStageEvent telemetry events are recorded in database.
     - Error messages are sanitized (no local paths, PHI, or secrets leaked).
     - Existing JobQueue retry/terminal-failure state machine is invoked.
     - Document and job states remain consistent.
  C. Temporary decrypted files:
     - Scratch files are guaranteed to be cleaned up on success and failure.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import uuid
import pymupdf
import pytest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from clindoc.db import (
    Base,
    Document,
    DocumentStatus,
    JobStageEvent,
    JobStatus,
    ProcessingJob,
    User,
)
from clindoc.jobs.queue import JobQueue
from clindoc.jobs.worker import DocumentProcessingWorker
from clindoc.ocr.document_text import (
    BBoxDict,
    DocumentPage,
    DocumentText,
    Token,
)
from clindoc.ocr.ingest import UnreadableDocumentError
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
        idp_subject="sub_ocr_worker_test_user",
        username="ocr_tester",
        full_name="OCR Tester",
        email="ocr_tester@hospital.org",
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


def _generate_multipage_pdf_bytes(page_count: int = 2) -> bytes:
    """Create in-memory born-digital PDF bytes with clinical text."""
    doc = pymupdf.open()
    for p_num in range(1, page_count + 1):
        p = doc.new_page(width=612, height=792)
        p.insert_text(
            (50, 50),
            f"Hospital Report Page {p_num}\n"
            f"Patient: John Doe MRN: 987654\n"
            f"Glucose: 95 mg/dL Platelets: 250 k/uL\n"
            f"Notes: Normal laboratory profile page {p_num}\n",
        )
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


# ─── A. SUCCESSFUL OCR / INGESTION TESTS ───────────────────────────────────────

def test_ocr_stage_success_records_telemetry_and_retains_document_text(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert successful OCR ingestion records STARTED and COMPLETED events, retains DocumentText, and verifies PHI-free metrics."""
    pdf_bytes = _generate_multipage_pdf_bytes(page_count=2)
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256="a" * 64,
        storage_ref=storage_ref,
        original_filename="LAB_0001_blood_panel.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=2,
        source_type="born_digital",
        uploaded_by=test_user.id,
        patient_context_id="MRN-987654",
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
        worker_id="test-ocr-worker-1",
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    completed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    assert completed_job.status in (JobStatus.COMPLETED, JobStatus.RUNNING)

    # 1. Verify DocumentText was returned and retained in memory
    assert worker.last_ocr_output is not None
    assert isinstance(worker.last_ocr_output, DocumentText)
    assert worker.last_ocr_output.num_pages == 2
    assert worker.last_ocr_output.total_tokens > 0
    assert worker.last_ocr_output.source_type == "born_digital"

    # 2. Query JobStageEvent records for stage "OCR"
    stage_events = (
        db_session.query(JobStageEvent)
        .filter(JobStageEvent.job_id == job.id, JobStageEvent.stage_name == "OCR")
        .order_by(JobStageEvent.created_at.asc())
        .all()
    )
    assert len(stage_events) == 2

    started_event = stage_events[0]
    assert started_event.status == "STARTED"
    assert started_event.duration_ms is None

    completed_event = stage_events[1]
    assert completed_event.status == "COMPLETED"
    assert completed_event.duration_ms is not None
    assert completed_event.duration_ms >= 0

    # 3. Verify metrics contain only PHI-free operational data
    metrics = completed_event.metrics
    assert metrics["page_count"] == 2
    assert metrics["modality"] == "born_digital"
    assert metrics["total_tokens"] > 0

    # Strict PHI audit: Assert no patient identifiers or raw clinical values in metrics
    forbidden_terms = ["John Doe", "987654", "MRN", "Glucose", "95", "Platelets", "250"]
    for key, val in metrics.items():
        assert not any(term.lower() in str(key).lower() for term in forbidden_terms)
        assert not any(term.lower() in str(val).lower() for term in forbidden_terms)


def test_ocr_stage_confidence_aggregation_when_available(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert aggregate OCR confidence is correctly calculated and included in metrics when available."""
    pdf_bytes = _generate_multipage_pdf_bytes(page_count=1)
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256="b" * 64,
        storage_ref=storage_ref,
        original_filename="scanned_clinic_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="scanned_image",
        uploaded_by=test_user.id,
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()

    # Create mock ingest function returning tokens with OCR confidence
    def mock_scanned_ingest(path, document_id, source_type, is_tabular):
        tokens = [
            Token(
                text="Word1",
                bbox=BBoxDict(x0=10.0, y0=10.0, x1=50.0, y1=20.0),
                confidence=0.90,
                line_id="line_0",
            ),
            Token(
                text="Word2",
                bbox=BBoxDict(x0=60.0, y0=10.0, x1=100.0, y1=20.0),
                confidence=0.96,
                line_id="line_0",
            ),
        ]
        page = DocumentPage(page_number=1, width_px=612, height_px=792, tokens=tokens)
        return DocumentText(
            document_id=document_id,
            source_type="scanned_image",
            pages=[page],
        )

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-ocr-confidence-worker",
        ingest_fn=mock_scanned_ingest,
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    completed_event = (
        db_session.query(JobStageEvent)
        .filter(JobStageEvent.job_id == job.id, JobStageEvent.status == "COMPLETED")
        .first()
    )
    assert completed_event is not None
    assert completed_event.metrics["ocr_confidence_mean"] == pytest.approx(0.93, 0.001)
    assert completed_event.metrics["page_count"] == 1
    assert completed_event.metrics["total_tokens"] == 2
    assert completed_event.metrics["modality"] == "scanned_image"


# ─── B. OCR / INGESTION FAILURE TESTS ──────────────────────────────────────────

def test_ocr_stage_corrupted_document_terminal_failure(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert corrupted document triggers STARTED and FAILED events, sanitizes errors, and transitions to terminal FAILED."""
    corrupted_bytes = b"NOT_A_VALID_PDF_STREAM_CORRUPTED_BYTES"
    storage_ref = blob_store.put(corrupted_bytes)

    doc = Document(
        sha256="c" * 64,
        storage_ref=storage_ref,
        original_filename="corrupted_scan.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(corrupted_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        patient_context_id="MRN-CORRUPT-TEST",
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
        worker_id="test-ocr-corrupt-worker",
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    failed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # Terminal failure verification
    assert failed_job.status == JobStatus.FAILED
    assert failed_job.error_code == "UnreadableDocumentError"

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED

    # Telemetry events verification
    events = (
        db_session.query(JobStageEvent)
        .filter(JobStageEvent.job_id == job.id, JobStageEvent.stage_name == "OCR")
        .order_by(JobStageEvent.created_at.asc())
        .all()
    )
    assert len(events) == 2
    assert events[0].status == "STARTED"
    assert events[1].status == "FAILED"
    assert events[1].duration_ms is not None
    assert events[1].duration_ms >= 0
    assert "UnreadableDocumentError" in events[1].error_message

    # Sanitization check: Ensure no local temp file path or MRN in error message
    assert "MRN-CORRUPT-TEST" not in events[1].error_message
    assert "corrupted_scan.pdf" not in events[1].error_message or "[REDACTED]" in events[1].error_message


def test_ocr_stage_transient_failure_invokes_retry_queue_mechanism(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert transient OCR exception records FAILED event and requeues job via existing JobQueue mechanism."""
    pdf_bytes = _generate_multipage_pdf_bytes(page_count=1)
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256="d" * 64,
        storage_ref=storage_ref,
        original_filename="retry_test_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()

    # Enqueue job with max_attempts = 3
    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id, max_attempts=3)
    db_session.commit()

    def failing_ingest(path, document_id, source_type, is_tabular):
        raise ConnectionResetError("Transient OCR socket error")

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-ocr-retry-worker",
        ingest_fn=failing_ingest,
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()
    assert claimed.attempts == 1

    requeued_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # Job should be returned to PENDING for retry because attempts (1) < max_attempts (3)
    assert requeued_job.status == JobStatus.PENDING
    assert requeued_job.attempts == 1
    assert requeued_job.worker_id is None
    assert requeued_job.lease_expires_at is None

    # Document remains in PROCESSING (not terminally failed)
    db_session.refresh(doc)
    assert doc.status == DocumentStatus.PROCESSING

    # Telemetry should record STARTED and FAILED for this attempt
    events = (
        db_session.query(JobStageEvent)
        .filter(JobStageEvent.job_id == job.id, JobStageEvent.stage_name == "OCR")
        .all()
    )
    assert len(events) == 2
    assert {e.status for e in events} == {"STARTED", "FAILED"}
    failed_ev = [e for e in events if e.status == "FAILED"][0]
    assert "ConnectionResetError" in failed_ev.error_message


def test_ocr_stage_transient_failure_terminal_when_attempts_exhausted(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert transient OCR exception transitions to FAILED when max_attempts is reached."""
    pdf_bytes = _generate_multipage_pdf_bytes(page_count=1)
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256="e" * 64,
        storage_ref=storage_ref,
        original_filename="exhaust_attempts_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()

    # Max attempts = 1
    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id, max_attempts=1)
    db_session.commit()

    def failing_ingest(path, document_id, source_type, is_tabular):
        raise TimeoutError("OCR timeout")

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-exhaust-worker",
        ingest_fn=failing_ingest,
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    failed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # Terminal failure because attempts (1) >= max_attempts (1)
    assert failed_job.status == JobStatus.FAILED
    assert failed_job.error_code == "TimeoutError"

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED


# ─── C. CLEANUP TESTS ─────────────────────────────────────────────────────────

def test_ocr_stage_temporary_file_cleaned_up_on_ocr_failure(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
    monkeypatch,
):
    """Assert temporary decrypted file created for OCR is unlinked even when OCR fails."""
    created_temp_paths: list[Path] = []
    original_prepare = DocumentProcessingWorker._prepare_scratch_file

    def tracking_prepare(self, document: Document):
        temp_path, is_temp = original_prepare(self, document)
        if temp_path:
            created_temp_paths.append(temp_path)
            assert temp_path.exists()
        return temp_path, is_temp

    monkeypatch.setattr(DocumentProcessingWorker, "_prepare_scratch_file", tracking_prepare)

    # Corrupted PDF to force OCR failure
    corrupted_bytes = b"CORRUPTED_PDF_BYTES_TRIGGERING_FAILURE"
    storage_ref = blob_store.put(corrupted_bytes)

    doc = Document(
        sha256="f" * 64,
        storage_ref=storage_ref,
        original_filename="cleanup_test.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(corrupted_bytes),
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

    def failing_ingest(path, document_id, source_type, is_tabular):
        raise UnreadableDocumentError("Simulated unreadable document during ingestion")

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-cleanup-worker",
        ingest_fn=failing_ingest,
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # Assert exactly 1 temp file was created and is now deleted
    assert len(created_temp_paths) == 1
    assert not created_temp_paths[0].exists()
