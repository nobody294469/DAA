"""Dedicated validation test suite for Canonical 3-Way CV Split Generator.

Verifies the 21 required invariants specified in Phase 1 Task 4:
 1. Exactly 120 documents represented overall
 2. Exactly 86 CV documents
 3. Exactly 34 OOD documents
 4. Every CV document occurs in exactly one of train/calibration/validation
 5. Every OOD document occurs only in OOD
 6. Zero document overlap between all four partitions
 7. Zero group_id overlap between partitions
 8. Zero patient overlap where patient IDs are available
 9. OOD composition remains 17 lab / 12 med / 5 narrative
10. Deterministic reproduction with same seed
11. Changed seed produces a valid alternative split
12. Document-family representation is preserved across partitions
13. CV template representation is preserved across partitions
14. All Tier-1 fields represented across partitions
15. Label distributions are reported correctly
16. All 9,168 examples assigned exactly once
17. No example duplication
18. No example loss
19. Split manifest matches actual dataset contents
20. GroupKFold functionality still works
21. Historical/legacy result directories remain untouched
"""
import json
from pathlib import Path
from collections import defaultdict
import pytest

from clindoc.m2.dataset import load_examples
from clindoc.m2.schema import (
    M2Example, OOD_MED_TEMPLATE, OOD_LAB_TEMPLATE, OOD_NARRATIVE_TEMPLATES,
    VerificationLabel,
)
from clindoc.m2.splits import (
    build_splits, build_3way_cv_split, validate_no_leakage, validate_3way_split,
    load_train_examples, load_calibration_examples, load_validation_examples,
    load_ood_examples, get_fold_examples,
)

CANONICAL_DATA_DIR = Path("data/m2_verification")
MANIFEST_PATH = CANONICAL_DATA_DIR / "split_manifest.json"
EXAMPLES_PATH = CANONICAL_DATA_DIR / "all_examples.jsonl"


@pytest.fixture(scope="module")
def manifest():
    assert MANIFEST_PATH.exists(), "split_manifest.json does not exist"
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def canonical_examples():
    assert EXAMPLES_PATH.exists(), "all_examples.jsonl does not exist"
    return load_examples(EXAMPLES_PATH)


@pytest.fixture(scope="module")
def patient_map():
    mapping = {}
    for p in Path("data/ground_truth").glob("LAB_*.json"):
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            pid = data.get("patient_id") or data.get("patient", {}).get("patient_id")
            mapping[data["doc_id"]] = pid

    for p in Path("data/med_ground_truth").glob("MED_*.json"):
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            pid = data.get("patient_id") or data.get("patient", {}).get("patient_id")
            mapping[data["doc_id"]] = pid

    for p in Path("data/narrative_ground_truth").glob("NARRATIVE_*.json"):
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            pid = data.get("patient_id") or data.get("patient", {}).get("patient_id")
            mapping[data["doc_id"]] = pid

    return mapping


# ─── INVARIANT 1: Exactly 120 documents represented overall ───────────────────
def test_01_total_documents_count(manifest):
    train = set(manifest["train_doc_ids"])
    calib = set(manifest["calibration_doc_ids"])
    val = set(manifest["validation_doc_ids"])
    ood = set(manifest["ood_doc_ids"])
    all_docs = train | calib | val | ood

    assert len(all_docs) == 120, f"Expected 120 documents, got {len(all_docs)}"
    assert manifest["document_counts"]["total"] == 120


# ─── INVARIANT 2: Exactly 86 CV documents ────────────────────────────────────
def test_02_cv_documents_count(manifest):
    train = set(manifest["train_doc_ids"])
    calib = set(manifest["calibration_doc_ids"])
    val = set(manifest["validation_doc_ids"])
    cv_docs = train | calib | val

    assert len(cv_docs) == 86, f"Expected 86 CV documents, got {len(cv_docs)}"
    assert manifest["document_counts"]["cv_total"] == 86
    assert set(manifest["cv_doc_ids"]) == cv_docs


