"""test_eval_phase4_cascade.py — Unit and integration tests for Phase 4 cascade evaluation runner.

Verifies:
  1. Correct conversion from M2Example to canonical Claim and Evidence.
  2. Calculation of §21 verification metrics (Macro-F1, Discrepancy Recall, AUROC, Brier, ECE).
  3. Calculation of §21.5 cascade dynamics (Stage A resolution rate, Stage C escalation rate, conditional accuracy).
  4. End-to-end execution of run_phase4_evaluation on a subset of examples.
  5. Report generation and structure.
"""
import json
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


def test_m2_example_to_claim_and_evidence_preserves_full_context():
    """Requirement 1: Verify full entity context is preserved for Stage B fidelity."""
    ex_med = _make_mock_example(field_type="med.name", claim="Medication: Amoxicillin")
    claim_med, ev_med = m2_example_to_claim_and_evidence(ex_med)

    assert claim_med.field_type == "med.name"
    # Must preserve exact claim representation used in M2 training
    assert claim_med.value == "Medication: Amoxicillin"
    assert ev_med.text == ex_med.evidence
    assert ev_med.bbox.to_list() == [10.0, 20.0, 100.0, 40.0]

    ex_val = _make_mock_example(field_type="lab.value", claim="Glucose: 105 mg/dL")
    claim_val, _ = m2_example_to_claim_and_evidence(ex_val)
    assert claim_val.field_type == "lab.value"
    assert claim_val.value == "Glucose: 105 mg/dL"
    # Stage A numeric fields must still be parsed
    assert claim_val.value_numeric == 105.0
    assert claim_val.unit_raw == "mg/dL"


def test_evaluate_cascade_on_examples_metrics():
    """Verify calculation of overall and cascade metrics with genuine probabilities."""
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

    # Probability metrics are present because Stage B provided calibrated probabilities
    assert metrics["overall"]["brier_score"] is not None
    assert metrics["overall"]["ece"] is not None

    assert metrics["cascade_behavior"]["stage_a_resolved"] >= 0
    assert metrics["cascade_behavior"]["stage_c_escalation_rate"] >= 0.0


def test_stage_c_mock_explicitly_flagged_as_mock():
    """Requirement 3: Verify Stage C DefaultMockLLMJudge is explicitly labeled as MOCK_ABLATION."""
    examples = [
        _make_mock_example("med.name", "Medication: Amoxicillin", "Amoxicillin 500mg", "SUPPORTED"),
    ]
    cascade = VerificationCascade(
        stage_a=DeterministicVerifier(),
        stage_b=LearnedVerifier(custom_predictor=MockModelPredictor(default_label="SUPPORTED", confidence=0.95)),
        stage_c=StageCJudge(client=DefaultMockLLMJudge()),
    )
    metrics = evaluate_cascade_on_examples(cascade, examples, split_name="test_split")
    assert metrics["stage_c_mode"] == "MOCK_ABLATION"
    assert "NOT an actual clinical LLM" in metrics["stage_c_notice"]


def test_actual_stage_b_probability_capture_and_no_fabrication():
    """Requirement 2: Ensure actual 3-class probabilities are captured without synthetic vector fabrication."""
    class CustomProbPredictor:
        def predict_logits(self, field_type: str, claim_value: str, evidence_text: str):
            import numpy as np
            # Return distinct 3-class logits
            if "Amoxicillin" in claim_value:
                return np.array([2.5, -1.0, -1.5], dtype=np.float32)
            return np.array([-1.5, 2.5, -1.0], dtype=np.float32)

    examples = [
        _make_mock_example("med.name", "Medication: Amoxicillin", "Amoxicillin 500mg", "SUPPORTED"),
        _make_mock_example("med.name", "Medication: Lisinopril", "Metformin 500mg", "DISCREPANCY"),
    ]

    cascade = VerificationCascade(
        stage_a=DeterministicVerifier(),
        stage_b=LearnedVerifier(custom_predictor=CustomProbPredictor()),
        stage_c=StageCJudge(client=DefaultMockLLMJudge()),
    )

    metrics = evaluate_cascade_on_examples(cascade, examples, split_name="test_split")
    probs_metrics = metrics["overall"]["stage_b_probabilities"]
    assert probs_metrics is not None
    assert probs_metrics["n_examples"] == 2
    assert "auroc_macro" in probs_metrics
    assert "brier_score" in probs_metrics
    assert "ece" in probs_metrics


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
        assert val_res["stage_c_mode"] == "MOCK_ABLATION"

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


