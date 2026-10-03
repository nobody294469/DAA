"""Comprehensive test suite for Verification Engine and Verification Cascade.

Authority:
  - PROJECT_SPEC §5 #8, §7.6, §10, §12.1, §12.2, §13, §17.1, §20.1, §24
  - DEC-003: Document context window bounding
  - DEC-005: Stage B escalation threshold (0.60 default)
  - DEC-009: Temperature scaling calibration for Stage B
  - DEC-013: Grammar-constrained Stage C output
  - DEC-018: 1D confidence calibration for Stage C
  - DEC-019: Field verification tiering (Tier 1 vs Tier 2)
"""
from __future__ import annotations

import json
import numpy as np
import pytest

from clindoc.extract.contracts import (
    BBoxModel,
    Claim,
    Evidence,
    FieldType,
    TIER_1_FIELDS,
    TIER_2_FIELDS,
)
from clindoc.m2.calibration import IsotonicConfidenceCalibrator, TemperatureScaler
from clindoc.schema.verification import VerificationResult
from clindoc.verify import (
    DefaultMockLLMJudge,
    DeterministicVerifier,
    LearnedVerifier,
    MockModelPredictor,
    StageAResult,
    StageBResult,
    StageCJudge,
    StageCJudgeOutput,
    StageCResult,
    VerificationCascade,
)


def _make_claim(
    field_type: FieldType,
    value: str,
    unit_raw: str | None = None,
    claim_id: str = "c-001",
    evidence_id: str = "ev-001",
    negated: bool = False,
    value_numeric: float | None = None,
    normalized_code: str | None = None,
    code_system: str | None = None,
) -> Claim:
    """Helper to construct a valid Claim instance."""
    return Claim(
        claim_id=claim_id,
        document_id="doc-test-001",
        group_id="group-1",
        field_type=field_type,
        value=value,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id=evidence_id,
        normalized_code=normalized_code,
        code_system=code_system,
        negated=negated,
        region_id="reg-1",
    )


def _make_evidence(
    text: str,
    evidence_id: str = "ev-001",
    char_start: int = 0,
    char_end: int | None = None,
    page_number: int = 1,
) -> Evidence:
    """Helper to construct a valid Evidence instance."""
    end = char_end if char_end is not None else len(text)
    return Evidence(
        evidence_id=evidence_id,
        document_id="doc-test-001",
        page_number=page_number,
        text=text,
        char_start=char_start,
        char_end=end,
        bbox=BBoxModel(x0=50.0, y0=100.0, x1=200.0, y1=120.0),
        ocr_confidence=0.98,
    )


# ─── 1. DETERMINISTIC VERIFIER TESTS (STAGE A) ───────────────────────────────

class TestStageADeterministicVerifier:
    """Tests for Stage A deterministic rule-based verification."""

    @pytest.fixture
    def verifier(self):
        return DeterministicVerifier()

    def test_deterministic_supported_numeric_value(self, verifier):
        claim = _make_claim("lab.value", "14.2", unit_raw="g/dL", value_numeric=14.2)
        evidence = _make_evidence("Hemoglobin: 14.2 g/dL (Normal)")
        res = verifier.verify(claim, evidence)

        assert res.label == "SUPPORTED"
        assert res.contradiction_type is None
        assert res.confidence == 1.0

    def test_deterministic_discrepancy_numeric_value(self, verifier):
        claim = _make_claim("lab.value", "14.2", unit_raw="g/dL", value_numeric=14.2)
        evidence = _make_evidence("Hemoglobin: 12.8 g/dL (Normal)")
        res = verifier.verify(claim, evidence)

        assert res.label == "DISCREPANCY"
        assert res.contradiction_type == "value_mismatch"
        assert res.confidence == 1.0

    def test_deterministic_unit_mismatch(self, verifier):
        claim = _make_claim("lab.value", "14.2", unit_raw="mg/dL", value_numeric=14.2)
        evidence = _make_evidence("Hemoglobin: 14.2 g/dL (Normal)")
        res = verifier.verify(claim, evidence)

        assert res.label == "DISCREPANCY"
        assert res.contradiction_type == "unit_mismatch"

    def test_deterministic_flag_supported_and_discrepancy(self, verifier):
        claim_high = _make_claim("lab.flag", "High")
        evidence_high = _make_evidence("Glucose: 180 mg/dL [H]")
        assert verifier.verify(claim_high, evidence_high).label == "SUPPORTED"

        evidence_low = _make_evidence("Glucose: 55 mg/dL [L]")
        res_disc = verifier.verify(claim_high, evidence_low)
        assert res_disc.label == "DISCREPANCY"
        assert res_disc.contradiction_type == "value_mismatch"

    def test_deterministic_med_dose_supported_and_discrepancy(self, verifier):
        claim = _make_claim("med.dose", "500", unit_raw="mg", value_numeric=500.0)
        evidence_supp = _make_evidence("Amoxicillin 500 mg PO TID")
        assert verifier.verify(claim, evidence_supp).label == "SUPPORTED"

        evidence_disc = _make_evidence("Amoxicillin 250 mg PO TID")
        res_disc = verifier.verify(claim, evidence_disc)
        assert res_disc.label == "DISCREPANCY"
        assert res_disc.contradiction_type == "dose_mismatch"

    def test_deterministic_med_route_synonyms(self, verifier):
        claim_po = _make_claim("med.route", "oral")
        evidence_po = _make_evidence("Lisinopril 10mg PO daily")
        assert verifier.verify(claim_po, evidence_po).label == "SUPPORTED"

        evidence_iv = _make_evidence("Lisinopril 10mg IV daily")
        res_disc = verifier.verify(claim_po, evidence_iv)
        assert res_disc.label == "DISCREPANCY"
        assert res_disc.contradiction_type == "value_mismatch"

    def test_deterministic_med_frequency_synonyms(self, verifier):
        claim_bid = _make_claim("med.frequency", "twice daily")
        evidence_bid = _make_evidence("Metformin 500mg BID with meals")
        assert verifier.verify(claim_bid, evidence_bid).label == "SUPPORTED"

        evidence_qd = _make_evidence("Metformin 500mg QD with meals")
        res_disc = verifier.verify(claim_bid, evidence_qd)
        assert res_disc.label == "DISCREPANCY"
        assert res_disc.contradiction_type == "value_mismatch"

    def test_deterministic_tier_2_unit_and_reference_range(self, verifier):
        claim_unit = _make_claim("lab.unit", "mg/dL")
        ev_unit = _make_evidence("140 mg/dL")
        assert verifier.verify(claim_unit, ev_unit).label == "SUPPORTED"

        claim_range = _make_claim("lab.reference_range", "12.0 - 15.5")
        ev_range = _make_evidence("Reference: 12.0-15.5 g/dL")
        assert verifier.verify(claim_range, ev_range).label == "SUPPORTED"

        ev_range_diff = _make_evidence("Reference: 10.0-14.0 g/dL")
        res_diff = verifier.verify(claim_range, ev_range_diff)
        assert res_diff.label == "DISCREPANCY"
        assert res_diff.contradiction_type == "value_mismatch"

    def test_deterministic_negation_mismatch_and_context_scoping(self, verifier):
        # Case A: affirmative claim with unrelated negation elsewhere in paragraph context
        # Must NOT be marked DISCREPANCY solely because "denies" occurs elsewhere in context
        claim_a = _make_claim("med.name", "Aspirin", negated=False)
        evidence_a = _make_evidence("Prescribed Aspirin 81mg daily.")
        context_a = "Patient denies chest pain. Patient is otherwise stable. Prescribed Aspirin 81mg daily."
        res_a = verifier.verify(claim_a, evidence_a, context_text=context_a)
        assert res_a.label == "SUPPORTED"
        assert res_a.contradiction_type is None

        # Case B: affirmative claim where evidence text explicitly denies taking medication
        # Must detect genuine negation mismatch
        claim_b = _make_claim("med.name", "Aspirin", negated=False)
        evidence_b = _make_evidence("Patient denies taking Aspirin.")
        res_b = verifier.verify(claim_b, evidence_b)
        assert res_b.label == "DISCREPANCY"
        assert res_b.contradiction_type == "negation_mismatch"

        # Case C: negated claim where evidence text denies taking medication
        # Both claim and evidence agree on negation -> not a negation mismatch
        claim_c = _make_claim("med.name", "Aspirin", negated=True)
        evidence_c = _make_evidence("Patient denies taking Aspirin.")
        res_c = verifier.verify(claim_c, evidence_c)
        assert res_c.label == "SUPPORTED"
        assert res_c.contradiction_type is None

    def test_deterministic_numeric_multi_number_reference_range_filtering(self, verifier):
        # Evidence with primary value 180 and reference range (70 - 99)
        evidence = _make_evidence("Glucose: 180 mg/dL (Reference 70 - 99)")

        # Claim with primary value 180 should be SUPPORTED
        claim_correct = _make_claim("lab.value", "180", unit_raw="mg/dL", value_numeric=180.0)
        res_corr = verifier.verify(claim_correct, evidence)
        assert res_corr.label == "SUPPORTED"

        # Claim with reference boundary 70 must NOT be supported as the primary lab value
        claim_ref_val = _make_claim("lab.value", "70", unit_raw="mg/dL", value_numeric=70.0)
        res_ref = verifier.verify(claim_ref_val, evidence)
        assert res_ref.label == "DISCREPANCY"
        assert res_ref.contradiction_type == "value_mismatch"

    def test_deterministic_missing_evidence(self, verifier):
        claim = _make_claim("lab.value", "14.2")
        res_none = verifier.verify(claim, None)
        assert res_none.label == "INSUFFICIENT_EVIDENCE"

        irrelevant_ev = _make_evidence("Patient attended clinic appointment.")
        assert verifier.verify(claim, irrelevant_ev).label == "INSUFFICIENT_EVIDENCE"

    def test_deterministic_unobserved_medication_frequency_insufficient_evidence(self, verifier):
        """Unobserved medication frequency vs different medication must yield INSUFFICIENT_EVIDENCE."""
        claim = _make_claim("med.frequency", "Insulin glargine frequency: at bedtime")
        evidence = _make_evidence("Rx: Gabapentin 300 mg capsule. Sig: Take 1 capsule oral TID")
        res = verifier.verify(claim, evidence)
        assert res.label == "INSUFFICIENT_EVIDENCE"

    def test_deterministic_unobserved_medication_dose_insufficient_evidence(self, verifier):
        """Unobserved medication dose vs different medication dose must yield INSUFFICIENT_EVIDENCE."""
        claim = _make_claim("med.dose", "Insulin glargine dose: 20 units", value_numeric=20.0)
        evidence = _make_evidence("Rx: Atorvastatin 10 mg tablet. Sig: Take 1 tablet daily")
        res = verifier.verify(claim, evidence)
        assert res.label == "INSUFFICIENT_EVIDENCE"

    def test_deterministic_lab_value_unrelated_reference_range_not_supported(self, verifier):
        """Lab value vs unrelated reference-range number must not become SUPPORTED."""
        claim = _make_claim("lab.value", "Chloride: 10.2 mmol/L", unit_raw="mmol/L", value_numeric=10.2)
        evidence = _make_evidence("Calcium 9.4 8.5 - 10.2 mg/dL\nChloride 100.0 96 - 106 mmol/L")
        res = verifier.verify(claim, evidence)
        assert res.label != "SUPPORTED"
        assert res.label == "DISCREPANCY"
        assert res.contradiction_type == "value_mismatch"

    def test_deterministic_genuine_matching_lab_value_supported(self, verifier):
        """Genuine matching lab value for the same entity must be SUPPORTED."""
        claim = _make_claim("lab.value", "Chloride: 100.0 mmol/L", unit_raw="mmol/L", value_numeric=100.0)
        evidence = _make_evidence("Calcium 9.4 8.5 - 10.2 mg/dL\nChloride 100.0 96 - 106 mmol/L")
        res = verifier.verify(claim, evidence)
        assert res.label == "SUPPORTED"
        assert res.contradiction_type is None

    def test_deterministic_genuine_wrong_lab_value_discrepancy(self, verifier):
        """Genuine wrong lab value for the same entity must be DISCREPANCY."""
        claim = _make_claim("lab.value", "Chloride: 105.0 mmol/L", unit_raw="mmol/L", value_numeric=105.0)
        evidence = _make_evidence("Calcium 9.4 8.5 - 10.2 mg/dL\nChloride 100.0 96 - 106 mmol/L")
        res = verifier.verify(claim, evidence)
        assert res.label == "DISCREPANCY"
        assert res.contradiction_type == "value_mismatch"


# ─── 2. LEARNED VERIFIER TESTS (STAGE B) ─────────────────────────────────────

class TestStageBLearnedVerifier:
    """Tests for Stage B learned semantic verification and TemperatureScaler."""

    def test_stage_b_confident_supported_no_escalation(self):
        predictor = MockModelPredictor(default_label="SUPPORTED", confidence=0.92)
        scaler = TemperatureScaler(init_temperature=1.0)
        scaler.is_fitted = True

        stage_b = LearnedVerifier(custom_predictor=predictor, scaler=scaler)
        claim = _make_claim("med.name", "Amoxicillin")
        evidence = _make_evidence("Amoxicillin 500mg PO TID")

        res = stage_b.verify(claim, evidence, stage_a_label="SUPPORTED")
        assert res.label == "SUPPORTED"
        assert res.calibrated_confidence >= 0.60
        assert not res.escalated
        assert res.escalation_reason is None

    def test_stage_b_low_confidence_triggers_escalation(self):
        # Predictor with low confidence (e.g. 0.45)
        class LowConfPredictor:
            def predict_logits(self, field_type: str, claim_value: str, evidence_text: str) -> np.ndarray:
                return np.array([0.1, 0.05, 0.0], dtype=np.float32)

        stage_b = LearnedVerifier(custom_predictor=LowConfPredictor())
        claim = _make_claim("med.name", "Metformin")
        evidence = _make_evidence("Some ambiguous clinical text")

        res = stage_b.verify(claim, evidence, stage_a_label=None)
        assert res.escalated
        assert "low_confidence" in res.escalation_reason

    def test_stage_b_stage_a_disagreement_triggers_escalation(self):
        predictor = MockModelPredictor(default_label="SUPPORTED", confidence=0.95)
        stage_b = LearnedVerifier(custom_predictor=predictor)
        claim = _make_claim("lab.value", "14.2")
        evidence = _make_evidence("Hemoglobin 12.8")

        # Stage A detected discrepancy, but Stage B predicted SUPPORTED
        res = stage_b.verify(claim, evidence, stage_a_label="DISCREPANCY")
        assert res.escalated
        assert "stage_a_disagreement" in res.escalation_reason

    def test_stage_b_insufficient_evidence_triggers_escalation(self):
        predictor = MockModelPredictor(default_label="INSUFFICIENT_EVIDENCE", confidence=0.95)
        stage_b = LearnedVerifier(custom_predictor=predictor)
        claim = _make_claim("med.name", "UnknownDrug")
        evidence = _make_evidence("Lab panel showing normal results")

        res = stage_b.verify(claim, evidence, stage_a_label=None)
        assert res.escalated
        assert "insufficient_evidence" in res.escalation_reason

    def test_stage_b_raw_vs_calibrated_confidence_preservation(self):
        predictor = MockModelPredictor(default_label="SUPPORTED", confidence=0.90)
        # Scaler with temperature 2.0 (softens overconfidence)
        scaler = TemperatureScaler(init_temperature=2.0)
        scaler.is_fitted = True

        stage_b = LearnedVerifier(custom_predictor=predictor, scaler=scaler)
        claim = _make_claim("med.name", "Atorvastatin")
        evidence = _make_evidence("Atorvastatin 20mg daily")

        res = stage_b.verify(claim, evidence, stage_a_label="SUPPORTED")
        assert res.raw_confidence > res.calibrated_confidence
        assert 0.0 <= res.calibrated_confidence <= 1.0


