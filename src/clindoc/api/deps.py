"""FastAPI Dependency Providers for Clindoc API Services.

Authority:
  - PRODUCTIZATION_SPEC.md §9.2 (API and Backend Architecture)
  - PRODUCTIZATION_DECISIONS.md PD-001, PD-007, PD-012
  - PRODUCTIZATION_DECISIONS.md Phase 8.7 Step 3 (Model Health Registry)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Generator, Optional

from fastapi import Depends
from sqlalchemy.orm import Session

from clindoc.api.audit.service import AuditService
from clindoc.api.auth.dependencies import (
    get_current_user,
    get_db_session,
    get_token_validator,
    set_global_session_factory,
    set_global_token_validator,
)
from clindoc.ingest.service import DocumentIngestionService
from clindoc.jobs.queue import JobQueue
from clindoc.review.approval import DocumentApprovalEngine
from clindoc.review.service import ClaimReviewService
from clindoc.review.triage import TriageEngine
from clindoc.storage.blob import BlobStore, EncryptedFileBlobStore

# Global service singletons / test override hooks
_GLOBAL_BLOB_STORE: Optional[BlobStore] = None
_GLOBAL_AUDIT_SERVICE: Optional[AuditService] = None
_GLOBAL_JOB_QUEUE: Optional[JobQueue] = None
_GLOBAL_CLAIM_REVIEW_SERVICE: Optional[ClaimReviewService] = None
_GLOBAL_APPROVAL_ENGINE: Optional[DocumentApprovalEngine] = None
_GLOBAL_TRIAGE_ENGINE: Optional[TriageEngine] = None
_GLOBAL_MODEL_HEALTH_REGISTRY = None  # ModelHealthRegistry singleton (lazy import)


def set_global_blob_store(store: Optional[BlobStore]) -> None:
    """Set the application-wide BlobStore instance (for testing/lifecycle)."""
    global _GLOBAL_BLOB_STORE
    _GLOBAL_BLOB_STORE = store


def set_global_audit_service(service: Optional[AuditService]) -> None:
    """Set the application-wide AuditService instance (for testing/lifecycle)."""
    global _GLOBAL_AUDIT_SERVICE
    _GLOBAL_AUDIT_SERVICE = service


def set_global_job_queue(queue: Optional[JobQueue]) -> None:
    """Set the application-wide JobQueue instance (for testing/lifecycle)."""
    global _GLOBAL_JOB_QUEUE
    _GLOBAL_JOB_QUEUE = queue


def set_global_claim_review_service(service: Optional[ClaimReviewService]) -> None:
    """Set the application-wide ClaimReviewService instance (for testing/lifecycle)."""
    global _GLOBAL_CLAIM_REVIEW_SERVICE
    _GLOBAL_CLAIM_REVIEW_SERVICE = service


def set_global_approval_engine(engine: Optional[DocumentApprovalEngine]) -> None:
    """Set the application-wide DocumentApprovalEngine instance (for testing/lifecycle)."""
    global _GLOBAL_APPROVAL_ENGINE
    _GLOBAL_APPROVAL_ENGINE = engine


def set_global_triage_engine(engine: Optional[TriageEngine]) -> None:
    """Set the application-wide TriageEngine instance (for testing/lifecycle)."""
    global _GLOBAL_TRIAGE_ENGINE
    _GLOBAL_TRIAGE_ENGINE = engine


def get_blob_store() -> BlobStore:
    """FastAPI dependency: Resolve the active BlobStore."""
    global _GLOBAL_BLOB_STORE
    if _GLOBAL_BLOB_STORE is not None:
        return _GLOBAL_BLOB_STORE
    root_dir = os.environ.get("CLINDOC_BLOB_ROOT_DIR", "data/blob_store")
    _GLOBAL_BLOB_STORE = EncryptedFileBlobStore(root_dir=root_dir)
    return _GLOBAL_BLOB_STORE


def get_audit_service() -> AuditService:
    """FastAPI dependency: Resolve the active AuditService."""
    global _GLOBAL_AUDIT_SERVICE
    if _GLOBAL_AUDIT_SERVICE is not None:
        return _GLOBAL_AUDIT_SERVICE
    _GLOBAL_AUDIT_SERVICE = AuditService()
    return _GLOBAL_AUDIT_SERVICE


def get_job_queue() -> JobQueue:
    """FastAPI dependency: Resolve the active JobQueue."""
    global _GLOBAL_JOB_QUEUE
    if _GLOBAL_JOB_QUEUE is not None:
        return _GLOBAL_JOB_QUEUE
    _GLOBAL_JOB_QUEUE = JobQueue()
    return _GLOBAL_JOB_QUEUE


def get_ingest_service(
    blob_store: BlobStore = Depends(get_blob_store),
) -> DocumentIngestionService:
    """FastAPI dependency: Resolve the DocumentIngestionService."""
    return DocumentIngestionService(blob_store=blob_store)


def get_claim_review_service(
    audit_service: AuditService = Depends(get_audit_service),
) -> ClaimReviewService:
    """FastAPI dependency: Resolve ClaimReviewService."""
    global _GLOBAL_CLAIM_REVIEW_SERVICE
    if _GLOBAL_CLAIM_REVIEW_SERVICE is not None:
        return _GLOBAL_CLAIM_REVIEW_SERVICE
    return ClaimReviewService(audit_service=audit_service)


def get_approval_engine(
    audit_service: AuditService = Depends(get_audit_service),
) -> DocumentApprovalEngine:
    """FastAPI dependency: Resolve DocumentApprovalEngine."""
    global _GLOBAL_APPROVAL_ENGINE
    if _GLOBAL_APPROVAL_ENGINE is not None:
        return _GLOBAL_APPROVAL_ENGINE
    return DocumentApprovalEngine(audit_service=audit_service)


def get_triage_engine(
    session: Session = Depends(get_db_session),
) -> TriageEngine:
    """FastAPI dependency: Resolve TriageEngine using the active TriagePolicy."""
    global _GLOBAL_TRIAGE_ENGINE
    if _GLOBAL_TRIAGE_ENGINE is not None:
        return _GLOBAL_TRIAGE_ENGINE
    from clindoc.db.models import TriagePolicy
    active_policy = (
        session.query(TriagePolicy)
        .filter(TriagePolicy.is_active == True)
        .order_by(TriagePolicy.created_at.desc())
        .first()
    )
    return TriageEngine(policy=active_policy)


def get_model_health_registry():
    """FastAPI dependency: Resolve the active ModelHealthRegistry."""
    global _GLOBAL_MODEL_HEALTH_REGISTRY
    if _GLOBAL_MODEL_HEALTH_REGISTRY is not None:
        return _GLOBAL_MODEL_HEALTH_REGISTRY
    from clindoc.services.model_health import get_model_health_registry as _get
    return _get()


def set_global_model_health_registry(registry) -> None:
    """Override the application-wide ModelHealthRegistry (for testing/lifecycle)."""
    global _GLOBAL_MODEL_HEALTH_REGISTRY
    _GLOBAL_MODEL_HEALTH_REGISTRY = registry


_GLOBAL_FHIR_EXPORT_SERVICE = None


def get_fhir_export_service(
    audit_service: AuditService = Depends(get_audit_service),
):
    """FastAPI dependency: Resolve FHIRExportService."""
    global _GLOBAL_FHIR_EXPORT_SERVICE
    if _GLOBAL_FHIR_EXPORT_SERVICE is not None:
        return _GLOBAL_FHIR_EXPORT_SERVICE
    from clindoc.services.fhir_export import FHIRExportService
    return FHIRExportService(audit_service=audit_service)


def set_global_fhir_export_service(service) -> None:
    """Override application-wide FHIRExportService (for testing/lifecycle)."""
    global _GLOBAL_FHIR_EXPORT_SERVICE
    _GLOBAL_FHIR_EXPORT_SERVICE = service


_GLOBAL_EHR_INTEGRATION_SERVICE = None


def get_ehr_integration_service(
    audit_service: AuditService = Depends(get_audit_service),
    export_service = Depends(get_fhir_export_service),
):
    """FastAPI dependency: Resolve EHRIntegrationService."""
    global _GLOBAL_EHR_INTEGRATION_SERVICE
    if _GLOBAL_EHR_INTEGRATION_SERVICE is not None:
        return _GLOBAL_EHR_INTEGRATION_SERVICE
    from clindoc.services.ehr_integration import EHRIntegrationService
    return EHRIntegrationService(audit_service=audit_service, export_service=export_service)


def set_global_ehr_integration_service(service) -> None:
    """Override application-wide EHRIntegrationService (for testing/lifecycle)."""
    global _GLOBAL_EHR_INTEGRATION_SERVICE
    _GLOBAL_EHR_INTEGRATION_SERVICE = service


