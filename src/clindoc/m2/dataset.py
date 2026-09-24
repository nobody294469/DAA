"""M2 dataset loading, validation, and statistics computation."""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path

from clindoc.m2.schema import M2Example, M2DatasetMetadata, VerificationLabel


def load_examples(jsonl_path: Path) -> list[M2Example]:
    examples = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(M2Example.model_validate(json.loads(line)))
    return examples


def save_examples(examples: list[M2Example], jsonl_path: Path) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(ex.model_dump_json() + "\n")


def compute_metadata(
    examples: list[M2Example],
    ood_doc_ids: list[str],
    cv_doc_ids: list[str],
) -> M2DatasetMetadata:
    def cross_count(examples, key_fn):
        result: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for ex in examples:
            result[key_fn(ex)][ex.label.value] += 1
        return {k: dict(v) for k, v in result.items()}

    label_counts = defaultdict(int)
    for ex in examples:
        label_counts[ex.label.value] += 1
    total = len(examples)
    label_pcts = {k: round(100 * v / total, 2) for k, v in label_counts.items()} if total > 0 else {}

    return M2DatasetMetadata(
        total_examples=total,
        label_counts=dict(label_counts),
        label_percentages=label_pcts,
        by_severity=cross_count(examples, lambda e: e.severity),
        by_field_type=cross_count(examples, lambda e: e.field_type),
        by_doc_template=cross_count(examples, lambda e: e.doc_template),
        by_generation_method=cross_count(examples, lambda e: e.generation_method),
        by_claim_type=cross_count(examples, lambda e: e.claim_type),
        ood_doc_ids=sorted(ood_doc_ids),
        cv_doc_ids=sorted(cv_doc_ids),
    )


def print_dataset_summary(meta: M2DatasetMetadata) -> None:
    print(f"\nM2 Dataset Summary ({meta.total_examples} examples)")
    print("-" * 50)
    for label, cnt in sorted(meta.label_counts.items()):
        pct = meta.label_percentages.get(label, 0)
        print(f"  {label}: {cnt} ({pct:.1f}%)")
    print("  By severity:")
    for sev, counts in sorted(meta.by_severity.items()):
        print(f"    {sev}: {counts}")
    print(f"  OOD docs: {len(meta.ood_doc_ids)}, CV docs: {len(meta.cv_doc_ids)}")
