"""Canonical E1–E4 Extraction Evaluation Harness.

Authority: PROJECT_SPEC.md §21.1, §21.2, §22.1
Experiments:
  - E1: Deterministic-only extraction
  - E2: GLiNER-only extraction
  - E3: LLM-only extraction (schema-constrained)
  - E4: Hybrid extraction router (production architecture)

Evaluates extracted Claim objects against canonical ground truth annotations
using the canonical 10-field vocabulary (DEC-017) and Claim/Evidence schemas.
Do NOT use legacy ExtractedLabReport or ExtractedPrescriptionDocument schemas.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from clindoc.extract.contracts import (
    Claim, DocumentExtractionOutput, FieldType, TIER_1_FIELDS, TIER_2_FIELDS,
)
from clindoc.extract.deterministic import DeterministicExtractor, _FLAG_MAP, _ROUTE_VOCAB
from clindoc.extract.gliner_fallback import GLiNERFallbackExtractor, MockGLiNERProvider
from clindoc.extract.llm_extractor import NarrativeExtractor, MockLLMProvider
from clindoc.extract.router import ExtractionRouter
from clindoc.ocr.document_text import DocumentText
from clindoc.ocr.ingest import ingest_from_record

_PROJ_ROOT = Path(__file__).resolve().parents[3]

ALL_CANONICAL_FIELDS: list[str] = sorted(list(TIER_1_FIELDS | TIER_2_FIELDS))


# ─── CANONICAL GROUND TRUTH CLAIM ────────────────────────────────────────────

class GroundTruthClaim(BaseModel):
    """Canonical ground truth claim model for extraction evaluation."""
    model_config = ConfigDict(extra="ignore")

    document_id: str
    field_type: str
    value: str
    group_id: Optional[str] = None
    value_numeric: Optional[float] = None
    unit_raw: Optional[str] = None
    evidence_text: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    page_number: Optional[int] = None


# ─── VALUE NORMALIZATION & MATCHING ──────────────────────────────────────────

def _format_num(val: Any) -> str:
    if isinstance(val, (int, float)):
        return str(int(val)) if val == int(val) else str(val)
    return str(val)


def normalize_field_value(field_type: str, val: Any) -> str:
    """Normalize extracted or ground truth string value for comparison."""
    if val is None:
        return ""
    text = str(val).strip()

    if field_type in ("lab.value", "med.dose"):
        # Extract leading numeric component if present
        m = re.search(r"(-?\d+(?:\.\d+)?)", text)
        if m:
            try:
                num = float(m.group(1))
                return _format_num(round(num, 4))
            except ValueError:
                pass
        return re.sub(r"\s+", " ", text).lower()

    if field_type == "lab.flag":
        upper = text.upper()
        return _FLAG_MAP.get(upper, upper)

    if field_type == "med.route":
        lower = text.lower()
        return _ROUTE_VOCAB.get(lower, lower)

    if field_type == "lab.reference_range":
        # Normalize dash variations and spacing: e.g. "70 - 99" vs "70-99"
        norm = re.sub(r"\s*[-–—]\s*", " - ", text)
        return re.sub(r"\s+", " ", norm).strip().lower()

    # Default text normalization: collapse whitespace, lowercase
    return re.sub(r"\s+", " ", text).strip().lower()


def is_value_match(gt_claim: GroundTruthClaim | Claim, pred_claim: Claim) -> bool:
    """Determine if a predicted claim exactly matches a ground truth claim."""
    if gt_claim.field_type != pred_claim.field_type:
        return False

    field_type = gt_claim.field_type

    # Numeric matching for lab.value and med.dose
    if field_type in ("lab.value", "med.dose"):
        gt_num = getattr(gt_claim, "value_numeric", None)
        pred_num = getattr(pred_claim, "value_numeric", None)

        if gt_num is not None and pred_num is not None:
            if abs(gt_num - pred_num) < 1e-4:
                return True

    gt_norm = normalize_field_value(field_type, gt_claim.value)
    pred_norm = normalize_field_value(field_type, pred_claim.value)

    if gt_norm and pred_norm and gt_norm == pred_norm:
        return True

    return False


def is_partial_match(gt_claim: GroundTruthClaim | Claim, pred_claim: Claim) -> bool:
    """Check for partial/subsequence match when exact match does not hold."""
    if gt_claim.field_type != pred_claim.field_type:
        return False

    field_type = gt_claim.field_type
    gt_norm = normalize_field_value(field_type, gt_claim.value)
    pred_norm = normalize_field_value(field_type, pred_claim.value)

    if not gt_norm or not pred_norm:
        return False

    # Partial substring overlap
    if gt_norm in pred_norm or pred_norm in gt_norm:
        return True

    # Token overlap for multi-word fields
    gt_tokens = set(gt_norm.split())
    pred_tokens = set(pred_norm.split())
    if gt_tokens and pred_tokens and len(gt_tokens & pred_tokens) / max(len(gt_tokens), 1) >= 0.5:
        return True

    return False


# ─── GROUND TRUTH LOADER ─────────────────────────────────────────────────────

def load_ground_truth_claims(
    doc_id: str,
    base_dir: Path | None = None,
) -> list[GroundTruthClaim]:
    """Load canonical ground truth claims for a given document ID."""
    root = base_dir or _PROJ_ROOT
    claims: list[GroundTruthClaim] = []

    doc_id_upper = doc_id.upper()

    if doc_id_upper.startswith("LAB_"):
        gt_file = root / "data" / "ground_truth" / f"{doc_id}.json"
        if not gt_file.exists():
            return []
        with open(gt_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        tests = data.get("tests", [])
        for i, t in enumerate(tests):
            group_id = f"{doc_id}_test_{i}"

            # lab.test_name
            if t.get("test_name"):
                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=group_id,
                    field_type="lab.test_name",
                    value=str(t["test_name"]),
                ))

            # lab.value
            if t.get("value") is not None:
                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=group_id,
                    field_type="lab.value",
                    value=str(t["value"]),
                    value_numeric=float(t["value"]),
                    unit_raw=t.get("unit"),
                ))

            # lab.unit
            if t.get("unit"):
                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=group_id,
                    field_type="lab.unit",
                    value=str(t["unit"]),
                ))

            # lab.reference_range
            ref_text = t.get("ref_range_text")
            if ref_text:
                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=group_id,
                    field_type="lab.reference_range",
                    value=str(ref_text),
                ))

            # lab.flag
            if t.get("flag"):
                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=group_id,
                    field_type="lab.flag",
                    value=str(t["flag"]),
                ))

    elif doc_id_upper.startswith("MED_"):
        gt_file = root / "data" / "med_ground_truth" / f"{doc_id}.json"
        if not gt_file.exists():
            return []
        with open(gt_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        medications = data.get("medications", [])
        for i, m in enumerate(medications):
            group_id = f"{doc_id}_med_{i}"

            # med.name
            if m.get("medication_name"):
                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=group_id,
                    field_type="med.name",
                    value=str(m["medication_name"]),
                ))

            # med.dose
            dose = m.get("dose")
            dose_unit = m.get("dose_unit", "")
            if dose is not None:
                dose_str = f"{_format_num(dose)} {dose_unit}".strip()
                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=group_id,
                    field_type="med.dose",
                    value=dose_str,
                    value_numeric=float(dose),
                    unit_raw=dose_unit,
                ))

            # med.route
            if m.get("route"):
                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=group_id,
                    field_type="med.route",
                    value=str(m["route"]),
                ))

            # med.frequency
            if m.get("frequency"):
                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=group_id,
                    field_type="med.frequency",
                    value=str(m["frequency"]),
                ))

            # med.duration
            if m.get("duration"):
                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=group_id,
                    field_type="med.duration",
                    value=str(m["duration"]),
                ))

    elif doc_id_upper.startswith("NARRATIVE_"):
        gt_file = root / "data" / "narrative_ground_truth" / f"{doc_id}.json"
        if not gt_file.exists():
            return []
        with open(gt_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        narr_claims = data.get("claims", [])
        for c in narr_claims:
            ft = c.get("field_type")
            if ft in ALL_CANONICAL_FIELDS:
                # Use canonical_value if present, fallback to value
                c_val = c.get("canonical_value")
                val_str = str(c_val) if c_val is not None else str(c.get("value", ""))
                val_num = None
                if isinstance(c_val, (int, float)):
                    val_num = float(c_val)

                claims.append(GroundTruthClaim(
                    document_id=doc_id,
                    group_id=c.get("group_id"),
                    field_type=ft,
                    value=val_str,
                    value_numeric=val_num,
                    unit_raw=c.get("unit"),
                    evidence_text=c.get("evidence_text"),
                    char_start=c.get("char_start"),
                    char_end=c.get("char_end"),
                    page_number=c.get("page_number", 1),
                ))

    return claims


# ─── FIELD-LEVEL MATCHING ENGINE ─────────────────────────────────────────────

def match_claims_for_field(
    gt_claims: Sequence[GroundTruthClaim | Claim],
    pred_claims: Sequence[Claim],
    field_type: str,
) -> dict[str, Any]:
    """Perform bipartite greedy matching for claims of a single field_type.

    Prioritizes:
    1. Group-aligned exact matches
    2. Any exact match
    3. Group-aligned partial matches
    4. Any partial match
    """
    gts = [g for g in gt_claims if g.field_type == field_type]
    preds = [p for p in pred_claims if p.field_type == field_type]

    n_gt = len(gts)
    n_pred = len(preds)

    if n_gt == 0 and n_pred == 0:
        return {
            "field_type": field_type,
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "exact_matches": 0,
            "partial_matches": 0,
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "exact_match_rate": 1.0,
            "gt_count": 0,
            "pred_count": 0,
        }

    matched_gt: set[int] = set()
    matched_pred: set[int] = set()
    exact_matches = 0
    partial_matches = 0

    # 1. Exact matches (group-aligned first)
    for g_idx, g in enumerate(gts):
        if g_idx in matched_gt:
            continue
        for p_idx, p in enumerate(preds):
            if p_idx in matched_pred:
                continue
            if g.group_id and p.group_id and g.group_id == p.group_id:
                if is_value_match(g, p):
                    matched_gt.add(g_idx)
                    matched_pred.add(p_idx)
                    exact_matches += 1
                    break

    # 2. Exact matches (ungrouped)
    for g_idx, g in enumerate(gts):
        if g_idx in matched_gt:
            continue
        for p_idx, p in enumerate(preds):
            if p_idx in matched_pred:
                continue
            if is_value_match(g, p):
                matched_gt.add(g_idx)
                matched_pred.add(p_idx)
                exact_matches += 1
                break

    # 3. Partial matches
    for g_idx, g in enumerate(gts):
        if g_idx in matched_gt:
            continue
        for p_idx, p in enumerate(preds):
            if p_idx in matched_pred:
                continue
            if is_partial_match(g, p):
                matched_gt.add(g_idx)
                matched_pred.add(p_idx)
                partial_matches += 1
                break

    # Standard extraction metric counts
    tp = exact_matches
    fp = n_pred - tp
    fn = n_gt - tp

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    em_rate = exact_matches / n_gt if n_gt > 0 else (1.0 if n_pred == 0 else 0.0)

    return {
        "field_type": field_type,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "exact_matches": exact_matches,
        "partial_matches": partial_matches,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "exact_match_rate": round(em_rate, 4),
        "gt_count": n_gt,
        "pred_count": n_pred,
    }


# ─── DOCUMENT & DATASET EVALUATION ───────────────────────────────────────────

def evaluate_document(
    doc_id: str,
    pred_claims: Sequence[Claim],
    gt_claims: Sequence[GroundTruthClaim | Claim] | None = None,
) -> dict[str, Any]:
    """Evaluate extraction predictions for a single document against ground truth."""
    if gt_claims is None:
        gt_claims = load_ground_truth_claims(doc_id)

    field_results: dict[str, dict[str, Any]] = {}

    for ft in ALL_CANONICAL_FIELDS:
        field_results[ft] = match_claims_for_field(gt_claims, pred_claims, ft)

    active_fields = [ft for ft in ALL_CANONICAL_FIELDS if field_results[ft]["gt_count"] > 0 or field_results[ft]["pred_count"] > 0]
    total_tp = sum(field_results[ft]["tp"] for ft in active_fields)
    total_fp = sum(field_results[ft]["fp"] for ft in active_fields)
    total_fn = sum(field_results[ft]["fn"] for ft in active_fields)
    total_gt = sum(field_results[ft]["gt_count"] for ft in active_fields)
    total_pred = sum(field_results[ft]["pred_count"] for ft in active_fields)
    total_em = sum(field_results[ft]["exact_matches"] for ft in active_fields)

    micro_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    micro_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) > 0 else 0.0
    macro_f1 = sum(field_results[ft]["f1"] for ft in active_fields) / len(active_fields) if active_fields else 0.0
    em_rate = total_em / total_gt if total_gt > 0 else (1.0 if total_pred == 0 else 0.0)

    return {
        "doc_id": doc_id,
        "fields": field_results,
        "total_gt": total_gt,
        "total_pred": total_pred,
        "total_tp": total_tp,
        "total_fp": total_fp,
        "total_fn": total_fn,
        "micro_precision": round(micro_p, 4),
        "micro_recall": round(micro_r, 4),
        "micro_f1": round(micro_f1, 4),
        "macro_f1": round(macro_f1, 4),
        "exact_match_rate": round(em_rate, 4),
    }


def evaluate_dataset(
    predictions_by_doc: dict[str, Sequence[Claim]],
    gt_by_doc: dict[str, Sequence[GroundTruthClaim | Claim]] | None = None,
) -> dict[str, Any]:
    """Aggregate extraction evaluation metrics across a dataset of documents."""
    all_doc_ids = sorted(predictions_by_doc.keys())
    if not all_doc_ids:
        return {
            "num_documents": 0,
            "metrics_per_field": {},
            "macro_f1": 0.0,
            "micro_precision": 0.0,
            "micro_recall": 0.0,
            "micro_f1": 0.0,
            "overall_exact_match_rate": 0.0,
            "total_gt_claims": 0,
            "total_pred_claims": 0,
            "per_document": [],
        }

    # Aggregate counts per field across all documents
    field_counts = {
        ft: {"tp": 0, "fp": 0, "fn": 0, "exact_matches": 0, "gt_count": 0, "pred_count": 0}
        for ft in ALL_CANONICAL_FIELDS
    }

    per_doc_evals = []
    for doc_id in all_doc_ids:
        preds = predictions_by_doc[doc_id]
        gts = gt_by_doc.get(doc_id) if gt_by_doc is not None else load_ground_truth_claims(doc_id)
        doc_eval = evaluate_document(doc_id, preds, gts)
        per_doc_evals.append(doc_eval)

        for ft in ALL_CANONICAL_FIELDS:
            fr = doc_eval["fields"][ft]
            field_counts[ft]["tp"] += fr["tp"]
            field_counts[ft]["fp"] += fr["fp"]
            field_counts[ft]["fn"] += fr["fn"]
            field_counts[ft]["exact_matches"] += fr["exact_matches"]
            field_counts[ft]["gt_count"] += fr["gt_count"]
            field_counts[ft]["pred_count"] += fr["pred_count"]

    metrics_per_field: dict[str, dict[str, Any]] = {}
    for ft in ALL_CANONICAL_FIELDS:
        fc = field_counts[ft]
        tp = fc["tp"]
        fp = fc["fp"]
        fn = fc["fn"]
        em = fc["exact_matches"]
        gt_cnt = fc["gt_count"]
        pred_cnt = fc["pred_count"]

        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        em_rate = em / gt_cnt if gt_cnt > 0 else (1.0 if pred_cnt == 0 else 0.0)

        metrics_per_field[ft] = {
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
            "exact_match_rate": round(em_rate, 4),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "exact_matches": em,
            "gt_count": gt_cnt,
            "pred_count": pred_cnt,
        }

    # Only include fields with GT or predictions in macro calculations
    active_fields = [ft for ft in ALL_CANONICAL_FIELDS if metrics_per_field[ft]["gt_count"] > 0 or metrics_per_field[ft]["pred_count"] > 0]
    total_tp = sum(metrics_per_field[ft]["tp"] for ft in active_fields)
    total_fp = sum(metrics_per_field[ft]["fp"] for ft in active_fields)
    total_fn = sum(metrics_per_field[ft]["fn"] for ft in active_fields)
    total_gt = sum(metrics_per_field[ft]["gt_count"] for ft in active_fields)
    total_pred = sum(metrics_per_field[ft]["pred_count"] for ft in active_fields)
    total_em = sum(metrics_per_field[ft]["exact_matches"] for ft in active_fields)

    micro_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    micro_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) > 0 else 0.0
    macro_f1 = sum(metrics_per_field[ft]["f1"] for ft in active_fields) / len(active_fields) if active_fields else 0.0
    overall_em = total_em / total_gt if total_gt > 0 else (1.0 if total_pred == 0 else 0.0)

    return {
        "num_documents": len(all_doc_ids),
        "metrics_per_field": metrics_per_field,
        "macro_f1": round(macro_f1, 4),
        "micro_precision": round(micro_p, 4),
        "micro_recall": round(micro_r, 4),
        "micro_f1": round(micro_f1, 4),
        "overall_exact_match_rate": round(overall_em, 4),
        "total_gt_claims": total_gt,
        "total_pred_claims": total_pred,
        "per_document": per_doc_evals,
    }


# ─── CANONICAL E1–E4 EVALUATION RUNNER ────────────────────────────────────────

def load_document_text_for_eval(doc_id: str, base_dir: Path | None = None) -> DocumentText | None:
    """Load DocumentText from ocr_cache for evaluation."""
    root = base_dir or _PROJ_ROOT
    doc_id_upper = doc_id.upper()

    if doc_id_upper.startswith("LAB_"):
        ocr_file = root / "data" / "ocr_cache" / f"{doc_id}.json"
    elif doc_id_upper.startswith("MED_"):
        ocr_file = root / "data" / "med_ocr_cache" / f"{doc_id}.json"
    elif doc_id_upper.startswith("NARRATIVE_"):
        ocr_file = root / "data" / "narrative_ocr_cache" / f"{doc_id}.json"
    else:
        ocr_file = root / "data" / "ocr_cache" / f"{doc_id}.json"

    if not ocr_file.exists():
        return None

    with open(ocr_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return ingest_from_record(data, doc_id=doc_id)


def load_split_manifest(base_dir: Path | None = None) -> dict[str, list[str]]:
    """Load canonical train, calibration, validation, and ood splits."""
    root = base_dir or _PROJ_ROOT
    manifest_path = root / "data" / "m2_verification" / "split_manifest.json"
    if not manifest_path.exists():
        return {"train": [], "calibration": [], "validation": [], "ood": [], "cv": []}

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    train = data.get("train_doc_ids", [])
    calib = data.get("calibration_doc_ids", [])
    val = data.get("validation_doc_ids", [])
    ood = data.get("ood_doc_ids", [])
    cv = sorted(list(set(train + calib + val)))

    return {
        "train": train,
        "calibration": calib,
        "validation": val,
        "ood": ood,
        "cv": cv,
    }


def run_path_extraction(
    doc: DocumentText,
    path: Literal["deterministic", "gliner", "llm_narrative", "hybrid"],
    router: ExtractionRouter | None = None,
) -> list[Claim]:
    """Execute a single extraction path on a DocumentText."""
    if router is None:
        router = ExtractionRouter(
            gliner_provider=MockGLiNERProvider(),
            llm_provider=MockLLMProvider(),
        )

    if path == "hybrid":
        output = router.extract_document(doc)
        return output.all_claims

    claims: list[Claim] = []
    is_lab = doc.document_id.upper().startswith("LAB_")

    for page in doc.pages:
        page_text = page.get_page_text()

        if path == "deterministic":
            # Force deterministic path only
            evidences, det_claims = router.deterministic.extract_region(
                doc.document_id, page, page_text, region_id=f"{doc.document_id}::p{page.page_number}::r0",
                is_lab=is_lab, char_offset=0,
            )
            claims.extend(det_claims)

        elif path == "gliner":
            # Run GLiNER across the page text
            evidences, gliner_claims = router.gliner.extract(
                doc.document_id, page, page_text, region_id=f"{doc.document_id}::p{page.page_number}::r0",
                char_offset=0,
            )
            claims.extend(gliner_claims)

        elif path == "llm_narrative":
            # Run LLM narrative extractor across the page text
            evidences, narr_claims, diag = router.narrative.extract(
                doc.document_id, page, page_text, region_id=f"{doc.document_id}::p{page.page_number}::r0",
                char_offset=0,
            )
            claims.extend(narr_claims)

    return claims


def run_extraction_evaluation(
    extraction_path: Literal["deterministic", "gliner", "llm_narrative", "hybrid"],
    experiment_id: str | None = None,
    split: Literal["all", "cv", "validation", "ood"] = "cv",
    doc_ids: list[str] | None = None,
    router: ExtractionRouter | None = None,
    base_dir: Path | None = None,
    save_path: Path | None = None,
) -> dict[str, Any]:
    """Run E1–E4 evaluation harness for the specified extraction path."""
    exp_map = {
        "deterministic": "E1",
        "gliner": "E2",
        "llm_narrative": "E3",
        "hybrid": "E4",
    }
    exp_id = experiment_id or exp_map.get(extraction_path, "E_CUSTOM")

    # Resolve document IDs
    if doc_ids is None:
        splits = load_split_manifest(base_dir)
        if split == "all":
            doc_ids = sorted(list(set(splits["cv"] + splits["ood"])))
        elif split == "cv":
            doc_ids = splits["cv"]
        elif split == "validation":
            doc_ids = splits["validation"]
        elif split == "ood":
            doc_ids = splits["ood"]
        else:
            doc_ids = splits.get(split, [])

    predictions_by_doc: dict[str, list[Claim]] = {}
    gt_by_doc: dict[str, list[GroundTruthClaim]] = {}

    for doc_id in doc_ids:
        doc_text = load_document_text_for_eval(doc_id, base_dir)
        if doc_text is None:
            continue

        preds = run_path_extraction(doc_text, extraction_path, router=router)
        predictions_by_doc[doc_id] = preds

        gts = load_ground_truth_claims(doc_id, base_dir)
        gt_by_doc[doc_id] = gts

    eval_result = evaluate_dataset(predictions_by_doc, gt_by_doc)

    report = {
        "experiment_id": exp_id,
        "extraction_path": extraction_path,
        "split": split,
        "num_documents": eval_result["num_documents"],
        "macro_f1": eval_result["macro_f1"],
        "micro_precision": eval_result["micro_precision"],
        "micro_recall": eval_result["micro_recall"],
        "micro_f1": eval_result["micro_f1"],
        "overall_exact_match_rate": eval_result["overall_exact_match_rate"],
        "total_gt_claims": eval_result["total_gt_claims"],
        "total_pred_claims": eval_result["total_pred_claims"],
        "metrics_per_field": eval_result["metrics_per_field"],
    }

    if save_path:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    return report
