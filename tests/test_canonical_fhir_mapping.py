"""Focused automated tests for canonical FHIR R4B and DEC-016 Provenance mapping.

Authority:
  - PROJECT_SPEC §5 #10, §7.7, §8, §9, §10, §15, §16
  - DEC-008: Hybrid MedicationRequest vs MedicationStatement routing
  - DEC-016: Two-tier provenance extension hierarchy (resource-level & element-level)
"""

from decimal import Decimal
import pytest

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.observation import Observation

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir import FHIRValidator, claims_to_fhir_bundle
from clindoc.fhir.constants import (
    EXT_CLAIM_PROVENANCE,
    LOINC_SYSTEM,
    OBS_CATEGORY_SYSTEM,
    OBS_INTERPRETATION_SYSTEM,
    RXNORM_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.schema.verification import VerificationResult


# ─── FIXTURES & HELPERS ───────────────────────────────────────────────────────

def _make_evidence(
    ev_id: str = "ev-1",
    doc_id: str = "DOC-001",
    page: int = 1,
    text: str = "Hemoglobin 14.2 g/dL (ref 12.0 - 16.0)",
    ocr_confidence: float | None = 0.98,
) -> Evidence:
    return Evidence(
        evidence_id=ev_id,
        document_id=doc_id,
        page_number=page,
        text=text,
        char_start=0,
        char_end=len(text),
        bbox=BBoxModel(x0=10.0, y0=20.0, x1=150.0, y1=35.0),
        ocr_confidence=ocr_confidence,
    )


def _make_verification(
    claim_id: str,
    ev_id: str = "ev-1",
    label: str = "SUPPORTED",
    confidence: float = 0.95,
    stage_reached: str = "A",
    contradiction_type: str | None = None,
) -> VerificationResult:
    c_type = contradiction_type
    if label == "DISCREPANCY" and c_type is None:
        c_type = "value_mismatch"
    return VerificationResult(
        claim_id=claim_id,
        label=label,
        confidence=confidence,
        stage_reached=stage_reached,
        evidence_id=ev_id,
        contradiction_type=c_type,
    )


# ─── LAB OBSERVATION TESTS ───────────────────────────────────────────────────

def test_lab_observation_mapping_and_validation():
    """Verify that lab claims map to a valid FHIR R4B Observation and DiagnosticReport."""
    ev = _make_evidence()
    claims = [
        Claim(
            claim_id="c-val",
            document_id="DOC-001",
            group_id="grp-hgb",
            field_type="lab.value",
            value="14.2",
            value_numeric=14.2,
            unit_raw="g/dL",
            extraction_method="deterministic",
            extraction_confidence=0.99,
            evidence_id="ev-1",
            region_id="reg-1",
        ),
        Claim(
            claim_id="c-name",
            document_id="DOC-001",
            group_id="grp-hgb",
            field_type="lab.test_name",
            value="Hemoglobin",
            extraction_method="deterministic",
            extraction_confidence=0.99,
            evidence_id="ev-1",
            normalized_code="718-7",
            code_system="LOINC",
            code_confidence=0.95,
            region_id="reg-1",
        ),
        Claim(
            claim_id="c-unit",
            document_id="DOC-001",
            group_id="grp-hgb",
            field_type="lab.unit",
            value="g/dL",
            extraction_method="deterministic",
            extraction_confidence=0.99,
            evidence_id="ev-1",
            normalized_code="g/dL",
            code_system="UCUM",
            code_confidence=0.99,
            region_id="reg-1",
        ),
        Claim(
            claim_id="c-flag",
            document_id="DOC-001",
            group_id="grp-hgb",
            field_type="lab.flag",
            value="NORMAL",
            extraction_method="deterministic",
            extraction_confidence=0.95,
            evidence_id="ev-1",
            region_id="reg-1",
        ),
        Claim(
            claim_id="c-ref",
            document_id="DOC-001",
            group_id="grp-hgb",
            field_type="lab.reference_range",
            value="12.0 - 16.0",
            extraction_method="deterministic",
            extraction_confidence=0.95,
            evidence_id="ev-1",
            region_id="reg-1",
        ),
    ]

    ev_map = {"ev-1": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)

    # 1. Official schema & integrity validation
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"
    assert val.resource_counts["Patient"] == 1
    assert val.resource_counts["DiagnosticReport"] == 1
    assert val.resource_counts["Observation"] == 1

    # 2. Inspect Observation fields
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    assert obs.status == "final"
    assert obs.category[0].coding[0].system == OBS_CATEGORY_SYSTEM
    assert obs.category[0].coding[0].code == "laboratory"

    # Preserved LOINC coding
    assert obs.code.coding[0].system == LOINC_SYSTEM
    assert obs.code.coding[0].code == "718-7"
    assert obs.code.text == "Hemoglobin"

    # Preserved UCUM valueQuantity
    assert float(obs.valueQuantity.value) == 14.2
    assert obs.valueQuantity.system == UCUM_SYSTEM
    assert obs.valueQuantity.code == "g/dL"

    # Preserved interpretation
    assert obs.interpretation[0].coding[0].system == OBS_INTERPRETATION_SYSTEM
    assert obs.interpretation[0].coding[0].code == "N"

    # Preserved reference range
    assert obs.referenceRange[0].text == "12.0 - 16.0"


def test_two_tier_provenance_hierarchy_on_lab():
    """Verify DEC-016 two-tier provenance: resource-level and constituent element-level."""
    ev = _make_evidence()
    claims = [
        Claim(
            claim_id="c-val-10",
            document_id="DOC-001",
            group_id="grp-10",
            field_type="lab.value",
            value="14.2",
            value_numeric=14.2,
            extraction_method="deterministic",
            extraction_confidence=0.99,
            evidence_id="ev-1",
            region_id="reg-1",
        ),
        Claim(
            claim_id="c-name-10",
            document_id="DOC-001",
            group_id="grp-10",
            field_type="lab.test_name",
            value="Hemoglobin",
            extraction_method="deterministic",
            extraction_confidence=0.99,
            evidence_id="ev-1",
            region_id="reg-1",
        ),
        Claim(
            claim_id="c-flag-10",
            document_id="DOC-001",
            group_id="grp-10",
            field_type="lab.flag",
            value="HIGH",
            extraction_method="deterministic",
            extraction_confidence=0.95,
            evidence_id="ev-1",
            region_id="reg-1",
        ),
    ]

    ev_map = {"ev-1": ev}
    ver_map = {
        "c-val-10": _make_verification("c-val-10", label="SUPPORTED", confidence=0.98),
        "c-name-10": _make_verification("c-name-10", label="SUPPORTED", confidence=0.95),
        "c-flag-10": _make_verification("c-flag-10", label="DISCREPANCY", confidence=0.88),
    }

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))

    # Tier 1: Resource-level provenance extension
    res_ext = next(ext for ext in obs.extension if ext.url == EXT_CLAIM_PROVENANCE)
    sub_map = {s.url: s for s in res_ext.extension}
    assert sub_map["claimId"].valueString == "c-val-10"
    assert sub_map["verificationLabel"].valueCode == "SUPPORTED"
    assert float(sub_map["confidence"].valueDecimal) == 0.98
    assert sub_map["stageReached"].valueCode == "A"
    assert sub_map["evidenceText"].valueString == ev.text
    assert sub_map["sourcePage"].valueInteger == 1
    assert sub_map["sourceBoundingBox"].valueString == "10.0,20.0,150.0,35.0"
    assert float(sub_map["ocrConfidence"].valueDecimal) == 0.98

    # Tier 2: Element-level provenance sub-extensions
    # On valueQuantity
    vq_ext = next(ext for ext in obs.valueQuantity.extension if ext.url == EXT_CLAIM_PROVENANCE)
    vq_sub = {s.url: s for s in vq_ext.extension}
    assert vq_sub["claimId"].valueString == "c-val-10"
    assert vq_sub["verificationLabel"].valueCode == "SUPPORTED"

    # On code (test_name)
    code_ext = next(ext for ext in obs.code.extension if ext.url == EXT_CLAIM_PROVENANCE)
    code_sub = {s.url: s for s in code_ext.extension}
    assert code_sub["claimId"].valueString == "c-name-10"
    assert code_sub["verificationLabel"].valueCode == "SUPPORTED"

    # On interpretation (flag - divergent DISCREPANCY verdict preserved at element level!)
    flag_ext = next(ext for ext in obs.interpretation[0].extension if ext.url == EXT_CLAIM_PROVENANCE)
    flag_sub = {s.url: s for s in flag_ext.extension}
    assert flag_sub["claimId"].valueString == "c-flag-10"
    assert flag_sub["verificationLabel"].valueCode == "DISCREPANCY"
    assert float(flag_sub["confidence"].valueDecimal) == 0.88


