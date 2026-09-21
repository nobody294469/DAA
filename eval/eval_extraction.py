"""Evaluation script comparing extracted lab observations against ground truth."""

import argparse
import json
from pathlib import Path
from typing import Any

from clindoc.schema.lab import ExtractedLabReport
from clindoc.synth.schema import LabReportGroundTruth


def evaluate_document_extraction(
    gt_record: LabReportGroundTruth,
    extracted_report: ExtractedLabReport,
) -> dict[str, Any]:
    """Evaluate extracted report against ground truth record."""
    gt_tests = {t.test_name: t for t in gt_record.tests}
    pred_obs = {o.test_name: o for o in extracted_report.observations}

    matched_keys = set(gt_tests.keys()) & set(pred_obs.keys())
    tp = len(matched_keys)
    fp = len(pred_obs) - tp
    fn = len(gt_tests) - tp

    # Field-level matches among True Positives
    name_matches = 0
    loinc_matches = 0
    value_matches = 0
    unit_matches = 0
    ref_matches = 0
    flag_matches = 0
    all_fields_matches = 0

    for k in matched_keys:
        gt = gt_tests[k]
        pred = pred_obs[k]

        name_ok = (pred.test_name.strip() == gt.test_name.strip())
        loinc_ok = (pred.loinc_code == gt.loinc_code)
        value_ok = (abs(pred.value - gt.value) < 1e-4)
        unit_ok = (pred.normalized_unit.strip() == gt.unit.strip())
        ref_ok = (pred.ref_range_text.strip() == gt.ref_range_text.strip())
        flag_ok = (pred.abnormal_flag == gt.flag)

        if name_ok:
            name_matches += 1
        if loinc_ok:
            loinc_matches += 1
        if value_ok:
            value_matches += 1
        if unit_ok:
            unit_matches += 1
        if ref_ok:
            ref_matches += 1
        if flag_ok:
            flag_matches += 1

        if name_ok and loinc_ok and value_ok and unit_ok and ref_ok and flag_ok:
            all_fields_matches += 1

    # Metadata matching
    patient_id_match = (
        extracted_report.patient is not None
        and extracted_report.patient.patient_id == gt_record.patient.patient_id
    )
    patient_name_match = (
        extracted_report.patient is not None
        and extracted_report.patient.name == gt_record.patient.name
    )
    dob_match = (
        extracted_report.patient is not None
        and extracted_report.patient.dob == gt_record.patient.dob
    )
    gender_match = (
        extracted_report.patient is not None
        and extracted_report.patient.gender == gt_record.patient.gender
    )
    date_match = (extracted_report.report_date == gt_record.report_date)
    doctor_match = (extracted_report.ordering_provider == gt_record.ordering_provider)

    return {
        "doc_id": gt_record.doc_id,
        "gt_count": len(gt_tests),
        "pred_count": len(pred_obs),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "name_matches": name_matches,
        "loinc_matches": loinc_matches,
        "value_matches": value_matches,
        "unit_matches": unit_matches,
        "ref_matches": ref_matches,
        "flag_matches": flag_matches,
        "all_fields_matches": all_fields_matches,
        "patient_id_match": patient_id_match,
        "patient_name_match": patient_name_match,
        "dob_match": dob_match,
        "gender_match": gender_match,
        "date_match": date_match,
        "doctor_match": doctor_match,
    }


