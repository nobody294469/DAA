"""Pydantic schemas for synthetic medication prescription ground-truth records."""

from pydantic import BaseModel, Field
from clindoc.synth.schema import PatientInfo


class MedicationGroundTruth(BaseModel):
    """Ground-truth definition of an individual medication regimen item."""

    medication_name: str = Field(..., description="Medication or brand name as printed")
    dose: float | None = Field(default=None, description="Numeric dose value")
    dose_unit: str | None = Field(default=None, description="Dose unit (e.g. mg, mcg, mL)")
    dosage_form: str | None = Field(default=None, description="Dosage form (e.g. tablet, capsule, inhaler)")
    route: str | None = Field(default=None, description="Administration route (e.g. oral, inhalation, topical)")
    frequency: str | None = Field(default=None, description="Dosing frequency wording")
    duration: str | None = Field(default=None, description="Duration of therapy")
    quantity: int | None = Field(default=None, description="Dispensed quantity")
    refills: int | None = Field(default=None, description="Number of refills authorized")
    sig_text: str = Field(..., description="Verbatim signature/directions printed on document")
    canonical_name: str = Field(..., description="Normalized drug concept name")
    rxcui: str | None = Field(default=None, description="Target RxNorm Concept Unique Identifier")


class PrescriptionGroundTruth(BaseModel):
    """Complete ground-truth record for a synthetic prescription/medication document."""

    doc_id: str = Field(..., description="Unique document ID (e.g. MED_0001)")
    template_type: str = Field(..., description="Layout template identifier")
    facility_name: str = Field(..., description="Prescribing facility or pharmacy name")
    prescriber: str = Field(..., description="Prescribing clinician name")
    prescription_date: str = Field(..., description="Prescription or order date (YYYY-MM-DD)")
    patient: PatientInfo = Field(..., description="Synthetic patient demographic info")
    medications: list[MedicationGroundTruth] = Field(..., min_length=1, description="Medication regimens")
