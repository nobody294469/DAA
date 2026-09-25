"""Unit tests for M2 checkpoint recovery, quarantine, and resumability."""
import json
from pathlib import Path
import pytest

from clindoc.m2.recovery import (
    parse_checkpoint_filename,
    discover_checkpoints,
    is_quarantined_checkpoint,
    is_quarantined_result,
    load_existing_results_map,
    get_training_plan,
)
from clindoc.m2.schema import M2ModelConfig


def test_parse_checkpoint_filename():
    p1 = Path("modernbert_text_only_fold0_seed42.pt")
    meta1 = parse_checkpoint_filename(p1)
    assert meta1 is not None
    assert meta1["model_name"] == "modernbert_text_only"
    assert meta1["fold"] == 0
    assert meta1["seed"] == 42

    p2 = Path("modernbert_full_fold3_seed44.pt")
    meta2 = parse_checkpoint_filename(p2)
    assert meta2 is not None
    assert meta2["model_name"] == "modernbert_full"
    assert meta2["fold"] == 3
    assert meta2["seed"] == 44

    p_invalid = Path("not_a_checkpoint.txt")
    assert parse_checkpoint_filename(p_invalid) is None


def test_discover_checkpoints_quarantine():
    ckpt_dir = Path("data/m2_results/checkpoints")
    if ckpt_dir.exists():
        # Default discover should exclude quarantined debug checkpoints
        clean_ckpts = discover_checkpoints(ckpt_dir, include_quarantined=False)
        clean_names = {p.name for p in clean_ckpts}
        assert "modernbert_full_fold0_seed42.pt" not in clean_names
        assert "ckpt_fold0_seed42_aux1_freeze0.pt" not in clean_names

        text_only = [p for p in clean_ckpts if "modernbert_text_only" in p.name]
        assert len(text_only) == 15


def test_recovered_result_causes_checkpoint_to_be_skipped_without_reevaluation(tmp_path):
    """Test that a recovered result is merged into existing_results_map and skips re-evaluation."""
    rec_file = tmp_path / "recovered.json"
    norm_file = tmp_path / "normal.json"

    dummy_rec = [
        {"model_name": "modernbert_text_only", "fold": 0, "seed": 42, "best_val_macro_f1": 0.8577, "source": "recovered"},
        {"model_name": "modernbert_text_only", "fold": 0, "seed": 43, "best_val_macro_f1": 0.8600, "source": "recovered"},
    ]
    with open(rec_file, "w", encoding="utf-8") as f:
        json.dump(dummy_rec, f)

    res_map = load_existing_results_map(results_file=norm_file, recovered_file=rec_file)
    assert len(res_map) == 2
    assert ("modernbert_text_only", 0, 42) in res_map
    assert ("modernbert_text_only", 0, 43) in res_map
    assert res_map[("modernbert_text_only", 0, 42)]["source"] == "recovered"


def test_duplicate_normal_and_recovered_result_handling(tmp_path):
    """Test that normal results take precedence over recovered results without duplicates."""
    rec_file = tmp_path / "recovered.json"
    norm_file = tmp_path / "normal.json"

    dummy_rec = [
        {"model_name": "modernbert_text_only", "fold": 0, "seed": 42, "best_val_macro_f1": 0.8577, "source": "recovered"},
    ]
    dummy_norm = [
        {"model_name": "modernbert_text_only", "fold": 0, "seed": 42, "best_val_macro_f1": 0.8800, "source": "official_normal"},
    ]
    with open(rec_file, "w", encoding="utf-8") as f:
        json.dump(dummy_rec, f)
    with open(norm_file, "w", encoding="utf-8") as f:
        json.dump(dummy_norm, f)

    res_map = load_existing_results_map(results_file=norm_file, recovered_file=rec_file)
    # Exactly 1 entry, no duplicates
    assert len(res_map) == 1
    # Official normal result took precedence
    assert res_map[("modernbert_text_only", 0, 42)]["source"] == "official_normal"
    assert res_map[("modernbert_text_only", 0, 42)]["best_val_macro_f1"] == 0.8800


def test_stale_debug_full_checkpoint_not_counted_as_completed(tmp_path):
    """Verify that modernbert_full_fold0_seed42.pt debug checkpoint and recovered record are quarantined."""
    debug_ckpt = Path("data/m2_results/checkpoints/modernbert_full/modernbert_full_fold0_seed42.pt")
    assert is_quarantined_checkpoint(debug_ckpt, debug=False) is True
    # In explicit debug mode, it is allowed
    assert is_quarantined_checkpoint(debug_ckpt, debug=True) is False

    debug_item = {
        "model_name": "modernbert_full",
        "fold": 0,
        "seed": 42,
        "best_val_macro_f1": 0.1667,
        "checkpoint_path": str(debug_ckpt),
    }
    assert is_quarantined_result(debug_item, debug=False) is True

    # Check that load_existing_results_map filters it out
    rec_file = tmp_path / "recovered.json"
    with open(rec_file, "w", encoding="utf-8") as f:
        json.dump([debug_item], f)
    res_map = load_existing_results_map(recovered_file=rec_file, debug=False)
    assert ("modernbert_full", 0, 42) not in res_map


