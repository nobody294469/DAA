"""Document Ingestion and Retrieval Endpoints (PRODUCTIZATION_SPEC §9.3).

Authority:
  - PRODUCTIZATION_SPEC.md §1.2, §2 (W-01, W-02), §4.3, §7.3, §9.3
  - PRODUCTIZATION_DECISIONS.md PD-007, PD-009, PD-012, PD-013

Endpoints:
  - POST /api/v1/documents: Upload document, screen, store encrypted, enqueue job, audit.
  - GET  /api/v1/documents/{id}: Retrieve document metadata under object-level authorization.
  - GET  /api/v1/documents/{id}/file: Stream decrypted document binary for review viewer.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from clindoc.api.audit.service import AuditService, AuditServiceError
from clindoc.api.auth.dependencies import get_current_user, get_db_session
from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.auth.object_access import authorize_document_access
from clindoc.api.auth.rbac import require_export_access, require_role
from clindoc.api.auth.roles import CanonicalRole
from clindoc.api.deps import (
    get_audit_service,
    get_blob_store,
    get_ehr_integration_service,
    get_fhir_export_service,
    get_ingest_service,
    get_job_queue,
)
from clindoc.db.enums import DocumentStatus
from clindoc.db.models import Document, FHIRBundle
from clindoc.ingest.service import DocumentIngestionService
from clindoc.jobs.queue import JobQueue
from clindoc.services.ehr_integration import (
    EHRApprovalMissingError,
    EHRDuplicateSubmissionError,
    EHRIntegrationError,
    EHRIntegrationService,
    EHRPatientIdentityError,
    EHRUnresolvedClaimError,
)
from clindoc.services.ehr_transport import (
    EHRAuthenticationError,
    EHRDraftRejectionError,
    EHRTimeoutError,
    EHRTransportError,
    EHRValidationError,
)
from clindoc.services.fhir_export import (
    FHIRExportEligibilityError,
    FHIRExportError,
    FHIRExportNotFoundError,
    FHIRExportService,
    FHIRExportValidationError,
)
from clindoc.storage.blob import BlobNotFoundError, BlobStorageError, BlobStore, DecryptionError


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
            detail="Document upload processing failed.",
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


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: uuid.UUID,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    blob_store: BlobStore = Depends(get_blob_store),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response:
    """Retrieve and stream decrypted document binary for authenticated review viewer (PD-013).

    Authority:
      - PRODUCTIZATION_SPEC.md §7.3, §9.3 (GET /documents/{id}/file)
      - PRODUCTIZATION_DECISIONS.md PD-007 (Encrypted blob storage)
      - PRODUCTIZATION_DECISIONS.md PD-009 (RBAC object authorization)
      - PRODUCTIZATION_DECISIONS.md PD-012 (Fail-closed audit logging)
      - PRODUCTIZATION_DECISIONS.md PD-013 (High-DPI PDF.js / SVG viewer)

    Security rules:
      1. Object-level authorization enforced:
         - system_admin strictly denied (HTTP 403) to prevent PHI exposure.
         - document_operator may only download documents they uploaded.
         - clinical_reviewer and approver may access active documents for review.
         - auditor and integration_service receive HTTP 403.
      2. Retrieves and decrypts ciphertext via authenticated AES-256-GCM.
      3. Records fail-closed audit event for document download.
      4. Returns decrypted payload with Content-Disposition inline and verified MIME type.
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

    # Retrieve and decrypt raw payload from encrypted blob store
    try:
        payload_bytes = blob_store.get(doc.storage_ref)
    except BlobNotFoundError as exc:
        logger.error("Blob not found for document %s at storage_ref %s", doc.id, doc.storage_ref)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document payload for ID '{document_id}' was not found in storage.",
        ) from exc
    except DecryptionError as exc:
        logger.error("Failed to decrypt blob for document %s: %s", doc.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document content decryption or integrity verification failed.",
        ) from exc
    except BlobStorageError as exc:
        logger.error("Blob storage error for document %s: %s", doc.id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unsafe document storage reference.",
        ) from exc

    # Record download audit event (PD-012: fail closed if audit fails)
    status_val = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
    try:
        audit_service.record_event(
            session=session,
            actor_id=str(current_user.user_id),
            event_type="DOCUMENT_FILE_DOWNLOADED",
            entity_type="document",
            entity_id=str(doc.id),
            action="DOWNLOAD",
            source_ip=source_ip,
            correlation_id=correlation_id,
            metadata_json={
                "status": status_val,
                "file_size_bytes": len(payload_bytes),
                "mime_type": doc.mime_type,
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("Audit log failed for document file download: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transaction failed closed: Audit event could not be secured.",
        ) from exc

    safe_filename = Path(doc.original_filename).name or "document.bin"
    return Response(
        content=payload_bytes,
        media_type=doc.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{safe_filename}"',
            "Content-Length": str(len(payload_bytes)),
        },
    )


@router.post("/{document_id}/export", status_code=status.HTTP_200_OK)
async def export_document_fhir(
    request: Request,
    document_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(require_export_access),
    session: Session = Depends(get_db_session),
    export_service: FHIRExportService = Depends(get_fhir_export_service),
    blob_store: BlobStore = Depends(get_blob_store),
):
    """Export an approved document into a validated FHIR R4B Bundle (Phase 8.8).

    Enforces:
      - RBAC: integration_service or approver role required.
      - Four-Eyes check: Document must have is_export_eligible=True.
      - Safety boundary: Only verified/accepted claims are exported.
      - Audit trail: Emits hash-chained FHIR_BUNDLE_EXPORTED audit event.
    """
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
    source_ip = request.client.host if request.client else None

    try:
        result = export_service.export_document(
            session=session,
            document_id=document_id,
            exported_by=current_user.user_id,
            blob_store=blob_store,
            source_ip=source_ip,
            correlation_id=correlation_id,
        )
        session.commit()
        return result.to_dict()
    except FHIRExportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FHIRExportEligibilityError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except FHIRExportValidationError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except FHIRExportError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get("/{document_id}/fhir-bundle", status_code=status.HTTP_200_OK)
async def get_document_fhir_bundle(
    document_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(require_export_access),
    session: Session = Depends(get_db_session),
    blob_store: BlobStore = Depends(get_blob_store),
):
    """Retrieve the finalized, approved FHIR R4B Bundle for a document (Phase 8.8)."""
    bundle_row = (
        session.query(FHIRBundle)
        .filter(FHIRBundle.document_id == document_id)
        .order_by(FHIRBundle.created_at.desc())
        .first()
    )
    if not bundle_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No exported FHIR bundle found for document '{document_id}'.",
        )

    bundle_dict = bundle_row.bundle_payload
    if not bundle_dict:
        try:
            raw_bytes = blob_store.get(bundle_row.storage_ref)
            import json as _json
            bundle_dict = _json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            logger.error("Failed to retrieve FHIR bundle from storage for doc %s: %s", document_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve FHIR bundle payload from storage.",
            ) from exc

    # Stage 3.4 Complete Attestation Gateway Check:
    # If the bundle carries an audit provenance extension or an audit ledger is active,
    # verify complete attestation before release!
    ledger_path_env = os.getenv("CLINDOC_AUDIT_LEDGER_PATH")
    if ledger_path_env and os.path.exists(ledger_path_env):
        from clindoc.audit.projection import AuditProjectionEngine
        from clindoc.fhir.signing import IncompleteAttestationError, BundleSigningError

        try:
            exact_bytes, transport_sha256 = AuditProjectionEngine.verify_bundle_complete_attestation(
                session=session,
                bundle_dict=bundle_dict,
                ledger_path=ledger_path_env,
            )
            return Response(
                content=exact_bytes,
                media_type="application/fhir+json",
                headers={"X-Transport-SHA256": transport_sha256},
            )
        except IncompleteAttestationError as exc:
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail=f"Incomplete Attestation: {exc}",
            ) from exc
        except BundleSigningError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Attestation Verification Failure: {exc}",
            ) from exc

    return bundle_dict


