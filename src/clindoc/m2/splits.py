"""Document-level train/val/test split construction for M2.

Rules:
- OOD held-out: mar_order_sheet (med) and template_c (lab) → never used in CV.
- CV: 5-fold GroupKFold on source_doc_id.
- No individual row splitting; entire document groups travel together.
"""
from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict

from clindoc.m2.schema import M2Example, OOD_MED_TEMPLATE, OOD_LAB_TEMPLATE


def build_splits(
    examples: list[M2Example],
    n_splits: int = 5,
    seed: int = 42,
) -> dict:
    """Build document-level GroupKFold splits and OOD holdout.

    Returns a manifest dict with keys:
      ood_doc_ids, cv_folds (list of {train_doc_ids, val_doc_ids})
    """
    import random
    rng = random.Random(seed)

    # Separate OOD documents
    ood_doc_ids: set[str] = set()
    cv_doc_ids_set: set[str] = set()

    for ex in examples:
        is_ood = (
            (ex.claim_type == "med" and ex.doc_template == OOD_MED_TEMPLATE) or
            (ex.claim_type == "lab" and ex.doc_template == OOD_LAB_TEMPLATE)
        )
        if is_ood:
            ood_doc_ids.add(ex.source_doc_id)
        else:
            cv_doc_ids_set.add(ex.source_doc_id)

    cv_doc_ids = sorted(cv_doc_ids_set)
    rng.shuffle(cv_doc_ids)

    # Build 5-fold splits on documents
    folds: list[dict[str, list[str]]] = []
    fold_size = len(cv_doc_ids) // n_splits
    for fold_idx in range(n_splits):
        val_start = fold_idx * fold_size
        val_end = val_start + fold_size if fold_idx < n_splits - 1 else len(cv_doc_ids)
        val_docs = cv_doc_ids[val_start:val_end]
        train_docs = [d for d in cv_doc_ids if d not in val_docs]
        folds.append({"train_doc_ids": train_docs, "val_doc_ids": val_docs})

    return {
        "ood_doc_ids": sorted(ood_doc_ids),
        "cv_doc_ids": cv_doc_ids,
        "cv_folds": folds,
        "n_splits": n_splits,
        "seed": seed,
    }


def save_split_manifest(manifest: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def get_fold_examples(
    examples: list[M2Example],
    manifest: dict,
    fold_idx: int,
) -> tuple[list[M2Example], list[M2Example]]:
    """Return (train_examples, val_examples) for a CV fold."""
    fold = manifest["cv_folds"][fold_idx]
    train_set = set(fold["train_doc_ids"])
    val_set = set(fold["val_doc_ids"])
    train = [ex for ex in examples if ex.source_doc_id in train_set]
    val = [ex for ex in examples if ex.source_doc_id in val_set]
    return train, val


def get_ood_examples(examples: list[M2Example], manifest: dict) -> list[M2Example]:
    ood_set = set(manifest["ood_doc_ids"])
    return [ex for ex in examples if ex.source_doc_id in ood_set]


def validate_no_leakage(manifest: dict) -> None:
    """Assert OOD doc IDs do not appear in any CV fold."""
    ood = set(manifest["ood_doc_ids"])
    for i, fold in enumerate(manifest["cv_folds"]):
        for doc in fold["train_doc_ids"] + fold["val_doc_ids"]:
            assert doc not in ood, (
                f"LEAKAGE: OOD doc {doc!r} appears in CV fold {i}"
            )
    # Assert train/val disjoint per fold
    for i, fold in enumerate(manifest["cv_folds"]):
        train_s = set(fold["train_doc_ids"])
        val_s = set(fold["val_doc_ids"])
        overlap = train_s & val_s
        assert not overlap, f"Train/val overlap in fold {i}: {overlap}"
