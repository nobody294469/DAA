"""M2 schema definitions for learned clinical claim/evidence verification."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class VerificationLabel(str, Enum):
    """3-way verification label for M2."""
    SUPPORTED = "SUPPORTED"
    DISCREPANCY = "DISCREPANCY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


LABEL_ORDER: list[str] = [
    VerificationLabel.SUPPORTED.value,
    VerificationLabel.DISCREPANCY.value,
    VerificationLabel.INSUFFICIENT_EVIDENCE.value,
]


class FieldType(str, Enum):
    """Enumeration of clinical field types used as auxiliary features."""
    LAB_VALUE = "lab.value"
    LAB_UNIT = "lab.unit"
    LAB_TEST_NAME = "lab.test_name"
    LAB_REF_RANGE = "lab.ref_range"
    LAB_FLAG = "lab.flag"
    LAB_LOINC_CODE = "lab.loinc_code"
    MED_NAME = "med.name"
    MED_DOSE = "med.dose"
    MED_DOSE_UNIT = "med.dose_unit"
    MED_ROUTE = "med.route"
    MED_FREQUENCY = "med.frequency"
    MED_DURATION = "med.duration"
    MED_QUANTITY = "med.quantity"


FIELD_TYPE_ORDER: list[str] = [f.value for f in FieldType]

DOC_TEMPLATE_ORDER: list[str] = [
    "template_a", "template_b", "template_c",
    "clinic_rx_pad", "discharge_summary", "pharmacy_label", "mar_order_sheet",
]

OOD_MED_TEMPLATE = "mar_order_sheet"
OOD_LAB_TEMPLATE = "template_c"

AUX_DIM = 1 + len(FIELD_TYPE_ORDER) + len(DOC_TEMPLATE_ORDER)  # 21
HIDDEN_DIM = 768
AUX_PROJ_DIM = 64
FUSION_DIM = HIDDEN_DIM + AUX_PROJ_DIM  # 832
NUM_LABELS = 3


class M2Example(BaseModel):
    example_id: str
    claim: str
    evidence: str
    field_type: str
    doc_template: str
    ocr_confidence: float = Field(..., ge=0.0, le=1.0)
    severity: Literal["clean", "low", "medium", "high"]
    label: VerificationLabel
    source_doc_id: str
    generation_method: str
    claim_type: Literal["lab", "med"]
    split: str = "unassigned"


class M2DatasetMetadata(BaseModel):
    total_examples: int
    label_counts: dict[str, int]
    label_percentages: dict[str, float]
    by_severity: dict[str, dict[str, int]]
    by_field_type: dict[str, dict[str, int]]
    by_doc_template: dict[str, dict[str, int]]
    by_generation_method: dict[str, dict[str, int]]
    by_claim_type: dict[str, dict[str, int]]
    ood_doc_ids: list[str]
    cv_doc_ids: list[str]


class M2ModelConfig(BaseModel):
    model_id: str = "thomas-sounack/BioClinical-ModernBERT-base"
    num_labels: int = NUM_LABELS
    max_seq_length: int = 256
    encoder_lr: float = 2e-5
    head_lr: float = 1e-4
    weight_decay: float = 0.01
    warmup_fraction: float = 0.06
    max_epochs: int = 10
    early_stopping_patience: int = 3
    batch_size: int = 16
    seeds: list[int] = Field(default_factory=lambda: [42, 43, 44])
    freeze_lower_n_layers: int = 0
    frozen_variant_n_layers: int = 8
    use_aux: bool = True
    aux_dim: int = AUX_DIM
    aux_proj_dim: int = AUX_PROJ_DIM


class PerClassMetrics(BaseModel):
    precision: float
    recall: float
    f1: float
    support: int


class M2EvalResult(BaseModel):
    model_name: str
    split: Literal["cv", "ood"]
    fold: int | None = None
    seed: int | None = None
    macro_f1: float
    discrepancy_recall: float
    per_class: dict[str, PerClassMetrics]
    confusion_matrix: list[list[int]]
    auroc_discrepancy: float | None = None
    brier_score: float | None = None
    ece: float | None = None
    by_severity: dict[str, Any] = Field(default_factory=dict)
    by_field_type: dict[str, Any] = Field(default_factory=dict)
    by_claim_type: dict[str, Any] = Field(default_factory=dict)
