"""Comprehensive test battery for Phase 4.4: DiagnosticReport, Clinical Grouping & Final FHIR R4B Validation.

Authority:
  - PROJECT_SPEC §5 #10, §7.7, §15, §16
  - DEC-008: Hybrid MedicationRequest vs MedicationStatement routing
  - DEC-016: Two-tier provenance hierarchy
  - DEC-020: Base FHIR R4B target (Strictly no fabricated US Core profiles)
  - Phase 4.4 Scope & Acceptance Gates:
      1. Grounded DiagnosticReport creation & suppression
      2. Grounded status, code, results, presentedForm, identifiers
      3. Cross-analyte and cross-medication contamination prevention
      4. Competing candidate deterministic selection & audit trail preservation
      5. Malformed group fail-closed behavior
      6. Observation value[x] exclusivity
      7. Bundle-level referential integrity and profile validation
"""

import datetime
from decimal import Decimal
import pytest

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import DocumentReference
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.quantity import Quantity

from clindoc.db.enums import ClaimReviewState
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir import FHIRValidator, claims_to_fhir_bundle
from clindoc.fhir.constants import (
    EXT_CLAIM_PROVENANCE,
    EXT_COMPETING_CANDIDATE,
    EXT_TEMPORAL_STATUS,
    LOINC_SYSTEM,
    OBS_CATEGORY_SYSTEM,
    RXNORM_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.schema.verification import VerificationResult
from clindoc.services.fhir_export import FHIRExportService


# ─── TEST FIXTURES & HELPERS ─────────────────────────────────────────────────

def _make_evidence(
    ev_id: str = "ev-44-1",
    doc_id: str = "DOC-44-001",
    page: int = 1,
    text: str = "Laboratory Report\nGlucose 110 mg/dL (ref 70-99)",
) -> Evidence:
    return Evidence(
        evidence_id=ev_id,
        document_id=doc_id,
        page_number=page,
        text=text,
        char_start=0,
        char_end=len(text),
        bbox=BBoxModel(x0=10.0, y0=20.0, x1=150.0, y1=35.0),
        ocr_confidence=0.99,
    )


def _make_claim(
    claim_id: str,
    field_type: str,
    value: str,
    doc_id: str = "DOC-44-001",
    group_id: str = "grp-1",
    ev_id: str = "ev-44-1",
    value_numeric: float | None = None,
    unit_raw: str | None = None,
    normalized_code: str | None = None,
    code_system: str | None = None,
    confidence: float = 0.95,
    temporal_status: str = "current",
    negated: bool = False,
    review_state: ClaimReviewState | None = None,
) -> Claim:
    c = Claim(
        claim_id=claim_id,
        document_id=doc_id,
        group_id=group_id,
        field_type=field_type,
        value=value,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        normalized_code=normalized_code,
        code_system=code_system,
        code_confidence=0.95 if normalized_code else 0.0,
        extraction_method="deterministic",
        extraction_confidence=confidence,
        evidence_id=ev_id,
        region_id="reg-1",
        temporal_status=temporal_status,
        negated=negated,
    )
    if review_state is not None:
        object.__setattr__(c, "review_state", review_state)
    return c


def _make_verification(
    claim_id: str,
    ev_id: str = "ev-44-1",
    label: str = "SUPPORTED",
    confidence: float = 0.95,
) -> VerificationResult:
    return VerificationResult(
        claim_id=claim_id,
        label=label,
        confidence=confidence,
        stage_reached="A",
        evidence_id=ev_id,
    )


# ─── 1. DIAGNOSTIC REPORT IMPLEMENTATION & SUPPRESSION ────────────────────────

def test_diagnostic_report_created_for_lab_report_doc_type():
    """Verify DiagnosticReport is created when document represents an explicit lab report."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
        _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="lab_report",
        document_metadata={"report_status": "final"},
    )

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"
    assert val.resource_counts["DiagnosticReport"] == 1
    assert val.resource_counts["Observation"] == 1

    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))
    assert diag.status == "final"
    assert diag.code.coding[0].system == LOINC_SYSTEM
    assert diag.code.coding[0].code == "11502-2"
    assert diag.category[0].coding[0].code == "LAB"
    assert len(diag.result) == 1
    assert diag.result[0].reference.startswith("Observation/")


def test_diagnostic_report_with_grounded_title_loinc_and_identifier():
    """Verify DiagnosticReport preserves grounded title, LOINC panel code, and report identifier."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
        _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    meta = {
        "report_title": "Comprehensive Metabolic Panel",
        "report_loinc": "24323-8",
        "accession_number": "ACC-987654",
        "report_status": "preliminary",
        "issued_at": "2026-10-30T10:00:00Z",
    }

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_metadata=meta,
    )

    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))
    assert diag.status == "preliminary"
    assert diag.code.text == "Comprehensive Metabolic Panel"
    assert diag.code.coding[0].code == "24323-8"
    assert diag.identifier[0].value == "ACC-987654"
    assert diag.issued is not None
    assert diag.issued.year == 2026