# ─── MEDICATION & DEC-008 TESTS ──────────────────────────────────────────────

def _make_med_claims(temporal_status: str = "current") -> tuple[list[Claim], dict[str, Evidence], dict[str, VerificationResult]]:
    ev = Evidence(
        evidence_id="ev-med-1",
        document_id="DOC-MED-001",
        page_number=1,
        text="Metformin 500 mg oral twice daily for 90 days",
        char_start=0,
        char_end=46,
        bbox=BBoxModel(x0=20.0, y0=50.0, x1=250.0, y1=65.0),
        ocr_confidence=0.99,
    )
    claims = [
        Claim(
            claim_id="c-med-name",
            document_id="DOC-MED-001",
            group_id="grp-metformin",
            field_type="med.name",
            value="Metformin",
            normalized_code="6809",
            code_system="RxNorm",
            code_confidence=0.98,
            extraction_method="deterministic",
            extraction_confidence=0.99,
            evidence_id="ev-med-1",
            temporal_status=temporal_status,
            region_id="reg-1",
        ),
        Claim(
            claim_id="c-med-dose",
            document_id="DOC-MED-001",
            group_id="grp-metformin",
            field_type="med.dose",
            value="500",
            value_numeric=500.0,
            unit_raw="mg",
            extraction_method="deterministic",
            extraction_confidence=0.99,
            evidence_id="ev-med-1",
            temporal_status=temporal_status,
            region_id="reg-1",
        ),
        Claim(
            claim_id="c-med-route",
            document_id="DOC-MED-001",
            group_id="grp-metformin",
            field_type="med.route",
            value="oral",
            extraction_method="deterministic",
            extraction_confidence=0.95,
            evidence_id="ev-med-1",
            temporal_status=temporal_status,
            region_id="reg-1",
        ),
        Claim(
            claim_id="c-med-freq",
            document_id="DOC-MED-001",
            group_id="grp-metformin",
            field_type="med.frequency",
            value="twice daily",
            extraction_method="deterministic",
            extraction_confidence=0.95,
            evidence_id="ev-med-1",
            temporal_status=temporal_status,
            region_id="reg-1",
        ),
        Claim(
            claim_id="c-med-dur",
            document_id="DOC-MED-001",
            group_id="grp-metformin",
            field_type="med.duration",
            value="90 days",
            extraction_method="deterministic",
            extraction_confidence=0.95,
            evidence_id="ev-med-1",
            temporal_status=temporal_status,
            region_id="reg-1",
        ),
    ]
    ev_map = {"ev-med-1": ev}
    ver_map = {c.claim_id: _make_verification(c.claim_id, ev_id="ev-med-1") for c in claims}
    return claims, ev_map, ver_map


