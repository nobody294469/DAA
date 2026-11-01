"""Comprehensive test battery for Phase 4.1: Observation & Terminology Hardening.

Authority:
  - Phase 4.1 Scope & Acceptance Gates
  - Decisions DEC-008, DEC-015, DEC-016, and Proposed DEC-020, DEC-021, DEC-022
  - HL7 FHIR R4B Observation, Dosage, and Interpretation semantics
  - SNOMED CT and HL7 v3 ObservationInterpretation standards
"""

from decimal import Decimal
import json
import pytest

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.dosage import Dosage
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.observation import Observation

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir import FHIRValidator, claims_to_fhir_bundle
from clindoc.fhir.constants import (
    EXT_CLAIM_PROVENANCE,
    INTERPRETATION_HL7_MAP,
    LOINC_SYSTEM,
    OBS_CATEGORY_SYSTEM,
    OBS_INTERPRETATION_SYSTEM,
    QUALITATIVE_SNOMED_MAP,
    SNOMED_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.schema.verification import VerificationResult
from clindoc.services.fhir_export import FHIRExportService


# ─── FIXTURES & HELPERS ───────────────────────────────────────────────────────

def _make_evidence(
    ev_id: str = "ev-h-1",
    doc_id: str = "DOC-H-001",
    text: str = "Evidence text",
) -> Evidence:
    return Evidence(
        evidence_id=ev_id,
        document_id=doc_id,
        page_number=1,
        text=text,
        char_start=0,
        char_end=len(text),
        bbox=BBoxModel(x0=10.0, y0=20.0, x1=150.0, y1=35.0),
        ocr_confidence=0.99,
    )


def _make_verification(
    claim_id: str,
    ev_id: str = "ev-h-1",
    label: str = "SUPPORTED",
    confidence: float = 0.98,
) -> VerificationResult:
    return VerificationResult(
        claim_id=claim_id,
        label=label,
        confidence=confidence,
        stage_reached="A",
        evidence_id=ev_id,
    )


def _make_claim(
    claim_id: str,
    field_type: str,
    value: str,
    value_numeric: float | None = None,
    unit_raw: str | None = None,
    normalized_code: str | None = None,
    code_system: str | None = None,
    negated: bool = False,
    temporal_status: str = "current",
    group_id: str = "grp-h-1",
    evidence_id: str = "ev-h-1",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        document_id="DOC-H-001",
        group_id=group_id,
        field_type=field_type,
        value=value,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        normalized_code=normalized_code,
        code_system=code_system,
        negated=negated,
        temporal_status=temporal_status,
        extraction_method="deterministic",
        extraction_confidence=0.99,
        evidence_id=evidence_id,
        region_id="R1",
    )


# ─── 1. QUALITATIVE LABORATORY VALUES & SNOMED CT ────────────────────────────

@pytest.mark.parametrize(
    "key,raw_value,expected_snomed_code,expected_display",
    [
        ("NEGATIVE", "Negative", "260385009", "Negative (qualifier value)"),
        ("NEGATIVE", "neg", "260385009", "Negative (qualifier value)"),
        ("POSITIVE", "Positive", "10828004", "Positive (qualifier value)"),
        ("NOT_DETECTED", "not detected", "260415000", "Not detected (qualifier value)"),
        ("DETECTED", "Detected", "260373001", "Detected (qualifier value)"),
        ("NO_GROWTH", "no growth", "264868006", "No growth (qualifier value)"),
        ("REACTIVE", "Reactive", "11214006", "Reactive (qualifier value)"),
        ("NON_REACTIVE", "Non-Reactive", "131194007", "Non-Reactive (qualifier value)"),
        ("EQUIVOCAL", "Equivocal", "42425007", "Equivocal (qualifier value)"),
        ("INDETERMINATE", "Indeterminate", "82334004", "Indeterminate (qualifier value)"),
        ("TRACE", "Trace", "1511000179104", "Trace amount (qualifier value)"),
    ],
)
def test_all_supported_snomed_qualitative_mappings(key, raw_value, expected_snomed_code, expected_display):
    """Verify that all approved SNOMED CT qualitative determinations map to Observation.valueCodeableConcept."""
    ev = _make_evidence(text=f"Analyte: {raw_value}")
    claim_val = _make_claim(
        claim_id=f"c-val-{key}",
        field_type="lab.value",
        value=raw_value,
        normalized_code=key,
        code_system=None,
    )
    claim_name = _make_claim(
        claim_id=f"c-name-{key}",
        field_type="lab.test_name",
        value="Screening Test",
        normalized_code="1234-5",
        code_system="LOINC",
    )
    ev_map = {ev.evidence_id: ev}
    ver_map = {
        claim_val.claim_id: _make_verification(claim_val.claim_id, ev_id=ev.evidence_id),
        claim_name.claim_id: _make_verification(claim_name.claim_id, ev_id=ev.evidence_id),
    }

    bundle = claims_to_fhir_bundle([claim_val, claim_name], ev_map, ver_map)
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Single-choice value[x] invariant
    assert obs.valueCodeableConcept is not None
    assert obs.valueQuantity is None
    assert obs.valueString is None

    # Coding checks
    coding = obs.valueCodeableConcept.coding[0]
    assert coding.system == SNOMED_SYSTEM
    assert coding.code == expected_snomed_code
    assert coding.display == expected_display
    assert obs.valueCodeableConcept.text == raw_value

    # Structural validation
    val_res = FHIRValidator.validate_bundle(bundle)
    assert val_res.is_valid, f"Bundle validation failed: {val_res.errors}"


def test_unmapped_qualitative_safe_fallback_to_valuestring():
    """Unrecognized qualitative determinations must safely fall back to valueString with NO fabricated coding."""
    ev = _make_evidence(text="Urinalysis: moderate amorphous crystals")
    claim_val = _make_claim(
        claim_id="c-val-unmapped",
        field_type="lab.value",
        value="moderate amorphous crystals",
        normalized_code=None,
        code_system=None,
    )
    ev_map = {ev.evidence_id: ev}
    ver_map = {claim_val.claim_id: _make_verification(claim_val.claim_id, ev_id=ev.evidence_id)}

    bundle = claims_to_fhir_bundle([claim_val], ev_map, ver_map)
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Must emit valueString without valueCodeableConcept or valueQuantity
    assert obs.valueString == "moderate amorphous crystals"
    assert obs.valueCodeableConcept is None
    assert obs.valueQuantity is None

    val_res = FHIRValidator.validate_bundle(bundle)
    assert val_res.is_valid, f"Bundle validation failed: {val_res.errors}"


def test_completed_negative_results_remain_valid_and_not_negated():
    """Verify that 'Troponin was negative' and 'COVID-19 not detected' have negated == False and emit valid Observations."""
    ev_trop = _make_evidence(ev_id="ev-trop", text="Troponin was negative.")
    ev_cov = _make_evidence(ev_id="ev-cov", text="COVID-19 was not detected.")

    claims_trop = [
        _make_claim("c-trop-name", "lab.test_name", "Troponin", group_id="grp-trop", evidence_id="ev-trop"),
        _make_claim("c-trop-val", "lab.value", "negative", normalized_code="NEGATIVE", group_id="grp-trop", evidence_id="ev-trop", negated=False),
    ]
    claims_cov = [
        _make_claim("c-cov-name", "lab.test_name", "COVID-19 PCR", group_id="grp-cov", evidence_id="ev-cov"),
        _make_claim("c-cov-val", "lab.value", "not detected", normalized_code="NOT_DETECTED", group_id="grp-cov", evidence_id="ev-cov", negated=False),
    ]

    ev_map = {"ev-trop": ev_trop, "ev-cov": ev_cov}
    ver_map = {
        "c-trop-name": _make_verification("c-trop-name", ev_id="ev-trop"),
        "c-trop-val": _make_verification("c-trop-val", ev_id="ev-trop"),
        "c-cov-name": _make_verification("c-cov-name", ev_id="ev-cov"),
        "c-cov-val": _make_verification("c-cov-val", ev_id="ev-cov"),
    }

    bundle = claims_to_fhir_bundle(claims_trop + claims_cov, ev_map, ver_map)
    observations = [e.resource for e in bundle.entry if isinstance(e.resource, Observation)]

    assert len(observations) == 2, "Both completed negative results must produce valid Observations"
    obs_trop = next(o for o in observations if o.code.text == "Troponin")
    assert obs_trop.valueCodeableConcept.coding[0].code == "260385009"

    obs_cov = next(o for o in observations if o.code.text == "COVID-19 PCR")
    assert obs_cov.valueCodeableConcept.coding[0].code == "260415000"


# ─── 2. NEGATION SAFETY ──────────────────────────────────────────────────────

def test_negated_claim_cannot_generate_observation():
    """Verify that 'No evidence of elevated troponin' (negated == True) NEVER emits an Observation."""
    ev = _make_evidence(text="No evidence of elevated troponin.")
    claims = [
        _make_claim(
            claim_id="c-neg-name",
            field_type="lab.test_name",
            value="Troponin",
            negated=True,
            group_id="grp-neg",
            evidence_id=ev.evidence_id,
        ),
        _make_claim(
            claim_id="c-neg-flag",
            field_type="lab.flag",
            value="HIGH",
            negated=True,
            group_id="grp-neg",
            evidence_id=ev.evidence_id,
        ),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {
        "c-neg-name": _make_verification("c-neg-name", ev_id=ev.evidence_id),
        "c-neg-flag": _make_verification("c-neg-flag", ev_id=ev.evidence_id),
    }

    # 1. Enforcement at FHIR mapping boundary
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    observations = [e.resource for e in bundle.entry if isinstance(e.resource, Observation)]
    assert len(observations) == 0, "A claim group with negated == True must NEVER generate an Observation!"

    # 2. Enforcement at FHIRExportService eligibility gate
    eligibility_name = FHIRExportService.evaluate_claim_eligibility(claims[0], ver_map["c-neg-name"], ev)
    assert eligibility_name.is_eligible is False
    assert "clinically negated" in eligibility_name.reason

    eligibility_flag = FHIRExportService.evaluate_claim_eligibility(claims[1], ver_map["c-neg-flag"], ev)
    assert eligibility_flag.is_eligible is False
    assert "clinically negated" in eligibility_flag.reason


# ─── 3. LABORATORY INTERPRETATION FLAGS ──────────────────────────────────────

@pytest.mark.parametrize(
    "raw_flag,expected_hl7_code,expected_hl7_display",
    [
        ("H", "H", "High"),
        ("HIGH", "H", "High"),
        ("HH", "HH", "Critical high"),
        ("CRITICAL HIGH", "HH", "Critical high"),
        ("PANIC HIGH", "HH", "Critical high"),
        ("L", "L", "Low"),
        ("LOW", "L", "Low"),
        ("LL", "LL", "Critical low"),
        ("CRITICAL LOW", "LL", "Critical low"),
        ("PANIC LOW", "LL", "Critical low"),
        ("A", "A", "Abnormal"),
        ("ABNORMAL", "A", "Abnormal"),
        ("AA", "AA", "Critical abnormal"),
        ("CRITICAL ABNORMAL", "AA", "Critical abnormal"),
        ("N", "N", "Normal"),
        ("NORMAL", "N", "Normal"),
        ("WNL", "N", "Normal"),
        ("IND", "IND", "Indeterminate"),
        ("INDETERMINATE", "IND", "Indeterminate"),
    ],
)
def test_all_supported_hl7_interpretation_mappings(raw_flag, expected_hl7_code, expected_hl7_display):
    """Verify that all approved flags map to official HL7 v3 ObservationInterpretation."""
    ev = _make_evidence(text=f"Potassium 6.8 mmol/L ({raw_flag})")
    claims = [
        _make_claim("c-val", "lab.value", "6.8", value_numeric=6.8, unit_raw="mmol/L"),
        _make_claim("c-flag", "lab.flag", raw_flag),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {
        "c-val": _make_verification("c-val", ev_id=ev.evidence_id),
        "c-flag": _make_verification("c-flag", ev_id=ev.evidence_id),
    }

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    assert obs.interpretation is not None
    assert len(obs.interpretation) == 1
    interp = obs.interpretation[0]
    assert interp.text == raw_flag
    assert len(interp.coding) == 1
    assert interp.coding[0].system == OBS_INTERPRETATION_SYSTEM
    assert interp.coding[0].code == expected_hl7_code
    assert interp.coding[0].display == expected_hl7_display


def test_unrecognized_interpretation_flag_safe_text_only_fallback():
    """Unrecognized flags must use text-only CodeableConcept with NO fabricated coding."""
    ev = _make_evidence(text="Result: OUT OF SPECIFICATION")
    claims = [
        _make_claim("c-val", "lab.value", "10.0", value_numeric=10.0),
        _make_claim("c-flag", "lab.flag", "OUT OF SPECIFICATION"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {
        "c-val": _make_verification("c-val", ev_id=ev.evidence_id),
        "c-flag": _make_verification("c-flag", ev_id=ev.evidence_id),
    }

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    assert obs.interpretation is not None
    interp = obs.interpretation[0]
    assert interp.text == "OUT OF SPECIFICATION"
    assert interp.coding is None, "Unrecognized flag must have coding=None (no fabricated codes!)"


# ─── 4. MEDICATION DOSE SAFETY ───────────────────────────────────────────────

def test_medication_valid_numeric_dose():
    """Numeric doses map cleanly to doseQuantity with Decimal value and UCUM units."""
    ev = _make_evidence(text="Metformin 500 mg oral daily")
    claims = [
        _make_claim("c-med-name", "med.name", "Metformin", normalized_code="6809", code_system="RxNorm"),
        _make_claim("c-med-dose", "med.dose", "500", value_numeric=500.0, unit_raw="mg"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {
        "c-med-name": _make_verification("c-med-name", ev_id=ev.evidence_id),
        "c-med-dose": _make_verification("c-med-dose", ev_id=ev.evidence_id),
    }

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    med_req = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    dosage = med_req.dosageInstruction[0]
    assert dosage.doseAndRate is not None
    dose_qty = dosage.doseAndRate[0].doseQuantity
    assert float(dose_qty.value) == 500.0
    assert dose_qty.unit == "mg"
    assert dose_qty.system == UCUM_SYSTEM


def test_medication_genuine_zero_dose():
    """Genuine zero dose stated in source text ('0 mg') is faithfully preserved."""
    ev = _make_evidence(text="Prednisone taper: 0 mg on day 14")
    claims = [
        _make_claim("c-med-name", "med.name", "Prednisone"),
        _make_claim("c-med-dose", "med.dose", "0 mg", value_numeric=0.0, unit_raw="mg"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {
        "c-med-name": _make_verification("c-med-name", ev_id=ev.evidence_id),
        "c-med-dose": _make_verification("c-med-dose", ev_id=ev.evidence_id),
    }

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    med_req = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    dosage = med_req.dosageInstruction[0]
    assert dosage.doseAndRate is not None
    dose_qty = dosage.doseAndRate[0].doseQuantity
    assert float(dose_qty.value) == 0.0
    assert dose_qty.unit == "mg"


@pytest.mark.parametrize(
    "unparseable_dose",
    [
        "one puff",
        "two sprays",
        "sliding scale",
        "titrate to effect",
        "as directed",
        "apply thin layer",
    ],
)
def test_medication_unparseable_dose_omits_dose_quantity_and_preserves_text(unparseable_dose):
    """Unparseable doses MUST omit doseQuantity (never emit 0.0) and preserve text in Dosage.text."""
    ev = _make_evidence(text=f"Albuterol {unparseable_dose} inhalation every 4 hours")
    claims = [
        _make_claim("c-med-name", "med.name", "Albuterol"),
        _make_claim("c-med-dose", "med.dose", unparseable_dose, value_numeric=None),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {
        "c-med-name": _make_verification("c-med-name", ev_id=ev.evidence_id),
        "c-med-dose": _make_verification("c-med-dose", ev_id=ev.evidence_id),
    }

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    med_req = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    dosage = med_req.dosageInstruction[0]

    # doseAndRate MUST be None: NO doseQuantity = 0.0!
    assert dosage.doseAndRate is None, f"doseAndRate must be None for unparseable dose '{unparseable_dose}'"

    # Verbatim dose instruction preserved in Dosage.text
    assert unparseable_dose in dosage.text

    # Check JSON serialization to guarantee no fabricated 0.0
    med_json = json.loads(med_req.json())
    dosage_json = med_json["dosageInstruction"][0]
    assert "doseAndRate" not in dosage_json, "doseAndRate must not appear in FHIR JSON for unparseable dose"


# ─── 5. PROVENANCE & VALUE[X] INTEGRITY ──────────────────────────────────────

def test_element_provenance_and_single_choice_invariant():
    """Verify that element-level provenance is preserved and Observation.value[x] has exactly one choice."""
    ev = _make_evidence(text="Glucose: 95 mg/dL")
    claims = [
        _make_claim("c-val", "lab.value", "95", value_numeric=95.0, unit_raw="mg/dL"),
        _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {
        "c-val": _make_verification("c-val", ev_id=ev.evidence_id),
        "c-name": _make_verification("c-name", ev_id=ev.evidence_id),
    }

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Single-choice invariant
    assert obs.valueQuantity is not None
    assert obs.valueCodeableConcept is None
    assert obs.valueString is None

    # Element provenance on valueQuantity
    assert obs.valueQuantity.extension is not None
    vq_ext = next(ext for ext in obs.valueQuantity.extension if ext.url == EXT_CLAIM_PROVENANCE)
    sub_map = {s.url: s for s in vq_ext.extension}
    assert sub_map["claimId"].valueString == "c-val"
    assert sub_map["verificationLabel"].valueCode == "SUPPORTED"
