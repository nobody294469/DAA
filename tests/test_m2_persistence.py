"""Unit tests for M2 persistent experiment-result backup mechanism."""
import json
import os
from pathlib import Path
import pytest

from clindoc.m2.recovery import (
    get_persist_dir,
    deduplicate_results,
    save_incremental_results,
    load_existing_results_map,
    get_training_plan,
)
from clindoc.m2.schema import M2ModelConfig


def test_persistence_disabled_by_default(monkeypatch, tmp_path):
    """When M2_PERSIST_DIR is not set, persistence is disabled and only local file is written."""
    monkeypatch.delenv("M2_PERSIST_DIR", raising=False)
    assert get_persist_dir() is None

    local_file = tmp_path / "local" / "m2_train_results.json"
    dummy_results = [
        {"model_name": "modernbert_full", "fold": 0, "seed": 42, "best_val_macro_f1": 0.91}
    ]

    ret = save_incremental_results(local_file, dummy_results)
    assert ret is None
    assert local_file.exists()
    with open(local_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["model_name"] == "modernbert_full"


def test_persistence_enabled_via_env_var(monkeypatch, tmp_path):
    """When M2_PERSIST_DIR is set, results are atomically written to both local and persistent directories."""
    persist_dir = tmp_path / "persistent_drive_backup"
    monkeypatch.setenv("M2_PERSIST_DIR", str(persist_dir))
    assert get_persist_dir() == persist_dir

    local_file = tmp_path / "local" / "m2_train_results.json"
    dummy_results = [
        {"model_name": "modernbert_full", "fold": 0, "seed": 42, "best_val_macro_f1": 0.9123},
        {"model_name": "modernbert_full", "fold": 0, "seed": 43, "best_val_macro_f1": 0.9234},
    ]

    backup_path = save_incremental_results(local_file, dummy_results)
    assert backup_path == persist_dir / "m2_train_results.json"
    assert local_file.exists()
    assert backup_path.exists()

    # Content in persistent backup must match local exactly
    with open(local_file, "r", encoding="utf-8") as f:
        local_data = json.load(f)
    with open(backup_path, "r", encoding="utf-8") as f:
        backup_data = json.load(f)
    assert local_data == backup_data
    assert len(backup_data) == 2

    # Individual run backup should also exist
    single_run = persist_dir / "runs" / "modernbert_full_fold0_seed43.json"
    assert single_run.exists()
    with open(single_run, "r", encoding="utf-8") as f:
        s_data = json.load(f)
    assert s_data["seed"] == 43


def test_persistence_enabled_via_explicit_arg(tmp_path):
    """Passing persist_dir directly works independently of environment variable."""
    persist_dir = tmp_path / "explicit_backup"
    local_file = tmp_path / "local" / "m2_train_results.json"
    dummy_results = [
        {"model_name": "modernbert_frozen_lower8", "fold": 1, "seed": 44, "best_val_macro_f1": 0.895}
    ]

    backup_path = save_incremental_results(local_file, dummy_results, persist_dir=persist_dir)
    assert backup_path == persist_dir / "m2_train_results.json"
    assert backup_path.exists()


def test_repeated_saves_do_not_corrupt_or_duplicate(tmp_path):
    """Repeated incremental saves with updated runs maintain clean JSON with no duplicates."""
    persist_dir = tmp_path / "persistent_backup"
    local_file = tmp_path / "local" / "m2_train_results.json"

    # Step 1: Save run 1
    results = [{"model_name": "modernbert_full", "fold": 0, "seed": 42, "best_val_macro_f1": 0.88}]
    save_incremental_results(local_file, results, persist_dir=persist_dir)

    # Step 2: Save run 1 again (e.g. updated score) and run 2
    results.append({"model_name": "modernbert_full", "fold": 0, "seed": 42, "best_val_macro_f1": 0.92})
    results.append({"model_name": "modernbert_full", "fold": 0, "seed": 43, "best_val_macro_f1": 0.90})
    save_incremental_results(local_file, results, persist_dir=persist_dir)

    backup_file = persist_dir / "m2_train_results.json"
    with open(backup_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Exactly 2 runs (run 1 was deduplicated to latest entry), no duplicates
    assert len(data) == 2
    f0_s42 = next(r for r in data if r["fold"] == 0 and r["seed"] == 42)
    assert f0_s42["best_val_macro_f1"] == 0.92
    f0_s43 = next(r for r in data if r["fold"] == 0 and r["seed"] == 43)
    assert f0_s43["best_val_macro_f1"] == 0.90

    # Ensure no lingering .tmp files
    tmp_files = list(persist_dir.glob("*.tmp")) + list(local_file.parent.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_load_existing_results_from_persistent_backup_when_local_missing(monkeypatch, tmp_path):
    """If local results file was wiped (e.g. Colab runtime restart), loads from M2_PERSIST_DIR."""
    persist_dir = tmp_path / "colab_drive_backup"
    persist_dir.mkdir(parents=True)
    persist_results_file = persist_dir / "m2_train_results.json"

    # Simulate completed modernbert_full runs saved in persistent drive
    completed_runs = [
        {"model_name": "modernbert_full", "fold": 0, "seed": 42, "best_val_macro_f1": 0.92},
        {"model_name": "modernbert_full", "fold": 0, "seed": 43, "best_val_macro_f1": 0.91},
    ]
    with open(persist_results_file, "w", encoding="utf-8") as f:
        json.dump(completed_runs, f)

    # Local results file does NOT exist
    local_file = tmp_path / "ephemeral_local" / "m2_train_results.json"
    assert not local_file.exists()

    monkeypatch.setenv("M2_PERSIST_DIR", str(persist_dir))

    # load_existing_results_map falls back to persistent drive
    res_map = load_existing_results_map(results_file=local_file)
    assert len(res_map) == 2
    assert ("modernbert_full", 0, 42) in res_map
    assert ("modernbert_full", 0, 43) in res_map
    assert res_map[("modernbert_full", 0, 42)]["best_val_macro_f1"] == 0.92