def test_stage_b_model_initialized_only_once_across_evaluation(monkeypatch):
    """Regression test (Requirement 9): Verify Stage B ModernBERT is initialized exactly once

    and reused across >= 5 examples without being reloaded inside evaluate_cascade_on_examples.
    """
    import unittest.mock as mock
    import numpy as np
    import torch

    load_call_count = 0
    orig_load_model = LearnedVerifier.load_model

    def spied_load_model(self):
        nonlocal load_call_count
        load_call_count += 1
        return orig_load_model(self)

    monkeypatch.setattr(LearnedVerifier, "load_model", spied_load_model)

    # Create dummy neural model to verify _model lifecycle
    dummy_model = mock.MagicMock()
    # Return mock logits tensor: shape (1, 3)
    dummy_model.return_value = torch.tensor([[2.5, -1.0, -1.5]], dtype=torch.float32)

    dummy_tokenizer = mock.MagicMock()
    dummy_tokenizer.return_value = {
        "input_ids": torch.tensor([[101, 102]], dtype=torch.long),
        "attention_mask": torch.tensor([[1, 1]], dtype=torch.long),
    }

    # Instantiate Stage B outside the loop
    stage_b = LearnedVerifier(escalation_threshold=0.60, eager_load=False)
    stage_b._model = dummy_model
    stage_b._tokenizer = dummy_tokenizer
    stage_b._device = "cpu"

    # Pre-loop initialization count: 0 (or 1 if load_model called)
    assert load_call_count == 0
    model_instance_id = id(stage_b._model)

    cascade = VerificationCascade(
        stage_a=DeterministicVerifier(),
        stage_b=stage_b,
        stage_c=StageCJudge(client=DefaultMockLLMJudge()),
    )

    # 5 evaluation examples
    examples = [
        _make_mock_example("med.name", f"Medication: Drug{i}", f"Drug{i} 10mg", "SUPPORTED")
        for i in range(5)
    ]

    metrics = evaluate_cascade_on_examples(cascade, examples, split_name="test_split")

    # 1. Verification that load_model was NOT called inside the loop (zero reloads)
    assert load_call_count == 0
    # 2. Verification that the single loaded model instance was reused for all 5 examples
    assert dummy_model.call_count == 5
    assert id(stage_b._model) == model_instance_id
    # 3. Verification that genuine logits/probabilities were captured for all 5 examples
    assert metrics["overall"]["stage_b_probabilities"]["n_examples"] == 5
    assert metrics["overall"]["brier_score"] is not None


class RecordingMockLLMJudge:
    """Mock judge that records the exact prompts received for assertion."""

    def __init__(self) -> None:
        self.recorded_prompts: list[str] = []

    def generate(self, prompt: str, schema: type) -> str:
        self.recorded_prompts.append(prompt)
        return json.dumps({
            "label": "SUPPORTED",
            "confidence": 0.95,
            "contradiction_type": None,
            "rationale": "Grounded in context",
        })


def test_evaluator_passes_page_text_and_expands_stage_c_context():
    """Requirement 6 (FIX 2): Prove that when page_text is supplied, Stage C receives expanded local context."""
    from clindoc.m2.context import extract_context_window
    from eval.eval_phase4_cascade import load_page_text_for_document

    # 1. Authoritative page text is successfully retrieved from canonical OCR caches
    page_text_lab = load_page_text_for_document("LAB_0001", page_number=1)
    assert page_text_lab is not None
    assert len(page_text_lab) > 100

    # 2. Establish ground truth character offsets within the document text
    ev_phrase = "Metro Health Diagnostic Center"
    char_start = page_text_lab.find(ev_phrase)
    assert char_start >= 0
    char_end = char_start + len(ev_phrase)

    ex = _make_mock_example(
        field_type="lab.value",
        claim="Glucose: 105 mg/dL",
        evidence=ev_phrase,
        label="SUPPORTED",
    )
    ex.source_doc_id = "LAB_0001"
    ex.char_start = char_start
    ex.char_end = char_end
    ex.page_number = 1

    recording_client = RecordingMockLLMJudge()
    cascade = VerificationCascade(
        stage_a=DeterministicVerifier(),
        stage_b=LearnedVerifier(custom_predictor=MockModelPredictor(default_label="SUPPORTED", confidence=0.50)),
        stage_c=StageCJudge(client=recording_client),
        always_stage_c=True,
    )

    claim, evidence = m2_example_to_claim_and_evidence(ex)

    # A) Direct verify without page_text: Stage C context_text is ONLY evidence.text
    cascade.verify_claim_detailed(claim=claim, evidence=evidence, page_text=None)
    prompt_without_pt = recording_client.recorded_prompts[-1]
    assert f"- Document Local Context Window: {ev_phrase}" in prompt_without_pt

    # B) Direct verify with page_text: Stage C context_text is expanded window
    cascade.verify_claim_detailed(claim=claim, evidence=evidence, page_text=page_text_lab)
    prompt_with_pt = recording_client.recorded_prompts[-1]
    expected_window, _, _ = extract_context_window(page_text_lab, char_start, char_end, is_tabular=True)
    assert f"- Document Local Context Window: {expected_window}" in prompt_with_pt
    assert len(expected_window) > len(ev_phrase)

    # C) Evaluator pipeline execution: evaluate_cascade_on_examples automatically loads and passes page_text
    recording_client.recorded_prompts.clear()
    evaluate_cascade_on_examples(cascade, [ex], split_name="test_split")
    evaluator_prompt = recording_client.recorded_prompts[-1]
    assert f"- Document Local Context Window: {expected_window}" in evaluator_prompt


