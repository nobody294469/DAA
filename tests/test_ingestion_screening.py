"""Unit tests for Phase 8.3 Task 2: Document Ingestion Screening & Deduplication.

Verifies:
  - Valid PDF accepted with INGESTED status and exact page count
  - Header sniffing and MIME mismatch quarantine (spoofing detection)
  - Malformed / truncated PDF quarantine (fail-closed)
  - Unsupported file format rejection (FAILED_INGEST)
  - Oversized file rejection (FAILED_INGEST)
  - Page limit violation rejection (FAILED_INGEST)
  - Cryptographic SHA-256 calculation fidelity
  - Database deduplication linking (duplicate_of without duplicate storage)
  - Quarantined input isolation from extraction pipelines
  - Plaintext elimination at rest in blob store
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import uuid
import pymupdf
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from clindoc.db.base import Base
from clindoc.db.enums import DocumentStatus
from clindoc.db.models import Document, Role, User, UserRole
from clindoc.ingest.service import (
    DEFAULT_MAX_FILE_SIZE_BYTES,
    DEFAULT_MAX_PAGE_COUNT,
    DocumentIngestionService,
    ScreeningResult,
    sniff_mime_type,
)
from clindoc.storage.blob import EncryptedFileBlobStore


def _create_synthetic_pdf(page_count: int = 1, text: str = "Clinical Lab Report") -> bytes:
    """Helper to generate in-memory valid PDF bytes."""
    doc = pymupdf.open()
    for i in range(page_count):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50 + (i * 10)), f"{text} - Page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def db_session() -> Session:
    """In-memory SQLite database session with compiled schema."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def test_user(db_session: Session) -> User:
    """Create a test operator user."""
    user = User(
        id=uuid.uuid4(),
        idp_subject="auth0|operator_test_123",
        username="test_operator",
        full_name="Document Operator",
        email="operator@hospital.org",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def blob_store(tmp_path: Path) -> EncryptedFileBlobStore:
    return EncryptedFileBlobStore(
        root_dir=tmp_path / "blobs",
        encryption_key=b"12345678901234567890123456789012",
    )


@pytest.fixture
def ingest_service(blob_store: EncryptedFileBlobStore) -> DocumentIngestionService:
    return DocumentIngestionService(
        blob_store=blob_store,
        max_file_size_bytes=10 * 1024 * 1024,  # 10 MB for testing
        max_page_count=50,                     # 50 pages for testing
    )


def test_valid_pdf_accepted(ingest_service: DocumentIngestionService):
    """Assert valid clinical PDF is accepted with INGESTED status and correct page count."""
    pdf_bytes = _create_synthetic_pdf(page_count=3, text="Metabolic Panel")
    result = ingest_service.screen_bytes(
        pdf_bytes,
        original_filename="lab_report.pdf",
        declared_mime_type="application/pdf",
    )

    assert result.status == DocumentStatus.INGESTED
    assert result.is_quarantined is False
    assert result.page_count == 3
    assert result.mime_type == "application/pdf"
    assert result.file_size_bytes == len(pdf_bytes)
    assert result.sha256 == hashlib.sha256(pdf_bytes).hexdigest()
    assert result.error_message is None


def test_mismatched_file_header_quarantined(ingest_service: DocumentIngestionService):
    """Assert declared PDF with non-PDF content triggers spoofing quarantine (W-02 / F-19)."""
    fake_pdf = b"Plain text claiming to be a PDF without magic bytes"
    result = ingest_service.screen_bytes(
        fake_pdf,
        original_filename="spoofed.pdf",
        declared_mime_type="application/pdf",
    )

    # Failed closed: declared PDF but magic bytes mismatch
    assert result.status == DocumentStatus.QUARANTINED
    assert result.is_quarantined is True
    assert "MIME header spoofing detected" in (result.error_message or "")


def test_malformed_truncated_pdf_quarantined(ingest_service: DocumentIngestionService):
    """Assert truncated/corrupt PDF header is quarantined."""
    corrupted_pdf = b"%PDF-1.7\nCorrupted byte stream with no xref or trailer"
    result = ingest_service.screen_bytes(
        corrupted_pdf,
        original_filename="corrupted.pdf",
        declared_mime_type="application/pdf",
    )

    assert result.status == DocumentStatus.QUARANTINED
    assert result.is_quarantined is True
    assert "PDF structural sanity check failed" in (result.error_message or "")


def test_unsupported_mime_rejected(ingest_service: DocumentIngestionService):
    """Assert unsupported formats (.exe, .zip) are rejected with FAILED_INGEST."""
    binary_payload = b"\x4d\x5a\x90\x00"  # MZ executable header
    result = ingest_service.screen_bytes(
        binary_payload,
        original_filename="malicious.exe",
        declared_mime_type="application/x-dosexec",
    )

    assert result.status == DocumentStatus.FAILED_INGEST
    assert result.is_quarantined is False
    assert "Unsupported file format" in (result.error_message or "")


def test_oversized_input_rejected(blob_store: EncryptedFileBlobStore):
    """Assert inputs exceeding max_file_size_bytes are rejected."""
    service = DocumentIngestionService(blob_store=blob_store, max_file_size_bytes=1000)
    oversized = b"%PDF-" + b"0" * 2000

    result = service.screen_bytes(oversized, original_filename="huge.pdf")
    assert result.status == DocumentStatus.FAILED_INGEST
    assert "exceeds maximum limit" in (result.error_message or "")


def test_page_limit_violation_rejected(blob_store: EncryptedFileBlobStore):
    """Assert PDFs exceeding page limit are rejected with FAILED_INGEST."""
    service = DocumentIngestionService(blob_store=blob_store, max_page_count=2)
    pdf_bytes = _create_synthetic_pdf(page_count=3)

    result = service.screen_bytes(pdf_bytes, original_filename="too_many_pages.pdf")
    assert result.status == DocumentStatus.FAILED_INGEST
    assert "Page count 3 exceeds maximum limit of 2" in (result.error_message or "")


def test_zero_byte_document_rejected(ingest_service: DocumentIngestionService):
    """Assert zero-byte upload is rejected."""
    result = ingest_service.screen_bytes(b"", original_filename="empty.pdf")
    assert result.status == DocumentStatus.FAILED_INGEST
    assert "empty" in (result.error_message or "").lower()


def test_sha256_calculated_correctly(ingest_service: DocumentIngestionService):
    """Assert exact cryptographic hash calculation."""
    pdf_bytes = _create_synthetic_pdf(page_count=1)
    result = ingest_service.screen_bytes(pdf_bytes, original_filename="doc.pdf")
    assert result.sha256 == hashlib.sha256(pdf_bytes).hexdigest()


def test_duplicate_detection_and_linking(
    db_session: Session,
    test_user: User,
    ingest_service: DocumentIngestionService,
    blob_store: EncryptedFileBlobStore,
):
    """Assert identical uploads reuse storage_ref and record duplicate_of foreign key."""
    pdf_bytes = _create_synthetic_pdf(page_count=1, text="Unique Patient Encounter 101")

    # 1. Ingest original document
    doc1 = ingest_service.ingest_document(
        session=db_session,
        raw_bytes=pdf_bytes,
        original_filename="encounter_1.pdf",
        uploaded_by=test_user.id,
    )

    assert doc1.status == DocumentStatus.INGESTED
    assert doc1.duplicate_of is None
    assert doc1.storage_ref.startswith("blob://local/")
    assert blob_store.exists(doc1.storage_ref)

    # 2. Ingest duplicate document
    doc2 = ingest_service.ingest_document(
        session=db_session,
        raw_bytes=pdf_bytes,
        original_filename="encounter_1_reupload.pdf",
        uploaded_by=test_user.id,
    )

    assert doc2.id != doc1.id
    assert doc2.duplicate_of == doc1.id
    assert doc2.storage_ref == doc1.storage_ref
    assert doc2.sha256 == doc1.sha256

    # Verify both records persist in DB
    all_docs = db_session.execute(select(Document).filter_by(sha256=doc1.sha256)).scalars().all()
    assert len(all_docs) == 2


def test_quarantined_input_cannot_proceed_to_extraction(
    db_session: Session,
    test_user: User,
    ingest_service: DocumentIngestionService,
):
    """Assert quarantined documents are flagged and barred from extraction querying."""
    corrupted_bytes = b"%PDF-Truncated-Stream-Illegal-Syntax"
    doc = ingest_service.ingest_document(
        session=db_session,
        raw_bytes=corrupted_bytes,
        original_filename="suspicious.pdf",
        uploaded_by=test_user.id,
    )

    assert doc.status == DocumentStatus.QUARANTINED

    # Pipeline worker query simulation: workers only query ready/ingested documents
    eligible_for_extraction = db_session.execute(
        select(Document).filter(
            Document.id == doc.id,
            Document.status.in_([DocumentStatus.INGESTED]),
        )
    ).scalar_one_or_none()

    assert eligible_for_extraction is None


def test_no_plaintext_in_blob_store(
    db_session: Session,
    test_user: User,
    ingest_service: DocumentIngestionService,
    blob_store: EncryptedFileBlobStore,
):
    """Assert no unencrypted plaintext bytes remain anywhere on disk after ingestion."""
    secret_marker = "HIGHLY_SENSITIVE_CLINICAL_TRIAL_DATA_998877"
    pdf_bytes = _create_synthetic_pdf(page_count=1, text=secret_marker)

    doc = ingest_service.ingest_document(
        session=db_session,
        raw_bytes=pdf_bytes,
        original_filename="trial_data.pdf",
        uploaded_by=test_user.id,
    )

    # Read ciphertext directly from disk
    raw_ciphertext = blob_store.get_raw_ciphertext(doc.storage_ref)
    assert secret_marker.encode("utf-8") not in raw_ciphertext
