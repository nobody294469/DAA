"""Patient Identity Confirmation & Document Approval Engine (PRODUCTIZATION_SPEC §7.3, PD-002, PD-009, PD-015).

Authority:
  - PRODUCTIZATION_SPEC.md §4.3 (documents, document_approvals, claims, audit_events)
  - PRODUCTIZATION_SPEC.md §7.3 (Human review workflow, Four-Eyes approval gate)
  - PRODUCTIZATION_DECISIONS.md PD-002 (Mode 0: 100% human review prior to export)
  - PRODUCTIZATION_DECISIONS.md PD-009 (Four-Eyes principle: uploader cannot approve own document)
  - PRODUCTIZATION_DECISIONS.md PD-012 (Append-only hash-chained audit logging, fail-closed)
  - PRODUCTIZATION_DECISIONS.md PD-015 (Patient context linkage & explicit identity confirmation)

Core Invariants:
  1. Patient Identity Confirmation:
     - Explicit human action required (never inferred or automated via MPI).
     - Compares header demographics against operator-selected patient context (Name, MRN, DOB, Gender).
     - Demographics mismatch prevents CONFIRMED status unless explicitly accepted as MISMATCH_ACCEPTED.
     - Persists identity_verification_status, identity_confirmed_by, identity_confirmed_at on Document.
  2. Four-Eyes Requirement:
     - Baseline rule: The uploader cannot approve their own document.
     - Enforced via configurable require_distinct_approver (defaults to True).
  3. Fail-Closed Document Approval Gate:
     - All claims for the document must be in terminal states: ACCEPTED, EDITED, REJECTED.
     - Document approval is blocked if any claim remains PENDING or ESCALATED.
     - Patient identity must be explicitly CONFIRMED or MISMATCH_ACCEPTED.
     - Document cannot be approved multiple times or if already rejected.
     - Only after all prerequisites pass: DocumentApproval is persisted, document.status
       becomes APPROVED, and is_export_eligible becomes True.
  4. Cryptographic Audit Logging:
     - Records PHI-free audit events: PATIENT_IDENTITY_CONFIRMED, DOCUMENT_APPROVED, DOCUMENT_REJECTED.
     - Transactional fail-closed guarantee: any audit failure rolls back all database mutations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any, Optional, Sequence, Union
import uuid

from sqlalchemy.orm import Session

from clindoc.api.audit.service import AuditService, AuditServiceError, validate_metadata_phi_free
from clindoc.db.enums import (
    ClaimReviewState,
    DocumentApprovalStatus,
    DocumentStatus,
    IdentityVerificationStatus,
    PatientIdentityApprovalStatus,
)
from clindoc.db.models import Claim, Document, DocumentApproval, TriagePolicy
from clindoc.review.triage import DEFAULT_MODE_0_POLICY_VERSION

logger = logging.getLogger(__name__)


# ─── EXCEPTIONS ───────────────────────────────────────────────────────────────

class ApprovalEngineError(Exception):
    """Base exception for identity confirmation and approval engine operations."""


class DocumentNotFoundError(ApprovalEngineError, KeyError):
    """Raised when a specified document does not exist."""


class IdentityConfirmationError(ApprovalEngineError):
    """Base exception for patient identity confirmation failures."""


class IdentityMismatchError(IdentityConfirmationError, ValueError):
    """Raised when demographics mismatch prevents CONFIRMED status."""


class UnconfirmedIdentityError(ApprovalEngineError, ValueError):
    """Raised when document approval is attempted without confirmed patient identity."""


class FourEyesViolationError(ApprovalEngineError, PermissionError):
    """Raised when an uploader attempts to approve their own document."""


class UnreviewedClaimsError(ApprovalEngineError, ValueError):
    """Raised when document approval is attempted while claims remain non-terminal."""


class InvalidDocumentApprovalStateError(ApprovalEngineError, ValueError):
    """Raised when an illegal approval state transition is attempted."""


class MissingReasonCodeError(ApprovalEngineError, ValueError):
    """Raised when a mandatory reason code is missing."""


class AuditIntegrationError(ApprovalEngineError):
    """Raised when audit event persistence fails, triggering rollback."""


# ─── DEMOGRAPHICS DATA CONTRACTS & NORMALIZATION ─────────────────────────────

@dataclass(frozen=True)
class PatientDemographics:
    """Standardized demographic attributes for patient identity verification."""

    name: Optional[str] = None
    mrn: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None


@dataclass(frozen=True)
class DemographicComparisonResult:
    """Outcome of comparing extracted document demographics to patient context."""

    is_match: bool
    mismatched_fields: list[str] = field(default_factory=list)
    matched_fields: list[str] = field(default_factory=list)


def _normalize_string(val: Optional[str]) -> str:
    """Normalize whitespace and case for demographic comparisons."""
    if not val:
        return ""
    return re.sub(r"\s+", " ", val.strip()).lower()


def _normalize_gender(val: Optional[str]) -> str:
    """Normalize gender strings to canonical representation."""
    norm = _normalize_string(val)
    if norm in ("m", "male"):
        return "male"
    if norm in ("f", "female"):
        return "female"
    if norm in ("o", "other"):
        return "other"
    return norm


def compare_demographics(
    context_demographics: PatientDemographics,
    extracted_demographics: PatientDemographics,
) -> DemographicComparisonResult:
    """Compare patient demographics between EHR context and extracted document headers.
    
    Fields compared:
      - name (case/whitespace insensitive)
      - mrn (case/whitespace insensitive)
      - dob (standardized string match)
      - gender (canonical gender string match)
    """
    mismatches: list[str] = []
    matches: list[str] = []

    # 1. Compare MRN
    if context_demographics.mrn and extracted_demographics.mrn:
        if _normalize_string(context_demographics.mrn) != _normalize_string(extracted_demographics.mrn):
            mismatches.append("mrn")
        else:
            matches.append("mrn")

    # 2. Compare Name
    if context_demographics.name and extracted_demographics.name:
        if _normalize_string(context_demographics.name) != _normalize_string(extracted_demographics.name):
            mismatches.append("name")
        else:
            matches.append("name")

    # 3. Compare DOB
    if context_demographics.dob and extracted_demographics.dob:
        if _normalize_string(context_demographics.dob) != _normalize_string(extracted_demographics.dob):
            mismatches.append("dob")
        else:
            matches.append("dob")

    # 4. Compare Gender
    if context_demographics.gender and extracted_demographics.gender:
        if _normalize_gender(context_demographics.gender) != _normalize_gender(extracted_demographics.gender):
            mismatches.append("gender")
        else:
            matches.append("gender")

    is_match = (len(mismatches) == 0)
    return DemographicComparisonResult(
        is_match=is_match,
        mismatched_fields=mismatches,
        matched_fields=matches,
    )


# ─── DOCUMENT APPROVAL & IDENTITY SERVICE ─────────────────────────────────────

class DocumentApprovalEngine:
    """Orchestrates patient identity confirmation, Four-Eyes approval checks, and audit."""

    def __init__(
        self,
        audit_service: Optional[AuditService] = None,
        *,
        require_distinct_approver: bool = True,
        policy_version: Optional[str] = None,
    ) -> None:
        self.audit_service = audit_service or AuditService()
        self.require_distinct_approver = require_distinct_approver
        self.policy_version = policy_version or DEFAULT_MODE_0_POLICY_VERSION

    def _resolve_document(self, session: Session, document_id: Union[uuid.UUID, str]) -> Document:
        """Resolve Document model by primary key."""
        doc_uuid = uuid.UUID(str(document_id)) if not isinstance(document_id, uuid.UUID) else document_id
        doc = session.get(Document, doc_uuid)
        if doc is None:
            raise DocumentNotFoundError(f"Document '{doc_uuid}' not found.")
        return doc

    # ─── 1. PATIENT IDENTITY CONFIRMATION ─────────────────────────────────────

    def confirm_identity(
        self,
        session: Session,
        document_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        *,
        extracted_demographics: Optional[PatientDemographics] = None,
        context_demographics: Optional[PatientDemographics] = None,
        notes: Optional[str] = None,
        commit: bool = True,
    ) -> Document:
        """Explicitly confirm patient identity matching document demographics to patient context.

        Requires an authorized human action. If demographics are provided and mismatch,
        retains MISMATCH status and raises IdentityMismatchError.
        """
        actor_uuid = uuid.UUID(str(actor_id)) if not isinstance(actor_id, uuid.UUID) else actor_id

        try:
            document = self._resolve_document(session, document_id)

            # Prevent mutating identity if document is already in terminal approved state
            if document.status in (DocumentStatus.APPROVED, DocumentStatus.REJECTED):
                raise InvalidDocumentApprovalStateError(
                    f"Cannot confirm identity on a document in '{document.status.value}' state."
                )

            # Check demographic consistency if both sides are provided
            comparison: Optional[DemographicComparisonResult] = None
            if extracted_demographics is not None and context_demographics is not None:
                comparison = compare_demographics(context_demographics, extracted_demographics)
                if not comparison.is_match:
                    document.identity_verification_status = IdentityVerificationStatus.MISMATCH
                    session.flush()
                    if commit:
                        session.commit()
                    raise IdentityMismatchError(
                        f"Demographics mismatch detected for fields: {comparison.mismatched_fields}. "
                        "Cannot set CONFIRMED status without explicit mismatch acceptance."
                    )

            # Record explicit confirmation
            document.identity_verification_status = IdentityVerificationStatus.CONFIRMED
            document.identity_confirmed_by = actor_uuid
            document.identity_confirmed_at = datetime.now(timezone.utc)
            session.flush()

            # Record PHI-free audit event
            audit_metadata: dict[str, Any] = {
                "document_id": str(document.id),
                "identity_status": IdentityVerificationStatus.CONFIRMED.value,
                "confirmed_by": str(actor_uuid),
                "has_mismatch": False,
                "notes_present": bool(notes),
            }
            if comparison:
                audit_metadata["matched_fields"] = comparison.matched_fields

            validate_metadata_phi_free(audit_metadata)

            try:
                self.audit_service.record_event(
                    session=session,
                    actor_id=str(actor_uuid),
                    event_type="PATIENT_IDENTITY_CONFIRMED",
                    entity_type="document",
                    entity_id=str(document.id),
                    action="CONFIRM_IDENTITY",
                    metadata_json=audit_metadata,
                )
            except Exception as exc:
                logger.error("Audit failure on confirm_identity for document %s: %s", document.id, exc)
                raise AuditIntegrationError(f"Audit event recording failed: {exc}") from exc

            if commit:
                session.commit()
            return document

        except IdentityMismatchError:
            raise
        except Exception:
            session.rollback()
            raise

    def accept_identity_mismatch(
        self,
        session: Session,
        document_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        reason_code: str,
        *,
        notes: Optional[str] = None,
        commit: bool = True,
    ) -> Document:
        """Explicitly accept a patient identity mismatch with human justification.

        Transitions identity status to MISMATCH_ACCEPTED, enabling document approval
        while preserving complete provenance of the mismatch override.
        """
        if not reason_code or not str(reason_code).strip():
            raise MissingReasonCodeError("Reason code is required to accept an identity mismatch.")

        actor_uuid = uuid.UUID(str(actor_id)) if not isinstance(actor_id, uuid.UUID) else actor_id

        try:
            document = self._resolve_document(session, document_id)

            if document.status in (DocumentStatus.APPROVED, DocumentStatus.REJECTED):
                raise InvalidDocumentApprovalStateError(
                    f"Cannot accept identity mismatch on a document in '{document.status.value}' state."
                )

            document.identity_verification_status = IdentityVerificationStatus.MISMATCH_ACCEPTED
            document.identity_confirmed_by = actor_uuid
            document.identity_confirmed_at = datetime.now(timezone.utc)
            session.flush()

            # Record PHI-free audit event
            audit_metadata: dict[str, Any] = {
                "document_id": str(document.id),
                "identity_status": IdentityVerificationStatus.MISMATCH_ACCEPTED.value,
                "confirmed_by": str(actor_uuid),
                "has_mismatch": True,
                "reason_code": reason_code.strip(),
                "notes_present": bool(notes),
            }
            validate_metadata_phi_free(audit_metadata)

            try:
                self.audit_service.record_event(
                    session=session,
                    actor_id=str(actor_uuid),
                    event_type="PATIENT_IDENTITY_CONFIRMED",
                    entity_type="document",
                    entity_id=str(document.id),
                    action="ACCEPT_IDENTITY_MISMATCH",
                    metadata_json=audit_metadata,
                )
            except Exception as exc:
                logger.error("Audit failure on accept_identity_mismatch for document %s: %s", document.id, exc)
                raise AuditIntegrationError(f"Audit event recording failed: {exc}") from exc

            if commit:
                session.commit()
            return document

        except Exception:
            session.rollback()
            raise

    # ─── 2. DOCUMENT APPROVAL ENGINE ──────────────────────────────────────────

    def approve_document(
        self,
        session: Session,
        document_id: Union[uuid.UUID, str],
        approver_id: Union[uuid.UUID, str],
        *,
        identity_notes: Optional[str] = None,
        commit: bool = True,
    ) -> DocumentApproval:
        """Formal document approval enforcing Four-Eyes, terminal claims, and confirmed identity.

        Prerequisites (all must hold):
          1. Document is not already approved or rejected.
          2. Four-Eyes check: approver must not be the document uploader.
          3. Patient identity is CONFIRMED or MISMATCH_ACCEPTED with confirmed actor.
          4. All document claims are in terminal review states (ACCEPTED, EDITED, REJECTED).
             Zero claims may be PENDING or ESCALATED.

        Outcomes:
          - Persists DocumentApproval record with is_export_eligible=True.
          - Updates document.status = DocumentStatus.APPROVED.
          - Records strictly PHI-free DOCUMENT_APPROVED audit event.
          - Fails closed on any error, rolling back all mutations.
        """
        approver_uuid = uuid.UUID(str(approver_id)) if not isinstance(approver_id, uuid.UUID) else approver_id

        try:
            document = self._resolve_document(session, document_id)

            # 1. State / Concurrency Validation
            current_status = document.status.value if hasattr(document.status, "value") else str(document.status)
            if current_status == DocumentStatus.APPROVED.value:
                raise InvalidDocumentApprovalStateError("Document is already approved.")
            if current_status == DocumentStatus.REJECTED.value:
                raise InvalidDocumentApprovalStateError("Document has already been rejected.")

            # Check for existing active approval row
            existing_approval = (
                session.query(DocumentApproval)
                .filter(
                    DocumentApproval.document_id == document.id,
                    DocumentApproval.approval_status == DocumentApprovalStatus.APPROVED,
                )
                .first()
            )
            if existing_approval is not None:
                raise InvalidDocumentApprovalStateError("Document has an existing active approval.")

            # 2. Four-Eyes Baseline Requirement (PD-009)
            if self.require_distinct_approver and document.uploaded_by == approver_uuid:
                raise FourEyesViolationError(
                    f"Four-Eyes policy violation: uploader '{document.uploaded_by}' cannot approve their own document."
                )

            # 3. Patient Identity Confirmation Prerequisite (PD-015)
            id_status = (
                document.identity_verification_status.value
                if hasattr(document.identity_verification_status, "value")
                else str(document.identity_verification_status)
            )
            eligible_id_statuses = {
                IdentityVerificationStatus.CONFIRMED.value,
                IdentityVerificationStatus.MISMATCH_ACCEPTED.value,
            }
            if id_status not in eligible_id_statuses or document.identity_confirmed_by is None:
                raise UnconfirmedIdentityError(
                    f"Patient identity is '{id_status}' (must be explicitly CONFIRMED or MISMATCH_ACCEPTED)."
                )

            # Map to PatientIdentityApprovalStatus enum
            if id_status == IdentityVerificationStatus.CONFIRMED.value:
                approval_id_status = PatientIdentityApprovalStatus.CONFIRMED
            else:
                approval_id_status = PatientIdentityApprovalStatus.MISMATCH_ACCEPTED

            # 4. Claim Review Terminal State Validation
            claims = session.query(Claim).filter(Claim.document_id == document.id).all()
            terminal_states = {
                ClaimReviewState.ACCEPTED.value,
                ClaimReviewState.EDITED.value,
                ClaimReviewState.REJECTED.value,
            }

            non_terminal_claims = [
                c for c in claims
                if (c.review_state.value if hasattr(c.review_state, "value") else str(c.review_state)) not in terminal_states
            ]
            if non_terminal_claims:
                states_summary = [
                    (c.review_state.value if hasattr(c.review_state, "value") else str(c.review_state))
                    for c in non_terminal_claims
                ]
                raise UnreviewedClaimsError(
                    f"Cannot approve document: {len(non_terminal_claims)} claims remain non-terminal {states_summary}. "
                    "All claims must be ACCEPTED, EDITED, or REJECTED."
                )

            # 5. Resolve Active Policy Version
            active_policy = session.query(TriagePolicy).filter(TriagePolicy.is_active.is_(True)).first()
            policy_version = active_policy.version if active_policy else self.policy_version

            # 6. Persist DocumentApproval Record
            approval = DocumentApproval(
                id=uuid.uuid4(),
                document_id=document.id,
                approver_id=approver_uuid,
                approval_status=DocumentApprovalStatus.APPROVED,
                patient_identity_status=approval_id_status,
                identity_notes=identity_notes,
                policy_version=policy_version,
                is_export_eligible=True,
            )
            session.add(approval)

            # 7. Update Document Status
            document.status = DocumentStatus.APPROVED
            session.flush()

            # 8. Cryptographic Hash-Chained Audit Logging
            accepted_count = sum(
                1 for c in claims
                if (c.review_state.value if hasattr(c.review_state, "value") else str(c.review_state)) == ClaimReviewState.ACCEPTED.value
            )
            edited_count = sum(
                1 for c in claims
                if (c.review_state.value if hasattr(c.review_state, "value") else str(c.review_state)) == ClaimReviewState.EDITED.value
            )
            rejected_count = sum(
                1 for c in claims
                if (c.review_state.value if hasattr(c.review_state, "value") else str(c.review_state)) == ClaimReviewState.REJECTED.value
            )

            audit_metadata: dict[str, Any] = {
                "document_id": str(document.id),
                "approval_id": str(approval.id),
                "approver_id": str(approver_uuid),
                "approval_status": DocumentApprovalStatus.APPROVED.value,
                "patient_identity_status": approval_id_status.value,
                "policy_version": policy_version,
                "is_export_eligible": True,
                "total_claims": len(claims),
                "accepted_claims": accepted_count,
                "edited_claims": edited_count,
                "rejected_claims": rejected_count,
            }
            validate_metadata_phi_free(audit_metadata)

            try:
                self.audit_service.record_event(
                    session=session,
                    actor_id=str(approver_uuid),
                    event_type="DOCUMENT_APPROVED",
                    entity_type="document",
                    entity_id=str(document.id),
                    action="APPROVE",
                    metadata_json=audit_metadata,
                )
            except Exception as exc:
                logger.error("Audit failure on approve_document for document %s: %s", document.id, exc)
                raise AuditIntegrationError(f"Audit event recording failed: {exc}") from exc

            if commit:
                session.commit()

            return approval

        except Exception:
            session.rollback()
            raise

    def reject_document(
        self,
        session: Session,
        document_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        reason_code: str,
        *,
        notes: Optional[str] = None,
        commit: bool = True,
    ) -> DocumentApproval:
        """Formal document rejection recording non-eligible status."""
        if not reason_code or not str(reason_code).strip():
            raise MissingReasonCodeError("Reason code is required to reject a document.")

        actor_uuid = uuid.UUID(str(actor_id)) if not isinstance(actor_id, uuid.UUID) else actor_id

        try:
            document = self._resolve_document(session, document_id)

            current_status = document.status.value if hasattr(document.status, "value") else str(document.status)
            if current_status == DocumentStatus.APPROVED.value:
                raise InvalidDocumentApprovalStateError("Cannot reject an already approved document.")
            if current_status == DocumentStatus.REJECTED.value:
                raise InvalidDocumentApprovalStateError("Document is already rejected.")

            # Map patient identity status safely
            id_status = (
                document.identity_verification_status.value
                if hasattr(document.identity_verification_status, "value")
                else str(document.identity_verification_status)
            )
            if id_status == IdentityVerificationStatus.CONFIRMED.value:
                approval_id_status = PatientIdentityApprovalStatus.CONFIRMED
            elif id_status == IdentityVerificationStatus.MISMATCH_ACCEPTED.value:
                approval_id_status = PatientIdentityApprovalStatus.MISMATCH_ACCEPTED
            else:
                approval_id_status = PatientIdentityApprovalStatus.OVERRIDDEN

            active_policy = session.query(TriagePolicy).filter(TriagePolicy.is_active.is_(True)).first()
            policy_version = active_policy.version if active_policy else self.policy_version

            approval = DocumentApproval(
                id=uuid.uuid4(),
                document_id=document.id,
                approver_id=actor_uuid,
                approval_status=DocumentApprovalStatus.REJECTED,
                patient_identity_status=approval_id_status,
                identity_notes=notes,
                policy_version=policy_version,
                is_export_eligible=False,
            )
            session.add(approval)
            document.status = DocumentStatus.REJECTED
            session.flush()

            audit_metadata: dict[str, Any] = {
                "document_id": str(document.id),
                "approval_id": str(approval.id),
                "actor_id": str(actor_uuid),
                "approval_status": DocumentApprovalStatus.REJECTED.value,
                "reason_code": reason_code.strip(),
                "is_export_eligible": False,
            }
            validate_metadata_phi_free(audit_metadata)

            try:
                self.audit_service.record_event(
                    session=session,
                    actor_id=str(actor_uuid),
                    event_type="DOCUMENT_REJECTED",
                    entity_type="document",
                    entity_id=str(document.id),
                    action="REJECT",
                    metadata_json=audit_metadata,
                )
            except Exception as exc:
                logger.error("Audit failure on reject_document for document %s: %s", document.id, exc)
                raise AuditIntegrationError(f"Audit event recording failed: {exc}") from exc

            if commit:
                session.commit()

            return approval

        except Exception:
            session.rollback()
            raise
