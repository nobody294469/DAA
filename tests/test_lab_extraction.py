"""Automated tests for laboratory extraction, schema validation, evidence preservation, and evaluation."""

import json
from pathlib import Path
import tempfile
import pytest

from clindoc.extract.lab_parser import extract_lab_report
from clindoc.extract.runner import batch_extract_all, extract_cached_document
from clindoc.ocr.schema import DocumentOCRRecord
from clindoc.schema.evidence import SourceEvidence
from clindoc.schema.lab import ExtractedLabReport, ExtractedObservation
from clindoc.synth.schema import LabReportGroundTruth
from eval.eval_extraction import evaluate_dataset, evaluate_document_extraction


@pytest.fixture
def sample_ocr_record() -> DocumentOCRRecord:
    """Fixture providing cached OCR record for LAB_0001."""
    ocr_file = Path("data/ocr_cache/LAB_0001.json")
    assert ocr_file.exists(), "data/ocr_cache/LAB_0001.json must exist"
    with open(ocr_file, "r", encoding="utf-8") as f:
        return DocumentOCRRecord.model_validate(json.load(f))


@pytest.fixture
def sample_gt_record() -> LabReportGroundTruth:
    """Fixture providing ground truth record for LAB_0001."""
    gt_file = Path("data/ground_truth/LAB_0001.json")
    assert gt_file.exists(), "data/ground_truth/LAB_0001.json must exist"
    with open(gt_file, "r", encoding="utf-8") as f:
        return LabReportGroundTruth.model_validate(json.load(f))


def test_single_known_report_extraction(sample_ocr_record, sample_gt_record):
    """Verify that LAB_0001 extracts structured observations matching clinical ground truth."""
    extracted = extract_lab_report(sample_ocr_record)

    assert extracted.doc_id == "LAB_0001"
    assert extracted.patient is not None
    assert extracted.patient.patient_id == sample_gt_record.patient.patient_id
    assert extracted.patient.name == sample_gt_record.patient.name
    assert extracted.patient.dob == sample_gt_record.patient.dob
    assert extracted.patient.gender == sample_gt_record.patient.gender
    assert extracted.report_date == sample_gt_record.report_date
    assert extracted.ordering_provider == sample_gt_record.ordering_provider

    # LAB_0001 has 14 tests in Comprehensive Metabolic Panel
    assert len(extracted.observations) == len(sample_gt_record.tests)

    # Check a specific test e.g. Glucose
    glucose_obs = next(o for o in extracted.observations if o.test_name == "Glucose")
    glucose_gt = next(t for t in sample_gt_record.tests if t.test_name == "Glucose")
    assert glucose_obs.value == glucose_gt.value
    assert glucose_obs.unit == glucose_gt.unit
    assert glucose_obs.loinc_code == "2345-7"
    assert glucose_obs.ref_range_text == glucose_gt.ref_range_text
    assert glucose_obs.abnormal_flag == glucose_gt.flag


def test_schema_validation(sample_ocr_record):
    """Verify that extracted report adheres strictly to intermediate Pydantic schema."""
    extracted = extract_lab_report(sample_ocr_record)
    serialized_json = extracted.model_dump_json()

    # Re-deserialize to ensure complete schema compliance
    reloaded = ExtractedLabReport.model_validate_json(serialized_json)
    assert reloaded.doc_id == extracted.doc_id
    assert len(reloaded.observations) == len(extracted.observations)

    # Validate observation constraints
    for obs in reloaded.observations:
        assert isinstance(obs.value, float)
        assert obs.unit
        assert obs.loinc_code is not None
        assert obs.abnormal_flag in ("NORMAL", "HIGH", "LOW")
        assert obs.source_model == "lab_row_parser"


def test_evidence_preservation(sample_ocr_record):
    """Verify that 100% of extracted observations contain traceable source evidence."""
    extracted = extract_lab_report(sample_ocr_record)

    assert len(extracted.observations) > 0
    for obs in extracted.observations:
        ev = obs.source_evidence
        assert isinstance(ev, SourceEvidence)
        assert ev.text.strip(), "Evidence text must not be empty"
        assert obs.test_name in ev.text or obs.raw_value in ev.text
        assert ev.page >= 1
        assert len(ev.bbox) == 4
        x0, y0, x1, y1 = ev.bbox
        assert x0 < x1, f"Invalid bbox width: {ev.bbox}"
        assert y0 < y1, f"Invalid bbox height: {ev.bbox}"
        assert 0.0 <= ev.ocr_confidence <= 1.0


def test_evaluation_against_ground_truth(sample_ocr_record, sample_gt_record):
    """Verify evaluation metric computation on a single document."""
    extracted = extract_lab_report(sample_ocr_record)
    doc_metrics = evaluate_document_extraction(sample_gt_record, extracted)

    assert doc_metrics["doc_id"] == "LAB_0001"
    assert doc_metrics["gt_count"] == len(sample_gt_record.tests)
    assert doc_metrics["pred_count"] == len(extracted.observations)
    assert doc_metrics["tp"] == len(sample_gt_record.tests)
    assert doc_metrics["fp"] == 0
    assert doc_metrics["fn"] == 0
    assert doc_metrics["all_fields_matches"] == len(sample_gt_record.tests)
    assert doc_metrics["patient_id_match"] is True
    assert doc_metrics["patient_name_match"] is True
    assert doc_metrics["dob_match"] is True
    assert doc_metrics["gender_match"] is True
    assert doc_metrics["date_match"] is True
    assert doc_metrics["doctor_match"] is True


def test_batch_extraction_and_dataset_evaluation():
    """Verify batch extraction over full dataset and automated metric evaluation."""
    metrics = evaluate_dataset("data/ground_truth", "data/extracted")

    assert metrics["num_documents"] == 50
    assert metrics["total_gt_observations"] > 500
    assert metrics["total_pred_observations"] == metrics["total_gt_observations"]
    assert metrics["detection_precision"] >= 0.95
    assert metrics["detection_recall"] >= 0.95
    assert metrics["detection_f1"] >= 0.95
    assert metrics["field_accuracies"]["test_name_accuracy"] >= 0.95
    assert metrics["field_accuracies"]["value_accuracy"] >= 0.95
    assert metrics["field_accuracies"]["loinc_accuracy"] >= 0.95
    assert metrics["field_accuracies"]["all_fields_exact_match"] >= 0.95
