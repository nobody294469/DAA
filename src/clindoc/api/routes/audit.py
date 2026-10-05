"""Audit Trail Inspection Endpoints (PRODUCTIZATION_SPEC §9.3, §11.3).

Authority:
  - PRODUCTIZATION_SPEC.md §1.2, §4.3, §9.3, §11.3
  - PRODUCTIZATION_DECISIONS.md PD-009, PD-012

Endpoints:
  - GET /api/v1/audit/events: Query immutable cryptographic audit trail under auditor/admin authorization.
"""

from __future__ import annotations

import logging
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from clindoc.api.audit.service import AuditService
from clindoc.api.auth.dependencies import get_db_session
from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.auth.rbac import require_audit_access
from clindoc.api.deps import get_audit_service
from clindoc.db.models import AuditEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/events", status_code=status.HTTP_200_OK)
def get_audit_events(
    request: Request,
    entity_type: Optional[str] = Query(None, description="Filter by target entity type (e.g. document, job)"),
    entity_id: Optional[str] = Query(None, description="Filter by target entity identifier"),
    limit: int = Query(50, ge=1, le=500, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
    current_user: AuthenticatedUser = Depends(require_audit_access),
    session: Session = Depends(get_db_session),
    audit_service: AuditService = Depends(get_audit_service),
):
    """Retrieve immutable audit events from the tamper-evident hash chain.

    Rules:
      1. Strictly requires auditor or system_admin authorization (HTTP 403 for others).
      2. Strictly read-only; no modification or deletion endpoints exist.
      3. Guaranteed PHI-free by schema constraints and audit service validation.
      4. Audits the audit-log access itself.
      5. Results ordered strictly by sequence_number ascending.
    """
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
    source_ip = request.client.host if request.client else None

    # Base query
    query = session.query(AuditEvent)
    if entity_type:
        query = query.filter(AuditEvent.entity_type == entity_type.strip())
    if entity_id:
        query = query.filter(AuditEvent.entity_id == entity_id.strip())

    total_count = query.count()
    events = (
        query.order_by(AuditEvent.sequence_number.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Record audit log access event
    try:
        audit_service.record_event(
            session=session,
            actor_id=str(current_user.user_id),
            event_type="AUDIT_LOG_ACCESSED",
            entity_type="audit_events",
            entity_id="all",
            action="READ",
            source_ip=source_ip,
            correlation_id=correlation_id,
            metadata_json={
                "filter_entity_type": entity_type.strip() if entity_type else None,
                "filter_entity_id": entity_id.strip() if entity_id else None,
                "limit": limit,
                "offset": offset,
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("Audit log recording failed for audit access: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transaction failed closed: Audit event could not be secured.",
        ) from exc

    return {
        "items": [
            {
                "id": str(e.id),
                "sequence_number": e.sequence_number,
                "timestamp": e.timestamp.isoformat(),
                "actor_id": e.actor_id,
                "event_type": e.event_type,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "action": e.action,
                "source_ip": e.source_ip,
                "correlation_id": e.correlation_id,
                "metadata_json": e.metadata_json,
                "previous_event_hash": e.previous_event_hash,
                "current_event_hash": e.current_event_hash,
            }
            for e in events
        ],
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }
