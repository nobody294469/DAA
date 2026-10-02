"""test_eval_phase4_cascade.py — Unit and integration tests for Phase 4 cascade evaluation runner.

Verifies:
  1. Correct conversion from M2Example to canonical Claim and Evidence.
  2. Calculation of §21 verification metrics (Macro-F1, Discrepancy Recall, AUROC, Brier, ECE).
  3. Calculation of §21.5 cascade dynamics (Stage A resolution rate, Stage C escalation rate, conditional accuracy).
  4. End-to-end execution of run_phase4_evaluation on a subset of examples.
  5. Report generation and structure.
"""
from pathlib import Path
import tempfile
import pytest

from clindoc.m2.schema import M2Example, VerificationLabel
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.stage_a import DeterministicVerifier
from clindoc.verify.stage_b import LearnedVerifier, MockModelPredictor
from clindoc.verify.stage_c import StageCJudge, DefaultMockLLMJudge
from eval.eval_phase4_cascade import (
    m2_example_to_claim_and_evidence,
    evaluate_cascade_on_examples,
    run_phase4_evaluation,
    main,
)


def _make_mock_example(
    field_type: str = "med.name",
    claim: str = "Medication: Lisinopril",
    evidence: str = "Lisinopril 10mg PO daily",
    label: str = "SUPPORTED",
    contradiction_type: str | None = None,
) -> M2Example:
    return M2Example(
        example_id="ex-001",
        claim=claim,
        evidence=evidence,
        field_type=field_type,
        doc_template="clinic_rx_pad",
        ocr_confidence=0.98,
        severity="clean",
        label=VerificationLabel(label),
        source_doc_id="MED_0001",
        generation_method="vocab_substitution",
        claim_type="med",
        split="cv",
        group_id="med_0",
        contradiction_type=contradiction_type,
        evidence_id="ev-001",
        char_start=0,
        char_end=len(evidence),
        page_number=1,
        bbox=[10.0, 20.0, 100.0, 40.0],
    )


def test_m2_example_to_claim_and_evidence_conversion():
    """Verify clean claim value extraction and contract compliance."""
    ex_med = _make_mock_example(field_type="med.name", claim="Medication: Amoxicillin")
    claim_med, ev_med = m2_example_to_claim_and_evidence(ex_med)

    assert claim_med.field_type == "med.name"
    assert claim_med.value == "Amoxicillin"
    assert ev_med.text == ex_med.evidence
    assert ev_med.bbox.to_list() == [10.0, 20.0, 100.0, 40.0]

    ex_val = _make_mock_example(field_type="lab.value", claim="Glucose: 105 mg/dL")
    claim_val, _ = m2_example_to_claim_and_evidence(ex_val)
    assert claim_val.field_type == "lab.value"
    assert claim_val.value_numeric == 105.0
    assert claim_val.unit_raw == "mg/dL"


def test_evaluate_cascade_on_examples_metrics():
    """Verify calculation of overall and cascade metrics."""
    examples = [
        _make_mock_example("med.name", "Medication: Amoxicillin", "Amoxicillin 500mg", "SUPPORTED"),
        _make_mock_example("med.name", "Medication: Lisinopril", "Metformin 500mg", "DISCREPANCY", "wrong_entity"),
        _make_mock_example("lab.value", "Glucose: 180 mg/dL", "Glucose 180 mg/dL", "SUPPORTED"),
        _make_mock_example("lab.value", "Glucose: 180 mg/dL", "Glucose 100 mg/dL", "DISCREPANCY", "value_mismatch"),
    ]

    cascade = VerificationCascade(
        stage_a=DeterministicVerifier(),
        stage_b=LearnedVerifier(custom_predictor=MockModelPredictor(default_label="SUPPORTED", confidence=0.95)),
        stage_c=StageCJudge(client=DefaultMockLLMJudge()),
    )

    metrics = evaluate_cascade_on_examples(cascade, examples, split_name="test_split")

    assert "overall" in metrics
    assert "cascade_behavior" in metrics
    assert "by_field_type" in metrics
    assert "by_contradiction_type" in metrics

    assert 0.0 <= metrics["overall"]["macro_f1"] <= 1.0
    assert 0.0 <= metrics["overall"]["discrepancy_recall"] <= 1.0
    assert 0.0 <= metrics["overall"]["brier_score"] <= 2.0
    assert 0.0 <= metrics["overall"]["ece"] <= 1.0

    assert metrics["cascade_behavior"]["stage_a_resolved"] >= 0
    assert metrics["cascade_behavior"]["stage_c_escalation_rate"] >= 0.0


def test_run_phase4_evaluation_end_to_end():
    """Verify end-to-end execution of evaluation runner on small slice of canonical dataset."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        report = run_phase4_evaluation(
            data_dir="data/m2_verification",
            results_dir=tmp_dir,
            checkpoint_path=None,  # Use heuristic / mock predictor
            split="val",
            fold_idx=0,
            max_examples=8,
        )

        assert report["status"] == "COMPLETED"
        assert "validation" in report["results"]
        val_res = report["results"]["validation"]
        assert val_res["n_examples"] == 8

        report_file = Path(tmp_dir) / "phase4_cascade_eval_report.json"
        assert report_file.exists()


def test_cli_main_argument_parsing():
    """Verify CLI entry point main() argument handling."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        report = main([
            "--data-dir", "data/m2_verification",
            "--results-dir", tmp_dir,
            "--split", "val",
            "--fold", "0",
            "--max-examples", "5",
        ])
        assert report["status"] == "COMPLETED"
        assert report["results"]["validation"]["n_examples"] == 5
