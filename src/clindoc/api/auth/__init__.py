"""OIDC Authentication and RBAC Authorization Subsystem.

Conforms to PRODUCTIZATION_SPEC.md §1.2, §5 and PRODUCTIZATION_DECISIONS.md (PD-006, PD-009).
"""

from __future__ import annotations

from clindoc.api.auth.config import OIDCConfig
from clindoc.api.auth.dependencies import (
    bearer_scheme,
    get_current_user,
    get_db_session,
    get_token_validator,
    set_global_session_factory,
    set_global_token_validator,
)
from clindoc.api.auth.models import (
    AuthenticatedUser,
    AuthenticationError,
    UserDeactivatedError,
    UserNotFoundError,
)
from clindoc.api.auth.object_access import (
    authorize_claim_access,
    authorize_document_access,
    authorize_job_access,
    check_four_eyes_approval,
)
from clindoc.api.auth.rbac import (
    deny_system_admin_phi,
    require_admin_access,
    require_audit_access,
    require_export_access,
    require_phi_access,
    require_role,
)
from clindoc.api.auth.roles import (
    ADMIN_PERMITTED_ROLES,
    ALL_CANONICAL_ROLES,
    AUDIT_PERMITTED_ROLES,
    CanonicalRole,
    EXPORT_PERMITTED_ROLES,
    PHI_PERMITTED_ROLES,
)
from clindoc.api.auth.token_validator import OIDCTokenValidator

__all__ = [
    # Roles
    "ADMIN_PERMITTED_ROLES",
    "ALL_CANONICAL_ROLES",
    "AUDIT_PERMITTED_ROLES",
    "CanonicalRole",
    "EXPORT_PERMITTED_ROLES",
    "PHI_PERMITTED_ROLES",
    # Principal & Models
    "AuthenticatedUser",
    "AuthenticationError",
    "UserDeactivatedError",
    "UserNotFoundError",
    # Config & Validator
    "OIDCConfig",
    "OIDCTokenValidator",
    # AuthN Dependencies
    "bearer_scheme",
    "get_current_user",
    "get_db_session",
    "get_token_validator",
    "set_global_session_factory",
    "set_global_token_validator",
    # RBAC Dependencies & Guards
    "deny_system_admin_phi",
    "require_admin_access",
    "require_audit_access",
    "require_export_access",
    "require_phi_access",
    "require_role",
    # Object-Level Authorization
    "authorize_claim_access",
    "authorize_document_access",
    "authorize_job_access",
    "check_four_eyes_approval",
]
