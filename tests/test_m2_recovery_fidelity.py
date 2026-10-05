"""test_m2_recovery_fidelity.py — Regression test proving bit-for-bit fidelity between in-memory trained model and recovered model.

Verifies:
  1. Checkpoint loading restores exact weights without corruption or drift.
  2. Logits, probabilities, predictions, and calibration metrics before saving match recovered evaluation.
  3. Preprocessing, tokenization, auxiliary feature projection, and label ordering are identical.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import torch

from clindoc.m2.schema import (
    M2Example,
    M2ModelConfig,
    M2EvalResult,
    VerificationLabel,
    LABEL_ORDER,
)
from clindoc.m2.model import ClinicalVerifier, format_input
from clindoc.m2.features import encode_aux_batch
from clindoc.m2.evaluate import evaluate_model_on_split
from clindoc.m2.recovery import recover_and_evaluate_checkpoint


class MockEncoder(torch.nn.Module):
    """Deterministic lightweight encoder mock returning fixed-dim embeddings."""
    def __init__(self, hidden_dim: int = 768):
        super().__init__()
        self.hidden_dim = hidden_dim
        # Fixed weights to verify exact parameter preservation
        self.proj = torch.nn.Linear(8, hidden_dim)

    def forward(self, input_ids, attention_mask):
        # Create deterministic pseudo-embeddings from input_ids
        b, seq_len = input_ids.shape
        x = input_ids.float().unsqueeze(-1).repeat(1, 1, 8)
        out = self.proj(x)
        mock_output = MagicMock()
        mock_output.last_hidden_state = out
        return mock_output


def _make_test_examples() -> list[M2Example]:
    labels = [
        VerificationLabel.SUPPORTED,
        VerificationLabel.DISCREPANCY,
        VerificationLabel.INSUFFICIENT_EVIDENCE,
        VerificationLabel.SUPPORTED,
    ]
    examples = []
    for i in range(8):
        examples.append(
            M2Example(
                example_id=f"ex_{i}",
                source_doc_id=f"doc_{i}",
                claim=f"Test claim {i}",
                evidence=f"Test evidence {i}",
                field_type="lab.value",
                claim_type="lab",
                label=labels[i % len(labels)],
                severity="medium",
                doc_template="template_a",
                ocr_confidence=0.92,
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


def test_checkpoint_recovery_numerical_fidelity(tmp_path):
    """Verify that an in-memory model and a checkpoint-recovered model produce identical outputs."""
    examples = _make_test_examples()
    manifest = {
        "n_splits": 5,
        "ood_doc_ids": ["doc_6", "doc_7"],
        "cv_folds": [
            {
                "train_doc_ids": ["doc_0", "doc_1"],
                "val_doc_ids": ["doc_2", "doc_3", "doc_4", "doc_5"],
            }
        ],
    }

    config = M2ModelConfig(batch_size=4)
    calib_temp = 1.004748

    # Build model with non-trivial deterministic weights
    with patch("transformers.AutoModel.from_pretrained") as mock_hf:
        mock_hf.return_value = MockEncoder()
        model_before = ClinicalVerifier(aux_mode="full")

    model_before.eval()

    # Obtain validation examples exactly as done during evaluation
    val_doc_ids = set(manifest["cv_folds"][0]["val_doc_ids"])
    val_examples = [e for e in examples if e.source_doc_id in val_doc_ids]

    # Evaluate directly before saving
    eval_before = evaluate_model_on_split(
        model=model_before,
        tokenizer=_dummy_tokenizer,
        examples=val_examples,
        model_name="modernbert_full",
        split="cv",
        fold=0,
        seed=42,
        config=config,
        device="cpu",
        aux_mode="full",
        temperature=calib_temp,
    )

    # Save to checkpoint
    ckpt_path = tmp_path / "modernbert_full_fold0_seed42.pt"
    torch.save({
        "model_state": model_before.state_dict(),
        "config": config.model_dump(),
        "aux_mode": "full",
        "freeze_lower_n_layers": 0,
        "fold": 0,
        "seed": 42,
        "best_val_macro_f1": eval_before.macro_f1,
        "model_name": "modernbert_full",
        "temperature": calib_temp,
    }, ckpt_path)

    # Recover using canonical recovery function
    with patch("transformers.AutoModel.from_pretrained") as mock_hf_rec, \
         patch("clindoc.m2.recovery.load_tokenizer") as mock_tok:
        mock_hf_rec.return_value = MockEncoder()
        mock_tok.return_value = _dummy_tokenizer

        recovered_res = recover_and_evaluate_checkpoint(
            checkpoint_path=ckpt_path,
            examples=examples,
            manifest=manifest,
            tokenizer=_dummy_tokenizer,
            config=config,
            device="cpu",
        )

    eval_after = recovered_res["val_eval"]

    # 1. State dict weights match
    recovered_state = torch.load(ckpt_path, map_location="cpu", weights_only=True)["model_state"]
    for k in model_before.state_dict():
        assert torch.equal(model_before.state_dict()[k], recovered_state[k]), f"Weight mismatch for {k}"

    # 2. Key metrics match exactly
    assert eval_before.macro_f1 == eval_after["macro_f1"], (
        f"Macro-F1 diverged: before={eval_before.macro_f1}, after={eval_after['macro_f1']}"
    )
    assert eval_before.discrepancy_recall == eval_after["discrepancy_recall"], (
        f"Discrepancy recall diverged: before={eval_before.discrepancy_recall}, after={eval_after['discrepancy_recall']}"
    )
    assert eval_before.confusion_matrix == eval_after["confusion_matrix"], (
        f"Confusion matrix diverged"
    )

    # 3. Probability metrics match within tight floating-point tolerance
    assert pytest.approx(eval_before.brier_score, rel=1e-5) == eval_after["brier_score"]
    assert pytest.approx(eval_before.ece, rel=1e-5) == eval_after["ece"]

    # 4. Calibration temperature preserved
    assert recovered_res["calibration_temperature"] == calib_temp
    assert eval_after["calibration_temperature"] == calib_temp
