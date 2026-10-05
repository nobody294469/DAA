"""Role-Based Access Control (RBAC) Dependencies and Guards.

Authority:
  - PRODUCTIZATION_SPEC.md §1.2 (Roles & Separation of Duties)
  - PRODUCTIZATION_DECISIONS.md PD-009 (RBAC, Separation of Duties, Four-Eyes)

Enforces:
  - Strict role validation based exclusively on local database UserRole records
  - Deny by default for missing, unknown, or insufficient roles
  - Mandatory system_admin PHI isolation: system_admin MUST receive HTTP 403
    when attempting to access document content, claims, evidence, or clinical text
  - Client-supplied request parameters or JWT role claims are NEVER trusted
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, HTTPException, status

from clindoc.api.auth.dependencies import get_current_user
from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.auth.roles import (
    ALL_CANONICAL_ROLES,
    AUDIT_PERMITTED_ROLES,
    ADMIN_PERMITTED_ROLES,
    CanonicalRole,
    EXPORT_PERMITTED_ROLES,
    PHI_PERMITTED_ROLES,
)


def require_role(*allowed_roles: str | CanonicalRole) -> Callable[..., AuthenticatedUser]:
    """FastAPI dependency factory: Require authenticated user to possess at least one specified role.

    Rules:
      - Uses the authenticated principal resolved from the local database.
      - Never trusts client-supplied JWT claims or request parameters.
      - Denies by default (HTTP 403) if the user possesses none of the required roles.

    Returns:
      Callable dependency returning the AuthenticatedUser upon successful authorization.
    """
    canonical_set: set[str] = {
        r.value if isinstance(r, CanonicalRole) else str(r).strip()
        for r in allowed_roles
        if r
    }

    def role_checker(
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if not canonical_set:
            # Deny by default if no allowed roles were specified
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: No authorized roles configured for this operation.",
            )

        # Check if user has any of the acceptable roles in the local database
        if current_user.roles.isdisjoint(canonical_set):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Forbidden: Insufficient permissions. Required role(s): "
                    f"{sorted(list(canonical_set))}; user roles: {sorted(list(current_user.roles))}"
                ),
            )

        return current_user

    return role_checker


def deny_system_admin_phi(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """FastAPI dependency: Strictly forbid system_admin from accessing clinical/PHI content (PD-009).

    System administrators have configuration, operations, and retention rights,
    but MUST receive HTTP 403 when attempting to access document content, claims,
    evidence, page text, clinical narratives, or other PHI.
    """
    if current_user.has_role(CanonicalRole.SYSTEM_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Forbidden: System administrators are strictly prohibited from accessing "
                "clinical/PHI content (PD-009, PRODUCTIZATION_SPEC §1.2)."
            ),
        )
    return current_user


def require_phi_access(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """FastAPI dependency: Authorize access to clinical / PHI resources.

    Enforces:
      1. Immediate HTTP 403 if user holds system_admin role (isolation policy).
      2. Deny by default: user must possess a clinical/document role (clinical_reviewer,
         approver, or document_operator).
      3. Auditors and integration services are denied direct PHI access.
    """
    # 1. Enforce admin PHI isolation
    deny_system_admin_phi(current_user)

    # 2. Enforce PHI permitted roles
    if current_user.roles.isdisjoint(PHI_PERMITTED_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User does not possess a role authorized for clinical/PHI data access.",
        )

    return current_user


def require_admin_access(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """FastAPI dependency: Require system_admin role for non-PHI system configuration/operations."""
    if not current_user.has_role(CanonicalRole.SYSTEM_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Operation requires system_admin privileges.",
        )
    return current_user


def require_audit_access(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """FastAPI dependency: Require auditor or system_admin role for non-PHI audit events & metrics."""
    if current_user.roles.isdisjoint(AUDIT_PERMITTED_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Operation requires auditor or system_admin privileges.",
        )
    return current_user


def require_export_access(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """FastAPI dependency: Require integration_service or approver role for FHIR export."""
    # system_admin is not allowed to export PHI bundles
    deny_system_admin_phi(current_user)

    if current_user.roles.isdisjoint(EXPORT_PERMITTED_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Operation requires integration_service or approver privileges.",
        )
    return current_user
