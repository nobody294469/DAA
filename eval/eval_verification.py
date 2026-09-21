"""Evaluation benchmark for clinical document extraction verifier on clean and corrupted data."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Any

from clindoc.ocr.schema import DocumentOCRRecord
from clindoc.schema.lab import ExtractedLabReport
from clindoc.verify.error_injection import ERROR_TYPES, generate_corrupted_dataset
from clindoc.verify.schema import InjectedErrorRecord, ReportVerificationResult
from clindoc.verify.verifier import verify_report


def run_clean_evaluation(
    extracted_dir: str | Path = "data/extracted",
    ocr_cache_dir: str | Path = "data/ocr_cache",
) -> dict[str, Any]:
    """Run verification on uncorrupted baseline extraction to measure false-positive rate."""
    extracted_dir = Path(extracted_dir)
    ocr_cache_dir = Path(ocr_cache_dir)

    report_files = sorted(extracted_dir.glob("LAB_*.json"))
    total_docs = len(report_files)
    total_observations = 0
    false_discrepancies = 0

    for rf in report_files:
        doc_id = rf.stem
        ocr_file = ocr_cache_dir / f"{doc_id}.json"
        if not ocr_file.exists():
            continue

        with open(rf, "r", encoding="utf-8") as f:
            report = ExtractedLabReport.model_validate(json.load(f))
        with open(ocr_file, "r", encoding="utf-8") as f:
            ocr_rec = DocumentOCRRecord.model_validate(json.load(f))

        res = verify_report(report, ocr_rec)
        total_observations += len(report.observations)
        false_discrepancies += len(res.discrepancies)

    fp_rate = false_discrepancies / total_observations if total_observations > 0 else 0.0

    return {
        "total_documents": total_docs,
        "total_observations": total_observations,
        "false_discrepancies": false_discrepancies,
        "clean_false_positive_rate": round(fp_rate, 4),
        "clean_accuracy": round(1.0 - fp_rate, 4),
    }


def run_corrupted_evaluation(
    extracted_dir: str | Path = "data/extracted",
    ocr_cache_dir: str | Path = "data/ocr_cache",
    seed: int = 42,
) -> dict[str, Any]:
    """Run verification on controlled corrupted copies to measure detection precision, recall, F1, and per-type metrics."""
    extracted_dir = Path(extracted_dir)
    ocr_cache_dir = Path(ocr_cache_dir)

    report_files = sorted(extracted_dir.glob("LAB_*.json"))
    clean_reports = []
    ocr_records = {}

    for rf in report_files:
        doc_id = rf.stem
        ocr_file = ocr_cache_dir / f"{doc_id}.json"
        if not ocr_file.exists():
            continue

        with open(rf, "r", encoding="utf-8") as f:
            clean_reports.append(ExtractedLabReport.model_validate(json.load(f)))
        with open(ocr_file, "r", encoding="utf-8") as f:
            ocr_records[doc_id] = DocumentOCRRecord.model_validate(json.load(f))

    corrupted_data = generate_corrupted_dataset(clean_reports, seed=seed)

    total_injected = 0
    total_detected = 0
    false_positives = 0

    per_type_injected = defaultdict(int)
    per_type_detected = defaultdict(int)

    sample_discrepancy = None

    for corrupted_rep, injected_errors in corrupted_data:
        ocr_rec = ocr_records[corrupted_rep.doc_id]
        v_res = verify_report(corrupted_rep, ocr_rec)

        if sample_discrepancy is None and v_res.discrepancies:
            sample_discrepancy = v_res.discrepancies[0].model_dump()

        # Build lookup of detected discrepancies by (field, observation)
        detected_keys = {(d.field, d.observation): d for d in v_res.discrepancies}

        # Also map observation_presence matching
        detected_obs_pres = {d.observation for d in v_res.discrepancies if d.field == "observation_presence"}

        for err in injected_errors:
            total_injected += 1
            per_type_injected[err.error_type] += 1

            is_caught = False
            if (err.field, err.observation) in detected_keys:
                is_caught = True
            elif err.error_type in ("DROPPED_OBSERVATION", "HALLUCINATED_OBSERVATION"):
                if err.observation in detected_obs_pres:
                    is_caught = True
            elif err.error_type == "TEST_NAME_CORRUPTION":
                # Verifier flags the corrupted observation name, with source_value pointing to original
                for d in v_res.discrepancies:
                    if d.field == "test_name" and (d.source_value == err.original_value or d.observation == err.corrupted_value):
                        is_caught = True
                        break
            elif any(d.observation == err.observation for d in v_res.discrepancies):
                is_caught = True

            if is_caught:
                total_detected += 1
                per_type_detected[err.error_type] += 1

        # Check for un-injected false positives
        injected_obs_names = {err.observation for err in injected_errors}
        corrupted_obs_names = {err.corrupted_value for err in injected_errors if err.corrupted_value is not None}
        for disc in v_res.discrepancies:
            if (disc.observation not in injected_obs_names) and (disc.observation not in corrupted_obs_names) and (disc.source_value not in injected_obs_names):
                false_positives += 1

    precision = total_detected / (total_detected + false_positives) if (total_detected + false_positives) > 0 else 0.0
    recall = total_detected / total_injected if total_injected > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    per_type_breakdown = {}
    for err_type in ERROR_TYPES:
        inj = per_type_injected[err_type]
        det = per_type_detected[err_type]
        rec = det / inj if inj > 0 else 0.0
        per_type_breakdown[err_type] = {
            "injected": inj,
            "detected": det,
            "recall": round(rec, 4),
        }

    return {
        "total_documents": len(clean_reports),
        "total_injected_errors": total_injected,
        "true_positives": total_detected,
        "false_negatives": total_injected - total_detected,
        "false_positives": false_positives,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "per_type_metrics": per_type_breakdown,
        "sample_discrepancy": sample_discrepancy,
    }


def run_full_verification_benchmark() -> dict[str, Any]:
    """Execute both clean baseline and corrupted error-injection evaluations."""
    clean_metrics = run_clean_evaluation()
    corrupted_metrics = run_corrupted_evaluation()

    return {
        "clean_baseline": clean_metrics,
        "corrupted_evaluation": corrupted_metrics,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate verification baseline on clean and corrupted datasets")
    parser.add_argument("--extracted-dir", default="data/extracted")
    parser.add_argument("--cache-dir", default="data/ocr_cache")
    args = parser.parse_args()

    clean = run_clean_evaluation(args.extracted_dir, args.cache_dir)
    corrupted = run_corrupted_evaluation(args.extracted_dir, args.cache_dir)

    print("\n" + "=" * 55)
    print("M0a VERIFICATION BASELINE EVALUATION")
    print("=" * 55)
    print("\n[A. CLEAN BASELINE EVALUATION]")
    print(f"Total Documents:           {clean['total_documents']}")
    print(f"Total Observations:        {clean['total_observations']}")
    print(f"False Discrepancies:       {clean['false_discrepancies']}")
    print(f"Clean False-Positive Rate: {clean['clean_false_positive_rate'] * 100:.2f}%")
    print(f"Clean Specificity:         {clean['clean_accuracy'] * 100:.2f}%")

    print("\n[B. CONTROLLED CORRUPTED EVALUATION]")
    print(f"Total Injected Errors:     {corrupted['total_injected_errors']}")
    print(f"True Positives (Detected): {corrupted['true_positives']}")
    print(f"False Negatives (Missed):  {corrupted['false_negatives']}")
    print(f"False Positives:           {corrupted['false_positives']}")
    print(f"Overall Precision:         {corrupted['precision'] * 100:.2f}%")
    print(f"Overall Recall:            {corrupted['recall'] * 100:.2f}%")
    print(f"Overall F1 Score:          {corrupted['f1'] * 100:.2f}%")

    print("\n[C. BREAKDOWN BY ERROR TYPE]")
    for etype, data in corrupted["per_type_metrics"].items():
        print(f"  - {etype:25s}: {data['detected']}/{data['injected']} detected ({data['recall'] * 100:.1f}%)")

    print("=" * 55 + "\n")
