"""Focused unit and integration tests for the unified CLI / orchestrator.

Tests:
  1. `clindoc process <input_file> --output-dir <out_dir>` on a valid document.
  2. Output validation: verifies fhir_bundle.json and audit_report.json conform strictly to schemas.
  3. `clindoc batch <input_dir> --output-dir <out_dir>` on multiple documents.
  4. Batch summary and artifact layout.
  5. Error handling: non-existent file, unsupported file extension, empty/claimless document.
  6. CLI entrypoint invocation and return codes.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from clindoc.audit import validate_audit_report
from clindoc.cli import batch_process, main, process_document
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict


def _make_token(text: str, x0: float, y0: float, x1: float, y1: float,
                line_id: str = "L0", confidence: float | None = 0.95) -> Token:
    return Token(
        text=text,
        bbox=BBoxDict(x0=x0, y0=y0, x1=x1, y1=y1),
        confidence=confidence,
        line_id=line_id,
    )


def _create_lab_document_text(doc_id: str = "LAB_TEST_001") -> DocumentText:
    """Create a structured DocumentText representing a valid clinical lab report."""
    tokens = [
        # Header line
        _make_token("Test", 10, 10, 50, 20, "L0"),
        _make_token("Name", 55, 10, 100, 20, "L0"),
        _make_token("Value", 105, 10, 150, 20, "L0"),
        _make_token("Unit", 155, 10, 200, 20, "L0"),
        _make_token("Reference", 205, 10, 280, 20, "L0"),
        _make_token("Range", 285, 10, 340, 20, "L0"),
        _make_token("Flag", 345, 10, 380, 20, "L0"),
        # Glucose test row
        _make_token("Glucose", 10, 30, 80, 40, "L1"),
        _make_token("95", 105, 30, 130, 40, "L1"),
        _make_token("mg/dL", 155, 30, 210, 40, "L1"),
        _make_token("70-99", 205, 30, 310, 40, "L1"),
        _make_token("Normal", 345, 30, 400, 40, "L1"),
    ]
    page = DocumentPage(page_number=1, width_px=612, height_px=792, tokens=tokens)
    return DocumentText(document_id=doc_id, source_type="born_digital", pages=[page])


@pytest.fixture
def sample_doc_json(tmp_path: Path) -> Path:
    doc = _create_lab_document_text("LAB_TEST_001")
    doc_path = tmp_path / "LAB_TEST_001.json"
    doc.save(doc_path)
    return doc_path


def test_cli_process_single_document(sample_doc_json: Path, tmp_path: Path) -> None:
    """Test `clindoc process` end-to-end command."""
    out_dir = tmp_path / "output_process"
    argv = ["process", str(sample_doc_json), "--output-dir", str(out_dir)]

    ret_code = main(argv)
    assert ret_code == 0

    fhir_path = out_dir / "fhir_bundle.json"
    audit_path = out_dir / "audit_report.json"

    assert fhir_path.exists(), "fhir_bundle.json was not created"
    assert audit_path.exists(), "audit_report.json was not created"

    # Validate FHIR bundle JSON structure
    with open(fhir_path, "r", encoding="utf-8") as f:
        bundle_data = json.load(f)
    assert bundle_data["resourceType"] == "Bundle"
    assert bundle_data["type"] == "collection"
    assert len(bundle_data["entry"]) > 0

    # Validate Audit Report JSON strictly against schemas/audit_report.json
    with open(audit_path, "r", encoding="utf-8") as f:
        audit_data = json.load(f)
    assert audit_data["document_id"] == "LAB_TEST_001"
    assert audit_data["summary"]["n_claims"] > 0
    assert validate_audit_report(audit_data) is True


def test_process_document_direct_api(sample_doc_json: Path, tmp_path: Path) -> None:
    """Test `process_document` Python API directly."""
    out_dir = tmp_path / "direct_api"
    result = process_document(input_file=sample_doc_json, output_dir=out_dir)

    assert result.document_id == "LAB_TEST_001"
    assert result.fhir_bundle_path.exists()
    assert result.audit_report_path.exists()
    assert result.n_claims > 0
    assert result.n_supported + result.n_discrepancy + result.n_insufficient_evidence == result.n_claims


def test_cli_batch_multiple_documents(tmp_path: Path) -> None:
    """Test `clindoc batch` command processing multiple documents."""
    in_dir = tmp_path / "batch_input"
    in_dir.mkdir(parents=True)

    # Create 2 test lab documents
    doc1 = _create_lab_document_text("LAB_DOC_A")
    doc1.save(in_dir / "LAB_DOC_A.json")

    doc2 = _create_lab_document_text("LAB_DOC_B")
    doc2.save(in_dir / "LAB_DOC_B.json")

    out_dir = tmp_path / "batch_output"
    argv = ["batch", str(in_dir), "--output-dir", str(out_dir)]

    ret_code = main(argv)
    assert ret_code == 0

    # Check per-document subdirectories and artifacts
    assert (out_dir / "LAB_DOC_A" / "fhir_bundle.json").exists()
    assert (out_dir / "LAB_DOC_A" / "audit_report.json").exists()
    assert (out_dir / "LAB_DOC_B" / "fhir_bundle.json").exists()
    assert (out_dir / "LAB_DOC_B" / "audit_report.json").exists()

    # Check batch summary
    summary_path = out_dir / "batch_summary.json"
    assert summary_path.exists()
    with open(summary_path, "r", encoding="utf-8") as f:
        summary_data = json.load(f)
    assert summary_data["total_documents"] == 2
    assert summary_data["successful"] == 2
    assert summary_data["failed"] == 0


def test_cli_missing_input_file_fails(tmp_path: Path) -> None:
    """Test that missing input file causes failure and exit code 1."""
    missing = tmp_path / "does_not_exist.pdf"
    out_dir = tmp_path / "output_fail"

    ret_code = main(["process", str(missing), "--output-dir", str(out_dir)])
    assert ret_code == 1


def test_cli_unsupported_extension_fails(tmp_path: Path) -> None:
    """Test that unsupported file type causes failure and exit code 1."""
    bad_file = tmp_path / "document.txt"
    bad_file.write_text("Hello world", encoding="utf-8")
    out_dir = tmp_path / "output_fail"

    ret_code = main(["process", str(bad_file), "--output-dir", str(out_dir)])
    assert ret_code == 1


def test_cli_empty_claims_fails(tmp_path: Path) -> None:
    """Test that document producing zero claims fails clearly."""
    # Blank page with no clinical entities
    page = DocumentPage(page_number=1, width_px=612, height_px=792, tokens=[])
    doc = DocumentText(document_id="EMPTY_DOC", source_type="born_digital", pages=[page])
    empty_file = tmp_path / "EMPTY_DOC.json"
    doc.save(empty_file)

    out_dir = tmp_path / "output_empty"
    ret_code = main(["process", str(empty_file), "--output-dir", str(out_dir)])
    assert ret_code == 1


def test_cli_no_args_prints_help() -> None:
    """Test that invocation without subcommand returns code 1."""
    ret_code = main([])
    assert ret_code == 1
