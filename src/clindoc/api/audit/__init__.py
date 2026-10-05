"""Cryptographic Hash-Chained Audit Service Subsystem.

Conforms to PRODUCTIZATION_SPEC.md §4.2, §5 and PRODUCTIZATION_DECISIONS.md (PD-012).
"""

from __future__ import annotations

from clindoc.api.audit.service import (
    AuditEventValidationError,
    AuditPHISecurityError,
    AuditService,
    AuditServiceError,
    ChainVerificationError,
    ChainVerificationResult,
    FORBIDDEN_PHI_KEYS,
    compute_audit_event_hash,
    validate_metadata_phi_free,
)

__all__ = [
    "AuditEventValidationError",
    "AuditPHISecurityError",
    "AuditService",
    "AuditServiceError",
    "ChainVerificationError",
    "ChainVerificationResult",
    "FORBIDDEN_PHI_KEYS",
    "compute_audit_event_hash",
    "validate_metadata_phi_free",
]
