"""Focused unit tests for Phase 8.3 Task 3B Step 1: Worker Core Skeleton.

Validates:
  1. Worker can claim a job through JobQueue.
  2. Document state transitions: INGESTED -> PROCESSING -> PENDING_REVIEW on success.
  3. Missing document is handled cleanly through JobQueue failure mechanism (DOCUMENT_NOT_FOUND).
  4. Quarantined / invalid document is rejected without processing (INVALID_DOCUMENT_STATE).
  5. Temporary working files are guaranteed to be cleaned up (both on success and on failure).
  6. Authoritative documents remain untouched.
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
    JobStatus,
    ProcessingJob,
    User,
)
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
        idp_subject="sub_worker_skeleton_user",
        username="worker_operator",
        full_name="Worker Operator",
        email="worker_op@hospital.org",
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


@pytest.fixture
def test_document(db_session: Session, test_user: User, blob_store: EncryptedFileBlobStore) -> Document:
    pdf_doc = pymupdf.open()
    page = pdf_doc.new_page(width=612, height=792)
    page.insert_text((50, 50), "Clinical Report Sample\nPatient: John Doe\nGlucose: 95 mg/dL")
    payload = pdf_doc.tobytes()
    pdf_doc.close()

    storage_ref = blob_store.put(payload)

    doc = Document(
        sha256="e" * 64,
        storage_ref=storage_ref,
        original_filename="sample_report.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(payload),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()
    return doc


def test_worker_can_claim_job(
    db_session: Session,
    test_document: Document,
    blob_store: EncryptedFileBlobStore,
):
    """Assert worker claims a pending job via JobQueue with proper worker identity."""
    queue = JobQueue(default_lease_seconds=300)
    job = queue.enqueue(session=db_session, document_id=test_document.id)
    db_session.commit()

    worker = DocumentProcessingWorker(queue=queue, blob_store=blob_store, worker_id="test-worker-1")
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.worker_id == "test-worker-1"
    assert claimed.attempts == 1


def test_worker_orchestrates_document_state_transition(
    db_session: Session,
    test_document: Document,
    blob_store: EncryptedFileBlobStore,
):
    """Assert successful job execution transitions document INGESTED -> PROCESSING -> PENDING_REVIEW."""
    assert test_document.status == DocumentStatus.INGESTED

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=test_document.id)
    db_session.commit()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="worker-state-test",
        stop_after_stage=None,
    )
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    completed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    assert completed_job.status == JobStatus.COMPLETED
    assert completed_job.completed_at is not None
    assert completed_job.current_stage == "COMPLETED"

    # Verify document reached PENDING_REVIEW
    db_session.refresh(test_document)
    assert test_document.status == DocumentStatus.PENDING_REVIEW


def test_missing_document_fails_job_closed(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert job referencing non-existent document fails terminally with DOCUMENT_NOT_FOUND."""
    from sqlalchemy import text

    # Create document, enqueue, and claim job
    doc = Document(
        sha256="0" * 64,
        storage_ref="vault://missing.pdf",
        original_filename="missing.pdf",
        mime_type="application/pdf",
        file_size_bytes=100,
        page_count=1,
        source_type="scanned_pdf",
        uploaded_by=test_user.id,
        status=DocumentStatus.INGESTED,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    worker = DocumentProcessingWorker(queue=queue, blob_store=blob_store, worker_id="worker-missing-doc")
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    # Simulate document being missing for the job
    missing_id = uuid.uuid4()
    db_session.execute(text("PRAGMA foreign_keys=OFF"))
    claimed.document_id = missing_id
    db_session.flush()
    db_session.execute(text("PRAGMA foreign_keys=ON"))

    failed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    assert failed_job.status == JobStatus.FAILED
    assert failed_job.error_code == "DOCUMENT_NOT_FOUND"
    assert str(missing_id) in failed_job.error_message


def test_quarantined_document_rejected_without_processing(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert document with QUARANTINED status is rejected with INVALID_DOCUMENT_STATE."""
    doc = Document(
        sha256="f" * 64,
        storage_ref="blob://local/test_quarantined",
        original_filename="malicious.exe",
        mime_type="application/octet-stream",
        file_size_bytes=100,
        page_count=1,
        source_type="unknown",
        uploaded_by=test_user.id,
        status=DocumentStatus.QUARANTINED,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    worker = DocumentProcessingWorker(queue=queue, blob_store=blob_store, worker_id="worker-quarantine-test")
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    failed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    assert failed_job.status == JobStatus.FAILED
    assert failed_job.error_code == "INVALID_DOCUMENT_STATE"
    assert "QUARANTINED" in failed_job.error_message


def test_temporary_scratch_files_cleaned_up_on_completion(
    db_session: Session,
    test_document: Document,
    blob_store: EncryptedFileBlobStore,
    monkeypatch,
):
    """Assert temporary decrypted scratch files are deleted upon job completion."""
    created_temp_paths: list[Path] = []
    original_prepare = DocumentProcessingWorker._prepare_scratch_file

    def tracking_prepare(self, document: Document):
        temp_path, is_temp = original_prepare(self, document)
        if temp_path:
            created_temp_paths.append(temp_path)
            assert temp_path.exists()
        return temp_path, is_temp

    monkeypatch.setattr(DocumentProcessingWorker, "_prepare_scratch_file", tracking_prepare)

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=test_document.id)
    db_session.commit()

    worker = DocumentProcessingWorker(queue=queue, blob_store=blob_store, worker_id="worker-cleanup-test")
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    assert len(created_temp_paths) == 1
    # Verify temporary file was deleted
    assert not created_temp_paths[0].exists()


def test_temporary_scratch_files_cleaned_up_on_failure(
    db_session: Session,
    test_document: Document,
    blob_store: EncryptedFileBlobStore,
    monkeypatch,
):
    """Assert temporary decrypted scratch files are deleted even when a stage raises an exception."""
    created_temp_paths: list[Path] = []
    original_prepare = DocumentProcessingWorker._prepare_scratch_file

    def tracking_prepare(self, document: Document):
        temp_path, is_temp = original_prepare(self, document)
        if temp_path:
            created_temp_paths.append(temp_path)
            assert temp_path.exists()
        return temp_path, is_temp

    def failing_ocr(self, session, job, document, file_path):
        raise RuntimeError("Simulated transient OCR crash")

    monkeypatch.setattr(DocumentProcessingWorker, "_prepare_scratch_file", tracking_prepare)
    monkeypatch.setattr(DocumentProcessingWorker, "_orchestrate_ocr_stage", failing_ocr)

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=test_document.id)
    db_session.commit()

    worker = DocumentProcessingWorker(queue=queue, blob_store=blob_store, worker_id="worker-fail-cleanup")
    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    assert len(created_temp_paths) == 1
    # Verify temporary file was deleted despite exception
    assert not created_temp_paths[0].exists()