def test_medication_statement_mapping_and_validation():
    """Verify narrative/discharge note maps to MedicationStatement per DEC-008."""
    claims, ev_map, ver_map = _make_med_claims(temporal_status="current")
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="discharge_summary")

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"
    assert val.resource_counts["MedicationStatement"] == 1
    assert "MedicationRequest" not in val.resource_counts

    ms = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    assert ms.status == "active"
    assert ms.medicationCodeableConcept.coding[0].system == RXNORM_SYSTEM
    assert ms.medicationCodeableConcept.coding[0].code == "6809"
    assert ms.medicationCodeableConcept.text == "Metformin"

    # Element provenance on medicationCodeableConcept
    med_prov = next(ext for ext in ms.medicationCodeableConcept.extension if ext.url == EXT_CLAIM_PROVENANCE)
    assert any(s.url == "claimId" and s.valueString == "c-med-name" for s in med_prov.extension)

    # Dosage element provenance
    dosage = ms.dosage[0]
    assert float(dosage.doseAndRate[0].doseQuantity.value) == 500.0
    assert dosage.doseAndRate[0].doseQuantity.unit == "mg"
    assert dosage.route.text == "oral"
    assert dosage.timing.code.text == "twice daily"


def test_medication_request_routing_prescription_doc():
    """Verify prescription doc type (clinic_rx_pad) maps to MedicationRequest per DEC-008."""
    claims, ev_map, ver_map = _make_med_claims(temporal_status="current")
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="clinic_rx_pad")

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"
    assert val.resource_counts["MedicationRequest"] == 1
    assert "MedicationStatement" not in val.resource_counts

    mr = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert mr.status == "active"
    assert mr.intent == "order"
    assert mr.medicationCodeableConcept.text == "Metformin"


def test_medication_request_routing_planned_temporal_status():
    """Verify temporal_status == 'planned' overrides to MedicationRequest per DEC-008."""
    claims, ev_map, ver_map = _make_med_claims(temporal_status="planned")
    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map, document_type="narrative_note")

    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True, f"Validation errors: {val.errors}"
    assert val.resource_counts["MedicationRequest"] == 1
    assert "MedicationStatement" not in val.resource_counts

    mr = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationRequest))
    assert mr.status == "active"
    assert mr.intent == "plan"