def test_load_recovered_headline_results_30_records():
    """Verify loading the 30 recovered records with exact schema and preserved metrics."""
    from clindoc.m2.recovery import load_recovered_headline_results, DEFAULT_HEADLINE_RECOVERED_FILE

    if not DEFAULT_HEADLINE_RECOVERED_FILE.exists():
        pytest.skip("m2_recovered_headline_results.json not found")

    headline_map = load_recovered_headline_results(DEFAULT_HEADLINE_RECOVERED_FILE)
    assert len(headline_map) == 30

    text_only = [rec for k, rec in headline_map.items() if k[0] == "modernbert_text_only"]
    ocr_only = [rec for k, rec in headline_map.items() if k[0] == "modernbert_ocr_only"]
    full_runs = [rec for k, rec in headline_map.items() if k[0] == "modernbert_full"]
    frozen_runs = [rec for k, rec in headline_map.items() if k[0] == "modernbert_frozen_lower8"]

    assert len(text_only) == 15
    assert len(ocr_only) == 15
    assert len(full_runs) == 0
    assert len(frozen_runs) == 0

    for k, rec in headline_map.items():
        assert rec["status"] == "completed_recovered_headline"
        assert rec["detailed_metrics_available"] is False
        assert rec["checkpoint_available"] is False

        # Verify all four headline metrics exist and are non-trivial floats
        assert 0.0 < rec["val_macro_f1"] <= 1.0
        assert 0.0 < rec["val_discrepancy_recall"] <= 1.0
        assert 0.0 < rec["ood_macro_f1"] <= 1.0
        assert 0.0 < rec["ood_discrepancy_recall"] <= 1.0

        # Verify missing detailed metrics are NOT fabricated in the record
        assert "per_class" not in rec
        assert "auroc_discrepancy" not in rec
        assert "brier_score" not in rec
        assert "ece" not in rec
        assert "confusion_matrix" not in rec


def test_expected_training_plan_with_recovered_headline_results():
    """Verify official training plan counts: 0 full completed, 30 headline recovered, 0 recovery, 30 training."""
    manifest_path = Path("data/m2_verification/split_manifest.json")
    if not manifest_path.exists():
        pytest.skip("split_manifest.json not found")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    config = M2ModelConfig()
    plan = get_training_plan(manifest, config, debug=False)

    by_variant = plan["by_variant"]
    assert by_variant["modernbert_text_only"]["completed"] == 0
    assert by_variant["modernbert_text_only"]["headline_recovered"] == 15
    assert by_variant["modernbert_text_only"]["recovery"] == 0
    assert by_variant["modernbert_text_only"]["training"] == 0

    assert by_variant["modernbert_ocr_only"]["completed"] == 0
    assert by_variant["modernbert_ocr_only"]["headline_recovered"] == 15
    assert by_variant["modernbert_ocr_only"]["recovery"] == 0
    assert by_variant["modernbert_ocr_only"]["training"] == 0

    assert by_variant["modernbert_full"]["completed"] == 0
    assert by_variant["modernbert_full"]["headline_recovered"] == 0
    assert by_variant["modernbert_full"]["recovery"] == 0
    assert by_variant["modernbert_full"]["training"] == 15

    assert by_variant["modernbert_frozen_lower8"]["completed"] == 0
    assert by_variant["modernbert_frozen_lower8"]["headline_recovered"] == 0
    assert by_variant["modernbert_frozen_lower8"]["recovery"] == 0
    assert by_variant["modernbert_frozen_lower8"]["training"] == 15

    assert plan["total_completed"] == 0
    assert plan["total_headline_recovered"] == 30
    assert plan["total_recovery"] == 0
    assert plan["total_training"] == 30


def test_headline_records_distinguished_and_not_retrained():
    """Ensure training planner recognizes headline records so they are not retrained."""
    from clindoc.m2.recovery import load_recovered_headline_results

    headline_map = load_recovered_headline_results()
    assert len(headline_map) == 30

    # Ensure all 15 text_only and 15 ocr_only runs are recognized
    for fold in range(5):
        for seed in (42, 43, 44):
            assert ("modernbert_text_only", fold, seed) in headline_map
            assert ("modernbert_ocr_only", fold, seed) in headline_map
            assert ("modernbert_full", fold, seed) not in headline_map
            assert ("modernbert_frozen_lower8", fold, seed) not in headline_map

            rec_text = headline_map[("modernbert_text_only", fold, seed)]
            assert rec_text["status"] == "completed_recovered_headline"
            assert rec_text["detailed_metrics_available"] is False
            assert rec_text["checkpoint_available"] is False


def test_step_evaluate_distinguishes_headline_without_fabrication(tmp_path):
    """Verify that reporting layer distinguishes headline-only from full results without fabricating metrics."""
    from clindoc.m2.run import _mean_std
    from clindoc.m2.recovery import load_recovered_headline_results

    headline_map = load_recovered_headline_results()
    text_runs = [rec for k, rec in headline_map.items() if k[0] == "modernbert_text_only"]
    assert len(text_runs) == 15

    val_f1_m, val_f1_s = _mean_std([r["val_macro_f1"] for r in text_runs])
    val_rec_m, val_rec_s = _mean_std([r["val_discrepancy_recall"] for r in text_runs])
    ood_f1_m, ood_f1_s = _mean_std([r["ood_macro_f1"] for r in text_runs])
    ood_rec_m, ood_rec_s = _mean_std([r["ood_discrepancy_recall"] for r in text_runs])

    # Verify real mean values computed directly from the 15 preserved runs
    assert round(val_f1_m, 4) == 0.8714
    assert round(val_rec_m, 4) == 0.9411
    assert round(ood_f1_m, 4) == 0.7822
    assert round(ood_rec_m, 4) == 0.9460
