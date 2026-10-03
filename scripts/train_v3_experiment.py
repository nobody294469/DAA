"""train_v3_experiment.py — First Stage B ModernBERT training experiment on V3 benchmark.

Dataset: data/m2_verification_v3/
Train: 2242 examples
Validation: 883 examples
Calibration: 678 examples
OOD: 1826 examples
Model: thomas-sounack/BioClinical-ModernBERT-base
Task: 3-class verification (SUPPORTED, DISCREPANCY, INSUFFICIENT_EVIDENCE)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import torch

from clindoc.m2.schema import M2ModelConfig, LABEL_ORDER
from clindoc.m2.dataset import load_examples
from clindoc.m2.splits import (
    load_train_examples,
    load_validation_examples,
    load_calibration_examples,
    load_ood_examples,
)
from clindoc.m2.train import train_one_fold


def sha256_file(path: Path | str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    print("=" * 70)
    print("STAGE B ModernBERT Training Experiment — V3 Benchmark")
    print("=" * 70)

    # 1. Dataset paths and strict hash verification
    v3_dir = Path("data/m2_verification_v3")
    v3_examples_file = v3_dir / "all_examples.jsonl"
    v3_manifest_file = v3_dir / "split_manifest.json"

    v2_dir = Path("data/m2_verification")
    v2_examples_file = v2_dir / "all_examples.jsonl"
    v2_manifest_file = v2_dir / "split_manifest.json"

    expected_v3_examples_hash = "40278c01848875382931a8a01397982ff6c00dfb7f588c30666b1ca8a66d6d16"
    expected_v3_manifest_hash = "cf41cc1d7e889f14cdcecf9ca2c411d54f2b737ddcfa280f6852b739050d2067"
    expected_v2_examples_hash = "ce924fa3921eedf2482c29279579ed010036273e608c399f38ce291c8306a2a8"
    expected_v2_manifest_hash = "8a1ebd157188c4c4032dab297df4c53f9d2fc1dd58a52596a368cc7c676e4393"

    v3_examples_hash = sha256_file(v3_examples_file)
    v3_manifest_hash = sha256_file(v3_manifest_file)
    v2_examples_hash = sha256_file(v2_examples_file)
    v2_manifest_hash = sha256_file(v2_manifest_file)

    assert v3_examples_hash == expected_v3_examples_hash, f"V3 examples hash mismatch: {v3_examples_hash}"
    assert v3_manifest_hash == expected_v3_manifest_hash, f"V3 manifest hash mismatch: {v3_manifest_hash}"
    assert v2_examples_hash == expected_v2_examples_hash, f"V2 historical examples modified: {v2_examples_hash}"
    assert v2_manifest_hash == expected_v2_manifest_hash, f"V2 historical manifest modified: {v2_manifest_hash}"

    print(f"V3 all_examples.jsonl SHA256: {v3_examples_hash} (VERIFIED)")
    print(f"V3 split_manifest.json SHA256: {v3_manifest_hash} (VERIFIED)")
    print(f"Historical V2 benchmark SHA256: {v2_examples_hash[:16]}... (UNTOUCHED)")

    # 2. Load dataset partitions
    print("\nLoading dataset partitions from V3 benchmark...")
    examples = load_examples(v3_examples_file)
    with open(v3_manifest_file, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    train_examples = load_train_examples(examples, manifest)
    val_examples = load_validation_examples(examples, manifest)
    calib_examples = load_calibration_examples(examples, manifest)
    ood_examples = load_ood_examples(examples, manifest)

    assert len(train_examples) == 2242, f"Expected 2242 train examples, got {len(train_examples)}"
    assert len(val_examples) == 883, f"Expected 883 val examples, got {len(val_examples)}"
    assert len(calib_examples) == 678, f"Expected 678 calib examples, got {len(calib_examples)}"
    assert len(ood_examples) == 1826, f"Expected 1826 ood examples, got {len(ood_examples)}"

    print(f"  Train:       {len(train_examples)} examples (source docs: {len(set(e.source_doc_id for e in train_examples))})")
    print(f"  Validation:  {len(val_examples)} examples (source docs: {len(set(e.source_doc_id for e in val_examples))})")
    print(f"  Calibration: {len(calib_examples)} examples (source docs: {len(set(e.source_doc_id for e in calib_examples))})")
    print(f"  OOD:         {len(ood_examples)} examples (source docs: {len(set(e.source_doc_id for e in ood_examples))})")

    # Document-level disjointness check
    train_docs = set(e.source_doc_id for e in train_examples)
    val_docs = set(e.source_doc_id for e in val_examples)
    calib_docs = set(e.source_doc_id for e in calib_examples)
    ood_docs = set(e.source_doc_id for e in ood_examples)

    assert train_docs.isdisjoint(val_docs), "Train and Val leak docs"
    assert train_docs.isdisjoint(calib_docs), "Train and Calib leak docs"
    assert val_docs.isdisjoint(calib_docs), "Val and Calib leak docs"
    assert train_docs.isdisjoint(ood_docs), "Train and OOD leak docs"
    assert val_docs.isdisjoint(ood_docs), "Val and OOD leak docs"
    assert calib_docs.isdisjoint(ood_docs), "Calib and OOD leak docs"
    print("  Document-level partition isolation: VERIFIED (0 leakage)")

    # 3. Model Configuration
    config = M2ModelConfig(
        model_id="thomas-sounack/BioClinical-ModernBERT-base",
        num_labels=3,
        max_seq_length=256,
        encoder_lr=2e-5,
        head_lr=1e-4,
        weight_decay=0.01,
        warmup_fraction=0.06,
        max_epochs=10,
        early_stopping_patience=3,
        batch_size=16,
        seeds=[42],
        freeze_lower_n_layers=0,
        use_aux=False,
    )

    print("\nModel & Training Configuration:")
    print(f"  model_id:               {config.model_id}")
    print(f"  aux_mode:               none")
    print(f"  freeze_lower_n_layers:  0")
    print(f"  seed:                   42")
    print(f"  batch_size:             {config.batch_size}")
    print(f"  max_epochs:             {config.max_epochs}")
    print(f"  early_stopping_patience:{config.early_stopping_patience}")
    print(f"  encoder_lr:             {config.encoder_lr}")
    print(f"  head_lr:                {config.head_lr}")
    print(f"  weight_decay:           {config.weight_decay}")
    print(f"  warmup_fraction:        {config.warmup_fraction}")
    print(f"  max_seq_length:         {config.max_seq_length}")
    print(f"  CUDA available:         {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  Device Name:            {torch.cuda.get_device_name(0)}")

    # 4. Checkpoint & Results Directory
    results_dir = Path("data/m2_results_v3")
    ckpt_dir = results_dir / "checkpoints"
    results_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    # 5. Run Training
    print("\nStarting Stage B Training...")
    start_time = time.time()
    result = train_one_fold(
        train_examples=train_examples,
        val_examples=val_examples,
        config=config,
        fold_idx=0,
        seed=42,
        checkpoint_dir=ckpt_dir,
        debug=False,
        aux_mode="none",
        freeze_lower_n_layers=0,
        model_name="modernbert_text_only_v3",
        ood_examples=ood_examples,
        calib_examples=calib_examples,
    )
    elapsed_time = time.time() - start_time
    print(f"\nTraining completed in {elapsed_time:.1f} seconds ({elapsed_time/60:.2f} minutes)")

    # 6. Extract and Format Output Metrics
    ckpt_path = Path(result["checkpoint_path"])
    ckpt_size_bytes = ckpt_path.stat().st_size
    ckpt_size_mb = ckpt_size_bytes / (1024 * 1024)

    val_eval = result["val_eval"]
    cm_val = val_eval["confusion_matrix"]
    total_val = sum(sum(r) for r in cm_val)
    correct_val = sum(cm_val[i][i] for i in range(len(cm_val)))
    val_accuracy = correct_val / total_val if total_val > 0 else 0.0

    ood_eval = result.get("ood_eval")
    ood_accuracy = None
    if ood_eval:
        cm_ood = ood_eval["confusion_matrix"]
        total_ood = sum(sum(r) for r in cm_ood)
        correct_ood = sum(cm_ood[i][i] for i in range(len(cm_ood)))
        ood_accuracy = correct_ood / total_ood if total_ood > 0 else 0.0

    # Best epoch identification from history
    history = result["history"]
    best_epoch_info = max(history, key=lambda h: h["val_macro_f1"])
    best_epoch = best_epoch_info["epoch"]

    # Post-hoc calibration info
    calib_metrics = result.get("calib_metrics", {})
    temp_T = result["calibration_temperature"]

    output_payload = {
        "git_commit": "a6a13d12001ea35342b20526b5ae8882bb977566",
        "dataset_name": "data/m2_verification_v3",
        "dataset_hashes": {
            "all_examples_sha256": v3_examples_hash,
            "split_manifest_sha256": v3_manifest_hash,
            "historical_v2_all_examples_sha256": v2_examples_hash,
            "historical_v2_split_manifest_sha256": v2_manifest_hash,
        },
        "partition_counts": {
            "train": len(train_examples),
            "validation": len(val_examples),
            "calibration": len(calib_examples),
            "ood": len(ood_examples),
            "total": len(examples),
        },
        "training_config": {
            "model_id": config.model_id,
            "aux_mode": "none",
            "freeze_lower_n_layers": 0,
            "seed": 42,
            "batch_size": config.batch_size,
            "max_epochs": config.max_epochs,
            "early_stopping_patience": config.early_stopping_patience,
            "encoder_lr": config.encoder_lr,
            "head_lr": config.head_lr,
            "weight_decay": config.weight_decay,
            "warmup_fraction": config.warmup_fraction,
            "max_seq_length": config.max_seq_length,
            "device": "cuda" if torch.cuda.is_available() else "cpu",
            "device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        },
        "training_execution": {
            "elapsed_seconds": round(elapsed_time, 2),
            "best_epoch": best_epoch,
            "best_epoch_train_loss": best_epoch_info.get("loss"),
            "best_epoch_val_loss": best_epoch_info.get("val_loss"),
            "best_val_macro_f1": result["best_val_macro_f1"],
            "history": history,
        },
        "validation_metrics": {
            "accuracy": round(val_accuracy, 4),
            "macro_f1": val_eval["macro_f1"],
            "discrepancy_recall": val_eval["discrepancy_recall"],
            "auroc_discrepancy": val_eval.get("auroc_discrepancy"),
            "brier_score": val_eval.get("brier_score"),
            "ece": val_eval.get("ece"),
            "confusion_matrix": cm_val,
            "per_class": val_eval["per_class"],
        },
        "calibration": {
            "partition": "data/m2_verification_v3 (calibration split, 678 examples)",
            "validation_used_for_fitting": False,
            "learned_temperature_T": temp_T,
            "metrics": calib_metrics,
        },
        "ood_metrics": {
            "accuracy": round(ood_accuracy, 4) if ood_accuracy is not None else None,
            "macro_f1": ood_eval["macro_f1"] if ood_eval else None,
            "discrepancy_recall": ood_eval["discrepancy_recall"] if ood_eval else None,
            "auroc_discrepancy": ood_eval.get("auroc_discrepancy") if ood_eval else None,
            "brier_score": ood_eval.get("brier_score") if ood_eval else None,
            "ece": ood_eval.get("ece") if ood_eval else None,
            "confusion_matrix": ood_eval["confusion_matrix"] if ood_eval else None,
            "per_class": ood_eval["per_class"] if ood_eval else None,
        } if ood_eval else None,
        "checkpoint": {
            "path": str(ckpt_path),
            "size_bytes": ckpt_size_bytes,
            "size_mb": round(ckpt_size_mb, 2),
            "trained_from_v3_train_partition": True,
            "calibration_used_only_v3_calibration_partition": True,
            "validation_not_used_for_temperature_fitting": True,
        }
    }

    results_json_file = results_dir / "v3_experiment_metrics.json"
    with open(results_json_file, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2)

    print("\n" + "=" * 70)
    print("EXPERIMENT SUMMARY")
    print("=" * 70)
    print(f"Checkpoint:               {ckpt_path} ({ckpt_size_mb:.2f} MB)")
    print(f"Best Epoch:               {best_epoch}")
    print(f"Train Loss @ Best Epoch:  {best_epoch_info.get('loss'):.4f}")
    print(f"Val Loss @ Best Epoch:    {best_epoch_info.get('val_loss'):.4f}")
    print(f"Val Accuracy:             {val_accuracy:.4f}")
    print(f"Val Macro-F1:             {val_eval['macro_f1']:.4f}")
    print(f"Val Discrepancy Recall:   {val_eval['discrepancy_recall']:.4f}")
    print(f"Val ECE (post-calib):     {val_eval.get('ece'):.4f}")
    print(f"Val Brier Score:          {val_eval.get('brier_score'):.4f}")
    print(f"Val AUROC Discrepancy:    {val_eval.get('auroc_discrepancy')}")
    print(f"Learned Temperature T:    {temp_T:.6f}")
    if calib_metrics:
        print(f"Calib ECE before -> after: {calib_metrics.get('ece_before', 0):.4f} -> {calib_metrics.get('ece_after', 0):.4f}")
        print(f"Calib NLL before -> after: {calib_metrics.get('nll_before', 0):.4f} -> {calib_metrics.get('nll_after', 0):.4f}")
    if ood_eval:
        print(f"OOD Accuracy:             {ood_accuracy:.4f}")
        print(f"OOD Macro-F1:             {ood_eval['macro_f1']:.4f}")
        print(f"OOD Discrepancy Recall:   {ood_eval['discrepancy_recall']:.4f}")
        print(f"OOD ECE:                  {ood_eval.get('ece'):.4f}")
    print(f"Results JSON:             {results_json_file}")
    print("=" * 70)


if __name__ == "__main__":
    main()
