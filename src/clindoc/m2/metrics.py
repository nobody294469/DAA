"""M2 evaluation metrics: per-class P/R/F1, macro-F1, DISCREPANCY recall,
confusion matrix, AUROC, Brier score, ECE."""
from __future__ import annotations
import math
from typing import Sequence, Any


LABEL_ORDER = ["SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE"]


def _confusion(y_true: list[int], y_pred: list[int]) -> list[list[int]]:
    n = len(LABEL_ORDER)
    cm = [[0] * n for _ in range(n)]
    for t, p in zip(y_true, y_pred):
        cm[t][p] += 1
    return cm


def per_class_metrics(y_true: list[int], y_pred: list[int]) -> dict:
    cm = _confusion(y_true, y_pred)
    results = {}
    for i, label in enumerate(LABEL_ORDER):
        tp = cm[i][i]
        fp = sum(cm[r][i] for r in range(len(LABEL_ORDER))) - tp
        fn = sum(cm[i][c] for c in range(len(LABEL_ORDER))) - tp
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        sup  = sum(1 for y in y_true if y == i)
        results[label] = {"precision": prec, "recall": rec, "f1": f1, "support": sup}
    return results


def macro_f1(y_true: list[int], y_pred: list[int]) -> float:
    pc = per_class_metrics(y_true, y_pred)
    return sum(v["f1"] for v in pc.values()) / len(LABEL_ORDER)


def slice_macro_f1(y_true: list[int], y_pred: list[int]) -> float:
    """Macro-F1 averaged only over classes represented in either y_true or y_pred."""
    present_classes = sorted(set(y_true) | set(y_pred))
    if not present_classes:
        return 0.0
    pc = per_class_metrics(y_true, y_pred)
    return sum(pc[LABEL_ORDER[c]]["f1"] for c in present_classes) / len(present_classes)


def discrepancy_recall(y_true: list[int], y_pred: list[int]) -> float:
    disc_idx = LABEL_ORDER.index("DISCREPANCY")
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == disc_idx and p == disc_idx)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == disc_idx and p != disc_idx)
    return tp / (tp + fn) if (tp + fn) > 0 else 0.0


def auroc_binary(y_true_binary: list[int], y_score: list[float]) -> float:
    """AUROC for binary (positive class vs rest) using trapezoidal rule."""
    pairs = sorted(zip(y_score, y_true_binary), key=lambda x: -x[0])
    n_pos = sum(y_true_binary)
    n_neg = len(y_true_binary) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    tp, fp = 0, 0
    roc = [(0.0, 0.0)]
    for _, label in pairs:
        if label == 1:
            tp += 1
        else:
            fp += 1
        roc.append((fp / n_neg, tp / n_pos))
    auc = sum((roc[i][0] - roc[i-1][0]) * (roc[i][1] + roc[i-1][1]) / 2
              for i in range(1, len(roc)))
    return round(auc, 4)


def brier_score(y_true: list[int], y_probs: list[list[float]]) -> float:
    """Multi-class Brier score."""
    n = len(y_true)
    total = 0.0
    K = len(LABEL_ORDER)
    for i, label in enumerate(y_true):
        for k in range(K):
            indicator = 1.0 if k == label else 0.0
            total += (y_probs[i][k] - indicator) ** 2
    return round(total / n, 4)


def ece(y_true: list[int], y_probs: list[list[float]], n_bins: int = 15) -> float:
    """Expected Calibration Error with equal-width bins on max-confidence."""
    confidences = [max(p) for p in y_probs]
    predictions = [p.index(max(p)) for p in y_probs]
    bin_size = 1.0 / n_bins
    total_ece = 0.0
    for b in range(n_bins):
        lo = b * bin_size
        hi = lo + bin_size
        idxs = [i for i, c in enumerate(confidences) if lo <= c < hi]
        if not idxs:
            continue
        avg_conf = sum(confidences[i] for i in idxs) / len(idxs)
        avg_acc  = sum(1 for i in idxs if predictions[i] == y_true[i]) / len(idxs)
        total_ece += abs(avg_conf - avg_acc) * len(idxs) / len(y_true)
    return round(total_ece, 4)


def reliability_data(y_true: list[int], y_probs: list[list[float]], n_bins: int = 15) -> dict:
    """Return per-bin avg confidence and accuracy for reliability diagram."""
    confidences = [max(p) for p in y_probs]
    predictions = [p.index(max(p)) for p in y_probs]
    bin_size = 1.0 / n_bins
    bins = []
    for b in range(n_bins):
        lo = b * bin_size
        hi = lo + bin_size
        idxs = [i for i, c in enumerate(confidences) if lo <= c < hi]
        if not idxs:
            bins.append({"lo": lo, "hi": hi, "count": 0, "avg_conf": 0.0, "avg_acc": 0.0})
            continue
        avg_conf = sum(confidences[i] for i in idxs) / len(idxs)
        avg_acc  = sum(1 for i in idxs if predictions[i] == y_true[i]) / len(idxs)
        bins.append({"lo": lo, "hi": hi, "count": len(idxs), "avg_conf": avg_conf, "avg_acc": avg_acc})
    return {"bins": bins}


