"""Tests for Phase 4.4.1: FHIR R4B Conformance & Terminology Serialization Audit.

Authority:
  - Phase 4.4.1 Specification
  - FHIR R4B 4.3.0 Base DiagnosticReport and Observation Specifications
  - HL7 DiagnosticReport.status ValueSet (http://hl7.org/fhir/diagnostic-report-status)
  - DEC-020: Base FHIR R4B Target
"""

import pytest
from decimal import Decimal
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.extension import Extension
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir.constants import (
    DIAGNOSTIC_REPORT_STATUSES,
    LOINC_SYSTEM,
    RXNORM_SYSTEM,
    SNOMED_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.fhir.mapper import (
    claims_to_fhir_bundle,
    resolve_diagnostic_report_lifecycle_status,
    resolve_observation_lifecycle_status,
)
from clindoc.fhir.validator import FHIRValidator
from clindoc.schema.verification import VerificationResult


# ─── FIXTURES & HELPERS ───────────────────────────────────────────────────────

def _make_evidence(
    ev_id: str = "ev-441-1",
    doc_id: str = "DOC-441-001",
    text: str = "Laboratory Report\nGlucose 110 mg/dL",
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


def _make_claim(
    claim_id: str,
    field_type: str,
    value: str,
    doc_id: str = "DOC-441-001",
    group_id: str = "grp-1",
    ev_id: str = "ev-441-1",
    value_numeric: float | None = None,
    unit_raw: str | None = None,
    normalized_code: str | None = None,
    code_system: str | None = None,
) -> Claim:
    return Claim(
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
        extraction_confidence=0.95,
        evidence_id=ev_id,
        region_id="reg-1",
    )


def _make_verification(claim_id: str, label: str = "SUPPORTED") -> VerificationResult:
    return VerificationResult(
        claim_id=claim_id,
        label=label,
        confidence=0.95,
        stage_reached="A",
        evidence_id="ev-441-1",
    )


# ─── SECTION 1: DIAGNOSTIC REPORT STATUS COMPLETENESS ─────────────────────────

def test_diagnostic_report_status_value_set_completeness():
    """Verify all 10 FHIR R4B DiagnosticReport statuses are present in the approved set."""
    expected_statuses = {
        "registered",
        "partial",
        "preliminary",
        "final",
        "amended",
        "corrected",
        "appended",
        "cancelled",
        "entered-in-error",
        "unknown",
    }
    assert DIAGNOSTIC_REPORT_STATUSES == expected_statuses
    assert "partial" in DIAGNOSTIC_REPORT_STATUSES


def test_diagnostic_report_partial_status_grounded_in_metadata():
    """Explicit grounded metadata report_status='partial' maps to DiagnosticReport.status='partial'."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={"c-val": _make_verification("c-val")},
        document_type="lab_report",
        document_metadata={"report_status": "partial"},
    )

    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))
    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    assert diag.status == "partial"
    # Phase 4.4.2 lifecycle independence: Observation lacking explicit observation-level lifecycle evidence remains 'unknown'
    assert obs.status == "unknown"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_diagnostic_report_partial_status_grounded_in_evidence_header():
    """Explicit report header 'Report Status: Partial' maps to DiagnosticReport.status='partial'."""
    ev = _make_evidence(text="Laboratory Report\nReport Status: Partial\nGlucose 110 mg/dL")
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={"c-val": _make_verification("c-val")},
        document_type="lab_report",
    )

    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))
    assert diag.status == "partial"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_diagnostic_report_no_synthetic_partial_inference():
    """Pipeline progress or verification status must NEVER synthesize a 'partial' status."""
    ev = _make_evidence(text="Clinical Narrative Note\nGlucose 110 mg/dL")
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    # No grounded report status in metadata or text
    status = resolve_diagnostic_report_lifecycle_status(
        document_metadata={},
        document_type="narrative_note",
        evidence_texts=[ev.text],
    )
    # Must remain 'unknown', never inferred as 'partial'
    assert status == "unknown"


def test_diagnostic_report_invalid_status_rejected_by_validator():
    """Invalid non-R4B status strings must be rejected by FHIRValidator."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={"c-val": _make_verification("c-val")},
        document_type="lab_report",
        document_metadata={"report_status": "final"},
    )
    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))

    # Illegally set invalid status
    object.__setattr__(diag, "status", "active")
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is False
    assert any("DiagnosticReport" in err and "invalid status 'active'" in err for err in val.errors)


