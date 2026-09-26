"""Data models and schemas for synthetic clinical narrative documents and ground truth.

Conforms to DEC-014 (Narrative Benchmark Synthesis Scope), DEC-015 (Claim Entity Grouping),
DEC-017 (Canonical Field Vocabulary), and DEC-003 / DR-3 (Context Window Policy).
"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

from clindoc.synth.schema import PatientInfo


class NarrativeClaimGroundTruth(BaseModel):
    """Ground truth for an atomic claim embedded within clinical narrative prose."""
    claim_id: str = Field(..., description="Globally unique identifier for the claim (UUID)")
    group_id: str = Field(..., description="Entity grouping identifier linking atomic claims (DEC-015)")
    field_type: str = Field(..., description="Canonical field type (DEC-017)")
    value: str = Field(..., description="Formatted claim value string")
    canonical_value: Any = Field(..., description="Typed ground-truth value (numeric, string, or flag)")
    unit: str | None = Field(default=None, description="Measurement or dose unit if applicable")
    evidence_id: str = Field(..., description="Globally unique identifier for the evidence span (UUID)")
    evidence_text: str = Field(..., description="Exact verbatim text span from document")
    char_start: int = Field(..., ge=0, description="Start offset in canonical page text")
    char_end: int = Field(..., ge=0, description="End offset in canonical page text")
    page_number: int = Field(default=1, ge=1, description="1-indexed page number")
    bbox: list[float] = Field(..., description="Enclosing bounding box [x0, y0, x1, y1]")
    contradiction_type: str | None = Field(
        default=None,
        description="Contradiction type if used as discrepancy: value_mismatch, dose_mismatch, wrong_entity, temporal_mismatch",
    )


class NarrativeLabEntity(BaseModel):
    """Ground-truth clinical entity for a laboratory analyte mentioned in prose."""
    group_id: str = Field(..., description="Unique entity group ID, e.g. NARRATIVE_0001_narr_lab_0")
    test_name: str = Field(..., description="Canonical laboratory test name")
    loinc_code: str = Field(..., description="LOINC code for test")
    value: float = Field(..., description="Numerical lab value")
    unit: str = Field(..., description="Measurement unit")
    ref_range_low: float = Field(..., description="Reference interval lower bound")
    ref_range_high: float = Field(..., description="Reference interval upper bound")
    ref_range_text: str = Field(..., description="Reference interval display text")
    flag: Literal["NORMAL", "HIGH", "LOW"] = Field(..., description="Interpretation flag")
    evidence_text: str = Field(..., description="Verbatim prose mention covering the lab finding")
    char_start: int = Field(default=-1, description="Start offset in canonical page text")
    char_end: int = Field(default=-1, description="End offset in canonical page text")
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0], description="Bounding box")
    page_number: int = Field(default=1, ge=1)


class NarrativeMedEntity(BaseModel):
    """Ground-truth clinical entity for a medication regimen mentioned in prose."""
    group_id: str = Field(..., description="Unique entity group ID, e.g. NARRATIVE_0001_narr_med_0")
    medication_name: str = Field(..., description="Medication concept name")
    dose: float = Field(..., description="Numeric dose amount")
    dose_unit: str = Field(default="mg", description="Dose unit")
    route: str = Field(..., description="Administration route (e.g. oral, PO, by mouth)")
    frequency: str = Field(..., description="Dosing frequency (e.g. once daily, twice daily)")
    duration: str | None = Field(default=None, description="Treatment duration if stated")
    sig_text: str = Field(..., description="Directions/sig phrase as stated in prose")
    evidence_text: str = Field(..., description="Verbatim prose mention covering the regimen")
    char_start: int = Field(default=-1, description="Start offset in canonical page text")
    char_end: int = Field(default=-1, description="End offset in canonical page text")
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0], description="Bounding box")
    page_number: int = Field(default=1, ge=1)


class NarrativeSection(BaseModel):
    """Clinical section in narrative document."""
    title: str = Field(..., description="Section title, e.g. HISTORY OF PRESENT ILLNESS")
    text: str = Field(..., description="Complete body text of the section")


class NarrativeDocumentGroundTruth(BaseModel):
    """Machine-readable ground truth record for a synthetic narrative clinical document."""
    doc_id: str = Field(..., description="Unique document identifier, e.g. NARRATIVE_0001")
    template_id: str = Field(..., description="Template identifier")
    template_version: str = Field(default="1.0", description="Template version")
    split: Literal["CV", "OOD"] = Field(..., description="Evaluation partition")
    patient: PatientInfo = Field(..., description="Synthetic patient demographic info")
    facility_name: str = Field(..., description="Clinical facility name")
    attending_physician: str = Field(..., description="Attending/consulting clinician")
    admission_date: str = Field(..., description="Encounter or admission date (YYYY-MM-DD)")
    discharge_date: str = Field(..., description="Encounter discharge or note date (YYYY-MM-DD)")
    sections: list[NarrativeSection] = Field(..., min_length=2, description="Narrative sections")
    lab_entities: list[NarrativeLabEntity] = Field(default_factory=list, description="Ground truth lab entities")
    med_entities: list[NarrativeMedEntity] = Field(default_factory=list, description="Ground truth med entities")
    claims: list[NarrativeClaimGroundTruth] = Field(default_factory=list, description="Ground truth atomic claims")
