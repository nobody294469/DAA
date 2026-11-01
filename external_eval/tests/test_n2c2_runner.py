"""Tests for n2c2 2018 Track 2 evaluation runner.

Authority: User Request / Evaluation Protocol Requirements
Location: external_eval/tests/test_n2c2_runner.py

Verifies:
1. Directory ingestion on valid multi-document directory fixtures.
2. Missing annotation file detection and error tracking.
3. Mismatched document IDs / orphan annotation file detection.
4. Empty document file detection and handling.
5. Malformed annotations in directory processing.
6. Unavailable model provider handling (Zero-Mock policy enforcement).
7. End-to-end evaluation run with PHI safety and protocol disclaimer verification.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "external_eval"))

from runners.n2c2_2018_runner import (
    validate_and_load_dataset,
    verify_model_provider_availability,
    run_n2c2_evaluation,
    ModelProviderUnavailableError,
    DatasetIntegrityError,
    get_git_revision,
)


@pytest.fixture
def valid_dataset_dir(tmp_path: Path) -> Path:
    """Creates a synthetic directory with 2 valid document-annotation pairs."""
    data_dir = tmp_path / "valid_n2c2"
    data_dir.mkdir()

    # Doc 100
    doc1_txt = "Patient discharged on Metformin 500 mg PO BID for 30 days."
    doc1_ann = (
        "T1\tDrug 22 31\tMetformin\n"
        "T2\tStrength 32 38\t500 mg\n"
        "T3\tRoute 39 41\tPO\n"
        "T4\tFrequency 42 45\tBID\n"
        "T5\tDuration 50 57\t30 days\n"
    )
    (data_dir / "100.txt").write_text(doc1_txt, encoding="utf-8")
    (data_dir / "100.ann").write_text(doc1_ann, encoding="utf-8")

    # Doc 101
    doc2_txt = "Resume Lisinopril 20 mg once daily."
    doc2_ann = (
        "T1\tDrug 7 17\tLisinopril\n"
        "T2\tStrength 18 23\t20 mg\n"
        "T3\tFrequency 24 34\tonce daily\n"
    )
    (data_dir / "101.txt").write_text(doc2_txt, encoding="utf-8")
    (data_dir / "101.ann").write_text(doc2_ann, encoding="utf-8")

    return data_dir


def test_directory_ingestion_valid(valid_dataset_dir: Path):
    """Verify ingestion of valid multi-document directory."""
    gt_by_doc, docs_text_by_id, summary = validate_and_load_dataset(
        data_dir=valid_dataset_dir,
        partition="test",
    )

    assert summary.valid_document_pairs == 2
    assert summary.total_txt_files == 2
    assert summary.total_ann_files == 2
    assert len(summary.processing_failures) == 0
    assert summary.total_accepted_claims == 8  # 5 in doc1 + 3 in doc2
    assert summary.accepted_by_field["med.name"] == 2
    assert summary.accepted_by_field["med.dose"] == 2
    assert summary.accepted_by_field["med.frequency"] == 2
    assert summary.accepted_by_field["med.route"] == 1
    assert summary.accepted_by_field["med.duration"] == 1

    assert "100" in gt_by_doc
    assert "101" in gt_by_doc
    assert "100" in docs_text_by_id
    assert "101" in docs_text_by_id


def test_missing_annotation_file(tmp_path: Path):
    """Verify detection when a .txt file has no matching .ann file."""
    data_dir = tmp_path / "missing_ann"
    data_dir.mkdir()

    (data_dir / "200.txt").write_text("Aspirin 81 mg daily.", encoding="utf-8")
    (data_dir / "200.ann").write_text("T1\tDrug 0 7\tAspirin\n", encoding="utf-8")

    # Unmatched .txt
    (data_dir / "201.txt").write_text("No annotation file for this note.", encoding="utf-8")

    gt_by_doc, docs_text_by_id, summary = validate_and_load_dataset(data_dir, partition="test")

    assert summary.valid_document_pairs == 1
    assert "200" in gt_by_doc
    assert "201" not in gt_by_doc
    assert len(summary.processing_failures) == 1
    failure = summary.processing_failures[0]
    assert failure.document_id == "201"
    assert failure.failure_type == "missing_ann_file"


def test_mismatched_id_orphan_annotation(tmp_path: Path):
    """Verify detection when a .ann file has no matching .txt file."""
    data_dir = tmp_path / "orphan_ann"
    data_dir.mkdir()

    (data_dir / "300.txt").write_text("Atorvastatin 40 mg daily.", encoding="utf-8")
    (data_dir / "300.ann").write_text("T1\tDrug 0 12\tAtorvastatin\n", encoding="utf-8")

    # Orphan .ann with no 301.txt
    (data_dir / "301.ann").write_text("T1\tDrug 0 10\tAmlodipine\n", encoding="utf-8")

    gt_by_doc, docs_text_by_id, summary = validate_and_load_dataset(data_dir, partition="test")

    assert summary.valid_document_pairs == 1
    assert "300" in gt_by_doc
    assert "301" not in gt_by_doc
    assert len(summary.processing_failures) == 1
    failure = summary.processing_failures[0]
    assert failure.document_id == "301"
    assert failure.failure_type == "missing_txt_file"


def test_empty_document(tmp_path: Path):
    """Verify rejection of empty clinical text files."""
    data_dir = tmp_path / "empty_doc"
    data_dir.mkdir()

    (data_dir / "400.txt").write_text("   \n\t  \n", encoding="utf-8")
    (data_dir / "400.ann").write_text("T1\tDrug 0 5\tEmpty\n", encoding="utf-8")

    gt_by_doc, docs_text_by_id, summary = validate_and_load_dataset(data_dir, partition="test")

    assert summary.valid_document_pairs == 0
    assert "400" not in gt_by_doc
    assert len(summary.processing_failures) == 1
    failure = summary.processing_failures[0]
    assert failure.document_id == "400"
    assert failure.failure_type == "empty_document"


def test_malformed_annotations_in_directory(tmp_path: Path):
    """Verify handling of malformed lines and unsupported tags in BRAT directory."""
    data_dir = tmp_path / "malformed_dir"
    data_dir.mkdir()

    doc_text = "Patient advised Tylenol 650 mg PO PRN pain."
    doc_ann = (
        "T1\tDrug 16 23\tTylenol\n"
        "T2\tReason 38 42\tpain\n"  # Unsupported tag Reason
        "BadLineWithoutTabsOrPrefix\n"  # Malformed line
    )
    (data_dir / "500.txt").write_text(doc_text, encoding="utf-8")
    (data_dir / "500.ann").write_text(doc_ann, encoding="utf-8")

    gt_by_doc, docs_text_by_id, summary = validate_and_load_dataset(data_dir, partition="test")

    assert summary.valid_document_pairs == 1
    assert summary.total_accepted_claims == 1
    assert summary.total_unsupported_annotations == 1
    assert summary.unsupported_by_label.get("Reason") == 1
    assert summary.total_rejected_annotations == 1
    assert "malformed_line" in summary.rejected_by_reason


def test_unavailable_model_provider_zero_mock_enforcement():
    """Verify that unavailable model endpoints abort with ModelProviderUnavailableError."""
    # Attempting llm_narrative with dead port must fail fast
    with pytest.raises(ModelProviderUnavailableError) as exc_info:
        verify_model_provider_availability(
            pipeline="llm_narrative",
            vllm_base_url="http://127.0.0.1:59999/v1",
        )
    assert "UNAVAILABLE" in str(exc_info.value)
    assert "MockLLMProvider is strictly forbidden" in str(exc_info.value)


def test_end_to_end_deterministic_evaluation(valid_dataset_dir: Path, tmp_path: Path):
    """Run full evaluation pipeline using genuine deterministic extractor and verify output."""
    out_dir = tmp_path / "output_eval"
    report = run_n2c2_evaluation(
        data_dir=valid_dataset_dir,
        partition="test",
        pipeline="deterministic",
        output_dir=out_dir,
    )

    # 1. Verify benchmark metadata
    bm = report["benchmark_metadata"]
    assert bm["dataset_name"] == "n2c2 2018 Track 2 (Medication Extraction & ADE)"
    assert bm["partition"] == "test"
    assert bm["total_documents_evaluated"] == 2
    assert bm["total_gt_claims"] == 8
    assert "git_revision" in bm
    assert "evaluation_timestamp_utc" in bm

    # 2. Verify model metadata
    mm = report["model_metadata"]
    assert mm["pipeline_name"] == "deterministic"
    assert mm["is_genuine_production"] is True

    # 3. Verify protocol disclaimer
    assert "protocol_disclaimer" in report
    assert "Official n2c2 2018 Challenge Protocol Disclaimer" in report["protocol_disclaimer"]
    assert "conflated" in report["protocol_disclaimer"]

    # 4. Verify canonical metrics
    metrics = report["canonical_evaluation_metrics"]
    assert "micro_precision" in metrics
    assert "micro_recall" in metrics
    assert "micro_f1" in metrics
    assert "macro_f1" in metrics
    assert "metrics_per_field" in metrics

    # 5. Verify PHI safety: No raw patient text in JSON
    json_path = Path(report["saved_report_path"])
    assert json_path.exists()
    json_content = json_path.read_text(encoding="utf-8")
    assert "Patient discharged on Metformin" not in json_content
    assert "Resume Lisinopril" not in json_content


def test_empty_directory_raises_dataset_integrity_error(tmp_path: Path):
    """Verify empty directory raises DatasetIntegrityError."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()

    with pytest.raises(DatasetIntegrityError):
        run_n2c2_evaluation(
            data_dir=empty_dir,
            partition="test",
            pipeline="deterministic",
        )
