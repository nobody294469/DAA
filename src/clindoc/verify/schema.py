"""Schemas for verification results, discrepancies, and error injection records."""

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class VerificationStatus(str, Enum):
    """Status of observation or field verification."""

    VERIFIED = "VERIFIED"
    DISCREPANCY = "DISCREPANCY"
    MISSING_SOURCE_EVIDENCE = "MISSING_SOURCE_EVIDENCE"
    UNCHECKED = "UNCHECKED"


class FieldDiscrepancy(BaseModel):
    """Machine-readable record of an individual field or observation discrepancy."""

    doc_id: str = Field(..., description="Document identifier")
    observation: str = Field(..., description="Analyte or test name")
    field: str = Field(..., description="Field name (value, unit, test_name, ref_range, flag, loinc, observation_presence)")
    extracted_value: Any = Field(..., description="Value extracted by pipeline")
    source_value: Any = Field(..., description="Value grounded in source evidence")
    source_evidence_text: str = Field(..., description="Verbatim source snippet or line")
    page: int = Field(..., ge=1, description="Page number")
    bbox: list[float] = Field(..., description="Bounding box [x0, y0, x1, y1]")
    ocr_confidence: float = Field(..., ge=0.0, le=1.0, description="OCR confidence of evidence")
    reason: str = Field(..., description="Machine-readable discrepancy code (e.g. VALUE_MISMATCH)")


class ObservationVerificationResult(BaseModel):
    """Verification outcome for an individual observation."""

    test_name: str
    status: VerificationStatus
    discrepancies: list[FieldDiscrepancy] = Field(default_factory=list)


class ReportVerificationResult(BaseModel):
    """Complete document-level verification output."""

    doc_id: str
    overall_status: VerificationStatus
    total_observations: int
    verified_observations: int
    discrepancy_count: int
    observation_results: list[ObservationVerificationResult] = Field(default_factory=list)
    discrepancies: list[FieldDiscrepancy] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InjectedErrorRecord(BaseModel):
    """Record of a controlled synthetic error injected into an extracted report."""

    doc_id: str
    observation: str
    field: str
    original_value: Any
    corrupted_value: Any
    error_type: Literal[
        "VALUE_CORRUPTION",
        "UNIT_CORRUPTION",
        "TEST_NAME_CORRUPTION",
        "REF_RANGE_CORRUPTION",
        "FLAG_CORRUPTION",
        "LOINC_CORRUPTION",
        "DROPPED_OBSERVATION",
        "HALLUCINATED_OBSERVATION",
    ]
