"""Intermediate schema for extracted medication regimens and prescription documents."""

from typing import Any
from pydantic import BaseModel, Field

from clindoc.schema.evidence import SourceEvidence
from clindoc.schema.lab import ExtractedPatientInfo


class ExtractedMedication(BaseModel):
    """Normalized intermediate representation of an individual medication regimen."""

    medication_name: str = Field(..., description="Verbatim extracted medication name")
    dose: float | None = Field(default=None, description="Extracted numeric dose value")
    dose_unit: str | None = Field(default=None, description="Dose unit (e.g. mg, mcg, mL)")
    dosage_form: str | None = Field(default=None, description="Dosage form (e.g. tablet, capsule, inhaler)")
    route: str | None = Field(default=None, description="Administration route (e.g. oral, inhalation, topical)")
    frequency: str | None = Field(default=None, description="Dosing frequency (e.g. once daily, BID, TID, every 8 hours)")
    duration: str | None = Field(default=None, description="Treatment duration (e.g. 5 days, 10 days, 30 days)")
    quantity: int | None = Field(default=None, description="Dispense quantity")
    refills: int | None = Field(default=None, description="Authorized refills count")
    sig: str = Field(..., description="Full clinical instruction or direction string")
    normalized_name: str | None = Field(default=None, description="Canonical normalized drug name")
    rxcui: str | None = Field(default=None, description="Mapped RxNorm Concept Unique Identifier")
    source_evidence: SourceEvidence = Field(..., description="Traceable source evidence line/snippet")
    source_model: str = Field(default="gliner_biomed_parser", description="Extractor model identifier")


class ExtractedPrescriptionDocument(BaseModel):
    """Normalized representation of a complete extracted medication/prescription document."""

    doc_id: str = Field(..., description="Document identifier matching source file")
    patient: ExtractedPatientInfo | None = Field(default=None, description="Patient demographic details")
    date: str | None = Field(default=None, description="Prescription or dispense date (YYYY-MM-DD)")
    prescriber: str | None = Field(default=None, description="Ordering clinician or prescriber name")
    facility: str | None = Field(default=None, description="Clinic or dispensing pharmacy name")
    template_type: str | None = Field(default=None, description="Document layout/template type")
    medications: list[ExtractedMedication] = Field(default_factory=list, description="Extracted medication regimens")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Extraction runtime metadata")
