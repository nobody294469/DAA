"""Review, Triage, and Approval API Endpoints (PRODUCTIZATION_SPEC §7.3, §9.3).

Authority:
  - PRODUCTIZATION_SPEC.md §1.2 (Role separation, clinical_reviewer vs approver)
  - PRODUCTIZATION_SPEC.md §4.3 (claims, review_actions, document_approvals)
  - PRODUCTIZATION_SPEC.md §7.2 (Triage policy engine, review queue priority)
  - PRODUCTIZATION_SPEC.md §7.3 (Reviewer actions, Four-Eyes approval gate)
  - PRODUCTIZATION_SPEC.md §9.3 (API endpoints under /api/v1)
  - PRODUCTIZATION_DECISIONS.md PD-002 (Mode 0: 100% human review prior to export)
  - PRODUCTIZATION_DECISIONS.md PD-009 (RBAC, Four-Eyes principle, system_admin PHI denial)
  - PRODUCTIZATION_DECISIONS.md PD-010 (Immutable claim history: edits create new versions)
  - PRODUCTIZATION_DECISIONS.md PD-012 (Hash-chained audit logging, fail-closed atomic)
  - PRODUCTIZATION_DECISIONS.md PD-015 (Explicit human patient identity confirmation)

Endpoints:
  - GET  /api/v1/review/queue
  - GET  /api/v1/documents/{document_id}/claims
  - POST /api/v1/claims/{claim_id}/actions
  - POST /api/v1/documents/{document_id}/identity-confirmation
  - POST /api/v1/documents/{document_id}/approval
  - GET  /api/v1/documents/{document_id}/review-summary (supporting review task context)
"""

from __future__ import annotations

from enum import Enum
import logging
from typing import Any, List, Optional
import uuid

from clindoc.observability.metrics import get_metrics

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from clindoc.api.auth.dependencies import get_current_user, get_db_session
from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.auth.object_access import (
    authorize_claim_access,
    authorize_document_access,
)
from clindoc.api.auth.rbac import deny_system_admin_phi, require_role
from clindoc.api.auth.roles import CanonicalRole
from clindoc.api.deps import (
    get_approval_engine,
    get_claim_review_service,
    get_triage_engine,
)
from clindoc.db.enums import (
    ClaimReviewState,
    DocumentApprovalStatus,
    DocumentStatus,
    RetentionState,
    ReviewActionType,
)
from clindoc.db.models import (
    Claim,
    Document,
    DocumentApproval,
    Evidence,
    ReviewAction,
    VerificationResult,
)
from clindoc.review.approval import (
    AuditIntegrationError as ApprovalAuditError,
    DocumentApprovalEngine,
    DocumentNotFoundError,
    FourEyesViolationError,
    IdentityMismatchError,
    InvalidDocumentApprovalStateError,
    MissingReasonCodeError as ApprovalMissingReasonError,
    PatientDemographics,
    UnconfirmedIdentityError,
    UnreviewedClaimsError,
)
from clindoc.review.service import (
    AuditIntegrationError as ClaimAuditError,
    ClaimNotFoundError,
    ClaimReviewService,
    InvalidStateTransitionError,
    MissingReasonCodeError as ClaimMissingReasonError,
    OptimisticLockError,
)
from clindoc.review.triage import ClaimTriageInput, TriageEngine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Review"])


# ─── PYDANTIC SCHEMAS ─────────────────────────────────────────────────────────

class ClaimActionType(str, Enum):
    ACCEPT = "ACCEPT"
    EDIT = "EDIT"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"
    REOPEN = "REOPEN"


class ClaimActionRequest(BaseModel):
    action: ClaimActionType
    expected_version: int
    reason_code: Optional[str] = None
    notes: Optional[str] = None
    new_value: Optional[str] = None
    new_normalized_code: Optional[str] = None
    new_code_system: Optional[str] = None
    page_text: Optional[str] = None


class IdentityConfirmationAction(str, Enum):
    CONFIRMED = "CONFIRMED"
    MISMATCH_ACCEPTED = "MISMATCH_ACCEPTED"


class DemographicsModel(BaseModel):
    name: Optional[str] = None
    mrn: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None