# ─── INVARIANT 3: Exactly 34 OOD documents ───────────────────────────────────
def test_03_ood_documents_count(manifest):
    ood = set(manifest["ood_doc_ids"])
    assert len(ood) == 34, f"Expected 34 OOD documents, got {len(ood)}"
    assert manifest["document_counts"]["ood"] == 34


# ─── INVARIANT 4: Every CV document occurs in exactly one partition ──────────
def test_04_cv_documents_exact_partition(manifest):
    train = set(manifest["train_doc_ids"])
    calib = set(manifest["calibration_doc_ids"])
    val = set(manifest["validation_doc_ids"])

    assert len(train) == 52, f"Expected 52 train docs, got {len(train)}"
    assert len(calib) == 17, f"Expected 17 calibration docs, got {len(calib)}"
    assert len(val) == 17, f"Expected 17 validation docs, got {len(val)}"

    # Sum of counts equals union count (pairwise disjointness)
    assert len(train) + len(calib) + len(val) == len(train | calib | val)


# ─── INVARIANT 5: Every OOD document occurs only in OOD ───────────────────────
def test_05_ood_documents_only_in_ood(manifest):
    ood = set(manifest["ood_doc_ids"])
    train = set(manifest["train_doc_ids"])
    calib = set(manifest["calibration_doc_ids"])
    val = set(manifest["validation_doc_ids"])

    for d in ood:
        assert d not in train, f"OOD document {d} found in train!"
        assert d not in calib, f"OOD document {d} found in calibration!"
        assert d not in val, f"OOD document {d} found in validation!"


# ─── INVARIANT 6: Zero document overlap between all four partitions ──────────
def test_06_zero_document_overlap(manifest):
    train = set(manifest["train_doc_ids"])
    calib = set(manifest["calibration_doc_ids"])
    val = set(manifest["validation_doc_ids"])
    ood = set(manifest["ood_doc_ids"])

    assert train.isdisjoint(calib)
    assert train.isdisjoint(val)
    assert train.isdisjoint(ood)
    assert calib.isdisjoint(val)
    assert calib.isdisjoint(ood)
    assert val.isdisjoint(ood)

    validate_no_leakage(manifest)


# ─── INVARIANT 7: Zero group_id overlap between partitions ───────────────────
def test_07_zero_group_id_overlap(manifest, canonical_examples):
    doc_to_part = {}
    for d in manifest["train_doc_ids"]:
        doc_to_part[d] = "train"
    for d in manifest["calibration_doc_ids"]:
        doc_to_part[d] = "calibration"
    for d in manifest["validation_doc_ids"]:
        doc_to_part[d] = "validation"
    for d in manifest["ood_doc_ids"]:
        doc_to_part[d] = "ood"

    group_to_part = {}
    for ex in canonical_examples:
        gid = ex.group_id
        part = doc_to_part[ex.source_doc_id]
        if gid in group_to_part:
            assert group_to_part[gid] == part, (
                f"GROUP LEAKAGE: group_id {gid} appears in {group_to_part[gid]} and {part}"
            )
        group_to_part[gid] = part


# ─── INVARIANT 8: Zero patient overlap where patient IDs are available ───────
def test_08_zero_patient_overlap(manifest, patient_map):
    train = set(manifest["train_doc_ids"])
    calib = set(manifest["calibration_doc_ids"])
    val = set(manifest["validation_doc_ids"])
    ood = set(manifest["ood_doc_ids"])

    patients = defaultdict(set)
    for doc_id, pid in patient_map.items():
        if doc_id in train:
            patients["train"].add(pid)
        elif doc_id in calib:
            patients["calibration"].add(pid)
        elif doc_id in val:
            patients["validation"].add(pid)
        elif doc_id in ood:
            patients["ood"].add(pid)

    assert patients["train"].isdisjoint(patients["calibration"])
    assert patients["train"].isdisjoint(patients["validation"])
    assert patients["train"].isdisjoint(patients["ood"])
    assert patients["calibration"].isdisjoint(patients["validation"])
    assert patients["calibration"].isdisjoint(patients["ood"])
    assert patients["validation"].isdisjoint(patients["ood"])


