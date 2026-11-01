"""Phase 4.4.2 — Final FHIR Export Integrity Audit Test Suite.

Verifies:
1. Lifecycle independence between DiagnosticReport and child Observations:
   - Partial report with preliminary observations.
   - Partial report with individually final observations.
   - Partial report with mixed preliminary and final observations.
   - Child observation with unknown/insufficient lifecycle evidence inside partial report.
2. Terminology semantic correctness:
   - LOINC code attached strictly to Observation.code, never numeric value.
   - Numeric results attached to Observation.valueQuantity.value.
   - UCUM units attached to Observation.valueQuantity.code and http://unitsofmeasure.org.
   - RxNorm medication codes attached to medicationCodeableConcept.
   - SNOMED CT qualitative results attached to Observation.valueCodeableConcept.
   - HL7 interpretation codes attached to Observation.interpretation.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.observation import Observation

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir.constants import (
    LOINC_SYSTEM,
    OBS_INTERPRETATION_SYSTEM,
    RXNORM_SYSTEM,
    SNOMED_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.fhir.mapper import claims_to_fhir_bundle
from clindoc.fhir.validator import FHIRValidator
from clindoc.schema.verification import VerificationResult


# ─── FIXTURE HELPERS ─────────────────────────────────────────────────────────

def _make_bbox() -> BBoxModel:
    return BBoxModel(
        x0=Decimal("10.0"),
        y0=Decimal("10.0"),
        x1=Decimal("100.0"),
        y1=Decimal("20.0"),
    )


def _make_evidence(
    ev_id: str = "ev-442-1",
    doc_id: str = "DOC_442",
    text: str = "Test evidence text",
) -> Evidence:
    return Evidence(
        evidence_id=ev_id,
        document_id=doc_id,
        page_number=1,
        text=text,
        char_start=0,
        char_end=len(text),
        bbox=_make_bbox(),
    )


def _make_claim(
    claim_id: str,
    field_type: str,
    value: str,
    value_numeric: float | None = None,
    unit_raw: str | None = None,
    normalized_code: str | None = None,
    code_system: str | None = None,
    evidence_id: str = "ev-442-1",
    group_id: str = "grp-442",
    temporal_status: str = "current",
) -> Claim:
    c = Claim(
        claim_id=claim_id,
        document_id="DOC_442",
        group_id=group_id,
        field_type=field_type,
        value=value,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        extraction_method="deterministic",
        extraction_confidence=0.98,
        evidence_id=evidence_id,
        normalized_code=normalized_code,
        code_system=code_system,
        code_confidence=0.95,
        negated=False,
        temporal_status=temporal_status,
        region_id="reg-1",
    )
    object.__setattr__(c, "review_state", "ACCEPTED")
    return c


def _make_verification(claim_id: str, label: str = "SUPPORTED", ev_id: str = "ev-442-1") -> VerificationResult:
    return VerificationResult(
        claim_id=claim_id,
        label=label,
        confidence=0.95,
        stage_reached="A",
        evidence_id=ev_id,
    )


# ─── SECTION B: DIAGNOSTICREPORT & OBSERVATION LIFECYCLE INDEPENDENCE ────────

def test_partial_report_with_preliminary_observations():
    """Partial DiagnosticReport with observation whose own evidence indicates preliminary results."""
    ev_rep = _make_evidence("ev-rep", text="Comprehensive Metabolic Panel\nReport Status: Partial")
    ev_obs = _make_evidence("ev-obs", text="Blood Culture: Positive\nResult Status: Preliminary")

    claims = [
        _make_claim("c-val", "lab.value", "Positive", evidence_id="ev-obs", group_id="grp-obs"),
        _make_claim("c-name", "lab.test_name", "Blood Culture", normalized_code="600-7", code_system="LOINC", evidence_id="ev-obs", group_id="grp-obs"),
    ]
    ev_map = {"ev-rep": ev_rep, "ev-obs": ev_obs}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="laboratory_report",
        document_metadata={"report_status": "partial"},
    )

    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))
    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    assert diag.status == "partial"
    assert obs.status == "preliminary"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_partial_report_with_individually_final_observations():
    """Partial DiagnosticReport with child observation whose own evidence indicates a final/completed result."""
    ev_obs = _make_evidence("ev-obs", text="Sodium: 138 mmol/L (ref 135-145)\nResult Status: Final")

    claims = [
        _make_claim("c-val", "lab.value", "138", value_numeric=138.0, unit_raw="mmol/L", evidence_id="ev-obs", group_id="grp-obs"),
        _make_claim("c-name", "lab.test_name", "Sodium", normalized_code="2951-2", code_system="LOINC", evidence_id="ev-obs", group_id="grp-obs"),
    ]
    ev_map = {"ev-obs": ev_obs}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="laboratory_report",
        document_metadata={"report_status": "partial"},
    )

    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))
    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    assert diag.status == "partial"
    # Child observation status is determined independently from its own evidence
    assert obs.status == "final"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_partial_report_with_mixed_preliminary_and_final_observations():
    """Partial DiagnosticReport with mixed preliminary and final observations within the same panel."""
    ev_final = _make_evidence("ev-fin", text="Complete Blood Count\nHemoglobin: 14.2 g/dL\nResult Status: Final")
    ev_prelim = _make_evidence("ev-pre", text="Band Neutrophils: 12%\nResult Status: Preliminary")

    claims = [
        _make_claim("c-hgb-val", "lab.value", "14.2", value_numeric=14.2, unit_raw="g/dL", evidence_id="ev-fin", group_id="grp-fin"),
        _make_claim("c-hgb-name", "lab.test_name", "Hemoglobin", normalized_code="718-7", code_system="LOINC", evidence_id="ev-fin", group_id="grp-fin"),
        _make_claim("c-diff-val", "lab.value", "12", value_numeric=12.0, unit_raw="%", evidence_id="ev-pre", group_id="grp-pre"),
        _make_claim("c-diff-name", "lab.test_name", "Band Neutrophils", normalized_code="763-3", code_system="LOINC", evidence_id="ev-pre", group_id="grp-pre"),
    ]
    ev_map = {"ev-fin": ev_final, "ev-pre": ev_prelim}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="laboratory_report",
        document_metadata={"report_status": "partial"},
    )

    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))
    observations = [e.resource for e in bundle.entry if isinstance(e.resource, Observation)]
    assert len(observations) == 2
    assert diag.status == "partial"

    # Identify individual observations by code
    hgb_obs = next(o for o in observations if any(c.code == "718-7" for c in o.code.coding))
    diff_obs = next(o for o in observations if any(c.code == "763-3" for c in o.code.coding))

    assert hgb_obs.status == "final"
    assert diff_obs.status == "preliminary"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_partial_report_with_unknown_or_insufficient_lifecycle_evidence():
    """A partial DiagnosticReport does NOT automatically force child observations without lifecycle evidence to preliminary."""
    ev = _make_evidence("ev-unk", text="Routine Lab Findings:\nPotassium 4.2 mmol/L (ref 3.5-5.0)")
    claims = [
        _make_claim("c-val", "lab.value", "4.2", value_numeric=4.2, unit_raw="mmol/L", evidence_id="ev-unk", group_id="grp-unk"),
        _make_claim("c-name", "lab.test_name", "Potassium", normalized_code="2823-3", code_system="LOINC", evidence_id="ev-unk", group_id="grp-unk"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims},
        document_type="laboratory_report",
        document_metadata={"report_status": "partial"},
    )

    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))
    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    assert diag.status == "partial"
    # Lacking observation-level lifecycle evidence, status must remain unknown, NOT forced to preliminary
    assert obs.status == "unknown"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


# ─── SECTION C: TERMINOLOGY SEMANTIC CORRECTNESS ─────────────────────────────

def test_terminology_loinc_maps_to_observation_code_and_never_numeric_value():
    """LOINC codes must map strictly to Observation.code, numeric results to valueQuantity.value, UCUM to valueQuantity.code."""
    ev = _make_evidence(text="Hemoglobin A1c: 6.8 % (ref 4.0-5.6)")
    claims = [
        _make_claim("c-val", "lab.value", "6.8", value_numeric=6.8, unit_raw="%"),
        _make_claim("c-name", "lab.test_name", "Hemoglobin A1c", normalized_code="4548-4", code_system="LOINC"),
        _make_claim("c-unit", "lab.unit", "%", normalized_code="%", code_system="UCUM"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: _make_verification(c.claim_id) for c in claims},
        document_type="laboratory_report",
    )

    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # LOINC code attached strictly to Observation.code
    assert obs.code.coding is not None
    loinc_coding = next(c for c in obs.code.coding if c.system == LOINC_SYSTEM)
    assert loinc_coding.code == "4548-4"
    assert loinc_coding.display == "Hemoglobin A1c"

    # Numeric value attached strictly to Observation.valueQuantity.value
    assert obs.valueQuantity is not None
    assert float(obs.valueQuantity.value) == 6.8
    assert obs.valueQuantity.unit == "%"
    assert obs.valueQuantity.code == "%"
    assert obs.valueQuantity.system == UCUM_SYSTEM

    # LOINC code must NEVER be attached to valueQuantity
    assert obs.valueQuantity.code != "4548-4"

    # Semantic invariance: code system of valueQuantity is UCUM, not LOINC
    assert obs.valueQuantity.system != LOINC_SYSTEM


def test_terminology_rxnorm_maps_to_medication_codeable_concept():
    """RxNorm medication codes map strictly to MedicationStatement/Request.medicationCodeableConcept."""
    ev = _make_evidence(text="Discharge Medications:\nMetformin 500 mg orally once daily")
    claims = [
        _make_claim("c-med", "med.name", "Metformin", normalized_code="6809", code_system="RxNorm", group_id="grp-med"),
        _make_claim("c-dose", "med.dose", "500 mg", value_numeric=500.0, unit_raw="mg", group_id="grp-med"),
        _make_claim("c-route", "med.route", "orally", group_id="grp-med"),
        _make_claim("c-freq", "med.frequency", "once daily", group_id="grp-med"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: _make_verification(c.claim_id) for c in claims},
        document_type="discharge_summary",
    )

    med_stmt: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))

    assert med_stmt.medicationCodeableConcept.coding is not None
    rx_coding = next(c for c in med_stmt.medicationCodeableConcept.coding if c.system == RXNORM_SYSTEM)
    assert rx_coding.code == "6809"
    assert rx_coding.display == "Metformin"
    assert med_stmt.medicationCodeableConcept.text == "Metformin"

    # Dosage instruction verification
    assert med_stmt.dosage is not None and len(med_stmt.dosage) > 0
    d0 = med_stmt.dosage[0]
    assert d0.doseAndRate[0].doseQuantity.value == 500.0
    assert d0.doseAndRate[0].doseQuantity.unit == "mg"


def test_terminology_snomed_ct_qualitative_maps_to_value_codeable_concept():
    """SNOMED CT qualitative results map to Observation.valueCodeableConcept when appropriate."""
    ev = _make_evidence(text="COVID-19 Rapid Antigen: Negative")
    claims = [
        _make_claim("c-val", "lab.value", "Negative"),
        _make_claim("c-name", "lab.test_name", "SARS-CoV-2 Antigen", normalized_code="94558-4", code_system="LOINC"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: _make_verification(c.claim_id) for c in claims},
        document_type="laboratory_report",
    )

    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Qualitative result mapped to valueCodeableConcept
    assert obs.valueCodeableConcept is not None
    assert obs.valueQuantity is None
    snomed_coding = next(c for c in obs.valueCodeableConcept.coding if c.system == SNOMED_SYSTEM)
    assert snomed_coding.code == "260385009"
    assert "Negative" in snomed_coding.display


def test_terminology_hl7_interpretation_maps_to_observation_interpretation():
    """HL7 interpretation codes (H, L, etc.) map strictly to Observation.interpretation."""
    ev = _make_evidence(text="Potassium: 6.2 mmol/L (ref 3.5-5.0) [H]")
    claims = [
        _make_claim("c-val", "lab.value", "6.2", value_numeric=6.2, unit_raw="mmol/L"),
        _make_claim("c-name", "lab.test_name", "Potassium", normalized_code="2823-3", code_system="LOINC"),
        _make_claim("c-flag", "lab.flag", "HIGH"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: _make_verification(c.claim_id) for c in claims},
        document_type="laboratory_report",
    )

    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    assert obs.interpretation is not None and len(obs.interpretation) > 0
    interp_coding = next(c for c in obs.interpretation[0].coding if c.system == OBS_INTERPRETATION_SYSTEM)
    assert interp_coding.code == "H"
    assert interp_coding.display == "High"
