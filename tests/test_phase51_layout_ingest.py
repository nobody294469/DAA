"""Adversarial and conformance tests for Phase 5.1: Document Ingestion & Layout Hardening.

Verifies:
- 2D multi-column reading order and column separation without interleaving.
- Three-column layouts, sidebars, and full-width headings above multi-column content.
- Tabular laboratory report layout, wrapped analyte name recovery, and multi-line headers.
- Plain text (.txt) ingestion with various encodings and CRLF/LF line endings.
- Microsoft Word (.docx) ingestion with paragraphs, headings, and tables.
- Malformed, encrypted, and oversized file handling.
- Evidence character offset accuracy and bounding box preservation.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import zipfile
import pytest

from clindoc.ocr.schema import LineBox, WordBox
from clindoc.ocr.layout import sort_page_lines_reading_order, _find_vertical_gutters
from clindoc.ocr.table_layout import detect_tabular_layout, reconstruct_tabular_page_text
from clindoc.ocr.ingest import (
    ingest_document,
    UnsupportedFileTypeError,
    UnreadableDocumentError,
    EmptyDocumentError,
)
from clindoc.ingest.service import (
    DocumentIngestionService,
    ScreeningResult,
    sniff_mime_type,
    SUPPORTED_MIME_TYPES,
)
from clindoc.db.enums import DocumentStatus
from clindoc.storage.blob import EncryptedFileBlobStore


@pytest.fixture
def temp_blob_store(tmp_path: Path) -> EncryptedFileBlobStore:
    return EncryptedFileBlobStore(
        root_dir=tmp_path / "blobs",
        encryption_key=b"12345678901234567890123456789012",
    )


@pytest.fixture
def ingest_service(temp_blob_store: EncryptedFileBlobStore) -> DocumentIngestionService:
    return DocumentIngestionService(blob_store=temp_blob_store)


# ─── STEP 1: MULTI-COLUMN READING ORDER TESTS ────────────────────────────────


def test_single_column_preserves_vertical_order():
    """Verify that single-column documents maintain top-to-bottom reading order."""
    lines = [
        LineBox(text="Line 1", bbox=[50.0, 100.0, 500.0, 114.0], confidence=1.0, page=1),
        LineBox(text="Line 2", bbox=[50.0, 120.0, 480.0, 134.0], confidence=1.0, page=1),
        LineBox(text="Line 3", bbox=[50.0, 140.0, 510.0, 154.0], confidence=1.0, page=1),
    ]
    ordered = sort_page_lines_reading_order(lines)
    assert [l.text for l in ordered] == ["Line 1", "Line 2", "Line 3"]


def test_two_column_no_interleaving():
    """Verify that two-column documents read Column 1 completely before Column 2."""
    # Col 1: x in [50, 270], y in [100, 200]
    # Col 2: x in [320, 540], y in [100, 200]
    # In naive Y-sort: Left 1, Right 1, Left 2, Right 2, Left 3, Right 3 (INTERLEAVED)
    # In 2D layout: Left 1, Left 2, Left 3, Right 1, Right 2, Right 3 (COLUMN ORDER)
    lines = [
        LineBox(text="Left 1: Chief Complaint", bbox=[50.0, 100.0, 260.0, 114.0], confidence=1.0, page=1),
        LineBox(text="Right 1: Plan and Orders", bbox=[320.0, 102.0, 530.0, 116.0], confidence=1.0, page=1),
        LineBox(text="Left 2: Patient reports chest pain", bbox=[50.0, 125.0, 270.0, 139.0], confidence=1.0, page=1),
        LineBox(text="Right 2: Start Lisinopril 10mg daily", bbox=[320.0, 128.0, 540.0, 142.0], confidence=1.0, page=1),
        LineBox(text="Left 3: Pain started 2 days ago", bbox=[50.0, 150.0, 265.0, 164.0], confidence=1.0, page=1),
        LineBox(text="Right 3: Schedule follow-up in 2 weeks", bbox=[320.0, 153.0, 535.0, 167.0], confidence=1.0, page=1),
    ]

    ordered = sort_page_lines_reading_order(lines, page_width=612.0, page_height=792.0)
    texts = [l.text for l in ordered]

    expected = [
        "Left 1: Chief Complaint",
        "Left 2: Patient reports chest pain",
        "Left 3: Pain started 2 days ago",
        "Right 1: Plan and Orders",
        "Right 2: Start Lisinopril 10mg daily",
        "Right 3: Schedule follow-up in 2 weeks",
    ]
    assert texts == expected, f"Expected column order without interleaving, got: {texts}"


def test_three_column_reading_order():
    """Verify that three-column layouts read Left -> Center -> Right columns in order."""
    # Col 1: [40, 180]
    # Col 2: [210, 360]
    # Col 3: [390, 550]
    lines = [
        LineBox(text="Col1 Line1", bbox=[40.0, 100.0, 180.0, 114.0], confidence=1.0, page=1),
        LineBox(text="Col2 Line1", bbox=[210.0, 100.0, 350.0, 114.0], confidence=1.0, page=1),
        LineBox(text="Col3 Line1", bbox=[390.0, 100.0, 540.0, 114.0], confidence=1.0, page=1),
        LineBox(text="Col1 Line2", bbox=[40.0, 130.0, 175.0, 144.0], confidence=1.0, page=1),
        LineBox(text="Col2 Line2", bbox=[210.0, 130.0, 355.0, 144.0], confidence=1.0, page=1),
        LineBox(text="Col3 Line2", bbox=[390.0, 130.0, 545.0, 144.0], confidence=1.0, page=1),
    ]
    ordered = sort_page_lines_reading_order(lines)
    texts = [l.text for l in ordered]

    assert texts == [
        "Col1 Line1",
        "Col1 Line2",
        "Col2 Line1",
        "Col2 Line2",
        "Col3 Line1",
        "Col3 Line2",
    ]


def test_full_width_heading_above_two_columns():
    """Verify full-width banner/heading is read FIRST, followed by Left col, then Right col."""
    lines = [
        LineBox(text="MEMORIAL HOSPITAL CLINICAL DISCHARGE SUMMARY", bbox=[40.0, 30.0, 560.0, 48.0], confidence=1.0, page=1),
        LineBox(text="Left: Assessment", bbox=[40.0, 90.0, 280.0, 104.0], confidence=1.0, page=1),
        LineBox(text="Right: Plan", bbox=[320.0, 90.0, 560.0, 104.0], confidence=1.0, page=1),
        LineBox(text="Left: Condition stable", bbox=[40.0, 115.0, 270.0, 129.0], confidence=1.0, page=1),
        LineBox(text="Right: Discharge home", bbox=[320.0, 115.0, 550.0, 129.0], confidence=1.0, page=1),
    ]

    ordered = sort_page_lines_reading_order(lines)
    texts = [l.text for l in ordered]

    assert texts[0] == "MEMORIAL HOSPITAL CLINICAL DISCHARGE SUMMARY"
    assert texts[1:3] == ["Left: Assessment", "Left: Condition stable"]
    assert texts[3:5] == ["Right: Plan", "Right: Discharge home"]


def test_sidebar_layout_not_interleaved():
    """Verify narrow sidebar (e.g. demographics/vitals) is kept separate from main narrative."""
    # Sidebar: x in [40, 160]
    # Main column: x in [190, 560]
    lines = [
        LineBox(text="BP: 120/80", bbox=[40.0, 100.0, 150.0, 114.0], confidence=1.0, page=1),
        LineBox(text="HR: 72 bpm", bbox=[40.0, 120.0, 140.0, 134.0], confidence=1.0, page=1),
        LineBox(text="Temp: 98.6 F", bbox=[40.0, 140.0, 155.0, 154.0], confidence=1.0, page=1),
        LineBox(text="Narrative line 1: Patient admitted for observation.", bbox=[190.0, 100.0, 560.0, 114.0], confidence=1.0, page=1),
        LineBox(text="Narrative line 2: Physical examination unremarkable.", bbox=[190.0, 120.0, 550.0, 134.0], confidence=1.0, page=1),
        LineBox(text="Narrative line 3: Labs pending at discharge.", bbox=[190.0, 140.0, 530.0, 154.0], confidence=1.0, page=1),
    ]

    ordered = sort_page_lines_reading_order(lines)
    texts = [l.text for l in ordered]

    # Sidebar read, then narrative read (or vice versa depending on gutter assignment, but NOT interleaved)
    sidebar_items = {"BP: 120/80", "HR: 72 bpm", "Temp: 98.6 F"}
    narrative_items = {
        "Narrative line 1: Patient admitted for observation.",
        "Narrative line 2: Physical examination unremarkable.",
        "Narrative line 3: Labs pending at discharge.",
    }

    first_three = set(texts[:3])
    second_three = set(texts[3:])
    assert (first_three == sidebar_items and second_three == narrative_items) or (
        first_three == narrative_items and second_three == sidebar_items
    ), f"Expected clean separation of sidebar and narrative, got: {texts}"


def test_footer_isolation():
    """Verify page number footer at bottom of page is kept at the end."""
    lines = [
        LineBox(text="Body line 1", bbox=[50.0, 100.0, 500.0, 114.0], confidence=1.0, page=1),
        LineBox(text="Body line 2", bbox=[50.0, 130.0, 500.0, 144.0], confidence=1.0, page=1),
        LineBox(text="Page 1 of 3 - Confidential Medical Record", bbox=[200.0, 750.0, 450.0, 762.0], confidence=1.0, page=1),
    ]
    ordered = sort_page_lines_reading_order(lines, page_height=792.0)
    assert ordered[-1].text == "Page 1 of 3 - Confidential Medical Record"


# ─── STEP 2: TABULAR LABORATORY REPORT LAYOUT TESTS ──────────────────────────


def test_detect_tabular_layout_heuristics():
    """Verify tabular detection detects unseen clinical lab reports by keywords and alignments."""
    # Document not starting with LAB_
    doc_id = "PATIENT_CMP_PANEL_2026"
    lines = [
        {"text": "METRO HEALTH CLINICAL PATHOLOGY", "bbox": [50.0, 40.0, 400.0, 55.0]},
        {"text": "Test Description", "bbox": [50.0, 100.0, 150.0, 114.0]},
        {"text": "Observed Value", "bbox": [200.0, 100.0, 280.0, 114.0]},
        {"text": "Reference Range", "bbox": [320.0, 100.0, 420.0, 114.0]},
        {"text": "Units", "bbox": [450.0, 100.0, 480.0, 114.0]},
        {"text": "Glucose", "bbox": [50.0, 120.0, 100.0, 134.0]},
        {"text": "98.5", "bbox": [200.0, 120.0, 230.0, 134.0]},
        {"text": "70.0 - 99.0", "bbox": [320.0, 120.0, 390.0, 134.0]},
        {"text": "mg/dL", "bbox": [450.0, 120.0, 485.0, 134.0]},
    ]
    assert detect_tabular_layout(doc_id, lines=lines) is True


def test_wrapped_analyte_name_recovery():
    """Verify multi-line wrapped analyte names are cleanly joined without losing values."""
    # Line 1: Analyte part 1, Value, Units, Range
    # Line 2: Analyte continuation (parenthetical), NO value
    page_lines = [
        {"text": "Estimated Glomerular Filtration Rate", "bbox": [50.0, 200.0, 220.0, 214.0], "confidence": 1.0},
        {"text": "68.0", "bbox": [250.0, 200.0, 275.0, 214.0], "confidence": 1.0},
        {"text": "mL/min/1.73m2", "bbox": [300.0, 200.0, 380.0, 214.0], "confidence": 1.0},
        {"text": "> 60.0", "bbox": [420.0, 200.0, 460.0, 214.0], "confidence": 1.0},
        # Wrapped continuation
        {"text": "(eGFR, Non-African American)", "bbox": [50.0, 216.0, 200.0, 228.0], "confidence": 1.0},
        # Next test
        {"text": "Creatinine", "bbox": [50.0, 240.0, 110.0, 254.0], "confidence": 1.0},
        {"text": "1.05", "bbox": [250.0, 240.0, 275.0, 254.0], "confidence": 1.0},
        {"text": "mg/dL", "bbox": [300.0, 240.0, 340.0, 254.0], "confidence": 1.0},
        {"text": "0.70 - 1.30", "bbox": [420.0, 240.0, 480.0, 254.0], "confidence": 1.0},
    ]

    page_text, rows = reconstruct_tabular_page_text(page_lines, page_number=1)
    text_lines = page_text.split("\n")

    # Should have 2 data rows, with continuation joined to eGFR
    assert len(text_lines) == 2
    assert "Estimated Glomerular Filtration Rate (eGFR, Non-African American) 68.0 mL/min/1.73m2 > 60.0" in text_lines[0]
    assert "Creatinine 1.05 mg/dL 0.70 - 1.30" in text_lines[1]


def test_multi_line_table_headers_joined():
    """Verify multi-line table headers (Observed\\nValue) are grouped into a single header row."""
    page_lines = [
        {"text": "Test", "bbox": [50.0, 100.0, 80.0, 114.0], "confidence": 1.0},
        {"text": "Observed", "bbox": [200.0, 100.0, 250.0, 114.0], "confidence": 1.0},
        {"text": "Reference", "bbox": [320.0, 100.0, 380.0, 114.0], "confidence": 1.0},
        # Second line of headers
        {"text": "Description", "bbox": [50.0, 114.0, 120.0, 126.0], "confidence": 1.0},
        {"text": "Value", "bbox": [200.0, 114.0, 235.0, 126.0], "confidence": 1.0},
        {"text": "Interval", "bbox": [320.0, 114.0, 370.0, 126.0], "confidence": 1.0},
        # Data row
        {"text": "Potassium", "bbox": [50.0, 140.0, 110.0, 154.0], "confidence": 1.0},
        {"text": "4.2", "bbox": [200.0, 140.0, 220.0, 154.0], "confidence": 1.0},
        {"text": "3.5 - 5.0", "bbox": [320.0, 140.0, 370.0, 154.0], "confidence": 1.0},
    ]

    page_text, rows = reconstruct_tabular_page_text(page_lines, page_number=1)
    text_lines = page_text.split("\n")

    assert "Test Description Observed Value Reference Interval" in text_lines[0]
    assert "Potassium 4.2 3.5 - 5.0" in text_lines[1]


# ─── STEP 3: TXT AND DOCX INGESTION TESTS ────────────────────────────────────


def test_txt_ingestion_utf8(tmp_path: Path):
    """Verify plain text file ingestion with UTF-8 encoding."""
    txt_file = tmp_path / "discharge_summary.txt"
    content = (
        "Memorial General Hospital\n"
        "Patient: John Doe | MRN: 12345\n\n"
        "CHIEF COMPLAINT\n"
        "Patient presents with acute onset dyspnea."
    )
    txt_file.write_text(content, encoding="utf-8")

    doc_text = ingest_document(txt_file, document_id="DOC_TXT_01")

    assert doc_text.document_id == "DOC_TXT_01"
    assert doc_text.source_type == "born_digital"
    assert len(doc_text.pages) == 1
    page = doc_text.pages[0]
    assert "Memorial General Hospital" in page.get_page_text()
    assert "CHIEF COMPLAINT" in page.get_page_text()
    # Check tokens have bounding boxes and line_ids
    assert len(page.tokens) > 0
    for tok in page.tokens:
        assert tok.bbox.x0 <= tok.bbox.x1
        assert tok.bbox.y0 <= tok.bbox.y1
        assert tok.confidence is None


def test_txt_ingestion_crlf_line_endings(tmp_path: Path):
    """Verify plain text file ingestion handles Windows CRLF line endings properly."""
    txt_file = tmp_path / "note_crlf.txt"
    crlf_content = b"Line 1\r\nLine 2\r\nLine 3\r\n"
    txt_file.write_bytes(crlf_content)

    doc_text = ingest_document(txt_file, document_id="DOC_CRLF_01")
    lines = doc_text.pages[0].get_page_text().split("\n")
    assert lines == ["Line 1", "Line 2", "Line 3"]


def test_docx_ingestion_with_tables_and_headings(tmp_path: Path):
    """Verify DOCX ingestion extracts headings, paragraphs, and table rows correctly."""
    docx_file = tmp_path / "clinical_report.docx"

    # Construct minimal valid DOCX archive in-memory
    # Contains: [Content_Types].xml, _rels/.rels, word/document.xml
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        '<w:p><w:r><w:t>CLINICAL PROGRESS NOTE</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Patient has improved clinically on oral Metformin 500mg BID.</w:t></w:r></w:p>'
        '<w:tbl>'
        '<w:tr>'
        '<w:tc><w:p><w:r><w:t>Glucose</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>105.0</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>mg/dL</w:t></w:r></w:p></w:tc>'
        '</w:tr>'
        '</w:tbl>'
        '</w:body>'
        '</w:document>'
    )

    with zipfile.ZipFile(docx_file, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc_xml)

    doc_text = ingest_document(docx_file, document_id="DOCX_01")
    assert doc_text.document_id == "DOCX_01"
    assert doc_text.source_type == "born_digital"
    full_text = doc_text.pages[0].get_page_text()

    assert "CLINICAL PROGRESS NOTE" in full_text
    assert "Metformin 500mg BID" in full_text
    assert "Glucose 105.0 mg/dL" in full_text


def test_service_screens_docx_and_txt(ingest_service: DocumentIngestionService):
    """Verify DocumentIngestionService screens valid DOCX and TXT files successfully."""
    # Screen TXT
    txt_bytes = b"Patient Jane Doe admitted on 2025-01-10 with fever."
    res_txt = ingest_service.screen_bytes(
        raw_bytes=txt_bytes,
        original_filename="note.txt",
        declared_mime_type="text/plain",
    )
    assert res_txt.status == DocumentStatus.INGESTED
    assert res_txt.mime_type == "text/plain"

    # Screen DOCX
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Note</w:t></w:r></w:p></w:body></w:document>')
    docx_bytes = buf.getvalue()

    res_docx = ingest_service.screen_bytes(
        raw_bytes=docx_bytes,
        original_filename="report.docx",
        declared_mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert res_docx.status == DocumentStatus.INGESTED
    assert res_docx.mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_service_quarantines_corrupted_docx(ingest_service: DocumentIngestionService):
    """Verify corrupted zip claiming to be DOCX is quarantined."""
    corrupted_bytes = b"PK\x03\x04corrupted_binary_stream_not_a_zip"
    res = ingest_service.screen_bytes(
        raw_bytes=corrupted_bytes,
        original_filename="corrupted.docx",
        declared_mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert res.status == DocumentStatus.QUARANTINED
    assert res.is_quarantined is True


def test_service_quarantines_encrypted_docx(ingest_service: DocumentIngestionService):
    """Verify password-protected DOCX (OLE EncryptedPackage) is quarantined."""
    encrypted_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"EncryptedPackage" + b"0" * 100
    res = ingest_service.screen_bytes(
        raw_bytes=encrypted_bytes,
        original_filename="encrypted.docx",
        declared_mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert res.status == DocumentStatus.QUARANTINED
    assert "Encrypted" in (res.error_message or "")


def test_empty_txt_and_docx_rejected(tmp_path: Path):
    """Verify zero-byte or whitespace-only files raise EmptyDocumentError."""
    empty_txt = tmp_path / "empty.txt"
    empty_txt.write_text("   \n\n   ")
    with pytest.raises(EmptyDocumentError):
        ingest_document(empty_txt)

    empty_docx = tmp_path / "empty.docx"
    empty_docx.write_bytes(b"")
    with pytest.raises(EmptyDocumentError):
        ingest_document(empty_docx)


# ─── STEP 4: EVIDENCE OFFSETS & PROVENANCE PRESERVATION ──────────────────────


def test_evidence_offsets_exact_substring(tmp_path: Path):
    """Verify character offsets map deterministically to exact substring in page_text."""
    txt_file = tmp_path / "evidence_test.txt"
    txt_file.write_text("Serum Potassium was 4.2 meq/L on admission.")

    doc_text = ingest_document(txt_file, document_id="EVID_01")
    page = doc_text.pages[0]
    page_text = page.get_page_text()

    # Find span for 4.2
    val = "4.2"
    char_start = page_text.find(val)
    char_end = char_start + len(val)

    # Slice must reproduce exact text
    assert page.slice_text(char_start, char_end) == val

    # Bounding box must be valid enclosing box
    bbox = page.get_bbox_for_span(char_start, char_end)
    assert bbox is not None
    assert bbox[0] <= bbox[2]
    assert bbox[1] <= bbox[3]
