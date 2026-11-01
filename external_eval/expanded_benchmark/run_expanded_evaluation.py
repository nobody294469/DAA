"""Evaluation Runner for the Expanded Diverse Synthetic Benchmark (60 documents, 999 claims).

Authority: User Request / Expanded Synthetic Benchmark Validation
Location: external_eval/expanded_benchmark/run_expanded_evaluation.py
"""
from __future__ import annotations

import datetime
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "external_eval"))

from clindoc.extract.contracts import Claim
from clindoc.extract.deterministic import DeterministicExtractor
from clindoc.extract.evaluate import (
    ALL_CANONICAL_FIELDS,
    GroundTruthClaim,
    evaluate_dataset,
)
from clindoc.extract.gliner_fallback import SubprocessGLiNERProvider
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict
from external_eval.experiments.poc_medspacy_section_v2.poc_v2_preprocessor import (
    SectionAwareExtractorV2,
)
from external_eval.experiments.poc_medspacy_section_v2.section_detector import (
    ClinicalSectionSpan,
    MedSpaCySectionDetectorV2,
)
from external_eval.runners.synthetic_benchmark_runner import (
    run_cascade_predictions,
    run_deterministic_predictions,
    run_gliner_predictions,
)

BENCHMARK_DIR = REPO_ROOT / "external_eval" / "expanded_benchmark"
MANIFEST_PATH = BENCHMARK_DIR / "manifest.json"
OUTPUT_PATH = BENCHMARK_DIR / "expanded_benchmark_results.json"


def load_expanded_document(entry: dict[str, Any]) -> tuple[DocumentText, list[GroundTruthClaim]]:
    doc_id = entry["doc_id"]
    txt_path = REPO_ROOT / entry["text_file"]
    gt_path = REPO_ROOT / entry["gt_file"]

    raw_text = txt_path.read_text(encoding="utf-8")
    lines = raw_text.splitlines()

    tokens: list[Token] = []
    for line_idx, line in enumerate(lines):
        line_id = f"L{line_idx}"
        words = line.split()
        for w in words:
            tokens.append(
                Token(
                    text=w,
                    bbox=BBoxDict(x0=0.0, y0=float(line_idx * 15), x1=100.0, y1=float(line_idx * 15 + 12)),
                    confidence=1.0,
                    line_id=line_id,
                )
            )

    page = DocumentPage(
        page_number=1,
        width_px=1000,
        height_px=max(1000, len(lines) * 20),
        tokens=tokens,
    )
    doc = DocumentText(
        document_id=doc_id,
        source_type="born_digital",
        pages=[page],
    )

    with open(gt_path, "r", encoding="utf-8") as f:
        gt_data = json.load(f)

    gt_claims: list[GroundTruthClaim] = []
    for c in gt_data.get("claims", []):
        gt_claims.append(
            GroundTruthClaim(
                document_id=doc_id,
                field_type=c["field_type"],
                value=c["value"],
                value_numeric=c.get("value_numeric"),
                unit_raw=c.get("unit_raw"),
                evidence_text=c.get("evidence_text"),
                char_start=c.get("char_start"),
                char_end=c.get("char_end"),
                page_number=c.get("page_number", 1),
            )
        )

    return doc, gt_claims