class IdentityConfirmationRequest(BaseModel):
    action: IdentityConfirmationAction
    reason_code: Optional[str] = None
    notes: Optional[str] = None
    context_demographics: Optional[DemographicsModel] = None
    extracted_demographics: Optional[DemographicsModel] = None


class DocumentApprovalRequest(BaseModel):
    identity_notes: Optional[str] = None


# ─── 1. REVIEW QUEUE ─────────────────────────────────────────────────────────

@router.get("/review/queue", status_code=status.HTTP_200_OK)
def get_review_queue(
    request: Request,
    status_filter: Optional[str] = Query(None, description="Filter by document status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: AuthenticatedUser = Depends(
        require_role(CanonicalRole.CLINICAL_REVIEWER, CanonicalRole.APPROVER)
    ),
    session: Session = Depends(get_db_session),
    triage_engine: TriageEngine = Depends(get_triage_engine),
):
    """Retrieve review queue containing documents and claims ordered by policy risk priority.

    Rules:
      - Only clinical_reviewer and approver can access the review queue.
      - System administrators are strictly denied PHI access (HTTP 403).
      - Returns only active, non-purged documents in review scope.
      - Claims within each document are ordered deterministically by policy risk rank (highest risk first).
      - No raw ciphertext or unnecessary PHI is exposed.
      - Read-only: no distributed locking or leases taken in this step.
    """
    deny_system_admin_phi(current_user)

    query = session.query(Document).filter(
        Document.deleted_at.is_(None),
        Document.retention_state != RetentionState.PURGED,
    )
    if status_filter:
        query = query.filter(Document.status == status_filter)
    else:
        query = query.filter(
            Document.status.in_([
                DocumentStatus.PENDING_REVIEW,
                DocumentStatus.IN_REVIEW,
            ])
        )

    documents = query.order_by(Document.uploaded_at.asc()).all()

    queue_items = []
    for doc in documents:
        # Enforce document object access
        try:
            authorize_document_access(doc, current_user)
        except HTTPException:
            continue

        claims = session.query(Claim).filter(Claim.document_id == doc.id).all()
        claim_ids = [c.id for c in claims]

        v_results = (
            session.query(VerificationResult)
            .filter(VerificationResult.claim_id.in_(claim_ids))
            .all()
            if claim_ids
            else []
        )
        v_map = {vr.claim_id: vr for vr in v_results}

        ev_ids = [c.evidence_id for c in claims if c.evidence_id]
        ev_records = (
            session.query(Evidence).filter(Evidence.id.in_(ev_ids)).all()
            if ev_ids
            else []
        )
        e_map = {e.id: e for e in ev_records}

        triage_summary = triage_engine.triage_document_claims(
            doc,
            claims,
            verification_results=v_map,
            evidence_map=e_map,
        )

        highest_risk_rank = (
            triage_summary.sorted_claims[0].priority_rank
            if triage_summary.sorted_claims
            else 9
        )

        status_str = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
        id_status_str = (
            doc.identity_verification_status.value
            if hasattr(doc.identity_verification_status, "value")
            else str(doc.identity_verification_status)
        )

        queue_items.append({
            "document_id": str(doc.id),
            "original_filename": doc.original_filename,
            "status": status_str,
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            "patient_context_id": doc.patient_context_id,
            "identity_verification_status": id_status_str,
            "total_claims": triage_summary.total_claims,
            "auto_processed_count": triage_summary.auto_processed_count,
            "human_review_required_count": triage_summary.human_review_required_count,
            "discrepancy_count": triage_summary.discrepancy_count,
            "highest_risk_rank": highest_risk_rank,
            "can_bulk_confirm_agreed": triage_summary.can_bulk_confirm_agreed,
            "claims": [
                {
                    "claim_id": str(sc.claim_id),
                    "field_type": sc.field_type,
                    "category": sc.category.value if hasattr(sc.category, "value") else str(sc.category),
                    "risk_tier": sc.risk_tier.value if hasattr(sc.risk_tier, "value") else str(sc.risk_tier),
                    "priority_rank": sc.priority_rank,
                    "primary_reason": sc.primary_reason,
                    "reasons": sc.reasons,
                    "is_bulk_confirmable": sc.is_bulk_confirmable,
                    "confidence": sc.confidence,
                }
                for sc in triage_summary.sorted_claims
            ],
            "_sort_key": (highest_risk_rank, doc.uploaded_at),
        })

    # Sort documents by highest risk rank first (1 = critical), then FIFO by uploaded_at
    queue_items.sort(key=lambda x: x["_sort_key"])
    for item in queue_items:
        del item["_sort_key"]

    paged_items = queue_items[offset : offset + limit]

    return {
        "total": len(queue_items),
        "limit": limit,
        "offset": offset,
        "items": paged_items,
    }