def test_diagnostic_report_suppression_when_no_observations():
    """DiagnosticReport must never be created when no Observation resources exist (e.g. medication-only doc)."""
    ev = _make_evidence(text="Metformin 500 mg oral twice daily")
    claims = [
        _make_claim("c-med", "med.name", "Metformin", normalized_code="6809", code_system="RxNorm"),
        _make_claim("c-dose", "med.dose", "500 mg", value_numeric=500.0, unit_raw="mg"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="clinic_rx_pad",
    )

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"
    assert "DiagnosticReport" not in val.resource_counts
    assert val.resource_counts["MedicationRequest"] == 1


def test_diagnostic_report_suppression_on_prescription_pad():
    """Prescription pads and pharmacy labels must suppress DiagnosticReport."""
    ev = _make_evidence(text="Glucose test strip review on Rx pad")
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="clinic_rx_pad",
    )

    # DiagnosticReport is suppressed on prescription pad
    diag = next((e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport)), None)
    assert diag is None
    # Observation is standalone
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"
    assert val.resource_counts["Observation"] == 1


def test_diagnostic_report_suppression_on_pure_narrative_note():
    """A narrative progress note with incidental lab mention must suppress DiagnosticReport."""
    ev = _make_evidence(text="Patient visited clinic. Blood glucose was noted at 110 mg/dL yesterday.")
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
        _make_claim("c-name", "lab.test_name", "Blood glucose"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_id="NARRATIVE-001",
        document_type="progress_note",
    )

    # Observation exists as standalone; DiagnosticReport is suppressed
    diag = next((e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport)), None)
    assert diag is None
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.status == "unknown"  # No grounded report lifecycle in narrative note

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_diagnostic_report_explicit_suppression_flag():
    """Explicit suppress_diagnostic_report=True must suppress DiagnosticReport."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="lab_report",
        document_metadata={"suppress_diagnostic_report": True},
    )

    diag = next((e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport)), None)
    assert diag is None


def test_diagnostic_report_presented_form_linkage():
    """Verify DiagnosticReport links DocumentReference attachment in presentedForm when document ref exists."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
        _make_claim("c-name", "lab.test_name", "Glucose"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="lab_report",
        document_metadata={
            "report_status": "final",
            "filename": "lab_report_001.pdf",
            "mime_type": "application/pdf",
        },
        include_document_reference=True,
    )

    docref: DocumentReference = next(e.resource for e in bundle.entry if isinstance(e.resource, DocumentReference))
    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))

    assert diag.presentedForm is not None
    assert len(diag.presentedForm) == 1
    assert diag.presentedForm[0].contentType == "application/pdf"
    assert diag.presentedForm[0].title == "lab_report_001.pdf"
    assert diag.presentedForm[0].title == docref.content[0].attachment.title


# ─── 2. CLINICAL GROUPING & CANDIDATE SELECTION ──────────────────────────────