@router.post("/{document_id}/transmit-ehr", status_code=status.HTTP_200_OK)
async def transmit_document_to_ehr(
    request: Request,
    document_id: uuid.UUID,
    destination_type: str = Query("sandbox", pattern="^(sandbox|mock|staging|production)$"),
    current_user: AuthenticatedUser = Depends(require_export_access),
    session: Session = Depends(get_db_session),
    ehr_service: EHRIntegrationService = Depends(get_ehr_integration_service),
):
    """Deliver an approved, verified document to the EHR transmission boundary (Phase 8.12).

    Enforces:
      - RBAC: integration_service or approver role required.
      - Four-Eyes check: Document must be APPROVED and is_export_eligible=True.
      - Patient identity: Patient identity must be CONFIRMED.
      - Safety boundary: Fallback or unverified claims are strictly blocked.
      - Idempotency: Duplicate transmissions are detected and rejected.
      - Audit trail: Emits hash-chained EHR_TRANSMISSION_* audit events.
    """
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
    try:
        result = ehr_service.transmit_document_to_ehr(
            session=session,
            document_id=document_id,
            actor_id=current_user.user_id,
            destination_type=destination_type,
            correlation_id=correlation_id,
        )
        session.commit()
        return result.to_dict()
    except FHIRExportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (EHRApprovalMissingError, EHRPatientIdentityError, FHIRExportEligibilityError, EHRUnresolvedClaimError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except EHRDuplicateSubmissionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EHRDraftRejectionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except EHRTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except EHRAuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except EHRValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except (EHRTransportError, EHRIntegrationError, FHIRExportError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc



