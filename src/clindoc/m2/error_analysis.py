"""error_analysis.py for M2 — breakdown failures by field_type, severity, etc."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from clindoc.m2.schema import M2Example, LABEL_ORDER

LABEL_TO_INT = {l: i for i, l in enumerate(LABEL_ORDER)}
INT_TO_LABEL = {i: l for l, i in LABEL_TO_INT.items()}


def analyze_errors(
    examples: list[M2Example],
    y_true: list[int],
    y_pred: list[int],
    max_examples_per_type: int = 5,
) -> dict:
    """Produce a structured error analysis report."""
    errors = [
        (examples[i], y_true[i], y_pred[i])
        for i in range(len(examples))
        if y_true[i] != y_pred[i]
    ]

    def _breakdown(key_fn):
        groups = defaultdict(lambda: {"correct": 0, "total": 0, "error_types": defaultdict(int)})
        for ex, yt, yp in zip(examples, y_true, y_pred):
            k = key_fn(ex)
            groups[k]["total"] += 1
            if yt == yp:
                groups[k]["correct"] += 1
            else:
                err_type = INT_TO_LABEL[yt] + "_predicted_as_" + INT_TO_LABEL[yp]
                groups[k]["error_types"][err_type] += 1
        return {k: dict(v, error_types=dict(v["error_types"])) for k, v in groups.items()}

    # Sample failure examples
    failure_examples = []
    seen_types: set = set()
    for ex, yt, yp in errors:
        err_key = (INT_TO_LABEL[yt], INT_TO_LABEL[yp])
        if err_key not in seen_types or sum(1 for e in failure_examples if e["true"] == INT_TO_LABEL[yt] and e["pred"] == INT_TO_LABEL[yp]) < max_examples_per_type:
            failure_examples.append({
                "doc_id": ex.source_doc_id,
                "claim": ex.claim[:200],
                "evidence": ex.evidence[:200],
                "field_type": ex.field_type,
                "severity": ex.severity,
                "true": INT_TO_LABEL[yt],
                "pred": INT_TO_LABEL[yp],
                "generation_method": ex.generation_method,
            })
            seen_types.add(err_key)

    return {
        "total_errors": len(errors),
        "total_examples": len(examples),
        "error_rate": round(len(errors) / max(len(examples), 1), 4),
        "by_severity": _breakdown(lambda e: e.severity),
        "by_field_type": _breakdown(lambda e: e.field_type),
        "by_claim_type": _breakdown(lambda e: e.claim_type),
        "by_doc_template": _breakdown(lambda e: e.doc_template),
        "failure_examples": failure_examples[:50],
    }
