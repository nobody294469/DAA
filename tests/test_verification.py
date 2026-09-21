"""Automated tests for verification baseline, error injection, and provenance preservation."""

import json
from pathlib import Path
import pytest

from clindoc.ocr.schema import DocumentOCRRecord
from clindoc.schema.lab import ExtractedLabReport
from clindoc.verify.error_injection import ERROR_TYPES, inject_single_error
from clindoc.verify.schema import (
    FieldDiscrepancy,
    ReportVerificationResult,
    VerificationStatus,
)
from clindoc.verify.verifier import verify_report
from eval.eval_verification import run_clean_evaluation, run_corrupted_evaluation


@pytest.fixture
def clean_report_and_ocr() -> tuple[ExtractedLabReport, DocumentOCRRecord]:
    """Fixture providing clean LAB_0001 report and corresponding OCR record."""
    report_file = Path("data/extracted/LAB_0001.json")
    ocr_file = Path("data/ocr_cache/LAB_0001.json")
    assert report_file.exists(), "data/extracted/LAB_0001.json must exist"
    assert ocr_file.exists(), "data/ocr_cache/LAB_0001.json must exist"

    with open(report_file, "r", encoding="utf-8") as f:
        report = ExtractedLabReport.model_validate(json.load(f))
    with open(ocr_file, "r", encoding="utf-8") as f:
        ocr_rec = DocumentOCRRecord.model_validate(json.load(f))

    return report, ocr_rec


def test_clean_document_produces_no_false_discrepancies(clean_report_and_ocr):
    """Verify that uncorrupted extraction produces zero false discrepancies."""
    report, ocr = clean_report_and_ocr
    res = verify_report(report, ocr)

    assert res.doc_id == "LAB_0001"
    assert res.overall_status == VerificationStatus.VERIFIED
    assert res.discrepancy_count == 0
    assert len(res.discrepancies) == 0
    assert res.verified_observations == len(report.observations)


def test_numeric_value_corruption_detected(clean_report_and_ocr):
    """Verify that corrupted numerical value is detected with source evidence."""
    report, ocr = clean_report_and_ocr
    corrupted_rep, err = inject_single_error(report, "VALUE_CORRUPTION", target_idx=0)

    res = verify_report(corrupted_rep, ocr)
    assert res.overall_status == VerificationStatus.DISCREPANCY
    assert len(res.discrepancies) >= 1

    disc = next(d for d in res.discrepancies if d.field == "value")
    assert disc.observation == err.observation
    assert disc.extracted_value == err.corrupted_value
    assert disc.source_value == err.original_value
    assert disc.reason == "VALUE_MISMATCH"
    assert disc.page >= 1
    assert len(disc.bbox) == 4
    assert disc.ocr_confidence >= 0.0


def test_unit_corruption_detected(clean_report_and_ocr):
    """Verify that corrupted measurement unit is detected."""
    report, ocr = clean_report_and_ocr
    corrupted_rep, err = inject_single_error(report, "UNIT_CORRUPTION", target_idx=0)

    res = verify_report(corrupted_rep, ocr)
    assert res.overall_status == VerificationStatus.DISCREPANCY

    disc = next(d for d in res.discrepancies if d.field == "unit")
    assert disc.observation == err.observation
    assert disc.extracted_value == err.corrupted_value
    assert disc.source_value == err.original_value
    assert disc.reason == "UNIT_MISMATCH"


def test_test_name_corruption_detected(clean_report_and_ocr):
    """Verify that corrupted test name is detected against source text."""
    report, ocr = clean_report_and_ocr
    corrupted_rep, err = inject_single_error(report, "TEST_NAME_CORRUPTION", target_idx=0)

    res = verify_report(corrupted_rep, ocr)
    assert res.overall_status == VerificationStatus.DISCREPANCY

    disc = next(d for d in res.discrepancies if d.field == "test_name")
    assert disc.extracted_value == err.corrupted_value
    assert disc.source_value == err.original_value
    assert disc.reason == "TEST_NAME_MISMATCH"