def run_evaluation() -> dict[str, Any]:
    print(f"=== Running Expanded Benchmark Evaluation on 60 Documents ===")
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    docs: list[DocumentText] = []
    gt_by_doc: dict[str, list[GroundTruthClaim]] = {}

    for entry in manifest["documents"]:
        doc, gt = load_expanded_document(entry)
        docs.append(doc)
        gt_by_doc[doc.document_id] = gt

    total_gt = sum(len(g) for g in gt_by_doc.values())
    print(f"Loaded {len(docs)} documents with {total_gt} total GT claims.\n")

    # 1. BASELINE DETERMINISTIC PREDICTIONS
    print("--- [1/4] Running Baseline Deterministic Extractor ---")
    base_det_preds = run_deterministic_predictions(docs)

    # 2. GLINER PREDICTIONS
    print("--- [2/4] Running Live GLiNER Subprocess Provider ---")
    gliner_provider = SubprocessGLiNERProvider()
    gliner_preds = run_gliner_predictions(docs, gliner_provider)

    # 3. BASELINE CASCADE
    print("--- [3/4] Running Baseline Cascade ---")
    base_cascade_preds = run_cascade_predictions(docs, base_det_preds, gliner_preds, threshold=0.75)

    eval_base_det = evaluate_dataset(predictions_by_doc=base_det_preds, gt_by_doc=gt_by_doc)
    eval_base_gliner = evaluate_dataset(predictions_by_doc=gliner_preds, gt_by_doc=gt_by_doc)
    eval_base_cascade = evaluate_dataset(predictions_by_doc=base_cascade_preds, gt_by_doc=gt_by_doc)

    # 4. POC V2 INTERVENTION
    print("--- [4/4] Running PoC v2 (MedSpaCy Section-Aware Isolation) ---")
    section_detector = MedSpaCySectionDetectorV2()
    section_extractor = SectionAwareExtractorV2(section_detector=section_detector)

    interv_det_preds: dict[str, list[Claim]] = {}
    doc_sections_summary: dict[str, list[str]] = {}

    for doc in docs:
        claims, sections = section_extractor.extract_document_deterministic(doc)
        interv_det_preds[doc.document_id] = claims
        doc_sections_summary[doc.document_id] = [f"{s.category}: '{s.title}'" for s in sections]

    interv_cascade_preds = run_cascade_predictions(docs, interv_det_preds, gliner_preds, threshold=0.75)

    eval_interv_det = evaluate_dataset(predictions_by_doc=interv_det_preds, gt_by_doc=gt_by_doc)
    eval_interv_cascade = evaluate_dataset(predictions_by_doc=interv_cascade_preds, gt_by_doc=gt_by_doc)

    # Compile Results
    results = {
        "evaluation_name": "ClinicalDoc Expanded Benchmark Generalization Evaluation",
        "benchmark_manifest": str(MANIFEST_PATH),
        "total_documents": len(docs),
        "total_ground_truth_claims": total_gt,
        "evaluation_timestamp": datetime.datetime.now().isoformat(),
        "baseline": {
            "deterministic": eval_base_det,
            "gliner": eval_base_gliner,
            "cascade": eval_base_cascade,
        },
        "poc_v2_intervention": {
            "deterministic": eval_interv_det,
            "cascade": eval_interv_cascade,
        },
    }

    OUTPUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved evaluation results to {OUTPUT_PATH}\n")

    # Print Side-by-Side Comparison Table for Cascade
    print("====================================================================================================")
    print("EXPANDED BENCHMARK CASCADE RESULTS (Baseline Cascade vs. PoC v2 Cascade): 60 Docs, 999 Claims")
    print("====================================================================================================")
    print(f"{'Field':22s} | {'Base TP':7s} {'FP':5s} {'FN':5s} {'Prec':6s} {'Rec':6s} {'F1':6s} | {'PoC2 TP':7s} {'FP':5s} {'FN':5s} {'Prec':6s} {'Rec':6s} {'F1':6s} | {'Delta F1':8s}")
    print("-" * 105)

    for field_name in ALL_CANONICAL_FIELDS:
        bm = eval_base_cascade["metrics_per_field"].get(field_name)
        im = eval_interv_cascade["metrics_per_field"].get(field_name)
        if not bm or not im:
            continue
        d_f1 = im["f1"] - bm["f1"]
        sign = "+" if d_f1 >= 0 else ""
        print(
            f"{field_name:22s} | "
            f"{bm['tp']:7d} {bm['fp']:5d} {bm['fn']:5d} {bm['precision']:6.3f} {bm['recall']:6.3f} {bm['f1']:6.3f} | "
            f"{im['tp']:7d} {im['fp']:5d} {im['fn']:5d} {im['precision']:6.3f} {im['recall']:6.3f} {im['f1']:6.3f} | "
            f"{sign}{d_f1:6.3f}"
        )

    print("-" * 105)
    b_prec = eval_base_cascade["micro_precision"]
    b_rec = eval_base_cascade["micro_recall"]
    b_f1 = eval_base_cascade["micro_f1"]
    b_tp = sum(m["tp"] for m in eval_base_cascade["metrics_per_field"].values())
    b_fp = sum(m["fp"] for m in eval_base_cascade["metrics_per_field"].values())
    b_fn = sum(m["fn"] for m in eval_base_cascade["metrics_per_field"].values())

    i_prec = eval_interv_cascade["micro_precision"]
    i_rec = eval_interv_cascade["micro_recall"]
    i_f1 = eval_interv_cascade["micro_f1"]
    i_tp = sum(m["tp"] for m in eval_interv_cascade["metrics_per_field"].values())
    i_fp = sum(m["fp"] for m in eval_interv_cascade["metrics_per_field"].values())
    i_fn = sum(m["fn"] for m in eval_interv_cascade["metrics_per_field"].values())

    d_mic_f1 = i_f1 - b_f1

    print(
        f"{'MICRO AVERAGE':22s} | "
        f"{b_tp:7d} {b_fp:5d} {b_fn:5d} {b_prec:6.4f} {b_rec:6.4f} {b_f1:6.4f} | "
        f"{i_tp:7d} {i_fp:5d} {i_fn:5d} {i_prec:6.4f} {i_rec:6.4f} {i_f1:6.4f} | "
        f"{'+' if d_mic_f1 >= 0 else ''}{d_mic_f1:6.4f}"
    )

    b_mac = eval_base_cascade["macro_f1"]
    i_mac = eval_interv_cascade["macro_f1"]
    d_mac = i_mac - b_mac
    print(f"{'MACRO F1':22s} | {' ':39s} {b_mac:6.4f} | {' ':39s} {i_mac:6.4f} | {'+' if d_mac >= 0 else ''}{d_mac:6.4f}")
    print("====================================================================================================\n")

    return results


if __name__ == "__main__":
    run_evaluation()
