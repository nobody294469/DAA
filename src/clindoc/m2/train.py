"""M2 training loop for ClinicalVerifier.

Features:
  - AdamW with differential learning rate (encoder 2e-5, head 1e-4)
  - Linear warmup (6% of steps) then cosine decay
  - Mixed precision (torch.amp.autocast + GradScaler) on CUDA
  - Early stopping on val macro-F1 (patience 3)
  - Full evaluation on val fold and held-out OOD test set
  - Reproducible via seed
  - Saves best checkpoint per fold+seed
"""
from __future__ import annotations

import json
import random
from functools import partial
from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from clindoc.m2.schema import M2Example, M2ModelConfig, M2EvalResult, LABEL_ORDER
from clindoc.m2.model import ClinicalVerifier, load_tokenizer, format_input
from clindoc.m2.features import encode_aux_batch
from clindoc.m2.metrics import macro_f1
from clindoc.m2.evaluate import evaluate_model_on_split

LABEL_TO_INT_LOCAL = {lbl: i for i, lbl in enumerate(LABEL_ORDER)}


class M2Dataset(Dataset):
    def __init__(self, examples: list[M2Example], tokenizer, max_length: int = 256):
        self.examples = examples
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.examples)

    def __getitem__(self, idx):
        return self.examples[idx]


def collate_fn(
    batch: list[M2Example],
    tokenizer,
    max_length: int,
    device: str,
    aux_mode: str = "full",
):
    texts = [format_input(ex.field_type, ex.claim, ex.evidence) for ex in batch]
    encoded = tokenizer(
        texts, padding=True, truncation=True,
        max_length=max_length, return_tensors="pt"
    )
    input_ids = encoded["input_ids"].to(device)
    attention_mask = encoded["attention_mask"].to(device)

    aux_features = None
    if aux_mode != "none":
        raw_aux = encode_aux_batch(
            [ex.ocr_confidence for ex in batch],
            [ex.field_type for ex in batch],
            [ex.doc_template for ex in batch],
            mode=aux_mode,
        )
        if raw_aux is not None:
            aux_features = raw_aux.to(device)

    labels = torch.tensor(
        [LABEL_TO_INT_LOCAL[ex.label.value] for ex in batch],
        dtype=torch.long, device=device,
    )
    return input_ids, attention_mask, aux_features, labels


def get_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def get_scheduler(optimizer, num_warmup_steps: int, num_training_steps: int):
    from torch.optim.lr_scheduler import LambdaLR
    import math

    def lr_lambda(current_step):
        if current_step < num_warmup_steps:
            return float(current_step) / float(max(1, num_warmup_steps))
        progress = float(current_step - num_warmup_steps) / float(
            max(1, num_training_steps - num_warmup_steps)
        )
        return max(0.0, 0.5 * (1.0 + math.cos(math.pi * progress)))

    return LambdaLR(optimizer, lr_lambda)