# ─── INVARIANT 9: OOD composition remains 17 lab / 12 med / 5 narrative ─────
def test_09_ood_composition(manifest):
    ood = manifest["ood_doc_ids"]
    lab_ood = [d for d in ood if d.startswith("LAB_")]
    med_ood = [d for d in ood if d.startswith("MED_")]
    narrative_ood = [d for d in ood if d.startswith("NARRATIVE_")]

    assert len(lab_ood) == 17, f"Expected 17 lab OOD docs, got {len(lab_ood)}"
    assert len(med_ood) == 12, f"Expected 12 med OOD docs, got {len(med_ood)}"
    assert len(narrative_ood) == 5, f"Expected 5 narrative OOD docs, got {len(narrative_ood)}"
    assert len(lab_ood) + len(med_ood) + len(narrative_ood) == 34


# ─── INVARIANT 10: Deterministic reproduction with same seed ─────────────────
def test_10_deterministic_reproduction(canonical_examples):
    manifest1 = build_splits(canonical_examples, n_splits=5, seed=42)
    manifest2 = build_splits(canonical_examples, n_splits=5, seed=42)

    assert manifest1["train_doc_ids"] == manifest2["train_doc_ids"]
    assert manifest1["calibration_doc_ids"] == manifest2["calibration_doc_ids"]
    assert manifest1["validation_doc_ids"] == manifest2["validation_doc_ids"]
    assert manifest1["ood_doc_ids"] == manifest2["ood_doc_ids"]


# ─── INVARIANT 11: Changed seed produces a valid alternative split ───────────
def test_11_changed_seed_alternative_split(canonical_examples):
    alt_manifest = build_splits(canonical_examples, n_splits=5, seed=999)

    # Invariants must still hold
    val_report = validate_3way_split(alt_manifest, examples=canonical_examples)
    assert val_report["status"] == "VALID"
    assert alt_manifest["document_counts"]["train"] == 52
    assert alt_manifest["document_counts"]["calibration"] == 17
    assert alt_manifest["document_counts"]["validation"] == 17
    assert alt_manifest["document_counts"]["ood"] == 34

    # Resulting assignment must differ from seed 42
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        canonical = json.load(f)
    assert alt_manifest["train_doc_ids"] != canonical["train_doc_ids"]


# ─── INVARIANT 12: Document-family representation is preserved ───────────────
def test_12_document_family_representation(manifest):
    doc_fam = manifest["doc_family_counts"]
    for part in ["train", "calibration", "validation"]:
        assert doc_fam[part].get("lab", 0) > 0, f"Missing lab in {part}"
        assert doc_fam[part].get("medication", 0) > 0, f"Missing med in {part}"
        assert doc_fam[part].get("narrative", 0) > 0, f"Missing narrative in {part}"

    assert doc_fam["train"] == {"lab": 20, "medication": 23, "narrative": 9}
    assert doc_fam["calibration"] == {"lab": 6, "medication": 8, "narrative": 3}
    assert doc_fam["validation"] == {"lab": 7, "medication": 7, "narrative": 3}
    assert doc_fam["ood"] == {"lab": 17, "medication": 12, "narrative": 5}


# ─── INVARIANT 13: CV template representation is preserved ───────────────────
def test_13_cv_template_representation(manifest):
    cv_templates = [
        "template_a", "template_b", "clinic_rx_pad", "discharge_summary", "pharmacy_label",
        "narrative_emergency_observation_note_v1",
        "narrative_inpatient_discharge_summary_v1",
        "narrative_surgical_discharge_summary_v1",
    ]
    template_counts = manifest["template_counts"]
    for part in ["train", "calibration", "validation"]:
        for tmpl in cv_templates:
            assert template_counts[part].get(tmpl, 0) > 0, (
                f"CV template {tmpl} has zero representation in {part}!"
            )


# ─── INVARIANT 14: All Tier-1 fields represented ─────────────────────────────
def test_14_tier1_fields_represented(manifest):
    tier1_fields = [
        "lab.value", "lab.flag", "med.name", "med.dose", "med.route", "med.frequency",
    ]
    field_counts = manifest["field_counts"]
    for part in ["train", "calibration", "validation", "ood"]:
        for f in tier1_fields:
            assert field_counts[part].get(f, 0) > 0, (
                f"Tier-1 field {f} has zero count in partition {part}"
            )