def test_multi_analyte_panel_clean_isolation():
    """Ensure multi-analyte panel isolates Glucose, Potassium, and Sodium without cross-contamination."""
    ev = _make_evidence(text="BMP Panel:\nGlucose 110 mg/dL (70-99 H)\nPotassium 4.2 mmol/L (3.5-5.0 N)\nSodium 140 mEq/L (135-145 N)")
    ev_map = {ev.evidence_id: ev}

    claims = [
        # Glucose group
        _make_claim("c-g-name", "lab.test_name", "Glucose", group_id="grp-glucose", normalized_code="2345-7", code_system="LOINC"),
        _make_claim("c-g-val", "lab.value", "110", group_id="grp-glucose", value_numeric=110.0, unit_raw="mg/dL"),
        _make_claim("c-g-unit", "lab.unit", "mg/dL", group_id="grp-glucose", normalized_code="mg/dL", code_system="UCUM"),
        _make_claim("c-g-flag", "lab.flag", "H", group_id="grp-glucose"),
        _make_claim("c-g-ref", "lab.reference_range", "70 - 99", group_id="grp-glucose"),

        # Potassium group
        _make_claim("c-k-name", "lab.test_name", "Potassium", group_id="grp-potassium", normalized_code="2823-3", code_system="LOINC"),
        _make_claim("c-k-val", "lab.value", "4.2", group_id="grp-potassium", value_numeric=4.2, unit_raw="mmol/L"),
        _make_claim("c-k-unit", "lab.unit", "mmol/L", group_id="grp-potassium", normalized_code="mmol/L", code_system="UCUM"),
        _make_claim("c-k-flag", "lab.flag", "N", group_id="grp-potassium"),
        _make_claim("c-k-ref", "lab.reference_range", "3.5 - 5.0", group_id="grp-potassium"),

        # Sodium group
        _make_claim("c-na-name", "lab.test_name", "Sodium", group_id="grp-sodium", normalized_code="2951-2", code_system="LOINC"),
        _make_claim("c-na-val", "lab.value", "140", group_id="grp-sodium", value_numeric=140.0, unit_raw="mEq/L"),
        _make_claim("c-na-unit", "lab.unit", "mEq/L", group_id="grp-sodium", normalized_code="meq/L", code_system="UCUM"),
        _make_claim("c-na-flag", "lab.flag", "N", group_id="grp-sodium"),
        _make_claim("c-na-ref", "lab.reference_range", "135 - 145", group_id="grp-sodium"),
    ]
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="lab_report",
        document_metadata={"report_status": "final"},
    )

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"
    assert val.resource_counts["Observation"] == 3
    assert val.resource_counts["DiagnosticReport"] == 1

    observations = [e.resource for e in bundle.entry if isinstance(e.resource, Observation)]
    obs_map = {o.code.coding[0].code: o for o in observations}

    # Verify Glucose
    obs_g = obs_map["2345-7"]
    assert float(obs_g.valueQuantity.value) == 110.0
    assert obs_g.valueQuantity.unit == "mg/dL"
    assert obs_g.interpretation[0].coding[0].code == "H"
    assert obs_g.referenceRange[0].text == "70 - 99"

    # Verify Potassium
    obs_k = obs_map["2823-3"]
    assert float(obs_k.valueQuantity.value) == 4.2
    assert obs_k.valueQuantity.unit == "mmol/L"
    assert obs_k.interpretation[0].coding[0].code == "N"
    assert obs_k.referenceRange[0].text == "3.5 - 5.0"

    # Verify Sodium
    obs_na = obs_map["2951-2"]
    assert float(obs_na.valueQuantity.value) == 140.0
    assert obs_na.valueQuantity.unit == "mEq/L"

    # Verify DiagnosticReport links all 3 observations
    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))
    assert len(diag.result) == 3
    diag_ref_ids = {r.reference for r in diag.result}
    assert {f"Observation/{o.id}" for o in observations} == diag_ref_ids


def test_cross_analyte_contamination_suppression():
    """If a single group contains claims for conflicting test names, fail closed and suppress the group."""
    ev = _make_evidence(text="Contaminated text block: Glucose 110 Potassium 4.2")
    ev_map = {ev.evidence_id: ev}

    # Malformed group with conflicting test names
    claims = [
        _make_claim("c-name-1", "lab.test_name", "Glucose", group_id="grp-bad", normalized_code="2345-7", code_system="LOINC"),
        _make_claim("c-name-2", "lab.test_name", "Potassium", group_id="grp-bad", normalized_code="2823-3", code_system="LOINC"),
        _make_claim("c-val", "lab.value", "110", group_id="grp-bad", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    audit_trail: list[dict] = []
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="lab_report",
        audit_trail=audit_trail,
    )

    # Group suppressed: 0 observations generated
    observations = [e.resource for e in bundle.entry if isinstance(e.resource, Observation)]
    assert len(observations) == 0

    # Audit trail recorded the contamination failure
    suppressed_entry = next((a for a in audit_trail if a.get("status") == "SUPPRESSED"), None)
    assert suppressed_entry is not None
    assert "Cross-analyte contamination" in suppressed_entry["reason"]


def test_cross_medication_contamination_suppression():
    """If a single group contains claims for conflicting drug names, fail closed and suppress the group."""
    ev = _make_evidence(text="Take Metformin 500 mg and Lisinopril 10 mg")
    ev_map = {ev.evidence_id: ev}

    claims = [
        _make_claim("c-m1", "med.name", "Metformin", group_id="grp-med-bad", normalized_code="6809", code_system="RxNorm"),
        _make_claim("c-m2", "med.name", "Lisinopril", group_id="grp-med-bad", normalized_code="29046", code_system="RxNorm"),
        _make_claim("c-d1", "med.dose", "500 mg", group_id="grp-med-bad", value_numeric=500.0, unit_raw="mg"),
    ]
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    audit_trail: list[dict] = []
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="clinic_rx_pad",
        audit_trail=audit_trail,
    )

    med_res = [e.resource for e in bundle.entry if isinstance(e.resource, (MedicationRequest, MedicationStatement))]
    assert len(med_res) == 0

    suppressed_entry = next((a for a in audit_trail if a.get("status") == "SUPPRESSED"), None)
    assert suppressed_entry is not None
    assert "Cross-medication contamination" in suppressed_entry["reason"]


