"""Evaluation script comparing extracted medication regimens against ground truth."""

import argparse
import json
from pathlib import Path
from typing import Any

from clindoc.schema.medication import ExtractedMedication, ExtractedPrescriptionDocument
from clindoc.synth.med_schema import MedicationGroundTruth, PrescriptionGroundTruth

DEFAULT_GT_DIR = Path("data/med_ground_truth")
DEFAULT_EXTRACTED_DIR = Path("data/med_extracted")
DEFAULT_REPORT_PATH = Path("data/med_evaluation_report.json")


def evaluate_document_medications(
    gt_record: PrescriptionGroundTruth,
    extracted_doc: ExtractedPrescriptionDocument,
) -> dict[str, Any]:
    """Evaluate extracted medications against ground truth for a single document."""
    gt_meds = {m.medication_name.strip().lower(): m for m in gt_record.medications}
    pred_meds = {m.medication_name.strip().lower(): m for m in extracted_doc.medications}

    matched_keys = set(gt_meds.keys()) & set(pred_meds.keys())
    tp = len(matched_keys)
    fp = len(pred_meds) - tp
    fn = len(gt_meds) - tp

    # Field-level matches among True Positives
    name_matches = 0
    dose_matches = 0
    unit_matches = 0
    form_matches = 0
    route_matches = 0
    freq_matches = 0
    dur_matches = 0
    qty_matches = 0
    refill_matches = 0
    rxcui_matches = 0
    complete_regimen_matches = 0
    evidence_valid_count = 0

    for k in matched_keys:
        gt: MedicationGroundTruth = gt_meds[k]
        pred: ExtractedMedication = pred_meds[k]

        name_ok = pred.medication_name.strip().lower() == gt.medication_name.strip().lower()
        
        dose_ok = (
            pred.dose is not None
            and gt.dose is not None
            and abs(pred.dose - gt.dose) < 1e-4
        ) or (pred.dose is None and gt.dose is None)

        unit_ok = (
            pred.dose_unit is not None
            and gt.dose_unit is not None
            and pred.dose_unit.strip().lower() == gt.dose_unit.strip().lower()
        ) or (pred.dose_unit is None and gt.dose_unit is None)

        form_ok = (
            pred.dosage_form is not None
            and gt.dosage_form is not None
            and pred.dosage_form.strip().lower() == gt.dosage_form.strip().lower()
        ) or (pred.dosage_form is None and gt.dosage_form is None)

        # Route matching (e.g. PO vs by mouth vs oral)
        pred_route = pred.route.strip().lower() if pred.route else ""
        gt_route = gt.route.strip().lower() if gt.route else ""
        route_ok = (pred_route == gt_route) or (
            pred_route in ("po", "by mouth", "oral") and gt_route in ("po", "by mouth", "oral")
        ) or (
            "inhalation" in pred_route and "inhalation" in gt_route
        )

        # Frequency matching
        pred_freq = pred.frequency.strip().lower() if pred.frequency else ""
        gt_freq = gt.frequency.strip().lower() if gt.frequency else ""
        freq_synonyms = {
            "qd": "once daily",
            "qday": "once daily",
            "daily": "once daily",
            "bid": "twice daily",
            "tid": "three times daily",
            "qid": "four times daily",
        }
        pred_norm_freq = freq_synonyms.get(pred_freq, pred_freq)
        gt_norm_freq = freq_synonyms.get(gt_freq, gt_freq)
        freq_ok = (pred_freq == gt_freq) or (pred_norm_freq == gt_norm_freq)

        # Duration matching
        pred_dur = (pred.duration.strip().lower() if pred.duration else None)
        gt_dur = (gt.duration.strip().lower() if gt.duration else None)
        dur_ok = (pred_dur == gt_dur)

        # Quantity and refills matching
        qty_ok = (pred.quantity == gt.quantity)
        refill_ok = (pred.refills == gt.refills)

        # RxCUI matching
        rxcui_ok = (pred.rxcui == gt.rxcui) if (pred.rxcui and gt.rxcui) else (pred.rxcui is None and gt.rxcui is None)

        # Evidence validation
        ev = pred.source_evidence
        ev_ok = (
            ev is not None
            and bool(ev.text)
            and ev.page >= 1
            and len(ev.bbox) == 4
            and ev.ocr_confidence > 0.0
        )

        if name_ok:
            name_matches += 1
        if dose_ok:
            dose_matches += 1
        if unit_ok:
            unit_matches += 1
        if form_ok:
            form_matches += 1
        if route_ok:
            route_matches += 1
        if freq_ok:
            freq_matches += 1
        if dur_ok:
            dur_matches += 1
        if qty_ok:
            qty_matches += 1
        if refill_ok:
            refill_matches += 1
        if rxcui_ok:
            rxcui_matches += 1
        if ev_ok:
            evidence_valid_count += 1

        # Complete regimen match: all core regimen attributes match
        if name_ok and dose_ok and unit_ok and form_ok and route_ok and freq_ok and dur_ok:
            complete_regimen_matches += 1

    return {
        "doc_id": gt_record.doc_id,
        "template_name": gt_record.template_type,
        "gt_count": len(gt_meds),
        "pred_count": len(pred_meds),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "name_matches": name_matches,
        "dose_matches": dose_matches,
        "unit_matches": unit_matches,
        "form_matches": form_matches,
        "route_matches": route_matches,
        "freq_matches": freq_matches,
        "dur_matches": dur_matches,
        "qty_matches": qty_matches,
        "refill_matches": refill_matches,
        "rxcui_matches": rxcui_matches,
        "complete_regimen_matches": complete_regimen_matches,
        "evidence_valid_count": evidence_valid_count,
    }


