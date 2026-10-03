"""Experiment E2: Standalone GLiNER-BioMed Extraction Evaluation.

Authority: PROJECT_SPEC.md §1.3, §11, §21.1, §22.1, §25 Phase 2, §26
Reference: DEC-001, DEC-004, DEC-017, DEC-019

Evaluates the standalone GLiNER-BioMed (Ihor/gliner-biomed-base-v1.0) entity extraction
path independently across the canonical 120-document clinical benchmark:
  - 50 Laboratory Reports (data/ground_truth/, data/ocr_cache/)
  - 50 Medication Forms (data/med_ground_truth/, data/med_ocr_cache/)
  - 20 Narrative Discharge Summaries (data/narrative_ground_truth/, data/narrative_ocr_cache/)

Reports precision, recall, F1, exact match rate, TP, FP, FN across all 10 canonical
fields (Tier 1 NER target fields and Tier 2 deterministic fields), with full breakdowns
by document family (Lab, Med, Narrative) and evaluation split (CV, OOD).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from clindoc.extract.contracts import TIER_1_FIELDS, TIER_2_FIELDS, Claim
from clindoc.extract.evaluate import (
    ALL_CANONICAL_FIELDS,
    evaluate_dataset,
    evaluate_document,
    load_document_text_for_eval,
    load_ground_truth_claims,
)
from clindoc.extract.gliner_fallback import (
    GLiNERFallbackExtractor,
    MockGLiNERProvider,
    SubprocessGLiNERProvider,
)

_PROJ_ROOT = Path(__file__).resolve().parents[2]


def get_git_commit(cwd: Path) -> str:
    """Retrieve current git commit hash."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN"


def load_all_benchmark_documents(root: Path) -> list[str]:
    """Return all 120 canonical benchmark document IDs in deterministic order."""
    labs = [f"LAB_{i:04d}" for i in range(1, 51)]
    meds = [f"MED_{i:04d}" for i in range(1, 51)]
    narrs = [f"NARRATIVE_{i:04d}" for i in range(1, 21)]
    return labs + meds + narrs


def load_split_definitions(root: Path) -> dict[str, list[str]]:
    """Load canonical CV and OOD splits from split_manifest.json."""
    manifest_path = root / "data" / "m2_verification_v3" / "split_manifest.json"
    if not manifest_path.exists():
        manifest_path = root / "data" / "m2_verification" / "split_manifest.json"

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    train_ids = manifest.get("train_doc_ids", [])
    calib_ids = manifest.get("calibration_doc_ids", [])
    val_ids = manifest.get("validation_doc_ids", [])
    ood_ids = manifest.get("ood_doc_ids", [])

    cv_ids = sorted(list(set(train_ids + calib_ids + val_ids)))
    return {
        "cv": cv_ids,
        "ood": sorted(ood_ids),
        "train": sorted(train_ids),
        "calibration": sorted(calib_ids),
        "validation": sorted(val_ids),
    }


