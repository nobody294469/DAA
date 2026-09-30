"""recovery.py — Checkpoint discovery, quarantine, and recovery evaluation for M2 ClinicalVerifier.

Capabilities:
  1. Discover completed M2 checkpoints on disk.
  2. Quarantine stale debug checkpoints (e.g. modernbert_full_fold0_seed42.pt).
  3. Load and merge existing results from m2_train_results_recovered.json and m2_train_results.json
     with deduplication and precedence rules.
  4. Reconstruct the ClinicalVerifier model with correct architecture and layer freezing from metadata.
  5. Load weights from model_state.
  6. Reconstruct the exact validation fold and held-out OOD test set from split_manifest.json.
  7. Evaluate the model on validation fold and OOD set using evaluate_model_on_split.
  8. Return the identical result schema as train_one_fold().
  9. Compute the dry-run training plan (completed, recovery, training counts).
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Optional

import torch

from clindoc.m2.schema import M2Example, M2ModelConfig, LABEL_ORDER
from clindoc.m2.model import ClinicalVerifier, load_tokenizer
from clindoc.m2.evaluate import evaluate_model_on_split
from clindoc.m2.splits import get_fold_examples, get_ood_examples
from clindoc.m2.dataset import load_examples

DEFAULT_CHECKPOINT_DIR = Path("data/m2_results/checkpoints")
DEFAULT_DATA_DIR = Path("data/m2_verification")
DEFAULT_RECOVERED_FILE = Path("data/m2_results/m2_train_results_recovered.json")
DEFAULT_RESULTS_FILE = Path("data/m2_results/m2_train_results.json")
DEFAULT_HEADLINE_RECOVERED_FILE = Path("data/m2_results/m2_recovered_headline_results.json")

# Explicitly quarantined debug checkpoints that must NOT be treated as official completed runs
QUARANTINED_CHECKPOINT_NAMES = {
    "modernbert_full_fold0_seed42.pt",
    "ckpt_fold0_seed42_aux1_freeze0.pt",
}


def is_quarantined_checkpoint(ckpt_path: Path, debug: bool = False) -> bool:
    """Return True if checkpoint is a known debug or invalid artifact in normal runs."""
    if debug:
        return False
    return ckpt_path.name in QUARANTINED_CHECKPOINT_NAMES


def is_quarantined_result(item: dict, debug: bool = False) -> bool:
    """Return True if result entry is from a quarantined debug run."""
    if debug:
        return False
    mname = item.get("model_name")
    fold = item.get("fold")
    seed = item.get("seed")
    if (mname, fold, seed) == ("modernbert_full", 0, 42):
        ckpt_path = item.get("checkpoint_path", "")
        # If it points to the known debug checkpoint or has a 1-epoch debug score
        if "modernbert_full_fold0_seed42.pt" in ckpt_path or item.get("best_val_macro_f1", 1.0) < 0.5:
            return True
    return False


def parse_checkpoint_filename(path: Path) -> dict | None:
    """Parse model_name, fold, and seed from a standard M2 checkpoint filename."""
    m = re.match(r"^(.+)_fold(\d+)_seed(\d+)\.pt$", path.name)
    if not m:
        return None
    return {
        "model_name": m.group(1),
        "fold": int(m.group(2)),
        "seed": int(m.group(3)),
    }


def discover_checkpoints(
    checkpoint_dir: Path = DEFAULT_CHECKPOINT_DIR,
    include_quarantined: bool = False,
    debug: bool = False,
) -> list[Path]:
    """Discover completed M2 model checkpoints under checkpoint_dir."""
    if not checkpoint_dir.exists():
        return []
    ckpts = list(checkpoint_dir.rglob("*.pt"))

    def sort_key(p: Path):
        meta = parse_checkpoint_filename(p)
        if meta:
            return (meta["model_name"], meta["fold"], meta["seed"])
        return (p.name, 0, 0)

    valid_ckpts = [p for p in ckpts if parse_checkpoint_filename(p) is not None]
    if not include_quarantined:
        valid_ckpts = [p for p in valid_ckpts if not is_quarantined_checkpoint(p, debug=debug)]

    valid_ckpts.sort(key=sort_key)
    return valid_ckpts


def get_persist_dir() -> Path | None:
    """Return Path to persistent directory if M2_PERSIST_DIR environment variable is set."""
    env_dir = os.environ.get("M2_PERSIST_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip())
    return None


def deduplicate_results(results: list[dict]) -> list[dict]:
    """Deduplicate result records by (model_name, fold, seed), keeping the latest entry."""
    seen: dict[tuple, dict] = {}
    for r in results:
        mname = r.get("model_name") or r.get("variant")
        fold = r.get("fold")
        seed = r.get("seed")
        if mname is not None and fold is not None and seed is not None:
            seen[(mname, fold, seed)] = r
        else:
            seen[id(r)] = r
    return list(seen.values())


def load_existing_results_map(
    results_file: Path | None = None,
    recovered_file: Path | None = None,
    debug: bool = False,
    persist_dir: Path | str | None = None,
) -> dict[tuple[str, int, int], dict]:
    """Load and merge results from recovered_file, results_file, and persistent backup if present.
    
    Rules:
      - Quarantined debug results are filtered out in normal (non-debug) mode.
      - Recovered results provide completed evaluations for matching (model_name, fold, seed).
      - Persistent backup file (from M2_PERSIST_DIR) is checked if local results_file does not exist.
      - Official results_file takes precedence over recovered_file for duplicate keys.
      - Never creates duplicate records.
    """
    merged: dict[tuple[str, int, int], dict] = {}

    # 1. Load recovered results first
    if recovered_file and recovered_file.exists():
        try:
            with open(recovered_file, "r", encoding="utf-8") as f:
                rec_data = json.load(f)
            if isinstance(rec_data, list):
                for item in rec_data:
                    if is_quarantined_result(item, debug=debug):
                        continue
                    mname = item.get("model_name")
                    fold = item.get("fold")
                    seed = item.get("seed")
                    if mname is not None and fold is not None and seed is not None:
                        merged[(mname, fold, seed)] = item
        except Exception as e:
            print(f"Warning: Could not read recovered results from {recovered_file}: {e}")

    # 2. Check persistent directory backup if local results_file is missing
    p_dir = persist_dir if persist_dir is not None else get_persist_dir()
    p_dir_path = Path(p_dir) if (p_dir is not None and str(p_dir).strip()) else None

    target_results_file = results_file
    if target_results_file and not target_results_file.exists():
        if p_dir_path:
            candidate = p_dir_path / target_results_file.name
            if candidate.exists():
                target_results_file = candidate
            elif (p_dir_path / "m2_train_results.json").exists():
                target_results_file = p_dir_path / "m2_train_results.json"

    # 3. Load official results file (takes precedence over recovered)
    if target_results_file and target_results_file.exists():
        try:
            with open(target_results_file, "r", encoding="utf-8") as f:
                res_data = json.load(f)
            if isinstance(res_data, list):
                for item in res_data:
                    if is_quarantined_result(item, debug=debug):
                        continue
                    mname = item.get("model_name")
                    fold = item.get("fold")
                    seed = item.get("seed")
                    if mname is not None and fold is not None and seed is not None:
                        merged[(mname, fold, seed)] = item
        except Exception as e:
            print(f"Warning: Could not read official results from {target_results_file}: {e}")

    # 4. If a persistent directory exists, merge any records from persistent m2_train_results.json
    if p_dir_path:
        for fname in ([results_file.name] if results_file else []) + ["m2_train_results.json"]:
            persist_results_file = p_dir_path / fname
            if persist_results_file.exists() and persist_results_file != target_results_file:
                try:
                    with open(persist_results_file, "r", encoding="utf-8") as f:
                        p_res_data = json.load(f)
                    if isinstance(p_res_data, list):
                        for item in p_res_data:
                            if is_quarantined_result(item, debug=debug):
                                continue
                            mname = item.get("model_name")
                            fold = item.get("fold")
                            seed = item.get("seed")
                            if mname is not None and fold is not None and seed is not None:
                                key = (mname, fold, seed)
                                if key not in merged:
                                    merged[key] = item
                except Exception as e:
                    print(f"Warning: Could not read persistent results from {persist_results_file}: {e}")

    # 5. Check persist_dir/runs/*.json for any individual persisted run records
    if p_dir_path:
        runs_dir = p_dir_path / "runs"
        if runs_dir.is_dir():
            for single_path in sorted(runs_dir.glob("*.json")):
                try:
                    with open(single_path, "r", encoding="utf-8") as f:
                        item = json.load(f)
                    if isinstance(item, dict):
                        if is_quarantined_result(item, debug=debug):
                            continue
                        mname = item.get("model_name")
                        fold = item.get("fold")
                        seed = item.get("seed")
                        if mname is not None and fold is not None and seed is not None:
                            key = (mname, fold, seed)
                            if key not in merged:
                                merged[key] = item
                except Exception as e:
                    print(f"Warning: Could not read individual run result from {single_path}: {e}")

    return merged


def load_recovered_headline_results(
    headline_file: Path | None = None,
) -> dict[tuple[str, int, int], dict]:
    """Load recovered headline-only results from m2_recovered_headline_results.json.
    
    Returns a dict keyed by (variant, fold, seed).
    Each entry preserves the recovered headline metrics:
      - val_macro_f1
      - val_discrepancy_recall
      - ood_macro_f1
      - ood_discrepancy_recall
      - status: 'completed_recovered_headline'
      - detailed_metrics_available: False
      - checkpoint_available: False
    
    These records are distinct from full evaluation records and must never fabricate
    missing detailed metrics (AUROC, Brier, ECE, per_class, confusion matrix).
    """
    if headline_file is None:
        headline_file = DEFAULT_HEADLINE_RECOVERED_FILE

    results: dict[tuple[str, int, int], dict] = {}
    if not headline_file.exists():
        return results

    try:
        with open(headline_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        runs = data.get("runs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        for run in runs:
            var = run.get("variant") or run.get("model_name")
            fold = run.get("fold")
            seed = run.get("seed")
            if var is not None and fold is not None and seed is not None:
                record = dict(run)
                record["variant"] = var
                record["model_name"] = var
                record["fold"] = fold
                record["seed"] = seed
                record["status"] = run.get("status", "completed_recovered_headline")
                record["detailed_metrics_available"] = False
                record["checkpoint_available"] = False
                results[(var, fold, seed)] = record
    except Exception as e:
        print(f"Warning: Could not read headline recovered results from {headline_file}: {e}")

    return results


def recover_and_evaluate_checkpoint(
    checkpoint_path: Path,
    examples: list[M2Example],
    manifest: dict,
    ood_examples: list[M2Example] | None = None,
    tokenizer=None,
    device: str | None = None,
    config: M2ModelConfig | None = None,
) -> dict:
    """Load a checkpoint and evaluate it on its validation fold and OOD set."""
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    state_dict = torch.load(checkpoint_path, map_location="cpu", weights_only=False)

    ckpt_config = state_dict.get("config", {})
    if config is None:
        config = M2ModelConfig.model_validate(ckpt_config) if ckpt_config else M2ModelConfig()

    model_id = ckpt_config.get("model_id", config.model_id)
    aux_mode = state_dict.get("aux_mode", "full" if state_dict.get("use_aux", True) else "none")
    freeze_lower_n_layers = state_dict.get("freeze_lower_n_layers", 0)
    fold_idx = state_dict.get("fold", 0)
    seed = state_dict.get("seed", 42)
    model_name = state_dict.get("model_name")
    if not model_name:
        parsed = parse_checkpoint_filename(checkpoint_path)
        model_name = parsed["model_name"] if parsed else "modernbert_model"

    best_val_macro_f1 = state_dict.get("best_val_macro_f1", 0.0)

    if tokenizer is None:
        tokenizer = load_tokenizer(model_id)

    # Reconstruct model architecture matching checkpoint metadata
    model = ClinicalVerifier(
        model_id=model_id,
        aux_mode=aux_mode,
        freeze_lower_n_layers=freeze_lower_n_layers,
    )
    model.load_state_dict(state_dict["model_state"])
    model.to(device)
    model.eval()

    # Reconstruct validation fold (excluding OOD documents)
    ood_set = set(manifest["ood_doc_ids"])
    _, val_ex = get_fold_examples(examples, manifest, fold_idx)
    val_ex = [e for e in val_ex if e.source_doc_id not in ood_set]

    # Evaluate on CV validation split
    val_eval = evaluate_model_on_split(
        model=model,
        tokenizer=tokenizer,
        examples=val_ex,
        model_name=model_name,
        split="cv",
        fold=fold_idx,
        seed=seed,
        config=config,
        device=device,
        aux_mode=aux_mode,
    )

    # Evaluate on held-out OOD test set
    ood_eval = None
    if ood_examples:
        ood_eval = evaluate_model_on_split(
            model=model,
            tokenizer=tokenizer,
            examples=ood_examples,
            model_name=model_name,
            split="ood",
            fold=fold_idx,
            seed=seed,
            config=config,
            device=device,
            aux_mode=aux_mode,
        )

    # Clean up GPU memory
    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return {
        "model_name": model_name,
        "fold": fold_idx,
        "seed": seed,
        "best_val_macro_f1": best_val_macro_f1,
        "val_eval": val_eval.model_dump(),
        "ood_eval": ood_eval.model_dump() if ood_eval else None,
        "history": [],
        "checkpoint_path": str(checkpoint_path),
    }


def save_incremental_results(
    path: Path,
    results: list[dict],
    persist_dir: Path | str | None = None,
) -> Path | None:
    """Save results atomically so interruptions cannot corrupt the JSON file.
    
    If persist_dir is provided or M2_PERSIST_DIR environment variable is set,
    also saves a synchronized backup atomically into the persistent directory.
    
    Deduplicates results by (model_name, fold, seed) while preserving order,
    ensuring repeated saves never create duplicate records.
    
    Returns the persistent file path if backup was written, else None.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    deduped = deduplicate_results(results)
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(deduped, f, indent=2)
    temp_path.replace(path)

    # Persistent backup (e.g. Google Drive when running in Colab)
    target_persist = None
    if persist_dir is None:
        target_persist = get_persist_dir()
    elif isinstance(persist_dir, str):
        target_persist = Path(persist_dir.strip()) if persist_dir.strip() else None
    else:
        target_persist = persist_dir

    if target_persist is not None:
        try:
            target_persist.mkdir(parents=True, exist_ok=True)
            persist_file = target_persist / path.name
            persist_temp = persist_file.with_suffix(".tmp")
            with open(persist_temp, "w", encoding="utf-8") as f:
                json.dump(deduped, f, indent=2)
            persist_temp.replace(persist_file)

            # Also persist the latest individual completed run record
            if deduped and isinstance(deduped[-1], dict):
                last = deduped[-1]
                mname = last.get("model_name") or last.get("variant")
                fold = last.get("fold")
                seed = last.get("seed")
                if mname is not None and fold is not None and seed is not None:
                    runs_dir = target_persist / "runs"
                    runs_dir.mkdir(parents=True, exist_ok=True)
                    single_file = runs_dir / f"{mname}_fold{fold}_seed{seed}.json"
                    single_temp = single_file.with_suffix(".tmp")
                    with open(single_temp, "w", encoding="utf-8") as f:
                        json.dump(last, f, indent=2)
                    single_temp.replace(single_file)

            return persist_file
        except Exception as e:
            print(f"Warning: Failed to backup results to persistent directory {target_persist}: {e}")
            return None

    return None


