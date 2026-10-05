"""Job Status and Operational Oversight Endpoints (PRODUCTIZATION_SPEC §9.3).

Authority:
  - PRODUCTIZATION_SPEC.md §1.2, §9.3
  - PRODUCTIZATION_DECISIONS.md PD-009, PD-012

Endpoints:
  - GET /api/v1/jobs/{id}: Retrieve background processing job state under object-level authorization.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from clindoc.api.audit.service import AuditService
from clindoc.api.auth.dependencies import get_current_user, get_db_session
from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.auth.object_access import authorize_job_access
from clindoc.api.deps import get_audit_service
from clindoc.db.models import ProcessingJob

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get("/{job_id}", status_code=status.HTTP_200_OK)
def get_job(
    job_id: uuid.UUID,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    audit_service: AuditService = Depends(get_audit_service),
):
    """Retrieve background processing job status under object-level authorization.

    Rules:
      1. Resolves ProcessingJob by primary key; returns 404 if not found.
      2. Enforces object-level scoping:
         - system_admin: permitted (operational queue oversight).
         - document_operator: permitted ONLY for jobs associated with their own uploaded documents.
         - clinical_reviewer, approver, auditor: permitted.
         - integration_service: denied (HTTP 403).
      3. Returns operational information only; strictly NO PHI, claim values, OCR text, or evidence.
      4. Records an audit event for job inspection.
    """
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
    source_ip = request.client.host if request.client else None

    job = session.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Processing job with ID '{job_id}' was not found.",
        )

    # Enforce object-level job access control (PD-009)
    authorize_job_access(job, current_user)

    status_val = job.status.value if hasattr(job.status, "value") else str(job.status)

    # Record read audit event
    try:
        audit_service.record_event(
            session=session,
            actor_id=str(current_user.user_id),
            event_type="JOB_ACCESSED",
            entity_type="processing_job",
            entity_id=str(job.id),
            action="READ",
            source_ip=source_ip,
            correlation_id=correlation_id,
            metadata_json={
                "status": status_val,
                "current_stage": job.current_stage,
                "attempts": job.attempts,
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("Audit log failed for job access: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transaction failed closed: Audit event could not be secured.",
        ) from exc

    return {
        "id": str(job.id),
        "document_id": str(job.document_id),
        "status": status_val,
        "current_stage": job.current_stage,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "error_code": job.error_code,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
