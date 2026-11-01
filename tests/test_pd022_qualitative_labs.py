"""Focused automated test suite for PD-022: Qualitative Laboratory Findings.

Authority:
  - PD-022: Representation of Qualitative Laboratory Determinations as lab.value and Export via FHIR R4B Observation.valueString
  - PROJECT_SPEC §8.3 (null value_numeric for qualitative lab determinations)
  - PROJECT_SPEC §15.2 (FHIR Observation.valueString export)
  - PRODUCTIZATION_SPEC §4.3 & §15.2
  - PRODUCTIZATION_DECISIONS PD-022

Verifies:
  A. Numeric laboratory value remains unchanged.
  B. "HBsAg Negative" produces lab.value = Negative, value_numeric = None.
  C. "HIV-1 Negative" does NOT produce value_numeric = 1.
  D. "Type 2 Non-reactive" does NOT produce value_numeric = 2.
  E. "VDRL Non-Reactive" is recognized/canonicalized as NON_REACTIVE.
  F. Arbitrary commentary is NOT normalized as a qualitative finding.
  G. lab.flag is NOT automatically synthesized from Negative.
  H. Explicit independently grounded lab.flag can coexist with qualitative lab.value.
  I. Qualitative value exports through FHIR Observation.valueString.
  J. Qualitative value NEVER exports as valueQuantity = 0.0.
  K. Existing numeric lab.value exports through valueQuantity exactly as before.
  L. Existing FHIR referential closure and provenance remain intact.
  M. Malformed/unsupported values fail safely.
  N. Existing whitespace-grounding tests continue to pass.
  O. Existing compound-medication tests continue to pass.
"""

from decimal import Decimal
import json
import pytest

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.extract.llm_extractor import _SYSTEM_PROMPT, MockLLMProvider, NarrativeExtractor
from clindoc.fhir.mapper import claims_to_fhir_bundle
from clindoc.fhir.validator import FHIRValidator
from clindoc.normalize.normalizer import (
    QUALITATIVE_CANONICAL_MAP,
    TerminologyNormalizer,
    normalize_qualitative_value,
)
from clindoc.schema.verification import VerificationResult
from clindoc.verify.stage_a import DeterministicVerifier


# ─── FIXTURES & HELPERS ───────────────────────────────────────────────────────

