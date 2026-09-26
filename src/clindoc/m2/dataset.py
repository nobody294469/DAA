"""M2 dataset loading, validation, and statistics computation."""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path

from clindoc.m2.schema import M2Example, M2DatasetMetadata, VerificationLabel
from typing import Any, Union
import yaml


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
    group_ids = set()
    contradiction_counts = defaultdict(int)
    for ex in examples:
        label_counts[ex.label.value] += 1
        if ex.group_id:
            group_ids.add(ex.group_id)
        if ex.contradiction_type:
            contradiction_counts[ex.contradiction_type] += 1

    total = len(examples)
    label_pcts = {k: round(100 * v / total, 2) for k, v in label_counts.items()} if total > 0 else {}

    try:
        from clindoc.m2.context import load_context_config
        ctx_cfg = load_context_config()
    except Exception:
        ctx_cfg = {}

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
        dataset_version="2.0-phase1-canonical",
        total_groups=len(group_ids),
        by_contradiction_type=dict(contradiction_counts),
        context_window_config=ctx_cfg,
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


class ClassBalanceError(AssertionError):
    """Raised when dataset class balance fails to meet DEC-011 floors."""
    pass


def assert_class_balance(
    metadata: Union[M2DatasetMetadata, dict, str, Path],
    config_path: Union[str, Path, None] = None,
) -> bool:
    """Enforce DEC-011 class-balance floors on M2 dataset metadata.

    Floors enforced per field type:
        SUPPORTED: count >= max(supported_ratio * N_field, supported_min_count)
        DISCREPANCY: count >= max(discrepancy_ratio * N_field, discrepancy_min_count)
        INSUFFICIENT_EVIDENCE: count >= insufficient_evidence_min_count ONLY where
                              semantically applicable.

    Thresholds and field applicability are loaded from configs/class_balance_floor.yaml.
    Fails loudly with ClassBalanceError showing comprehensive diagnostics.
    """
    if isinstance(metadata, (str, Path)):
        meta_path = Path(metadata)
        if not meta_path.exists():
            raise FileNotFoundError(f"Metadata file not found: {meta_path}")
        with open(meta_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        by_field_type = raw_data.get("by_field_type", raw_data)
    elif hasattr(metadata, "by_field_type"):
        by_field_type = metadata.by_field_type
    elif isinstance(metadata, dict):
        by_field_type = metadata.get("by_field_type", metadata)
    else:
        raise TypeError(f"Unsupported metadata type: {type(metadata)}")

    if config_path is None:
        resolved_config_path = Path("configs/class_balance_floor.yaml")
    else:
        resolved_config_path = Path(config_path)

    if not resolved_config_path.exists():
        raise FileNotFoundError(f"Class balance configuration file not found: {resolved_config_path}")

    with open(resolved_config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    floors = config.get("floors", {})
    supported_ratio = float(floors.get("supported_ratio", 0.10))
    supported_min_count = int(floors.get("supported_min_count", 30))
    discrepancy_ratio = float(floors.get("discrepancy_ratio", 0.10))
    discrepancy_min_count = int(floors.get("discrepancy_min_count", 30))
    ie_min_count = int(floors.get("insufficient_evidence_min_count", 15))

    active_fields = config.get("active_fields", {})
    expected_fields = active_fields.get("tier_1_learned_fields", [
        "lab.value", "lab.flag", "med.name", "med.dose", "med.frequency", "med.route"
    ])
    ie_applicability = config.get("insufficient_evidence_applicability", {})

    violations: list[str] = []

    for field_name in expected_fields:
        if field_name not in by_field_type:
            violations.append(
                f"- [MISSING_FIELD] Field type '{field_name}' is required by configuration "
                f"(tier_1_learned_fields) but not present in dataset metadata."
            )
            continue

        field_counts = by_field_type[field_name]
        n_field = sum(field_counts.values())

        if n_field == 0:
            violations.append(
                f"- [EMPTY_FIELD] Field type '{field_name}' is present in metadata but has 0 total examples."
            )
            continue

        # Check SUPPORTED
        supp_count = field_counts.get("SUPPORTED", 0)
        supp_floor = max(supported_ratio * n_field, float(supported_min_count))
        if supp_count < supp_floor:
            violations.append(
                f"- [FLOOR_VIOLATION] Field '{field_name}' - class 'SUPPORTED':\n"
                f"    Total field examples: {n_field}\n"
                f"    Class counts: {dict(field_counts)}\n"
                f"    Required minimum: {supp_floor:.1f} "
                f"(max({supported_ratio*100:.1f}% of {n_field} = {supported_ratio*n_field:.1f}, {supported_min_count}))\n"
                f"    Actual count: {supp_count}"
            )

        # Check DISCREPANCY
        disc_count = field_counts.get("DISCREPANCY", 0)
        disc_floor = max(discrepancy_ratio * n_field, float(discrepancy_min_count))
        if disc_count < disc_floor:
            violations.append(
                f"- [FLOOR_VIOLATION] Field '{field_name}' - class 'DISCREPANCY':\n"
                f"    Total field examples: {n_field}\n"
                f"    Class counts: {dict(field_counts)}\n"
                f"    Required minimum: {disc_floor:.1f} "
                f"(max({discrepancy_ratio*100:.1f}% of {n_field} = {discrepancy_ratio*n_field:.1f}, {discrepancy_min_count}))\n"
                f"    Actual count: {disc_count}"
            )

        # Check INSUFFICIENT_EVIDENCE
        is_ie_applicable = bool(ie_applicability.get(field_name, False))
        if is_ie_applicable:
            ie_count = field_counts.get("INSUFFICIENT_EVIDENCE", 0)
            ie_floor = ie_min_count
            if ie_count < ie_floor:
                violations.append(
                    f"- [FLOOR_VIOLATION] Field '{field_name}' - class 'INSUFFICIENT_EVIDENCE':\n"
                    f"    Total field examples: {n_field}\n"
                    f"    Class counts: {dict(field_counts)}\n"
                    f"    Required minimum: {ie_floor} (INSUFFICIENT_EVIDENCE is semantically applicable)\n"
                    f"    Actual count: {ie_count}"
                )

    if violations:
        diag_lines = [
            f"Dataset class balance assertion failed (DEC-011)!",
            f"Total violations: {len(violations)}",
            *violations,
        ]
        raise ClassBalanceError("\n".join(diag_lines))

    return True
