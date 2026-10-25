"""train_v3_experiment.py — Stage B ModernBERT training on the V3 benchmark (CLI).

Authority:
  - reports/M2_V3_MODERNBERT_TRAINING_REPORT.md (reference run; the defaults reproduce it)
  - DEC-009: temperature scaling fitted on the dedicated calibration split only

Dataset: data/m2_verification_v3/ (train 2242 / validation 883 / calibration 678 / OOD 1826),
SHA-256 locked. Model: BioClinical-ModernBERT-base, text-only (aux_mode="none"), 3 classes
(SUPPORTED, DISCREPANCY, INSUFFICIENT_EVIDENCE).

Outputs under --output-dir (default data/m2_results_v3/, gitignored):
  <run-name>_metrics.json   metrics (v3_experiment_metrics.json structure; the default run name
                            also writes v3_experiment_metrics.json)
  <run-name>_progress.json  live progress, atomically replaced (read by GET /api/model)
  checkpoints/<run-name>_fold0_seed<seed>.pt
plus, unless --no-model-card or --smoke, the committed model card reports/stage_b_runs/<run-name>.json.

Run from the repo root with $env:PYTHONPATH="src":
  python scripts/train_v3_experiment.py --smoke --device cpu --model-id <local base model dir>
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import random
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import torch
import transformers

from clindoc.m2.dataset import load_examples
from clindoc.m2.model import MODEL_CHECKPOINT
from clindoc.m2.schema import M2ModelConfig
from clindoc.m2.splits import (
    load_train_examples,
    load_validation_examples,
    load_calibration_examples,
    load_ood_examples,
)
from clindoc.m2.train import train_one_fold

REPO_ROOT = Path(__file__).resolve().parents[1]
V3_DIR = "data/m2_verification_v3"
DEFAULT_RUN_NAME = "modernbert_text_only_v3"
# Locked dataset files -> (sha256 of the CRLF Windows checkout, sha256 of the LF git blob).
DATASET_HASHES = {
    f"{V3_DIR}/all_examples.jsonl": (
        "40278c01848875382931a8a01397982ff6c00dfb7f588c30666b1ca8a66d6d16",
        "ab775656b8821a03067a43a02c11c2914f669bfaa1466b2c8adc6bba3495c6e1",
    ),
    f"{V3_DIR}/split_manifest.json": (
        "cf41cc1d7e889f14cdcecf9ca2c411d54f2b737ddcfa280f6852b739050d2067",
        "dc358cfe32cc191c7d9f734d8ed06b40625a002b56949a5d7d75df7b563f2e31",
    ),
    "data/m2_verification/all_examples.jsonl": (
        "ce924fa3921eedf2482c29279579ed010036273e608c399f38ce291c8306a2a8",
        "beb1f2fbb4eb32247aa62c885161487d4ee83d85491aeb80b6b87f8479043a38",
    ),
    "data/m2_verification/split_manifest.json": (
        "8a1ebd157188c4c4032dab297df4c53f9d2fc1dd58a52596a368cc7c676e4393",
        "31b76cb31a5b0482f6c9845d6b2ce0da6fbe0ad12f28c9b9a6a7e39b006137ca",
    ),
}
EXPECTED_COUNTS = {"train": 2242, "validation": 883, "calibration": 678, "ood": 1826}
SMOKE_COUNTS = {"train": 48, "validation": 24, "calibration": 24, "ood": 32}
HISTORY_KEYS = ("epoch", "train_loss", "val_loss", "val_macro_f1", "val_accuracy")


def file_sha256(path: Path) -> tuple[str, str]:
    """(sha256 of the bytes on disk, sha256 with CRLF normalised to LF)."""
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest(), hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def verify_dataset_hashes(
    expected: dict[str, tuple[str, str]] = DATASET_HASHES, root: Path = REPO_ROOT
) -> dict[str, str]:
    """Raise if a locked dataset file changed; accepts CRLF and LF checkouts. Returns disk hashes."""
    hashes = {}
    for rel, (crlf_hash, lf_hash) in expected.items():
        raw, lf = file_sha256(root / rel)
        if raw != crlf_hash and lf != lf_hash:
            raise ValueError(f"{rel}: SHA-256 {raw} (LF-normalised {lf}) does not match the locked benchmark")
        hashes[rel] = raw
    return hashes


def _at_least(lo: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        n = int(value)
        if n < lo:
            raise argparse.ArgumentTypeError(f"must be >= {lo}")
        return n
    return parse


def _fraction(value: str) -> float:
    f = float(value)
    if not 0.0 < f <= 1.0:
        raise argparse.ArgumentTypeError("must be in (0, 1]")
    return f


def _run_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise argparse.ArgumentTypeError("letters, digits, '.', '_' or '-' only (it names output files)")
    return value


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Train the Stage B ModernBERT verifier on the V3 benchmark.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--model-id", default=os.environ.get("CLINDOC_BASE_MODEL") or MODEL_CHECKPOINT,
                   help="base encoder: hub id or local dir; env CLINDOC_BASE_MODEL sets the default")
    p.add_argument("--output-dir", type=Path, default=None,
                   help="default data/m2_results_v3 (a fresh temp dir with --smoke)")
    p.add_argument("--run-name", type=_run_name, default=DEFAULT_RUN_NAME)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-epochs", type=_at_least(1), default=10)
    p.add_argument("--patience", type=_at_least(1), default=3, help="early-stopping patience (epochs)")
    p.add_argument("--batch-size", type=_at_least(1), default=16)
    p.add_argument("--grad-accum", type=_at_least(1), default=1,
                   help="micro-batches per optimizer step (effective batch = batch-size x grad-accum)")
    p.add_argument("--max-seq-length", type=_at_least(8), default=256)
    p.add_argument("--freeze-lower-n-layers", type=_at_least(0), default=0,
                   help="freeze the embeddings and the lowest N encoder layers")
    p.add_argument("--gradient-checkpointing", action="store_true")
    p.add_argument("--eval-batch-size", type=_at_least(1), default=64,
                   help="lower (e.g. 16) on small/hot laptop GPUs: long eval kernels can trip the Windows "
                        "driver timeout (TDR, nvlddmkm event 153) and kill the run")
    p.add_argument("--aux-mode", choices=("none", "ocr_only"), default="none",
                   help="none = V6 text-only; ocr_only = V7 OCR-aware (fuses OCR confidence). "
                        "Stage B serving supports only 'none'")
    p.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    p.add_argument("--gpu-mem-fraction", type=_fraction, default=None,
                   help="cap this process at FRACTION of GPU memory (protects a co-tenant GPU job)")
    p.add_argument("--smoke", action="store_true",
                   help="tiny end-to-end check: 48/24/24/32 examples, 1 epoch, batch 4, temp output dir, no model card")
    p.add_argument("--no-model-card", action="store_true", help="do not write reports/stage_b_runs/<run-name>.json")
    p.add_argument("--extra-train", type=Path, action="append", default=[],
                   help="extra M2Example JSONL appended to the V3 train split, e.g. data/m2_open/train.jsonl "
                        "(scripts/build_open_claims.py); V3 validation/calibration/OOD stay untouched")
    args = p.parse_args(argv)
    if args.gpu_mem_fraction is not None and args.device == "cpu":
        p.error("--gpu-mem-fraction needs a CUDA device")
    return args


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_commit() -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def hardware_info(device: str) -> dict:
    gpu = torch.cuda.get_device_properties(0) if device == "cuda" else None
    return {
        "gpu": gpu.name if gpu else None,
        "vram_mb": gpu.total_memory // 2**20 if gpu else None,
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "cuda": torch.version.cuda,
    }


def write_json_atomic(path: Path, payload: dict) -> None:
    """Write via a temp file + os.replace so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class ProgressWriter:
    """Live <run-name>_progress.json; used as train_one_fold's progress_callback.

    Rewritten at every phase/epoch boundary and at most every `min_interval_s` on step events.
    A failed write (e.g. a reader holding the file on Windows) is reported, never fatal.
    """

    def __init__(self, path: Path, run_name: str, device: str, max_epochs: int,
                 min_interval_s: float = 10.0) -> None:
        self.path = path
        self.min_interval_s = min_interval_s
        now = _utc_now()
        self.state = {
            "run_name": run_name, "status": "running", "phase": "load", "epoch": 0,
            "max_epochs": max_epochs, "step": 0, "steps_per_epoch": None, "global_step": 0,
            "total_steps": None, "train_loss": None, "last_val_macro_f1": None,
            "started_at": now, "updated_at": now, "eta_seconds": None, "device": device,
            "history": [], "error": None,
        }
        self._train_t0: Optional[float] = None
        self._last_write = 0.0
        self._write()

    def __call__(self, event: dict) -> None:
        s = self.state
        for key in ("phase", "epoch", "max_epochs", "step", "steps_per_epoch", "global_step",
                    "total_steps", "train_loss"):
            if key in event:
                s[key] = event[key]
        if event.get("event") == "epoch":
            s["last_val_macro_f1"] = event["val_macro_f1"]
            s["history"].append({k: event.get(k) for k in HISTORY_KEYS})
        if s["phase"] == "train" and self._train_t0 is None:
            self._train_t0 = time.monotonic()
        if self._train_t0 is not None and s["global_step"] and s["total_steps"]:
            per_step = (time.monotonic() - self._train_t0) / s["global_step"]
            s["eta_seconds"] = round(per_step * (s["total_steps"] - s["global_step"]))
        if event.get("event") != "step" or time.monotonic() - self._last_write >= self.min_interval_s:
            self._write()

    def finish(self, status: str, error: Optional[str] = None) -> None:
        self.state.update(status=status, error=error)
        if status == "completed":
            self.state.update(phase="done", eta_seconds=0)
        self._write()

    def _write(self) -> None:
        self.state["updated_at"] = _utc_now()
        self._last_write = time.monotonic()
        try:
            write_json_atomic(self.path, self.state)
        except OSError as e:
            print(f"  [progress] could not write {self.path}: {e}")


