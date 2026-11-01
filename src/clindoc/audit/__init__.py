"""Audit report generation and validation package.

Authority: PROJECT_SPEC.md §7.8, schemas/audit_report.json
"""

from clindoc.audit.quarantine import (
    ExternalConcurrentModificationError,
    ForensicQuarantineError,
    ForensicQuarantineVerificationError,
    UnquiescedLedgerModificationError,
    UnsupportedFilesystemError,
    execute_verified_forensic_quarantine,
    find_valid_tail_offset,
    recover_torn_tail,
)
from clindoc.audit.report import (
    AuditReport,
    AuditReportSummary,
    generate_audit_report,
    validate_audit_report,
)

__all__ = [
    "AuditReport",
    "AuditReportSummary",
    "generate_audit_report",
    "validate_audit_report",
    "ExternalConcurrentModificationError",
    "ForensicQuarantineError",
    "ForensicQuarantineVerificationError",
    "UnquiescedLedgerModificationError",
    "UnsupportedFilesystemError",
    "execute_verified_forensic_quarantine",
    "find_valid_tail_offset",
    "recover_torn_tail",
]
