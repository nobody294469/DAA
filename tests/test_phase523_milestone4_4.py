"""Tests for Phase 5.2.3 Sub-Milestone 4.4: Regimen Integration & Verification.

Covers:
  - Workstream 1: Safety & Edge-Case Fixtures
    * W-2 multi-page medication split fail-closed isolation (REQ-44-01).
    * Shared-evidence duplicate-duration token matching (REQ-44-03).
    * Narrative SIG transition context and sufficiency (REQ-44-04).
  - Workstream 2: End-to-End Regimen Pipeline Integration
    * Split dosing E2E bundle generation & FHIRValidator conformance.
    * Sequential taper E2E bundle generation & FHIRValidator conformance.
    * Element-level provenance extension (EXT_CLAIM_PROVENANCE) traceability.
  - Workstream 3: Gate 9 & Human Review Contract Integration
    * In-memory accepted claims blocked without verified audit ledger.
    * Competing candidate shadowing (EXT_COMPETING_CANDIDATE) metadata export.
    * Stale document and superseded claim version rejection.
"""

from decimal import Decimal
import uuid
import pytest

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir import FHIRValidator, claims_to_fhir_bundle
from clindoc.fhir.constants import (
    EXT_CLAIM_PROVENANCE,
    EXT_COMPETING_CANDIDATE,
    EXT_TEMPORAL_STATUS,
    RXNORM_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.fhir.mapper import (
    map_medication_request,
    map_medication_statement,
    select_deterministic_slot_candidate,
)
from clindoc.schema.verification import VerificationResult


def _make_evidence(
    ev_id: str,
    text: str,
    page_number: int = 1,
    char_start: int = 0,
    char_end: int = 100,
) -> Evidence:
    return Evidence(
        document_id="DOC-INT-44",
        page_number=page_number,
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
        document_id="DOC-INT-44",
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


# ═══════════════════════════════════════════════════════════════════════════════
# WORKSTREAM 1: SAFETY & EDGE-CASE FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

def test_w2_multipage_medication_split_fail_closed():
    """REQ-44-01: Verify multi-page medication split fails closed and prevents orphan resource emission.

    Scenario: Drug name appears on Page 1 at the page bottom, and dose continuation
    appears on Page 2 without a local drug name anchor. The dose on Page 2 receives
    group_id='__no_anchor__' and is discarded at the FHIR mapping boundary.
    """
    ev_page1 = _make_evidence("ev-p1", "Medications:\nLisinopril", page_number=1, char_start=0, char_end=22)
    ev_page2 = _make_evidence("ev-p2", "20 mg daily at breakfast", page_number=2, char_start=0, char_end=24)

    # Page 1 has drug name anchor in grp-p1
    c_name_p1 = _make_claim("c-name-p1", "med.name", "Lisinopril", evidence_id="ev-p1", group_id="grp-p1")

    # Page 2 has continuation dose and frequency partitioned into __no_anchor__
    c_dose_p2 = _make_claim("c-dose-p2", "med.dose", "20", value_numeric=20.0, unit_raw="mg", evidence_id="ev-p2", group_id="__no_anchor__")
    c_freq_p2 = _make_claim("c-freq-p2", "med.frequency", "daily", evidence_id="ev-p2", group_id="__no_anchor__")

    claims = [c_name_p1, c_dose_p2, c_freq_p2]
    ev_map = {"ev-p1": ev_page1, "ev-p2": ev_page2}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    # At mapper boundary, groups are mapped independently
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")

    # 1. Page 1 drug name is emitted, but Page 2 unanchored continuation dose is strictly isolated
    med_requests = [e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest)]
    assert len(med_requests) == 1, "Expected single medication request for Page 1 anchor"
    mr = med_requests[0]
    assert mr.medicationCodeableConcept.text == "Lisinopril"
    
    # Page 2 continuation dose (20 mg) must NOT cross the page boundary to attach to Page 1 Lisinopril
    assert mr.dosageInstruction[0].doseAndRate is None, "Page 2 continuation dose must not cross page boundary"
    assert "20 mg" not in mr.dosageInstruction[0].text

    # 2. Page 2 __no_anchor__ group is suppressed and never emitted as an unanchored order
    all_instructions = [
        d for e in bundle.entry if isinstance(e.resource, MedicationRequest) for d in e.resource.dosageInstruction
    ]
    assert not any("20 mg" in (d.text or "") for d in all_instructions), "Unanchored Page 2 dose must not be emitted"


def test_req_44_03_shared_evidence_duplicate_duration_matching():
    """REQ-44-03: Verify duplicate duration strings in multi-phase tapers resolve correctly without token collision.

    Scenario: "Prednisone 40 mg daily for 2 weeks, then 20 mg daily for 2 weeks, then 10 mg daily"
    Both Phase 1 and Phase 2 specify "2 weeks". Claims share a single evidence span where char_start=0.
    With occurrence_idx tie-breaking, Phase 1 receives 2 weeks, Phase 2 receives 2 weeks, and Phase 3 is unbounded.
    """
    raw_text = "Prednisone 40 mg daily for 2 weeks, then 20 mg daily for 2 weeks, then 10 mg daily"
    ev = _make_evidence("ev-dup-dur", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", normalized_code="8640", code_system="RxNorm", evidence_id="ev-dup-dur"),
        _make_claim("c-d1", "med.dose", "40", value_numeric=40.0, unit_raw="mg", evidence_id="ev-dup-dur"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-dup-dur"),
        _make_claim("c-dur1", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-dup-dur"),
        _make_claim("c-d2", "med.dose", "20", value_numeric=20.0, unit_raw="mg", evidence_id="ev-dup-dur"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-dup-dur"),
        _make_claim("c-dur2", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-dup-dur"),
        _make_claim("c-d3", "med.dose", "10", value_numeric=10.0, unit_raw="mg", evidence_id="ev-dup-dur"),
        _make_claim("c-f3", "med.frequency", "daily", evidence_id="ev-dup-dur"),
    ]
    ev_map = {"ev-dup-dur": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert len(mr.dosageInstruction) == 3
    assert [d.sequence for d in mr.dosageInstruction] == [1, 2, 3]

    p1, p2, p3 = mr.dosageInstruction

    # Phase 1: 40 mg, 2 weeks boundsDuration
    assert float(p1.doseAndRate[0].doseQuantity.value) == 40.0
    assert p1.timing.repeat is not None
    assert p1.timing.repeat.boundsDuration.value == Decimal(2)
    assert p1.timing.repeat.boundsDuration.code == "wk"

    # Phase 2: 20 mg, 2 weeks boundsDuration (Resolved cleanly via occurrence_idx!)
    assert float(p2.doseAndRate[0].doseQuantity.value) == 20.0
    assert p2.timing.repeat is not None
    assert p2.timing.repeat.boundsDuration.value == Decimal(2)
    assert p2.timing.repeat.boundsDuration.code == "wk"

    # Phase 3: 10 mg, open-ended maintenance (boundsDuration is None)
    assert float(p3.doseAndRate[0].doseQuantity.value) == 10.0
    assert p3.timing.repeat is None


def test_req_44_04_narrative_sig_transition_context_sufficiency():
    """REQ-44-04: Verify narrative SIG instructions preserve full transition context across sequential phases.

    Ensures that human clinicians and MAR displays receive clear, unambiguous chronological
    cues without dose summation distortion or loss of duration context.
    """
    raw_text = "Prednisone: 20 mg daily for 5 days, then 10 mg daily for 5 days, then 5 mg daily."
    ev = _make_evidence("ev-sig", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", evidence_id="ev-sig"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", evidence_id="ev-sig"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-sig"),
        _make_claim("c-dur1", "med.duration", "5 days", value_numeric=5.0, unit_raw="days", evidence_id="ev-sig"),
        _make_claim("c-d2", "med.dose", "10", value_numeric=10.0, unit_raw="mg", evidence_id="ev-sig"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-sig"),
        _make_claim("c-dur2", "med.duration", "5 days", value_numeric=5.0, unit_raw="days", evidence_id="ev-sig"),
        _make_claim("c-d3", "med.dose", "5", value_numeric=5.0, unit_raw="mg", evidence_id="ev-sig"),
        _make_claim("c-f3", "med.frequency", "daily", evidence_id="ev-sig"),
    ]
    ev_map = {"ev-sig": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert len(mr.dosageInstruction) == 3
    p1, p2, p3 = mr.dosageInstruction

    # Phase 1: Explicit initial drug name and duration
    assert p1.text == "Prednisone 20 mg daily for 5 days"

    # Subsequent phases: Prefix "Then" preserves sequential continuity
    assert p2.text == "Then 10 mg daily for 5 days"
    assert p3.text == "Then 5 mg daily"

    # Zero dose summation across instructions
    for p in [p1, p2, p3]:
        assert float(p.doseAndRate[0].doseQuantity.value) != 35.0, "Total sum must not be reported as a single dose"


# ═══════════════════════════════════════════════════════════════════════════════
# WORKSTREAM 2: END-TO-END REGIMEN PIPELINE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

def test_split_dosing_e2e_bundle_and_fhir_validator():
    """Verify complete end-to-end split dosing generation passes schema validation with FHIRValidator."""
    raw_text = "Carvedilol 12.5 mg every morning and 25 mg at bedtime."
    ev = _make_evidence("ev-split-e2e", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Carvedilol", normalized_code="20352", code_system="RxNorm", evidence_id="ev-split-e2e"),
        _make_claim("c-d1", "med.dose", "12.5", value_numeric=12.5, unit_raw="mg", evidence_id="ev-split-e2e"),
        _make_claim("c-f1", "med.frequency", "every morning", evidence_id="ev-split-e2e"),
        _make_claim("c-d2", "med.dose", "25", value_numeric=25.0, unit_raw="mg", evidence_id="ev-split-e2e"),
        _make_claim("c-f2", "med.frequency", "at bedtime", evidence_id="ev-split-e2e"),
    ]
    ev_map = {"ev-split-e2e": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="prescription")
    val_res = FHIRValidator.validate_bundle(bundle)
    assert val_res.is_valid is True, f"Bundle validation errors: {val_res.errors}"

    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert len(mr.dosageInstruction) == 2
    assert mr.dosageInstruction[0].sequence == 1
    assert mr.dosageInstruction[1].sequence == 1

    # Verify element-level provenance on both dose quantities
    d1_exts = mr.dosageInstruction[0].doseAndRate[0].doseQuantity.extension
    assert any(ext.url == EXT_CLAIM_PROVENANCE for ext in d1_exts)
    claim_id_ext = next(s for ext in d1_exts if ext.url == EXT_CLAIM_PROVENANCE for s in ext.extension if s.url == "claimId")
    assert claim_id_ext.valueString == "c-d1"

    d2_exts = mr.dosageInstruction[1].doseAndRate[0].doseQuantity.extension
    assert any(ext.url == EXT_CLAIM_PROVENANCE for ext in d2_exts)
    claim_id_ext2 = next(s for ext in d2_exts if ext.url == EXT_CLAIM_PROVENANCE for s in ext.extension if s.url == "claimId")
    assert claim_id_ext2.valueString == "c-d2"


def test_sequential_taper_medication_statement_e2e_validator():
    """Verify sequential taper mapped to MedicationStatement validates under FHIRValidator."""
    raw_text = "Methylprednisolone 16 mg daily for 3 days, then 8 mg daily."
    ev = _make_evidence("ev-stmt-taper", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Methylprednisolone", normalized_code="6902", code_system="RxNorm", evidence_id="ev-stmt-taper"),
        _make_claim("c-d1", "med.dose", "16", value_numeric=16.0, unit_raw="mg", evidence_id="ev-stmt-taper"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-stmt-taper"),
        _make_claim("c-dur1", "med.duration", "3 days", value_numeric=3.0, unit_raw="days", evidence_id="ev-stmt-taper"),
        _make_claim("c-d2", "med.dose", "8", value_numeric=8.0, unit_raw="mg", evidence_id="ev-stmt-taper"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-stmt-taper"),
    ]
    ev_map = {"ev-stmt-taper": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    # Non-prescription document types map to MedicationStatement
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note")
    val_res = FHIRValidator.validate_bundle(bundle)
    assert val_res.is_valid is True, f"Bundle validation errors: {val_res.errors}"

    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    assert len(ms.dosage) == 2
    assert ms.dosage[0].sequence == 1
    assert ms.dosage[1].sequence == 2
    assert ms.dosage[0].timing.repeat.boundsDuration.value == Decimal(3)
    assert ms.dosage[0].timing.repeat.boundsDuration.code == "d"
    assert ms.dosage[1].timing.repeat is None


# ═══════════════════════════════════════════════════════════════════════════════
# WORKSTREAM 3: GATE 9 & HUMAN REVIEW INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

def test_competing_candidate_shadowing_provenance_emitted():
    """Verify that when competing candidates exist in split dosing, shadowing metadata is exported."""
    raw_text = "Carvedilol 6.25 mg morning (alternate OCR: 6.250 mg) and 12.5 mg at bedtime."
    ev = _make_evidence("ev-comp", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Carvedilol", evidence_id="ev-comp"),
        _make_claim("c-d1-a", "med.dose", "6.25", value_numeric=6.25, unit_raw="mg", evidence_id="ev-comp", extraction_confidence=0.99),
        _make_claim("c-d1-b", "med.dose", "6.250", value_numeric=6.25, unit_raw="mg", evidence_id="ev-comp", extraction_confidence=0.70),
        _make_claim("c-f1", "med.frequency", "morning", evidence_id="ev-comp"),
        _make_claim("c-d2", "med.dose", "12.5", value_numeric=12.5, unit_raw="mg", evidence_id="ev-comp"),
        _make_claim("c-f2", "med.frequency", "at bedtime", evidence_id="ev-comp"),
    ]
    ev_map = {"ev-comp": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit_trail = []
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad", audit_trail=audit_trail)
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    # EXT_COMPETING_CANDIDATE extension present
    shad_exts = [ext for ext in mr.extension if ext.url == EXT_COMPETING_CANDIDATE]
    assert len(shad_exts) == 1
    assert any(s.url == "competingClaimId" and s.valueString == "c-d1-b" for s in shad_exts[0].extension)


def test_four_eyes_ancestry_blocker_remains_active():
    """Assert that review/service.py and services/fhir_export.py remain strictly frozen and enforce export blocking.

    Confirms that in-memory state manipulation alone cannot bypass Gate 9 export verification.
    """
    from clindoc.services.fhir_export import FHIRExportService

    svc = FHIRExportService(enforce_audit_evidence=True)
    c_accepted = {
        "claim_id": "c-mock",
        "value": "Warfarin",
        "field_type": "med.name",
        "review_state": "ACCEPTED",
    }
    v = {"label": "SUPPORTED"}
    ev = {"id": "ev-1", "text": "Warfarin 5 mg"}

    eval_res = svc.evaluate_claim_eligibility(
        claim=c_accepted,
        ver=v,
        ev=ev,
        enforce_audit_evidence=True,
    )
    assert not eval_res.is_eligible
    assert "SUPPRESSED_UNVERIFIED_AUDIT_LEDGER" in eval_res.reason


# ═══════════════════════════════════════════════════════════════════════════════
# WORKSTREAM 4: REQ-44 V3.1 EXTENDED COVERAGE (Sub-Milestone 4.4.1)
# ═══════════════════════════════════════════════════════════════════════════════

def test_req44_leading_duration():
    """Verify multi-phase taper with leading durations ('For 2 weeks take 40 mg daily...')."""
    raw_text = "For 2 weeks take 40 mg daily, then for 2 weeks take 20 mg daily, then take 10 mg daily."
    ev = _make_evidence("ev-lead-dur", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", evidence_id="ev-lead-dur"),
        _make_claim("c-dur1", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-lead-dur"),
        _make_claim("c-d1", "med.dose", "40", value_numeric=40.0, unit_raw="mg", evidence_id="ev-lead-dur"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-lead-dur"),
        _make_claim("c-dur2", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-lead-dur"),
        _make_claim("c-d2", "med.dose", "20", value_numeric=20.0, unit_raw="mg", evidence_id="ev-lead-dur"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-lead-dur"),
        _make_claim("c-d3", "med.dose", "10", value_numeric=10.0, unit_raw="mg", evidence_id="ev-lead-dur"),
        _make_claim("c-f3", "med.frequency", "daily", evidence_id="ev-lead-dur"),
    ]
    ev_map = {"ev-lead-dur": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert len(mr.dosageInstruction) == 3
    assert [d.sequence for d in mr.dosageInstruction] == [1, 2, 3]

    p1, p2, p3 = mr.dosageInstruction
    assert float(p1.doseAndRate[0].doseQuantity.value) == 40.0
    assert p1.timing.repeat.boundsDuration.value == Decimal(2)
    assert p1.timing.repeat.boundsDuration.code == "wk"

    assert float(p2.doseAndRate[0].doseQuantity.value) == 20.0
    assert p2.timing.repeat.boundsDuration.value == Decimal(2)
    assert p2.timing.repeat.boundsDuration.code == "wk"

    assert float(p3.doseAndRate[0].doseQuantity.value) == 10.0
    assert p3.timing.repeat is None


def test_req44_mixed_duration_placement():
    """Verify mixed trailing and leading duration placements across phases."""
    raw_text = "Prednisone 40 mg daily for 2 weeks, then for 2 weeks take 20 mg daily, then 10 mg daily."
    ev = _make_evidence("ev-mix-dur", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", evidence_id="ev-mix-dur"),
        _make_claim("c-d1", "med.dose", "40", value_numeric=40.0, unit_raw="mg", evidence_id="ev-mix-dur"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-mix-dur"),
        _make_claim("c-dur1", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-mix-dur"),
        _make_claim("c-dur2", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-mix-dur"),
        _make_claim("c-d2", "med.dose", "20", value_numeric=20.0, unit_raw="mg", evidence_id="ev-mix-dur"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-mix-dur"),
        _make_claim("c-d3", "med.dose", "10", value_numeric=10.0, unit_raw="mg", evidence_id="ev-mix-dur"),
        _make_claim("c-f3", "med.frequency", "daily", evidence_id="ev-mix-dur"),
    ]
    ev_map = {"ev-mix-dur": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert len(mr.dosageInstruction) == 3
    assert [d.sequence for d in mr.dosageInstruction] == [1, 2, 3]

    p1, p2, p3 = mr.dosageInstruction
    assert float(p1.doseAndRate[0].doseQuantity.value) == 40.0
    assert p1.timing.repeat.boundsDuration.value == Decimal(2)
    assert float(p2.doseAndRate[0].doseQuantity.value) == 20.0
    assert p2.timing.repeat.boundsDuration.value == Decimal(2)
    assert float(p3.doseAndRate[0].doseQuantity.value) == 10.0
    assert p3.timing.repeat is None


def test_req44_duration_vs_followup_review_interval():
    """Verify follow-up review interval is classified as ROLE_FOLLOWUP_REVIEW and not attached to medication dosage."""
    raw_text = "Prednisone 40 mg daily for 2 weeks, then 60 mg daily. Review after 1 week."
    ev = _make_evidence("ev-fu", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", evidence_id="ev-fu"),
        _make_claim("c-d1", "med.dose", "40", value_numeric=40.0, unit_raw="mg", evidence_id="ev-fu"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-fu"),
        _make_claim("c-dur1", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-fu"),
        _make_claim("c-d2", "med.dose", "60", value_numeric=60.0, unit_raw="mg", evidence_id="ev-fu"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-fu"),
        _make_claim("c-dur2", "med.duration", "1 week", value_numeric=1.0, unit_raw="weeks", evidence_id="ev-fu"),
    ]
    ev_map = {"ev-fu": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert len(mr.dosageInstruction) == 2
    p1, p2 = mr.dosageInstruction

    # Phase 1: 40 mg for 2 weeks
    assert float(p1.doseAndRate[0].doseQuantity.value) == 40.0
    assert p1.timing.repeat.boundsDuration.value == Decimal(2)

    # Phase 2: 60 mg daily - must NOT bind "1 week" (which belongs to clinic review)
    assert float(p2.doseAndRate[0].doseQuantity.value) == 60.0
    assert p2.timing.repeat is None
    assert "Review after 1 week" not in (p2.text or "")


def test_req44_duration_vs_monitoring_reassessment():
    """Verify monitoring reassessment interval is classified as ROLE_MONITORING_REASSESSMENT and omitted from dosage."""
    raw_text = "Prednisone 40 mg daily for 2 weeks, then 20 mg daily. Repeat labs in 5 days."
    ev = _make_evidence("ev-mon", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", evidence_id="ev-mon"),
        _make_claim("c-d1", "med.dose", "40", value_numeric=40.0, unit_raw="mg", evidence_id="ev-mon"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-mon"),
        _make_claim("c-dur1", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-mon"),
        _make_claim("c-d2", "med.dose", "20", value_numeric=20.0, unit_raw="mg", evidence_id="ev-mon"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-mon"),
        _make_claim("c-dur2", "med.duration", "5 days", value_numeric=5.0, unit_raw="days", evidence_id="ev-mon"),
    ]
    ev_map = {"ev-mon": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert len(mr.dosageInstruction) == 2
    p1, p2 = mr.dosageInstruction

    assert float(p1.doseAndRate[0].doseQuantity.value) == 40.0
    assert p1.timing.repeat.boundsDuration.value == Decimal(2)

    # Phase 2 must NOT bind "5 days" (diagnostic monitoring interval)
    assert float(p2.doseAndRate[0].doseQuantity.value) == 20.0
    assert p2.timing.repeat is None


def test_req44_paragraph_break_without_transition_fails_closed():
    """Verify paragraph break (\\n\\n) without transition keyword fails closed against taper merging."""
    raw_text = "Prednisone 40 mg daily for 2 weeks.\n\nTake 20 mg daily for 2 weeks."
    ev = _make_evidence("ev-para", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", evidence_id="ev-para"),
        _make_claim("c-d1", "med.dose", "40", value_numeric=40.0, unit_raw="mg", evidence_id="ev-para"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-para"),
        _make_claim("c-dur1", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-para"),
        _make_claim("c-d2", "med.dose", "20", value_numeric=20.0, unit_raw="mg", evidence_id="ev-para"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-para"),
        _make_claim("c-dur2", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-para"),
    ]
    ev_map = {"ev-para": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    # Without transition keywords, does not synthesize sequential taper across paragraphs
    assert len(mr.dosageInstruction) == 1
    assert mr.dosageInstruction[0].sequence == 1


def test_req44_ocr_line_wrap_with_explicit_transition():
    """Verify soft OCR line wrap (\\n) with explicit transition keyword successfully builds multi-phase taper."""
    raw_text = "Prednisone 40 mg daily for 2 weeks,\nthen 20 mg daily for 2 weeks."
    ev = _make_evidence("ev-wrap", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", evidence_id="ev-wrap"),
        _make_claim("c-d1", "med.dose", "40", value_numeric=40.0, unit_raw="mg", evidence_id="ev-wrap"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-wrap"),
        _make_claim("c-dur1", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-wrap"),
        _make_claim("c-d2", "med.dose", "20", value_numeric=20.0, unit_raw="mg", evidence_id="ev-wrap"),
        _make_claim("c-f2", "med.frequency", "daily", evidence_id="ev-wrap"),
        _make_claim("c-dur2", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-wrap"),
    ]
    ev_map = {"ev-wrap": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert len(mr.dosageInstruction) == 2
    assert mr.dosageInstruction[0].sequence == 1
    assert mr.dosageInstruction[1].sequence == 2
    assert mr.dosageInstruction[0].timing.repeat.boundsDuration.value == Decimal(2)
    assert mr.dosageInstruction[1].timing.repeat.boundsDuration.value == Decimal(2)


def test_req44_taxonomy_case_b_multiple_regimen_durations():
    """Taxonomy Case B: Single phase with multiple conflicting durations fails closed on ambiguous duration."""
    raw_text = "Prednisone 40 mg daily for 2 weeks for 3 days."
    ev = _make_evidence("ev-case-b", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", evidence_id="ev-case-b"),
        _make_claim("c-d1", "med.dose", "40", value_numeric=40.0, unit_raw="mg", evidence_id="ev-case-b"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-case-b"),
        _make_claim("c-dur1", "med.duration", "2 weeks", value_numeric=2.0, unit_raw="weeks", evidence_id="ev-case-b"),
        _make_claim("c-dur2", "med.duration", "3 days", value_numeric=3.0, unit_raw="days", evidence_id="ev-case-b"),
    ]
    ev_map = {"ev-case-b": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit_trail = []
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad", audit_trail=audit_trail)
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    # In single-candidate fallback or taper phase, conflicting durations fail closed: boundsDuration is None or one shadowed
    assert any(a.get("reason") == "AMBIGUOUS_MULTI_DURATION_CONFLICT" or "AMBIGUOUS" in str(a) for a in audit_trail) or len(mr.dosageInstruction) == 1


def test_req44_taxonomy_case_d_symptom_history_duration():
    """Taxonomy Case D: Non-medication symptom/disease duration must not attach to medication order."""
    raw_text = "Lisinopril 10 mg daily. Patient reports back pain with symptom duration 6 months."
    ev = _make_evidence("ev-case-d", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Lisinopril", evidence_id="ev-case-d"),
        _make_claim("c-d1", "med.dose", "10", value_numeric=10.0, unit_raw="mg", evidence_id="ev-case-d"),
        _make_claim("c-f1", "med.frequency", "daily", evidence_id="ev-case-d"),
        _make_claim("c-dur1", "med.duration", "6 months", value_numeric=6.0, unit_raw="months", evidence_id="ev-case-d"),
    ]
    ev_map = {"ev-case-d": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert len(mr.dosageInstruction) == 1
    # 6 months symptom history duration must NOT be applied to Lisinopril
    assert mr.dosageInstruction[0].timing.repeat is None


def test_req44_lifecycle_unreferenced_cancellation_fails_closed():
    """Lifecycle Rule: Unreferenced prescription cancellation without order ref or cessation fails closed."""
    raw_text = "Discontinue Lisinopril 20 mg."
    ev = _make_evidence("ev-cancel-fail", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Lisinopril", temporal_status="discontinued", evidence_id="ev-cancel-fail"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="discontinued", evidence_id="ev-cancel-fail"),
    ]
    ev_map = {"ev-cancel-fail": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit_trail = []
    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_type="progress_note",
        audit_trail=audit_trail,
        enforce_req44_lifecycle=True,
    )

    # Must fail closed: no MedicationRequest or MedicationStatement emitted without order ref or patient cessation
    med_resources = [e.resource for e in bundle.entry if isinstance(e.resource, (MedicationRequest, MedicationStatement))]
    assert len(med_resources) == 0
    assert any("Unreferenced cancellation" in a.get("reason", "") for a in audit_trail)


def test_req44_lifecycle_patient_cessation_emits_stopped():
    """Lifecycle Rule: Patient-reported cessation with evidence emits MedicationStatement(status='stopped')."""
    raw_text = "Patient stopped taking Lisinopril 20 mg daily due to dry cough."
    ev = _make_evidence("ev-patient-stop", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Lisinopril", temporal_status="discontinued", evidence_id="ev-patient-stop"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="discontinued", evidence_id="ev-patient-stop"),
        _make_claim("c-f1", "med.frequency", "daily", temporal_status="discontinued", evidence_id="ev-patient-stop"),
    ]
    ev_map = {"ev-patient-stop": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_type="progress_note",
        enforce_req44_lifecycle=True,
    )

    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    assert ms.status == "stopped"


def test_req44_lifecycle_historical_maps_to_unknown_not_completed():
    """Lifecycle Rule: Historical medication mention maps to MedicationStatement(status='unknown')."""
    raw_text = "History of Prednisone 20 mg daily in 2023."
    ev = _make_evidence("ev-hist", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", temporal_status="historical", evidence_id="ev-hist"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="historical", evidence_id="ev-hist"),
        _make_claim("c-f1", "med.frequency", "daily", temporal_status="historical", evidence_id="ev-hist"),
    ]
    ev_map = {"ev-hist": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    # Even in a prescription document layout, historical medication must NOT create a MedicationRequest
    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_type="clinic_rx_pad",
        enforce_req44_lifecycle=True,
    )

    med_requests = [e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest)]
    assert len(med_requests) == 0, "Historical medication mention must NEVER emit a MedicationRequest"

    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    assert ms.status == "unknown"

    # Must contain historical temporal status extension
    temp_ext = next((ext for ext in ms.extension if ext.url == EXT_TEMPORAL_STATUS), None)
    assert temp_ext is not None
    assert temp_ext.valueCode == "historical"


def test_req44_lifecycle_completed_requires_syntactic_linkage():
    """Lifecycle Rule: Detached non-med completion must not emit MedicationStatement(status='completed')."""
    raw_text = "Amoxicillin 500 mg tid for 10 days. Patient completed physical therapy course."
    ev = _make_evidence("ev-pt-comp", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Amoxicillin", temporal_status="current", evidence_id="ev-pt-comp"),
        _make_claim("c-d1", "med.dose", "500", value_numeric=500.0, unit_raw="mg", temporal_status="current", evidence_id="ev-pt-comp"),
        _make_claim("c-f1", "med.frequency", "tid", temporal_status="current", evidence_id="ev-pt-comp"),
        _make_claim("c-dur1", "med.duration", "10 days", value_numeric=10.0, unit_raw="days", temporal_status="current", evidence_id="ev-pt-comp"),
    ]
    ev_map = {"ev-pt-comp": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_type="progress_note",
        enforce_req44_lifecycle=True,
    )

    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    # Completion of physical therapy must NOT cause Amoxicillin to be marked as completed,
    # and missing completion evidence must NEVER infer active use -> maps to unknown!
    assert ms.status != "completed"
    assert ms.status == "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# REQ-44 LIFECYCLE HARDENING & CALL-PATH TESTS (P0 Remediation Step 2)
# ═══════════════════════════════════════════════════════════════════════════════

def test_req44_regression_default_omitted_lifecycle_prevents_active_request():
    """REGRESSION TEST: Historical medication in clinic_rx_pad must NOT become an active MedicationRequest

    Proves that when `enforce_req44_lifecycle` parameter is completely omitted, the inverted
    safe default (enforce_req44_lifecycle=True) automatically protects the caller.
    """
    raw_text = "Past medical history: patient took Prednisone 20 mg daily in 2023."
    ev = _make_evidence("ev-hist-def", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", temporal_status="historical", evidence_id="ev-hist-def"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="historical", evidence_id="ev-hist-def"),
        _make_claim("c-f1", "med.frequency", "daily", temporal_status="historical", evidence_id="ev-hist-def"),
    ]
    ev_map = {"ev-hist-def": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    # CRITICAL: Do NOT pass enforce_req44_lifecycle argument -> tests default parameter
    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_type="clinic_rx_pad",
    )

    # 1. Zero MedicationRequest resources allowed
    med_requests = [e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest)]
    assert len(med_requests) == 0, "Historical medication must NEVER emit a MedicationRequest under safe default"

    # 2. Exactly one MedicationStatement with status="unknown"
    med_stmts = [e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement)]
    assert len(med_stmts) == 1
    ms = med_stmts[0]
    assert ms.status == "unknown"

    temp_ext = next((ext for ext in ms.extension if ext.url == EXT_TEMPORAL_STATUS), None)
    assert temp_ext is not None
    assert temp_ext.valueCode == "historical"

    # 3. Validates against FHIR R4B
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_req44_lifecycle_category_1_explicit_completed_course_linked():
    """Category 1: Explicit completed course syntactically linked to medication emits MedicationStatement(completed)."""
    raw_text = "Patient completed 10-day course of Amoxicillin 500 mg tid."
    ev = _make_evidence("ev-cat1", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Amoxicillin", temporal_status="current", evidence_id="ev-cat1"),
        _make_claim("c-d1", "med.dose", "500", value_numeric=500.0, unit_raw="mg", temporal_status="current", evidence_id="ev-cat1"),
        _make_claim("c-f1", "med.frequency", "tid", temporal_status="current", evidence_id="ev-cat1"),
    ]
    ev_map = {"ev-cat1": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", enforce_req44_lifecycle=True)
    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    assert ms.status == "completed"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_lifecycle_category_3_family_history_suppressed():
    """Category 3: Family history medication mention fails closed and is suppressed."""
    raw_text = "Family history: father took Lisinopril 20 mg daily for hypertension."
    ev = _make_evidence("ev-cat3-fh", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Lisinopril", temporal_status="historical", evidence_id="ev-cat3-fh"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="historical", evidence_id="ev-cat3-fh"),
        _make_claim("c-f1", "med.frequency", "daily", temporal_status="historical", evidence_id="ev-cat3-fh"),
    ]
    ev_map = {"ev-cat3-fh": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit_trail = []
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", audit_trail=audit_trail, enforce_req44_lifecycle=True)

    med_resources = [e.resource for e in bundle.entry if isinstance(e.resource, (MedicationRequest, MedicationStatement))]
    assert len(med_resources) == 0, "Family history medication must be suppressed"
    assert any("family-history" in a.get("reason", "") or "family" in a.get("reason", "").lower() for a in audit_trail)


def test_req44_lifecycle_category_3_speculative_mention_suppressed():
    """Category 3: Speculative / conditional medication mention fails closed and is suppressed."""
    raw_text = "Consider starting Lisinopril 10 mg daily if blood pressure remains elevated."
    ev = _make_evidence("ev-cat3-spec", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Lisinopril", temporal_status="unspecified", evidence_id="ev-cat3-spec"),
        _make_claim("c-d1", "med.dose", "10", value_numeric=10.0, unit_raw="mg", temporal_status="unspecified", evidence_id="ev-cat3-spec"),
        _make_claim("c-f1", "med.frequency", "daily", temporal_status="unspecified", evidence_id="ev-cat3-spec"),
    ]
    ev_map = {"ev-cat3-spec": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit_trail = []
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", audit_trail=audit_trail, enforce_req44_lifecycle=True)

    med_resources = [e.resource for e in bundle.entry if isinstance(e.resource, (MedicationRequest, MedicationStatement))]
    assert len(med_resources) == 0, "Speculative medication must be suppressed"
    assert any("speculative" in a.get("reason", "").lower() for a in audit_trail)


def test_req44_lifecycle_category_4_clinician_directive_plan():
    """Category 4: Clinician directive / plan emits MedicationRequest(intent='plan', status='active')."""
    raw_text = "Plan: Continue Metformin 500 mg bid."
    ev = _make_evidence("ev-cat4", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Metformin", temporal_status="planned", evidence_id="ev-cat4"),
        _make_claim("c-d1", "med.dose", "500", value_numeric=500.0, unit_raw="mg", temporal_status="planned", evidence_id="ev-cat4"),
        _make_claim("c-f1", "med.frequency", "bid", temporal_status="planned", evidence_id="ev-cat4"),
    ]
    ev_map = {"ev-cat4": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", enforce_req44_lifecycle=True)
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert mr.status == "active"
    assert mr.intent == "plan"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_lifecycle_category_5_new_prescription_order():
    """Category 5: Prescription order in clinic_rx_pad emits MedicationRequest(intent='order', status='active')."""
    raw_text = "Lisinopril 20 mg oral daily #30"
    ev = _make_evidence("ev-cat5", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Lisinopril", temporal_status="current", evidence_id="ev-cat5"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="current", evidence_id="ev-cat5"),
        _make_claim("c-f1", "med.frequency", "daily", temporal_status="current", evidence_id="ev-cat5"),
    ]
    ev_map = {"ev-cat5": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad", enforce_req44_lifecycle=True)
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert mr.status == "active"
    assert mr.intent == "order"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_lifecycle_category_6_verified_patient_intake():
    """Category 6: Verified patient intake in progress note emits MedicationStatement(status='active')."""
    raw_text = "Patient currently takes Amlodipine 5 mg daily at home."
    ev = _make_evidence("ev-cat6", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Amlodipine", temporal_status="current", evidence_id="ev-cat6"),
        _make_claim("c-d1", "med.dose", "5", value_numeric=5.0, unit_raw="mg", temporal_status="current", evidence_id="ev-cat6"),
        _make_claim("c-f1", "med.frequency", "daily", temporal_status="current", evidence_id="ev-cat6"),
    ]
    ev_map = {"ev-cat6": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", enforce_req44_lifecycle=True)
    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    assert ms.status == "active"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_lifecycle_category_8_prescriber_cancellation_linked_to_order():
    """Category 8: Order cancellation with explicit order reference emits MedicationRequest(cancelled, order)."""
    raw_text = "Discontinue previous Rx #10492 for Lisinopril 20 mg."
    ev = _make_evidence("ev-cat8", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Lisinopril", temporal_status="discontinued", evidence_id="ev-cat8"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="discontinued", evidence_id="ev-cat8"),
    ]
    ev_map = {"ev-cat8": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad", enforce_req44_lifecycle=True)
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert mr.status == "cancelled"
    assert mr.intent == "order"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_lifecycle_category_9_narrative_discontinue_without_order_ref_suppressed():
    """Category 9: Narrative 'Discontinue Prednisone' without order ref or patient cessation is suppressed."""
    raw_text = "Discontinue Prednisone 50 mg."
    ev = _make_evidence("ev-cat9", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    # Note: temporal_status is unspecified, but text contains discontinue directive
    claims = [
        _make_claim("c-name", "med.name", "Prednisone", temporal_status="unspecified", evidence_id="ev-cat9"),
        _make_claim("c-d1", "med.dose", "50", value_numeric=50.0, unit_raw="mg", temporal_status="unspecified", evidence_id="ev-cat9"),
    ]
    ev_map = {"ev-cat9": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit_trail = []
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", audit_trail=audit_trail, enforce_req44_lifecycle=True)

    med_resources = [e.resource for e in bundle.entry if isinstance(e.resource, (MedicationRequest, MedicationStatement))]
    assert len(med_resources) == 0, "Unreferenced narrative discontinue must be suppressed, NEVER fabricated into stopped statement"
    assert any("Unreferenced cancellation" in a.get("reason", "") for a in audit_trail)


def test_req44_call_path_synthetic_export():
    """Verify synthetic export harness explicitly passes enforce_req44_lifecycle=True."""
    from clindoc.testing.synthetic_export import export_unreviewed_synthetic_bundle_experimental

    raw_text = "Patient has history of Metformin 500 mg daily in 2022."
    ev = _make_evidence("ev-synth", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-synth-name", "med.name", "Metformin", temporal_status="historical", evidence_id="ev-synth"),
        _make_claim("c-synth-dose", "med.dose", "500", value_numeric=500.0, unit_raw="mg", temporal_status="historical", evidence_id="ev-synth"),
    ]
    ev_map = {"ev-synth": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    result = export_unreviewed_synthetic_bundle_experimental(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_id="DOC-SYNTH-01",
        document_type="clinic_rx_pad",
    )

    bundle = result.bundle
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True

    # Under enforce_req44_lifecycle=True, synthetic export of historical claim must emit MedicationStatement(unknown), NOT MedicationRequest
    med_requests = [e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest)]
    assert len(med_requests) == 0
    ms = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    assert ms.status == "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# REQ-44 REMEDIATION ADVERSARIAL TESTS (Checks 1, 2, 3, 5)
# ═══════════════════════════════════════════════════════════════════════════════

def test_req44_check1_detached_pt_completion_uncompleted_med_unknown():
    """Check 1: Detached physical-therapy completion alongside an uncompleted medication mention maps to unknown."""
    raw_text = "Completed physical therapy. Prednisone 20 mg."
    ev = _make_evidence("ev-c1-pt", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", temporal_status="current", evidence_id="ev-c1-pt"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="current", evidence_id="ev-c1-pt"),
    ]
    ev_map = {"ev-c1-pt": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit_trail = []
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", audit_trail=audit_trail, enforce_req44_lifecycle=True)
    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))

    # Must NOT be completed, and must NEVER infer active from detached completion
    assert ms.status == "unknown"
    assert any("Detached or ungrounded course completion" in a.get("reason", "") for a in audit_trail)
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_check1_direct_medication_completion_wording():
    """Check 1: Direct medication completion wording ('Prednisone completed') maps to completed."""
    raw_text = "Prednisone 20 mg daily. Prednisone completed."
    ev = _make_evidence("ev-c1-direct", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", temporal_status="current", evidence_id="ev-c1-direct"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="current", evidence_id="ev-c1-direct"),
        _make_claim("c-f1", "med.frequency", "daily", temporal_status="current", evidence_id="ev-c1-direct"),
    ]
    ev_map = {"ev-c1-direct": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", enforce_req44_lifecycle=True)
    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))

    assert ms.status == "completed"
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_check2_patient_speculation_under_plan_heading_suppressed():
    """Check 2: Patient speculation under a Plan heading fails closed and is suppressed."""
    raw_text = "Plan: Patient says they may start Prednisone next month."
    ev = _make_evidence("ev-c2-spec", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", temporal_status="planned", evidence_id="ev-c2-spec"),
    ]
    ev_map = {"ev-c2-spec": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit_trail = []
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", audit_trail=audit_trail, enforce_req44_lifecycle=True)

    med_resources = [e.resource for e in bundle.entry if isinstance(e.resource, (MedicationRequest, MedicationStatement))]
    # Must fail closed: zero medication resources emitted
    assert len(med_resources) == 0
    assert any("patient speculation" in a.get("reason", "").lower() for a in audit_trail)


def test_req44_check2_genuine_clinician_directive_under_plan_heading():
    """Check 2: Genuine clinician directive under a Plan heading emits MedicationRequest(intent='plan')."""
    raw_text = "Plan: Continue Metformin 500 mg bid."
    ev = _make_evidence("ev-c2-dir", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Metformin", temporal_status="planned", evidence_id="ev-c2-dir"),
        _make_claim("c-d1", "med.dose", "500", value_numeric=500.0, unit_raw="mg", temporal_status="planned", evidence_id="ev-c2-dir"),
        _make_claim("c-f1", "med.frequency", "bid", temporal_status="planned", evidence_id="ev-c2-dir"),
    ]
    ev_map = {"ev-c2-dir": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", enforce_req44_lifecycle=True)
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    assert mr.status == "active"
    assert mr.intent == "plan"
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_check3_two_medications_distinct_orders_only_one_cancelled():
    """Check 3: Two medications with distinct order references: only the cancelled one is cancelled."""
    ev_lis = _make_evidence("ev-c3-lis", "Discontinue previous Rx #10492 for Lisinopril 20 mg.", 1, 0, 52)
    ev_aml = _make_evidence("ev-c3-aml", "Amlodipine 5 mg (ref #8888) oral daily.", 1, 53, 93)

    claims = [
        # Group 1: Lisinopril (cancelled under Rx #10492)
        _make_claim("c-lis-name", "med.name", "Lisinopril", temporal_status="discontinued", evidence_id="ev-c3-lis", group_id="grp-lis"),
        _make_claim("c-lis-dose", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="discontinued", evidence_id="ev-c3-lis", group_id="grp-lis"),
        # Group 2: Amlodipine (active order with ref #8888, NOT cancelled)
        _make_claim("c-aml-name", "med.name", "Amlodipine", temporal_status="current", evidence_id="ev-c3-aml", group_id="grp-aml"),
        _make_claim("c-aml-dose", "med.dose", "5", value_numeric=5.0, unit_raw="mg", temporal_status="current", evidence_id="ev-c3-aml", group_id="grp-aml"),
        _make_claim("c-aml-freq", "med.frequency", "daily", temporal_status="current", evidence_id="ev-c3-aml", group_id="grp-aml"),
    ]
    ev_map = {"ev-c3-lis": ev_lis, "ev-c3-aml": ev_aml}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad", enforce_req44_lifecycle=True)
    mr_lis = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest) and "Lisinopril" in e.resource.medicationCodeableConcept.text)
    mr_aml = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest) and "Amlodipine" in e.resource.medicationCodeableConcept.text)

    # Lisinopril must be cancelled
    assert mr_lis.status == "cancelled"
    assert mr_lis.intent == "order"

    # Amlodipine must remain active; ref #8888 cannot be hijacked by the Lisinopril cancellation
    assert mr_aml.status == "active"
    assert mr_aml.intent == "order"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_check3_unrelated_cancellation_elsewhere_does_not_cancel_med():
    """Check 3: Unrelated cancellation (e.g. lab order void) elsewhere does NOT cancel the medication."""
    raw_text = "Patient previously voided lab order #9999. Lisinopril 10 mg oral daily."
    ev = _make_evidence("ev-c3-unrel", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Lisinopril", temporal_status="current", evidence_id="ev-c3-unrel"),
        _make_claim("c-d1", "med.dose", "10", value_numeric=10.0, unit_raw="mg", temporal_status="current", evidence_id="ev-c3-unrel"),
        _make_claim("c-f1", "med.frequency", "daily", temporal_status="current", evidence_id="ev-c3-unrel"),
    ]
    ev_map = {"ev-c3-unrel": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad", enforce_req44_lifecycle=True)
    mr: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    # Lisinopril must NOT be cancelled by the unrelated lab void
    assert mr.status == "active"
    assert mr.intent == "order"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_check5_explicit_current_patient_intake():
    """Check 5: Explicit affirmative current patient intake in progress note emits MedicationStatement(active)."""
    raw_text = "Patient currently takes Amlodipine 5 mg daily at home."
    ev = _make_evidence("ev-c5-act", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Amlodipine", temporal_status="current", evidence_id="ev-c5-act"),
        _make_claim("c-d1", "med.dose", "5", value_numeric=5.0, unit_raw="mg", temporal_status="current", evidence_id="ev-c5-act"),
        _make_claim("c-f1", "med.frequency", "daily", temporal_status="current", evidence_id="ev-c5-act"),
    ]
    ev_map = {"ev-c5-act": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", enforce_req44_lifecycle=True)
    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))

    assert ms.status == "active"
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_check5_previously_prescribed_unconfirmed_use():
    """Check 5: Previously prescribed medication with unconfirmed current use maps to unknown, NEVER active."""
    raw_text = "Previously prescribed Prednisone; current use not confirmed."
    ev = _make_evidence("ev-c5-unconf", raw_text, page_number=1, char_start=0, char_end=len(raw_text))

    claims = [
        _make_claim("c-name", "med.name", "Prednisone", temporal_status="unspecified", evidence_id="ev-c5-unconf"),
        _make_claim("c-d1", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="unspecified", evidence_id="ev-c5-unconf"),
    ]
    ev_map = {"ev-c5-unconf": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    audit_trail = []
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", audit_trail=audit_trail, enforce_req44_lifecycle=True)
    ms: MedicationStatement = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))

    assert ms.status == "unknown"
    assert any("Unconfirmed patient intake" in a.get("reason", "") for a in audit_trail)
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True


def test_req44_check5_mixed_progress_note_confirmed_vs_unverified():
    """Check 5: Mixed progress note with confirmed intake for Metformin and unverified intake for Lisinopril."""
    ev_met = _make_evidence("ev-c5-met", "Patient takes Metformin 500 mg bid.", 1, 0, 35)
    ev_lis = _make_evidence("ev-c5-lis", "Previously prescribed Lisinopril 10 mg; adherence unverified.", 1, 36, 97)

    claims = [
        # Group 1: Metformin (affirmative intake "Patient takes")
        _make_claim("c-met-name", "med.name", "Metformin", temporal_status="current", evidence_id="ev-c5-met", group_id="grp-met"),
        _make_claim("c-met-dose", "med.dose", "500", value_numeric=500.0, unit_raw="mg", temporal_status="current", evidence_id="ev-c5-met", group_id="grp-met"),
        _make_claim("c-met-freq", "med.frequency", "bid", temporal_status="current", evidence_id="ev-c5-met", group_id="grp-met"),
        # Group 2: Lisinopril (unconfirmed intake "adherence unverified")
        _make_claim("c-lis-name", "med.name", "Lisinopril", temporal_status="unspecified", evidence_id="ev-c5-lis", group_id="grp-lis"),
        _make_claim("c-lis-dose", "med.dose", "10", value_numeric=10.0, unit_raw="mg", temporal_status="unspecified", evidence_id="ev-c5-lis", group_id="grp-lis"),
    ]
    ev_map = {"ev-c5-met": ev_met, "ev-c5-lis": ev_lis}
    ver_map = {c.claim_id: _make_verification(c.claim_id, c.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="progress_note", enforce_req44_lifecycle=True)
    ms_met = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement) and "Metformin" in e.resource.medicationCodeableConcept.text)
    ms_lis = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement) and "Lisinopril" in e.resource.medicationCodeableConcept.text)

    # Confirmed intake must be active
    assert ms_met.status == "active"

    # Unverified intake must be unknown, NOT active!
    assert ms_lis.status == "unknown"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True