# ─── 2. CLAIM RETRIEVAL ──────────────────────────────────────────────────────

@router.get("/documents/{document_id}/claims", status_code=status.HTTP_200_OK)
def get_document_claims(
    document_id: uuid.UUID,
    request: Request,
    include_superseded: bool = Query(False, description="Whether to include historical superseded versions"),
    active_only: bool = Query(False, description="Filter to pending claims only"),
    current_user: AuthenticatedUser = Depends(
        require_role(CanonicalRole.CLINICAL_REVIEWER, CanonicalRole.APPROVER)
    ),
    session: Session = Depends(get_db_session),
    triage_engine: TriageEngine = Depends(get_triage_engine),
):
    """Retrieve claims for a document preserving lineage and review status.

    Rules:
      - Only clinical_reviewer and approver can access clinical claims.
      - System administrators strictly denied (HTTP 403).
      - Object-level authorization enforced on Document.
      - Preserves claim lineage (version, supersedes_claim_id, origin).
      - Strictly does not expose internal secrets or raw encrypted blob storage keys.
    """
    deny_system_admin_phi(current_user)

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    authorize_document_access(doc, current_user)

    all_claims = (
        session.query(Claim)
        .filter(Claim.document_id == document_id)
        .order_by(Claim.claim_id, Claim.version.desc())
        .all()
    )

    if not include_superseded:
        # Keep only the latest version for each distinct stable claim_id
        seen: set[uuid.UUID] = set()
        latest_claims = []
        for c in all_claims:
            if c.claim_id not in seen:
                seen.add(c.claim_id)
                latest_claims.append(c)
        target_claims = latest_claims
    else:
        target_claims = all_claims

    if active_only:
        target_claims = [
            c for c in target_claims
            if (c.review_state.value if hasattr(c.review_state, "value") else str(c.review_state))
            == ClaimReviewState.PENDING.value
        ]

    claim_ids = [c.id for c in target_claims]
    v_results = (
        session.query(VerificationResult)
        .filter(VerificationResult.claim_id.in_(claim_ids))
        .all()
        if claim_ids
        else []
    )
    v_map = {vr.claim_id: vr for vr in v_results}

    ev_ids = [c.evidence_id for c in target_claims if c.evidence_id]
    ev_records = (
        session.query(Evidence).filter(Evidence.id.in_(ev_ids)).all()
        if ev_ids
        else []
    )
    e_map = {e.id: e for e in ev_records}

    claims_output = []
    for c in target_claims:
        v_res = v_map.get(c.id)
        ev = e_map.get(c.evidence_id) if c.evidence_id else None

        triage_in = ClaimTriageInput.from_orm(c, verification_result=v_res, evidence=ev)
        t_res = triage_engine.classify_claim(triage_in)

        review_state_str = c.review_state.value if hasattr(c.review_state, "value") else str(c.review_state)
        origin_str = c.origin.value if hasattr(c.origin, "value") else str(c.origin)

        claims_output.append({
            "id": str(c.id),
            "claim_id": str(c.claim_id),
            "document_id": str(c.document_id),
            "group_id": c.group_id,
            "field_type": c.field_type,
            "value": c.value,
            "normalized_code": c.normalized_code,
            "code_system": c.code_system,
            "version": c.version,
            "supersedes_claim_id": str(c.supersedes_claim_id) if c.supersedes_claim_id else None,
            "origin": origin_str,
            "review_state": review_state_str,
            "created_by": str(c.created_by) if c.created_by else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "evidence_id": str(c.evidence_id) if c.evidence_id else None,
            "grounding": {
                "page_number": ev.page_number,
                "bounding_box": ev.bounding_box,
                "ocr_confidence": ev.ocr_confidence,
                "text_snippet": ev.evidence_text,
            } if ev else None,
            "verification": {
                "label": v_res.label.value if hasattr(v_res.label, "value") else str(v_res.label),
                "stage": (
                    v_res.verification_stage.value
                    if hasattr(v_res.verification_stage, "value")
                    else str(v_res.verification_stage)
                ) if getattr(v_res, "verification_stage", None) else None,
                "calibrated_confidence": v_res.calibrated_confidence,
                "contradiction_type": (
                    v_res.contradiction_type.value
                    if hasattr(v_res.contradiction_type, "value")
                    else str(v_res.contradiction_type)
                ) if v_res.contradiction_type else None,
            } if v_res else None,
            "triage": {
                "category": t_res.category.value if hasattr(t_res.category, "value") else str(t_res.category),
                "risk_tier": t_res.risk_tier.value if hasattr(t_res.risk_tier, "value") else str(t_res.risk_tier),
                "priority_rank": t_res.priority_rank,
                "primary_reason": t_res.primary_reason,
                "is_bulk_confirmable": t_res.is_bulk_confirmable,
            },
        })

    return {
        "document_id": str(doc.id),
        "total": len(claims_output),
        "claims": claims_output,
    }


# ─── 3. CLAIM ACTIONS ────────────────────────────────────────────────────────

@router.post("/claims/{claim_id}/actions", status_code=status.HTTP_200_OK)
def perform_claim_action(
    claim_id: uuid.UUID,
    payload: ClaimActionRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(
        require_role(CanonicalRole.CLINICAL_REVIEWER, CanonicalRole.APPROVER)
    ),
    session: Session = Depends(get_db_session),
    claim_review_service: ClaimReviewService = Depends(get_claim_review_service),
):
    """Execute human review action on a clinical claim.

    Actions:
      - ACCEPT: Validates PENDING state, records ReviewAction, audits.
      - EDIT: Validates new_value and reason_code, inserts new Claim version,
              runs inline re-verification, updates triage, records ReviewAction, audits.
      - REJECT: Validates reason_code, transitions to REJECTED, audits.
      - ESCALATE: Transitions to ESCALATED, audits.
      - REOPEN: Reopens ACCEPTED/REJECTED/ESCALATED claim back to PENDING.

    Errors:
      - 401 unauthenticated
      - 403 unauthorized
      - 404 claim or document not found
      - 409 state conflict or optimistic lock conflict
      - 422 missing reason code or required parameters
      - 500 audit failure or unexpected error
    """
    deny_system_admin_phi(current_user)

    # Resolve Claim to verify object authorization
    claim = session.get(Claim, claim_id)
    if claim is None:
        claim = (
            session.query(Claim)
            .filter(Claim.claim_id == claim_id)
            .order_by(Claim.version.desc())
            .first()
        )
    if claim is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Claim '{claim_id}' not found.",
        )

    # Enforce document and claim authorization
    doc = session.get(Document, claim.document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated document for claim not found.",
        )

    authorize_document_access(doc, current_user)
    authorize_claim_access(claim, current_user)

    # Validate action-specific payload constraints
    if payload.action == ClaimActionType.EDIT:
        if not payload.new_value or not str(payload.new_value).strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Field 'new_value' is required for EDIT action.",
            )
        if not payload.reason_code or not str(payload.reason_code).strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Field 'reason_code' is required for EDIT action.",
            )

    if payload.action == ClaimActionType.REJECT:
        if not payload.reason_code or not str(payload.reason_code).strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Field 'reason_code' is required for REJECT action.",
            )

    try:
        if payload.action == ClaimActionType.ACCEPT:
            updated = claim_review_service.accept_claim(
                session=session,
                claim_id=claim.id,
                actor_id=current_user.user_id,
                expected_version=payload.expected_version,
                reason_code=payload.reason_code,
                notes=payload.notes,
                commit=True,
            )
            get_metrics().record_review_action("accept", "success")
            return {
                "action": "ACCEPT",
                "claim_id": str(updated.claim_id),
                "row_id": str(updated.id),
                "version": updated.version,
                "review_state": updated.review_state.value if hasattr(updated.review_state, "value") else str(updated.review_state),
                "origin": updated.origin.value if hasattr(updated.origin, "value") else str(updated.origin),
            }

        elif payload.action == ClaimActionType.EDIT:
            edit_res = claim_review_service.edit_claim(
                session=session,
                claim_id=claim.id,
                actor_id=current_user.user_id,
                new_value=str(payload.new_value).strip(),
                reason_code=str(payload.reason_code).strip(),
                expected_version=payload.expected_version,
                new_normalized_code=payload.new_normalized_code,
                new_code_system=payload.new_code_system,
                notes=payload.notes,
                page_text=payload.page_text,
                commit=True,
            )
            new_c = edit_res.new_claim
            get_metrics().record_review_action("edit", "success")
            return {
                "action": "EDIT",
                "claim_id": str(new_c.claim_id),
                "row_id": str(new_c.id),
                "version": new_c.version,
                "previous_version": edit_res.previous_claim.version,
                "supersedes_claim_id": str(new_c.supersedes_claim_id),
                "review_state": new_c.review_state.value if hasattr(new_c.review_state, "value") else str(new_c.review_state),
                "origin": new_c.origin.value if hasattr(new_c.origin, "value") else str(new_c.origin),
                "verification_label": (
                    edit_res.verification_result.label.value
                    if hasattr(edit_res.verification_result.label, "value")
                    else str(edit_res.verification_result.label)
                ),
                "triage_category": (
                    edit_res.triage_result.category.value
                    if hasattr(edit_res.triage_result.category, "value")
                    else str(edit_res.triage_result.category)
                ),
                "triage_priority_rank": edit_res.triage_result.priority_rank,
            }

        elif payload.action == ClaimActionType.REJECT:
            updated = claim_review_service.reject_claim(
                session=session,
                claim_id=claim.id,
                actor_id=current_user.user_id,
                reason_code=str(payload.reason_code).strip(),
                expected_version=payload.expected_version,
                notes=payload.notes,
                commit=True,
            )
            get_metrics().record_review_action("reject", "success")
            return {
                "action": "REJECT",
                "claim_id": str(updated.claim_id),
                "row_id": str(updated.id),
                "version": updated.version,
                "review_state": updated.review_state.value if hasattr(updated.review_state, "value") else str(updated.review_state),
                "origin": updated.origin.value if hasattr(updated.origin, "value") else str(updated.origin),
            }

        elif payload.action == ClaimActionType.ESCALATE:
            updated = claim_review_service.escalate_claim(
                session=session,
                claim_id=claim.id,
                actor_id=current_user.user_id,
                expected_version=payload.expected_version,
                reason_code=payload.reason_code,
                notes=payload.notes,
                commit=True,
            )
            get_metrics().record_review_action("escalate", "success")
            return {
                "action": "ESCALATE",
                "claim_id": str(updated.claim_id),
                "row_id": str(updated.id),
                "version": updated.version,
                "review_state": updated.review_state.value if hasattr(updated.review_state, "value") else str(updated.review_state),
                "origin": updated.origin.value if hasattr(updated.origin, "value") else str(updated.origin),
            }

        elif payload.action == ClaimActionType.REOPEN:
            updated = claim_review_service.reopen_claim(
                session=session,
                claim_id=claim.id,
                actor_id=current_user.user_id,
                expected_version=payload.expected_version,
                reason_code=payload.reason_code,
                notes=payload.notes,
                commit=True,
            )
            get_metrics().record_review_action("reopen", "success")
            return {
                "action": "REOPEN",
                "claim_id": str(updated.claim_id),
                "row_id": str(updated.id),
                "version": updated.version,
                "review_state": updated.review_state.value if hasattr(updated.review_state, "value") else str(updated.review_state),
                "origin": updated.origin.value if hasattr(updated.origin, "value") else str(updated.origin),
            }

    except ClaimNotFoundError as exc:
        get_metrics().record_review_action(payload.action.value.lower(), "error")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OptimisticLockError as exc:
        get_metrics().record_review_action(payload.action.value.lower(), "error")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidStateTransitionError as exc:
        get_metrics().record_review_action(payload.action.value.lower(), "error")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ClaimMissingReasonError as exc:
        get_metrics().record_review_action(payload.action.value.lower(), "error")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ClaimAuditError as exc:
        get_metrics().record_review_action(payload.action.value.lower(), "error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transaction failed closed: Audit event could not be secured.",
        ) from exc
    except ValueError as exc:
        get_metrics().record_review_action(payload.action.value.lower(), "error")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc



# ─── 4. PATIENT IDENTITY CONFIRMATION ────────────────────────────────────────

@router.post("/documents/{document_id}/identity-confirmation", status_code=status.HTTP_200_OK)
def confirm_patient_identity(
    document_id: uuid.UUID,
    payload: IdentityConfirmationRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(
        require_role(CanonicalRole.CLINICAL_REVIEWER, CanonicalRole.APPROVER)
    ),
    session: Session = Depends(get_db_session),
    approval_engine: DocumentApprovalEngine = Depends(get_approval_engine),
):
    """Explicitly confirm patient identity or accept demographic mismatch with justification.

    Rules:
      - Explicit human action required (never automated).
      - Demographics mismatch prevents CONFIRMED status unless explicitly accepted as MISMATCH_ACCEPTED.
      - Requires clinical_reviewer or approver role and valid object authorization.
    """
    deny_system_admin_phi(current_user)

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    authorize_document_access(doc, current_user)

    try:
        if payload.action == IdentityConfirmationAction.CONFIRMED:
            context_demo = None
            if payload.context_demographics:
                context_demo = PatientDemographics(
                    name=payload.context_demographics.name,
                    mrn=payload.context_demographics.mrn,
                    dob=payload.context_demographics.dob,
                    gender=payload.context_demographics.gender,
                )

            extracted_demo = None
            if payload.extracted_demographics:
                extracted_demo = PatientDemographics(
                    name=payload.extracted_demographics.name,
                    mrn=payload.extracted_demographics.mrn,
                    dob=payload.extracted_demographics.dob,
                    gender=payload.extracted_demographics.gender,
                )

            updated = approval_engine.confirm_identity(
                session=session,
                document_id=doc.id,
                actor_id=current_user.user_id,
                context_demographics=context_demo,
                extracted_demographics=extracted_demo,
                notes=payload.notes,
                commit=True,
            )

        elif payload.action == IdentityConfirmationAction.MISMATCH_ACCEPTED:
            if not payload.reason_code or not str(payload.reason_code).strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Field 'reason_code' is required to accept an identity mismatch.",
                )

            updated = approval_engine.accept_identity_mismatch(
                session=session,
                document_id=doc.id,
                actor_id=current_user.user_id,
                reason_code=str(payload.reason_code).strip(),
                notes=payload.notes,
                commit=True,
            )

        id_status_str = (
            updated.identity_verification_status.value
            if hasattr(updated.identity_verification_status, "value")
            else str(updated.identity_verification_status)
        )

        return {
            "document_id": str(updated.id),
            "identity_verification_status": id_status_str,
            "identity_confirmed_by": str(updated.identity_confirmed_by) if updated.identity_confirmed_by else None,
            "identity_confirmed_at": updated.identity_confirmed_at.isoformat() if updated.identity_confirmed_at else None,
        }

    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IdentityMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidDocumentApprovalStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ApprovalMissingReasonError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ApprovalAuditError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transaction failed closed: Audit event could not be secured.",
        ) from exc


