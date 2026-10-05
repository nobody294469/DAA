"""Document Ingestion and Retrieval Endpoints (PRODUCTIZATION_SPEC §9.3).

Authority:
  - PRODUCTIZATION_SPEC.md §1.2, §2 (W-01, W-02), §4.3, §9.3
  - PRODUCTIZATION_DECISIONS.md PD-007, PD-009, PD-012

Endpoints:
  - POST /api/v1/documents: Upload document, screen, store encrypted, enqueue job, audit.
  - GET  /api/v1/documents/{id}: Retrieve document metadata under object-level authorization.
"""

from __future__ import annotations

import logging
from typing import Optional
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from clindoc.api.audit.service import AuditService, AuditServiceError
from clindoc.api.auth.dependencies import get_current_user, get_db_session
from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.auth.object_access import authorize_document_access
from clindoc.api.auth.rbac import require_role
from clindoc.api.auth.roles import CanonicalRole
from clindoc.api.deps import (
    get_audit_service,
    get_ingest_service,
    get_job_queue,
)
from clindoc.db.enums import DocumentStatus
from clindoc.db.models import Document
from clindoc.ingest.service import DocumentIngestionService
from clindoc.jobs.queue import JobQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    patient_context_id: Optional[str] = Form(None),
    current_user: AuthenticatedUser = Depends(require_role(CanonicalRole.DOCUMENT_OPERATOR)),
    session: Session = Depends(get_db_session),
    ingest_service: DocumentIngestionService = Depends(get_ingest_service),
    job_queue: JobQueue = Depends(get_job_queue),
    audit_service: AuditService = Depends(get_audit_service),
):
    """Authenticated Document Upload & Asynchronous Ingestion.

    Rules:
      1. Enforces document_operator authorization (HTTP 403 for unauthorized roles).
      2. Screens file payload (size limits, MIME magic numbers, password protection).
      3. Computes SHA-256 and handles deduplication (PD-007).
      4. Stores encrypted ciphertext in BlobStore (no plaintext PHI on disk).
      5. Enqueues a ProcessingJob for asynchronous worker execution (zero OCR/ML in request).
      6. In the SAME database transaction, records an append-only AuditEvent.
      7. Fail-closed: If audit recording or any persistence fails, the entire transaction
         is rolled back and rejected.
    """
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
    source_ip = request.client.host if request.client else None

    # Read binary payload
    try:
        raw_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read uploaded file payload: {exc}",
        ) from exc

    if not raw_bytes or len(raw_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded document payload is empty (zero bytes).",
        )

    filename = file.filename or "unknown.pdf"
    content_type = file.content_type

    try:
        # 1. Ingestion screening and persistence (commit=False to keep transaction open)
        doc = ingest_service.ingest_document(
            session=session,
            raw_bytes=raw_bytes,
            original_filename=filename,
            uploaded_by=current_user.user_id,
            declared_mime_type=content_type,
            patient_context_id=patient_context_id.strip() if patient_context_id else None,
            commit=False,
        )

        job = None
        # 2. Asynchronous job enqueueing for accepted documents
        if doc.status == DocumentStatus.INGESTED:
            job = job_queue.enqueue(
                session=session,
                document_id=doc.id,
            )

        # 3. Transaction-bound audit event recording (strictly PHI-free metadata)
        status_val = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
        audit_metadata = {
            "sha256": doc.sha256,
            "original_filename": doc.original_filename,
            "mime_type": doc.mime_type,
            "file_size_bytes": doc.file_size_bytes,
            "page_count": doc.page_count,
            "status": status_val,
            "is_duplicate": doc.duplicate_of is not None,
            "job_enqueued": job is not None,
        }

        audit_service.record_event(
            session=session,
            actor_id=str(current_user.user_id),
            event_type="DOCUMENT_INGESTED",
            entity_type="document",
            entity_id=str(doc.id),
            action="UPLOAD",
            source_ip=source_ip,
            correlation_id=correlation_id,
            metadata_json=audit_metadata,
        )

        # 4. Atomic Commit: Document + Job + AuditEvent
        session.commit()

    except AuditServiceError as exc:
        # Strict fail-closed stance (PD-012): transaction rolls back if audit fails
        session.rollback()
        logger.error("Audit recording failed during document upload; failing closed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transaction failed closed: Audit event could not be secured.",
        ) from exc
    except Exception as exc:
        session.rollback()
        logger.error("Document upload transaction aborted: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document upload processing failed: {exc}",
        ) from exc

    status_val = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
    return {
        "id": str(doc.id),
        "sha256": doc.sha256,
        "original_filename": doc.original_filename,
        "mime_type": doc.mime_type,
        "file_size_bytes": doc.file_size_bytes,
        "page_count": doc.page_count,
        "status": status_val,
        "uploaded_by": str(doc.uploaded_by),
        "uploaded_at": doc.uploaded_at.isoformat(),
        "job_id": str(job.id) if job else None,
        "duplicate_of": str(doc.duplicate_of) if doc.duplicate_of else None,
        "patient_context_id": doc.patient_context_id,
    }


@router.get("/{document_id}", status_code=status.HTTP_200_OK)
def get_document(
    document_id: uuid.UUID,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    audit_service: AuditService = Depends(get_audit_service),
):
    """Retrieve document metadata under strict object-level authorization (PD-009).

    Rules:
      1. Resolves Document by primary key; returns 404 if missing.
      2. Applies object-level authorization:
         - system_admin receives HTTP 403 (PHI isolation).
         - document_operator may only access their own uploaded documents.
         - clinical_reviewer and approver may access active documents.
         - auditor and integration_service receive HTTP 403.
      3. Records audit event for document content/metadata access.
      4. Returns contract-supported metadata (strictly no raw storage ciphertext).
    """
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
    source_ip = request.client.host if request.client else None

    doc = session.query(Document).filter(Document.id == document_id).first()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID '{document_id}' was not found.",
        )

    # Enforce object-level access control (PD-009, PRODUCTIZATION_SPEC §1.2)
    authorize_document_access(doc, current_user, action="read")

    # Record read audit event
    status_val = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
    id_verif_val = (
        doc.identity_verification_status.value
        if hasattr(doc.identity_verification_status, "value")
        else str(doc.identity_verification_status)
    )

    try:
        audit_service.record_event(
            session=session,
            actor_id=str(current_user.user_id),
            event_type="DOCUMENT_ACCESSED",
            entity_type="document",
            entity_id=str(doc.id),
            action="READ",
            source_ip=source_ip,
            correlation_id=correlation_id,
            metadata_json={
                "status": status_val,
                "file_size_bytes": doc.file_size_bytes,
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("Audit log failed for document access: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transaction failed closed: Audit event could not be secured.",
        ) from exc

    return {
        "id": str(doc.id),
        "sha256": doc.sha256,
        "original_filename": doc.original_filename,
        "mime_type": doc.mime_type,
        "file_size_bytes": doc.file_size_bytes,
        "page_count": doc.page_count,
        "status": status_val,
        "source_type": doc.source_type,
        "uploaded_by": str(doc.uploaded_by),
        "uploaded_at": doc.uploaded_at.isoformat(),
        "patient_context_id": doc.patient_context_id,
        "identity_verification_status": id_verif_val,
        "duplicate_of": str(doc.duplicate_of) if doc.duplicate_of else None,
    }