def evaluate_dataset(
    gt_dir: str | Path = "data/ground_truth",
    extracted_dir: str | Path = "data/extracted",
) -> dict[str, Any]:
    """Evaluate all documents in dataset and return aggregated metrics."""
    gt_dir = Path(gt_dir)
    extracted_dir = Path(extracted_dir)

    gt_files = sorted(gt_dir.glob("LAB_*.json"))
    doc_results = []

    for gtf in gt_files:
        doc_id = gtf.stem
        ext_file = extracted_dir / f"{doc_id}.json"
        if not ext_file.exists():
            continue

        with open(gtf, "r", encoding="utf-8") as f:
            gt_record = LabReportGroundTruth.model_validate(json.load(f))
        with open(ext_file, "r", encoding="utf-8") as f:
            ext_record = ExtractedLabReport.model_validate(json.load(f))

        res = evaluate_document_extraction(gt_record, ext_record)
        doc_results.append(res)

    total_gt = sum(r["gt_count"] for r in doc_results)
    total_pred = sum(r["pred_count"] for r in doc_results)
    total_tp = sum(r["tp"] for r in doc_results)
    total_fp = sum(r["fp"] for r in doc_results)
    total_fn = sum(r["fn"] for r in doc_results)

    precision = total_tp / total_pred if total_pred > 0 else 0.0
    recall = total_tp / total_gt if total_gt > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    total_matched = total_tp
    acc_name = sum(r["name_matches"] for r in doc_results) / total_matched if total_matched > 0 else 0.0
    acc_loinc = sum(r["loinc_matches"] for r in doc_results) / total_matched if total_matched > 0 else 0.0
    acc_value = sum(r["value_matches"] for r in doc_results) / total_matched if total_matched > 0 else 0.0
    acc_unit = sum(r["unit_matches"] for r in doc_results) / total_matched if total_matched > 0 else 0.0
    acc_ref = sum(r["ref_matches"] for r in doc_results) / total_matched if total_matched > 0 else 0.0
    acc_flag = sum(r["flag_matches"] for r in doc_results) / total_matched if total_matched > 0 else 0.0
    acc_all = sum(r["all_fields_matches"] for r in doc_results) / total_matched if total_matched > 0 else 0.0

    num_docs = len(doc_results)
    meta_pid_acc = sum(1 for r in doc_results if r["patient_id_match"]) / num_docs if num_docs > 0 else 0.0
    meta_name_acc = sum(1 for r in doc_results if r["patient_name_match"]) / num_docs if num_docs > 0 else 0.0
    meta_dob_acc = sum(1 for r in doc_results if r["dob_match"]) / num_docs if num_docs > 0 else 0.0
    meta_gender_acc = sum(1 for r in doc_results if r["gender_match"]) / num_docs if num_docs > 0 else 0.0
    meta_date_acc = sum(1 for r in doc_results if r["date_match"]) / num_docs if num_docs > 0 else 0.0
    meta_doctor_acc = sum(1 for r in doc_results if r["doctor_match"]) / num_docs if num_docs > 0 else 0.0

    return {
        "num_documents": num_docs,
        "total_gt_observations": total_gt,
        "total_pred_observations": total_pred,
        "detection_precision": round(precision, 4),
        "detection_recall": round(recall, 4),
        "detection_f1": round(f1, 4),
        "field_accuracies": {
            "test_name_accuracy": round(acc_name, 4),
            "loinc_accuracy": round(acc_loinc, 4),
            "value_accuracy": round(acc_value, 4),
            "unit_accuracy": round(acc_unit, 4),
            "reference_range_accuracy": round(acc_ref, 4),
            "abnormal_flag_accuracy": round(acc_flag, 4),
            "all_fields_exact_match": round(acc_all, 4),
        },
        "metadata_accuracies": {
            "patient_id": round(meta_pid_acc, 4),
            "patient_name": round(meta_name_acc, 4),
            "dob": round(meta_dob_acc, 4),
            "gender": round(meta_gender_acc, 4),
            "report_date": round(meta_date_acc, 4),
            "ordering_provider": round(meta_doctor_acc, 4),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate extracted lab data against ground truth")
    parser.add_argument("--gt-dir", default="data/ground_truth")
    parser.add_argument("--extracted-dir", default="data/extracted")
    args = parser.parse_args()

    metrics = evaluate_dataset(args.gt_dir, args.extracted_dir)
    print("\n" + "=" * 50)
    print("M0a EXTRACTION EVALUATION SUMMARY")
    print("=" * 50)
    print(f"Evaluated documents: {metrics['num_documents']}")
    print(f"Total Ground-Truth Observations: {metrics['total_gt_observations']}")
    print(f"Total Extracted Observations:    {metrics['total_pred_observations']}")
    print(f"Observation Detection Precision: {metrics['detection_precision'] * 100:.2f}%")
    print(f"Observation Detection Recall:    {metrics['detection_recall'] * 100:.2f}%")
    print(f"Observation Detection F1 Score:  {metrics['detection_f1'] * 100:.2f}%")
    print("\nField Accuracies on Matched Observations:")
    for f_name, f_acc in metrics["field_accuracies"].items():
        print(f"  - {f_name:28s}: {f_acc * 100:.2f}%")
    print("\nMetadata Demographics Accuracies:")
    for m_name, m_acc in metrics["metadata_accuracies"].items():
        print(f"  - {m_name:28s}: {m_acc * 100:.2f}%")
    print("=" * 50 + "\n")