# ─── SECTION 2: OBSERVATION VALUE[X] CARDINALITY & DATA ABSENT REASON ────────

def test_observation_exactly_one_value_quantity_valid():
    """Atomic observation with exactly one valueQuantity is valid."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={"c-val": _make_verification("c-val")},
        document_type="lab_report",
    )
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_observation_multiple_value_choices_rejected():
    """Observation with multiple value[x] choices populated must be rejected."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={"c-val": _make_verification("c-val")},
        document_type="lab_report",
    )
    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Illegally populate second value choice
    object.__setattr__(obs, "valueString", "110 mg/dL")
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is False
    assert any(
        ("multiple value[x]" in err) or ("got multiple" in err) or ("Any of one field value is expected" in err)
        for err in val.errors
    )


def test_observation_zero_value_with_valid_data_absent_reason_valid():
    """Observation with no value[x] but a valid dataAbsentReason is valid under FHIR R4B and ClinicalDoc."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={"c-val": _make_verification("c-val")},
        document_type="lab_report",
    )
    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Set valueQuantity to None and supply dataAbsentReason
    object.__setattr__(obs, "valueQuantity", None)
    object.__setattr__(
        obs,
        "dataAbsentReason",
        CodeableConcept(
            coding=[
                Coding(
                    system="http://terminology.hl7.org/CodeSystem/data-absent-reason",
                    code="specimen-unsatisfactory",
                    display="Unsatisfactory Specimen",
                )
            ],
            text="Specimen hemolyzed",
        ),
    )
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_observation_zero_value_and_no_data_absent_reason_rejected():
    """Observation with neither value[x] nor dataAbsentReason violates ClinicalDoc application constraints."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={"c-val": _make_verification("c-val")},
        document_type="lab_report",
    )
    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Strip valueQuantity without providing dataAbsentReason
    object.__setattr__(obs, "valueQuantity", None)
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is False
    assert any("missing value[x] without dataAbsentReason" in err for err in val.errors)


