"""Unit tests for M2 evaluation metrics."""
import pytest
from clindoc.m2.metrics import (
    macro_f1, discrepancy_recall, auroc_binary, brier_score, ece,
    per_class_metrics, reliability_data, LABEL_ORDER
)


def test_perfect_metrics():
    y_true = [0, 1, 2, 0, 1, 2]
    y_pred = [0, 1, 2, 0, 1, 2]
    f1 = macro_f1(y_true, y_pred)
    assert f1 == 1.0
    disc_rec = discrepancy_recall(y_true, y_pred)
    assert disc_rec == 1.0


def test_discrepancy_recall():
    disc_idx = LABEL_ORDER.index("DISCREPANCY")
    other_idx = LABEL_ORDER.index("SUPPORTED")
    # 2 true discrepancies: 1 predicted correctly, 1 missed
    y_true = [disc_idx, disc_idx, other_idx]
    y_pred = [disc_idx, other_idx, other_idx]
    assert discrepancy_recall(y_true, y_pred) == 0.5


def test_auroc_binary():
    # Perfect ranking
    y_true = [1, 1, 0, 0]
    scores = [0.9, 0.8, 0.2, 0.1]
    assert auroc_binary(y_true, scores) == 1.0

    # Inverted ranking
    scores_rev = [0.1, 0.2, 0.8, 0.9]
    assert auroc_binary(y_true, scores_rev) == 0.0


def test_brier_score():
    y_true = [0, 1]
    # Perfect probabilities
    y_probs_perfect = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert brier_score(y_true, y_probs_perfect) == 0.0


def test_ece_and_reliability():
    y_true = [0, 1, 2]
    y_probs = [[0.9, 0.05, 0.05], [0.1, 0.8, 0.1], [0.1, 0.1, 0.8]]
    e = ece(y_true, y_probs, n_bins=5)
    assert 0.0 <= e <= 1.0

    rel = reliability_data(y_true, y_probs, n_bins=5)
    assert "bins" in rel
    assert len(rel["bins"]) == 5
