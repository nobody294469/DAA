"""Object-Level Authorization Helpers (PD-009, PRODUCTIZATION_SPEC §1.2, §5).

Authority:
  - PRODUCTIZATION_SPEC.md §1.2 (Role scopes and separation of duties)
  - PRODUCTIZATION_DECISIONS.md PD-009 (RBAC, Separation of Duties, Four-Eyes)

Enforces:
  - Scoped object authorization based on authenticated database identity (User.id)
  - Deny by default: never allows broad "if authenticated then allow" access
  - system_admin isolation: strict HTTP 403 when attempting to access PHI / document contents
  - Document Operator scoped to own uploaded documents (uploaded_by == user.id)
  - Four-Eyes approval check: uploader cannot approve their own document (PD-009)
  - Client-supplied parameters (e.g. user_id, owner_id) are NEVER trusted
"""

from __future__ import annotations

from fastapi import HTTPException, status

from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.auth.roles import CanonicalRole
from clindoc.db.enums import RetentionState
from clindoc.db.models import Claim, Document, ProcessingJob


def authorize_document_access(
    document: Document,
    user: AuthenticatedUser,
    action: str = "read",
) -> None:
    """Authorize access to a specific Document entity and its clinical contents.

    Rules:
      1. system_admin isolation: strictly denied (HTTP 403) for document/PHI content.
      2. auditor: read-only access to audit trail/metrics, NOT direct document PHI (HTTP 403).
      3. integration_service: export endpoints only, cannot browse raw documents (HTTP 403).
      4. document_operator: permitted ONLY for documents they uploaded (uploaded_by == user.user_id).
      5. clinical_reviewer / approver: permitted for active, non-purged documents in review scope.
      6. Deleted/purged documents: rejected with HTTP 403 / 404.
      7. Deny by default for any unknown or unassigned roles.
    """
    # 1. Mandatory system_admin PHI isolation rule (PD-009)
    if user.has_role(CanonicalRole.SYSTEM_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Forbidden: System administrators are strictly prohibited from accessing "
                "document content or clinical data (PD-009, PRODUCTIZATION_SPEC §1.2)."
            ),
        )

    # 2. Check deletion / retention tombstone
    if document.deleted_at is not None or document.retention_state == RetentionState.PURGED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: The requested document has been deleted or purged.",
        )

    # 3. Clinical Reviewer and Approver have clinical document access
    if user.has_any_role(CanonicalRole.CLINICAL_REVIEWER, CanonicalRole.APPROVER):
        return

    # 4. Document Operator is scoped strictly to their own uploaded documents
    if user.has_role(CanonicalRole.DOCUMENT_OPERATOR):
        if document.uploaded_by != user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Document operators may only access documents they uploaded.",
            )
        return

    # 5. Auditor and Integration Service have no direct document content access
    if user.has_role(CanonicalRole.AUDITOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Auditors have access to audit logs and metrics, not clinical document contents.",
        )

    if user.has_role(CanonicalRole.INTEGRATION_SERVICE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Integration service accounts are restricted to FHIR export endpoints.",
        )

    # 6. Deny by default
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: User lacks authorization to access this document.",
    )


def check_four_eyes_approval(
    document: Document,
    user: AuthenticatedUser,
) -> None:
    """Validate Four-Eyes approval policy (PD-009, PRODUCTIZATION_SPEC §1.2).

    Rules:
      1. User must possess the 'approver' role.
      2. The user who uploaded the document CANNOT approve that document.
    """
    if not user.has_role(CanonicalRole.APPROVER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Document approval requires the 'approver' role.",
        )

    if document.uploaded_by == user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Forbidden: Four-Eyes Policy violation. The document operator who uploaded "
                "a document cannot approve that document for export (PD-009)."
            ),
        )


def authorize_claim_access(
    claim: Claim,
    user: AuthenticatedUser,
    action: str = "read",
) -> None:
    """Authorize access to an individual extracted Claim (PHI).

    Rules:
      1. system_admin strictly denied (HTTP 403).
      2. auditor strictly denied (HTTP 403).
      3. integration_service strictly denied (HTTP 403).
      4. document_operator strictly denied (HTTP 403; operators monitor jobs, not review clinical claims).
      5. clinical_reviewer / approver permitted.
      6. Deny by default.
    """
    # 1. system_admin PHI isolation
    if user.has_role(CanonicalRole.SYSTEM_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Forbidden: System administrators are strictly prohibited from accessing "
                "clinical claims (PD-009, PRODUCTIZATION_SPEC §1.2)."
            ),
        )

    # 2. Clinical review roles permitted
    if user.has_any_role(CanonicalRole.CLINICAL_REVIEWER, CanonicalRole.APPROVER):
        return

    # 3. Operators cannot review/access clinical claims
    if user.has_role(CanonicalRole.DOCUMENT_OPERATOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Document operators do not have permissions to review or inspect clinical claims.",
        )

    # 4. Auditors and Integration services denied
    if user.has_any_role(CanonicalRole.AUDITOR, CanonicalRole.INTEGRATION_SERVICE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Non-clinical roles are not authorized to access individual clinical claims.",
        )

    # 5. Deny by default
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: User lacks authorization to access clinical claims.",
    )


def authorize_job_access(
    job: ProcessingJob,
    user: AuthenticatedUser,
) -> None:
    """Authorize access to a background ProcessingJob and its operational state.

    Rules:
      1. system_admin permitted (operational queue monitoring and health oversight, non-PHI).
      2. document_operator permitted ONLY if they uploaded the associated document.
      3. clinical_reviewer / approver permitted (monitoring jobs for review).
      4. auditor permitted (operational metrics and telemetry).
      5. integration_service denied (HTTP 403).
      6. Deny by default.
    """
    # 1. Administrative operations permitted
    if user.has_role(CanonicalRole.SYSTEM_ADMIN):
        return

    # 2. Clinical review and auditing permitted
    if user.has_any_role(CanonicalRole.CLINICAL_REVIEWER, CanonicalRole.APPROVER, CanonicalRole.AUDITOR):
        return

    # 3. Document operator scoped to their own jobs
    if user.has_role(CanonicalRole.DOCUMENT_OPERATOR):
        if job.document and job.document.uploaded_by == user.user_id:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Document operators may only monitor jobs for their own uploaded documents.",
        )

    # 4. Deny by default
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: User lacks authorization to view this processing job.",
    )