def evaluate_all(
    gt_dir: Path = DEFAULT_GT_DIR,
    extracted_dir: Path = DEFAULT_EXTRACTED_DIR,
    report_path: Path = DEFAULT_REPORT_PATH,
) -> dict[str, Any]:
    """Run comprehensive evaluation across all medication documents."""
    gt_files = sorted(gt_dir.glob("*.json"))
    if not gt_files:
        raise FileNotFoundError(f"No ground truth files in {gt_dir}")

    total_gt = 0
    total_pred = 0
    total_tp = 0
    total_fp = 0
    total_fn = 0

    total_name = 0
    total_dose = 0
    total_unit = 0
    total_form = 0
    total_route = 0
    total_freq = 0
    total_dur = 0
    total_qty = 0
    total_refill = 0
    total_rxcui = 0
    total_complete_regimen = 0
    total_evidence_valid = 0

    doc_summaries = []

    for gt_f in gt_files:
        doc_id = gt_f.stem
        ext_f = extracted_dir / f"{doc_id}.json"
        if not ext_f.exists():
            print(f"Warning: missing extracted file for {doc_id}")
            continue

        with open(gt_f, "r", encoding="utf-8") as f:
            gt_record = PrescriptionGroundTruth.model_validate(json.load(f))
        with open(ext_f, "r", encoding="utf-8") as f:
            ext_doc = ExtractedPrescriptionDocument.model_validate(json.load(f))

        doc_res = evaluate_document_medications(gt_record, ext_doc)
        doc_summaries.append(doc_res)

        total_gt += doc_res["gt_count"]
        total_pred += doc_res["pred_count"]
        total_tp += doc_res["tp"]
        total_fp += doc_res["fp"]
        total_fn += doc_res["fn"]

        total_name += doc_res["name_matches"]
        total_dose += doc_res["dose_matches"]
        total_unit += doc_res["unit_matches"]
        total_form += doc_res["form_matches"]
        total_route += doc_res["route_matches"]
        total_freq += doc_res["freq_matches"]
        total_dur += doc_res["dur_matches"]
        total_qty += doc_res["qty_matches"]
        total_refill += doc_res["refill_matches"]
        total_rxcui += doc_res["rxcui_matches"]
        total_complete_regimen += doc_res["complete_regimen_matches"]
        total_evidence_valid += doc_res["evidence_valid_count"]

    precision = total_tp / total_pred if total_pred > 0 else 0.0
    recall = total_tp / total_gt if total_gt > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    field_accuracies = {
        "medication_name_accuracy": total_name / total_tp if total_tp > 0 else 0.0,
        "dose_accuracy": total_dose / total_tp if total_tp > 0 else 0.0,
        "dose_unit_accuracy": total_unit / total_tp if total_tp > 0 else 0.0,
        "dosage_form_accuracy": total_form / total_tp if total_tp > 0 else 0.0,
        "route_accuracy": total_route / total_tp if total_tp > 0 else 0.0,
        "frequency_accuracy": total_freq / total_tp if total_tp > 0 else 0.0,
        "duration_accuracy": total_dur / total_tp if total_tp > 0 else 0.0,
        "quantity_accuracy": total_qty / total_tp if total_tp > 0 else 0.0,
        "refills_accuracy": total_refill / total_tp if total_tp > 0 else 0.0,
        "rxcui_accuracy": total_rxcui / total_tp if total_tp > 0 else 0.0,
        "evidence_preservation_rate": total_evidence_valid / total_tp if total_tp > 0 else 0.0,
    }

    complete_regimen_match_rate = total_complete_regimen / total_gt if total_gt > 0 else 0.0

    report = {
        "total_documents": len(doc_summaries),
        "total_ground_truth_medications": total_gt,
        "total_extracted_medications": total_pred,
        "entity_metrics": {
            "true_positives": total_tp,
            "false_positives": total_fp,
            "false_negatives": total_fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        },
        "field_accuracies": field_accuracies,
        "complete_regimen_exact_match": {
            "matches": total_complete_regimen,
            "total_gt": total_gt,
            "rate": complete_regimen_match_rate,
        },
        "document_breakdown": doc_summaries,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n============================================================")
    print("           M0b MEDICATION EXTRACTION EVALUATION REPORT      ")
    print("============================================================")
    print(f"Evaluated Documents:             {len(doc_summaries)}")
    print(f"Ground Truth Medications:       {total_gt}")
    print(f"Extracted Medications:          {total_pred}")
    print("------------------------------------------------------------")
    print(f"Entity Precision:               {precision:.4f} ({precision*100:.1f}%)")
    print(f"Entity Recall:                  {recall:.4f} ({recall*100:.1f}%)")
    print(f"Entity F1:                      {f1:.4f} ({f1*100:.1f}%)")
    print("------------------------------------------------------------")
    print(f"Medication Name Accuracy:       {field_accuracies['medication_name_accuracy']:.4f}")
    print(f"Dose Accuracy:                  {field_accuracies['dose_accuracy']:.4f}")
    print(f"Dose Unit Accuracy:             {field_accuracies['dose_unit_accuracy']:.4f}")
    print(f"Dosage Form Accuracy:           {field_accuracies['dosage_form_accuracy']:.4f}")
    print(f"Route Accuracy:                 {field_accuracies['route_accuracy']:.4f}")
    print(f"Frequency Accuracy:             {field_accuracies['frequency_accuracy']:.4f}")
    print(f"Duration Accuracy:              {field_accuracies['duration_accuracy']:.4f}")
    print(f"Quantity Accuracy:              {field_accuracies['quantity_accuracy']:.4f}")
    print(f"Refills Accuracy:               {field_accuracies['refills_accuracy']:.4f}")
    print(f"RxCUI Mapping Accuracy:         {field_accuracies['rxcui_accuracy']:.4f}")
    print(f"Evidence Preservation Rate:     {field_accuracies['evidence_preservation_rate']:.4f}")
    print("------------------------------------------------------------")
    print(f"Complete Regimen Exact Match:   {total_complete_regimen} / {total_gt} ({complete_regimen_match_rate*100:.1f}%)")
    print("============================================================\n")

    return report


def main():
    parser = argparse.ArgumentParser(description="Medication Extraction Evaluation")
    parser.add_argument("--gt-dir", type=str, default=str(DEFAULT_GT_DIR), help="Ground truth directory")
    parser.add_argument("--extracted-dir", type=str, default=str(DEFAULT_EXTRACTED_DIR), help="Extracted documents directory")
    parser.add_argument("--report", type=str, default=str(DEFAULT_REPORT_PATH), help="Output report JSON path")
    args = parser.parse_args()

    evaluate_all(
        gt_dir=Path(args.gt_dir),
        extracted_dir=Path(args.extracted_dir),
        report_path=Path(args.report),
    )


if __name__ == "__main__":
    main()
