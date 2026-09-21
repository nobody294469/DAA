"""Ground-truth data models for synthetic clinical laboratory reports."""

from typing import Literal
from pydantic import BaseModel, Field


class LabTestResult(BaseModel):
    """Ground truth representation of an individual laboratory analyte result."""

    test_name: str = Field(..., description="Canonical clinical name of the test/analyte")
    loinc_code: str = Field(..., description="LOINC code for the test")
    value: float = Field(..., description="Observed numerical value")
    unit: str = Field(..., description="Measurement unit (UCUM-aligned)")
    ref_range_low: float = Field(..., description="Lower bound of reference interval")
    ref_range_high: float = Field(..., description="Upper bound of reference interval")
    ref_range_text: str = Field(..., description="Formatted reference interval string as displayed")
    flag: Literal["NORMAL", "HIGH", "LOW"] = Field(..., description="Interpretation flag")
    panel_name: str = Field(..., description="Name of the laboratory panel")


class PatientInfo(BaseModel):
    """Synthetic patient demographic info."""

    patient_id: str = Field(..., description="Unique synthetic patient identifier")
    name: str = Field(..., description="Patient full name")
    dob: str = Field(..., description="Date of birth (YYYY-MM-DD)")
    gender: Literal["M", "F"] = Field(..., description="Administrative gender")


class LabReportGroundTruth(BaseModel):
    """Complete ground truth record for a synthetic laboratory report document."""

    doc_id: str = Field(..., description="Unique document ID, e.g. LAB_0001")
    patient: PatientInfo
    report_date: str = Field(..., description="Date of the laboratory report (YYYY-MM-DD)")
    ordering_provider: str = Field(..., description="Ordering physician name")
    facility_name: str = Field(..., description="Performing laboratory or healthcare facility")
    template_id: str = Field(..., description="Identifier for layout template used")
    tests: list[LabTestResult] = Field(..., description="List of ground-truth test results")