# ─── 3. STAGE C LLM JUDGE TESTS ──────────────────────────────────────────────

class TestStageCLLMJudge:
    """Tests for Stage C schema enforcement (DEC-013) and calibration (DEC-018)."""

    def test_stage_c_schema_constrained_output_validation(self):
        # Valid output
        valid = StageCJudgeOutput(
            label="DISCREPANCY",
            confidence=0.85,
            contradiction_type="value_mismatch",
            rationale="The lab value 14.2 does not match source text 12.8.",
        )
        assert valid.label == "DISCREPANCY"

        # Discrepancy without contradiction_type MUST raise
        with pytest.raises(ValueError, match="contradiction_type must not be null"):
            StageCJudgeOutput(
                label="DISCREPANCY",
                confidence=0.85,
                contradiction_type=None,
                rationale="Value mismatch without specifying contradiction type.",
            )

        # Supported with contradiction_type MUST raise
        with pytest.raises(ValueError, match="contradiction_type must be null"):
            StageCJudgeOutput(
                label="SUPPORTED",
                confidence=0.95,
                contradiction_type="value_mismatch",
                rationale="Claim is supported.",
            )

    def test_stage_c_scalar_confidence_calibration(self):
        calibrator = IsotonicConfidenceCalibrator()
        calibrator.fit(
            confidences=[0.2, 0.4, 0.6, 0.8, 1.0],
            binary_labels=[0, 0, 1, 1, 1],
        )

        stage_c = StageCJudge(calibrator=calibrator)
        claim = _make_claim("med.name", "Lisinopril")
        evidence = _make_evidence("Lisinopril 10mg daily")

        res = stage_c.judge(claim, evidence)
        assert res.label in ("SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE")
        assert 0.0 <= res.calibrated_confidence <= 1.0
        assert len(res.rationale) >= 5