def test_competing_candidates_deterministic_selection_and_audit():
    """When multiple supported candidates compete for lab.value slot, selection is deterministic and alternatives are preserved."""
    ev = _make_evidence()
    ev_map = {ev.evidence_id: ev}

    # Two competing value candidates for the same group
    c_val_high = _make_claim("c-val-1", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL", confidence=0.98)
    c_val_low = _make_claim("c-val-2", "lab.value", "112", value_numeric=112.0, unit_raw="mg/dL", confidence=0.85)
    c_name = _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC")

    claims = [c_val_high, c_val_low, c_name]
    ver_map = {
        "c-val-1": _make_verification("c-val-1", confidence=0.98),
        "c-val-2": _make_verification("c-val-2", confidence=0.85),
        "c-name": _make_verification("c-name", confidence=0.95),
    }

    audit_trail: list[dict] = []
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="lab_report",
        audit_trail=audit_trail,
    )

    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    # Selected candidate is 110 (higher confidence)
    assert float(obs.valueQuantity.value) == 110.0

    # Competing candidate is recorded in audit trail
    assert len(audit_trail) >= 1
    comp_audit = next((a for a in audit_trail if a.get("competing_claim_id") == "c-val-2"), None)
    assert comp_audit is not None
    assert comp_audit["selected_claim_id"] == "c-val-1"
    assert comp_audit["slot"] == "lab.value"

    # Competing candidate extension is attached on the Observation
    comp_exts = [ext for ext in obs.extension if ext.url == EXT_COMPETING_CANDIDATE]
    assert len(comp_exts) == 1
    sub_map = {s.url: s.valueString for s in comp_exts[0].extension}
    assert sub_map["selectedClaimId"] == "c-val-1"
    assert sub_map["competingClaimId"] == "c-val-2"


def test_competing_candidates_human_review_priority():
    """Human review state (ACCEPTED) overrides raw automated confidence in candidate competition."""
    ev = _make_evidence()
    ev_map = {ev.evidence_id: ev}

    # c-val-1 has lower confidence but is human ACCEPTED
    c_val_accepted = _make_claim(
        "c-val-1", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL",
        confidence=0.75, review_state=ClaimReviewState.ACCEPTED
    )
    # c-val-2 has higher automated confidence but is unreviewed
    c_val_unreviewed = _make_claim(
        "c-val-2", "lab.value", "115", value_numeric=115.0, unit_raw="mg/dL",
        confidence=0.95, review_state=None
    )
    c_name = _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC")

    claims = [c_val_accepted, c_val_unreviewed, c_name]
    ver_map = {
        "c-val-1": _make_verification("c-val-1", confidence=0.75),
        "c-val-2": _make_verification("c-val-2", confidence=0.95),
        "c-name": _make_verification("c-name", confidence=0.95),
    }

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="lab_report",
    )

    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    # Human-accepted candidate 110 selected over unreviewed 115
    assert float(obs.valueQuantity.value) == 110.0


def test_malformed_group_fail_closed():
    """A lab group lacking both test name and value must fail closed."""
    ev = _make_evidence(text="Just a flag: Normal")
    ev_map = {ev.evidence_id: ev}
    claims = [
        _make_claim("c-flag", "lab.flag", "NORMAL", group_id="grp-empty"),
    ]
    ver_map = {"c-flag": _make_verification("c-flag")}

    audit_trail: list[dict] = []
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        audit_trail=audit_trail,
    )

    obs = [e.resource for e in bundle.entry if isinstance(e.resource, Observation)]
    assert len(obs) == 0
    assert any(a.get("status") == "SUPPRESSED" and "Malformed group" in a.get("reason", "") for a in audit_trail)


