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
from clindoc.verify.stage_a import DeterministicVerifier, StageAResult
from clindoc.verify.stage_b import LearnedVerifier, StageBResult, MockModelPredictor
from clindoc.verify.stage_c import StageCJudge, StageCResult, StageCJudgeOutput, DefaultMockLLMJudge
from clindoc.verify.cascade import VerificationCascade

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
    # Task 7 Cascade Components
    "VerificationCascade",
    "DeterministicVerifier",
    "LearnedVerifier",
    "StageCJudge",
    "StageAResult",
    "StageBResult",
    "StageCResult",
    "StageCJudgeOutput",
    "MockModelPredictor",
    "DefaultMockLLMJudge",
]