# ─── 4. FULL CASCADE INTEGRATION TESTS ───────────────────────────────────────

class TestVerificationCascadeIntegration:
    """End-to-end integration tests for the multi-stage verification cascade."""

    def test_tier_2_deterministic_resolution_bypasses_stage_b(self):
        # Tier 2 field (lab.unit) should resolve at Stage A without calling Stage B
        class FailIfCalledPredictor:
            def predict_logits(self, *args, **kwargs):
                raise AssertionError("Stage B should not be called for Tier 2 field")

        cascade = VerificationCascade(
            stage_b=LearnedVerifier(custom_predictor=FailIfCalledPredictor())
        )
        claim = _make_claim("lab.unit", "mg/dL")
        evidence = _make_evidence("Glucose: 105 mg/dL")

        res = cascade.verify_claim(claim, evidence)
        assert res.stage_reached == "A"
        assert res.label == "SUPPORTED"
        assert res.confidence == 1.0
        assert res.stage_a_label == "SUPPORTED"
        assert res.stage_b_label is None
        assert res.rationale is None
        assert not res.escalated

    def test_tier_1_confident_agreement_resolves_at_stage_b(self):
        # Amoxicillin supported at Stage A and Stage B with high confidence -> Stage B final
        mock_judge_called = False

        class TrackingJudge(StageCJudge):
            def judge(self, *args, **kwargs):
                nonlocal mock_judge_called
                mock_judge_called = True
                return super().judge(*args, **kwargs)

        cascade = VerificationCascade(stage_c=TrackingJudge())
        claim = _make_claim("med.name", "Amoxicillin")
        evidence = _make_evidence("Amoxicillin 500mg PO TID")

        res = cascade.verify_claim(claim, evidence)
        assert res.stage_reached == "B"
        assert res.label == "SUPPORTED"
        assert not res.escalated
        assert res.rationale is None
        assert not mock_judge_called, "Stage C must NOT be invoked when not escalated (§12.2)"

    def test_disagreement_escalates_to_stage_c_and_populates_rationale(self):
        # Stage A detects discrepancy, Stage B claims SUPPORTED -> escalates to Stage C
        predictor = MockModelPredictor(default_label="SUPPORTED", confidence=0.95)
        cascade = VerificationCascade(
            stage_b=LearnedVerifier(custom_predictor=predictor)
        )
        claim = _make_claim("lab.value", "14.2", unit_raw="g/dL", value_numeric=14.2)
        evidence = _make_evidence("Hemoglobin: 12.8 g/dL")

        res = cascade.verify_claim(claim, evidence)
        assert res.stage_reached == "C"
        assert res.escalated
        assert res.stage_a_label == "DISCREPANCY"
        assert res.stage_b_label == "SUPPORTED"
        assert res.rationale is not None
        assert len(res.rationale) > 5

    def test_low_confidence_stage_b_escalates_to_stage_c(self):
        class LowConfPredictor:
            def predict_logits(self, *args, **kwargs):
                return np.array([0.1, 0.05, 0.0], dtype=np.float32)

        cascade = VerificationCascade(
            stage_b=LearnedVerifier(custom_predictor=LowConfPredictor())
        )
        claim = _make_claim("med.name", "Metformin")
        evidence = _make_evidence("Patient instructed on diabetes care.")

        res = cascade.verify_claim(claim, evidence)
        assert res.stage_reached == "C"
        assert res.escalated

    def test_missing_evidence_yields_insufficient_evidence(self):
        cascade = VerificationCascade()
        claim = _make_claim("lab.value", "14.2")

        res = cascade.verify_claim(claim, None)
        assert res.label == "INSUFFICIENT_EVIDENCE"
        assert res.stage_reached == "A"
        assert res.contradiction_type is None
        assert res.confidence == 1.0

    def test_verification_result_contract_and_evidence_preservation(self):
        cascade = VerificationCascade()
        claim = _make_claim("med.dose", "500", unit_raw="mg", value_numeric=500.0, evidence_id="ev-999")
        evidence = _make_evidence("Amoxicillin 500mg", evidence_id="ev-999")

        res = cascade.verify_claim(claim, evidence)
        # Verify result is a valid VerificationResult instance
        assert isinstance(res, VerificationResult)
        assert res.claim_id == claim.claim_id
        assert res.evidence_id == "ev-999"
        assert res.label in ("SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE")

        # Invariant checks
        if res.label == "DISCREPANCY":
            assert res.contradiction_type is not None
        else:
            assert res.contradiction_type is None

        if res.stage_reached in ("A", "B"):
            assert res.rationale is None
        elif res.stage_reached == "C":
            assert res.rationale is not None

    def test_batch_verification_claims(self):
        cascade = VerificationCascade()
        claims = [
            _make_claim("lab.value", "14.2", unit_raw="g/dL", value_numeric=14.2, claim_id="c1", evidence_id="e1"),
            _make_claim("med.dose", "500", unit_raw="mg", value_numeric=500.0, claim_id="c2", evidence_id="e2"),
            _make_claim("lab.unit", "mg/dL", claim_id="c3", evidence_id="e3"),
        ]
        evidence_map = {
            "e1": _make_evidence("Hb 14.2 g/dL", evidence_id="e1"),
            "e2": _make_evidence("Amox 250mg", evidence_id="e2"),
            "e3": _make_evidence("Glucose 110 mg/dL", evidence_id="e3"),
        }
        results = cascade.verify_claims(claims, evidence_map)
        assert len(results) == 3
        assert results[0].claim_id == "c1"
        assert results[0].label == "SUPPORTED"
        assert results[1].claim_id == "c2"
        assert results[1].label == "DISCREPANCY"
        assert results[1].contradiction_type == "dose_mismatch"
        assert results[2].claim_id == "c3"
        assert results[2].stage_reached == "A"
        assert results[2].label == "SUPPORTED"
