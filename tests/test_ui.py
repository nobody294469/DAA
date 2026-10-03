"""Focused unit and headless integration tests for the Streamlit Reviewer UI.

Tests:
  1. Document page bounding box rendering with verdict color-coding.
  2. Verdict badges, cascade stage badges, and confidence formatting.
  3. Streamlit application initial view rendering (headless via AppTest).
  4. Streamlit application demo execution flow with metrics, claims, and download artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path
from PIL import Image
import pytest
from streamlit.testing.v1 import AppTest

from clindoc.extract.contracts import BBoxModel, Evidence
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict
from clindoc.ui.render import (
    format_confidence,
    get_stage_badge_html,
    get_verdict_badge_html,
    render_document_page_with_bboxes,
)


@pytest.fixture
def sample_evidence() -> Evidence:
    return Evidence(
        evidence_id="ev-test-1",
        document_id="DOC_TEST_01",
        page_number=1,
        text="Glucose 115 mg/dL",
        char_start=0,
        char_end=17,
        bbox=BBoxModel(x0=50.0, y0=100.0, x1=200.0, y1=130.0),
        ocr_confidence=0.96,
    )


@pytest.fixture
def sample_doc_json(tmp_path: Path) -> Path:
    tokens = [
        Token(text="Glucose", bbox=BBoxDict(x0=50, y0=100, x1=120, y1=120), line_id="L1"),
        Token(text="115", bbox=BBoxDict(x0=130, y0=100, x1=160, y1=120), line_id="L1"),
        Token(text="mg/dL", bbox=BBoxDict(x0=170, y0=100, x1=220, y1=120), line_id="L1"),
    ]
    page = DocumentPage(page_number=1, width_px=612, height_px=792, tokens=tokens)
    doc = DocumentText(document_id="DOC_TEST_01", source_type="born_digital", pages=[page])
    doc_path = tmp_path / "DOC_TEST_01.json"
    doc.save(doc_path)
    return doc_path


def test_render_page_image_with_bboxes(sample_doc_json: Path, sample_evidence: Evidence) -> None:
    """Test rendering document page with overlaid color-coded bounding boxes."""
    annotations = [
        (sample_evidence, "SUPPORTED", "lab.value"),
    ]
    img = render_document_page_with_bboxes(
        file_path=sample_doc_json,
        page_number=1,
        evidence_annotations=annotations,
    )

    assert isinstance(img, Image.Image)
    assert img.width >= 400
    assert img.height >= 500


def test_verdict_and_stage_badges_html() -> None:
    """Test HTML badge formatting for verdicts and stages."""
    supp_html = get_verdict_badge_html("SUPPORTED")
    assert "SUPPORTED" in supp_html
    assert "#155724" in supp_html

    disc_html = get_verdict_badge_html("DISCREPANCY")
    assert "DISCREPANCY" in disc_html
    assert "#721c24" in disc_html

    insuff_html = get_verdict_badge_html("INSUFFICIENT_EVIDENCE")
    assert "INSUFFICIENT_EVIDENCE" in insuff_html

    stage_a_html = get_stage_badge_html("A")
    assert "Deterministic" in stage_a_html

    stage_b_html = get_stage_badge_html("B")
    assert "ModernBERT" in stage_b_html

    stage_c_html = get_stage_badge_html("C")
    assert "LLM Judge" in stage_c_html

    # Confidence formatting
    assert format_confidence(0.9854) == "98.5%"
    assert format_confidence(None) == "N/A"


APP_PATH = Path(__file__).resolve().parents[1] / "src" / "clindoc" / "ui" / "app.py"


def test_streamlit_app_initial_state() -> None:
    """Test Streamlit application initial render without uploaded file."""
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()

    assert not at.exception, f"App raised exception: {at.exception}"
    # Header should be present
    assert any("Clindoc2FHIR Reviewer" in str(m.value) for m in at.markdown)
    # Welcome banner should be shown
    assert any("Welcome!" in str(i.value) for i in at.info)


def test_streamlit_app_run_demo_lab_report() -> None:
    """Test full execution of Reviewer UI when clicking 'Load Demo Lab Report'."""
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception

    # Find the 'Load Demo Lab Report' button
    demo_btn = next((b for b in at.button if "Load Demo Lab Report" in b.label), None)
    assert demo_btn is not None, "Demo button not found in sidebar"

    # Click button and re-run
    demo_btn.click().run()
    assert not at.exception

    # Metric cards should be populated
    metric_labels = [m.label for m in at.metric]
    assert "Total Claims" in metric_labels
    assert "Supported" in metric_labels
    assert "Discrepancies" in metric_labels
    assert "Insufficient Evidence" in metric_labels
    assert "Escalated to Stage C" in metric_labels

    # Download buttons should be present
    download_labels = [db.label for db in at.download_button]
    assert any("Download FHIR R4B Bundle" in l for l in download_labels)
    assert any("Download Machine-Readable Audit Report" in l for l in download_labels)
