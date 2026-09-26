"""M2: Learned Clinical Claim/Evidence Verification package."""
from clindoc.m2.schema import (
    VerificationLabel, FieldType, M2Example, M2DatasetMetadata, M2ModelConfig,
    M2EvalResult, PerClassMetrics, LABEL_ORDER,
)
from clindoc.m2.generation import generate_m2_dataset
from clindoc.m2.splits import (
    build_splits, build_3way_cv_split, get_fold_examples, get_ood_examples,
    load_train_examples, load_calibration_examples, load_validation_examples,
    load_ood_examples, validate_3way_split,
)
from clindoc.m2.dataset import load_examples, save_examples, compute_metadata
from clindoc.m2.features import encode_aux_features, encode_aux_batch
from clindoc.m2.model import ClinicalVerifier, format_input
from clindoc.m2.metrics import macro_f1, discrepancy_recall, ece, brier_score

__all__ = [
    "VerificationLabel", "FieldType", "M2Example", "M2DatasetMetadata",
    "M2ModelConfig", "M2EvalResult", "PerClassMetrics", "LABEL_ORDER",
    "generate_m2_dataset", "build_splits", "build_3way_cv_split",
    "get_fold_examples", "get_ood_examples",
    "load_train_examples", "load_calibration_examples", "load_validation_examples",
    "load_ood_examples", "validate_3way_split",
    "load_examples", "save_examples", "compute_metadata",
    "encode_aux_features", "encode_aux_batch",
    "ClinicalVerifier", "format_input",
    "macro_f1", "discrepancy_recall", "ece", "brier_score",
]