def train_one_fold(
    train_examples: list[M2Example],
    val_examples: list[M2Example],
    config: M2ModelConfig,
    fold_idx: int,
    seed: int,
    checkpoint_dir: Path,
    debug: bool = False,
    aux_mode: str = "full",
    freeze_lower_n_layers: int = 0,
    model_name: str = "modernbert_full",
    ood_examples: list[M2Example] | None = None,
) -> dict:
    """Train ClinicalVerifier on one fold. Returns dict with val_eval, ood_eval, history."""
    set_seed(seed)
    device = get_device()
    print(f"\n  [{model_name} | fold={fold_idx}, seed={seed}] Device: {device} | aux_mode={aux_mode} | freeze={freeze_lower_n_layers}")
    print(f"  Train: {len(train_examples)} | Val: {len(val_examples)}")

    tokenizer = load_tokenizer(config.model_id)
    model = ClinicalVerifier(
        model_id=config.model_id,
        aux_mode=aux_mode,
        freeze_lower_n_layers=freeze_lower_n_layers,
    ).to(device)
    print(f"  Trainable params: {model.n_trainable_params:,}")

    if debug:
        train_examples = train_examples[:48]
        val_examples = val_examples[:24]

    batch_size = 8 if debug else config.batch_size
    max_epochs = 1 if debug else config.max_epochs

    # Differential LR: encoder gets lower LR
    encoder_params = [p for n, p in model.named_parameters()
                      if "encoder" in n and p.requires_grad]
    head_params = [p for n, p in model.named_parameters()
                   if "encoder" not in n and p.requires_grad]
    optimizer = torch.optim.AdamW([
        {"params": encoder_params, "lr": config.encoder_lr},
        {"params": head_params,    "lr": config.head_lr},
    ], weight_decay=config.weight_decay)

    steps_per_epoch = max(1, len(train_examples) // batch_size)
    total_steps = steps_per_epoch * max_epochs
    warmup_steps = max(1, int(config.warmup_fraction * total_steps))
    scheduler = get_scheduler(optimizer, warmup_steps, total_steps)
    criterion = nn.CrossEntropyLoss()
    scaler = torch.amp.GradScaler("cuda") if device == "cuda" else None

    best_val_f1 = -1.0
    best_state = None
    patience_counter = 0
    history = []

    collate = partial(
        collate_fn,
        tokenizer=tokenizer,
        max_length=config.max_seq_length,
        device=device,
        aux_mode=aux_mode,
    )

    for epoch in range(max_epochs):
        model.train()
        random.shuffle(train_examples)
        epoch_loss = 0.0
        n_batches = 0
        for start in range(0, len(train_examples), batch_size):
            batch = train_examples[start: start + batch_size]
            if not batch:
                continue
            input_ids, attn_mask, aux_feat, labels = collate(batch)
            optimizer.zero_grad()
            if device == "cuda" and scaler is not None:
                with torch.amp.autocast("cuda", dtype=torch.float16):
                    logits = model(input_ids, attn_mask, aux_features=aux_feat)
                    loss = criterion(logits, labels)
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                scale_before = scaler.get_scale()
                scaler.step(optimizer)
                scaler.update()
                scale_after = scaler.get_scale()
                if scale_before <= scale_after:
                    scheduler.step()
            else:
                logits = model(input_ids, attn_mask, aux_features=aux_feat)
                loss = criterion(logits, labels)
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
                scheduler.step()
            epoch_loss += loss.item()
            n_batches += 1

        avg_loss = epoch_loss / max(n_batches, 1)

        # Validation pass
        model.eval()
        y_true, y_pred = [], []
        eval_batch_size = 64 if not debug else 4
        with torch.no_grad():
            for start in range(0, len(val_examples), eval_batch_size):
                batch = val_examples[start: start + eval_batch_size]
                if not batch:
                    continue
                input_ids, attn_mask, aux_feat, labels = collate(batch)
                if device == "cuda":
                    with torch.amp.autocast("cuda", dtype=torch.float16):
                        logits = model(input_ids, attn_mask, aux_features=aux_feat)
                else:
                    logits = model(input_ids, attn_mask, aux_features=aux_feat)
                preds = logits.argmax(dim=-1).cpu().tolist()
                y_true.extend(labels.cpu().tolist())
                y_pred.extend(preds)

        val_f1 = macro_f1(y_true, y_pred)
        print(f"    Epoch {epoch+1:2d}/{max_epochs}: loss={avg_loss:.4f} | val_macro_f1={val_f1:.4f}")
        history.append({"epoch": epoch + 1, "loss": avg_loss, "val_macro_f1": val_f1})

        if val_f1 > best_val_f1:
            best_val_f1 = val_f1
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1
            if not debug and patience_counter >= config.early_stopping_patience:
                print(f"    -> Early stopping at epoch {epoch+1} (best val macro-F1: {best_val_f1:.4f})")
                break

    # Restore best checkpoint
    if best_state is not None:
        model.load_state_dict(best_state)

    # Save checkpoint
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    ckpt_name = f"{model_name}_fold{fold_idx}_seed{seed}.pt"
    ckpt_path = checkpoint_dir / ckpt_name
    if best_state is not None:
        torch.save({
            "model_state": best_state,
            "config": config.model_dump(),
            "aux_mode": aux_mode,
            "freeze_lower_n_layers": freeze_lower_n_layers,
            "fold": fold_idx,
            "seed": seed,
            "best_val_macro_f1": best_val_f1,
            "model_name": model_name,
        }, ckpt_path)

    # Comprehensive evaluation using evaluate_model_on_split
    val_eval = evaluate_model_on_split(
        model, tokenizer, val_examples,
        model_name=model_name, split="cv",
        fold=fold_idx, seed=seed, config=config,
        device=device, aux_mode=aux_mode,
    )
    print(f"  * [{model_name} F{fold_idx} S{seed}] Val Macro-F1: {val_eval.macro_f1:.4f} | Disc Recall: {val_eval.discrepancy_recall:.4f}")

    ood_eval = None
    if ood_examples:
        ood_eval = evaluate_model_on_split(
            model, tokenizer, ood_examples,
            model_name=model_name, split="ood",
            fold=fold_idx, seed=seed, config=config,
            device=device, aux_mode=aux_mode,
        )
        print(f"    OOD Macro-F1: {ood_eval.macro_f1:.4f} | Disc Recall: {ood_eval.discrepancy_recall:.4f}")

    # Clean up GPU memory
    del model
    del optimizer
    del scheduler
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return {
        "model_name": model_name,
        "fold": fold_idx,
        "seed": seed,
        "best_val_macro_f1": best_val_f1,
        "val_eval": val_eval.model_dump(),
        "ood_eval": ood_eval.model_dump() if ood_eval else None,
        "history": history,
        "checkpoint_path": str(ckpt_path),
    }


def run_training(
    examples: list[M2Example],
    manifest: dict,
    config: M2ModelConfig,
    out_dir: Path,
    debug: bool = False,
    ood_examples: list[M2Example] | None = None,
    results_file: Path | None = None,
    recovered_file: Path | None = None,
    headline_file: Path | None = None,
    persist_dir: Path | str | None = None,
) -> list[dict]:
    """Run full CV training for all 4 neural variants across 5 folds x 3 seeds with resumability and persistent backup."""
    from clindoc.m2.recovery import (
        recover_and_evaluate_checkpoint,
        save_incremental_results,
        load_existing_results_map,
        load_recovered_headline_results,
        is_quarantined_checkpoint,
        get_persist_dir,
    )

    if results_file is None:
        results_file = out_dir.parent / ("m2_train_debug_results.json" if debug else "m2_train_results.json")
    if recovered_file is None:
        recovered_file = out_dir.parent / "m2_train_results_recovered.json"
    if headline_file is None:
        headline_file = out_dir.parent / "m2_recovered_headline_results.json"
    if persist_dir is None:
        persist_dir = get_persist_dir()

    if persist_dir:
        print(f"Persistent backup enabled -> will sync completed results to {persist_dir}")

    # Merge recovered and official existing results (including persistent backup if present)
    existing_results_map = load_existing_results_map(
        results_file=results_file,
        recovered_file=recovered_file,
        debug=debug,
        persist_dir=persist_dir,
    )
    if existing_results_map:
        print(f"Loaded {len(existing_results_map)} valid completed result(s) from previous runs")

    # Load recovered headline results separately
    headline_results_map = load_recovered_headline_results(headline_file=headline_file)
    if headline_results_map:
        print(f"Loaded {len(headline_results_map)} historical headline-recovered result(s) (checkpoints/detailed metrics not available)")

    results = []
    n_folds = 1 if debug else manifest["n_splits"]
    seeds = [config.seeds[0]] if debug else config.seeds

    variants = [
        {"name": "modernbert_text_only", "aux_mode": "none", "freeze": 0},
        {"name": "modernbert_ocr_only", "aux_mode": "ocr_only", "freeze": 0},
        {"name": "modernbert_full", "aux_mode": "full", "freeze": 0},
        {"name": "modernbert_frozen_lower8", "aux_mode": "full", "freeze": config.frozen_variant_n_layers},
    ]
    if debug:
        variants = [variants[2]]  # Only full model in debug

    from clindoc.m2.splits import get_fold_examples
    ood_set = set(manifest["ood_doc_ids"])
    tokenizer = None

    for var in variants:
        var_dir = out_dir / var["name"]
        print(f"\n=======================================================")
        print(f"=== Neural Variant: {var['name']} (aux_mode={var['aux_mode']}, freeze={var['freeze']}) ===")
        print(f"=======================================================")
        for fold_idx in range(n_folds):
            train_ex, val_ex = get_fold_examples(examples, manifest, fold_idx)
            # Ensure OOD examples are not in train or val
            train_ex = [e for e in train_ex if e.source_doc_id not in ood_set]
            val_ex = [e for e in val_ex if e.source_doc_id not in ood_set]

            for seed in seeds:
                run_key = (var["name"], fold_idx, seed)
                ckpt_name = f"{var['name']}_fold{fold_idx}_seed{seed}.pt"
                ckpt_path = var_dir / ckpt_name

                # 1. Result already recorded in official existing_results_map
                if run_key in existing_results_map and run_key not in headline_results_map:
                    print(f"  [SKIP] Full result already recorded for {var['name']} fold={fold_idx} seed={seed}")
                    results.append(existing_results_map[run_key])
                    save_incremental_results(results_file, results, persist_dir=persist_dir)
                    continue

                # 2. Historical headline-recovered result (do not retrain, do not load missing checkpoint)
                if run_key in headline_results_map:
                    print(f"  [SKIP] Historical headline-recovered result recognized for {var['name']} fold={fold_idx} seed={seed} (val_f1={headline_results_map[run_key]['val_macro_f1']}, ood_f1={headline_results_map[run_key]['ood_macro_f1']}; will not retrain)")
                    continue

                # 3. Checkpoint already exists on disk and is not quarantined -> recover evaluation
                if ckpt_path.exists() and not is_quarantined_checkpoint(ckpt_path, debug=debug):
                    print(f"  [RESUME] Found existing checkpoint {ckpt_path.name}, recovering evaluation...")
                    if tokenizer is None:
                        tokenizer = load_tokenizer(config.model_id)
                    res = recover_and_evaluate_checkpoint(
                        checkpoint_path=ckpt_path,
                        examples=examples,
                        manifest=manifest,
                        ood_examples=ood_examples,
                        tokenizer=tokenizer,
                        config=config,
                    )
                    results.append(res)
                    save_incremental_results(results_file, results, persist_dir=persist_dir)
                    continue

                # 3. Train normally
                res = train_one_fold(
                    train_examples=train_ex,
                    val_examples=val_ex,
                    config=config,
                    fold_idx=fold_idx,
                    seed=seed,
                    checkpoint_dir=var_dir,
                    debug=debug,
                    aux_mode=var["aux_mode"],
                    freeze_lower_n_layers=var["freeze"],
                    model_name=var["name"],
                    ood_examples=ood_examples,
                )
                results.append(res)
                save_incremental_results(results_file, results, persist_dir=persist_dir)

    return results