def eval_block(ev: Optional[dict]) -> Optional[dict]:
    """Model-card validation/ood block from an M2EvalResult dump (confusion rows = truth, LABEL_ORDER)."""
    if not ev:
        return None
    cm = ev["confusion_matrix"]
    total = sum(map(sum, cm))
    return {
        "accuracy": round(sum(cm[i][i] for i in range(len(cm))) / total, 4) if total else None,
        "macro_f1": ev["macro_f1"],
        "discrepancy_recall": ev["discrepancy_recall"],
        "per_class": ev["per_class"],
        "confusion_matrix": cm,
        "ece": ev.get("ece"),
        "auroc_macro": None,  # not computed by evaluate_model_on_split; see auroc_discrepancy
        "auroc_discrepancy": ev.get("auroc_discrepancy"),
        "brier_score": ev.get("brier_score"),
    }


def _repo_rel(path: Path) -> str:
    path = path.resolve()
    return path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else str(path)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    run_name = args.run_name
    if args.output_dir is not None:
        out_dir = args.output_dir
    elif args.smoke:
        out_dir = Path(tempfile.mkdtemp(prefix="clindoc_stage_b_smoke_"))
    else:
        out_dir = REPO_ROOT / "data" / "m2_results_v3"

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("--device cuda requested but CUDA is not available")
    if args.gpu_mem_fraction is not None and device != "cuda":
        raise SystemExit("--gpu-mem-fraction needs a CUDA device")

    print("=" * 70)
    print(f"STAGE B ModernBERT Training — V3 Benchmark — run {run_name}{' (SMOKE)' if args.smoke else ''}")
    print("=" * 70)

    # 1. Locked benchmark integrity (line-ending agnostic)
    hashes = verify_dataset_hashes()
    for rel, h in hashes.items():
        print(f"  {rel} SHA256 {h[:16]}... VERIFIED")

    # 2. Dataset partitions (document-level isolation)
    examples = load_examples(REPO_ROOT / V3_DIR / "all_examples.jsonl")
    manifest = json.loads((REPO_ROOT / V3_DIR / "split_manifest.json").read_text(encoding="utf-8"))
    parts = {
        "train": load_train_examples(examples, manifest),
        "validation": load_validation_examples(examples, manifest),
        "calibration": load_calibration_examples(examples, manifest),
        "ood": load_ood_examples(examples, manifest),
    }
    counts = {k: len(v) for k, v in parts.items()}
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"partition counts {counts} != expected {EXPECTED_COUNTS}")
    docs = {k: {e.source_doc_id for e in v} for k, v in parts.items()}
    for a, b in itertools.combinations(docs, 2):
        if not docs[a].isdisjoint(docs[b]):
            raise ValueError(f"document leakage between {a} and {b}")
    print(f"  Partitions {counts}; document-level isolation VERIFIED")
    for path in args.extra_train:
        extra = load_examples(path)
        if not {e.source_doc_id for e in extra}.isdisjoint(set().union(*docs.values())):
            raise ValueError(f"{path} shares documents with the V3 benchmark")
        parts["train"] = parts["train"] + extra
        print(f"  + {len(extra)} extra train examples from {path}")

    max_epochs, batch_size = args.max_epochs, args.batch_size
    if args.smoke:
        rng = random.Random(args.seed)
        parts = {k: rng.sample(v, min(SMOKE_COUNTS[k], len(v))) for k, v in parts.items()}
        max_epochs, batch_size = 1, 4
    used_counts = {k: len(v) for k, v in parts.items()}

    # 3. Configuration (defaults = the reference V3 run)
    config = M2ModelConfig(
        model_id=args.model_id,
        num_labels=3,
        max_seq_length=args.max_seq_length,
        encoder_lr=2e-5,
        head_lr=1e-4,
        weight_decay=0.01,
        warmup_fraction=0.06,
        max_epochs=max_epochs,
        early_stopping_patience=args.patience,
        batch_size=batch_size,
        seeds=[args.seed],
        freeze_lower_n_layers=args.freeze_lower_n_layers,
        use_aux=args.aux_mode != "none",
    )
    hw = hardware_info(device)
    train_cfg = {
        "model_id": config.model_id,
        "aux_mode": args.aux_mode,
        "freeze_lower_n_layers": args.freeze_lower_n_layers,
        "seed": args.seed,
        "batch_size": batch_size,
        "grad_accum_steps": args.grad_accum,
        "effective_batch_size": batch_size * args.grad_accum,
        "gradient_checkpointing": args.gradient_checkpointing,
        "max_epochs": max_epochs,
        "early_stopping_patience": config.early_stopping_patience,
        "encoder_lr": config.encoder_lr,
        "head_lr": config.head_lr,
        "weight_decay": config.weight_decay,
        "warmup_fraction": config.warmup_fraction,
        "max_seq_length": config.max_seq_length,
        "device": device,
        "device_name": hw["gpu"] or "cpu",
        "gpu_mem_fraction": args.gpu_mem_fraction,
        "smoke": args.smoke,
        "extra_train": [_repo_rel(Path(x)) for x in args.extra_train],
    }
    print("\nConfiguration:")
    for k, v in train_cfg.items():
        print(f"  {k:24s} {v}")
    print(f"  {'hardware':24s} {hw}")
    print(f"  {'output_dir':24s} {out_dir}")

    if args.gpu_mem_fraction is not None:
        torch.cuda.set_per_process_memory_fraction(args.gpu_mem_fraction, 0)

    # 4. Train + calibrate + evaluate, with live progress
    progress = ProgressWriter(out_dir / f"{run_name}_progress.json", run_name, device, max_epochs)
    start = time.time()
    try:
        result = train_one_fold(
            train_examples=parts["train"],
            val_examples=parts["validation"],
            config=config,
            fold_idx=0,
            seed=args.seed,
            checkpoint_dir=out_dir / "checkpoints",
            debug=False,
            aux_mode=args.aux_mode,
            freeze_lower_n_layers=args.freeze_lower_n_layers,
            model_name=run_name,
            ood_examples=parts["ood"],
            calib_examples=parts["calibration"],
            grad_accum_steps=args.grad_accum,
            gradient_checkpointing=args.gradient_checkpointing,
            eval_batch_size=args.eval_batch_size,
            device=device,
            progress_callback=progress,
        )
        elapsed = time.time() - start

        # 5. Outputs
        history = result["history"]
        best = max(history, key=lambda h: h["val_macro_f1"]) if history else {}
        calib_metrics = result.get("calib_metrics") or {}
        temperature = result["calibration_temperature"]
        ckpt_path = Path(result["checkpoint_path"])
        ckpt_size = ckpt_path.stat().st_size if ckpt_path.exists() else None
        commit = git_commit()

        metrics = {
            "git_commit": commit,
            "dataset_name": V3_DIR,
            "dataset_hashes": {
                "all_examples_sha256": hashes[f"{V3_DIR}/all_examples.jsonl"],
                "split_manifest_sha256": hashes[f"{V3_DIR}/split_manifest.json"],
                "historical_v2_all_examples_sha256": hashes["data/m2_verification/all_examples.jsonl"],
                "historical_v2_split_manifest_sha256": hashes["data/m2_verification/split_manifest.json"],
            },
            "partition_counts": {**used_counts, "total": len(examples)},
            "training_config": train_cfg,
            "hardware": hw,
            "training_execution": {
                "elapsed_seconds": round(elapsed, 2),
                "epochs_run": len(history),
                "best_epoch": best.get("epoch"),
                "best_epoch_train_loss": best.get("loss"),
                "best_epoch_val_loss": best.get("val_loss"),
                "best_val_macro_f1": result["best_val_macro_f1"],
                "history": history,
            },
            "validation_metrics": eval_block(result["val_eval"]),
            "calibration": {
                "partition": f"{V3_DIR} (calibration split, {used_counts['calibration']} examples)",
                "validation_used_for_fitting": False,
                "learned_temperature_T": temperature,
                "metrics": calib_metrics,
            },
            "ood_metrics": eval_block(result.get("ood_eval")),
            "checkpoint": {
                "path": str(ckpt_path),
                "size_bytes": ckpt_size,
                "size_mb": round(ckpt_size / 2**20, 2) if ckpt_size else None,
                "trained_from_v3_train_partition": True,
                "calibration_used_only_v3_calibration_partition": True,
                "validation_not_used_for_temperature_fitting": True,
            },
        }
        metrics_file = out_dir / f"{run_name}_metrics.json"
        write_json_atomic(metrics_file, metrics)
        if run_name == DEFAULT_RUN_NAME:
            write_json_atomic(out_dir / "v3_experiment_metrics.json", metrics)  # legacy file name

        card_file = None
        if not (args.smoke or args.no_model_card):
            base_model = config.model_id.replace(str(Path.home()), "~")  # no user paths in committed cards
            card = {
                "run_name": run_name,
                "created_at": _utc_now(),
                "git_commit": commit,
                "base_model": base_model,
                "device": device,
                "hardware": hw,
                "config": {**config.model_dump(), **train_cfg, "model_id": base_model},
                "dataset": {"hashes": hashes, "partition_counts": used_counts},
                "training": {
                    "duration_s": round(elapsed, 2),
                    "epochs_run": len(history),
                    "best_epoch": best.get("epoch"),
                    "history": [
                        {"epoch": h["epoch"], "train_loss": h["loss"], "val_loss": h["val_loss"],
                         "val_macro_f1": h["val_macro_f1"], "val_accuracy": h.get("val_accuracy")}
                        for h in history
                    ],
                },
                "validation": eval_block(result["val_eval"]),
                "calibration": {
                    "temperature": temperature,
                    **{k: calib_metrics.get(k) for k in ("ece_before", "ece_after", "nll_before", "nll_after")},
                },
                "ood": eval_block(result.get("ood_eval")),
                "checkpoint": {"path": _repo_rel(ckpt_path), "size_bytes": ckpt_size},
            }
            card_file = REPO_ROOT / "reports" / "stage_b_runs" / f"{run_name}.json"
            write_json_atomic(card_file, card)
        progress.finish("completed")
    except BaseException as e:  # incl. KeyboardInterrupt: the live progress must not stay "running"
        progress.finish("failed", error=f"{type(e).__name__}: {e}")
        raise

    val, ood = metrics["validation_metrics"], metrics["ood_metrics"]
    print("\n" + "=" * 70)
    print("EXPERIMENT SUMMARY")
    print("=" * 70)
    print(f"Wall time:            {elapsed:.1f} s | epochs run {len(history)} | best epoch {best.get('epoch')}")
    print(f"Checkpoint:           {ckpt_path} ({(ckpt_size or 0) / 2**20:.2f} MB)")
    print(f"Temperature T:        {temperature:.6f}")
    print(f"Val  acc / macro-F1:  {val['accuracy']} / {val['macro_f1']:.4f} | disc recall {val['discrepancy_recall']:.4f}")
    if ood:
        print(f"OOD  acc / macro-F1:  {ood['accuracy']} / {ood['macro_f1']:.4f} | disc recall {ood['discrepancy_recall']:.4f}")
    print(f"Metrics JSON:         {metrics_file}")
    print(f"Progress JSON:        {progress.path}")
    print(f"Model card:           {card_file or '(not written)'}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