def test_discontinued_and_historical_statuses():
    """Verify stopped / completed statuses are properly assigned."""
    # Discontinued -> stopped
    claims_disc, ev_disc, ver_disc = _make_med_claims(temporal_status="discontinued")
    b_disc = claims_to_fhir_bundle(claims_disc, ev_disc, ver_disc, document_type="discharge_summary")
    ms_disc = next(e.resource for e in b_disc.entry if isinstance(e.resource, MedicationStatement))
    assert ms_disc.status == "stopped"

    # Historical -> completed
    claims_hist, ev_hist, ver_hist = _make_med_claims(temporal_status="historical")
    b_hist = claims_to_fhir_bundle(claims_hist, ev_hist, ver_hist, document_type="clinic_rx_pad")
    mr_hist = next(e.resource for e in b_hist.entry if isinstance(e.resource, MedicationRequest))
    assert mr_hist.status == "completed"


def test_born_digital_document_omits_ocr_confidence():
    """Verify that when ocr_confidence is null (born-digital), ocrConfidence is omitted per §16.1."""
    ev_digital = _make_evidence(ocr_confidence=None)
    claim = Claim(
        claim_id="c-dig-1",
        document_id="DOC-DIG-001",
        field_type="lab.value",
        value="5.0",
        value_numeric=5.0,
        extraction_method="deterministic",
        extraction_confidence=1.0,
        evidence_id="ev-1",
        region_id="reg-1",
    )
    bundle = claims_to_fhir_bundle(
        [claim],
        {"ev-1": ev_digital},
        {"c-dig-1": _make_verification("c-dig-1")},
    )
    obs = next(e.resource for e in bundle.entry if isinstance(e.resource, Observation))
    res_ext = next(ext for ext in obs.extension if ext.url == EXT_CLAIM_PROVENANCE)
    sub_urls = [s.url for s in res_ext.extension]
    assert "ocrConfidence" not in sub_urls


def test_missing_normalized_code_falls_back_to_text_only():
    """Verify §15.3: failed normalization falls back to text-only with no .coding[] entry."""
    ev = _make_evidence()
    claim = Claim(
        claim_id="c-unmapped",
        document_id="DOC-001",
        field_type="med.name",
        value="CustomCompoundedFormula",
        normalized_code=None,  # Normalization failed
        code_system=None,
        code_confidence=0.0,
        extraction_method="deterministic",
        extraction_confidence=0.9,
        evidence_id="ev-1",
        region_id="reg-1",
    )
    bundle = claims_to_fhir_bundle([claim], {"ev-1": ev}, {"c-unmapped": _make_verification("c-unmapped")})
    ms = next(e.resource for e in bundle.entry if isinstance(e.resource, MedicationStatement))
    assert ms.medicationCodeableConcept.text == "CustomCompoundedFormula"
    assert ms.medicationCodeableConcept.coding is None


# ─── INVALID INPUT ERROR HANDLING TESTS ──────────────────────────────────────

def test_empty_claims_raises_value_error():
    """Verify that an empty claim sequence raises ValueError (§0.3, invalid inputs MUST raise)."""
    with pytest.raises(ValueError, match="claims sequence must not be empty"):
        claims_to_fhir_bundle([], {}, {})


def test_missing_verification_result_raises_value_error():
    """Verify that an unverified claim raises ValueError (§5 Component 10)."""
    ev = _make_evidence()
    claim = Claim(
        claim_id="c-orphan",
        document_id="DOC-001",
        field_type="lab.value",
        value="10.0",
        value_numeric=10.0,
        extraction_method="deterministic",
        extraction_confidence=0.9,
        evidence_id="ev-1",
        region_id="reg-1",
    )
    with pytest.raises(ValueError, match="has no corresponding VerificationResult"):
        claims_to_fhir_bundle([claim], {"ev-1": ev}, {})  # empty verification_map


def test_missing_evidence_raises_value_error():
    """Verify that a claim referencing missing evidence raises ValueError."""
    claim = Claim(
        claim_id="c-no-ev",
        document_id="DOC-001",
        field_type="lab.value",
        value="10.0",
        value_numeric=10.0,
        extraction_method="deterministic",
        extraction_confidence=0.9,
        evidence_id="ev-missing",
        region_id="reg-1",
    )
    ver = _make_verification("c-no-ev", ev_id="ev-missing")
    with pytest.raises(ValueError, match="references missing Evidence"):
        claims_to_fhir_bundle([claim], {}, {"c-no-ev": ver})  # empty evidence_map