def test_observation_both_value_and_data_absent_reason_rejected():
    """Observation with BOTH value[x] and dataAbsentReason violates FHIR R4B specification invariant."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={"c-val": _make_verification("c-val")},
        document_type="lab_report",
    )
    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Supply dataAbsentReason while keeping valueQuantity populated
    object.__setattr__(
        obs,
        "dataAbsentReason",
        CodeableConcept(
            coding=[
                Coding(
                    system="http://terminology.hl7.org/CodeSystem/data-absent-reason",
                    code="unknown",
                    display="Unknown",
                )
            ]
        ),
    )
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is False
    assert any("both value[x] and dataAbsentReason" in err for err in val.errors)


# ─── SECTION 3: NARRATIVE DIAGNOSTIC REPORT AUDIT & ISOLATION ─────────────────

def test_narrative_future_plan_does_not_create_diagnostic_report():
    """Planning sentence 'Return to clinic for repeat comprehensive metabolic panel' must NOT trigger DiagnosticReport."""
    ev = _make_evidence(
        text=(
            "Patient was evaluated for hypertension.\n"
            "Pre-admission testing confirmed serum sodium was 142 mmol/L.\n"
            "FOLLOW-UP & COORDINATION: Return to clinic in six months for repeat comprehensive metabolic panel."
        )
    )
    claims = [
        _make_claim("c-val", "lab.value", "142", value_numeric=142.0, unit_raw="mmol/L"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={"c-val": _make_verification("c-val")},
        document_type="narrative_note",
    )

    # DiagnosticReport must be suppressed: running narrative with future order sentence is NOT a diagnostic report
    dr_count = sum(1 for e in bundle.entry if e.resource.resource_type == "DiagnosticReport")
    obs_count = sum(1 for e in bundle.entry if e.resource.resource_type == "Observation")
    assert dr_count == 0
    assert obs_count == 1

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_narrative_embedded_authentic_panel_header_creates_diagnostic_report():
    """Narrative note with an authentic embedded section header creates a grounded DiagnosticReport."""
    ev = _make_evidence(
        text=(
            "HOSPITAL DISCHARGE SUMMARY\n"
            "Patient was admitted with acute chest pain.\n"
            "SECTION: COMPREHENSIVE METABOLIC PANEL\n"
            "Sodium 138 mmol/L (ref 135-145)\n"
            "Potassium 4.2 mmol/L (ref 3.5-5.0)\n"
        )
    )
    claims = [
        _make_claim("c-val1", "lab.value", "138", value_numeric=138.0, unit_raw="mmol/L", group_id="grp-1"),
        _make_claim("c-name1", "lab.test_name", "Sodium", group_id="grp-1", normalized_code="2951-2", code_system="LOINC"),
        _make_claim("c-val2", "lab.value", "4.2", value_numeric=4.2, unit_raw="mmol/L", group_id="grp-2"),
        _make_claim("c-name2", "lab.test_name", "Potassium", group_id="grp-2", normalized_code="2823-3", code_system="LOINC"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: _make_verification(c.claim_id) for c in claims},
        document_type="discharge_summary",
    )

    dr_count = sum(1 for e in bundle.entry if e.resource.resource_type == "DiagnosticReport")
    obs_count = sum(1 for e in bundle.entry if e.resource.resource_type == "Observation")
    assert dr_count == 1
    assert obs_count == 2

    diag: DiagnosticReport = next(e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport))
    assert diag.code.coding[0].code == "24323-8"
    assert diag.code.coding[0].system == LOINC_SYSTEM
    assert len(diag.result) == 2

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


# ─── SECTION 4: TERMINOLOGY SERIALIZATION & CODING FIDELITY ───────────────────

def test_terminology_serialization_loinc_rxnorm_ucum_fidelity():
    """Normalized LOINC, RxNorm, and UCUM codes are faithfully serialized into FHIR resources."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "110", value_numeric=110.0, unit_raw="mg/dL"),
        _make_claim("c-name", "lab.test_name", "Glucose", normalized_code="2345-7", code_system="LOINC"),
        _make_claim("c-unit", "lab.unit", "mg/dL", normalized_code="mg/dL", code_system="UCUM"),
        _make_claim("c-med", "med.name", "Metformin", normalized_code="6809", code_system="RxNorm", group_id="grp-med"),
        _make_claim("c-dose", "med.dose", "500 mg", value_numeric=500.0, unit_raw="mg", group_id="grp-med"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: _make_verification(c.claim_id) for c in claims},
        document_type="clinic_rx_pad",
    )

    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    med: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    # Check LOINC coding on Observation.code
    assert obs.code.coding is not None
    assert obs.code.coding[0].system == LOINC_SYSTEM
    assert obs.code.coding[0].code == "2345-7"
    assert obs.code.coding[0].display == "Glucose"

    # Check UCUM unit on Observation.valueQuantity
    assert obs.valueQuantity is not None
    assert obs.valueQuantity.system == UCUM_SYSTEM
    assert obs.valueQuantity.code == "mg/dL"
    assert obs.valueQuantity.value == Decimal("110.0")

    # Check RxNorm coding on MedicationRequest.medicationCodeableConcept
    assert med.medicationCodeableConcept.coding is not None
    assert med.medicationCodeableConcept.coding[0].system == RXNORM_SYSTEM
    assert med.medicationCodeableConcept.coding[0].code == "6809"
    assert med.medicationCodeableConcept.coding[0].display == "Metformin"

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"


def test_terminology_unmapped_concept_safely_remains_text_only():
    """Unmapped laboratory test names and medications without normalized codes remain text-only."""
    ev = _make_evidence()
    claims = [
        _make_claim("c-val", "lab.value", "14.5", value_numeric=14.5, unit_raw="units"),
        _make_claim("c-name", "lab.test_name", "Novel Experimental Biomarker XYZ", normalized_code=None, code_system=None),
        _make_claim("c-med", "med.name", "Investigational Drug Compound ABC", normalized_code=None, code_system=None, group_id="grp-med"),
    ]
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: _make_verification(c.claim_id) for c in claims},
        document_type="clinic_rx_pad",
    )

    obs: Observation = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    med: MedicationRequest = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))

    # Observation.code has text but NO fabricated coding
    assert obs.code.text == "Novel Experimental Biomarker XYZ"
    assert obs.code.coding is None

    # MedicationRequest.medicationCodeableConcept has text but NO fabricated coding
    assert med.medicationCodeableConcept.text == "Investigational Drug Compound ABC"
    assert med.medicationCodeableConcept.coding is None

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"