# ─── 5. DOCUMENT APPROVAL ────────────────────────────────────────────────────

@router.post("/documents/{document_id}/approval", status_code=status.HTTP_200_OK)
def approve_document(
    document_id: uuid.UUID,
    payload: DocumentApprovalRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(
        require_role(CanonicalRole.APPROVER)
    ),
    session: Session = Depends(get_db_session),
    approval_engine: DocumentApprovalEngine = Depends(get_approval_engine),
):
    """Execute final document approval enforcing Four-Eyes, terminal claims, and identity.

    Rules:
      - Only 'approver' role may approve documents.
      - clinical_reviewer is strictly denied approval (HTTP 403).
      - Underlying DocumentApprovalEngine enforces:
        1. Four-Eyes principle: uploader cannot approve their own document.
        2. Terminal claims requirement: all claims must be ACCEPTED, EDITED, or REJECTED.
        3. Identity confirmation: must be CONFIRMED or MISMATCH_ACCEPTED.
        4. Immutability: document cannot be approved twice or if already rejected.
      - Upon success: DocumentApproval record created, is_export_eligible set to True,
        document.status updated to APPROVED, and DOCUMENT_APPROVED audit event secured.
    """
    deny_system_admin_phi(current_user)

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    authorize_document_access(doc, current_user)

    try:
        approval = approval_engine.approve_document(
            session=session,
            document_id=doc.id,
            approver_id=current_user.user_id,
            identity_notes=payload.identity_notes,
            commit=True,
        )

        approval_status_str = (
            approval.approval_status.value
            if hasattr(approval.approval_status, "value")
            else str(approval.approval_status)
        )

        return {
            "approval_id": str(approval.id),
            "document_id": str(approval.document_id),
            "status": approval_status_str,
            "approved_by": str(approval.approver_id),
            "approver_id": str(approval.approver_id),
            "approved_at": approval.created_at.isoformat() if approval.created_at else None,
            "is_export_eligible": approval.is_export_eligible,
            "policy_version": approval.policy_version,
        }

    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FourEyesViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Four-Eyes policy violation: document uploader cannot approve their own document.",
        ) from exc
    except UnreviewedClaimsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot approve document: all claims must be in terminal review states (ACCEPTED, EDITED, REJECTED).",
        ) from exc
    except UnconfirmedIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot approve document: patient identity must be confirmed or mismatch explicitly accepted.",
        ) from exc
    except InvalidDocumentApprovalStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ApprovalAuditError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transaction failed closed: Audit event could not be secured.",
        ) from exc


