"""Robustness Benchmark runner evaluating Track A (Visual) and Track B (OCR Text) independently."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

from clindoc.degrade.engine import DegradationEngine
from clindoc.degrade.metrics import (
    classify_field_failure,
    compute_ocr_quality_metrics,
)
from clindoc.degrade.schema import (
    DegradationTrack,
    DegradationType,
    SeverityLevel,
)
from clindoc.extract.lab_parser import extract_lab_report
from clindoc.extract.med_parser import MedicationRegimenParser
from clindoc.ocr.pipeline import process_document
from clindoc.ocr.schema import DocumentOCRRecord
from clindoc.schema.medication import ExtractedPrescriptionDocument
from clindoc.synth.med_schema import PrescriptionGroundTruth
from clindoc.synth.schema import LabReportGroundTruth
from eval.eval_extraction import evaluate_document_extraction
from eval.eval_medication import evaluate_document_medications

DEFAULT_REPORT_PATH = Path("data/benchmark_results/m1_robustness_report.json")


class RobustnessBenchmark:
    """Executes controlled degradation benchmarks across Track A (Visual) and Track B (OCR Text)."""

    def __init__(
        self,
        engine: DegradationEngine | None = None,
        med_parser: MedicationRegimenParser | None = None,
    ):
        self.engine = engine or DegradationEngine()
        self.med_parser = med_parser or MedicationRegimenParser()

    def run_lab_track_a_condition(
        self,
        doc_ids: list[str],
        deg_type: DegradationType,
        severity: SeverityLevel,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Evaluate Track A visual degradation on laboratory reports using PaddleOCR."""
        ocr_metrics_list = []
        extraction_summaries = []
        ocr_failures = 0
        extraction_failures = 0
        total_evaluations = 0

        out_pdf_dir = Path("data/degraded/visual/lab")
        out_ocr_dir = Path("data/degraded/ocr_cache/visual_lab")

        for d_id in doc_ids:
            clean_pdf = Path(f"data/rendered/{d_id}.pdf")
            clean_ocr_f = Path(f"data/ocr_cache/{d_id}.json")
            gt_f = Path(f"data/ground_truth/{d_id}.json")

            if not clean_pdf.exists() or not clean_ocr_f.exists() or not gt_f.exists():
                continue

            with open(clean_ocr_f, "r", encoding="utf-8") as f:
                clean_ocr = DocumentOCRRecord.model_validate(json.load(f))
            with open(gt_f, "r", encoding="utf-8") as f:
                gt_rec = LabReportGroundTruth.model_validate(json.load(f))

            # 1. Visual degradation -> image-backed PDF
            deg_record = self.engine.apply_visual_degradation(
                clean_pdf_path=clean_pdf,
                degradation_type=deg_type,
                severity=severity,
                output_dir=out_pdf_dir,
                seed=seed,
            )

            # 2. Force PaddleOCR (NO born-digital fallback)
            deg_ocr = process_document(
                input_path=deg_record.output_pdf,  # type: ignore
                output_dir=out_ocr_dir,
                engine="paddleocr",
                use_born_digital=False,
                force=True,
                dpi=150,
            )

            # 3. Compute OCR Quality Metrics
            ocr_metric = compute_ocr_quality_metrics(clean_ocr, deg_ocr)
            ocr_metrics_list.append(ocr_metric)

            # 4. Downstream Extraction
            extracted_lab = extract_lab_report(deg_ocr)
            eval_res = evaluate_document_extraction(gt_rec, extracted_lab)
            extraction_summaries.append(eval_res)

            # 5. Decoupled Failure Classification
            deg_text = " ".join(l.text for l in deg_ocr.lines)
            extracted_tests = {o.test_name: o for o in extracted_lab.observations}
            for gt_test in gt_rec.tests:
                total_evaluations += 1
                is_correct = False
                if gt_test.test_name in extracted_tests:
                    pred = extracted_tests[gt_test.test_name]
                    if abs(pred.value - gt_test.value) < 1e-4:
                        is_correct = True

                classification = classify_field_failure(
                    target_token=f"{gt_test.value:g}",
                    is_field_correct=is_correct,
                    degraded_ocr_text=deg_text,
                )
                if classification == "OCR_FAILURE":
                    ocr_failures += 1
                elif classification == "EXTRACTION_FAILURE":
                    extraction_failures += 1

        # Aggregate metrics
        count = max(len(ocr_metrics_list), 1)
        mean_cer = sum(m["cer"] for m in ocr_metrics_list) / count
        mean_wer = sum(m["wer"] for m in ocr_metrics_list) / count
        mean_token_f1 = sum(m["token_f1"] for m in ocr_metrics_list) / count

        total_gt = sum(s["gt_count"] for s in extraction_summaries)
        total_pred = sum(s["pred_count"] for s in extraction_summaries)
        total_tp = sum(s["tp"] for s in extraction_summaries)
        prec = total_tp / total_pred if total_pred > 0 else 0.0
        rec = total_tp / total_gt if total_gt > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0

        val_acc = (
            sum(s["value_matches"] for s in extraction_summaries) / total_tp
            if total_tp > 0
            else 0.0
        )
        unit_acc = (
            sum(s["unit_matches"] for s in extraction_summaries) / total_tp
            if total_tp > 0
            else 0.0
        )
        loinc_acc = (
            sum(s["loinc_matches"] for s in extraction_summaries) / total_tp
            if total_tp > 0
            else 0.0
        )

        return {
            "track": "visual",
            "dataset": "lab",
            "degradation_type": deg_type.value,
            "severity": severity.value,
            "documents_evaluated": len(ocr_metrics_list),
            "ocr_metrics": {
                "mean_cer": round(mean_cer, 4),
                "mean_wer": round(mean_wer, 4),
                "mean_token_f1": round(mean_token_f1, 4),
            },
            "extraction_metrics": {
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4),
                "value_accuracy": round(val_acc, 4),
                "unit_accuracy": round(unit_acc, 4),
                "loinc_accuracy": round(loinc_acc, 4),
            },
            "failure_attribution": {
                "total_observations_evaluated": total_evaluations,
                "ocr_failures": ocr_failures,
                "extraction_failures": extraction_failures,
                "ocr_failure_rate": round(ocr_failures / max(total_evaluations, 1), 4),
                "extraction_failure_rate": round(extraction_failures / max(total_evaluations, 1), 4),
            },
        }

    def run_med_track_a_condition(
        self,
        doc_ids: list[str],
        deg_type: DegradationType,
        severity: SeverityLevel,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Evaluate Track A visual degradation on medication regimens using PaddleOCR."""
        ocr_metrics_list = []
        extraction_summaries = []
        ocr_failures = 0
        extraction_failures = 0
        total_evaluations = 0
        batches_data: list[tuple[DocumentOCRRecord, PrescriptionGroundTruth]] = []

        out_pdf_dir = Path("data/degraded/visual/med")
        out_ocr_dir = Path("data/degraded/ocr_cache/visual_med")

        for d_id in doc_ids:
            clean_pdf = Path(f"data/med_rendered/{d_id}.pdf")
            clean_ocr_f = Path(f"data/med_ocr_cache/{d_id}.json")
            gt_f = Path(f"data/med_ground_truth/{d_id}.json")

            if not clean_pdf.exists() or not clean_ocr_f.exists() or not gt_f.exists():
                continue

            with open(clean_ocr_f, "r", encoding="utf-8") as f:
                clean_ocr = DocumentOCRRecord.model_validate(json.load(f))
            with open(gt_f, "r", encoding="utf-8") as f:
                gt_rec = PrescriptionGroundTruth.model_validate(json.load(f))

            # 1. Visual degradation -> image-backed PDF
            deg_record = self.engine.apply_visual_degradation(
                clean_pdf_path=clean_pdf,
                degradation_type=deg_type,
                severity=severity,
                output_dir=out_pdf_dir,
                seed=seed,
            )

            # 2. Force PaddleOCR (NO born-digital fallback)
            deg_ocr = process_document(
                input_path=deg_record.output_pdf,  # type: ignore
                output_dir=out_ocr_dir,
                engine="paddleocr",
                use_born_digital=False,
                force=True,
                dpi=150,
            )

            # 3. Compute OCR Quality Metrics
            ocr_metric = compute_ocr_quality_metrics(clean_ocr, deg_ocr)
            ocr_metrics_list.append(ocr_metric)
            batches_data.append((deg_ocr, gt_rec))

        # 4. Batch Downstream Extraction
        deg_ocrs = [b[0] for b in batches_data]
        extracted_meds_list = self.med_parser.parse_documents(deg_ocrs) if deg_ocrs else []

        for (deg_ocr, gt_rec), extracted_med in zip(batches_data, extracted_meds_list):
            eval_res = evaluate_document_medications(gt_rec, extracted_med)
            extraction_summaries.append(eval_res)

            # 5. Decoupled Failure Classification
            deg_text = " ".join(l.text for l in deg_ocr.lines)
            extracted_meds = {m.medication_name.lower(): m for m in extracted_med.medications}
            for gt_m in gt_rec.medications:
                total_evaluations += 1
                is_correct = False
                if gt_m.medication_name.lower() in extracted_meds:
                    pred = extracted_meds[gt_m.medication_name.lower()]
                    if (
                        pred.dose is not None
                        and gt_m.dose is not None
                        and abs(pred.dose - gt_m.dose) < 1e-4
                    ):
                        is_correct = True

                classification = classify_field_failure(
                    target_token=gt_m.medication_name,
                    is_field_correct=is_correct,
                    degraded_ocr_text=deg_text,
                )
                if classification == "OCR_FAILURE":
                    ocr_failures += 1
                elif classification == "EXTRACTION_FAILURE":
                    extraction_failures += 1

        count = max(len(ocr_metrics_list), 1)
        mean_cer = sum(m["cer"] for m in ocr_metrics_list) / count
        mean_wer = sum(m["wer"] for m in ocr_metrics_list) / count
        mean_token_f1 = sum(m["token_f1"] for m in ocr_metrics_list) / count

        total_gt = sum(s["gt_count"] for s in extraction_summaries)
        total_pred = sum(s["pred_count"] for s in extraction_summaries)
        total_tp = sum(s["tp"] for s in extraction_summaries)
        prec = total_tp / total_pred if total_pred > 0 else 0.0
        rec = total_tp / total_gt if total_gt > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0

        dose_acc = (
            sum(s["dose_matches"] for s in extraction_summaries) / total_tp
            if total_tp > 0
            else 0.0
        )
        unit_acc = (
            sum(s["unit_matches"] for s in extraction_summaries) / total_tp
            if total_tp > 0
            else 0.0
        )
        exact_matches = sum(s["complete_regimen_matches"] for s in extraction_summaries)
        exact_rate = exact_matches / total_gt if total_gt > 0 else 0.0

        return {
            "track": "visual",
            "dataset": "medication",
            "degradation_type": deg_type.value,
            "severity": severity.value,
            "documents_evaluated": len(ocr_metrics_list),
            "ocr_metrics": {
                "mean_cer": round(mean_cer, 4),
                "mean_wer": round(mean_wer, 4),
                "mean_token_f1": round(mean_token_f1, 4),
            },
            "extraction_metrics": {
                "entity_precision": round(prec, 4),
                "entity_recall": round(rec, 4),
                "entity_f1": round(f1, 4),
                "dose_accuracy": round(dose_acc, 4),
                "unit_accuracy": round(unit_acc, 4),
                "complete_regimen_exact_match": round(exact_rate, 4),
            },
            "failure_attribution": {
                "total_medications_evaluated": total_evaluations,
                "ocr_failures": ocr_failures,
                "extraction_failures": extraction_failures,
                "ocr_failure_rate": round(ocr_failures / max(total_evaluations, 1), 4),
                "extraction_failure_rate": round(extraction_failures / max(total_evaluations, 1), 4),
            },
        }

    def run_lab_track_b_condition(
        self,
        doc_ids: list[str],
        severity: SeverityLevel,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Evaluate Track B OCR text corruption on laboratory reports."""
        ocr_metrics_list = []
        extraction_summaries = []
        ocr_failures = 0
        extraction_failures = 0
        total_evaluations = 0

        out_ocr_dir = Path("data/degraded/ocr_cache/corrupt_lab")

        for d_id in doc_ids:
            clean_ocr_f = Path(f"data/ocr_cache/{d_id}.json")
            gt_f = Path(f"data/ground_truth/{d_id}.json")
            if not clean_ocr_f.exists() or not gt_f.exists():
                continue

            with open(clean_ocr_f, "r", encoding="utf-8") as f:
                clean_ocr = DocumentOCRRecord.model_validate(json.load(f))
            with open(gt_f, "r", encoding="utf-8") as f:
                gt_rec = LabReportGroundTruth.model_validate(json.load(f))

            # Apply Track B OCR corruption
            corrupt_ocr, _ = self.engine.apply_ocr_corruption(
                clean_ocr_record=clean_ocr,
                severity=severity,
                output_dir=out_ocr_dir,
                seed=seed,
            )

            ocr_metric = compute_ocr_quality_metrics(clean_ocr, corrupt_ocr)
            ocr_metrics_list.append(ocr_metric)

            extracted_lab = extract_lab_report(corrupt_ocr)
            eval_res = evaluate_document_extraction(gt_rec, extracted_lab)
            extraction_summaries.append(eval_res)

            corrupt_text = " ".join(l.text for l in corrupt_ocr.lines)
            extracted_tests = {o.test_name: o for o in extracted_lab.observations}
            for gt_test in gt_rec.tests:
                total_evaluations += 1
                is_correct = False
                if gt_test.test_name in extracted_tests:
                    pred = extracted_tests[gt_test.test_name]
                    if abs(pred.value - gt_test.value) < 1e-4:
                        is_correct = True

                classification = classify_field_failure(
                    target_token=f"{gt_test.value:g}",
                    is_field_correct=is_correct,
                    degraded_ocr_text=corrupt_text,
                )
                if classification == "OCR_FAILURE":
                    ocr_failures += 1
                elif classification == "EXTRACTION_FAILURE":
                    extraction_failures += 1

        count = max(len(ocr_metrics_list), 1)
        mean_cer = sum(m["cer"] for m in ocr_metrics_list) / count
        mean_wer = sum(m["wer"] for m in ocr_metrics_list) / count
        mean_token_f1 = sum(m["token_f1"] for m in ocr_metrics_list) / count

        total_gt = sum(s["gt_count"] for s in extraction_summaries)
        total_pred = sum(s["pred_count"] for s in extraction_summaries)
        total_tp = sum(s["tp"] for s in extraction_summaries)
        prec = total_tp / total_pred if total_pred > 0 else 0.0
        rec = total_tp / total_gt if total_gt > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0

        val_acc = (
            sum(s["value_matches"] for s in extraction_summaries) / total_tp
            if total_tp > 0
            else 0.0
        )
        unit_acc = (
            sum(s["unit_matches"] for s in extraction_summaries) / total_tp
            if total_tp > 0
            else 0.0
        )
        loinc_acc = (
            sum(s["loinc_matches"] for s in extraction_summaries) / total_tp
            if total_tp > 0
            else 0.0
        )

        return {
            "track": "ocr_text",
            "dataset": "lab",
            "degradation_type": "ocr_character_corruption",
            "severity": severity.value,
            "documents_evaluated": len(ocr_metrics_list),
            "ocr_metrics": {
                "mean_cer": round(mean_cer, 4),
                "mean_wer": round(mean_wer, 4),
                "mean_token_f1": round(mean_token_f1, 4),
            },
            "extraction_metrics": {
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4),
                "value_accuracy": round(val_acc, 4),
                "unit_accuracy": round(unit_acc, 4),
                "loinc_accuracy": round(loinc_acc, 4),
            },
            "failure_attribution": {
                "total_observations_evaluated": total_evaluations,
                "ocr_failures": ocr_failures,
                "extraction_failures": extraction_failures,
                "ocr_failure_rate": round(ocr_failures / max(total_evaluations, 1), 4),
                "extraction_failure_rate": round(extraction_failures / max(total_evaluations, 1), 4),
            },
        }

    def run_med_track_b_condition(
        self,
        doc_ids: list[str],
        severity: SeverityLevel,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Evaluate Track B OCR text corruption on medication regimens."""
        ocr_metrics_list = []
        extraction_summaries = []
        ocr_failures = 0
        extraction_failures = 0
        total_evaluations = 0
        batches_data: list[tuple[DocumentOCRRecord, PrescriptionGroundTruth]] = []

        out_ocr_dir = Path("data/degraded/ocr_cache/corrupt_med")

        for d_id in doc_ids:
            clean_ocr_f = Path(f"data/med_ocr_cache/{d_id}.json")
            gt_f = Path(f"data/med_ground_truth/{d_id}.json")
            if not clean_ocr_f.exists() or not gt_f.exists():
                continue

            with open(clean_ocr_f, "r", encoding="utf-8") as f:
                clean_ocr = DocumentOCRRecord.model_validate(json.load(f))
            with open(gt_f, "r", encoding="utf-8") as f:
                gt_rec = PrescriptionGroundTruth.model_validate(json.load(f))

            corrupt_ocr, _ = self.engine.apply_ocr_corruption(
                clean_ocr_record=clean_ocr,
                severity=severity,
                output_dir=out_ocr_dir,
                seed=seed,
            )

            ocr_metric = compute_ocr_quality_metrics(clean_ocr, corrupt_ocr)
            ocr_metrics_list.append(ocr_metric)
            batches_data.append((corrupt_ocr, gt_rec))

        # Batch GLiNER inference for entire condition
        corrupt_ocrs = [b[0] for b in batches_data]
        extracted_meds_list = self.med_parser.parse_documents(corrupt_ocrs) if corrupt_ocrs else []

        for (corrupt_ocr, gt_rec), extracted_med in zip(batches_data, extracted_meds_list):
            eval_res = evaluate_document_medications(gt_rec, extracted_med)
            extraction_summaries.append(eval_res)

            corrupt_text = " ".join(l.text for l in corrupt_ocr.lines)
            extracted_meds = {m.medication_name.lower(): m for m in extracted_med.medications}
            for gt_m in gt_rec.medications:
                total_evaluations += 1
                is_correct = False
                if gt_m.medication_name.lower() in extracted_meds:
                    pred = extracted_meds[gt_m.medication_name.lower()]
                    if (
                        pred.dose is not None
                        and gt_m.dose is not None
                        and abs(pred.dose - gt_m.dose) < 1e-4
                    ):
                        is_correct = True

                classification = classify_field_failure(
                    target_token=gt_m.medication_name,
                    is_field_correct=is_correct,
                    degraded_ocr_text=corrupt_text,
                )
                if classification == "OCR_FAILURE":
                    ocr_failures += 1
                elif classification == "EXTRACTION_FAILURE":
                    extraction_failures += 1

        count = max(len(ocr_metrics_list), 1)
        mean_cer = sum(m["cer"] for m in ocr_metrics_list) / count
        mean_wer = sum(m["wer"] for m in ocr_metrics_list) / count
        mean_token_f1 = sum(m["token_f1"] for m in ocr_metrics_list) / count

        total_gt = sum(s["gt_count"] for s in extraction_summaries)
        total_pred = sum(s["pred_count"] for s in extraction_summaries)
        total_tp = sum(s["tp"] for s in extraction_summaries)
        prec = total_tp / total_pred if total_pred > 0 else 0.0
        rec = total_tp / total_gt if total_gt > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0

        dose_acc = (
            sum(s["dose_matches"] for s in extraction_summaries) / total_tp
            if total_tp > 0
            else 0.0
        )
        unit_acc = (
            sum(s["unit_matches"] for s in extraction_summaries) / total_tp
            if total_tp > 0
            else 0.0
        )
        exact_matches = sum(s["complete_regimen_matches"] for s in extraction_summaries)
        exact_rate = exact_matches / total_gt if total_gt > 0 else 0.0

        return {
            "track": "ocr_text",
            "dataset": "medication",
            "degradation_type": "ocr_character_corruption",
            "severity": severity.value,
            "documents_evaluated": len(ocr_metrics_list),
            "ocr_metrics": {
                "mean_cer": round(mean_cer, 4),
                "mean_wer": round(mean_wer, 4),
                "mean_token_f1": round(mean_token_f1, 4),
            },
            "extraction_metrics": {
                "entity_precision": round(prec, 4),
                "entity_recall": round(rec, 4),
                "entity_f1": round(f1, 4),
                "dose_accuracy": round(dose_acc, 4),
                "unit_accuracy": round(unit_acc, 4),
                "complete_regimen_exact_match": round(exact_rate, 4),
            },
            "failure_attribution": {
                "total_medications_evaluated": total_evaluations,
                "ocr_failures": ocr_failures,
                "extraction_failures": extraction_failures,
                "ocr_failure_rate": round(ocr_failures / max(total_evaluations, 1), 4),
                "extraction_failure_rate": round(extraction_failures / max(total_evaluations, 1), 4),
            },
        }

    def run_full_benchmark(
        self,
        visual_sample_size: int = 5,
        text_sample_size: int = 50,
        output_report_path: Path = DEFAULT_REPORT_PATH,
    ) -> dict[str, Any]:
        """Execute the complete M1 Document Degradation & Robustness Benchmark."""
        t_start = time.time()
        print("\n" + "=" * 65)
        print("  STARTING M1 DOCUMENT DEGRADATION & ROBUSTNESS BENCHMARK  ")
        print("=" * 65)

        # Select documents
        lab_visual_ids = [f"LAB_{i:04d}" for i in range(1, visual_sample_size + 1)]
        med_visual_ids = [f"MED_{i:04d}" for i in range(1, visual_sample_size + 1)]
        lab_text_ids = [f"LAB_{i:04d}" for i in range(1, text_sample_size + 1)]
        med_text_ids = [f"MED_{i:04d}" for i in range(1, text_sample_size + 1)]

        track_a_results: dict[str, Any] = {"lab": {}, "medication": {}}
        track_b_results: dict[str, Any] = {"lab": {}, "medication": {}}
        robustness_curves: list[dict[str, Any]] = []

        severities = [SeverityLevel.LOW, SeverityLevel.MEDIUM, SeverityLevel.HIGH]

        # Key visual conditions for Track A
        visual_conditions = [
            DegradationType.GAUSSIAN_BLUR,
            DegradationType.JPEG_COMPRESSION,
            DegradationType.RESOLUTION_REDUCTION,
            DegradationType.ROTATION_SKEW,
            DegradationType.CONTRAST_BRIGHTNESS,
            DegradationType.PARTIAL_LOCAL,
            DegradationType.COMBINED,
        ]

        # ----------------------------------------------------
        # TRACK A — VISUAL ROBUSTNESS (PaddleOCR)
        # ----------------------------------------------------
        print("\n>>> EXECUTING TRACK A — VISUAL ROBUSTNESS (Forced PaddleOCR) <<<", flush=True)
        for deg_type in visual_conditions:
            deg_key = deg_type.value
            track_a_results["lab"][deg_key] = {}
            track_a_results["medication"][deg_key] = {}

            for sev in severities:
                print(f"  [Track A] Processing {deg_key} ({sev.value})...", flush=True)
                # Lab evaluation
                res_lab = self.run_lab_track_a_condition(lab_visual_ids, deg_type, sev)
                track_a_results["lab"][deg_key][sev.value] = res_lab
                robustness_curves.append({
                    "track": "visual",
                    "dataset": "lab",
                    "degradation": deg_key,
                    "severity": sev.value,
                    "mean_cer": res_lab["ocr_metrics"]["mean_cer"],
                    "extraction_f1": res_lab["extraction_metrics"]["f1"],
                    "ocr_failure_rate": res_lab["failure_attribution"]["ocr_failure_rate"],
                    "extraction_failure_rate": res_lab["failure_attribution"]["extraction_failure_rate"],
                })

                # Medication evaluation
                res_med = self.run_med_track_a_condition(med_visual_ids, deg_type, sev)
                track_a_results["medication"][deg_key][sev.value] = res_med
                robustness_curves.append({
                    "track": "visual",
                    "dataset": "medication",
                    "degradation": deg_key,
                    "severity": sev.value,
                    "mean_cer": res_med["ocr_metrics"]["mean_cer"],
                    "extraction_f1": res_med["extraction_metrics"]["entity_f1"],
                    "complete_regimen_exact_match": res_med["extraction_metrics"]["complete_regimen_exact_match"],
                    "ocr_failure_rate": res_med["failure_attribution"]["ocr_failure_rate"],
                    "extraction_failure_rate": res_med["failure_attribution"]["extraction_failure_rate"],
                })

        # ----------------------------------------------------
        # TRACK B — OCR / TEXT ROBUSTNESS (Controlled Noise)
        # ----------------------------------------------------
        print("\n>>> EXECUTING TRACK B — OCR / TEXT ROBUSTNESS (Character Noise) <<<", flush=True)
        track_b_results["lab"]["ocr_character_corruption"] = {}
        track_b_results["medication"]["ocr_character_corruption"] = {}

        for sev in severities:
            print(f"  [Track B] Processing OCR character corruption ({sev.value}) on {len(lab_text_ids)} docs...", flush=True)
            res_lab_b = self.run_lab_track_b_condition(lab_text_ids, sev)
            track_b_results["lab"]["ocr_character_corruption"][sev.value] = res_lab_b
            robustness_curves.append({
                "track": "ocr_text",
                "dataset": "lab",
                "degradation": "ocr_character_corruption",
                "severity": sev.value,
                "mean_cer": res_lab_b["ocr_metrics"]["mean_cer"],
                "extraction_f1": res_lab_b["extraction_metrics"]["f1"],
                "ocr_failure_rate": res_lab_b["failure_attribution"]["ocr_failure_rate"],
                "extraction_failure_rate": res_lab_b["failure_attribution"]["extraction_failure_rate"],
            })

            res_med_b = self.run_med_track_b_condition(med_text_ids, sev)
            track_b_results["medication"]["ocr_character_corruption"][sev.value] = res_med_b
            robustness_curves.append({
                "track": "ocr_text",
                "dataset": "medication",
                "degradation": "ocr_character_corruption",
                "severity": sev.value,
                "mean_cer": res_med_b["ocr_metrics"]["mean_cer"],
                "extraction_f1": res_med_b["extraction_metrics"]["entity_f1"],
                "complete_regimen_exact_match": res_med_b["extraction_metrics"]["complete_regimen_exact_match"],
                "ocr_failure_rate": res_med_b["failure_attribution"]["ocr_failure_rate"],
                "extraction_failure_rate": res_med_b["failure_attribution"]["extraction_failure_rate"],
            })

        total_duration = round(time.time() - t_start, 2)

        report = {
            "title": "M1 Document Degradation & Robustness Benchmark Report",
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": total_duration,
            "configuration": {
                "visual_sample_size": visual_sample_size,
                "text_sample_size": text_sample_size,
                "paddleocr_dpi": 150,
            },
            "track_a_visual_robustness": track_a_results,
            "track_b_ocr_text_robustness": track_b_results,
            "robustness_curves": robustness_curves,
        }

        output_report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        print("\n" + "=" * 65)
        print(f"  BENCHMARK COMPLETE IN {total_duration:.1f}s")
        print(f"  Report saved to: {output_report_path}")
        print("=" * 65 + "\n")
        return report


def main():
    parser = argparse.ArgumentParser(description="M1 Robustness Benchmark")
    parser.add_argument("--visual-samples", type=int, default=3, help="Sample size per visual condition")
    parser.add_argument("--text-samples", type=int, default=50, help="Sample size per OCR text condition")
    parser.add_argument("--output", type=str, default=str(DEFAULT_REPORT_PATH), help="Output JSON report path")
    args = parser.parse_args()

    bench = RobustnessBenchmark()
    bench.run_full_benchmark(
        visual_sample_size=args.visual_samples,
        text_sample_size=args.text_samples,
        output_report_path=Path(args.output),
    )


if __name__ == "__main__":
    main()
