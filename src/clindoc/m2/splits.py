"""Document-level train/val/test/calibration split construction for M2.

Rules & Invariants (Phase 0 / Phase 1 Task 4, DEC-015, DEC-017, AUDIT-05):
- Fundamental split unit is DOCUMENT, not example.
- All examples belonging to one document remain in the same partition.
- OOD held-out pool: 34 documents (17 lab, 12 med, 5 narrative) -> IMMUTABLE, never used in CV.
- 86 CV pool is partitioned into:
    TRAIN (~60%, 52 documents)
    CALIBRATION (~20%, 17 documents)
    VALIDATION (~20%, 17 documents)
- Zero document, group_id, or patient_id leakage across boundaries.
- GroupKFold 5-fold cross-validation functionality preserved for experimental plan.
"""
from __future__ import annotations
import json
import random
from pathlib import Path
from collections import defaultdict
from typing import Any, Optional

import yaml

from clindoc.m2.schema import (
    M2Example, OOD_MED_TEMPLATE, OOD_LAB_TEMPLATE, OOD_NARRATIVE_TEMPLATES,
)

# Standard template allocation targets for the canonical 86 CV documents:
# (train_count, calibration_count, validation_count)
CANONICAL_TEMPLATE_ALLOCATIONS: dict[str, tuple[int, int, int]] = {
    "template_b": (10, 3, 4),
    "template_a": (10, 3, 3),
    "clinic_rx_pad": (8, 2, 3),
    "discharge_summary": (8, 3, 2),
    "pharmacy_label": (7, 3, 2),
    "narrative_emergency_observation_note_v1": (3, 1, 1),
    "narrative_inpatient_discharge_summary_v1": (3, 1, 1),
    "narrative_surgical_discharge_summary_v1": (3, 1, 1),
}


