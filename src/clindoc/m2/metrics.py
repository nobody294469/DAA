"""M2 evaluation metrics: per-class P/R/F1, macro-F1, DISCREPANCY recall,
confusion matrix, AUROC, Brier score, ECE."""
from __future__ import annotations
import math
from typing import Sequence


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
