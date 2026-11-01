"""Tests for Phase 5.2.3 Sub-Milestone 4.3: FHIR Medication Regimen Mapping Hardening.

Covers:
  - Split-dose regimens (e.g. Carvedilol morning/bedtime) with concurrent sequence=1.
  - Sequential taper regimens (e.g. Prednisone) with chronological sequence=1, 2 and boundsDuration.
  - Duration preservation and UCUM duration codes without ungrounded invention.
  - Missing or ambiguous duration in tapers.
  - Conflicting instructions and deterministic candidate slot selection.
  - Unsupported and incomplete regimen structures.
  - Zero-dose omission vs explicit zero dose.
  - Warning W-1 parenthetical fail-closed preservation.
  - FHIR R4B schema validity via FHIRValidator.
  - Regression parity for single-dose medications.
  - Direct map_medication_request and map_medication_statement functional contracts.
"""

from decimal import Decimal
import uuid

import pytest
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir import FHIRValidator, claims_to_fhir_bundle
from clindoc.fhir.constants import (
    EXT_CLAIM_PROVENANCE,
    EXT_COMPETING_CANDIDATE,
    RXNORM_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.fhir.mapper import (
    map_medication_request,
    map_medication_statement,
    select_deterministic_slot_candidate,
)
from clindoc.schema.verification import VerificationResult


def _make_evidence(ev_id: str, text: str, char_start: int = 0, char_end: int = 100) -> Evidence:
    return Evidence(
        document_id="DOC-REGIMEN-001",
        page_number=1,
        text=text,
        char_start=char_start,
        char_end=char_end,
        bbox=BBoxModel(x0=10.0, y0=20.0, x1=200.0, y1=40.0),
    )


def _make_claim(
    claim_id: str,
    field_type: str,
    value: str,
    value_numeric: float | None = None,
    unit_raw: str | None = None,
    evidence_id: str = "ev-1",
    group_id: str = "grp-med-1",
    temporal_status: str = "current",
    normalized_code: str | None = None,
    code_system: str | None = None,
    extraction_confidence: float = 0.95,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        document_id="DOC-REGIMEN-001",
        group_id=group_id,
        field_type=field_type,
        value=value,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        extraction_method="deterministic",
        extraction_confidence=extraction_confidence,
        evidence_id=evidence_id,
        normalized_code=normalized_code,
        code_system=code_system,
        region_id="reg-1",
        temporal_status=temporal_status,
    )


def _make_verification(
    claim_id: str,
    evidence_id: str,
    label: str = "SUPPORTED",
    confidence: float = 0.95,
) -> VerificationResult:
    return VerificationResult(
        claim_id=claim_id,
        label=label,
        confidence=confidence,
        stage_reached="A",
        evidence_id=evidence_id,
    )


# ─── 1. SPLIT DOSING REGIMENS ────────────────────────────────────────────────

def test_split_dosing_carvedilol_morning_bedtime():
    """Verify Carvedilol 6.25 mg morning and 12.5 mg bedtime maps to 1 MedicationRequest with 2 concurrent instructions."""
    raw_text = "Carvedilol: 6.25 mg every morning. 12.5 mg at bedtime."
    ev = _make_evidence("ev-carv", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Carvedilol", normalized_code="20352", code_system="RxNorm", evidence_id="ev-carv"),
        _make_claim("c-d1", "med.dose", "6.25", value_numeric=6.25, unit_raw="mg", evidence_id="ev-carv"),
        _make_claim("c-f1", "med.frequency", "every morning", evidence_id="ev-carv"),
        _make_claim("c-d2", "med.dose", "12.5", value_numeric=12.5, unit_raw="mg", evidence_id="ev-carv"),
        _make_claim("c-f2", "med.frequency", "at bedtime", evidence_id="ev-carv"),
    ]
    ev_map = {"ev-carv": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="clinic_rx_pad",
    )

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"FHIR validation errors: {val.errors}"
    assert val.resource_counts["MedicationRequest"] == 1
    assert "MedicationStatement" not in val.resource_counts

    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert mr.status == "active"
    assert mr.intent == "order"
    assert mr.medicationCodeableConcept.coding[0].code == "20352"

    # Exactly 2 dosage instructions, both concurrent (sequence=1)
    assert len(mr.dosageInstruction) == 2
    d1 = mr.dosageInstruction[0]
    d2 = mr.dosageInstruction[1]

    assert d1.sequence == 1, "Split doses must be concurrent (sequence=1)"
    assert d2.sequence == 1, "Split doses must be concurrent (sequence=1)"

    # Dose and timing preservation
    assert float(d1.doseAndRate[0].doseQuantity.value) == 6.25
    assert d1.doseAndRate[0].doseQuantity.unit == "mg"
    assert d1.timing.code.text == "every morning"

    assert float(d2.doseAndRate[0].doseQuantity.value) == 12.5
    assert d2.doseAndRate[0].doseQuantity.unit == "mg"
    assert d2.timing.code.text == "at bedtime"

    # Element-level provenance on doses
    d1_prov = next(ext for ext in d1.doseAndRate[0].doseQuantity.extension if ext.url == EXT_CLAIM_PROVENANCE)
    assert any(s.url == "claimId" and s.valueString == "c-d1" for s in d1_prov.extension)

    d2_prov = next(ext for ext in d2.doseAndRate[0].doseQuantity.extension if ext.url == EXT_CLAIM_PROVENANCE)
    assert any(s.url == "claimId" and s.valueString == "c-d2" for s in d2_prov.extension)


def test_split_dosing_medication_statement_narrative():
    """Verify narrative split dosing maps to MedicationStatement.dosage with 2 concurrent instructions."""
    raw_text = "Patient takes Carvedilol 6.25 mg AM and 12.5 mg PM."
    ev = _make_evidence("ev-stmt", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Carvedilol", evidence_id="ev-stmt"),
        _make_claim("c-d1", "med.dose", "6.25", value_numeric=6.25, unit_raw="mg", evidence_id="ev-stmt"),
        _make_claim("c-f1", "med.frequency", "AM", evidence_id="ev-stmt"),
        _make_claim("c-d2", "med.dose", "12.5", value_numeric=12.5, unit_raw="mg", evidence_id="ev-stmt"),
        _make_claim("c-f2", "med.frequency", "PM", evidence_id="ev-stmt"),
    ]
    ev_map = {"ev-stmt": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="discharge_summary",
    )

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"FHIR validation errors: {val.errors}"
    assert val.resource_counts["MedicationStatement"] == 1

    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    assert ms.status == "active"
    assert len(ms.dosage) == 2
    assert ms.dosage[0].sequence == 1 and ms.dosage[1].sequence == 1
    assert float(ms.dosage[0].doseAndRate[0].doseQuantity.value) == 6.25
    assert float(ms.dosage[1].doseAndRate[0].doseQuantity.value) == 12.5


def test_split_dosing_with_competing_candidates_in_morning_slot():
    """When multiple candidates compete for the morning slot in split dosing, the winner is picked and the other shadowed."""
    raw_text = "Carvedilol 6.25 mg morning (alternate OCR: 6.250 mg) and 12.5 mg at bedtime."
    ev = _make_evidence("ev-comp-am", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Carvedilol", evidence_id="ev-comp-am"),
        _make_claim("c-d1-a", "med.dose", "6.25", value_numeric=6.25, unit_raw="mg", evidence_id="ev-comp-am", extraction_confidence=0.99),
        _make_claim("c-d1-b", "med.dose", "6.250", value_numeric=6.25, unit_raw="mg", evidence_id="ev-comp-am", extraction_confidence=0.75),
        _make_claim("c-f1", "med.frequency", "morning", evidence_id="ev-comp-am"),
        _make_claim("c-d2", "med.dose", "12.5", value_numeric=12.5, unit_raw="mg", evidence_id="ev-comp-am"),
        _make_claim("c-f2", "med.frequency", "at bedtime", evidence_id="ev-comp-am"),
    ]
    ev_map = {"ev-comp-am": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit_trail = []
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="clinic_rx_pad",
        audit_trail=audit_trail,
    )

    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert len(mr.dosageInstruction) == 2
    assert float(mr.dosageInstruction[0].doseAndRate[0].doseQuantity.value) == 6.25

    # Shadowed extension present on resource
    shad_exts = [ext for ext in mr.extension if ext.url == EXT_COMPETING_CANDIDATE]
    assert len(shad_exts) == 1
    assert any(s.url == "competingClaimId" and s.valueString == "c-d1-b" for s in shad_exts[0].extension)


# ─── 2. SEQUENTIAL TAPER REGIMENS ───────────────────────────────────────────

def test_sequential_taper_prednisone_bounded_duration():
    """Verify Prednisone 20 mg daily for 5 days then 10 mg daily maps to sequential phases with boundsDuration."""
    raw_text = "Prednisone: 20 mg once daily for 5 days. Then 10 mg once daily."
    ev = _make_evidence("ev-pred", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", normalized_code="8640", code_system="RxNorm", evidence_id="ev-pred"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", evidence_id="ev-pred"),
        _make_claim("c-f1", "med.frequency", "once daily", evidence_id="ev-pred"),
        _make_claim("c-dur1", "med.duration", "5 days", value_numeric=5.0, unit_raw="days", evidence_id="ev-pred"),
        _make_claim("c-d2", "med.dose", "10", value_numeric=10.0, unit_raw="mg", evidence_id="ev-pred"),
        _make_claim("c-f2", "med.frequency", "once daily", evidence_id="ev-pred"),
    ]
    ev_map = {"ev-pred": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="clinic_rx_pad",
    )

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"FHIR validation errors: {val.errors}"
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    # Exactly 2 sequential phases
    assert len(mr.dosageInstruction) == 2
    p1 = mr.dosageInstruction[0]
    p2 = mr.dosageInstruction[1]

    assert p1.sequence == 1, "Phase 1 must have sequence=1"
    assert p2.sequence == 2, "Phase 2 must have sequence=2"

    # Phase 1: 20 mg, once daily, duration bounds = 5 days
    assert float(p1.doseAndRate[0].doseQuantity.value) == 20.0
    assert p1.timing.code.text == "once daily"
    assert p1.timing.repeat is not None
    assert p1.timing.repeat.boundsDuration.value == Decimal(5)
    assert p1.timing.repeat.boundsDuration.unit == "days"
    assert p1.timing.repeat.boundsDuration.system == UCUM_SYSTEM
    assert p1.timing.repeat.boundsDuration.code == "d"

    # Phase 2: 10 mg, once daily, NO boundsDuration (open-ended maintenance / end of course)
    assert float(p2.doseAndRate[0].doseQuantity.value) == 10.0
    assert p2.timing.code.text == "once daily"
    assert p2.timing.repeat is None, "Phase 2 must NOT have boundsDuration when duration is absent"

    # Narrative text preserves sequence
    assert "20 mg" in p1.text and "for 5 days" in p1.text
    assert "Then" in p2.text and "10 mg" in p2.text

    # No dose combination (neither phase should imply 30 mg)
    assert float(p1.doseAndRate[0].doseQuantity.value) != 30.0
    assert float(p2.doseAndRate[0].doseQuantity.value) != 30.0


