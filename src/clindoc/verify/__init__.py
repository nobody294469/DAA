"""Clinical document extraction verification and controlled error injection module."""

from clindoc.verify.schema import (
    FieldDiscrepancy,
    InjectedErrorRecord,
    ObservationVerificationResult,
    ReportVerificationResult,
    VerificationStatus,
)
from clindoc.verify.verifier import verify_observation_against_evidence, verify_report
from clindoc.verify.error_injection import ERROR_TYPES, inject_single_error, generate_corrupted_dataset

__all__ = [
    "VerificationStatus",
    "FieldDiscrepancy",
    "ObservationVerificationResult",
    "ReportVerificationResult",
    "InjectedErrorRecord",
    "verify_observation_against_evidence",
    "verify_report",
    "ERROR_TYPES",
    "inject_single_error",
    "generate_corrupted_dataset",
]
