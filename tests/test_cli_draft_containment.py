"""Comprehensive tests for DEF-004: CLI Draft Containment & Governance Notice (Option A Ratified).

Authority:
  - CLINICALDOC_P0_IMPLEMENTATION_EXECUTION_PLAN.md §5, Step 5
  - CLINICALDOC_P0_SECURITY_REMEDIATION_DESIGN_v2.md §5.2
  - HL7 FHIR R4B 4.3.0 normative bundle specification

Coverage:
  1. Both CLI subcommands (`clindoc process` and `clindoc batch`).
  2. Normative draft markers:
     - Bundle.meta.tag: UNREVIEWED_CLI_DRAFT and UNAUTHORIZED_RESEARCH_DRAFT.
     - Bundle.identifier: UNAUTHORIZED_RESEARCH_DRAFT.
     - Zero non-standard Bundle.meta.security labels.
  3. CLI output containment:
     - Mandatory fhir_bundle.draft.json filename.
     - Absolute absence of plain fhir_bundle.json across all CLI outputs.
     - Mandatory sidecar GOVERNANCE_NOTICE.txt with authoritative disclaimer text.
  4. Command-line parser enforcement:
     - Strict rejection of bypass flags: --force, --approved, --skip-review,
       --force-final, --bypass-review, --allow-unreviewed.
  5. Cryptographic signature absence:
     - Bundle.signature is strictly None for CLI drafts.
  6. FHIR schema validation:
     - Full validation through FHIRValidator confirming zero schema errors.
  7. Institutional transmission boundary enforcement:
     - Rejection of draft bundle by MockFHIRTransport (EHRDraftRejectionError / 403).
     - Rejection of draft bundle by HTTPFHIRTransport (EHRDraftRejectionError / 403).
     - Rejection of draft bundle by MLLPFHIRTransport (EHRDraftRejectionError / 403).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import pytest

from fhir.resources.R4B.bundle import Bundle
from clindoc.cli import (
    DRAFT_AUTH_CODE,
    DRAFT_AUTH_DISPLAY,
    DRAFT_AUTH_SYSTEM,
    DRAFT_GOVERNANCE_CODE,
    DRAFT_GOVERNANCE_DISPLAY,
    DRAFT_GOVERNANCE_SYSTEM,
    DRAFT_IDENTIFIER_SYSTEM,
    DRAFT_IDENTIFIER_VALUE,
    GOVERNANCE_NOTICE_TEXT,
    batch_process,
    main,
    process_document,
)
from clindoc.fhir.validator import FHIRValidator
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict
from clindoc.services.ehr_transport import (
    EHRDraftRejectionError,
    HTTPFHIRTransport,
    HTTPFHIRTransportConfig,
    MLLPFHIRTransport,
    MockFHIRTransport,
    assert_no_draft_tags,
)


def _make_token(text: str, x0: float, y0: float, x1: float, y1: float,
                line_id: str = "L0", confidence: float | None = 0.95) -> Token:
    return Token(
        text=text,
        bbox=BBoxDict(x0=x0, y0=y0, x1=x1, y1=y1),
        confidence=confidence,
        line_id=line_id,
    )


def _create_lab_document_text(doc_id: str = "LAB_DRAFT_001") -> DocumentText:
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
    doc = _create_lab_document_text("LAB_DRAFT_001")
    doc_path = tmp_path / "LAB_DRAFT_001.json"
    doc.save(doc_path)
    return doc_path


# ─── TEST 1: SINGLE DOCUMENT CLI PROCESS DRAFT MARKERS & CONTAINMENT ─────────


def test_cli_process_emits_normative_draft_markers(sample_doc_json: Path, tmp_path: Path) -> None:
    """Verify `clindoc process` stamps normative draft markers and omits security labels / signature."""
    out_dir = tmp_path / "proc_draft_out"
    ret = main(["process", str(sample_doc_json), "--output-dir", str(out_dir)])
    assert ret == 0

    # 1. Output files: fhir_bundle.draft.json exists, fhir_bundle.json does NOT
    draft_file = out_dir / "fhir_bundle.draft.json"
    assert draft_file.exists(), "fhir_bundle.draft.json must exist"
    assert not (out_dir / "fhir_bundle.json").exists(), "Plain fhir_bundle.json MUST NOT be created"

    # 2. Sidecar warning exists
    notice_file = out_dir / "GOVERNANCE_NOTICE.txt"
    assert notice_file.exists(), "GOVERNANCE_NOTICE.txt must exist"
    notice_text = notice_file.read_text(encoding="utf-8")
    assert "WARNING: UNAUTHORIZED RESEARCH DRAFT" in notice_text
    assert "Mode 0 clinical review" in notice_text
    assert "Gate 9 cryptographic audit ledger verification" in notice_text

    # 3. Payload inspection
    with open(draft_file, "r", encoding="utf-8") as f:
        bundle_dict = json.load(f)

    # Normative tags
    meta = bundle_dict.get("meta") or {}
    tags = meta.get("tag") or []
    tag_map = {t.get("code"): t for t in tags}

    assert DRAFT_GOVERNANCE_CODE in tag_map, f"Missing tag {DRAFT_GOVERNANCE_CODE}"
    assert tag_map[DRAFT_GOVERNANCE_CODE]["system"] == DRAFT_GOVERNANCE_SYSTEM
    assert tag_map[DRAFT_GOVERNANCE_CODE]["display"] == DRAFT_GOVERNANCE_DISPLAY

    assert DRAFT_AUTH_CODE in tag_map, f"Missing tag {DRAFT_AUTH_CODE}"
    assert tag_map[DRAFT_AUTH_CODE]["system"] == DRAFT_AUTH_SYSTEM
    assert tag_map[DRAFT_AUTH_CODE]["display"] == DRAFT_AUTH_DISPLAY

    # No non-standard Bundle.meta.security
    security = meta.get("security")
    assert security is None or len(security) == 0, f"meta.security must be empty, found {security}"

    # Normative identifier
    ident = bundle_dict.get("identifier")
    assert ident is not None, "Bundle.identifier must be present"
    assert ident.get("system") == DRAFT_IDENTIFIER_SYSTEM
    assert ident.get("value") == DRAFT_IDENTIFIER_VALUE

    # Signature must be strictly absent
    assert bundle_dict.get("signature") is None, "Bundle.signature must not be present on CLI drafts"


# ─── TEST 2: BATCH CLI PROCESS DRAFT CONTAINMENT ─────────────────────────────


def test_cli_batch_emits_draft_bundles_and_notices(tmp_path: Path) -> None:
    """Verify `clindoc batch` writes only .draft.json bundles and sidecar notices at all levels."""
    in_dir = tmp_path / "batch_input"
    in_dir.mkdir(parents=True)

    doc_a = _create_lab_document_text("BATCH_DOC_1")
    doc_a.save(in_dir / "BATCH_DOC_1.json")
    doc_b = _create_lab_document_text("BATCH_DOC_2")
    doc_b.save(in_dir / "BATCH_DOC_2.json")

    out_dir = tmp_path / "batch_out"
    ret = main(["batch", str(in_dir), "--output-dir", str(out_dir)])
    assert ret == 0

    # Check root level
    assert (out_dir / "GOVERNANCE_NOTICE.txt").exists()
    assert (out_dir / "BATCH_DOC_1_fhir_bundle.draft.json").exists()
    assert (out_dir / "BATCH_DOC_2_fhir_bundle.draft.json").exists()
    assert not (out_dir / "BATCH_DOC_1_fhir_bundle.json").exists()
    assert not (out_dir / "BATCH_DOC_2_fhir_bundle.json").exists()

    # Check subdirectories
    for doc_id in ["BATCH_DOC_1", "BATCH_DOC_2"]:
        sub_dir = out_dir / doc_id
        assert (sub_dir / "fhir_bundle.draft.json").exists()
        assert (sub_dir / "GOVERNANCE_NOTICE.txt").exists()
        assert not (sub_dir / "fhir_bundle.json").exists()

        with open(sub_dir / "fhir_bundle.draft.json", "r", encoding="utf-8") as f:
            b_dict = json.load(f)
        tags = (b_dict.get("meta") or {}).get("tag") or []
        tag_codes = {t.get("code") for t in tags}
        assert DRAFT_GOVERNANCE_CODE in tag_codes
        assert DRAFT_AUTH_CODE in tag_codes
        assert b_dict.get("identifier", {}).get("value") == DRAFT_IDENTIFIER_VALUE
        assert b_dict.get("signature") is None


# ─── TEST 3: STRICT REJECTION OF BYPASS FLAGS ────────────────────────────────


@pytest.mark.parametrize("flag", [
    "--force",
    "--approved",
    "--skip-review",
    "--force-final",
    "--bypass-review",
    "--allow-unreviewed",
    "--force=true",
    "--skip-review=1",
])
def test_cli_rejects_bypass_flags(sample_doc_json: Path, tmp_path: Path, flag: str) -> None:
    """Verify that any attempt to pass review bypass flags exits with code 1 and error message."""
    out_dir = tmp_path / "bypass_attempt"
    argv = ["process", str(sample_doc_json), "--output-dir", str(out_dir), flag]

    # Capture stderr
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        ret = main(argv)
        err_output = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr

    assert ret == 1, f"Expected return code 1 for bypass flag {flag}, got {ret}"
    assert "Forbidden CLI bypass flag" in err_output
    assert not (out_dir / "fhir_bundle.draft.json").exists()
    assert not (out_dir / "fhir_bundle.json").exists()


# ─── TEST 4: FHIR VALIDATOR ACCEPTS NORMATIVE DRAFT MARKERS ──────────────────


def test_fhir_validator_accepts_normative_draft_bundle(sample_doc_json: Path, tmp_path: Path) -> None:
    """Verify that normative FHIR R4B draft markers pass FHIRValidator with zero schema errors."""
    out_dir = tmp_path / "validator_test"
    result = process_document(input_file=sample_doc_json, output_dir=out_dir)

    validator = FHIRValidator()
    val_res = validator.validate_bundle(result.fhir_bundle)

    assert val_res.is_valid is True, f"Draft bundle failed FHIR validation: {val_res.errors}"
    assert len(val_res.errors) == 0

    # Also test from raw JSON file
    with open(result.fhir_bundle_path, "r", encoding="utf-8") as f:
        raw_json = f.read()
    val_json_res = validator.validate_bundle(raw_json)
    assert val_json_res.is_valid is True
    assert len(val_json_res.errors) == 0


# ─── TEST 5: INSTITUTIONAL TRANSMISSION BOUNDARIES REJECT DRAFT BUNDLES ──────


def test_mock_fhir_transport_rejects_cli_draft_bundle(sample_doc_json: Path, tmp_path: Path) -> None:
    """Verify that MockFHIRTransport raises EHRDraftRejectionError (HTTP 403) on draft bundles."""
    out_dir = tmp_path / "transport_test"
    result = process_document(input_file=sample_doc_json, output_dir=out_dir)

    transport = MockFHIRTransport()
    bundle_json = result.fhir_bundle.json(indent=2)

    with pytest.raises(EHRDraftRejectionError) as exc_info:
        transport.transmit(
            bundle_json,
            idempotency_key="test-key-1",
            correlation_id="test-corr-1",
            document_id="LAB_DRAFT_001",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_type == "DRAFT_REJECTED"
    assert "EHR boundary rejected bundle bearing draft tag" in str(exc_info.value)


def test_http_fhir_transport_rejects_cli_draft_bundle(sample_doc_json: Path, tmp_path: Path) -> None:
    """Verify that HTTPFHIRTransport raises EHRDraftRejectionError (HTTP 403) before sending."""
    out_dir = tmp_path / "transport_test_http"
    result = process_document(input_file=sample_doc_json, output_dir=out_dir)

    config = HTTPFHIRTransportConfig(endpoint_url="https://mock-ehr.hospital.org/fhir")
    transport = HTTPFHIRTransport(config)
    bundle_json = result.fhir_bundle.json(indent=2)

    with pytest.raises(EHRDraftRejectionError) as exc_info:
        transport.transmit(
            bundle_json,
            idempotency_key="test-key-2",
            correlation_id="test-corr-2",
            document_id="LAB_DRAFT_001",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_type == "DRAFT_REJECTED"


def test_mllp_fhir_transport_rejects_cli_draft_bundle(sample_doc_json: Path, tmp_path: Path) -> None:
    """Verify that MLLPFHIRTransport raises EHRDraftRejectionError (HTTP 403) on draft bundles."""
    out_dir = tmp_path / "transport_test_mllp"
    result = process_document(input_file=sample_doc_json, output_dir=out_dir)

    transport = MLLPFHIRTransport(host="127.0.0.1", port=2575)
    bundle_json = result.fhir_bundle.json(indent=2)

    with pytest.raises(EHRDraftRejectionError) as exc_info:
        transport.transmit(
            bundle_json,
            idempotency_key="test-key-3",
            correlation_id="test-corr-3",
            document_id="LAB_DRAFT_001",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_type == "DRAFT_REJECTED"


def test_assert_no_draft_tags_detects_draft_markers() -> None:
    """Direct verification of assert_no_draft_tags across all draft marker permutations."""
    # 1. Clean approved bundle passes
    clean_bundle = {
        "resourceType": "Bundle",
        "id": "clean-bundle-1",
        "meta": {"tag": [{"code": "CLINICALLY_REVIEWED", "system": "http://clindoc.org/fhir/tag/governance-status"}]},
    }
    assert_no_draft_tags(clean_bundle)  # No exception

    # 2. Tag with UNREVIEWED_CLI_DRAFT fails
    draft_tag_bundle = {
        "resourceType": "Bundle",
        "id": "draft-bundle-1",
        "meta": {"tag": [{"code": "UNREVIEWED_CLI_DRAFT", "system": "http://clindoc.org/fhir/tag/governance-status"}]},
    }
    with pytest.raises(EHRDraftRejectionError):
        assert_no_draft_tags(draft_tag_bundle)

    # 3. Identifier with UNAUTHORIZED_RESEARCH_DRAFT fails
    draft_ident_bundle = {
        "resourceType": "Bundle",
        "id": "draft-bundle-2",
        "identifier": {"value": "UNAUTHORIZED_RESEARCH_DRAFT", "system": "http://clindoc.org/fhir/identifier/export-authorization"},
    }
    with pytest.raises(EHRDraftRejectionError):
        assert_no_draft_tags(draft_ident_bundle)