def get_training_plan(
    manifest: dict,
    config: M2ModelConfig,
    out_dir: Path = DEFAULT_CHECKPOINT_DIR,
    debug: bool = False,
    results_file: Path | None = None,
    recovered_file: Path | None = None,
    headline_file: Path | None = None,
    persist_dir: Path | str | None = None,
    variants_filter: list[str] | str | None = None,
) -> dict:
    """Compute the planned run counts broken down by variant without running training."""
    if results_file is None:
        results_file = out_dir.parent / ("m2_train_debug_results.json" if debug else "m2_train_results.json")
    if recovered_file is None:
        recovered_file = out_dir.parent / "m2_train_results_recovered.json"
    if headline_file is None:
        headline_file = out_dir.parent / "m2_recovered_headline_results.json"

    # Official completed results from active training runs (checking persistent backup if local missing)
    official_results_map = load_existing_results_map(
        results_file=results_file,
        recovered_file=None,
        debug=debug,
        persist_dir=persist_dir,
    )
    # Recovered headline-only results from preserved console outputs
    headline_results_map = load_recovered_headline_results(headline_file)
    # Older recovered checkpoint results (fallback)
    ckpt_recovered_map = load_existing_results_map(
        results_file=None,
        recovered_file=recovered_file,
        debug=debug,
        persist_dir=persist_dir,
    )

    n_folds = 1 if debug else manifest["n_splits"]
    seeds = [config.seeds[0]] if debug else config.seeds

    variants = [
        {"name": "modernbert_text_only", "aux_mode": "none", "freeze": 0},
        {"name": "modernbert_ocr_only", "aux_mode": "ocr_only", "freeze": 0},
        {"name": "modernbert_full", "aux_mode": "full", "freeze": 0},
        {"name": "modernbert_frozen_lower8", "aux_mode": "full", "freeze": config.frozen_variant_n_layers},
    ]
    if debug:
        variants = [variants[2]]
    elif variants_filter:
        if isinstance(variants_filter, str):
            filter_set = {variants_filter}
        else:
            filter_set = set(variants_filter)
        variants = [v for v in variants if v["name"] in filter_set]

    plan_by_variant = {}
    total_training = 0
    total_recovery = 0
    total_completed = 0
    total_headline_recovered = 0

    for var in variants:
        var_name = var["name"]
        var_dir = out_dir / var_name
        v_plan = {
            "completed": 0,
            "headline_recovered": 0,
            "recovery": 0,
            "training": 0,
        }

        for fold_idx in range(n_folds):
            for seed in seeds:
                run_key = (var_name, fold_idx, seed)
                ckpt_name = f"{var_name}_fold{fold_idx}_seed{seed}.pt"
                ckpt_path = var_dir / ckpt_name

                if run_key in official_results_map:
                    v_plan["completed"] += 1
                elif run_key in headline_results_map:
                    v_plan["headline_recovered"] += 1
                elif run_key in ckpt_recovered_map:
                    v_plan["completed"] += 1
                elif ckpt_path.exists() and not is_quarantined_checkpoint(ckpt_path, debug=debug):
                    v_plan["recovery"] += 1
                else:
                    v_plan["training"] += 1

        plan_by_variant[var_name] = v_plan
        total_training += v_plan["training"]
        total_recovery += v_plan["recovery"]
        total_completed += v_plan["completed"]
        total_headline_recovered += v_plan["headline_recovered"]

    return {
        "by_variant": plan_by_variant,
        "total_completed": total_completed,
        "total_headline_recovered": total_headline_recovered,
        "total_recovery": total_recovery,
        "total_training": total_training,
        "official_results_count": len(official_results_map),
        "headline_results_count": len(headline_results_map),
        "existing_results_count": len(official_results_map) + len(headline_results_map),
    }