def test_reference_range_corruption_detected(clean_report_and_ocr):
    """Verify that corrupted reference interval is detected."""
    report, ocr = clean_report_and_ocr
    corrupted_rep, err = inject_single_error(report, "REF_RANGE_CORRUPTION", target_idx=0)

    res = verify_report(corrupted_rep, ocr)
    assert res.overall_status == VerificationStatus.DISCREPANCY

    disc = next(d for d in res.discrepancies if d.field == "ref_range")
    assert disc.observation == err.observation
    assert disc.extracted_value == err.corrupted_value
    assert disc.source_value == err.original_value
    assert disc.reason == "REF_RANGE_MISMATCH"


def test_abnormal_flag_corruption_detected(clean_report_and_ocr):
    """Verify that corrupted abnormal flag is detected."""
    report, ocr = clean_report_and_ocr
    corrupted_rep, err = inject_single_error(report, "FLAG_CORRUPTION", target_idx=0)

    res = verify_report(corrupted_rep, ocr)
    assert res.overall_status == VerificationStatus.DISCREPANCY

    disc = next(d for d in res.discrepancies if d.field == "flag")
    assert disc.observation == err.observation
    assert disc.extracted_value == err.corrupted_value
    assert disc.source_value == err.original_value
    assert disc.reason == "FLAG_MISMATCH"


def test_missing_observation_detected(clean_report_and_ocr):
    """Verify that dropping an observation from the report triggers OMITTED_SOURCE_OBSERVATION."""
    report, ocr = clean_report_and_ocr
    corrupted_rep, err = inject_single_error(report, "DROPPED_OBSERVATION", target_idx=0)

    res = verify_report(corrupted_rep, ocr)
    assert res.overall_status == VerificationStatus.DISCREPANCY

    disc = next(d for d in res.discrepancies if d.reason == "OMITTED_SOURCE_OBSERVATION")
    assert disc.source_value == err.original_value
    assert disc.field == "observation_presence"


def test_extra_hallucinated_observation_detected(clean_report_and_ocr):
    """Verify that hallucinating an ungrounded observation triggers UNSUPPORTED_OBSERVATION."""
    report, ocr = clean_report_and_ocr
    corrupted_rep, err = inject_single_error(report, "HALLUCINATED_OBSERVATION", target_idx=0)

    res = verify_report(corrupted_rep, ocr)
    assert res.overall_status == VerificationStatus.DISCREPANCY

    disc = next(d for d in res.discrepancies if d.reason == "UNSUPPORTED_OBSERVATION")
    assert disc.observation == err.corrupted_value
    assert disc.source_value is None


def test_evidence_preservation_in_discrepancies(clean_report_and_ocr):
    """Verify that every discrepancy contains page, bbox, source text, and confidence."""
    report, ocr = clean_report_and_ocr
    corrupted_rep, err = inject_single_error(report, "VALUE_CORRUPTION", target_idx=0)

    res = verify_report(corrupted_rep, ocr)
    for disc in res.discrepancies:
        assert isinstance(disc, FieldDiscrepancy)
        assert disc.page >= 1
        assert len(disc.bbox) == 4
        assert disc.bbox[0] < disc.bbox[2]
        assert disc.bbox[1] < disc.bbox[3]
        assert disc.source_evidence_text.strip()
        assert 0.0 <= disc.ocr_confidence <= 1.0


def test_full_evaluation_benchmark():
    """Verify that full clean and corrupted benchmark suites run and achieve high recall and specificity."""
    clean = run_clean_evaluation()
    assert clean["total_documents"] == 50
    assert clean["clean_false_positive_rate"] == 0.0
    assert clean["clean_accuracy"] == 1.0

    corrupted = run_corrupted_evaluation()
    assert corrupted["total_documents"] == 50
    assert corrupted["recall"] >= 0.95
    assert corrupted["precision"] >= 0.90
    assert corrupted["f1"] >= 0.90