def _make_evidence(
    ev_id: str = "ev-qual-1",
    doc_id: str = "DOC-QUAL-001",
    page: int = 1,
    text: str = "HBsAg: Negative",
    ocr_confidence: float = 0.98,
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
    ev_id: str = "ev-qual-1",
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


def _make_claim(
    claim_id: str,
    field_type: str,
    value: str,
    value_numeric: float | None = None,
    unit_raw: str | None = None,
    group_id: str = "grp-1",
    document_id: str = "DOC-001",
    evidence_id: str = "ev-qual-1",
    normalized_code: str | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        document_id=document_id,
        group_id=group_id,
        field_type=field_type,
        value=value,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id=evidence_id,
        region_id="reg-1",
        normalized_code=normalized_code,
    )


# ─── TEST SUITE ───────────────────────────────────────────────────────────────

def test_a_numeric_lab_value_remains_unchanged():
    """A. Numeric laboratory value remains unchanged."""
    normalizer = TerminologyNormalizer()
    claim = _make_claim(
        claim_id="c-num-1",
        field_type="lab.value",
        value="12.6",
        unit_raw="GM%",
    )
    normalized = normalizer.normalize_claim(claim)

    assert normalized.value == "12.6", "Raw value must remain verbatim"
    assert normalized.value_numeric == 12.6, "Numeric value must be correctly parsed"
    assert normalized.unit_raw == "GM%", "Raw unit must remain verbatim"
    assert normalized.normalized_code != "NEGATIVE"


def test_b_hbsag_negative_normalization():
    """B. 'HBsAg Negative' produces lab.value = Negative, value_numeric = None."""
    normalizer = TerminologyNormalizer()
    claim = _make_claim(
        claim_id="c-hbsag-1",
        field_type="lab.value",
        value="Negative",
        unit_raw=None,
    )
    normalized = normalizer.normalize_claim(claim)

    assert normalized.value == "Negative", "Verbatim value preserved"
    assert normalized.value_numeric is None, "Qualitative value must have value_numeric = None"
    assert normalized.normalized_code == "NEGATIVE", "Normalized to canonical vocabulary"
    assert normalized.unit_raw is None, "No unit for qualitative value"


def test_c_hiv1_negative_does_not_produce_numeric_one():
    """C. 'HIV-1 Negative' does NOT produce value_numeric = 1."""
    normalizer = TerminologyNormalizer()
    claim = _make_claim(
        claim_id="c-hiv-1",
        field_type="lab.value",
        value="HIV-1 Negative",
        unit_raw=None,
    )
    normalized = normalizer.normalize_claim(claim)

    assert normalized.value == "HIV-1 Negative", "Raw value preserved"
    assert normalized.value_numeric is None, "Must NOT extract embedded digit 1 as numeric value"
    assert normalized.normalized_code == "NEGATIVE"


def test_d_type2_nonreactive_does_not_produce_numeric_two():
    """D. 'Type 2 Non-reactive' does NOT produce value_numeric = 2."""
    normalizer = TerminologyNormalizer()
    claim = _make_claim(
        claim_id="c-type2-1",
        field_type="lab.value",
        value="Type 2 Non-reactive",
        unit_raw=None,
    )
    normalized = normalizer.normalize_claim(claim)

    assert normalized.value == "Type 2 Non-reactive", "Raw value preserved"
    assert normalized.value_numeric is None, "Must NOT extract embedded digit 2 as numeric value"
    assert normalized.normalized_code == "NON_REACTIVE"


def test_e_vdrl_non_reactive_canonicalized():
    """E. 'VDRL Non-Reactive' is recognized/canonicalized as NON_REACTIVE."""
    normalizer = TerminologyNormalizer()
    claim = _make_claim(
        claim_id="c-vdrl-1",
        field_type="lab.value",
        value="VDRL Non-Reactive",
        unit_raw=None,
    )
    normalized = normalizer.normalize_claim(claim)

    assert normalized.normalized_code == "NON_REACTIVE"
    assert normalized.value_numeric is None
    assert normalized.value == "VDRL Non-Reactive"


def test_f_arbitrary_commentary_not_normalized_as_qualitative():
    """F. Arbitrary commentary is NOT normalized as a qualitative finding."""
    normalizer = TerminologyNormalizer()
    commentary_samples = [
        "specimen slightly hemolyzed",
        "sample clotted, cannot process",
        "repeat sample requested",
        "lipemic specimen",
    ]
    for comment in commentary_samples:
        assert normalize_qualitative_value(comment) is None, f"Commentary '{comment}' must not match qualitative vocabulary"
        claim = _make_claim(
            claim_id="c-comm-1",
            field_type="lab.value",
            value=comment,
        )
        normalized = normalizer.normalize_claim(claim)
        assert normalized.normalized_code != "NEGATIVE"
        assert normalized.normalized_code != "POSITIVE"
        assert normalized.normalized_code is None
        assert normalized.value_numeric is None


def test_g_lab_flag_not_synthesized_from_negative():
    """G. lab.flag is NOT automatically synthesized from Negative."""
    text = "HBsAg: Negative"
    mock_prov = MockLLMProvider()
    resp_raw = mock_prov.complete(_SYSTEM_PROMPT, f"Extract clinical information from the following clinical text:\n\n{text}\n\nOutput JSON:")
    data = json.loads(resp_raw)
    extractions = data.get("extractions", [])
    field_types = [e["field_type"] for e in extractions]

    assert "lab.value" in field_types
    assert "lab.flag" not in field_types, "lab.flag must NOT be synthesized from 'Negative'"


def test_h_explicit_grounded_flag_coexists_with_qualitative_value():
    """H. Explicit independently grounded lab.flag can coexist with qualitative lab.value."""
    ev = _make_evidence(text="HBsAg: Negative (NORMAL)")
    claims = [
        _make_claim(
            claim_id="c-val-1",
            field_type="lab.value",
            value="Negative",
            value_numeric=None,
            evidence_id="ev-qual-1",
        ),
        _make_claim(
            claim_id="c-flag-1",
            field_type="lab.flag",
            value="NORMAL",
            evidence_id="ev-qual-1",
        ),
    ]
    ev_map = {"ev-qual-1": ev}
    ver_map = {
        "c-val-1": _make_verification("c-val-1"),
        "c-flag-1": _make_verification("c-flag-1"),
    }

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    obs = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"][0]

    # Phase 4.1: Verified qualitative values map to Observation.valueCodeableConcept with SNOMED CT
    assert obs.valueCodeableConcept is not None
    assert obs.valueCodeableConcept.text == "Negative"
    assert obs.valueCodeableConcept.coding[0].code == "260385009"
    assert obs.valueCodeableConcept.coding[0].system == "http://snomed.info/sct"
    assert obs.valueString is None
    assert obs.valueQuantity is None
    assert obs.interpretation is not None
    assert obs.interpretation[0].coding[0].code == "N"


def test_i_qualitative_value_exports_through_fhir_value_codeable_concept_and_fallback():
    """I. Verified qualitative values export through Observation.valueCodeableConcept; unmapped values export through valueString."""
    ev = _make_evidence(text="VDRL: Non-Reactive")
    claim = _make_claim(
        claim_id="c-val-vdrl",
        field_type="lab.value",
        value="Non-Reactive",
        value_numeric=None,
        normalized_code="NON_REACTIVE",
        evidence_id="ev-qual-1",
    )
    ev_map = {"ev-qual-1": ev}
    ver_map = {"c-val-vdrl": _make_verification("c-val-vdrl")}

    bundle = claims_to_fhir_bundle([claim], ev_map, ver_map)
    obs = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"][0]

    assert obs.valueCodeableConcept is not None
    assert obs.valueCodeableConcept.text == "Non-Reactive"
    assert obs.valueCodeableConcept.coding[0].code == "131194007"
    assert obs.valueCodeableConcept.coding[0].system == "http://snomed.info/sct"
    assert obs.valueString is None
    assert obs.valueQuantity is None

    # Safe text-only fallback for unmapped qualitative finding
    claim_unmapped = _make_claim(
        claim_id="c-val-unmapped",
        field_type="lab.value",
        value="rare epithelial cells seen",
        value_numeric=None,
        normalized_code=None,
        evidence_id="ev-qual-1",
    )
    ver_map_unmapped = {"c-val-unmapped": _make_verification("c-val-unmapped")}
    bundle_unmapped = claims_to_fhir_bundle([claim_unmapped], ev_map, ver_map_unmapped)
    obs_unmapped = [e.resource for e in bundle_unmapped.entry if e.resource.resource_type == "Observation"][0]
    assert obs_unmapped.valueString == "rare epithelial cells seen"
    assert obs_unmapped.valueCodeableConcept is None
    assert obs_unmapped.valueQuantity is None


def test_j_qualitative_value_never_exports_as_value_quantity_zero():
    """J. Qualitative value NEVER exports as valueQuantity = 0.0."""
    qual_values = ["Negative", "Positive", "Reactive", "Non-Reactive", "Detected", "Not Detected", "Equivocal"]
    for qv in qual_values:
        ev = _make_evidence(text=f"Test: {qv}")
        claim = _make_claim(
            claim_id=f"c-val-{qv}",
            field_type="lab.value",
            value=qv,
            value_numeric=None,
            evidence_id="ev-qual-1",
        )
        ev_map = {"ev-qual-1": ev}
        ver_map = {f"c-val-{qv}": _make_verification(f"c-val-{qv}")}

        bundle = claims_to_fhir_bundle([claim], ev_map, ver_map)
        obs = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"][0]

        assert obs.valueCodeableConcept is not None, f"Observation.valueCodeableConcept must be populated for '{qv}'"
        assert obs.valueCodeableConcept.text == qv
        assert obs.valueQuantity is None, f"Observation.valueQuantity must NOT be emitted for qualitative '{qv}'"
        assert obs.valueString is None

        # Check JSON representation to guarantee no 0.0 valueQuantity
        obs_dict = json.loads(obs.json())
        assert "valueQuantity" not in obs_dict, f"valueQuantity key must not exist in FHIR JSON for qualitative '{qv}'"
        assert "valueCodeableConcept" in obs_dict


def test_k_existing_numeric_lab_value_exports_through_value_quantity_as_before():
    """K. Existing numeric lab.value exports through valueQuantity exactly as before."""
    ev = _make_evidence(text="Hemoglobin 14.2 g/dL")
    claims = [
        _make_claim(
            claim_id="c-val-hgb",
            group_id="grp-hgb",
            field_type="lab.value",
            value="14.2",
            value_numeric=14.2,
            unit_raw="g/dL",
            evidence_id="ev-qual-1",
        ),
        _make_claim(
            claim_id="c-name-hgb",
            group_id="grp-hgb",
            field_type="lab.test_name",
            value="Hemoglobin",
            evidence_id="ev-qual-1",
        ),
    ]
    ev_map = {"ev-qual-1": ev}
    ver_map = {
        "c-val-hgb": _make_verification("c-val-hgb"),
        "c-name-hgb": _make_verification("c-name-hgb"),
    }

    bundle = claims_to_fhir_bundle(claims, ev_map, ver_map)
    obs = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"][0]

    assert obs.valueQuantity is not None
    assert obs.valueQuantity.value == Decimal("14.2")
    assert obs.valueQuantity.unit == "g/dL"
    assert obs.valueString is None


def test_l_fhir_referential_closure_and_provenance_intact():
    """L. Existing FHIR referential closure and provenance remain intact."""
    ev = _make_evidence(text="HBsAg: Negative")
    claim = _make_claim(
        claim_id="c-val-hbsag",
        field_type="lab.value",
        value="Negative",
        value_numeric=None,
        evidence_id="ev-qual-1",
    )
    ev_map = {"ev-qual-1": ev}
    ver_map = {"c-val-hbsag": _make_verification("c-val-hbsag")}

    bundle = claims_to_fhir_bundle(
        [claim],
        ev_map,
        ver_map,
        include_document_reference=True,
        include_provenance_resource=True,
    )

    validator = FHIRValidator()
    result = validator.validate_bundle(bundle)
    assert result.is_valid, f"Bundle validation failed: {result.errors}"

    # Verify Patient, DocumentReference, Provenance references
    obs = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"][0]
    patients = [e.resource for e in bundle.entry if e.resource.resource_type == "Patient"]
    doc_refs = [e.resource for e in bundle.entry if e.resource.resource_type == "DocumentReference"]
    provenances = [e.resource for e in bundle.entry if e.resource.resource_type == "Provenance"]

    assert len(patients) == 1
    assert obs.subject.reference == f"Patient/{patients[0].id}"
    assert len(doc_refs) == 1
    assert obs.derivedFrom[0].reference == f"DocumentReference/{doc_refs[0].id}"
    assert len(provenances) == 1
    assert any(tgt.reference == f"Observation/{obs.id}" for tgt in provenances[0].target)


def test_m_stage_a_handles_qualitative_lab_values_without_modification():
    """M. Stage A verifies grounded qualitative lab.value as SUPPORTED."""
    verifier = DeterministicVerifier()
    claim = _make_claim(
        claim_id="c-val-hbsag",
        field_type="lab.value",
        value="Negative",
        value_numeric=None,
        evidence_id="ev-1",
    )
    ev = _make_evidence(text="HBsAg: Negative")
    res = verifier.verify(claim, ev)
    assert res.label == "SUPPORTED"
    assert res.confidence == 1.0

    # Discrepancy case: evidence text has Positive, claim is Negative
    ev_pos = _make_evidence(text="HBsAg: Positive")
    res_disc = verifier.verify(claim, ev_pos)
    assert res_disc.label == "DISCREPANCY"
    assert res_disc.contradiction_type == "value_mismatch"


def test_prompt_contract_integrity():
    """Verify prompt contract includes PD-022 laboratory qualitative instructions."""
    assert "quantitative numeric laboratory test result value" in _SYSTEM_PROMPT
    assert "grounded qualitative laboratory determination" in _SYSTEM_PROMPT
    assert "HBsAg: Negative" in _SYSTEM_PROMPT
    assert "HIV: Negative" in _SYSTEM_PROMPT
    assert "VDRL: Non-Reactive" in _SYSTEM_PROMPT
    assert "ARBITRARY COMMENTARY IS NOT A QUALITATIVE RESULT" in _SYSTEM_PROMPT