def recover_checkpoints(
    checkpoint_dir: Path = DEFAULT_CHECKPOINT_DIR,
    data_dir: Path = DEFAULT_DATA_DIR,
    out_file: Path = DEFAULT_RECOVERED_FILE,
    max_recoveries: int | None = None,
    variant_filter: str | None = None,
    debug: bool = False,
) -> list[dict]:
    """Recover and evaluate checkpoints without retraining."""
    ckpts = discover_checkpoints(checkpoint_dir, include_quarantined=False, debug=debug)
    if variant_filter:
        ckpts = [p for p in ckpts if variant_filter in p.name]

    print(f"Discovered {len(ckpts)} valid non-quarantined checkpoints in {checkpoint_dir}")
    if not ckpts:
        return []

    examples = load_examples(data_dir / "all_examples.jsonl")
    with open(data_dir / "split_manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)
    ood_ex = get_ood_examples(examples, manifest)

    if max_recoveries is not None:
        ckpts = ckpts[:max_recoveries]
        print(f"Limiting recovery evaluation to {len(ckpts)} checkpoint(s)")

    tokenizer = None
    results = []
    for i, ckpt_path in enumerate(ckpts):
        print(f"\n[{i+1}/{len(ckpts)}] Recovering {ckpt_path.name}...")
        if tokenizer is None:
            tokenizer = load_tokenizer()
        res = recover_and_evaluate_checkpoint(
            checkpoint_path=ckpt_path,
            examples=examples,
            manifest=manifest,
            ood_examples=ood_ex,
            tokenizer=tokenizer,
        )
        print(f"  * [{res['model_name']} F{res['fold']} S{res['seed']}] Val Macro-F1: {res['val_eval']['macro_f1']:.4f} | Disc Recall: {res['val_eval']['discrepancy_recall']:.4f}")
        if res.get("ood_eval"):
            print(f"    OOD Macro-F1: {res['ood_eval']['macro_f1']:.4f} | Disc Recall: {res['ood_eval']['discrepancy_recall']:.4f}")
        results.append(res)
        save_incremental_results(out_file, results)

    print(f"\nRecovery complete. {len(results)} evaluated results saved to {out_file}")
    return results


def main():
    parser = argparse.ArgumentParser(description="M2 Checkpoint Recovery Utility")
    parser.add_argument("--list-only", action="store_true", help="List discovered checkpoints without evaluating")
    parser.add_argument("--smoke-test", action="store_true", help="Recover and evaluate ONE checkpoint only as smoke test")
    parser.add_argument("--variant", type=str, default=None, help="Filter checkpoints by variant name (e.g. modernbert_text_only)")
    parser.add_argument("--out-file", type=str, default=str(DEFAULT_RECOVERED_FILE), help="Output JSON path")

    args = parser.parse_args()

    ckpts = discover_checkpoints(include_quarantined=False)
    if args.variant:
        ckpts = [p for p in ckpts if args.variant in p.name]

    print(f"Found {len(ckpts)} valid non-quarantined checkpoints:")
    for p in ckpts:
        meta = parse_checkpoint_filename(p)
        print(f"  - {p.name} (model={meta['model_name']}, fold={meta['fold']}, seed={meta['seed']})")

    if args.list_only:
        return

    if args.smoke_test:
        print("\nRunning smoke-test recovery on the first checkpoint...")
        recover_checkpoints(max_recoveries=1, variant_filter=args.variant, out_file=Path(args.out_file))
    else:
        recover_checkpoints(variant_filter=args.variant, out_file=Path(args.out_file))


if __name__ == "__main__":
    main()
