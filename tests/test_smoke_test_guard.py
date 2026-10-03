"""test_smoke_test_guard.py — Focused tests proving missing checkpoint causes clean failure without training.

Verifies:
  1. Default execution (`python eval/smoke_test_modernbert_full.py 0 42`) exits non-zero if checkpoint is absent.
  2. "checkpoint not found; smoke verification cannot continue" is printed.
  3. train_one_fold is strictly NOT invoked when in verification mode.
  4. Explicit `--train` is required to allow training when checkpoint is missing.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from eval.smoke_test_modernbert_full import run_smoke_test, main


def test_missing_checkpoint_aborts_without_training(capsys):
    """Missing checkpoint in default verification mode must exit non-zero and never train."""
    nonexistent = Path("data/m2_results/checkpoints/smoke_test/nonexistent_model_fold99_seed999.pt")
    if nonexistent.exists():
        nonexistent.unlink()

    with patch("eval.smoke_test_modernbert_full.train_one_fold") as mock_train:
        with pytest.raises(SystemExit) as exc_info:
            run_smoke_test(
                fold_idx=99,
                seed=999,
                checkpoint_path=nonexistent,
                allow_train=False,
            )

        # 1. Must exit non-zero
        assert exc_info.value.code != 0

        # 2. train_one_fold must NOT be called
        mock_train.assert_not_called()

    # 3. Required error message must be printed
    captured = capsys.readouterr()
    assert "checkpoint not found; smoke verification cannot continue" in captured.out


def test_cli_default_args_abort_on_missing_checkpoint(capsys):
    """CLI default invocation 'python eval/smoke_test_modernbert_full.py 99 999' aborts without training."""
    with patch("eval.smoke_test_modernbert_full.train_one_fold") as mock_train:
        with pytest.raises(SystemExit) as exc_info:
            main(["99", "999"])

        assert exc_info.value.code != 0
        mock_train.assert_not_called()

    captured = capsys.readouterr()
    assert "checkpoint not found; smoke verification cannot continue" in captured.out


def test_missing_explicit_checkpoint_arg_aborts(capsys):
    """Explicit nonexistent --checkpoint argument aborts without training."""
    with patch("eval.smoke_test_modernbert_full.train_one_fold") as mock_train:
        with pytest.raises(SystemExit) as exc_info:
            main(["--checkpoint", "nonexistent/custom_path.pt"])

        assert exc_info.value.code != 0
        mock_train.assert_not_called()

    captured = capsys.readouterr()
    assert "checkpoint not found; smoke verification cannot continue" in captured.out


def test_allow_train_flag_enables_training_when_missing():
    """When allow_train=True, missing checkpoint proceeds to training."""
    nonexistent = Path("data/m2_results/checkpoints/smoke_test/nonexistent_model_fold99_seed999.pt")

    with patch("eval.smoke_test_modernbert_full.load_examples", return_value=[]), \
         patch("eval.smoke_test_modernbert_full.get_fold_examples", return_value=([], [])), \
         patch("eval.smoke_test_modernbert_full.get_ood_examples", return_value=[]), \
         patch("eval.smoke_test_modernbert_full.load_calibration_examples", return_value=[]), \
         patch("eval.smoke_test_modernbert_full.open", MagicMock()), \
         patch("json.load", return_value={"ood_doc_ids": []}), \
         patch("eval.smoke_test_modernbert_full.train_one_fold") as mock_train:

        # Mock train returning dummy checkpoint path and abort afterwards to avoid full eval
        mock_train.side_effect = RuntimeError("Training invoked as expected")

        with pytest.raises(RuntimeError, match="Training invoked as expected"):
            run_smoke_test(
                fold_idx=99,
                seed=999,
                checkpoint_path=nonexistent,
                allow_train=True,
            )

        mock_train.assert_called_once()
