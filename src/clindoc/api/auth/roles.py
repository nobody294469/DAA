"""Canonical Role Definitions for Role-Based Access Control (RBAC).

Authority:
  - PRODUCTIZATION_SPEC.md §1.2 (Roles & Separation of Duties)
  - PRODUCTIZATION_DECISIONS.md PD-009 (RBAC, Separation of Duties, Four-Eyes)
"""

from __future__ import annotations

from enum import Enum


class CanonicalRole(str, Enum):
    """The six canonical platform roles defined by PRODUCTIZATION_SPEC §1.2."""

    SYSTEM_ADMIN = "system_admin"
    DOCUMENT_OPERATOR = "document_operator"
    CLINICAL_REVIEWER = "clinical_reviewer"
    APPROVER = "approver"
    AUDITOR = "auditor"
    INTEGRATION_SERVICE = "integration_service"


# Role category groups
ALL_CANONICAL_ROLES: frozenset[str] = frozenset(r.value for r in CanonicalRole)

# Roles permitted to access clinical / PHI data (subject to object-level scoping)
PHI_PERMITTED_ROLES: frozenset[str] = frozenset({
    CanonicalRole.CLINICAL_REVIEWER.value,
    CanonicalRole.APPROVER.value,
    CanonicalRole.DOCUMENT_OPERATOR.value,
})

# Roles permitted to access administrative / non-PHI system configuration
ADMIN_PERMITTED_ROLES: frozenset[str] = frozenset({
    CanonicalRole.SYSTEM_ADMIN.value,
})

# Roles permitted to access audit logs and compliance metrics
AUDIT_PERMITTED_ROLES: frozenset[str] = frozenset({
    CanonicalRole.AUDITOR.value,
    CanonicalRole.SYSTEM_ADMIN.value,
})

# Roles permitted to export finalized FHIR bundles
EXPORT_PERMITTED_ROLES: frozenset[str] = frozenset({
    CanonicalRole.INTEGRATION_SERVICE.value,
    CanonicalRole.APPROVER.value,
})
