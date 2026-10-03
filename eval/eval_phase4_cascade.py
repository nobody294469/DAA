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

from clindoc.extract.contracts import BBoxModel, Claim, Evidence, FieldType, TIER_2_FIELDS
from clindoc.m2.context import reconstruct_canonical_page_text
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
from clindoc.m2.splits import (
    get_fold_examples,
    get_ood_examples,
    load_validation_examples,
)
from clindoc.schema.verification import VerificationResult
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.stage_a import DeterministicVerifier
from clindoc.verify.stage_b import LearnedVerifier
from clindoc.verify.stage_c import DefaultMockLLMJudge, StageCJudge

DEFAULT_DATA_DIR = Path("data/m2_verification_v3")
DEFAULT_RESULTS_DIR = Path("experiments/V8-verification-cascade")
DEFAULT_CHECKPOINT = Path(
    "data/m2_results/checkpoints/modernbert_text_only/modernbert_text_only_fold0_seed42.pt"
)

_PAGE_TEXT_CACHE: dict[tuple[str, int], Optional[str]] = {}


def load_page_text_for_document(
    doc_id: str,
    page_number: int = 1,
    base_dir: Optional[Path] = None,
) -> Optional[str]:
    """Retrieve authoritative page text from canonical OCR caches.

    Adheres strictly to DEC-003 and project spec §7.1.
    """
    key = (doc_id, page_number)
    if key in _PAGE_TEXT_CACHE:
        return _PAGE_TEXT_CACHE[key]

    root = base_dir or Path(".")
    candidates = [
        (root / "data" / "ocr_cache" / f"{doc_id}.json", True),
        (root / "data" / "med_ocr_cache" / f"{doc_id}.json", False),
        (root / "data" / "narrative_ocr_cache" / f"{doc_id}.json", False),
    ]

    for p, is_tab in candidates:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    ocr_data = json.load(f)
                txt, _ = reconstruct_canonical_page_text(
                    ocr_data.get("lines", []),
                    is_tabular=is_tab,
                    page_number=page_number,
                )
                _PAGE_TEXT_CACHE[key] = txt
                return txt
            except Exception:
                pass

    _PAGE_TEXT_CACHE[key] = None
    return None


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

    # 2. Parse structured numeric value and unit while preserving full claim representation
    raw_claim = ex.claim.strip()
    value_numeric: Optional[float] = None
    unit_raw: Optional[str] = None

    # Extract numeric float if present for structured claim attributes
    num_match = re.search(r"[-+]?\d+(?:\.\d+)?", raw_claim)
    if num_match:
        try:
            value_numeric = float(num_match.group(0))
        except ValueError:
            value_numeric = None
        # Extract unit immediately following the numeric value (e.g. "105 mg/dL" or "500 mg")
        rest = raw_claim[num_match.end():].strip()
        unit_match = re.match(r"^([a-zA-Z/%*]+(?:/[a-zA-Z]+)?)", rest)
        if unit_match and unit_match.group(1).lower() not in ("dose", "flag", "route", "frequency", "medication", "sig"):
            unit_raw = unit_match.group(1)

    # Input Fidelity: preserve exact claim string used by M2 ModernBERT training and evaluation
    claim = Claim(
        claim_id=ex.example_id,
        document_id=ex.source_doc_id,
        group_id=ex.group_id or f"group-{ex.source_doc_id}",
        field_type=ex.field_type,  # type: ignore[arg-type]
        value=raw_claim,
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
    record_diagnostics: bool = False,
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

    # True Stage B probability tracking
    stage_b_all_y_true: list[int] = []
    stage_b_all_probs: list[list[float]] = []
    stage_b_resolved_y_true: list[int] = []
    stage_b_resolved_probs: list[list[float]] = []

    # Per-field breakdowns
    by_field_true: dict[str, list[int]] = defaultdict(list)
    by_field_pred: dict[str, list[int]] = defaultdict(list)
    by_field_stages: dict[str, dict[str, int]] = defaultdict(lambda: {"A": 0, "B": 0, "C": 0})

    # Per-contradiction-type breakdown
    by_contra_true: dict[str, list[int]] = defaultdict(list)
    by_contra_pred: dict[str, list[int]] = defaultdict(list)

    diagnostics_list: list[dict[str, Any]] = []

    total_examples = len(examples)
    t0 = time.time()

    for idx, ex in enumerate(examples):
        claim, evidence = m2_example_to_claim_and_evidence(ex)
        page_text = load_page_text_for_document(ex.source_doc_id, ex.page_number or 1)
        v_res, stage_a_res, stage_b_res, stage_c_res = cascade.verify_claim_all_stages(
            claim=claim,
            evidence=evidence,
            page_text=page_text,
        )

        true_idx = LABEL_ORDER.index(ex.label.value)
        pred_idx = LABEL_ORDER.index(v_res.label)

        y_true.append(true_idx)
        y_pred.append(pred_idx)

        # Genuine Stage B Calibrated Probabilities (no fabricated vectors)
        if stage_b_res is not None and stage_b_res.logits is not None:
            b_probs = cascade.stage_b.get_calibrated_probabilities(stage_b_res).tolist()
            stage_b_all_y_true.append(true_idx)
            stage_b_all_probs.append(b_probs)
            if v_res.stage_reached == "B":
                stage_b_resolved_y_true.append(true_idx)
                stage_b_resolved_probs.append(b_probs)

        # Stage behavior accounting
        stg = v_res.stage_reached
        stage_counts[stg] = stage_counts.get(stg, 0) + 1
        if true_idx == pred_idx:
            stage_correct[stg] = stage_correct.get(stg, 0) + 1

        predicted_label_counts[v_res.label] += 1
        if v_res.label == "DISCREPANCY" and v_res.contradiction_type:
            contradiction_type_preds[v_res.contradiction_type] += 1

        if v_res.escalated:
            reason = stage_b_res.escalation_reason if stage_b_res and stage_b_res.escalation_reason else "escalated_to_stage_c"
            escalation_reasons[reason] += 1

        # Per-field tracking
        by_field_true[ex.field_type].append(true_idx)
        by_field_pred[ex.field_type].append(pred_idx)
        by_field_stages[ex.field_type][stg] += 1

        # Per-contradiction-type tracking
        if ex.contradiction_type:
            by_contra_true[ex.contradiction_type].append(true_idx)
            by_contra_pred[ex.contradiction_type].append(pred_idx)

        if record_diagnostics:
            # Stage A resolution reason
            stage_a_reason = None
            if stage_a_res:
                if stage_a_res.details and "reason" in stage_a_res.details:
                    stage_a_reason = str(stage_a_res.details["reason"])
                elif stage_a_res.label is None:
                    stage_a_reason = "deferred_to_learned"
                elif v_res.stage_reached == "A":
                    stage_a_reason = "tier_2_deterministic" if claim.field_type in TIER_2_FIELDS else "resolved_stage_a"
                else:
                    stage_a_reason = "precheck_complete"
            elif v_res.stage_reached == "A":
                stage_a_reason = "missing_or_blank_evidence"

            # Stage B probabilities
            stage_b_probs_dict = None
            if stage_b_res is not None and stage_b_res.logits is not None:
                p_vec = cascade.stage_b.get_calibrated_probabilities(stage_b_res)
                stage_b_probs_dict = {
                    lbl: round(float(p_vec[i]), 6)
                    for i, lbl in enumerate(LABEL_ORDER)
                }

            # Stage C metadata
            stage_c_is_fallback = False
            stage_c_error = None
            if stage_c_res is not None:
                stage_c_is_fallback = getattr(stage_c_res, "is_fallback", False) or "fallback" in (stage_c_res.rationale or "").lower()
                stage_c_error = getattr(stage_c_res, "error", None)
                if not stage_c_error and stage_c_is_fallback:
                    stage_c_error = stage_c_res.rationale

            stage_b_temp = None
            if hasattr(cascade.stage_b, "scaler") and cascade.stage_b.scaler:
                stage_b_temp = getattr(cascade.stage_b.scaler, "temperature", None)

            diag_item = {
                "example_id": ex.example_id,
                "group_id": ex.group_id or claim.group_id,
                "document_id": ex.source_doc_id,
                "field_type": ex.field_type,
                "ground_truth_label": ex.label.value,
                "stage_a": {
                    "label": stage_a_res.label if stage_a_res else v_res.stage_a_label,
                    "resolved": bool(v_res.stage_reached == "A"),
                    "confidence": stage_a_res.confidence if stage_a_res else (1.0 if v_res.stage_reached == "A" else None),
                    "contradiction_type": stage_a_res.contradiction_type if stage_a_res else None,
                    "resolution_reason": stage_a_reason,
                    "details": stage_a_res.details if stage_a_res else {},
                },
                "stage_b": {
                    "label": stage_b_res.label if stage_b_res else v_res.stage_b_label,
                    "raw_confidence": stage_b_res.raw_confidence if stage_b_res else v_res.stage_b_raw_confidence,
                    "calibrated_confidence": stage_b_res.calibrated_confidence if stage_b_res else (v_res.confidence if v_res.stage_reached == "B" else None),
                    "calibrated_probabilities": stage_b_probs_dict,
                    "escalated": stage_b_res.escalated if stage_b_res else False,
                    "escalation_reason": stage_b_res.escalation_reason if stage_b_res else None,
                },
                "stage_c": {
                    "invoked": bool(stage_c_res is not None or v_res.stage_reached == "C"),
                    "label": stage_c_res.label if stage_c_res else (v_res.label if v_res.stage_reached == "C" else None),
                    "raw_confidence": stage_c_res.raw_confidence if stage_c_res else None,
                    "calibrated_confidence": stage_c_res.calibrated_confidence if stage_c_res else (v_res.confidence if v_res.stage_reached == "C" else None),
                    "contradiction_type": stage_c_res.contradiction_type if stage_c_res else (v_res.contradiction_type if v_res.stage_reached == "C" else None),
                    "rationale": stage_c_res.rationale if stage_c_res else (v_res.rationale if v_res.stage_reached == "C" else None),
                    "attempts": stage_c_res.attempts if stage_c_res else None,
                    "is_fallback": stage_c_is_fallback,
                    "error": stage_c_error,
                    "model_name": getattr(stage_c_res, "model_name", None) or getattr(cascade.stage_c.client, "model_name", None),
                    "prompt_version": getattr(stage_c_res, "prompt_version", None),
                },
                "cascade": {
                    "final_label": v_res.label,
                    "final_confidence": v_res.confidence,
                    "stage_reached": v_res.stage_reached,
                    "escalated": v_res.escalated,
                    "contradiction_type": v_res.contradiction_type,
                    "rationale": v_res.rationale,
                    "is_correct": bool(v_res.label == ex.label.value),
                },
                "claim": {
                    "claim_id": claim.claim_id,
                    "value": claim.value,
                    "value_numeric": claim.value_numeric,
                    "unit_raw": claim.unit_raw,
                    "negated": claim.negated,
                },
                "evidence": {
                    "evidence_id": evidence.evidence_id if evidence else None,
                    "text": evidence.text if evidence else None,
                    "char_start": evidence.char_start if evidence else None,
                    "char_end": evidence.char_end if evidence else None,
                    "page_number": evidence.page_number if evidence else None,
                    "ocr_confidence": evidence.ocr_confidence if evidence else None,
                },
                "metadata": {
                    "stage_b_model_path": str(cascade.stage_b.model_path) if getattr(cascade.stage_b, "model_path", None) else None,
                    "stage_b_temperature": stage_b_temp,
                    "stage_c_client": getattr(cascade.stage_c.client, "model_name", type(cascade.stage_c.client).__name__),
                },
            }
            diagnostics_list.append(diag_item)

    elapsed = time.time() - t0

    # ─── COMPUTE CORE CATEGORICAL METRICS (§21.3) ───────────────────────────
    macro_f1_score = macro_f1(y_true, y_pred)
    disc_rec = discrepancy_recall(y_true, y_pred)
    per_class = per_class_metrics(y_true, y_pred)

    # ─── COMPUTE PROBABILITY METRICS FROM GENUINE STAGE B LOGITS ─────────────
    stage_b_prob_metrics: dict[str, Any] = {}
    if stage_b_all_probs:
        aurocs: dict[str, float] = {}
        for i, lbl in enumerate(LABEL_ORDER):
            bin_true = [1 if y == i else 0 for y in stage_b_all_y_true]
            bin_scores = [p[i] for p in stage_b_all_probs]
            aurocs[lbl] = auroc_binary(bin_true, bin_scores)
        stage_b_prob_metrics = {
            "n_examples": len(stage_b_all_probs),
            "auroc_macro": round(sum(aurocs.values()) / len(aurocs), 4),
            "auroc_discrepancy": round(aurocs["DISCREPANCY"], 4),
            "brier_score": round(brier_score(stage_b_all_y_true, stage_b_all_probs), 4),
            "ece": round(ece(stage_b_all_y_true, stage_b_all_probs), 4),
            "source": "genuine_stage_b_calibrated_logits",
        }

    stage_b_resolved_metrics: dict[str, Any] = {}
    if stage_b_resolved_probs:
        aurocs_res: dict[str, float] = {}
        for i, lbl in enumerate(LABEL_ORDER):
            bin_true = [1 if y == i else 0 for y in stage_b_resolved_y_true]
            bin_scores = [p[i] for p in stage_b_resolved_probs]
            aurocs_res[lbl] = auroc_binary(bin_true, bin_scores)
        preds_res = [int(np.argmax(p)) for p in stage_b_resolved_probs]
        stage_b_resolved_metrics = {
            "n_examples": len(stage_b_resolved_probs),
            "macro_f1": round(macro_f1(stage_b_resolved_y_true, preds_res), 4),
            "discrepancy_recall": round(discrepancy_recall(stage_b_resolved_y_true, preds_res), 4),
            "auroc_macro": round(sum(aurocs_res.values()) / len(aurocs_res), 4),
            "auroc_discrepancy": round(aurocs_res["DISCREPANCY"], 4),
            "brier_score": round(brier_score(stage_b_resolved_y_true, stage_b_resolved_probs), 4),
            "ece": round(ece(stage_b_resolved_y_true, stage_b_resolved_probs), 4),
        }

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

    is_mock_stage_c = isinstance(cascade.stage_c.client, DefaultMockLLMJudge)

    res = {
        "split": split_name,
        "n_examples": total_examples,
        "elapsed_seconds": round(elapsed, 2),
        "stage_c_mode": "MOCK_ABLATION" if is_mock_stage_c else "CLINICAL_LLM",
        "stage_c_notice": (
            "DefaultMockLLMJudge is a unit-test mock for pipeline testing, NOT an actual clinical LLM. "
            "Stage C decisions reflect keyword test heuristics." if is_mock_stage_c else "Evaluated with clinical LLM backend."
        ),
        "overall": {
            "macro_f1": round(macro_f1_score, 4),
            "discrepancy_recall": round(disc_rec, 4),
            "auroc_macro": stage_b_prob_metrics.get("auroc_macro"),
            "auroc_discrepancy": stage_b_prob_metrics.get("auroc_discrepancy"),
            "brier_score": stage_b_prob_metrics.get("brier_score"),
            "ece": stage_b_prob_metrics.get("ece"),
            "stage_b_probabilities": stage_b_prob_metrics,
            "stage_b_resolved_subset": stage_b_resolved_metrics,
            "probability_instrumentation_policy": (
                "AUROC, Brier, and ECE are computed strictly from genuine Stage B "
                "calibrated probability distributions. No synthetic distributions are fabricated "
                "for Stage C scalar confidence outputs."
            ),
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
            "escalation_reasons": dict(escalation_reasons),
            "predicted_labels": dict(predicted_label_counts),
            "predicted_contradiction_types": dict(contradiction_type_preds),
        },
        "by_field_type": field_metrics,
        "by_contradiction_type": contra_metrics,
    }
    if record_diagnostics:
        res["diagnostics"] = diagnostics_list
    return res


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
    stage_c_client: str = "mock",
    vllm_url: str = "http://localhost:8000/v1",
    vllm_model: str = "Qwen/Qwen2.5-7B-Instruct",
    save_diagnostics: bool = False,
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
    print(f"Stage C Client:      {stage_c_client} ({vllm_model if stage_c_client == 'vllm' else 'DefaultMockLLMJudge'})")
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
    print(f"Loaded {len(examples)} total examples from {data_dir}")

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
        device=device,
    )
    if checkpoint_path is not None:
        if stage_b.is_model_loaded:
            print(f"Loaded ModernBERT Stage-B checkpoint on {stage_b._device}: {checkpoint_path}")
        else:
            print(f"Notice: Failed to load ModernBERT checkpoint from {checkpoint_path}; using fallback predictor.")

    if stage_c_client == "vllm":
        from clindoc.verify.stage_c import VLLMJudgeClient
        llm_client = VLLMJudgeClient(
            model_name=vllm_model,
            base_url=vllm_url,
            temperature=0.0,
            seed=42,
            max_tokens=256,
        )
    else:
        llm_client = DefaultMockLLMJudge()

    stage_c = StageCJudge(client=llm_client)

    cascade = VerificationCascade(
        stage_a=stage_a,
        stage_b=stage_b,
        stage_c=stage_c,
        always_stage_c=always_stage_c,
    )

    full_report: dict[str, Any] = {
        "status": "COMPLETED",
        "experiment_id": "V8-verification-cascade",
        "data_dir": str(data_dir),
        "total_examples": len(examples),
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
        "escalation_threshold": escalation_threshold,
        "always_stage_c": always_stage_c,
        "stage_c_client": stage_c_client,
        "vllm_model": vllm_model if stage_c_client == "vllm" else None,
        "results": {},
    }

    ood_doc_ids = set(manifest.get("ood_doc_ids", []))

    # 3. Evaluate Validation Split
    if split in ("val", "both"):
        if "validation_doc_ids" in manifest and fold_idx == 0:
            print("\nEvaluating on Validation Split (Canonical In-Distribution Partition)...")
            val_ex = load_validation_examples(examples, manifest)
            val_split_name = "validation"
        else:
            print("\nEvaluating on Validation Split (CV Fold %d)..." % fold_idx)
            train_ex, val_ex = get_fold_examples(examples, manifest, fold_idx)
            val_ex = [e for e in val_ex if e.source_doc_id not in ood_doc_ids]
            val_split_name = f"val_fold{fold_idx}"

        total_val_count = len(val_ex)
        if max_examples is not None:
            val_ex = val_ex[:max_examples]
            print(f"  Total validation examples: {total_val_count} (evaluating first {len(val_ex)})")
        else:
            print(f"  Total validation examples: {total_val_count}")

        val_results = evaluate_cascade_on_examples(
            cascade, val_ex, split_name=val_split_name, record_diagnostics=save_diagnostics
        )
        full_report["results"]["validation"] = val_results
        _print_summary("VALIDATION", val_results)

    # 4. Evaluate OOD Split
    if split in ("ood", "both"):
        print("\nEvaluating on Held-Out OOD Split (Template Holdout)...")
        ood_ex = get_ood_examples(examples, manifest)
        total_ood_count = len(ood_ex)
        if max_examples is not None:
            ood_ex = ood_ex[:max_examples]
            print(f"  Total OOD examples: {total_ood_count} (evaluating first {len(ood_ex)})")
        else:
            print(f"  Total OOD examples: {total_ood_count}")

        ood_results = evaluate_cascade_on_examples(
            cascade, ood_ex, split_name="ood_heldout", record_diagnostics=save_diagnostics
        )
        full_report["results"]["ood"] = ood_results
        _print_summary("OUT-OF-DISTRIBUTION (OOD)", ood_results)

    # 5. Persist Diagnostics (if requested) & Report
    if save_diagnostics:
        diag_file = results_dir / "phase4_cascade_diagnostics.json"
        diagnostics_payload: dict[str, Any] = {
            "status": "COMPLETED",
            "experiment_id": "V8-verification-cascade",
            "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
            "escalation_threshold": escalation_threshold,
            "always_stage_c": always_stage_c,
            "stage_c_client": stage_c_client,
            "vllm_model": vllm_model if stage_c_client == "vllm" else None,
            "diagnostics": {},
        }
        if "validation" in full_report["results"] and "diagnostics" in full_report["results"]["validation"]:
            diagnostics_payload["diagnostics"]["validation"] = full_report["results"]["validation"].pop("diagnostics")
        if "ood" in full_report["results"] and "diagnostics" in full_report["results"]["ood"]:
            diagnostics_payload["diagnostics"]["ood"] = full_report["results"]["ood"].pop("diagnostics")

        with open(diag_file, "w", encoding="utf-8") as f:
            json.dump(diagnostics_payload, f, indent=2)
        print(f"\nPer-example diagnostics written to: {diag_file}")
        full_report["diagnostics_file"] = str(diag_file)

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

    def _fmt(val: float | None) -> str:
        return f"{val:.4f}" if val is not None else "N/A"

    print("\n" + "-" * 70)
    print(f"SUMMARY RESULTS: {header}")
    print("-" * 70)
    print(f"  Examples Evaluated:     {res['n_examples']:,} (elapsed: {res['elapsed_seconds']}s)")
    print(f"  Stage C Mode:           {res.get('stage_c_mode', 'UNKNOWN')}")
    if res.get("stage_c_mode") == "MOCK_ABLATION":
        print("  Stage C Notice:         DefaultMockLLMJudge (keyword test mock, NOT clinical LLM)")
    print(f"  Cascade Macro-F1:       {ov['macro_f1']:.4f}")
    print(f"  Cascade Disc. Recall:   {ov['discrepancy_recall']:.4f}")
    print("  Stage B Prob Metrics:   (Genuine calibrated probabilities, uncorrupted by Stage C)")
    print(f"    AUROC (Macro):        {_fmt(ov.get('auroc_macro'))}")
    print(f"    AUROC (Discrepancy):  {_fmt(ov.get('auroc_discrepancy'))}")
    print(f"    Brier Score:          {_fmt(ov.get('brier_score'))}")
    print(f"    ECE:                  {_fmt(ov.get('ece'))}")
    res_sub = ov.get("stage_b_resolved_subset")
    if res_sub:
        print(f"  Stage B Resolved Subset ({res_sub['n_examples']} claims):")
        print(f"    Subset Macro-F1:      {_fmt(res_sub.get('macro_f1'))}")
        print(f"    Subset Disc. Recall:  {_fmt(res_sub.get('discrepancy_recall'))}")
        print(f"    Subset AUROC Macro:   {_fmt(res_sub.get('auroc_macro'))}")
        print(f"    Subset Brier:         {_fmt(res_sub.get('brier_score'))}")
        print(f"    Subset ECE:           {_fmt(res_sub.get('ece'))}")
    print("\n  Cascade Dynamics:")
    print(f"    Stage A Resolved:     {cb['stage_a_resolved']:,} ({cb['stage_a_resolution_rate']*100:.1f}%) | Accuracy: {ca.get('accuracy_stage_A', 0.0):.4f}")
    print(f"    Stage B Resolved:     {cb['stage_b_resolved']:,} | Accuracy: {ca.get('accuracy_stage_B', 0.0):.4f}")
    print(f"    Stage C Escalated:    {cb['stage_c_invoked']:,} ({cb['stage_c_escalation_rate']*100:.1f}%) | Accuracy: {ca.get('accuracy_stage_C', 0.0):.4f}")
    print(f"    Final Labels:         SUPPORTED={cb['predicted_labels'].get('SUPPORTED', 0)}, DISCREPANCY={cb['predicted_labels'].get('DISCREPANCY', 0)}, INSUFFICIENT={cb['predicted_labels'].get('INSUFFICIENT_EVIDENCE', 0)}")
    print("-" * 70)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Phase 4 Verification Cascade Evaluation Runner")
    parser.add_argument("--data-dir", type=str, default=str(DEFAULT_DATA_DIR), help="Path to dataset directory (default: data/m2_verification_v3)")
    parser.add_argument("--results-dir", type=str, default=str(DEFAULT_RESULTS_DIR), help="Output directory for reports")
    parser.add_argument("--checkpoint", type=str, default=None, help="Path to Stage B ModernBERT checkpoint .pt")
    parser.add_argument("--split", choices=["val", "ood", "both"], default="both", help="Which split(s) to evaluate")
    parser.add_argument("--fold", type=int, default=0, help="Fold index for validation split (0-4)")
    parser.add_argument("--escalation-threshold", type=float, default=0.60, help="Stage B confidence threshold for Stage C escalation (DEC-005)")
    parser.add_argument("--always-stage-c", action="store_true", help="Force always-Stage-C for ablation comparison (§12.2)")
    parser.add_argument("--max-examples", type=int, default=None, help="Limit number of examples per split for fast smoke testing")
    parser.add_argument("--device", type=str, default=None, help="Device for Stage B ModernBERT ('cuda', 'cpu', or None for auto)")
    parser.add_argument("--stage-c-client", choices=["mock", "vllm"], default="mock", help="Stage C LLM judge client backend (default: mock)")
    parser.add_argument("--vllm-url", type=str, default="http://localhost:8000/v1", help="vLLM OpenAI-compatible endpoint URL")
    parser.add_argument("--vllm-model", type=str, default="Qwen/Qwen2.5-7B-Instruct", help="Model name served by vLLM")
    parser.add_argument(
        "--save-diagnostics",
        action="store_true",
        default=False,
        help="Save detailed per-example diagnostic JSON under the output directory",
    )

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
        device=args.device,
        stage_c_client=args.stage_c_client,
        vllm_url=args.vllm_url,
        vllm_model=args.vllm_model,
        save_diagnostics=args.save_diagnostics,
    )


if __name__ == "__main__":
    main()
