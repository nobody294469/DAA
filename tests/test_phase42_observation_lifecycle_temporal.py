"""Comprehensive test battery for Phase 4.2: FHIR Observation Lifecycle & Temporal Semantics.

Authority:
  - Phase 4.2 Scope & Acceptance Criteria
  - Decisions DEC-008, DEC-015, DEC-016
  - Proposed DEC-020, DEC-021, DEC-022
  - Phase 4.0.1, Phase 4.0.2, Phase 4.1, Phase 4.1.1
  - HL7 FHIR R4B Observation, DiagnosticReport, MedicationRequest, MedicationStatement standards
"""

import datetime
from decimal import Decimal
import json
import pytest

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.dosage import Dosage
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.observation import Observation

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir import FHIRValidator, claims_to_fhir_bundle
from clindoc.fhir.constants import (
    EXT_CLAIM_PROVENANCE,
    EXT_TEMPORAL_STATUS,
    LOINC_SYSTEM,
    OBS_CATEGORY_SYSTEM,
    RXNORM_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.db.enums import ClaimReviewState
from clindoc.schema.verification import VerificationResult
from clindoc.services.fhir_export import FHIRExportService


# ─── FIXTURES & FACTORIES ────────────────────────────────────────────────────

def _make_evidence(
    ev_id: str = "ev-42-1",
    doc_id: str = "DOC-42-001",
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
    ev_id: str = "ev-42-1",
    label: str = "SUPPORTED",
    confidence: float = 0.98,
    stage_reached: str = "A",
    contradiction_type: str | None = None,
) -> VerificationResult:
    c_type = contradiction_type or ("value_mismatch" if label == "DISCREPANCY" else None)
    return VerificationResult(
        claim_id=claim_id,
        label=label,
        confidence=confidence,
        stage_reached=stage_reached,
        evidence_id=ev_id,
        contradiction_type=c_type,
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
    group_id: str = "grp-42-1",
    evidence_id: str = "ev-42-1",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        document_id="DOC-42-001",
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


# ─── 1. OBSERVATION LIFECYCLE TESTS ──────────────────────────────────────────

@pytest.mark.parametrize(
    "status_key,expected_status",
    [
        ("preliminary", "preliminary"),
        ("final", "final"),
        ("amended", "amended"),
        ("corrected", "corrected"),
        ("cancelled", "cancelled"),
        ("unknown", "unknown"),
    ],
)
def test_all_supported_observation_lifecycle_states(status_key, expected_status):
    """Verify that every supported Observation lifecycle state maps accurately."""
    ev = _make_evidence(text=f"Report Status: {status_key.title()}\nGlucose: 105 mg/dL")
    claims = [
        _make_claim("c-val", "lab.value", "105", value_numeric=105.0, unit_raw="mg/dL"),
        _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_metadata={"report_status": status_key},
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.status == expected_status

    diag = next((e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport)), None)
    assert diag is not None
    assert diag.status == expected_status

    val_res = FHIRValidator.validate_bundle(bundle)
    assert val_res.is_valid, f"Bundle validation failed: {val_res.errors}"


def test_supported_claim_in_explicitly_preliminary_report_remains_preliminary():
    """A claim with SUPPORTED verdict in an explicitly preliminary report remains preliminary."""
    ev = _make_evidence(text="Preliminary Report\nPotassium: 4.5 mmol/L")
    claims = [
        _make_claim("c-val", "lab.value", "4.5", value_numeric=4.5, unit_raw="mmol/L"),
        _make_claim("c-name", "lab.test_name", "Potassium", normalized_code="2823-3", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    # Strong SUPPORTED verdict
    ver_map = {
        "c-val": _make_verification("c-val", ev_id=ev.evidence_id, label="SUPPORTED", confidence=0.99),
        "c-name": _make_verification("c-name", ev_id=ev.evidence_id, label="SUPPORTED", confidence=0.99),
    }

    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_metadata={"report_status": "preliminary"},
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.status == "preliminary", "SUPPORTED verdict must NOT override preliminary lifecycle"


def test_discrepancy_claim_in_explicitly_final_report_remains_final():
    """A DISCREPANCY claim in an explicitly final report remains final in Observation.status."""
    ev = _make_evidence(text="Report Status: Final\nSodium: 138 mEq/L")
    claims = [
        _make_claim("c-val", "lab.value", "138", value_numeric=138.0, unit_raw="mEq/L"),
        _make_claim("c-name", "lab.test_name", "Sodium", normalized_code="2951-2", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    # DISCREPANCY verdict
    ver_map = {
        "c-val": _make_verification("c-val", ev_id=ev.evidence_id, label="DISCREPANCY", confidence=0.82),
        "c-name": _make_verification("c-name", ev_id=ev.evidence_id, label="SUPPORTED", confidence=0.95),
    }

    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_metadata={"report_status": "final"},
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.status == "final", "DISCREPANCY verdict must NOT alter grounded final report lifecycle"


def test_bare_narrative_with_supported_verdict_remains_unknown():
    """A bare narrative with a SUPPORTED verdict and no report lifecycle metadata remains unknown."""
    ev = _make_evidence(text="The patient's glucose was documented at 110 mg/dL during rounds.")
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
        _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {
        "c-val": _make_verification("c-val", ev_id=ev.evidence_id, label="SUPPORTED", confidence=0.98),
        "c-name": _make_verification("c-name", ev_id=ev.evidence_id, label="SUPPORTED", confidence=0.98),
    }

    # Narrative note without explicit report_status
    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_type="narrative_note",
        document_metadata=None,
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.status == "unknown", "Bare narrative with SUPPORTED verdict must have status='unknown'"


@pytest.mark.parametrize(
    "header_text,expected_status",
    [
        ("Report Status: Preliminary", "preliminary"),
        ("Report Status: Final", "final"),
        ("Diagnostic Status: Amended", "amended"),
        ("Lab Status: Corrected", "corrected"),
        ("Report Status: Cancelled", "cancelled"),
        ("FINAL REPORT\nPatient Labs", "final"),
        ("PRELIMINARY REPORT\nPatient Labs", "preliminary"),
    ],
)
def test_header_grounded_lifecycle_detection_from_evidence(header_text, expected_status):
    """Verify that explicit diagnostic report status headers in evidence text are recognized."""
    ev = _make_evidence(text=f"{header_text}\nHemoglobin: 14.2 g/dL")
    claims = [
        _make_claim("c-val", "lab.value", "14.2", value_numeric=14.2, unit_raw="g/dL"),
        _make_claim("c-name", "lab.test_name", "Hemoglobin", normalized_code="718-7", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    # No document_metadata passed: must detect from grounded evidence header
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.status == expected_status


def test_human_accepted_review_state_does_not_change_observation_status():
    """Human review state (ACCEPTED) must not alter the grounded clinical lifecycle status."""
    ev = _make_evidence(text="Preliminary Report\nPlatelets: 250 K/uL")
    c_val = _make_claim("c-val", "lab.value", "250", value_numeric=250.0, unit_raw="K/uL")
    c_name = _make_claim("c-name", "lab.test_name", "Platelets", normalized_code="777-3", code_system="LOINC")

    ev_map = {ev.evidence_id: ev}
    ver_val = _make_verification("c-val", ev_id=ev.evidence_id)
    ver_name = _make_verification("c-name", ev_id=ev.evidence_id)
    ver_map = {"c-val": ver_val, "c-name": ver_name}

    # Verify eligibility passes when human reviewer marks claim ACCEPTED
    claim_dict = c_val.model_dump()
    claim_dict["review_state"] = "ACCEPTED"
    eval_val = FHIRExportService.evaluate_claim_eligibility(claim_dict, ver_val, ev)
    assert eval_val.is_eligible is True

    # Bundle mapping must preserve the grounded preliminary status, NOT change to 'final'
    bundle = claims_to_fhir_bundle(
        [c_val, c_name],
        ev_map,
        ver_map,
        document_metadata={"report_status": "preliminary"},
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.status == "preliminary", "Human ACCEPTED state must NOT change Observation.status to 'final'"


# ─── 2. TEMPORAL STATUS EXTENSION & TIMING TESTS ─────────────────────────────

def test_current_and_historical_findings_in_same_document():
    """Verify that current and historical observations in the same document are isolated cleanly."""
    ev_cur = _make_evidence(ev_id="ev-cur", text="Current Labs: Glucose 110 mg/dL")
    ev_hist = _make_evidence(ev_id="ev-hist", text="Previous discharge labs: Glucose was 95 mg/dL")

    claims_cur = [
        _make_claim("c-cur-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL", temporal_status="current", group_id="grp-cur", evidence_id="ev-cur"),
        _make_claim("c-cur-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC", temporal_status="current", group_id="grp-cur", evidence_id="ev-cur"),
    ]
    claims_hist = [
        _make_claim("c-hist-val", "lab.value", "95", value_numeric=95.0, unit_raw="mg/dL", temporal_status="historical", group_id="grp-hist", evidence_id="ev-hist"),
        _make_claim("c-hist-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC", temporal_status="historical", group_id="grp-hist", evidence_id="ev-hist"),
    ]

    ev_map = {"ev-cur": ev_cur, "ev-hist": ev_hist}
    ver_map = {
        "c-cur-val": _make_verification("c-cur-val", ev_id="ev-cur"),
        "c-cur-name": _make_verification("c-cur-name", ev_id="ev-cur"),
        "c-hist-val": _make_verification("c-hist-val", ev_id="ev-hist"),
        "c-hist-name": _make_verification("c-hist-name", ev_id="ev-hist"),
    }

    bundle = claims_to_fhir_bundle(claims_cur + claims_hist, ev_map, ver_map)
    observations = [e.resource for e in bundle.entry if isinstance(e.resource, Observation)]
    assert len(observations) == 2

    obs_cur = next(o for o in observations if float(o.valueQuantity.value) == 110.0)
    obs_hist = next(o for o in observations if float(o.valueQuantity.value) == 95.0)

    # Current Observation extension
    temp_ext_cur = next(ext for ext in obs_cur.extension if ext.url == EXT_TEMPORAL_STATUS)
    assert temp_ext_cur.valueCode == "current"
    assert obs_cur.note is None, "Current observation must not carry historical explanatory note"

    # Historical Observation extension and note
    temp_ext_hist = next(ext for ext in obs_hist.extension if ext.url == EXT_TEMPORAL_STATUS)
    assert temp_ext_hist.valueCode == "historical"
    assert obs_hist.note is not None
    assert "Historical laboratory finding documented in clinical narrative." in obs_hist.note[0].text


def test_historical_finding_without_date_has_no_fabricated_effective_time():
    """Historical findings without explicit collection timing must omit effective[x] and encounter date."""
    ev = _make_evidence(text="Prior glucose was 95 mg/dL")
    claims = [
        _make_claim("c-val", "lab.value", "95", value_numeric=95.0, unit_raw="mg/dL", temporal_status="historical"),
        _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC", temporal_status="historical"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    encounter_info = {
        "encounter_id": "ENC-99",
        "encounter_class": "IMP",
        "status": "finished",
        "period_start": "2026-10-15T08:00:00Z",
    }

    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        encounter_info=encounter_info,
        document_metadata=None,
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Invariants: no fabricated effectiveDateTime, effectivePeriod, or issued
    assert obs.effectiveDateTime is None, "Encounter date must NEVER be treated as collection time"
    assert obs.effectivePeriod is None
    assert obs.issued is None


def test_date_only_precision_preserved_without_invented_midnight_or_timezone():
    """Date-only collection timing preserves YYYY-MM-DD precision without fabricating 00:00:00 or timezone."""
    ev = _make_evidence(text="Collection Date: 2026-10-15\nPotassium: 4.2 mmol/L")
    claims = [
        _make_claim("c-val", "lab.value", "4.2", value_numeric=4.2, unit_raw="mmol/L"),
        _make_claim("c-name", "lab.test_name", "Potassium", normalized_code="2823-3", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_metadata={"collection_date": "2026-10-15"},
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    assert obs.effectiveDateTime == datetime.date(2026, 10, 15)
    obs_json = json.loads(obs.json())
    assert obs_json["effectiveDateTime"] == "2026-10-15"
    assert "T00:00:00" not in obs_json["effectiveDateTime"]


def test_full_timestamp_precision_with_grounded_timezone():
    """Full timestamp with explicit timezone is preserved faithfully."""
    ev = _make_evidence(text="Specimen collected: 2026-10-15T08:30:00Z\nGlucose: 105 mg/dL")
    claims = [
        _make_claim("c-val", "lab.value", "105", value_numeric=105.0, unit_raw="mg/dL"),
        _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_metadata={"collection_time": "2026-10-15T08:30:00Z"},
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.effectiveDateTime is not None
    obs_json = json.loads(obs.json())
    assert "2026-10-15T08:30:00" in obs_json["effectiveDateTime"]


def test_missing_timezone_in_timestamp_omits_effective_time_safely():
    """When a timestamp lacks timezone information, effectiveDateTime is omitted rather than guessing."""
    ev = _make_evidence(text="Collection time: 2026-10-15T08:30:00 (no tz)\nGlucose: 105 mg/dL")
    claims = [
        _make_claim("c-val", "lab.value", "105", value_numeric=105.0, unit_raw="mg/dL"),
        _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_metadata={"collection_time": "2026-10-15T08:30:00"},
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.effectiveDateTime is None, "Timestamps without explicit timezone must be safely omitted"


def test_effective_period_populated_when_both_endpoints_grounded():
    """When both collection start and end are grounded, effectivePeriod is populated."""
    ev = _make_evidence(text="24-hour urine collection: 2026-10-14 08:00 to 2026-10-15 08:00")
    claims = [
        _make_claim("c-val", "lab.value", "1500", value_numeric=1500.0, unit_raw="mL"),
        _make_claim("c-name", "lab.test_name", "Urine Volume", normalized_code="2162-6", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    period_dict = {
        "start": "2026-10-14T08:00:00Z",
        "end": "2026-10-15T08:00:00Z",
    }
    bundle = claims_to_fhir_bundle(
        claims,
        ev_map,
        ver_map,
        document_metadata={"effective_period": period_dict},
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.effectivePeriod is not None
    assert obs.effectiveDateTime is None
    obs_json = json.loads(obs.json())
    assert obs_json["effectivePeriod"]["start"] == "2026-10-14T08:00:00+00:00"
    assert obs_json["effectivePeriod"]["end"] == "2026-10-15T08:00:00+00:00"


def test_planned_diagnostic_test_does_not_become_observation():
    """Planned diagnostic orders must never emit an Observation resource."""
    ev = _make_evidence(text="Plan: Order lipid panel next week.")
    claims = [
        _make_claim("c-plan-name", "lab.test_name", "Lipid Panel", temporal_status="planned"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {"c-plan-name": _make_verification("c-plan-name", ev_id=ev.evidence_id)}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    observations = [e.resource for e in bundle.entry if isinstance(e.resource, Observation)]
    assert len(observations) == 0, "Planned diagnostic test must NEVER generate an Observation"


def test_unspecified_temporal_status_omits_extension():
    """Unspecified temporal status must omit the temporal-status extension entirely."""
    ev = _make_evidence(text="Hemoglobin: 14.0 g/dL")
    claims = [
        _make_claim("c-val", "lab.value", "14.0", value_numeric=14.0, unit_raw="g/dL", temporal_status="unspecified"),
        _make_claim("c-name", "lab.test_name", "Hemoglobin", normalized_code="718-7", code_system="LOINC", temporal_status="unspecified"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Only provenance extension should be present
    temp_ext = next((ext for ext in obs.extension if ext.url == EXT_TEMPORAL_STATUS), None)
    assert temp_ext is None, "Extension must be omitted when temporal_status is 'unspecified'"


# ─── 3. MEDICATION LIFECYCLE & TEMPORAL TESTS ────────────────────────────────

def test_medication_discontinued_must_not_be_active():
    """Discontinued medications must map to stopped status (never active)."""
    ev = _make_evidence(text="Discontinue Lisinopril 20 mg oral daily.")
    claims = [
        _make_claim("c-med-name", "med.name", "Lisinopril", normalized_code="29046", code_system="RxNorm", temporal_status="discontinued"),
        _make_claim("c-med-dose", "med.dose", "20", value_numeric=20.0, unit_raw="mg", temporal_status="discontinued"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    # 1. Statement routing (discharge_summary)
    bundle_stat = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="discharge_summary")
    ms = next(e.resource for e in bundle_stat.entry if isinstance(e.resource, MedicationStatement))
    assert ms.status == "stopped", "Discontinued MedicationStatement must have status='stopped'"

    # 2. Request routing (clinic_rx_pad)
    bundle_req = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr = next(e.resource for e in bundle_req.entry if isinstance(e.resource, MedicationRequest))
    assert mr.status == "stopped", "Discontinued MedicationRequest must have status='stopped'"


def test_historical_medication_statement_status_is_unknown_not_completed():
    """A historical medication mention in narrative does not automatically mean course was completed."""
    ev = _make_evidence(text="Past medical history mentions Metformin 500 mg daily.")
    claims = [
        _make_claim("c-med-name", "med.name", "Metformin", normalized_code="6809", code_system="RxNorm", temporal_status="historical"),
        _make_claim("c-med-dose", "med.dose", "500", value_numeric=500.0, unit_raw="mg", temporal_status="historical"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="discharge_summary")
    ms = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))

    # MedicationStatement.status for historical mention is 'unknown'
    assert ms.status == "unknown", "Historical medication mention must map to 'unknown' (not completed)"

    # Temporal extension present
    temp_ext = next(ext for ext in ms.extension if ext.url == EXT_TEMPORAL_STATUS)
    assert temp_ext.valueCode == "historical"


def test_medication_unspecified_omits_temporal_extension():
    """Medication with unspecified temporal status omits the extension entirely."""
    ev = _make_evidence(text="Atorvastatin 40 mg oral daily.")
    claims = [
        _make_claim("c-med-name", "med.name", "Atorvastatin", normalized_code="83367", code_system="RxNorm", temporal_status="unspecified"),
        _make_claim("c-med-dose", "med.dose", "40", value_numeric=40.0, unit_raw="mg", temporal_status="unspecified"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    temp_ext = next((ext for ext in mr.extension if ext.url == EXT_TEMPORAL_STATUS), None)
    assert temp_ext is None, "Temporal extension must be omitted when temporal_status is 'unspecified'"


# ─── 4. PHASE 4.2.1 FORMAL DEFINITIONS & RESOURCE-SPECIFIC RESTRICTIONS ──────

def test_formal_temporal_extension_definitions_exist_and_validate():
    """Verify that machine-readable StructureDefinition, ValueSet, and CodeSystem exist and validate."""
    from clindoc.fhir.definitions import (
        get_temporal_status_codesystem,
        get_temporal_status_valueset,
        get_temporal_status_structuredefinition,
    )
    from clindoc.fhir.constants import (
        ALL_TEMPORAL_STATUS_CODES,
        TEMPORAL_STATUS_SYSTEM,
        TEMPORAL_STATUS_VALUESET,
        EXT_TEMPORAL_STATUS,
    )

    # 1. CodeSystem
    cs = get_temporal_status_codesystem()
    assert cs.url == TEMPORAL_STATUS_SYSTEM
    assert cs.status == "draft"
    cs_codes = {c.code for c in cs.concept}
    assert cs_codes == ALL_TEMPORAL_STATUS_CODES, f"CodeSystem concept mismatch: {cs_codes}"
    assert cs_codes == {"current", "historical", "planned", "discontinued"}

    # 2. ValueSet
    vs = get_temporal_status_valueset()
    assert vs.url == TEMPORAL_STATUS_VALUESET
    assert vs.status == "draft"
    vs_codes = {c.code for inc in vs.compose.include for c in inc.concept}
    assert vs_codes == ALL_TEMPORAL_STATUS_CODES, f"ValueSet concept mismatch: {vs_codes}"

    # 3. StructureDefinition
    sd = get_temporal_status_structuredefinition()
    assert sd.url == EXT_TEMPORAL_STATUS
    assert sd.status == "draft"
    assert sd.type == "Extension"
    assert sd.baseDefinition == "http://hl7.org/fhir/StructureDefinition/Extension"
    context_exprs = {ctx.expression for ctx in sd.context}
    assert "Observation" in context_exprs
    assert "MedicationStatement" in context_exprs
    assert "MedicationRequest" in context_exprs


def test_resource_specific_temporal_medication_request_planned_and_discontinued():
    """MedicationRequest supports current, historical, planned, and discontinued."""
    ev = _make_evidence(text="Plan: Start Metoprolol 25 mg oral daily.")
    claims = [
        _make_claim("c-med-name", "med.name", "Metoprolol", normalized_code="6868", code_system="RxNorm", temporal_status="planned"),
        _make_claim("c-med-dose", "med.dose", "25", value_numeric=25.0, unit_raw="mg", temporal_status="planned"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")
    mr = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert mr.intent == "plan"
    temp_ext = next(ext for ext in mr.extension if ext.url == EXT_TEMPORAL_STATUS)
    assert temp_ext.valueCode == "planned"

    # Validator check
    validator = FHIRValidator()
    result = validator.validate_bundle(bundle)
    assert result.is_valid, f"Validation errors: {result.errors}"


def test_resource_specific_temporal_medication_statement_discontinued():
    """MedicationStatement supports discontinued; extension has valueCode='discontinued'."""
    ev = _make_evidence(text="Patient stopped taking Aspirin 81 mg.")
    claims = [
        _make_claim("c-med-name", "med.name", "Aspirin", normalized_code="1191", code_system="RxNorm", temporal_status="discontinued"),
        _make_claim("c-med-dose", "med.dose", "81", value_numeric=81.0, unit_raw="mg", temporal_status="discontinued"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="discharge_summary")
    ms = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    assert ms.status == "stopped"
    temp_ext = next(ext for ext in ms.extension if ext.url == EXT_TEMPORAL_STATUS)
    assert temp_ext.valueCode == "discontinued"

    validator = FHIRValidator()
    result = validator.validate_bundle(bundle)
    assert result.is_valid, f"Validation errors: {result.errors}"


def test_validator_rejects_disallowed_temporal_state_for_observation():
    """FHIRValidator must reject an Observation if an illegal temporal code (e.g. discontinued, planned) is attached."""
    from fhir.resources.R4B.extension import Extension

    ev = _make_evidence(text="Potassium 4.0 mmol/L")
    claims = [
        _make_claim("c-val", "lab.value", "4.0", value_numeric=4.0, unit_raw="mmol/L", temporal_status="current"),
        _make_claim("c-name", "lab.test_name", "Potassium", normalized_code="2823-3", code_system="LOINC", temporal_status="current"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id=ev.evidence_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Manually inject an invalid temporal status extension for Observation
    obs.extension.append(Extension(url=EXT_TEMPORAL_STATUS, valueCode="discontinued"))

    validator = FHIRValidator()
    res = validator.validate_bundle(bundle)
    assert not res.is_valid
    assert any("invalid temporal status 'discontinued'" in err for err in res.errors)
