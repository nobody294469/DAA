"""eval_phase4_cascade.py — Evaluation runner for the Phase 4 Verification Cascade.

Authority:
  - PROJECT_SPEC.md §5 #8, §7.6, §10, §12.1, §12.2, §17, §21, §22 (Experiment V8 & V10)
  - DEC-003: Document context window bounding
  - DEC-005: Stage B escalation threshold (default: 0.60)
  - DEC-009: Temperature scaling calibration for Stage B
  - DEC-013: Grammar-constrained Stage C output contract
  - DEC-018: Confidence calibration for Stage C
  - DEC-019: Field verification tiering (Tier 1 vs Tier 2)

Evaluates the canonical VerificationCascade (Stage A -> Stage B -> Stage C) on:
  1. Cross-validation fold (CV validation split, default fold 0)
  2. Out-of-Distribution (OOD) template holdout split
  3. Cascade behavior: Stage A resolution rate, Stage C escalation rate, conditional accuracy
  4. Per-field metrics for all canonical fields
  5. Per-contradiction-type discrepancy recall
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any, Optional, Sequence

import numpy as np
import torch

from clindoc.extract.contracts import BBoxModel, Claim, Evidence, FieldType
from clindoc.m2.dataset import load_examples
from clindoc.m2.metrics import (
    LABEL_ORDER,
    auroc_binary,
    brier_score,
    discrepancy_recall,
    ece,
    macro_f1,
    per_class_metrics,
)
from clindoc.m2.schema import M2Example
from clindoc.m2.splits import get_fold_examples, get_ood_examples
from clindoc.schema.verification import VerificationResult
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.stage_a import DeterministicVerifier
from clindoc.verify.stage_b import LearnedVerifier
from clindoc.verify.stage_c import DefaultMockLLMJudge, StageCJudge

DEFAULT_DATA_DIR = Path("data/m2_verification")
DEFAULT_RESULTS_DIR = Path("experiments/V8-verification-cascade")
DEFAULT_CHECKPOINT = Path(
    "data/m2_results/checkpoints/modernbert_text_only/modernbert_text_only_fold0_seed42.pt"
)


def m2_example_to_claim_and_evidence(ex: M2Example) -> tuple[Claim, Evidence]:
    """Convert an M2Example into canonical Claim and Evidence contract instances."""
    # 1. Bounding box & Evidence span
    if ex.bbox and len(ex.bbox) == 4:
        bbox = BBoxModel.from_list(ex.bbox)
    else:
        bbox = BBoxModel(x0=0.0, y0=0.0, x1=100.0, y1=20.0)

    evidence_text = ex.evidence or ""
    char_start = ex.char_start if ex.char_start is not None else 0
    char_end = ex.char_end if ex.char_end is not None else len(evidence_text)
    if char_end < char_start:
        char_end = char_start + len(evidence_text)

    evidence = Evidence(
        evidence_id=ex.evidence_id or f"ev-{ex.example_id}",
        document_id=ex.source_doc_id,
        page_number=ex.page_number or 1,
        text=evidence_text,
        char_start=char_start,
        char_end=char_end,
        bbox=bbox,
        ocr_confidence=ex.ocr_confidence,
    )

    # 2. Parse clean field value, numeric value, and unit from ex.claim
    raw_claim = ex.claim.strip()
    val_clean = raw_claim
    value_numeric: Optional[float] = None
    unit_raw: Optional[str] = None

    ft = ex.field_type
    if ft == "med.name":
        if "Medication:" in raw_claim:
            val_clean = raw_claim.split("Medication:", 1)[1].strip()
    elif ft == "lab.flag":
        if "flag:" in raw_claim:
            val_clean = raw_claim.split("flag:", 1)[1].strip()
    elif ft == "med.dose":
        if "dose:" in raw_claim:
            val_clean = raw_claim.split("dose:", 1)[1].strip()
    elif ft == "med.route":
        if "route:" in raw_claim:
            val_clean = raw_claim.split("route:", 1)[1].strip()
    elif ft == "med.frequency":
        if "frequency:" in raw_claim:
            val_clean = raw_claim.split("frequency:", 1)[1].strip()
    elif ft == "lab.value":
        if ":" in raw_claim:
            val_clean = raw_claim.split(":", 1)[1].strip()

    # Extract numeric float if present
    num_match = re.search(r"[-+]?\d+(?:\.\d+)?", val_clean)
    if num_match:
        try:
            value_numeric = float(num_match.group(0))
        except ValueError:
            value_numeric = None

    # Extract unit if present (e.g. "14.2 g/dL" or "500 mg")
    unit_match = re.search(r"\b([a-zA-Z/%*]+(?:/[a-zA-Z]+)?)\b", val_clean)
    if unit_match and unit_match.group(1).lower() not in ("dose", "flag", "route", "frequency", "medication"):
        unit_raw = unit_match.group(1)

    claim = Claim(
        claim_id=ex.example_id,
        document_id=ex.source_doc_id,
        group_id=ex.group_id or f"group-{ex.source_doc_id}",
        field_type=ft,  # type: ignore[arg-type]
        value=val_clean,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        extraction_method="deterministic",
        extraction_confidence=1.0,
        evidence_id=evidence.evidence_id,
        region_id=f"reg-{ex.source_doc_id}",
    )

    return claim, evidence


def evaluate_cascade_on_examples(
    cascade: VerificationCascade,
    examples: list[M2Example],
    split_name: str = "val",
) -> dict[str, Any]:
    """Execute the VerificationCascade over a list of M2Examples and compute §21 metrics."""
    if not examples:
        return {}

    y_true: list[int] = []
    y_pred: list[int] = []
    y_probs: list[list[float]] = []
    discrepancy_scores: list[float] = []

    # Cascade behavior counters
    stage_counts = {"A": 0, "B": 0, "C": 0}
    stage_correct = {"A": 0, "B": 0, "C": 0}
    escalation_reasons: dict[str, int] = defaultdict(int)
    predicted_label_counts: dict[str, int] = defaultdict(int)
    contradiction_type_preds: dict[str, int] = defaultdict(int)

    # Per-field breakdowns
    by_field_true: dict[str, list[int]] = defaultdict(list)
    by_field_pred: dict[str, list[int]] = defaultdict(list)
    by_field_stages: dict[str, dict[str, int]] = defaultdict(lambda: {"A": 0, "B": 0, "C": 0})

    # Per-contradiction-type breakdown
    by_contra_true: dict[str, list[int]] = defaultdict(list)
    by_contra_pred: dict[str, list[int]] = defaultdict(list)

    total_examples = len(examples)
    t0 = time.time()

    for idx, ex in enumerate(examples):
        claim, evidence = m2_example_to_claim_and_evidence(ex)
        v_res: VerificationResult = cascade.verify_claim(claim=claim, evidence=evidence)

        true_idx = LABEL_ORDER.index(ex.label.value)
        pred_idx = LABEL_ORDER.index(v_res.label)

        y_true.append(true_idx)
        y_pred.append(pred_idx)

        # Build 3-class probability distribution centered on calibrated confidence
        p_vec = [0.0, 0.0, 0.0]
        conf = float(v_res.confidence)
        conf = max(0.0, min(1.0, conf))
        p_vec[pred_idx] = conf
        rem = (1.0 - conf) / 2.0
        for k in range(3):
            if k != pred_idx:
                p_vec[k] = rem
        y_probs.append(p_vec)
        discrepancy_scores.append(p_vec[LABEL_ORDER.index("DISCREPANCY")])

        # Stage behavior accounting
        stg = v_res.stage_reached
        stage_counts[stg] = stage_counts.get(stg, 0) + 1
        if true_idx == pred_idx:
            stage_correct[stg] = stage_correct.get(stg, 0) + 1

        predicted_label_counts[v_res.label] += 1
        if v_res.label == "DISCREPANCY" and v_res.contradiction_type:
            contradiction_type_preds[v_res.contradiction_type] += 1

        if v_res.escalated:
            # Stage B escalation reason
            reason = "escalated_to_stage_c"
            escalation_reasons[reason] += 1

        # Per-field tracking
        by_field_true[ex.field_type].append(true_idx)
        by_field_pred[ex.field_type].append(pred_idx)
        by_field_stages[ex.field_type][stg] += 1

        # Per-contradiction-type tracking
        if ex.contradiction_type:
            by_contra_true[ex.contradiction_type].append(true_idx)
            by_contra_pred[ex.contradiction_type].append(pred_idx)

    elapsed = time.time() - t0

    # ─── COMPUTE CORE METRICS (§21.3) ─────────────────────────────────────────
    macro_f1_score = macro_f1(y_true, y_pred)
    disc_rec = discrepancy_recall(y_true, y_pred)
    per_class = per_class_metrics(y_true, y_pred)

    # Multi-class AUROC (One-vs-Rest)
    aurocs: dict[str, float] = {}
    for i, lbl in enumerate(LABEL_ORDER):
        bin_true = [1 if y == i else 0 for y in y_true]
        bin_scores = [p[i] for p in y_probs]
        aurocs[lbl] = auroc_binary(bin_true, bin_scores)
    auroc_macro = sum(aurocs.values()) / len(aurocs)
    auroc_disc = aurocs["DISCREPANCY"]

    brier = brier_score(y_true, y_probs)
    ece_val = ece(y_true, y_probs)

    # ─── CASCADE BEHAVIOR METRICS (§21.5) ────────────────────────────────────
    stage_a_res_rate = stage_counts["A"] / total_examples
    stage_b_invoked = stage_counts["B"] + stage_counts["C"]
    stage_c_res_rate = stage_counts["C"] / total_examples
    escalation_rate = stage_c_res_rate

    cond_accuracy = {}
    for stg in ("A", "B", "C"):
        cnt = stage_counts[stg]
        cond_accuracy[f"accuracy_stage_{stg}"] = (
            round(stage_correct[stg] / cnt, 4) if cnt > 0 else 0.0
        )

    # ─── PER-FIELD METRICS ───────────────────────────────────────────────────
    field_metrics = {}
    for ft in sorted(by_field_true.keys()):
        ft_true = by_field_true[ft]
        ft_pred = by_field_pred[ft]
        ft_n = len(ft_true)
        ft_macro = macro_f1(ft_true, ft_pred)
        ft_rec = discrepancy_recall(ft_true, ft_pred)
        ft_acc = sum(1 for t, p in zip(ft_true, ft_pred) if t == p) / ft_n
        field_metrics[ft] = {
            "n_examples": ft_n,
            "macro_f1": round(ft_macro, 4),
            "discrepancy_recall": round(ft_rec, 4),
            "accuracy": round(ft_acc, 4),
            "stage_distribution": by_field_stages[ft],
            "escalation_rate": round(by_field_stages[ft]["C"] / ft_n, 4) if ft_n > 0 else 0.0,
        }

    # ─── PER-CONTRADICTION-TYPE METRICS ──────────────────────────────────────
    contra_metrics = {}
    for ctype in sorted(by_contra_true.keys()):
        c_true = by_contra_true[ctype]
        c_pred = by_contra_pred[ctype]
        c_n = len(c_true)
        c_rec = discrepancy_recall(c_true, c_pred)
        contra_metrics[ctype] = {
            "n_examples": c_n,
            "discrepancy_recall": round(c_rec, 4),
        }

    return {
        "split": split_name,
        "n_examples": total_examples,
        "elapsed_seconds": round(elapsed, 2),
        "overall": {
            "macro_f1": round(macro_f1_score, 4),
            "discrepancy_recall": round(disc_rec, 4),
            "auroc_macro": round(auroc_macro, 4),
            "auroc_discrepancy": round(auroc_disc, 4),
            "brier_score": brier,
            "ece": ece_val,
        },
        "per_class": per_class,
        "cascade_behavior": {
            "stage_a_resolved": stage_counts["A"],
            "stage_a_resolution_rate": round(stage_a_res_rate, 4),
            "stage_b_invoked": stage_b_invoked,
            "stage_b_resolved": stage_counts["B"],
            "stage_c_invoked": stage_counts["C"],
            "stage_c_escalation_rate": round(escalation_rate, 4),
            "conditional_accuracy": cond_accuracy,
            "predicted_labels": dict(predicted_label_counts),
            "predicted_contradiction_types": dict(contradiction_type_preds),
        },
        "by_field_type": field_metrics,
        "by_contradiction_type": contra_metrics,
    }


def run_phase4_evaluation(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    results_dir: Path | str = DEFAULT_RESULTS_DIR,
    checkpoint_path: Path | str | None = None,
    split: str = "both",
    fold_idx: int = 0,
    escalation_threshold: float = 0.60,
    always_stage_c: bool = False,
    max_examples: int | None = None,
    device: str | None = None,
) -> dict[str, Any]:
    """Execute end-to-end Phase 4 VerificationCascade evaluation."""
    data_dir = Path(data_dir)
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("PHASE 4 VERIFICATION CASCADE EVALUATION (Experiment V8 / V10)")
    print(f"Data directory:      {data_dir}")
    print(f"Split mode:          {split}")
    print(f"Validation fold:     {fold_idx}")
    print(f"Escalation thresh:   {escalation_threshold:.2f} (DEC-005)")
    print(f"Always Stage C:      {always_stage_c}")
    print("=" * 80)

    # 1. Load canonical dataset and split manifest
    all_examples_path = data_dir / "all_examples.jsonl"
    manifest_path = data_dir / "split_manifest.json"

    if not all_examples_path.exists():
        raise FileNotFoundError(f"Dataset not found at {all_examples_path}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"Split manifest not found at {manifest_path}")

    examples = load_examples(all_examples_path)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # 2. Select checkpoints & configure cascade
    if checkpoint_path is None:
        if DEFAULT_CHECKPOINT.exists():
            checkpoint_path = DEFAULT_CHECKPOINT
            print(f"Using default ModernBERT checkpoint: {checkpoint_path}")
        else:
            print("Notice: No physical ModernBERT checkpoint found; Stage B will use calibrated fallback predictor.")

    stage_a = DeterministicVerifier()
    stage_b = LearnedVerifier(
        model_path=checkpoint_path,
        escalation_threshold=escalation_threshold,
    )
    stage_c = StageCJudge(client=DefaultMockLLMJudge())

    cascade = VerificationCascade(
        stage_a=stage_a,
        stage_b=stage_b,
        stage_c=stage_c,
        always_stage_c=always_stage_c,
    )

    full_report: dict[str, Any] = {
        "status": "COMPLETED",
        "experiment_id": "V8-verification-cascade",
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
        "escalation_threshold": escalation_threshold,
        "always_stage_c": always_stage_c,
        "results": {},
    }

    ood_doc_ids = set(manifest.get("ood_doc_ids", []))

    # 3. Evaluate Validation Split
    if split in ("val", "both"):
        print("\nEvaluating on Validation Split (CV Fold %d)..." % fold_idx)
        train_ex, val_ex = get_fold_examples(examples, manifest, fold_idx)
        val_ex = [e for e in val_ex if e.source_doc_id not in ood_doc_ids]
        if max_examples is not None:
            val_ex = val_ex[:max_examples]
        print(f"  Total validation examples: {len(val_ex)}")

        val_results = evaluate_cascade_on_examples(cascade, val_ex, split_name=f"val_fold{fold_idx}")
        full_report["results"]["validation"] = val_results
        _print_summary("VALIDATION", val_results)

    # 4. Evaluate OOD Split
    if split in ("ood", "both"):
        print("\nEvaluating on Held-Out OOD Split (Template Holdout)...")
        ood_ex = get_ood_examples(examples, manifest)
        if max_examples is not None:
            ood_ex = ood_ex[:max_examples]
        print(f"  Total OOD examples: {len(ood_ex)}")

        ood_results = evaluate_cascade_on_examples(cascade, ood_ex, split_name="ood_heldout")
        full_report["results"]["ood"] = ood_results
        _print_summary("OUT-OF-DISTRIBUTION (OOD)", ood_results)

    # 5. Persist Report
    report_file = results_dir / "phase4_cascade_eval_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2)
    print(f"\nFinal Phase 4 Cascade report written to: {report_file}")

    return full_report


def _print_summary(header: str, res: dict[str, Any]) -> None:
    """Print clean terminal summary of cascade evaluation."""
    ov = res["overall"]
    cb = res["cascade_behavior"]
    ca = cb["conditional_accuracy"]

    print("\n" + "-" * 70)
    print(f"SUMMARY RESULTS: {header}")
    print("-" * 70)
    print(f"  Examples Evaluated:     {res['n_examples']:,} (elapsed: {res['elapsed_seconds']}s)")
    print(f"  Macro-F1:               {ov['macro_f1']:.4f}")
    print(f"  Discrepancy Recall:     {ov['discrepancy_recall']:.4f}")
    print(f"  AUROC (Macro):          {ov['auroc_macro']:.4f}")
    print(f"  AUROC (Discrepancy):    {ov['auroc_discrepancy']:.4f}")
    print(f"  Brier Score:            {ov['brier_score']:.4f}")
    print(f"  ECE:                    {ov['ece']:.4f}")
    print("\n  Cascade Dynamics:")
    print(f"    Stage A Resolved:     {cb['stage_a_resolved']:,} ({cb['stage_a_resolution_rate']*100:.1f}%) | Accuracy: {ca.get('accuracy_stage_A', 0.0):.4f}")
    print(f"    Stage B Resolved:     {cb['stage_b_resolved']:,} | Accuracy: {ca.get('accuracy_stage_B', 0.0):.4f}")
    print(f"    Stage C Escalated:    {cb['stage_c_invoked']:,} ({cb['stage_c_escalation_rate']*100:.1f}%) | Accuracy: {ca.get('accuracy_stage_C', 0.0):.4f}")
    print(f"    Final Labels:         SUPPORTED={cb['predicted_labels'].get('SUPPORTED', 0)}, DISCREPANCY={cb['predicted_labels'].get('DISCREPANCY', 0)}, INSUFFICIENT={cb['predicted_labels'].get('INSUFFICIENT_EVIDENCE', 0)}")
    print("-" * 70)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Phase 4 Verification Cascade Evaluation Runner")
    parser.add_argument("--data-dir", type=str, default=str(DEFAULT_DATA_DIR), help="Path to data/m2_verification")
    parser.add_argument("--results-dir", type=str, default=str(DEFAULT_RESULTS_DIR), help="Output directory for reports")
    parser.add_argument("--checkpoint", type=str, default=None, help="Path to Stage B ModernBERT checkpoint .pt")
    parser.add_argument("--split", choices=["val", "ood", "both"], default="both", help="Which split(s) to evaluate")
    parser.add_argument("--fold", type=int, default=0, help="Fold index for validation split (0-4)")
    parser.add_argument("--escalation-threshold", type=float, default=0.60, help="Stage B confidence threshold for Stage C escalation (DEC-005)")
    parser.add_argument("--always-stage-c", action="store_true", help="Force always-Stage-C for ablation comparison (§12.2)")
    parser.add_argument("--max-examples", type=int, default=None, help="Limit number of examples per split for fast smoke testing")

    args = parser.parse_args(argv)

    return run_phase4_evaluation(
        data_dir=args.data_dir,
        results_dir=args.results_dir,
        checkpoint_path=args.checkpoint,
        split=args.split,
        fold_idx=args.fold,
        escalation_threshold=args.escalation_threshold,
        always_stage_c=args.always_stage_c,
        max_examples=args.max_examples,
    )


if __name__ == "__main__":
    main()