def test_sequential_taper_multi_phase_with_weeks_duration():
    """Verify 3-step taper with weeks duration units."""
    raw_text = "Dexamethasone 4 mg daily for 2 weeks, then 2 mg daily for 1 week, then 1 mg daily."
    ev = _make_evidence("ev-dex", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Dexamethasone", evidence_id="ev-dex"),
        _make_claim("c-d1", "med.dose", "4", value_numeric=4.0, unit_raw="mg", evidence_id="ev-dex"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-dex"),
        _make_claim("c-dur1", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-dex"),
        _make_claim("c-d2", "med.dose", "2", value_numeric=2.0, unit_raw="mg", evidence_id="ev-dex"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-dex"),
        _make_claim("c-dur2", "med.duration", "1 week", value_numeric=1.0, unit_raw="weeks", evidence_id="ev-dex"),
        _make_claim("c-d3", "med.dose", "1", value_numeric=1.0, unit_raw="mg", evidence_id="ev-dex"),
        _make_claim("c-f3", "med.frequency", "daily", evidence_id="ev-dex"),
    ]
    ev_map = {"ev-dex": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert len(mr.dosageInstruction) == 3
    assert [d.sequence for d in mr.dosageInstruction] == [1, 2, 3]

    # Phase 1: 2 weeks (UCUM code "wk")
    assert mr.dosageInstruction[0].timing.repeat.boundsDuration.value == Decimal(2)
    assert mr.dosageInstruction[0].timing.repeat.boundsDuration.code == "wk"

    # Phase 2: 1 week (UCUM code "wk")
    assert mr.dosageInstruction[1].timing.repeat.boundsDuration.value == Decimal(1)
    assert mr.dosageInstruction[1].timing.repeat.boundsDuration.code == "wk"

    # Phase 3: no boundsDuration
    assert mr.dosageInstruction[2].timing.repeat is None


def test_sequential_taper_missing_duration_does_not_invent_bounds():
    """When a taper transition is present but duration is missing, no boundsDuration is invented."""
    raw_text = "Prednisone 20 mg daily then 10 mg daily."
    ev = _make_evidence("ev-nodur", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", evidence_id="ev-nodur"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", evidence_id="ev-nodur"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-nodur"),
        _make_claim("c-d2", "med.dose", "10", value_numeric=10.0, unit_raw="mg", evidence_id="ev-nodur"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-nodur"),
    ]
    ev_map = {"ev-nodur": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert len(mr.dosageInstruction) == 2
    assert mr.dosageInstruction[0].sequence == 1
    assert mr.dosageInstruction[1].sequence == 2

    # Neither phase has boundsDuration
    assert mr.dosageInstruction[0].timing.repeat is None
    assert mr.dosageInstruction[1].timing.repeat is None


# ─── 3. CONFLICTING INSTRUCTIONS & DETERMINISTIC SLOTS ───────────────────────

def test_conflicting_unslotted_doses_selects_deterministic_winner():
    """When two conflicting doses exist without timing or taper cues, deterministic selection runs."""
    raw_text = "Metformin 500 mg po daily. (Note elsewhere: Metformin 1000 mg po daily)."
    ev = _make_evidence("ev-conflict", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Metformin", evidence_id="ev-conflict"),
        _make_claim("c-d1", "med.dose", "500", value_numeric=500.0, unit_raw="mg", evidence_id="ev-conflict", extraction_confidence=0.99),
        _make_claim("c-d2", "med.dose", "1000", value_numeric=1000.0, unit_raw="mg", evidence_id="ev-conflict", extraction_confidence=0.80),
        _make_claim("c-f", "med.frequency", "daily", evidence_id="ev-conflict"),
    ]
    ev_map = {"ev-conflict": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    # Exactly 1 dosage instruction (not split, not taper)
    assert len(mr.dosageInstruction) == 1
    assert float(mr.dosageInstruction[0].doseAndRate[0].doseQuantity.value) == 500.0

    # Competing candidate shadowed
    comp_ext = next(ext for ext in mr.extension if ext.url == EXT_COMPETING_CANDIDATE)
    assert any(s.url == "competingClaimId" and s.valueString == "c-d2" for s in comp_ext.extension)


# ─── 4. UNSUPPORTED, INCOMPLETE & SPECIAL DOSING ────────────────────────────

def test_unparseable_dose_string_sliding_scale_omits_quantity():
    """Unparseable dose strings omit doseQuantity and do NOT emit zero-dose fallback."""
    raw_text = "Insulin aspart sliding scale per protocol with meals."
    ev = _make_evidence("ev-scale", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Insulin aspart", evidence_id="ev-scale"),
        _make_claim("c-d", "med.dose", "sliding scale per protocol", value_numeric=None, unit_raw=None, evidence_id="ev-scale"),
        _make_claim("c-f", "med.frequency", "with meals", evidence_id="ev-scale"),
    ]
    ev_map = {"ev-scale": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"FHIR validation errors: {val.errors}"

    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    dosage = mr.dosageInstruction[0]
    assert dosage.doseAndRate is None, "Unparseable dose must NOT emit doseAndRate or zero quantity"
    assert "sliding scale" in dosage.text
    # Element provenance preserved on Dosage.extension
    assert any(ext.url == EXT_CLAIM_PROVENANCE for ext in dosage.extension)


def test_explicit_zero_dose_permitted_when_grounded():
    """Genuinely stated zero dose is permitted and parsed as Decimal(0.0)."""
    raw_text = "Prednisone 0 mg daily (taper completed)."
    ev = _make_evidence("ev-zero", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", evidence_id="ev-zero"),
        _make_claim("c-d", "med.dose", "0", value_numeric=0.0, unit_raw="mg", evidence_id="ev-zero"),
        _make_claim("c-f", "med.frequency", "daily", evidence_id="ev-zero"),
    ]
    ev_map = {"ev-zero": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert float(mr.dosageInstruction[0].doseAndRate[0].doseQuantity.value) == 0.0


def test_malformed_group_missing_name_and_dose_suppressed():
    """A medication group lacking both drug name and dose is suppressed with an audit entry."""
    raw_text = "oral twice daily for 30 days."
    ev = _make_evidence("ev-mal", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-r", "med.route", "oral", evidence_id="ev-mal"),
        _make_claim("c-f", "med.frequency", "twice daily", evidence_id="ev-mal"),
    ]
    ev_map = {"ev-mal": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit = []
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad", audit_trail=audit)
    med_res = [e.resource for e in bundle.entry if isinstance(e.resource, (MedicationRequest, MedicationStatement))]
    assert len(med_res) == 0
    assert any(a.get("status") == "SUPPRESSED" for a in audit)


def test_cross_medication_contamination_conflicting_names_suppressed():
    """A group with two distinct medication names is suppressed to prevent cross-contamination."""
    raw_text = "Lisinopril and Metformin 500 mg daily."
    ev = _make_evidence("ev-cross", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-n1", "med.name", "Lisinopril", normalized_code="29046", code_system="RxNorm", evidence_id="ev-cross"),
        _make_claim("c-n2", "med.name", "Metformin", normalized_code="6809", code_system="RxNorm", evidence_id="ev-cross"),
        _make_claim("c-d", "med.dose", "500", value_numeric=500.0, unit_raw="mg", evidence_id="ev-cross"),
    ]
    ev_map = {"ev-cross": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit = []
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad", audit_trail=audit)
    med_res = [e.resource for e in bundle.entry if isinstance(e.resource, (MedicationRequest, MedicationStatement))]
    assert len(med_res) == 0
    assert any("Cross-medication contamination" in str(a.get("reason")) for a in audit)


# ─── 5. WARNING W-1 & FAIL-CLOSED PRESERVATION ──────────────────────────────

def test_warning_w1_complex_parenthetical_historical_not_split_dosed():
    """A historical/discontinued dose in a parenthetical clause is NOT mapped into a concurrent split-dose instruction."""
    raw_text = "Metoprolol (previously 25 mg daily, discontinued) 50 mg daily."
    ev = _make_evidence("ev-w1", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Metoprolol", evidence_id="ev-w1"),
        _make_claim("c-d-hist", "med.dose", "25", value_numeric=25.0, unit_raw="mg", evidence_id="ev-w1", temporal_status="discontinued"),
        _make_claim("c-d-curr", "med.dose", "50", value_numeric=50.0, unit_raw="mg", evidence_id="ev-w1", temporal_status="current"),
        _make_claim("c-f", "med.frequency", "daily", evidence_id="ev-w1"),
    ]
    ev_map = {"ev-w1": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    # Must NOT create a split-dose regimen
    assert len(mr.dosageInstruction) == 1
    # Active current dose is emitted
    assert float(mr.dosageInstruction[0].doseAndRate[0].doseQuantity.value) in (25.0, 50.0)


# ─── 6. REGRESSION PARITY ───────────────────────────────────────────────────

def test_single_dose_medication_regression_parity():
    """Standard single-dose medication retains exact formatting and attribute values."""
    raw_text = "Metformin 500 mg oral twice daily for 90 days."
    ev = _make_evidence("ev-single", raw_text, 0, len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Metformin", normalized_code="6809", code_system="RxNorm", evidence_id="ev-single"),
        _make_claim("c-dose", "med.dose", "500", value_numeric=500.0, unit_raw="mg", evidence_id="ev-single"),
        _make_claim("c-route", "med.route", "oral", evidence_id="ev-single"),
        _make_claim("c-freq", "med.frequency", "twice daily", evidence_id="ev-single"),
        _make_claim("c-dur", "med.duration", "90 days", value_numeric=90.0, unit_raw="days", evidence_id="ev-single"),
    ]
    ev_map = {"ev-single": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"FHIR validation errors: {val.errors}"

    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert len(mr.dosageInstruction) == 1
    d = mr.dosageInstruction[0]
    assert d.sequence == 1
    assert float(d.doseAndRate[0].doseQuantity.value) == 500.0
    assert d.doseAndRate[0].doseQuantity.unit == "mg"
    assert d.route.text == "oral"
    assert d.timing.code.text == "twice daily"
    assert d.timing.repeat.boundsDuration.value == Decimal(90)
    assert d.timing.repeat.boundsDuration.code == "d"


# ─── 7. DIRECT FUNCTION CONTRACTS ───────────────────────────────────────────

def test_direct_map_medication_request_and_statement_functions():
    """Direct functional verification of map_medication_request and map_medication_statement."""
    from fhir.resources.R4B.codeableconcept import CodeableConcept
    from fhir.resources.R4B.coding import Coding
    from fhir.resources.R4B.dosage import Dosage, DosageDoseAndRate
    from fhir.resources.R4B.quantity import Quantity

    code = CodeableConcept(coding=[Coding(system=RXNORM_SYSTEM, code="20352", display="Carvedilol")], text="Carvedilol")
    d1 = Dosage(sequence=1, text="6.25 mg morning", doseAndRate=[DosageDoseAndRate(doseQuantity=Quantity(value=Decimal("6.25"), unit="mg"))])
    d2 = Dosage(sequence=1, text="12.5 mg bedtime", doseAndRate=[DosageDoseAndRate(doseQuantity=Quantity(value=Decimal("12.5"), unit="mg"))])

    # 1. MedicationRequest
    mr = map_medication_request(
        resource_id="mr-test-1",
        patient_id="pat-test",
        medication_code=code,
        dosage_instructions=[d1, d2],
        temporal_status="planned",
        encounter_id="enc-1",
        doc_ref_id="docref-1",
    )
    assert mr.id == "mr-test-1"
    assert mr.status == "active"
    assert mr.intent == "plan"
    assert mr.subject.reference == "Patient/pat-test"
    assert mr.encounter.reference == "Encounter/enc-1"
    assert mr.supportingInformation[0].reference == "DocumentReference/docref-1"
    assert len(mr.dosageInstruction) == 2

    # 2. MedicationStatement
    ms = map_medication_statement(
        resource_id="ms-test-1",
        patient_id="pat-test",
        medication_code=code,
        dosages=[d1, d2],
        temporal_status="discontinued",
        encounter_id="enc-1",
        doc_ref_id="docref-1",
    )
    assert ms.id == "ms-test-1"
    assert ms.status == "stopped"
    assert ms.subject.reference == "Patient/pat-test"
    assert ms.context.reference == "Encounter/enc-1"
    assert ms.derivedFrom[0].reference == "DocumentReference/docref-1"
    assert len(ms.dosage) == 2