# ─── INVARIANT 15: Label distributions reported correctly ─────────────────────
def test_15_label_distributions(manifest):
    label_counts = manifest["label_counts"]
    for part in ["train", "calibration", "validation", "ood"]:
        assert label_counts[part]["SUPPORTED"] > 0
        assert label_counts[part]["DISCREPANCY"] > 0
        assert label_counts[part]["INSUFFICIENT_EVIDENCE"] > 0
        # In our balanced synthesis, SUPPORTED and DISCREPANCY are equal in count per partition
        assert label_counts[part]["SUPPORTED"] == label_counts[part]["DISCREPANCY"]


# ─── INVARIANT 16: All 9,168 examples assigned exactly once ───────────────────
def test_16_all_examples_assigned_once(manifest, canonical_examples):
    tr = load_train_examples(canonical_examples, manifest)
    cal = load_calibration_examples(canonical_examples, manifest)
    val = load_validation_examples(canonical_examples, manifest)
    ood = load_ood_examples(canonical_examples, manifest)

    total = len(tr) + len(cal) + len(val) + len(ood)
    assert total == 9168, f"Expected 9168 assigned examples, got {total}"


# ─── INVARIANT 17: No example duplication ────────────────────────────────────
def test_17_no_example_duplication(manifest, canonical_examples):
    tr = set(e.example_id for e in load_train_examples(canonical_examples, manifest))
    cal = set(e.example_id for e in load_calibration_examples(canonical_examples, manifest))
    val = set(e.example_id for e in load_validation_examples(canonical_examples, manifest))
    ood = set(e.example_id for e in load_ood_examples(canonical_examples, manifest))

    assert tr.isdisjoint(cal)
    assert tr.isdisjoint(val)
    assert tr.isdisjoint(ood)
    assert cal.isdisjoint(val)
    assert cal.isdisjoint(ood)
    assert val.isdisjoint(ood)


# ─── INVARIANT 18: No example loss ───────────────────────────────────────────
def test_18_no_example_loss(manifest, canonical_examples):
    tr = set(e.example_id for e in load_train_examples(canonical_examples, manifest))
    cal = set(e.example_id for e in load_calibration_examples(canonical_examples, manifest))
    val = set(e.example_id for e in load_validation_examples(canonical_examples, manifest))
    ood = set(e.example_id for e in load_ood_examples(canonical_examples, manifest))

    all_assigned = tr | cal | val | ood
    all_canonical = set(e.example_id for e in canonical_examples)
    assert all_assigned == all_canonical


# ─── INVARIANT 19: Split manifest matches actual dataset contents ─────────────
def test_19_manifest_matches_dataset(manifest, canonical_examples):
    doc_ids_in_examples = set(e.source_doc_id for e in canonical_examples)
    doc_ids_in_manifest = (
        set(manifest["train_doc_ids"]) |
        set(manifest["calibration_doc_ids"]) |
        set(manifest["validation_doc_ids"]) |
        set(manifest["ood_doc_ids"])
    )
    assert doc_ids_in_examples == doc_ids_in_manifest
    assert manifest["example_counts"]["total"] == len(canonical_examples)


# ─── INVARIANT 20: GroupKFold functionality still works ───────────────────────
def test_20_group_kfold_still_works(manifest, canonical_examples):
    assert "cv_folds" in manifest
    assert len(manifest["cv_folds"]) == 5
    for i in range(5):
        tr, val = get_fold_examples(canonical_examples, manifest, i)
        tr_docs = set(e.source_doc_id for e in tr)
        val_docs = set(e.source_doc_id for e in val)
        assert tr_docs.isdisjoint(val_docs)
        assert len(tr_docs | val_docs) == 86


# ─── INVARIANT 21: Historical/legacy result directories remain untouched ──────
def test_21_legacy_directories_untouched():
    v1_legacy = Path("data/m2_results_v1_legacy")
    assert v1_legacy.exists(), "data/m2_results_v1_legacy directory must be preserved"
    assert v1_legacy.is_dir()