def test_medication_dosage_relationships():
    """Verify medication name, dose, route, frequency and duration form correct Dosage relationships."""
    ev = _make_evidence(text="Metformin 500 mg PO BID with meals for 30 days")
    ev_map = {ev.evidence_id: ev}

    claims = [
        _make_claim("c-name", "med.name", "Metformin", group_id="grp-m", normalized_code="6809", code_system="RxNorm"),
        _make_claim("c-dose", "med.dose", "500 mg", group_id="grp-m", value_numeric=500.0, unit_raw="mg"),
        _make_claim("c-route", "med.route", "oral", group_id="grp-m"),
        _make_claim("c-freq", "med.frequency", "twice daily", group_id="grp-m"),
        _make_claim("c-dur", "med.duration", "30 days", group_id="grp-m"),
    ]
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="clinic_rx_pad",
    )

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"

    med_req: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    dosage = med_req.dosageInstruction[0]

    assert dosage.route.text == "oral"
    assert dosage.timing.code.text == "twice daily"
    assert float(dosage.doseAndRate[0].doseQuantity.value) == 500.0
    assert dosage.doseAndRate[0].doseQuantity.unit == "mg"
    assert "for 30 days" in dosage.text


# ─── 3. FINAL FHIR R4B VALIDATION ─────────────────────────────────────────────

def test_observation_value_exclusivity_validation():
    """Observation with multiple value[x] populated must be rejected by FHIRValidator."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="lab_report",
    )

    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Illegally populate a second value[x]
    object.__setattr__(obs, "valueString", "110 mg/dL")

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is False
    assert any(("multiple value[x]" in err) or ("got multiple" in err) or ("Any of one field value is expected" in err) for err in val.errors)

    # Illegally remove all value[x]
    object.__setattr__(obs, "valueString", None)
    object.__setattr__(obs, "valueQuantity", None)
    val2 = FHIRValidator.validate_bundle(bundle)
    assert val2.is_valid is False
    assert any("missing value[x]" in err for err in val2.errors)


def test_dec020_us_core_profile_rejection():
    """DEC-020: Fabricated US Core profile URLs on any resource or bundle must be rejected."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="lab_report",
    )

    # Illegally inject US Core profile
    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    from fhir.resources.R4B.meta import Meta
    obs.meta = Meta(profile=["http://hl7.org/fhir/us/core/StructureDefinition/us-core-observation-lab"])

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is False
    assert any("violates DEC-020" in err for err in val.errors)


def test_full_bundle_referential_integrity():
    """Verify end-to-end referential integrity across Patient, Org, Encounter, DocRef, DiagReport, Obs, Meds, Prov."""
    ev = _make_evidence()
    ev_map = {ev.evidence_id: ev}

    claims = [
        _make_claim("c-v", "lab.value", "110", group_id="grp-l", value_numeric=110.0, unit_raw="mg/dL"),
        _make_claim("c-n", "lab.test_name", "Glucose", group_id="grp-l", normalized_code="2345-7", code_system="LOINC"),
        _make_claim("c-m", "med.name", "Metformin", group_id="grp-m", normalized_code="6809", code_system="RxNorm"),
        _make_claim("c-d", "med.dose", "500 mg", group_id="grp-m", value_numeric=500.0, unit_raw="mg"),
    ]
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    service = FHIRExportService()
    result = service.export_intermediate_representation(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="lab_report",
        document_metadata={
            "report_status": "final",
            "filename": "full_report.pdf",
            "source_bytes": b"%PDF-1.4 simulated pdf document bytes for hash test",
        },
        encounter_info={"encounter_id": "ENC-44"},
        include_document_reference=True,
        include_provenance_resource=True,
    )

    assert result.is_valid is True, f"Validation errors: {result.validation_report.errors}"
    assert result.bundle_sha256 is not None
    assert len(result.bundle_sha256) == 64
    assert result.exportable_claim_ids == ["c-v", "c-n", "c-m", "c-d"]
    assert len(result.non_exportable_claims) == 0

    # Ensure all resources present
    rc = result.validation_report.resource_counts
    assert rc["Patient"] == 1
    assert rc["Organization"] == 1
    assert rc["DocumentReference"] == 1
    assert rc["Encounter"] == 1
    assert rc["Observation"] == 1
    assert rc["DiagnosticReport"] == 1
    assert rc["MedicationStatement"] == 1
    assert rc["Device"] == 1
    assert rc["Provenance"] == 1