# ─── 6. SUPPORTING ENDPOINT: DOCUMENT REVIEW SUMMARY ─────────────────────────

@router.get("/documents/{document_id}/review-summary", status_code=status.HTTP_200_OK)
def get_document_review_summary(
    document_id: uuid.UUID,
    request: Request,
    current_user: AuthenticatedUser = Depends(
        require_role(CanonicalRole.CLINICAL_REVIEWER, CanonicalRole.APPROVER)
    ),
    session: Session = Depends(get_db_session),
    triage_engine: TriageEngine = Depends(get_triage_engine),
):
    """Retrieve comprehensive review task context, triage summary, and approval state.

    Necessity:
      In review workflows, after selecting a document from the queue, a reviewer/approver
      needs to inspect the document-level review state, identity verification status,
      triage aggregate counts, and export eligibility alongside claim details.
    """
    deny_system_admin_phi(current_user)

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    authorize_document_access(doc, current_user)

    claims = session.query(Claim).filter(Claim.document_id == doc.id).all()
    claim_ids = [c.id for c in claims]

    v_results = (
        session.query(VerificationResult)
        .filter(VerificationResult.claim_id.in_(claim_ids))
        .all()
        if claim_ids
        else []
    )
    v_map = {vr.claim_id: vr for vr in v_results}

    ev_ids = [c.evidence_id for c in claims if c.evidence_id]
    ev_records = (
        session.query(Evidence).filter(Evidence.id.in_(ev_ids)).all()
        if ev_ids
        else []
    )
    e_map = {e.id: e for e in ev_records}

    triage_summary = triage_engine.triage_document_claims(
        doc,
        claims,
        verification_results=v_map,
        evidence_map=e_map,
    )

    active_approval = (
        session.query(DocumentApproval)
        .filter(
            DocumentApproval.document_id == doc.id,
            DocumentApproval.approval_status == DocumentApprovalStatus.APPROVED,
        )
        .first()
    )

    status_str = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
    id_status_str = (
        doc.identity_verification_status.value
        if hasattr(doc.identity_verification_status, "value")
        else str(doc.identity_verification_status)
    )

    return {
        "document_id": str(doc.id),
        "original_filename": doc.original_filename,
        "status": status_str,
        "patient_context_id": doc.patient_context_id,
        "identity_verification_status": id_status_str,
        "identity_confirmed_by": str(doc.identity_confirmed_by) if doc.identity_confirmed_by else None,
        "identity_confirmed_at": doc.identity_confirmed_at.isoformat() if doc.identity_confirmed_at else None,
        "total_claims": triage_summary.total_claims,
        "auto_processed_count": triage_summary.auto_processed_count,
        "human_review_required_count": triage_summary.human_review_required_count,
        "discrepancy_count": triage_summary.discrepancy_count,
        "can_bulk_confirm_agreed": triage_summary.can_bulk_confirm_agreed,
        "is_approved": active_approval is not None,
        "is_export_eligible": active_approval.is_export_eligible if active_approval else False,
    }