def load_split_config(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load split configuration from YAML."""
    if config_path is None:
        config_path = Path("configs/split_config.yaml")
    p = Path(config_path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {
        "proportions": {"train": 0.60, "calibration": 0.20, "validation": 0.20},
        "random_seed": 42,
    }


def build_3way_cv_split(
    cv_doc_ids: list[str],
    doc_template_map: dict[str, str],
    seed: int = 42,
    train_ratio: float = 0.60,
    calibration_ratio: float = 0.20,
    validation_ratio: float = 0.20,
) -> dict[str, list[str]]:
    """Partition CV documents into Train, Calibration, and Validation.

    Performs template-stratified deterministic allocation.
    Returns dict with keys: 'train_doc_ids', 'calibration_doc_ids', 'validation_doc_ids'.
    """
    rng = random.Random(seed)

    # Group documents by template
    template_docs: dict[str, list[str]] = defaultdict(list)
    for doc_id in sorted(cv_doc_ids):
        tmpl = doc_template_map.get(doc_id, "unknown")
        template_docs[tmpl].append(doc_id)

    train_docs: list[str] = []
    calib_docs: list[str] = []
    val_docs: list[str] = []

    # If matching the canonical 86-document CV template structure, use canonical allocation
    is_canonical_cv_pool = (
        len(cv_doc_ids) == 86 and
        set(template_docs.keys()) == set(CANONICAL_TEMPLATE_ALLOCATIONS.keys())
    )

    for tmpl in sorted(template_docs.keys()):
        docs = list(template_docs[tmpl])
        rng.shuffle(docs)
        n_docs = len(docs)

        if is_canonical_cv_pool and tmpl in CANONICAL_TEMPLATE_ALLOCATIONS:
            n_tr, n_cal, n_val = CANONICAL_TEMPLATE_ALLOCATIONS[tmpl]
        else:
            # Proportional fallback
            n_tr = int(round(n_docs * train_ratio))
            n_cal = int(round(n_docs * calibration_ratio))
            # Ensure at least 1 in calibration and validation if template has >= 3 docs
            if n_docs >= 3:
                n_cal = max(1, n_cal)
                n_val = max(1, n_docs - n_tr - n_cal)
                n_tr = n_docs - n_cal - n_val
            else:
                n_val = n_docs - n_tr - n_cal

        train_docs.extend(docs[:n_tr])
        calib_docs.extend(docs[n_tr:n_tr + n_cal])
        val_docs.extend(docs[n_tr + n_cal:])

    return {
        "train_doc_ids": sorted(train_docs),
        "calibration_doc_ids": sorted(calib_docs),
        "validation_doc_ids": sorted(val_docs),
    }


def build_splits(
    examples: list[M2Example],
    n_splits: int = 5,
    seed: int = 42,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build document-level canonical 3-way split, GroupKFold splits, and OOD holdout.

    Returns a comprehensive manifest dict.
    """
    config = load_split_config(config_path)
    proportions = config.get("proportions", {"train": 0.60, "calibration": 0.20, "validation": 0.20})
    split_version = config.get("split_policy", {}).get("version", "1.0-canonical-3way")

    # Separate OOD documents and map doc templates
    ood_doc_ids_set: set[str] = set()
    cv_doc_ids_set: set[str] = set()
    doc_template_map: dict[str, str] = {}
    doc_family_map: dict[str, str] = {}

    for ex in examples:
        doc_id = ex.source_doc_id
        doc_template_map[doc_id] = ex.doc_template

        # Document family determination
        if ex.doc_template.startswith("narrative_"):
            doc_family_map[doc_id] = "narrative"
        elif ex.claim_type == "lab" or ex.doc_template.startswith("template_"):
            doc_family_map[doc_id] = "lab"
        else:
            doc_family_map[doc_id] = "medication"

        is_ood = (
            (ex.claim_type == "med" and ex.doc_template == OOD_MED_TEMPLATE) or
            (ex.claim_type == "lab" and ex.doc_template == OOD_LAB_TEMPLATE) or
            (ex.doc_template in OOD_NARRATIVE_TEMPLATES) or
            (ex.split == "ood")
        )
        if is_ood:
            ood_doc_ids_set.add(doc_id)
        else:
            cv_doc_ids_set.add(doc_id)

    cv_doc_ids = sorted(cv_doc_ids_set)
    ood_doc_ids = sorted(ood_doc_ids_set)

    # 1. Canonical 3-way split on CV pool
    threeway = build_3way_cv_split(
        cv_doc_ids=cv_doc_ids,
        doc_template_map=doc_template_map,
        seed=seed,
        train_ratio=proportions.get("train", 0.60),
        calibration_ratio=proportions.get("calibration", 0.20),
        validation_ratio=proportions.get("validation", 0.20),
    )
    train_doc_ids = threeway["train_doc_ids"]
    calibration_doc_ids = threeway["calibration_doc_ids"]
    validation_doc_ids = threeway["validation_doc_ids"]

    # 2. GroupKFold 5-fold cross-validation on CV pool (backward compatibility)
    rng_cv = random.Random(seed)
    shuffled_cv = list(cv_doc_ids)
    rng_cv.shuffle(shuffled_cv)

    folds: list[dict[str, list[str]]] = []
    fold_size = len(shuffled_cv) // n_splits
    for fold_idx in range(n_splits):
        val_start = fold_idx * fold_size
        val_end = val_start + fold_size if fold_idx < n_splits - 1 else len(shuffled_cv)
        val_docs = shuffled_cv[val_start:val_end]
        tr_docs = [d for d in shuffled_cv if d not in val_docs]
        folds.append({"train_doc_ids": sorted(tr_docs), "val_doc_ids": sorted(val_docs)})

    # 3. Document-to-partition mapping
    doc_to_partition: dict[str, str] = {}
    for d in train_doc_ids:
        doc_to_partition[d] = "train"
    for d in calibration_doc_ids:
        doc_to_partition[d] = "calibration"
    for d in validation_doc_ids:
        doc_to_partition[d] = "validation"
    for d in ood_doc_ids:
        doc_to_partition[d] = "ood"

    # 4. Aggregations & Audits
    example_counts = {"train": 0, "calibration": 0, "validation": 0, "ood": 0, "total": len(examples)}
    by_family: dict[str, dict[str, int]] = {p: defaultdict(int) for p in ["train", "calibration", "validation", "ood"]}
    by_template: dict[str, dict[str, int]] = {p: defaultdict(int) for p in ["train", "calibration", "validation", "ood"]}
    by_field_type: dict[str, dict[str, int]] = {p: defaultdict(int) for p in ["train", "calibration", "validation", "ood"]}
    by_label: dict[str, dict[str, int]] = {p: defaultdict(int) for p in ["train", "calibration", "validation", "ood"]}
    class_by_field: dict[str, dict[str, dict[str, int]]] = {
        p: defaultdict(lambda: defaultdict(int)) for p in ["train", "calibration", "validation", "ood"]
    }

    # Group and patient leakage trackers
    group_to_partition: dict[str, str] = {}
    group_leakage: list[dict[str, str]] = []

    for ex in examples:
        partition = doc_to_partition[ex.source_doc_id]
        example_counts[partition] += 1
        fam = doc_family_map[ex.source_doc_id]
        by_family[partition][fam] += 1
        by_template[partition][ex.doc_template] += 1
        by_field_type[partition][ex.field_type] += 1
        by_label[partition][ex.label.value] += 1
        class_by_field[partition][ex.field_type][ex.label.value] += 1

        # Check group leakage
        gid = ex.group_id
        if gid:
            if gid in group_to_partition and group_to_partition[gid] != partition:
                group_leakage.append({
                    "group_id": gid,
                    "first_partition": group_to_partition[gid],
                    "second_partition": partition,
                })
            else:
                group_to_partition[gid] = partition

    # Document family counts (documents, not examples)
    doc_family_counts: dict[str, dict[str, int]] = {
        "train": defaultdict(int),
        "calibration": defaultdict(int),
        "validation": defaultdict(int),
        "ood": defaultdict(int),
    }
    for doc_id, part in doc_to_partition.items():
        doc_family_counts[part][doc_family_map[doc_id]] += 1

    manifest = {
        "split_version": split_version,
        "algorithm": "stratified_document_family_and_template",
        "seed": seed,
        "document_counts": {
            "train": len(train_doc_ids),
            "calibration": len(calibration_doc_ids),
            "validation": len(validation_doc_ids),
            "ood": len(ood_doc_ids),
            "cv_total": len(cv_doc_ids),
            "total": len(train_doc_ids) + len(calibration_doc_ids) + len(validation_doc_ids) + len(ood_doc_ids),
        },
        "example_counts": example_counts,
        "train_doc_ids": train_doc_ids,
        "calibration_doc_ids": calibration_doc_ids,
        "validation_doc_ids": validation_doc_ids,
        "ood_doc_ids": ood_doc_ids,
        "cv_doc_ids": cv_doc_ids,
        "cv_folds": folds,
        "n_splits": n_splits,
        "doc_family_counts": {p: dict(c) for p, c in doc_family_counts.items()},
        "example_family_counts": {p: dict(c) for p, c in by_family.items()},
        "template_counts": {p: dict(c) for p, c in by_template.items()},
        "field_counts": {p: dict(c) for p, c in by_field_type.items()},
        "label_counts": {p: dict(c) for p, c in by_label.items()},
        "class_by_field": {p: {k: dict(v) for k, v in d.items()} for p, d in class_by_field.items()},
        "audits": {
            "document_overlap": {
                "train_calib": len(set(train_doc_ids) & set(calibration_doc_ids)),
                "train_val": len(set(train_doc_ids) & set(validation_doc_ids)),
                "calib_val": len(set(calibration_doc_ids) & set(validation_doc_ids)),
                "train_ood": len(set(train_doc_ids) & set(ood_doc_ids)),
                "calib_ood": len(set(calibration_doc_ids) & set(ood_doc_ids)),
                "val_ood": len(set(validation_doc_ids) & set(ood_doc_ids)),
            },
            "group_leakage_count": len(group_leakage),
            "ood_contamination": (
                len(set(train_doc_ids) & set(ood_doc_ids)) +
                len(set(calibration_doc_ids) & set(ood_doc_ids)) +
                len(set(validation_doc_ids) & set(ood_doc_ids))
            ),
        },
    }

    validate_no_leakage(manifest)
    return manifest


def save_split_manifest(manifest: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


# ─── CALIBRATION SAFETY & PARTITION LOADING API ──────────────────────────────

def load_train_examples(examples: list[M2Example], manifest: dict) -> list[M2Example]:
    """Load only examples belonging to the TRAIN partition."""
    doc_ids = set(manifest.get("train_doc_ids", []))
    if not doc_ids:
        raise ValueError("Manifest does not contain 'train_doc_ids'")
    return [ex for ex in examples if ex.source_doc_id in doc_ids]


def load_calibration_examples(examples: list[M2Example], manifest: dict) -> list[M2Example]:
    """Load only examples belonging to the CALIBRATION partition.

    SAFETY: Never use these examples for model fitting or parameter updates.
    Calibration examples are strictly reserved for post-hoc temperature scaling,
    isotonic calibration, and decision threshold selection.
    """
    doc_ids = set(manifest.get("calibration_doc_ids", []))
    if not doc_ids:
        raise ValueError("Manifest does not contain 'calibration_doc_ids'")
    return [ex for ex in examples if ex.source_doc_id in doc_ids]


def load_validation_examples(examples: list[M2Example], manifest: dict) -> list[M2Example]:
    """Load only examples belonging to the in-distribution VALIDATION partition.

    SAFETY: Never use these examples for training, calibration fitting, or threshold tuning.
    Validation examples are strictly reserved for final unbiased in-distribution evaluation.
    """
    doc_ids = set(manifest.get("validation_doc_ids", []))
    if not doc_ids:
        raise ValueError("Manifest does not contain 'validation_doc_ids'")
    return [ex for ex in examples if ex.source_doc_id in doc_ids]


def load_ood_examples(examples: list[M2Example], manifest: dict) -> list[M2Example]:
    """Load only examples belonging to the out-of-distribution (OOD) partition.

    SAFETY: Never use OOD data for model fitting, calibration fitting, early stopping,
    threshold tuning, or model selection.
    """
    ood_set = set(manifest.get("ood_doc_ids", []))
    if not ood_set:
        raise ValueError("Manifest does not contain 'ood_doc_ids'")
    return [ex for ex in examples if ex.source_doc_id in ood_set]


# Backward-compatible alias
def get_ood_examples(examples: list[M2Example], manifest: dict) -> list[M2Example]:
    return load_ood_examples(examples, manifest)


def get_fold_examples(
    examples: list[M2Example],
    manifest: dict,
    fold_idx: int,
) -> tuple[list[M2Example], list[M2Example]]:
    """Return (train_examples, val_examples) for a GroupKFold CV fold."""
    fold = manifest["cv_folds"][fold_idx]
    train_set = set(fold["train_doc_ids"])
    val_set = set(fold["val_doc_ids"])
    train = [ex for ex in examples if ex.source_doc_id in train_set]
    val = [ex for ex in examples if ex.source_doc_id in val_set]
    return train, val


# ─── AUDIT & INTEGRITY VALIDATION ─────────────────────────────────────────────

def validate_no_leakage(manifest: dict) -> None:
    """Assert mutual disjointness across partitions and CV folds."""
    ood = set(manifest.get("ood_doc_ids", []))

    # 1. Check 3-way split if present
    train_docs = set(manifest.get("train_doc_ids", []))
    calib_docs = set(manifest.get("calibration_doc_ids", []))
    val_docs = set(manifest.get("validation_doc_ids", []))

    if train_docs or calib_docs or val_docs:
        # Check mutual disjointness among train, calibration, validation
        assert train_docs.isdisjoint(calib_docs), (
            f"LEAKAGE: Train and Calibration overlap: {train_docs & calib_docs}"
        )
        assert train_docs.isdisjoint(val_docs), (
            f"LEAKAGE: Train and Validation overlap: {train_docs & val_docs}"
        )
        assert calib_docs.isdisjoint(val_docs), (
            f"LEAKAGE: Calibration and Validation overlap: {calib_docs & val_docs}"
        )

        # Check OOD isolation
        assert train_docs.isdisjoint(ood), (
            f"LEAKAGE: Train contains OOD documents: {train_docs & ood}"
        )
        assert calib_docs.isdisjoint(ood), (
            f"LEAKAGE: Calibration contains OOD documents: {calib_docs & ood}"
        )
        assert val_docs.isdisjoint(ood), (
            f"LEAKAGE: Validation contains OOD documents: {val_docs & ood}"
        )

    # 2. Check CV folds
    for i, fold in enumerate(manifest.get("cv_folds", [])):
        fold_train = set(fold["train_doc_ids"])
        fold_val = set(fold["val_doc_ids"])
        overlap = fold_train & fold_val
        assert not overlap, f"Train/val overlap in CV fold {i}: {overlap}"
        assert fold_train.isdisjoint(ood), (
            f"LEAKAGE: OOD doc in CV fold {i} train: {fold_train & ood}"
        )
        assert fold_val.isdisjoint(ood), (
            f"LEAKAGE: OOD doc in CV fold {i} val: {fold_val & ood}"
        )


def validate_3way_split(
    manifest: dict,
    examples: list[M2Example] | None = None,
    patient_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Perform rigorous 21-point validation of the canonical 3-way split.

    Returns diagnostic report. Raises AssertionError on invariant violation.
    """
    validate_no_leakage(manifest)

    train_docs = set(manifest["train_doc_ids"])
    calib_docs = set(manifest["calibration_doc_ids"])
    val_docs = set(manifest["validation_doc_ids"])
    ood_docs = set(manifest["ood_doc_ids"])
    cv_docs = set(manifest.get("cv_doc_ids", []))

    total_cv_expected = 86
    total_ood_expected = 34
    total_docs_expected = 120

    # 1. Total documents
    all_docs = train_docs | calib_docs | val_docs | ood_docs
    assert len(all_docs) == total_docs_expected, (
        f"Expected {total_docs_expected} total documents, got {len(all_docs)}"
    )

    # 2. CV documents count
    cv_reconstructed = train_docs | calib_docs | val_docs
    assert len(cv_reconstructed) == total_cv_expected, (
        f"Expected {total_cv_expected} CV documents, got {len(cv_reconstructed)}"
    )
    if cv_docs:
        assert cv_docs == cv_reconstructed, "cv_doc_ids does not match train + calib + val"

    # 3. OOD documents count
    assert len(ood_docs) == total_ood_expected, (
        f"Expected {total_ood_expected} OOD documents, got {len(ood_docs)}"
    )

    # 4. Partition disjointness
    assert train_docs.isdisjoint(calib_docs)
    assert train_docs.isdisjoint(val_docs)
    assert calib_docs.isdisjoint(val_docs)
    assert cv_reconstructed.isdisjoint(ood_docs)

    # 5. OOD composition audit (17 lab, 12 med, 5 narrative)
    doc_families = manifest.get("doc_family_counts", {})
    if doc_families and "ood" in doc_families:
        ood_fam = doc_families["ood"]
        assert ood_fam.get("lab", 0) == 17, f"OOD lab docs != 17: {ood_fam}"
        assert ood_fam.get("medication", 0) == 12, f"OOD med docs != 12: {ood_fam}"
        assert ood_fam.get("narrative", 0) == 5, f"OOD narrative docs != 5: {ood_fam}"

    # 6. Patient overlap check if patient_map provided
    if patient_map:
        patients_per_part: dict[str, set[str]] = defaultdict(set)
        for doc_id, pid in patient_map.items():
            if doc_id in train_docs:
                patients_per_part["train"].add(pid)
            elif doc_id in calib_docs:
                patients_per_part["calibration"].add(pid)
            elif doc_id in val_docs:
                patients_per_part["validation"].add(pid)
            elif doc_id in ood_docs:
                patients_per_part["ood"].add(pid)

        assert patients_per_part["train"].isdisjoint(patients_per_part["calibration"])
        assert patients_per_part["train"].isdisjoint(patients_per_part["validation"])
        assert patients_per_part["calibration"].isdisjoint(patients_per_part["validation"])
        assert (patients_per_part["train"] | patients_per_part["calibration"] | patients_per_part["validation"]).isdisjoint(patients_per_part["ood"])

    # 7. Examples audit if examples provided
    if examples:
        total_examples = len(examples)
        manifest_total = manifest.get("example_counts", {}).get("total", total_examples)
        assert total_examples == manifest_total, f"Expected {manifest_total} examples from manifest, got {total_examples}"

        doc_to_part = {}
        for d in train_docs:
            doc_to_part[d] = "train"
        for d in calib_docs:
            doc_to_part[d] = "calibration"
        for d in val_docs:
            doc_to_part[d] = "validation"
        for d in ood_docs:
            doc_to_part[d] = "ood"

        assigned_examples: dict[str, list[M2Example]] = defaultdict(list)
        group_to_part: dict[str, str] = {}

        for ex in examples:
            p = doc_to_part.get(ex.source_doc_id)
            assert p is not None, f"Example {ex.example_id} source_doc {ex.source_doc_id} unassigned"
            assigned_examples[p].append(ex)

            gid = ex.group_id
            if gid:
                if gid in group_to_part:
                    assert group_to_part[gid] == p, (
                        f"Group leakage: group {gid} spans {group_to_part[gid]} and {p}"
                    )
                else:
                    group_to_part[gid] = p

        total_assigned = sum(len(exs) for exs in assigned_examples.values())
        assert total_assigned == total_examples, (
            f"Assigned examples {total_assigned} != total examples {total_examples}"
        )

    return {
        "status": "VALID",
        "total_documents": len(all_docs),
        "cv_documents": len(cv_reconstructed),
        "ood_documents": len(ood_docs),
        "train_documents": len(train_docs),
        "calibration_documents": len(calib_docs),
        "validation_documents": len(val_docs),
    }
