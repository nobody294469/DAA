"""Intermediate data models for extracted clinical laboratory reports."""

from typing import Any, Literal
from pydantic import BaseModel, Field

from clindoc.schema.evidence import SourceEvidence


class ExtractedPatientInfo(BaseModel):
    """Extracted patient demographic and identity information."""

    patient_id: str | None = Field(default=None, description="Patient identifier")
    name: str | None = Field(default=None, description="Patient full name")
    dob: str | None = Field(default=None, description="Date of birth (YYYY-MM-DD)")
    gender: Literal["M", "F"] | None = Field(default=None, description="Administrative gender")


class ExtractedObservation(BaseModel):
    """Normalized intermediate representation of an individual laboratory observation."""

    test_name: str = Field(..., description="Canonical clinical name of the test/analyte")
    raw_value: str = Field(..., description="Raw string value from document")
    value: float = Field(..., description="Parsed numeric value")
    unit: str = Field(..., description="Extracted measurement unit")
    normalized_unit: str = Field(..., description="Normalized unit (UCUM-aligned)")
    ref_range_low: float | None = Field(default=None, description="Lower reference interval bound")
    ref_range_high: float | None = Field(default=None, description="Upper reference interval bound")
    ref_range_text: str = Field(..., description="Full reference interval string")
    abnormal_flag: Literal["NORMAL", "HIGH", "LOW"] = Field(..., description="Interpretation flag")
    loinc_code: str | None = Field(default=None, description="Inferred or mapped LOINC code")
    panel_name: str | None = Field(default=None, description="Panel name if identifiable")
    source_evidence: SourceEvidence = Field(..., description="Grounding evidence for this observation")
    source_model: str = Field(default="lab_row_parser", description="Extractor model/rule identifier")


class ExtractedLabReport(BaseModel):
    """Complete intermediate representation for an extracted clinical laboratory document."""

    doc_id: str = Field(..., description="Document ID matching source file")
    patient: ExtractedPatientInfo | None = Field(default=None, description="Extracted patient details")
    report_date: str | None = Field(default=None, description="Report date (YYYY-MM-DD)")
    ordering_provider: str | None = Field(default=None, description="Ordering clinician name")
    facility_name: str | None = Field(default=None, description="Facility / laboratory name")
    observations: list[ExtractedObservation] = Field(default_factory=list, description="Extracted laboratory observations")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Extraction runtime metadata")
