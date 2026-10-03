"""Experiment E3: Standalone Narrative/Free-Text Extraction Evaluation.

Authority: PROJECT_SPEC.md §1.3, §11, §13, §21.1, §22.1, §25 Phase 2, §26
Reference: DEC-001, DEC-006, DEC-013, DEC-017, DEC-019, DEC-020

Evaluates the standalone LLM narrative extraction path across the canonical 20 narrative
clinical documents:
  - 15 Cross-Validation Narrative Documents (NARRATIVE_0001–NARRATIVE_0015)
  - 5 Out-of-Distribution Narrative Documents (NARRATIVE_0016–NARRATIVE_0020)
Source Ground Truth: data/narrative_ground_truth/
Source OCR Cache: data/narrative_ocr_cache/

Optionally evaluates across the full 120-document benchmark (50 Lab, 50 Med, 20 Narrative)
for complete multi-extractor comparison.

Outputs machine-readable metrics: experiments/E3-narrative-extraction/e3_results.json.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from clindoc.extract.contracts import (
    TIER_1_FIELDS,
    TIER_2_FIELDS,
    Claim,
    Evidence,
)
from clindoc.extract.evaluate import (
    ALL_CANONICAL_FIELDS,
    evaluate_dataset,
    evaluate_document,
    load_document_text_for_eval,
    load_ground_truth_claims,
)
from clindoc.extract.llm_extractor import (
    PERMITTED_NARRATIVE_FIELDS,
    MockLLMProvider,
    NarrativeExtractor,
    VLLMProvider,
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


def load_narrative_document_ids() -> list[str]:
    """Return the canonical 20 narrative document IDs."""
    return [f"NARRATIVE_{i:04d}" for i in range(1, 21)]


def load_all_benchmark_document_ids() -> list[str]:
    """Return all 120 canonical benchmark document IDs."""
    labs = [f"LAB_{i:04d}" for i in range(1, 51)]
    meds = [f"MED_{i:04d}" for i in range(1, 51)]
    narrs = [f"NARRATIVE_{i:04d}" for i in range(1, 21)]
    return labs + meds + narrs


def load_narrative_splits(root: Path) -> dict[str, list[str]]:
    """Partition the 20 narrative documents into CV (15) and OOD (5)."""
    manifest_path = root / "data" / "m2_verification_v3" / "split_manifest.json"
    if not manifest_path.exists():
        manifest_path = root / "data" / "m2_verification" / "split_manifest.json"

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    ood_all = set(manifest.get("ood_doc_ids", []))
    narr_docs = load_narrative_document_ids()

    ood_narr = [d for d in narr_docs if d in ood_all]
    cv_narr = [d for d in narr_docs if d not in ood_all]

    return {
        "cv": sorted(cv_narr),
        "ood": sorted(ood_narr),
    }


def run_e3_evaluation(
    provider_type: str = "mock",
    vllm_base_url: str = "http://localhost:8000/v1",
    vllm_model: str = "Qwen/Qwen2.5-7B-Instruct",
    scope: str = "narrative",
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute standalone E3 narrative extraction evaluation.

    Args:
        provider_type: 'mock' (default deterministic offline heuristic) or 'vllm'.
        vllm_base_url: Base URL for vLLM server if using vllm.
        vllm_model: Target model name if using vllm.
        scope: 'narrative' (20 docs, primary E3 focus) or 'all' (120 docs).
        output_dir: Destination directory for results artifact.
    """
    root = _PROJ_ROOT
    out_dir = output_dir or (root / "experiments" / "E3-narrative-extraction")
    out_dir.mkdir(parents=True, exist_ok=True)

    git_hash = get_git_commit(root)
    splits = load_narrative_splits(root)

    if scope == "all":
        eval_doc_ids = load_all_benchmark_document_ids()
    else:
        eval_doc_ids = load_narrative_document_ids()

    print(f"Loaded {len(eval_doc_ids)} documents for evaluation (scope={scope}).")
    print(f"Narrative partition: {len(splits['cv'])} CV documents, {len(splits['ood'])} OOD documents.")

    # Initialize Provider
    is_mock = (provider_type == "mock")
    if is_mock:
        print("Using MockLLMProvider (deterministic offline regex/heuristic provider per DEC-006 / configs/extraction.yaml).")
        provider = MockLLMProvider()
        model_display_name = "MockLLMProvider (deterministic offline heuristic)"
    else:
        print(f"Connecting to vLLM at {vllm_base_url} with model {vllm_model}...")
        provider = VLLMProvider(model_name=vllm_model, base_url=vllm_base_url)
        model_display_name = f"VLLMProvider ({vllm_model})"

    extractor = NarrativeExtractor(provider=provider, max_retries=2)

    # Ingest document texts
    print("Ingesting document text records...")
    doc_texts: dict[str, Any] = {}
    missing_docs = []
    for doc_id in eval_doc_ids:
        dt = load_document_text_for_eval(doc_id, root)
        if dt is None:
            missing_docs.append(doc_id)
        else:
            doc_texts[doc_id] = dt

    if missing_docs:
        raise RuntimeError(f"Missing OCR cache for documents: {missing_docs}")

    # Run extraction document by document
    print("Executing narrative extraction...")
    t0 = time.time()
    claims_by_doc: dict[str, list[Claim]] = {}
    evidences_by_doc: dict[str, list[Evidence]] = {}
    diagnostics_by_doc: dict[str, dict] = {}

    for doc_id in eval_doc_ids:
        dt = doc_texts[doc_id]
        doc_claims = []
        doc_evs = []
        for page in dt.pages:
            page_text = page.get_page_text()
            region_id = f"{doc_id}::p{page.page_number}::r0"
            evs, cls, diag = extractor.extract(
                document_id=doc_id,
                page=page,
                region_text=page_text,
                region_id=region_id,
                char_offset=0,
            )
            doc_claims.extend(cls)
            doc_evs.extend(evs)
            diagnostics_by_doc[doc_id] = diag

        claims_by_doc[doc_id] = doc_claims
        evidences_by_doc[doc_id] = doc_evs

    elapsed = time.time() - t0
    print(f"Extraction completed in {elapsed:.2f}s ({elapsed / len(eval_doc_ids):.3f}s/doc).")

    # Load canonical ground truth claims
    print("Loading canonical ground truth claims...")
    gt_by_doc: dict[str, Any] = {}
    for doc_id in eval_doc_ids:
        gt_by_doc[doc_id] = load_ground_truth_claims(doc_id, root)

    total_pred = sum(len(c) for c in claims_by_doc.values())
    total_gt = sum(len(g) for g in gt_by_doc.values())
    print(f"Total predicted claims: {total_pred} | Total GT claims: {total_gt}")

    # Compute evaluation metrics
    print("Evaluating extraction predictions against ground truth...")
    overall_eval = evaluate_dataset(claims_by_doc, gt_by_doc)

    # Narrative-specific slice (always computed)
    narr_ids = load_narrative_document_ids()
    narr_eval = evaluate_dataset(
        {d: claims_by_doc[d] for d in narr_ids if d in claims_by_doc},
        {d: gt_by_doc[d] for d in narr_ids if d in gt_by_doc},
    )

    # Narrative CV vs OOD split
    cv_eval = evaluate_dataset(
        {d: claims_by_doc[d] for d in splits["cv"] if d in claims_by_doc},
        {d: gt_by_doc[d] for d in splits["cv"] if d in gt_by_doc},
    )
    ood_eval = evaluate_dataset(
        {d: claims_by_doc[d] for d in splits["ood"] if d in claims_by_doc},
        {d: gt_by_doc[d] for d in splits["ood"] if d in gt_by_doc},
    )

    # Document-level detail
    per_doc_summary = []
    for doc_eval in narr_eval.get("per_document", []):
        per_doc_summary.append({
            "doc_id": doc_eval["doc_id"],
            "split": "OOD" if doc_eval["doc_id"] in splits["ood"] else "CV",
            "total_gt": doc_eval["total_gt"],
            "total_pred": doc_eval["total_pred"],
            "total_tp": doc_eval["total_tp"],
            "total_fp": doc_eval["total_fp"],
            "total_fn": doc_eval["total_fn"],
            "micro_precision": doc_eval["micro_precision"],
            "micro_recall": doc_eval["micro_recall"],
            "micro_f1": doc_eval["micro_f1"],
            "exact_match_rate": doc_eval["exact_match_rate"],
        })

    results_data = {
        "experiment_id": "E3-narrative-extraction",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": git_hash,
        "model_name": model_display_name,
        "provider_type": provider_type,
        "is_mock": is_mock,
        "target_production_model": "Qwen/Qwen2.5-7B-Instruct-AWQ (via Colab T4 vLLM)",
        "scope": scope,
        "timing": {
            "total_elapsed_seconds": round(elapsed, 2),
            "seconds_per_document": round(elapsed / max(len(eval_doc_ids), 1), 3),
        },
        "evaluation_scope": {
            "evaluated_documents": len(eval_doc_ids),
            "narrative_documents": len(narr_ids),
            "narrative_cv_documents": len(splits["cv"]),
            "narrative_ood_documents": len(splits["ood"]),
        },
        "diagnostics": {
            "total_retries": sum(d.get("retries", 0) for d in diagnostics_by_doc.values()),
            "total_validation_errors": sum(len(d.get("validation_errors", [])) for d in diagnostics_by_doc.values()),
            "total_rejected_extractions": sum(len(d.get("rejected", [])) for d in diagnostics_by_doc.values()),
        },
        "primary_narrative_benchmark": {
            "num_documents": narr_eval["num_documents"],
            "total_gt_claims": narr_eval["total_gt_claims"],
            "total_pred_claims": narr_eval["total_pred_claims"],
            "micro_precision": narr_eval["micro_precision"],
            "micro_recall": narr_eval["micro_recall"],
            "micro_f1": narr_eval["micro_f1"],
            "macro_f1": narr_eval["macro_f1"],
            "overall_exact_match_rate": narr_eval["overall_exact_match_rate"],
            "metrics_per_field": narr_eval["metrics_per_field"],
            "by_split": {
                "cross_validation": {
                    "num_documents": cv_eval["num_documents"],
                    "total_gt_claims": cv_eval["total_gt_claims"],
                    "total_pred_claims": cv_eval["total_pred_claims"],
                    "micro_precision": cv_eval["micro_precision"],
                    "micro_recall": cv_eval["micro_recall"],
                    "micro_f1": cv_eval["micro_f1"],
                    "macro_f1": cv_eval["macro_f1"],
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
                    "metrics_per_field": ood_eval["metrics_per_field"],
                },
            },
            "per_document": per_doc_summary,
        },
        "overall_evaluation": {
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
    }

    # Save machine-readable JSON
    results_json_path = out_dir / "e3_results.json"
    with open(results_json_path, "w", encoding="utf-8") as f:
        json.dump(results_data, f, indent=2)
    print(f"Persisted results JSON to: {results_json_path}")

    return results_data


def main():
    parser = argparse.ArgumentParser(description="Run E3 Standalone Narrative Extraction Evaluation")
    parser.add_argument("--provider", type=str, default="mock", choices=["mock", "vllm"], help="Inference provider")
    parser.add_argument("--vllm-url", type=str, default="http://localhost:8000/v1", help="vLLM endpoint URL")
    parser.add_argument("--vllm-model", type=str, default="Qwen/Qwen2.5-7B-Instruct", help="vLLM model ID")
    parser.add_argument("--scope", type=str, default="narrative", choices=["narrative", "all"], help="Evaluation scope")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else None
    results = run_e3_evaluation(
        provider_type=args.provider,
        vllm_base_url=args.vllm_url,
        vllm_model=args.vllm_model,
        scope=args.scope,
        output_dir=out_dir,
    )

    print("\n" + "=" * 80)
    print("E3 NARRATIVE EXTRACTION SUMMARY RESULTS (20 NARRATIVE DOCUMENTS)")
    print("=" * 80)
    nb = results["primary_narrative_benchmark"]
    print(f"Documents: {nb['num_documents']} (15 CV, 5 OOD)")
    print(f"Total GT Claims: {nb['total_gt_claims']} | Total Predicted Claims: {nb['total_pred_claims']}")
    print(f"Micro Precision: {nb['micro_precision']:.4f} | Micro Recall: {nb['micro_recall']:.4f} | Micro F1: {nb['micro_f1']:.4f}")
    print(f"Macro F1: {nb['macro_f1']:.4f} | Overall Exact Match Rate: {nb['overall_exact_match_rate']:.4f}")
    print("\nPer-Field Metrics:")
    for ft, m in nb["metrics_per_field"].items():
        print(f"  {ft:20s}: P={m['precision']:.4f} | R={m['recall']:.4f} | F1={m['f1']:.4f} (GT={m['gt_count']}, Pred={m['pred_count']}, TP={m['tp']})")


if __name__ == "__main__":
    main()
