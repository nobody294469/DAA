"""smoke_test_modernbert_full.py — Minimal controlled real-pipeline smoke test for modernbert_full.

Exercises the real canonical AUX18 pipeline on a single fold (fold 0, seed 42)
without using debug mode (no subset truncation, real epochs, real calibration fitting).

Verifies:
  1. Canonical AUX18 architecture (18-dim auxiliary feature projection).
  2. Checkpoint saving with fitted post-hoc calibration temperature (DEC-009).
  3. Validation fold evaluation and held-out OOD evaluation with temperature scaling.
  4. Standalone checkpoint recovery via recover_and_evaluate_checkpoint().
  5. calibration_temperature persistence in both checkpoint state_dict and recovered M2EvalResult.
  6. Strict quarantine rejection of legacy AUX21 and debug checkpoints.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import torch

from clindoc.m2.schema import M2ModelConfig, M2EvalResult, AUX_DIM
from clindoc.m2.model import ClinicalVerifier, load_tokenizer
from clindoc.m2.splits import get_fold_examples, get_ood_examples, load_calibration_examples
from clindoc.m2.dataset import load_examples
from clindoc.m2.train import train_one_fold
from clindoc.m2.recovery import (
    recover_and_evaluate_checkpoint,
    is_quarantined_checkpoint,
    QUARANTINED_CHECKPOINT_NAMES,
)

DATA_DIR = Path("data/m2_verification")
SMOKE_OUT_DIR = Path("data/m2_results/checkpoints/smoke_test")
CONFIG_PATH = Path("configs/m2_train_config.json")


def load_config() -> M2ModelConfig:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return M2ModelConfig.model_validate(data)
    return M2ModelConfig()


def run_smoke_test(
    fold_idx: int = 0,
    seed: int = 42,
    checkpoint_path: Path | str | None = None,
    allow_train: bool = False,
) -> dict:
    print("=" * 80)
    print(f"M2 REAL SMOKE TEST: modernbert_full (Canonical AUX18, Fold {fold_idx}, Seed {seed})")
    print("=" * 80)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A (CPU)"
    print(f"Device: {device} ({gpu_name})")
    print(f"Canonical AUX_DIM: {AUX_DIM} (DEC-017)")
    assert AUX_DIM == 18, f"Expected canonical AUX_DIM=18, got {AUX_DIM}"

    # Determine target checkpoint path
    if checkpoint_path is not None:
        ckpt_path = Path(checkpoint_path)
    else:
        ckpt_name = f"modernbert_full_fold{fold_idx}_seed{seed}.pt"
        ckpt_path = SMOKE_OUT_DIR / ckpt_name

    elapsed = 0.0

    if not ckpt_path.exists():
        if not allow_train:
            print(f"\n[ERROR] Checkpoint not found at: {ckpt_path}")
            print("checkpoint not found; smoke verification cannot continue")
            sys.exit(1)
        else:
            print(f"\n[WARNING] Checkpoint not found at: {ckpt_path}")
            print("  -> Explicit training requested (--train); starting training run...")

    # 1. Load data & splits
    print("\n[1/6] Loading canonical M2 dataset and split manifest...")
    examples = load_examples(DATA_DIR / "all_examples.jsonl")
    with open(DATA_DIR / "split_manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    ood_set = set(manifest["ood_doc_ids"])
    train_ex, val_ex = get_fold_examples(examples, manifest, fold_idx)
    train_ex = [e for e in train_ex if e.source_doc_id not in ood_set]
    val_ex = [e for e in val_ex if e.source_doc_id not in ood_set]
    ood_ex = get_ood_examples(examples, manifest)
    calib_ex = load_calibration_examples(examples, manifest)

    print(f"  Train examples (Fold {fold_idx}): {len(train_ex)}")
    print(f"  Val examples (Fold {fold_idx}):   {len(val_ex)}")
    print(f"  OOD examples:                   {len(ood_ex)}")
    print(f"  Calibration partition examples: {len(calib_ex)}")

    config = load_config()
    print(f"  Batch size: {config.batch_size} | Max seq length: {config.max_seq_length}")
    print(f"  Learning rates: encoder={config.encoder_lr}, head={config.head_lr}")

    # 2. Check if checkpoint already exists to avoid retraining
    if ckpt_path.exists():
        print(f"\n[2/6] Found existing smoke checkpoint at: {ckpt_path}")
        print("  -> Continuing verification from EXISTING checkpoint (retraining skipped).")
    else:
        print(f"\n[2/6] Training modernbert_full on Fold {fold_idx} (debug=False, real pipeline)...")
        SMOKE_OUT_DIR.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        train_result = train_one_fold(
            train_examples=train_ex,
            val_examples=val_ex,
            config=config,
            fold_idx=fold_idx,
            seed=seed,
            checkpoint_dir=SMOKE_OUT_DIR,
            debug=False,  # Full real training, no debug subset trimming
            aux_mode="full",
            freeze_lower_n_layers=0,
            model_name="modernbert_full",
            ood_examples=ood_ex,
            calib_examples=calib_ex,
        )
        elapsed = time.time() - t0
        print(f"\nTraining completed in {elapsed:.1f}s ({elapsed/60:.2f} min)")
        ckpt_path = Path(train_result["checkpoint_path"])

    print(f"Verified checkpoint target: {ckpt_path} ({ckpt_path.stat().st_size / 1e6:.1f} MB)")

    # 3. Verify checkpoint contents & temperature
    print("\n[3/6] Verifying checkpoint state_dict and AUX18 architecture...")
    raw_ckpt = torch.load(ckpt_path, map_location="cpu")
    assert "model_state" in raw_ckpt, "Missing 'model_state' in checkpoint"
    assert "temperature" in raw_ckpt, "Missing 'temperature' in checkpoint state_dict (DEC-009)"
    assert raw_ckpt["temperature"] is not None, "Checkpoint temperature is None"
    fitted_temp = float(raw_ckpt["temperature"])
    assert fitted_temp > 0, f"Invalid temperature: {fitted_temp}"
    print(f"  * Checkpoint temperature: T = {fitted_temp:.6f}")

    # Verify AUX18 projection weight shape
    # ClinicalVerifier defines aux_proj as nn.Sequential(nn.Linear(AUX_DIM, AUX_PROJ_DIM), nn.GELU())
    # The linear projection layer is at index 0, so the state_dict key is 'aux_proj.0.weight'
    model_state = raw_ckpt["model_state"]
    aux_proj_weight = model_state.get("aux_proj.0.weight")
    if aux_proj_weight is None:
        aux_proj_weight = model_state.get("aux_proj.weight")
    assert aux_proj_weight is not None, (
        f"Missing auxiliary projection weight in model_state. Keys with 'aux': {[k for k in model_state if 'aux' in k]}"
    )
    assert aux_proj_weight.shape == (64, 18), (
        f"Expected AUX18 projection shape (64, 18), got {aux_proj_weight.shape}"
    )
    print(f"  * AUX projection weight shape: {list(aux_proj_weight.shape)} (Canonical AUX18 verified)")

    # 4. Verify standalone recovery
    print("\n[4/6] Verifying standalone checkpoint recovery via recover_and_evaluate_checkpoint()...")
    tokenizer = load_tokenizer(config.model_id)
    recovered = recover_and_evaluate_checkpoint(
        checkpoint_path=ckpt_path,
        examples=examples,
        manifest=manifest,
        ood_examples=ood_ex,
        tokenizer=tokenizer,
        config=config,
    )

    rec_temp = recovered.get("calibration_temperature")
    assert rec_temp is not None, "Missing 'calibration_temperature' in recovered result"
    assert abs(rec_temp - fitted_temp) < 1e-6, (
        f"Recovered temperature ({rec_temp}) does not match checkpoint ({fitted_temp})"
    )
    val_eval = recovered["val_eval"]
    assert val_eval.get("calibration_temperature") == rec_temp, (
        "val_eval missing calibration_temperature"
    )
    print(f"  * Recovered calibration_temperature: {rec_temp:.6f}")
    print(f"  * Val Macro-F1: {val_eval['macro_f1']:.4f} | Disc Recall: {val_eval['discrepancy_recall']:.4f}")
    print(f"  * Val Brier Score: {val_eval.get('brier_score')} | ECE: {val_eval.get('ece')}")

    ood_eval = recovered.get("ood_eval")
    if ood_eval:
        assert ood_eval.get("calibration_temperature") == rec_temp, (
            "ood_eval missing calibration_temperature"
        )
        print(f"  * OOD Macro-F1: {ood_eval['macro_f1']:.4f} | Disc Recall: {ood_eval['discrepancy_recall']:.4f}")
        print(f"  * OOD Brier Score: {ood_eval.get('brier_score')} | ECE: {ood_eval.get('ece')}")

    # 5. Verify quarantine rules
    print("\n[5/6] Verifying quarantine rules on legacy checkpoints...")
    p_aux21 = Path("checkpoints/full_model/ckpt_fold0_seed42_aux1_freeze0.pt")
    p_debug = Path("checkpoints/modernbert_full/modernbert_full_fold0_seed42.pt")
    assert is_quarantined_checkpoint(p_aux21, debug=False) is True, "Legacy AUX21 checkpoint was not quarantined!"
    assert is_quarantined_checkpoint(p_debug, debug=False) is True, "Stale debug checkpoint was not quarantined!"
    print(f"  * Quarantined names: {QUARANTINED_CHECKPOINT_NAMES}")
    print("  * Legacy AUX21 checkpoint (Linear(21, 64)) strictly quarantined and rejected.")

    # 6. Save smoke test summary report
    print("\n[6/6] Writing smoke test verification report...")
    smoke_report = {
        "status": "PASSED",
        "variant": "modernbert_full",
        "aux_mode": "full",
        "aux_dim": AUX_DIM,
        "fold": fold_idx,
        "seed": seed,
        "device": device,
        "gpu_name": gpu_name,
        "training_time_seconds": round(elapsed, 2),
        "calibration_temperature": rec_temp,
        "checkpoint_path": str(ckpt_path),
        "val_eval": val_eval,
        "ood_eval": ood_eval,
    }
    report_file = SMOKE_OUT_DIR / "smoke_test_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(smoke_report, f, indent=2)
    print(f"Smoke test report written to: {report_file}")

    print("\n" + "=" * 80)
    print("SMOKE TEST COMPLETE: All assertions passed successfully!")
    print("=" * 80)
    return smoke_report


def main(argv: list[str] | None = None) -> dict:
    if argv is None:
        argv = sys.argv[1:]
    fold = 0
    seed = 42
    ckpt_override = None
    allow_train = False
    non_flag_args = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--checkpoint", "-c") and i + 1 < len(argv):
            ckpt_override = Path(argv[i + 1])
            i += 2
        elif arg.startswith("--checkpoint="):
            ckpt_override = Path(arg.split("=", 1)[1])
            i += 1
        elif arg in ("--train", "--allow-train"):
            allow_train = True
            i += 1
        elif arg.endswith(".pt"):
            ckpt_override = Path(arg)
            i += 1
        else:
            non_flag_args.append(arg)
            i += 1

    if len(non_flag_args) > 0 and non_flag_args[0].isdigit():
        fold = int(non_flag_args[0])
    if len(non_flag_args) > 1 and non_flag_args[1].isdigit():
        seed = int(non_flag_args[1])

    return run_smoke_test(
        fold_idx=fold,
        seed=seed,
        checkpoint_path=ckpt_override,
        allow_train=allow_train,
    )


if __name__ == "__main__":
    main()
