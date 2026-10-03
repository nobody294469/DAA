"""Unit tests for ModernBERT calibration temperature persistence and evaluation."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import torch

from clindoc.m2.schema import (
    M2Example,
    M2ModelConfig,
    M2EvalResult,
    VerificationLabel,
)
from clindoc.m2.evaluate import evaluate_model_on_split
from clindoc.m2.recovery import (
    is_quarantined_checkpoint,
    is_quarantined_result,
    discover_checkpoints,
    save_incremental_results,
    recover_and_evaluate_checkpoint,
    load_existing_results_map,
    QUARANTINED_CHECKPOINT_NAMES,
)


class DummyModel(torch.nn.Module):
    """Dummy model that outputs deterministic logits for 3 classes."""
    def __init__(self, logits_matrix: torch.Tensor):
        super().__init__()
        self.logits_matrix = logits_matrix
        self.step = 0

    def forward(self, input_ids, attention_mask, aux_features=None):
        n = input_ids.size(0)
        out = self.logits_matrix[:n].clone()
        return out


def _make_dummy_examples(n: int = 4) -> list[M2Example]:
    labels = [
        VerificationLabel.SUPPORTED,
        VerificationLabel.DISCREPANCY,
        VerificationLabel.INSUFFICIENT_EVIDENCE,
        VerificationLabel.SUPPORTED,
    ]
    examples = []
    for i in range(n):
        examples.append(
            M2Example(
                example_id=f"ex_{i}",
                source_doc_id=f"doc_{i}",
                claim="Claim text",
                evidence="Evidence text",
                field_type="lab.value",
                claim_type="lab",
                label=labels[i % len(labels)],
                severity="medium",
                doc_template="template_a",
                ocr_confidence=0.95,
                generation_method="natural",
            )
        )
    return examples


def _dummy_tokenizer(texts, padding=True, truncation=True, max_length=256, return_tensors="pt"):
    n = len(texts)
    return {
        "input_ids": torch.ones((n, 8), dtype=torch.long),
        "attention_mask": torch.ones((n, 8), dtype=torch.long),
    }


def test_temperature_one_preserves_probability_behavior():
    """temperature=1.0 matches standard softmax logits and preserves output probabilities."""
    examples = _make_dummy_examples(4)
    # 4 examples with distinctive logits
    logits = torch.tensor([
        [2.0, -1.0, 0.5],
        [-0.5, 3.0, 0.0],
        [0.0, 1.0, 2.5],
        [1.5, 0.2, -0.8],
    ], dtype=torch.float32)

    model = DummyModel(logits)
    config = M2ModelConfig(batch_size=8)

    res_default = evaluate_model_on_split(
        model=model,
        tokenizer=_dummy_tokenizer,
        examples=examples,
        model_name="dummy_verifier",
        split="cv",
        fold=0,
        seed=42,
        config=config,
        device="cpu",
        aux_mode="none",
        temperature=1.0,
    )

    expected_probs = torch.softmax(logits, dim=-1).tolist()
    # Check that predictions and probabilities match
    assert res_default.calibration_temperature == 1.0
    assert res_default.brier_score is not None
    assert res_default.ece is not None

    # Compare directly with standard softmax Brier score
    y_true = [0, 1, 2, 0]  # indices matching VerificationLabel
    expected_brier = 0.0
    for i, true_idx in enumerate(y_true):
        for c in range(3):
            target = 1.0 if c == true_idx else 0.0
            expected_brier += (expected_probs[i][c] - target) ** 2
    expected_brier /= len(examples)
    assert pytest.approx(res_default.brier_score, rel=1e-3) == expected_brier


def test_temperature_scales_probability_dependent_metrics():
    """temperature != 1.0 changes probabilities, Brier, and ECE while preserving discrete argmax predictions."""
    examples = _make_dummy_examples(4)
    logits = torch.tensor([
        [3.0, -1.0, 0.5],
        [-0.5, 4.0, 0.0],
        [0.0, 1.0, 3.5],
        [2.5, 0.2, -0.8],
    ], dtype=torch.float32)

    model = DummyModel(logits)
    config = M2ModelConfig(batch_size=8)

    res_t1 = evaluate_model_on_split(
        model=model,
        tokenizer=_dummy_tokenizer,
        examples=examples,
        model_name="dummy_verifier",
        split="cv",
        fold=0,
        seed=42,
        config=config,
        device="cpu",
        aux_mode="none",
        temperature=1.0,
    )

    res_t2 = evaluate_model_on_split(
        model=model,
        tokenizer=_dummy_tokenizer,
        examples=examples,
        model_name="dummy_verifier",
        split="cv",
        fold=0,
        seed=42,
        config=config,
        device="cpu",
        aux_mode="none",
        temperature=2.0,
    )

    # Argmax predictions are invariant to temperature scaling (T > 0)
    assert res_t1.macro_f1 == res_t2.macro_f1
    assert res_t1.discrepancy_recall == res_t2.discrepancy_recall
    assert res_t1.confusion_matrix == res_t2.confusion_matrix

    # But probability-dependent metrics must change
    assert res_t1.brier_score != res_t2.brier_score
    assert res_t1.ece != res_t2.ece
    assert res_t2.calibration_temperature == 2.0


def test_calibration_temperature_persisted_in_schema():
    """calibration_temperature field is serialized in M2EvalResult."""
    result = M2EvalResult(
        model_name="modernbert_full",
        split="cv",
        fold=1,
        seed=43,
        macro_f1=0.88,
        discrepancy_recall=0.85,
        per_class={},
        confusion_matrix=[[10, 0, 0], [0, 10, 0], [0, 0, 10]],
        calibration_temperature=1.2345,
    )
    dumped = result.model_dump()
    assert "calibration_temperature" in dumped
    assert dumped["calibration_temperature"] == 1.2345

    # Roundtrip JSON
    loaded = M2EvalResult.model_validate(json.loads(result.model_dump_json()))
    assert loaded.calibration_temperature == 1.2345


def test_checkpoint_recovery_preserves_temperature(tmp_path):
    """recover_and_evaluate_checkpoint reads temperature from state_dict and passes it to evaluation."""
    ckpt_path = tmp_path / "modernbert_text_only_fold0_seed42.pt"
    dummy_state = {
        "model_state": {},
        "config": {"model_id": "test_id", "batch_size": 8},
        "aux_mode": "none",
        "freeze_lower_n_layers": 0,
        "fold": 0,
        "seed": 42,
        "best_val_macro_f1": 0.89,
        "model_name": "modernbert_text_only",
        "temperature": 1.4567,
    }
    torch.save(dummy_state, ckpt_path)

    dummy_ex = _make_dummy_examples(4)
    manifest = {
        "n_splits": 5,
        "ood_doc_ids": [],
        "cv_folds": [{"train_doc_ids": [], "val_doc_ids": [e.source_doc_id for e in dummy_ex]}],
    }

    mock_eval_result = M2EvalResult(
        model_name="modernbert_text_only",
        split="cv",
        fold=0,
        seed=42,
        macro_f1=0.89,
        discrepancy_recall=0.85,
        per_class={},
        confusion_matrix=[],
        calibration_temperature=1.4567,
    )

    with patch("clindoc.m2.recovery.ClinicalVerifier") as mock_model_cls, \
         patch("clindoc.m2.recovery.load_tokenizer") as mock_tok, \
         patch("clindoc.m2.recovery.evaluate_model_on_split") as mock_eval:

        mock_instance = MagicMock()
        mock_model_cls.return_value = mock_instance
        mock_eval.return_value = mock_eval_result

        recovered = recover_and_evaluate_checkpoint(
            checkpoint_path=ckpt_path,
            examples=dummy_ex,
            manifest=manifest,
        )

        assert recovered["calibration_temperature"] == 1.4567
        assert recovered["val_eval"]["calibration_temperature"] == 1.4567

        # Verify evaluate_model_on_split was called with temperature=1.4567
        mock_eval.assert_called_once()
        _, kwargs = mock_eval.call_args
        assert kwargs["temperature"] == 1.4567


def test_checkpoint_recovery_defaults_temperature_when_missing(tmp_path):
    """When checkpoint lacks 'temperature' key, it defaults to 1.0 without error."""
    ckpt_path = tmp_path / "modernbert_text_only_fold1_seed43.pt"
    dummy_state = {
        "model_state": {},
        "config": {"model_id": "test_id", "batch_size": 8},
        "aux_mode": "none",
        "freeze_lower_n_layers": 0,
        "fold": 1,
        "seed": 43,
        "best_val_macro_f1": 0.87,
        "model_name": "modernbert_text_only",
        # No "temperature" key (e.g. legacy/uncalibrated checkpoint)
    }
    torch.save(dummy_state, ckpt_path)

    dummy_ex = _make_dummy_examples(4)
    manifest = {
        "n_splits": 5,
        "ood_doc_ids": [],
        "cv_folds": [
            {"train_doc_ids": [], "val_doc_ids": []},
            {"train_doc_ids": [], "val_doc_ids": [e.source_doc_id for e in dummy_ex]},
        ],
    }

    mock_eval_result = M2EvalResult(
        model_name="modernbert_text_only",
        split="cv",
        fold=1,
        seed=43,
        macro_f1=0.87,
        discrepancy_recall=0.83,
        per_class={},
        confusion_matrix=[],
        calibration_temperature=1.0,
    )

    with patch("clindoc.m2.recovery.ClinicalVerifier") as mock_model_cls, \
         patch("clindoc.m2.recovery.load_tokenizer") as mock_tok, \
         patch("clindoc.m2.recovery.evaluate_model_on_split") as mock_eval:

        mock_instance = MagicMock()
        mock_model_cls.return_value = mock_instance
        mock_eval.return_value = mock_eval_result

        recovered = recover_and_evaluate_checkpoint(
            checkpoint_path=ckpt_path,
            examples=dummy_ex,
            manifest=manifest,
        )

        assert recovered["calibration_temperature"] == 1.0
        _, kwargs = mock_eval.call_args
        assert kwargs["temperature"] == 1.0


def test_quarantined_checkpoints_remain_rejected():
    """Verify quarantined checkpoints and legacy AUX21 checkpoint remain quarantined."""
    assert "modernbert_full_fold0_seed42.pt" in QUARANTINED_CHECKPOINT_NAMES
    assert "ckpt_fold0_seed42_aux1_freeze0.pt" in QUARANTINED_CHECKPOINT_NAMES

    p_debug = Path("checkpoints/modernbert_full/modernbert_full_fold0_seed42.pt")
    p_aux21 = Path("checkpoints/full_model/ckpt_fold0_seed42_aux1_freeze0.pt")
    assert is_quarantined_checkpoint(p_debug) is True
    assert is_quarantined_checkpoint(p_aux21) is True

    # Quarantined results should be filtered out
    item_debug = {
        "model_name": "modernbert_full",
        "fold": 0,
        "seed": 42,
        "checkpoint_path": str(p_debug),
        "best_val_macro_f1": 0.1667,
    }
    assert is_quarantined_result(item_debug) is True


def test_persistence_preserves_existing_valid_recovered_results(tmp_path):
    """save_incremental_results preserves existing valid recovered results without dropping them."""
    results_file = tmp_path / "m2_train_results.json"

    # Step 1: Pre-populate results_file with valid recovered results (e.g. modernbert_text_only)
    initial_recovered = [
        {"model_name": "modernbert_text_only", "fold": 0, "seed": 42, "best_val_macro_f1": 0.85, "calibration_temperature": 1.1},
        {"model_name": "modernbert_text_only", "fold": 0, "seed": 43, "best_val_macro_f1": 0.86, "calibration_temperature": 1.2},
    ]
    save_incremental_results(results_file, initial_recovered)

    # Step 2: An incremental save of a new variant (e.g. modernbert_full)
    new_results = [
        {"model_name": "modernbert_full", "fold": 0, "seed": 43, "best_val_macro_f1": 0.91, "calibration_temperature": 0.95},
    ]
    save_incremental_results(results_file, new_results)

    with open(results_file, "r", encoding="utf-8") as f:
        saved_data = json.load(f)

    # Must contain both the initial recovered runs AND the new full run
    assert len(saved_data) == 3
    names_and_seeds = [(r["model_name"], r["fold"], r["seed"]) for r in saved_data]
    assert ("modernbert_text_only", 0, 42) in names_and_seeds
    assert ("modernbert_text_only", 0, 43) in names_and_seeds
    assert ("modernbert_full", 0, 43) in names_and_seeds