def test_diagnostics_disabled_by_default_preserves_clean_report():
    """Verify diagnostics are NOT recorded or written unless explicitly requested."""
    examples = [
        _make_mock_example("med.name", "Medication: Amoxicillin", "Amoxicillin 500mg", "SUPPORTED"),
    ]
    cascade = VerificationCascade(
        stage_a=DeterministicVerifier(),
        stage_b=LearnedVerifier(custom_predictor=MockModelPredictor(default_label="SUPPORTED", confidence=0.95)),
        stage_c=StageCJudge(client=DefaultMockLLMJudge()),
    )
    # 1. evaluate_cascade_on_examples default: record_diagnostics=False
    metrics = evaluate_cascade_on_examples(cascade, examples, split_name="test_split")
    assert "diagnostics" not in metrics

    # 2. run_phase4_evaluation default: save_diagnostics=False
    with tempfile.TemporaryDirectory() as tmp_dir:
        report = run_phase4_evaluation(
            data_dir="data/m2_verification",
            results_dir=tmp_dir,
            split="val",
            max_examples=2,
            save_diagnostics=False,
        )
        assert "diagnostics_file" not in report
        assert not (Path(tmp_dir) / "phase4_cascade_diagnostics.json").exists()


def test_diagnostics_records_all_required_fields():
    """Verify all 11 diagnostic fields are recorded with exact types and no Stage C probability fabrication."""
    class MultiMockPredictor:
        def predict_logits(self, field_type: str, claim_value: str, evidence_text: str):
            import numpy as np
            if "HighConf" in claim_value:
                return np.array([3.0, -1.0, -2.0], dtype=np.float32)  # High conf -> Stage B resolved
            return np.array([0.2, 0.1, -0.3], dtype=np.float32)  # Low conf -> Escalated to Stage C

    examples = [
        # Example 1: Stage A resolved (Tier-2 field)
        _make_mock_example("lab.unit", "mg/dL", "mg/dL", "SUPPORTED"),
        # Example 2: Stage B resolved
        _make_mock_example("med.name", "Medication: HighConfDrug", "HighConfDrug 10mg", "SUPPORTED"),
        # Example 3: Stage C resolved (escalated from Stage B)
        _make_mock_example("med.name", "Medication: AmbiguousDrug", "DifferentDrug 20mg", "DISCREPANCY", "wrong_entity"),
    ]

    cascade = VerificationCascade(
        stage_a=DeterministicVerifier(),
        stage_b=LearnedVerifier(custom_predictor=MultiMockPredictor(), escalation_threshold=0.60),
        stage_c=StageCJudge(client=DefaultMockLLMJudge(model_name="test-mock-judge")),
    )

    metrics = evaluate_cascade_on_examples(cascade, examples, split_name="val", record_diagnostics=True)
    assert "diagnostics" in metrics
    diags = metrics["diagnostics"]
    assert len(diags) == 3

    # Check Example 1: Stage A
    d0 = diags[0]
    assert d0["example_id"] == "ex-001"
    assert d0["group_id"] == "med_0"
    assert d0["document_id"] == "MED_0001"
    assert d0["field_type"] == "lab.unit"
    assert d0["ground_truth_label"] == "SUPPORTED"
    assert d0["stage_a"]["resolved"] is True
    assert d0["stage_a"]["label"] == "SUPPORTED"
    assert d0["stage_a"]["resolution_reason"] == "tier_2_deterministic"
    assert d0["stage_b"]["label"] is None
    assert d0["stage_c"]["invoked"] is False
    assert d0["cascade"]["stage_reached"] == "A"
    assert d0["cascade"]["final_label"] == "SUPPORTED"
    assert d0["claim"]["value"] == "mg/dL"
    assert d0["evidence"]["text"] == "mg/dL"

    # Check Example 2: Stage B resolved
    d1 = diags[1]
    assert d1["cascade"]["stage_reached"] == "B"
    assert d1["stage_b"]["label"] == "SUPPORTED"
    assert d1["stage_b"]["calibrated_confidence"] is not None
    assert d1["stage_b"]["calibrated_probabilities"] is not None
    assert "SUPPORTED" in d1["stage_b"]["calibrated_probabilities"]
    assert "DISCREPANCY" in d1["stage_b"]["calibrated_probabilities"]
    assert "INSUFFICIENT_EVIDENCE" in d1["stage_b"]["calibrated_probabilities"]
    assert d1["stage_b"]["escalated"] is False
    assert d1["stage_c"]["invoked"] is False

    # Check Example 3: Stage C invoked
    d2 = diags[2]
    assert d2["cascade"]["stage_reached"] == "C"
    assert d2["stage_b"]["escalated"] is True
    assert "low_confidence" in d2["stage_b"]["escalation_reason"]
    assert d2["stage_c"]["invoked"] is True
    assert d2["stage_c"]["label"] in ("SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE")
    assert isinstance(d2["stage_c"]["raw_confidence"], float)
    assert isinstance(d2["stage_c"]["calibrated_confidence"], float)
    # Stage C must NOT fabricate probability distribution
    assert "calibrated_probabilities" not in d2["stage_c"]
    assert d2["stage_c"]["is_fallback"] is False
    assert d2["stage_c"]["error"] is None
    assert d2["stage_c"]["model_name"] == "test-mock-judge"
    assert d2["metadata"]["stage_c_client"] == "test-mock-judge"