def compute_risk_coverage(
    y_true: Sequence[Any],
    y_pred: Sequence[Any],
    confidences: Sequence[float],
) -> dict[str, list[float]]:
    """Compute selective-prediction risk-coverage curve.

    Coverage represents the fraction of examples retained at or above
    a confidence threshold.
    Risk represents the error rate among retained examples.

    Handles tied confidences by grouping examples with identical confidence
    into the same threshold step.
    """
    if y_true is None or y_pred is None or confidences is None:
        raise ValueError("y_true, y_pred, and confidences cannot be None")
    if len(y_true) != len(y_pred) or len(y_true) != len(confidences):
        raise ValueError(
            f"Length mismatch: len(y_true)={len(y_true)}, len(y_pred)={len(y_pred)}, len(confidences)={len(confidences)}"
        )

    n_total = len(y_true)
    if n_total == 0:
        return {
            "coverages": [],
            "risks": [],
            "thresholds": [],
            "coverage": [],
            "risk": [],
        }

    # Validate confidences
    float_confidences: list[float] = []
    for idx, c in enumerate(confidences):
        if c is None:
            raise ValueError(f"Confidence at index {idx} is None")
        c_float = float(c)
        if math.isnan(c_float):
            raise ValueError(f"Confidence at index {idx} is NaN")
        float_confidences.append(c_float)

    # Sort indices by confidence descending
    sorted_indices = sorted(range(n_total), key=lambda i: float_confidences[i], reverse=True)

    coverages: list[float] = []
    risks: list[float] = []
    thresholds: list[float] = []

    cum_count = 0
    cum_errors = 0

    idx = 0
    while idx < n_total:
        current_conf = float_confidences[sorted_indices[idx]]
        # Group all examples with identical confidence (tied confidences)
        group_count = 0
        group_errors = 0
        while idx < n_total and (
            float_confidences[sorted_indices[idx]] == current_conf
            or math.isclose(float_confidences[sorted_indices[idx]], current_conf, rel_tol=1e-9, abs_tol=1e-12)
        ):
            i = sorted_indices[idx]
            group_count += 1
            if y_true[i] != y_pred[i]:
                group_errors += 1
            idx += 1

        cum_count += group_count
        cum_errors += group_errors

        cov = cum_count / n_total
        risk = cum_errors / cum_count

        coverages.append(cov)
        risks.append(risk)
        thresholds.append(current_conf)

    return {
        "coverages": coverages,
        "risks": risks,
        "thresholds": thresholds,
        "coverage": coverages,
        "risk": risks,
    }


def compute_aurc(
    risk_coverage_or_coverages: dict[str, list[float]] | Sequence[tuple[float, float]] | Sequence[float],
    risks: Sequence[float] | None = None,
    round_digits: int | None = 4,
) -> float:
    """Compute Area Under the Risk-Coverage Curve (AURC) using trapezoidal integration.

    Accepts:
    1. A dictionary returned by compute_risk_coverage()
    2. Parallel sequences of (coverages, risks)
    3. A sequence of (coverage, risk) tuples
    """
    if risk_coverage_or_coverages is None:
        raise ValueError("risk_coverage_or_coverages cannot be None")

    covs: list[float] = []
    r_list: list[float] = []

    if isinstance(risk_coverage_or_coverages, dict):
        cov_data = risk_coverage_or_coverages.get("coverages", risk_coverage_or_coverages.get("coverage", []))
        risk_data = risk_coverage_or_coverages.get("risks", risk_coverage_or_coverages.get("risk", []))
        covs = [float(c) for c in cov_data]
        r_list = [float(r) for r in risk_data]
    elif risks is not None:
        covs = [float(c) for c in risk_coverage_or_coverages]
        r_list = [float(r) for r in risks]
    else:
        for item in risk_coverage_or_coverages:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                covs.append(float(item[0]))
                r_list.append(float(item[1]))
            else:
                raise ValueError("Must provide risks sequence or sequence of (coverage, risk) pairs")

    if len(covs) != len(r_list):
        raise ValueError(f"Length mismatch: len(coverages)={len(covs)}, len(risks)={len(r_list)}")

    if len(covs) == 0:
        return 0.0

    for c in covs:
        if c < 0.0 or c > 1.0:
            raise ValueError(f"Coverage value must be in [0, 1], got {c}")
    for r_val in r_list:
        if r_val < 0.0 or r_val > 1.0:
            raise ValueError(f"Risk value must be in [0, 1], got {r_val}")

    # Sort pairs by coverage ascending
    pairs = sorted(zip(covs, r_list), key=lambda p: p[0])
    sorted_covs = [p[0] for p in pairs]
    sorted_r = [p[1] for p in pairs]

    # Integrate from coverage 0.0 to 1.0
    # For coverage in [0, sorted_covs[0]], risk is constant at sorted_r[0]
    area = sorted_covs[0] * sorted_r[0]

    for i in range(1, len(sorted_covs)):
        dx = sorted_covs[i] - sorted_covs[i - 1]
        if dx > 0:
            area += dx * (sorted_r[i - 1] + sorted_r[i]) / 2.0

    if sorted_covs[-1] < 1.0:
        area += (1.0 - sorted_covs[-1]) * sorted_r[-1]

    if round_digits is not None:
        return round(area, round_digits)
    return area
