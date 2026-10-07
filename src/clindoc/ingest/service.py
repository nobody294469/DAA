"""Document ingestion validation, safety screening, and blob persistence service.

Authority:
  - PRODUCTIZATION_SPEC.md §2 (W-01 Upload, W-02 Safety screening)
  - PRODUCTIZATION_SPEC.md §4.2, §4.4, §5 (Threat T-01, T-04, T-08, T-18), §9.2, §13 (F-01, F-19)
  - PRODUCTIZATION_DECISIONS.md PD-007, §7 Item 3

Implements:
  - SHA-256 computation and deduplication linking (duplicate_of)
  - MIME type sniffing and magic-number validation
  - File-size and page-count limit enforcement (configurable operational defaults)
  - Non-rendering PDF structural sanity checks
  - Fail-closed quarantine routing (QUARANTINED / FAILED_INGEST)
  - Encrypted blob store persistence without invoking ML/extraction pipelines
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Optional, Set
import uuid

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from clindoc.db.enums import DocumentStatus
from clindoc.db.models import Document
from clindoc.observability.metrics import get_metrics
from clindoc.storage.blob import BlobStore


# Configurable operational defaults (PRODUCTIZATION_SPEC.md §2 W-01 and §5 T-08 mandate
# that file-size and page-count limits exist and be enforced, but do NOT explicitly specify
# these exact numerical values. These values are operational defaults isolated behind configuration).
DEFAULT_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB configurable operational default
DEFAULT_MAX_PAGE_COUNT = 100                    # 100 pages configurable operational default
SUPPORTED_MIME_TYPES: Set[str] = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
}


@dataclass
class ScreeningResult:
    """Explicit outcome of document safety screening and limit checks."""

    status: DocumentStatus
    sha256: str
    file_size_bytes: int
    page_count: int
    mime_type: str
    is_quarantined: bool
    is_duplicate: bool
    duplicate_of: Optional[uuid.UUID] = None
    storage_ref: Optional[str] = None
    error_message: Optional[str] = None


class IngestionScreeningError(Exception):
    """Raised when an unrecoverable ingestion screening error occurs."""
    pass


def sniff_mime_type(data: bytes) -> Optional[str]:
    """Inspect magic numbers in byte payload to determine MIME type."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):
        return "image/tiff"
    return None