def test_run_phase4_evaluation_saves_diagnostics_file_end_to_end():
    """Verify that run_phase4_evaluation writes a separate phase4_cascade_diagnostics.json file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        report = run_phase4_evaluation(
            data_dir="data/m2_verification",
            results_dir=tmp_dir,
            split="val",
            fold_idx=0,
            max_examples=4,
            save_diagnostics=True,
        )
        assert report["status"] == "COMPLETED"
        assert "diagnostics_file" in report

        diag_path = Path(report["diagnostics_file"])
        assert diag_path.exists()
        assert diag_path.name == "phase4_cascade_diagnostics.json"

        with open(diag_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["status"] == "COMPLETED"
        assert "validation" in data["diagnostics"]
        assert len(data["diagnostics"]["validation"]) == 4

        # Primary eval report must retain clean aggregate metrics without redundant diagnostics dump
        report_path = Path(tmp_dir) / "phase4_cascade_eval_report.json"
        with open(report_path, "r", encoding="utf-8") as f:
            eval_report = json.load(f)
        assert "diagnostics" not in eval_report["results"]["validation"]
        assert "macro_f1" in eval_report["results"]["validation"]["overall"]


def test_cli_save_diagnostics_flag():
    """Verify CLI accepts --save-diagnostics flag and produces diagnostic artifact."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        report = main([
            "--data-dir", "data/m2_verification",
            "--results-dir", tmp_dir,
            "--split", "val",
            "--fold", "0",
            "--max-examples", "3",
            "--save-diagnostics",
        ])
        assert report["status"] == "COMPLETED"
        assert (Path(tmp_dir) / "phase4_cascade_diagnostics.json").exists()


def test_evaluator_defaults_to_v3_dataset_and_verifies_split_counts():
    """Requirement 7: Verify evaluator defaults to data/m2_verification_v3 with exact V3 counts."""
    from eval.eval_phase4_cascade import DEFAULT_DATA_DIR
    from clindoc.m2.dataset import load_examples
    from clindoc.m2.splits import load_validation_examples, get_ood_examples

    assert DEFAULT_DATA_DIR == Path("data/m2_verification_v3")

    # Verify partition counts directly against authoritative V3 files
    examples = load_examples(DEFAULT_DATA_DIR / "all_examples.jsonl")
    with open(DEFAULT_DATA_DIR / "split_manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert len(examples) == 5629
    val_ex = load_validation_examples(examples, manifest)
    assert len(val_ex) == 883
    ood_ex = get_ood_examples(examples, manifest)
    assert len(ood_ex) == 1826

    # Verify runner defaults to V3 and records 5629 total examples
    with tempfile.TemporaryDirectory() as tmp_dir:
        report = run_phase4_evaluation(
            results_dir=tmp_dir,
            split="val",
            max_examples=2,
        )
        assert report["status"] == "COMPLETED"
        assert Path(report["data_dir"]) == Path("data/m2_verification_v3")
        assert report["total_examples"] == 5629



