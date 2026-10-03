"""Unit tests for M2 evaluation metrics."""
import pytest
from clindoc.m2.metrics import (
    macro_f1, discrepancy_recall, auroc_binary, brier_score, ece,
    per_class_metrics, reliability_data, LABEL_ORDER,
    compute_risk_coverage, compute_aurc
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


def test_risk_coverage_perfect_predictions():
    # 1. Perfect predictions: all correct
    y_true = [0, 1, 2, 0, 1]
    y_pred = [0, 1, 2, 0, 1]
    confidences = [0.95, 0.85, 0.75, 0.65, 0.55]
    curve = compute_risk_coverage(y_true, y_pred, confidences)
    assert curve["coverages"] == [0.2, 0.4, 0.6, 0.8, 1.0]
    assert curve["risks"] == [0.0, 0.0, 0.0, 0.0, 0.0]
    assert curve["thresholds"] == [0.95, 0.85, 0.75, 0.65, 0.55]
    aurc = compute_aurc(curve)
    assert aurc == 0.0


def test_risk_coverage_completely_incorrect():
    # 2. Completely incorrect predictions: all wrong
    y_true = [0, 1, 2]
    y_pred = [1, 2, 0]
    confidences = [0.9, 0.8, 0.7]
    curve = compute_risk_coverage(y_true, y_pred, confidences)
    assert curve["coverages"] == [1 / 3, 2 / 3, 1.0]
    assert curve["risks"] == [1.0, 1.0, 1.0]
    aurc = compute_aurc(curve)
    assert aurc == 1.0


def test_risk_coverage_mixed_predictions():
    # 3. Mixed predictions
    # 4 examples: 3 correct, 1 wrong at lower confidence
    y_true = ["SUPPORTED", "DISCREPANCY", "SUPPORTED", "INSUFFICIENT_EVIDENCE"]
    y_pred = ["SUPPORTED", "DISCREPANCY", "SUPPORTED", "SUPPORTED"]  # last is wrong
    confidences = [0.95, 0.85, 0.75, 0.60]
    curve = compute_risk_coverage(y_true, y_pred, confidences)
    assert curve["coverages"] == [0.25, 0.5, 0.75, 1.0]
    # First 3 retained are correct (risk=0), at full coverage risk=1/4=0.25
    assert curve["risks"] == [0.0, 0.0, 0.0, 0.25]
    aurc = compute_aurc(curve)
    assert 0.0 < aurc < 0.25


def test_risk_coverage_confidence_ordering():
    # 4. Confidence ordering: informative vs inverted confidences
    # Model A: well-ordered (high confidence = correct, low confidence = wrong)
    y_true = [1, 1, 0, 0]
    y_pred = [1, 1, 1, 1]  # 2 correct, 2 incorrect (50% accuracy)
    conf_well_ordered = [0.9, 0.8, 0.3, 0.2]
    curve_a = compute_risk_coverage(y_true, y_pred, conf_well_ordered)
    aurc_a = compute_aurc(curve_a)

    # Model B: inverted ordering (high confidence = wrong, low confidence = correct)
    conf_inverted = [0.2, 0.3, 0.8, 0.9]
    curve_b = compute_risk_coverage(y_true, y_pred, conf_inverted)
    aurc_b = compute_aurc(curve_b)

    # Well-ordered model must achieve significantly lower AURC than inverted
    assert aurc_a < aurc_b
    assert aurc_a < 0.5  # better than random / full error rate
    assert aurc_b > 0.5  # worse than random / full error rate


def test_risk_coverage_tied_confidences():
    # 5. Tied confidences
    # All confidences identical: should yield a single point at coverage=1.0
    y_true = [1, 1, 0, 1]
    y_pred = [1, 0, 0, 1]  # 1 error out of 4 (error rate = 0.25)
    conf_all_tied = [0.8, 0.8, 0.8, 0.8]
    curve_tied = compute_risk_coverage(y_true, y_pred, conf_all_tied)
    assert len(curve_tied["coverages"]) == 1
    assert curve_tied["coverages"] == [1.0]
    assert curve_tied["risks"] == [0.25]
    assert curve_tied["thresholds"] == [0.8]
    assert compute_aurc(curve_tied) == 0.25

    # Grouped ties: two distinct tiers with ties within tiers
    conf_grouped = [0.9, 0.9, 0.5, 0.5]
    # At 0.9: indices 0, 1 are both correct
    # At 0.5: index 2 is wrong, index 3 is correct
    y_t = [0, 0, 1, 0]
    y_p = [0, 0, 0, 0]
    curve_grp = compute_risk_coverage(y_t, y_p, conf_grouped)
    assert curve_grp["coverages"] == [0.5, 1.0]
    assert curve_grp["risks"] == [0.0, 0.25]
    assert curve_grp["thresholds"] == [0.9, 0.5]


def test_risk_coverage_empty_and_invalid_inputs():
    # 6. Empty and invalid inputs
    # Empty inputs
    empty_curve = compute_risk_coverage([], [], [])
    assert empty_curve["coverages"] == []
    assert empty_curve["risks"] == []
    assert compute_aurc(empty_curve) == 0.0
    assert compute_aurc([]) == 0.0

    # Length mismatches
    with pytest.raises(ValueError, match="Length mismatch"):
        compute_risk_coverage([1, 0], [1], [0.9, 0.8])
    with pytest.raises(ValueError, match="Length mismatch"):
        compute_risk_coverage([1, 0], [1, 0], [0.9])

    # None inputs
    with pytest.raises(ValueError, match="cannot be None"):
        compute_risk_coverage(None, [1], [0.9])
    with pytest.raises(ValueError, match="Confidence at index"):
        compute_risk_coverage([1], [1], [None])
    with pytest.raises(ValueError, match="Confidence at index"):
        compute_risk_coverage([1], [1], [float("nan")])

    # Invalid curve points in compute_aurc
    with pytest.raises(ValueError, match="Length mismatch"):
        compute_aurc([0.5, 1.0], [0.1])
    with pytest.raises(ValueError, match="Coverage value must be in"):
        compute_aurc([-0.1, 1.0], [0.1, 0.2])
    with pytest.raises(ValueError, match="Risk value must be in"):
        compute_aurc([0.5, 1.0], [0.1, 1.5])


def test_aurc_consistency():
    # 7. AURC consistency
    # Constant risk R gives AURC = R
    covs = [0.25, 0.5, 0.75, 1.0]
    risks_const = [0.35, 0.35, 0.35, 0.35]
    assert compute_aurc(covs, risks_const) == 0.35

    # Multiple input formats must yield the exact same result
    dict_input = {"coverages": covs, "risks": [0.1, 0.2, 0.3, 0.4]}
    pairs_input = list(zip(covs, [0.1, 0.2, 0.3, 0.4]))
    aurc_from_dict = compute_aurc(dict_input)
    aurc_from_pairs = compute_aurc(pairs_input)
    aurc_from_args = compute_aurc(covs, [0.1, 0.2, 0.3, 0.4])
    assert aurc_from_dict == aurc_from_pairs == aurc_from_args

    # Exact trapezoidal calculation check
    # Segment 1: [0, 0.25] -> width 0.25, risk 0.1 -> 0.025
    # Segment 2: [0.25, 0.5] -> width 0.25, avg risk 0.15 -> 0.0375
    # Segment 3: [0.5, 0.75] -> width 0.25, avg risk 0.25 -> 0.0625
    # Segment 4: [0.75, 1.0] -> width 0.25, avg risk 0.35 -> 0.0875
    # Total = 0.025 + 0.0375 + 0.0625 + 0.0875 = 0.2125
    assert aurc_from_dict == 0.2125