class DocumentIngestionService:
    """Handles document pre-ingestion screening, deduplication, and encrypted persistence."""

    def __init__(
        self,
        blob_store: BlobStore,
        max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
        max_page_count: int = DEFAULT_MAX_PAGE_COUNT,
    ) -> None:
        self.blob_store = blob_store
        self.max_file_size_bytes = max_file_size_bytes
        self.max_page_count = max_page_count

    def screen_bytes(
        self,
        raw_bytes: bytes,
        original_filename: str,
        declared_mime_type: Optional[str] = None,
    ) -> ScreeningResult:
        """Perform stateless safety screening, type sniffing, and structural integrity checks.

        Fails closed on corrupted, oversized, or malicious content.
        """
        file_size = len(raw_bytes)
        sha256_hex = hashlib.sha256(raw_bytes).hexdigest()

        # 1. Zero-byte check
        if file_size == 0:
            get_metrics().record_document_status("failed_ingest")
            get_metrics().record_security_event("malformed_file")
            return ScreeningResult(
                status=DocumentStatus.FAILED_INGEST,
                sha256=sha256_hex,
                file_size_bytes=0,
                page_count=0,
                mime_type=declared_mime_type or "application/octet-stream",
                is_quarantined=False,
                is_duplicate=False,
                error_message="Document payload is empty (zero bytes).",
            )

        # 2. File size ceiling check (F-01 / F-19)
        if file_size > self.max_file_size_bytes:
            get_metrics().record_document_status("failed_ingest")
            get_metrics().record_security_event("oversized_file")
            return ScreeningResult(
                status=DocumentStatus.FAILED_INGEST,
                sha256=sha256_hex,
                file_size_bytes=file_size,
                page_count=0,
                mime_type=declared_mime_type or "application/octet-stream",
                is_quarantined=False,
                is_duplicate=False,
                error_message=f"File size {file_size} bytes exceeds maximum limit of {self.max_file_size_bytes} bytes.",
            )

        # 3. Magic-number / MIME type sniffing
        sniffed_mime = sniff_mime_type(raw_bytes)

        # If declared MIME is explicitly unsupported, reject immediately with FAILED_INGEST
        if declared_mime_type and declared_mime_type not in SUPPORTED_MIME_TYPES:
            get_metrics().record_document_status("failed_ingest")
            get_metrics().record_security_event("malformed_file")
            return ScreeningResult(
                status=DocumentStatus.FAILED_INGEST,
                sha256=sha256_hex,
                file_size_bytes=file_size,
                page_count=0,
                mime_type=declared_mime_type,
                is_quarantined=False,
                is_duplicate=False,
                error_message=f"Unsupported file format '{declared_mime_type}'. Permitted: {sorted(SUPPORTED_MIME_TYPES)}.",
            )

        # Header mismatch detection (declared a supported MIME like PDF, but magic bytes don't match)
        if declared_mime_type and declared_mime_type != sniffed_mime:
            get_metrics().record_document_status("quarantined")
            get_metrics().record_security_event("mime_spoof")
            return ScreeningResult(
                status=DocumentStatus.QUARANTINED,
                sha256=sha256_hex,
                file_size_bytes=file_size,
                page_count=0,
                mime_type=declared_mime_type,
                is_quarantined=True,
                is_duplicate=False,
                error_message=f"MIME header spoofing detected: declared '{declared_mime_type}' but observed '{sniffed_mime or 'unknown'}'.",
            )

        effective_mime = sniffed_mime or declared_mime_type or "application/octet-stream"

        if effective_mime not in SUPPORTED_MIME_TYPES:
            get_metrics().record_document_status("failed_ingest")
            get_metrics().record_security_event("malformed_file")
            return ScreeningResult(
                status=DocumentStatus.FAILED_INGEST,
                sha256=sha256_hex,
                file_size_bytes=file_size,
                page_count=0,
                mime_type=effective_mime,
                is_quarantined=False,
                is_duplicate=False,
                error_message=f"Unsupported file format '{effective_mime}'. Permitted: {sorted(SUPPORTED_MIME_TYPES)}.",
            )

        # 4. PDF Structural and Sanity Screening (W-02 / F-19)
        page_count = 1
        if effective_mime == "application/pdf":
            try:
                # Non-rendering structural probe using PyMuPDF
                pdf_doc = pymupdf.open(stream=raw_bytes, filetype="pdf")
                
                # Check for active password encryption (Threat T-08)
                if pdf_doc.is_encrypted:
                    pdf_doc.close()
                    get_metrics().record_document_status("quarantined")
                    get_metrics().record_security_event("encrypted_pdf")
                    return ScreeningResult(
                        status=DocumentStatus.QUARANTINED,
                        sha256=sha256_hex,
                        file_size_bytes=file_size,
                        page_count=0,
                        mime_type=effective_mime,
                        is_quarantined=True,
                        is_duplicate=False,
                        error_message="Encrypted/password-protected PDFs are rejected by security policy (T-08).",
                    )

                page_count = len(pdf_doc)
                if page_count == 0:
                    pdf_doc.close()
                    get_metrics().record_document_status("quarantined")
                    get_metrics().record_security_event("malformed_file")
                    return ScreeningResult(
                        status=DocumentStatus.QUARANTINED,
                        sha256=sha256_hex,
                        file_size_bytes=file_size,
                        page_count=0,
                        mime_type=effective_mime,
                        is_quarantined=True,
                        is_duplicate=False,
                        error_message="PDF contains zero readable pages (corrupt catalog).",
                    )

                # Light structural probe: read first page bounds to ensure xref is intact
                _ = pdf_doc[0].rect
                pdf_doc.close()

            except Exception as exc:
                get_metrics().record_document_status("quarantined")
                get_metrics().record_security_event("malformed_file")
                return ScreeningResult(
                    status=DocumentStatus.QUARANTINED,
                    sha256=sha256_hex,
                    file_size_bytes=file_size,
                    page_count=0,
                    mime_type=effective_mime,
                    is_quarantined=True,
                    is_duplicate=False,
                    error_message=f"PDF structural sanity check failed: {type(exc).__name__}: {exc}",
                )

            # 5. Page count limit check
            if page_count > self.max_page_count:
                get_metrics().record_document_status("failed_ingest")
                get_metrics().record_security_event("excessive_pages")
                return ScreeningResult(
                    status=DocumentStatus.FAILED_INGEST,
                    sha256=sha256_hex,
                    file_size_bytes=file_size,
                    page_count=page_count,
                    mime_type=effective_mime,
                    is_quarantined=False,
                    is_duplicate=False,
                    error_message=f"Page count {page_count} exceeds maximum limit of {self.max_page_count}.",
                )

        # Successfully passed screening
        get_metrics().record_document_status("ingested")
        return ScreeningResult(
            status=DocumentStatus.INGESTED,
            sha256=sha256_hex,
            file_size_bytes=file_size,
            page_count=page_count,
            mime_type=effective_mime,
            is_quarantined=False,
            is_duplicate=False,
            error_message=None,
        )

    def ingest_document(
        self,
        session: Session,
        raw_bytes: bytes,
        original_filename: str,
        uploaded_by: uuid.UUID,
        declared_mime_type: Optional[str] = None,
        patient_context_id: Optional[str] = None,
        commit: bool = True,
    ) -> Document:
        """Execute full upload screening, deduplication, encrypted storage, and persistence.

        Thin boundary: does NOT run OCR or ML extraction.
        """
        screening = self.screen_bytes(
            raw_bytes,
            original_filename=original_filename,
            declared_mime_type=declared_mime_type,
        )

        # Deduplication check against existing documents by SHA-256
        existing_doc = session.execute(
            select(Document).filter_by(sha256=screening.sha256).order_by(Document.uploaded_at)
        ).scalars().first()

        storage_ref: str
        duplicate_of: Optional[uuid.UUID] = None

        if existing_doc is not None:
            # Content deduplication: reuse existing storage_ref without writing duplicate ciphertext
            storage_ref = existing_doc.storage_ref
            duplicate_of = existing_doc.id
            screening.is_duplicate = True
            screening.duplicate_of = duplicate_of
            screening.storage_ref = storage_ref
        elif screening.status in (DocumentStatus.INGESTED, DocumentStatus.QUARANTINED):
            # Persist encrypted ciphertext into blob store
            storage_ref = self.blob_store.put(raw_bytes)
            screening.storage_ref = storage_ref
        else:
            # FAILED_INGEST without storage
            storage_ref = f"none://failed_ingest/{screening.sha256}"
            screening.storage_ref = storage_ref

        # Persist Document entity into database
        document = Document(
            sha256=screening.sha256,
            storage_ref=storage_ref,
            original_filename=original_filename,
            mime_type=screening.mime_type,
            file_size_bytes=screening.file_size_bytes,
            page_count=max(1, screening.page_count),
            source_type="scanned_pdf" if screening.mime_type == "application/pdf" else "image",
            uploaded_by=uploaded_by,
            status=screening.status,
            patient_context_id=patient_context_id,
            duplicate_of=duplicate_of,
        )
        session.add(document)
        if commit:
            session.commit()
        else:
            session.flush()

        return document