def run_e2_evaluation(
    use_mock: bool = False,
    batch_size: int = 60,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute standalone E2 GLiNER extraction evaluation across all 120 benchmark documents."""
    root = _PROJ_ROOT
    out_dir = output_dir or (root / "experiments" / "E2-gliner-extraction")
    out_dir.mkdir(parents=True, exist_ok=True)

    git_hash = get_git_commit(root)
    doc_ids = load_all_benchmark_documents(root)
    splits = load_split_definitions(root)

    print(f"Loaded {len(doc_ids)} benchmark documents (50 Lab, 50 Med, 20 Narrative).")
    print(f"Partition: {len(splits['cv'])} CV documents, {len(splits['ood'])} OOD documents.")

    # Initialize GLiNER provider and extractor
    if use_mock:
        print("Using MockGLiNERProvider (deterministic regex mock for testing).")
        provider = MockGLiNERProvider()
        model_name = "MockGLiNERProvider (mock regex)"
    else:
        print("Using SubprocessGLiNERProvider with real GLiNER-BioMed in .venv_ner...")
        provider = SubprocessGLiNERProvider()
        model_name = provider.model_name

    extractor = GLiNERFallbackExtractor(provider=provider)

    # Ingest document texts
    print("Ingesting document pages...")
    doc_texts: dict[str, Any] = {}
    missing_docs = []
    for doc_id in doc_ids:
        dt = load_document_text_for_eval(doc_id, root)
        if dt is None:
            missing_docs.append(doc_id)
        else:
            doc_texts[doc_id] = dt

    if missing_docs:
        raise RuntimeError(f"Missing OCR cache for documents: {missing_docs}")

    # Prepare page texts for batch inference
    all_items: list[tuple[str, Any, str]] = []  # (doc_id, page, page_text)
    for doc_id in doc_ids:
        dt = doc_texts[doc_id]
        for page in dt.pages:
            all_items.append((doc_id, page, page.get_page_text()))

    print(f"Total pages to extract: {len(all_items)} across {len(doc_ids)} documents.")

    # Run batch inference through provider
    t0 = time.time()
    all_page_texts = [item[2] for item in all_items]
    all_entity_lists: list[list[dict[str, Any]]] = []

    print(f"Running GLiNER inference in batches of {batch_size}...")
    for i in range(0, len(all_page_texts), batch_size):
        chunk = all_page_texts[i : i + batch_size]
        print(f"  Inferring chunk {i // batch_size + 1} ({len(chunk)} pages)...")
        chunk_results = provider.predict_batch(chunk)
        all_entity_lists.extend(chunk_results)

    inference_duration = time.time() - t0
    print(f"GLiNER inference completed in {inference_duration:.2f}s ({inference_duration / len(all_items):.3f}s/page).")

    # Map predicted entities into Claim & Evidence objects using canonical E2 path
    print("Building Claim and Evidence objects via canonical GLiNERFallbackExtractor...")
    diagnostics: dict[str, list[str]] = {"unsupported_labels": [], "rejected_labels": []}
    claims_by_doc: dict[str, list[Claim]] = {d: [] for d in doc_ids}

    for (doc_id, page, page_text), entities in zip(all_items, all_entity_lists):
        region_id = f"{doc_id}::p{page.page_number}::full_page"
        evidences, claims = extractor._build_claims(
            document_id=doc_id,
            page=page,
            region_text=page_text,
            region_id=region_id,
            entities=entities,
            char_offset=0,
            diagnostics=diagnostics,
        )
        claims_by_doc[doc_id].extend(claims)

    total_pred_claims = sum(len(c) for c in claims_by_doc.values())
    print(f"Total claims extracted by GLiNER: {total_pred_claims}")

    # Load canonical ground truth claims
    print("Loading canonical ground truth claims...")
    gt_by_doc: dict[str, Any] = {}
    for doc_id in doc_ids:
        gt_by_doc[doc_id] = load_ground_truth_claims(doc_id, root)

    total_gt_claims = sum(len(g) for g in gt_by_doc.values())
    print(f"Total ground-truth claims: {total_gt_claims}")

    # Run evaluations
    print("Running canonical evaluation harness across benchmark...")
    overall_eval = evaluate_dataset(claims_by_doc, gt_by_doc)

    # Subsystem slices
    lab_docs = [d for d in doc_ids if d.startswith("LAB_")]
    med_docs = [d for d in doc_ids if d.startswith("MED_")]
    narr_docs = [d for d in doc_ids if d.startswith("NARRATIVE_")]

    lab_eval = evaluate_dataset(
        {d: claims_by_doc[d] for d in lab_docs},
        {d: gt_by_doc[d] for d in lab_docs},
    )
    med_eval = evaluate_dataset(
        {d: claims_by_doc[d] for d in med_docs},
        {d: gt_by_doc[d] for d in med_docs},
    )
    narr_eval = evaluate_dataset(
        {d: claims_by_doc[d] for d in narr_docs},
        {d: gt_by_doc[d] for d in narr_docs},
    )

    # Split slices
    cv_eval = evaluate_dataset(
        {d: claims_by_doc[d] for d in splits["cv"]},
        {d: gt_by_doc[d] for d in splits["cv"]},
    )
    ood_eval = evaluate_dataset(
        {d: claims_by_doc[d] for d in splits["ood"]},
        {d: gt_by_doc[d] for d in splits["ood"]},
    )

    results_data = {
        "experiment_id": "E2-gliner-extraction",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": git_hash,
        "model_name": model_name,
        "is_mock": use_mock,
        "evaluation_scope": {
            "total_documents": len(doc_ids),
            "lab_documents": len(lab_docs),
            "med_documents": len(med_docs),
            "narrative_documents": len(narr_docs),
            "cv_documents": len(splits["cv"]),
            "ood_documents": len(splits["ood"]),
        },
        "timing": {
            "total_elapsed_seconds": round(inference_duration, 2),
            "seconds_per_page": round(inference_duration / max(len(all_items), 1), 3),
        },
        "diagnostics": {
            "unsupported_label_occurrences": len(diagnostics.get("unsupported_labels", [])),
            "rejected_label_occurrences": len(diagnostics.get("rejected_labels", [])),
        },
        "overall": {
            "num_documents": overall_eval["num_documents"],
            "total_gt_claims": overall_eval["total_gt_claims"],
            "total_pred_claims": overall_eval["total_pred_claims"],
            "micro_precision": overall_eval["micro_precision"],
            "micro_recall": overall_eval["micro_recall"],
            "micro_f1": overall_eval["micro_f1"],
            "macro_f1": overall_eval["macro_f1"],
            "overall_exact_match_rate": overall_eval["overall_exact_match_rate"],
            "metrics_per_field": overall_eval["metrics_per_field"],
        },
        "by_document_type": {
            "laboratory": {
                "num_documents": lab_eval["num_documents"],
                "total_gt_claims": lab_eval["total_gt_claims"],
                "total_pred_claims": lab_eval["total_pred_claims"],
                "micro_precision": lab_eval["micro_precision"],
                "micro_recall": lab_eval["micro_recall"],
                "micro_f1": lab_eval["micro_f1"],
                "macro_f1": lab_eval["macro_f1"],
                "overall_exact_match_rate": lab_eval["overall_exact_match_rate"],
                "metrics_per_field": lab_eval["metrics_per_field"],
            },
            "medication": {
                "num_documents": med_eval["num_documents"],
                "total_gt_claims": med_eval["total_gt_claims"],
                "total_pred_claims": med_eval["total_pred_claims"],
                "micro_precision": med_eval["micro_precision"],
                "micro_recall": med_eval["micro_recall"],
                "micro_f1": med_eval["micro_f1"],
                "macro_f1": med_eval["macro_f1"],
                "overall_exact_match_rate": med_eval["overall_exact_match_rate"],
                "metrics_per_field": med_eval["metrics_per_field"],
            },
            "narrative": {
                "num_documents": narr_eval["num_documents"],
                "total_gt_claims": narr_eval["total_gt_claims"],
                "total_pred_claims": narr_eval["total_pred_claims"],
                "micro_precision": narr_eval["micro_precision"],
                "micro_recall": narr_eval["micro_recall"],
                "micro_f1": narr_eval["micro_f1"],
                "macro_f1": narr_eval["macro_f1"],
                "overall_exact_match_rate": narr_eval["overall_exact_match_rate"],
                "metrics_per_field": narr_eval["metrics_per_field"],
            },
        },
        "by_split": {
            "cross_validation": {
                "num_documents": cv_eval["num_documents"],
                "total_gt_claims": cv_eval["total_gt_claims"],
                "total_pred_claims": cv_eval["total_pred_claims"],
                "micro_precision": cv_eval["micro_precision"],
                "micro_recall": cv_eval["micro_recall"],
                "micro_f1": cv_eval["micro_f1"],
                "macro_f1": cv_eval["macro_f1"],
                "overall_exact_match_rate": cv_eval["overall_exact_match_rate"],
                "metrics_per_field": cv_eval["metrics_per_field"],
            },
            "out_of_distribution": {
                "num_documents": ood_eval["num_documents"],
                "total_gt_claims": ood_eval["total_gt_claims"],
                "total_pred_claims": ood_eval["total_pred_claims"],
                "micro_precision": ood_eval["micro_precision"],
                "micro_recall": ood_eval["micro_recall"],
                "micro_f1": ood_eval["micro_f1"],
                "macro_f1": ood_eval["macro_f1"],
                "overall_exact_match_rate": ood_eval["overall_exact_match_rate"],
                "metrics_per_field": ood_eval["metrics_per_field"],
            },
        },
    }

    # Save machine-readable JSON
    results_json_path = out_dir / "e2_results.json"
    with open(results_json_path, "w", encoding="utf-8") as f:
        json.dump(results_data, f, indent=2)
    print(f"Persisted results JSON to: {results_json_path}")

    return results_data


def main():
    parser = argparse.ArgumentParser(description="Run E2 Standalone GLiNER Extraction Evaluation")
    parser.add_argument("--mock", action="store_true", help="Use MockGLiNERProvider for quick testing without GPU/model")
    parser.add_argument("--batch-size", type=int, default=60, help="Batch size for GLiNER inference")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory path")
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else None
    results = run_e2_evaluation(use_mock=args.mock, batch_size=args.batch_size, output_dir=out_dir)

    print("\n" + "=" * 80)
    print("E2 EVALUATION SUMMARY RESULTS")
    print("=" * 80)
    ov = results["overall"]
    print(f"Overall Documents: {ov['num_documents']}")
    print(f"Total GT Claims: {ov['total_gt_claims']} | Total Predicted Claims: {ov['total_pred_claims']}")
    print(f"Micro Precision: {ov['micro_precision']} | Micro Recall: {ov['micro_recall']} | Micro F1: {ov['micro_f1']}")
    print(f"Macro F1: {ov['macro_f1']} | Overall Exact Match Rate: {ov['overall_exact_match_rate']}")
    print("\nPer-Field Metrics:")
    for ft, m in ov["metrics_per_field"].items():
        print(f"  {ft:20s}: P={m['precision']:.4f} | R={m['recall']:.4f} | F1={m['f1']:.4f} | EM={m['exact_match_rate']:.4f} (GT={m['gt_count']}, Pred={m['pred_count']}, TP={m['tp']})")


if __name__ == "__main__":
    main()
