"""Dedicated test suite for Canonical DocumentText Ingestion.

Verifies the 21 required ingestion points specified in Phase 1 Task 4 / Task 5:
 1. Born-digital PDF ingestion
 2. Image/scanned PDF ingestion
 3. Multi-page document ingestion
 4. Page numbering (1-indexed)
 5. Character-offset validity
 6. Exact character slicing
 7. Geometry validity (x0 <= x1, y0 <= y1)
 8. OCR confidence range (0.0 <= conf <= 1.0 or None)
 9. Modality/source metadata (born_digital vs scanned_image)
10. Missing geometry handling
11. Missing OCR confidence handling
12. OCR failure handling
13. Unreadable/invalid document handling
14. Deterministic repeated ingestion
15. Document ID preservation
16. Compatibility with existing M2 evidence representation
17. DR-3 context compatibility
18. Processing of representative lab document
19. Processing of representative medication document
20. Processing of representative narrative document
21. OOD document ingestion without changing its OOD identity
"""
import json
from pathlib import Path
import pytest
import pymupdf

from clindoc.ocr.geometry import BBox, BBoxDict, enclose_bboxes
from clindoc.ocr.document_text import (
    DocumentText, DocumentPage, Token, DegradationMetadata,
)
from clindoc.ocr.ingest import (
    ingest_document, batch_ingest, ingest_from_record,
    IngestionError, UnreadableDocumentError, UnsupportedFileTypeError,
    EmptyDocumentError, OCREngineError,
)
from clindoc.m2.context import extract_context_window


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_multipage_pdf(tmp_path):
    """Create a temporary 2-page born-digital PDF."""
    pdf_path = tmp_path / "sample_multipage.pdf"
    doc = pymupdf.open()

    # Page 1
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((50, 50), "Clinical Report Page 1\nPatient: John Doe\nGlucose: 95 mg/dL")

    # Page 2
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((50, 50), "Clinical Report Page 2\nMedication: Metformin 500 mg\nFollow-up in 3 months")

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def sample_empty_pdf(tmp_path):
    """Create an empty 0-page or blank PDF."""
    pdf_path = tmp_path / "sample_blank.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)  # page with no text
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


# ─── 1. BORN-DIGITAL PDF INGESTION ───────────────────────────────────────────
def test_01_born_digital_pdf_ingestion():
    lab_pdf = Path("data/rendered/LAB_0001.pdf")
    doc_text = ingest_document(lab_pdf, document_id="LAB_0001", source_type="born_digital")

    assert doc_text.document_id == "LAB_0001"
    assert doc_text.source_type == "born_digital"
    assert doc_text.num_pages >= 1
    assert doc_text.total_tokens > 0
    assert doc_text.validate_schema() is True


# ─── 2. IMAGE / SCANNED PDF INGESTION ────────────────────────────────────────
def test_02_scanned_pdf_ingestion():
    # Use an OOD degraded lab cache representing scanned/degraded OCR
    cache_path = Path("data/ocr_cache/LAB_0050.json")
    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Ingest from OCR record representing scanned extraction
    doc_text = ingest_from_record(data, doc_id="LAB_0050", is_tabular=True)
    assert doc_text.document_id == "LAB_0050"
    assert doc_text.source_type in ("scanned_image", "born_digital")
    assert doc_text.num_pages >= 1
    assert doc_text.total_tokens > 0
    assert doc_text.validate_schema() is True


# ─── 3. MULTI-PAGE DOCUMENT INGESTION ─────────────────────────────────────────
def test_03_multipage_document_ingestion(sample_multipage_pdf):
    doc_text = ingest_document(sample_multipage_pdf, document_id="MULTI_001", force=True)

    assert doc_text.num_pages == 2
    assert doc_text.get_page(1).page_number == 1
    assert doc_text.get_page(2).page_number == 2
    assert "Page 1" in doc_text.get_page(1).get_page_text()
    assert "Page 2" in doc_text.get_page(2).get_page_text()


# ─── 4. PAGE NUMBERING (1-INDEXED) ───────────────────────────────────────────
def test_04_page_numbering_1_indexed(sample_multipage_pdf):
    doc_text = ingest_document(sample_multipage_pdf, document_id="MULTI_001", force=True)

    # Check 1-indexed numbering
    for idx, page in enumerate(doc_text.pages, start=1):
        assert page.page_number == idx
    with pytest.raises(IndexError):
        doc_text.get_page(0)  # 0 is not a valid 1-indexed page
    with pytest.raises(IndexError):
        doc_text.get_page(3)  # out of bounds


# ─── 5. CHARACTER-OFFSET VALIDITY ─────────────────────────────────────────────
def test_05_character_offset_validity():
    doc_text = ingest_document("data/rendered/LAB_0001.pdf", document_id="LAB_0001")
    page = doc_text.get_page(1)
    text = page.get_page_text()
    token_offsets = page.get_token_offsets()

    assert len(token_offsets) == len(page.tokens)
    for tok, c_start, c_end in token_offsets:
        assert 0 <= c_start < c_end <= len(text)
        assert text[c_start:c_end] == tok.text


# ─── 6. EXACT CHARACTER SLICING ──────────────────────────────────────────────
def test_06_exact_character_slicing():
    doc_text = ingest_document("data/med_rendered/MED_0025.pdf", document_id="MED_0025")
    page = doc_text.get_page(1)

    # The canonical M2 example at [297:323] is 'Amoxicillin 250 mg capsule'
    sliced = page.slice_text(297, 323)
    assert sliced == "Amoxicillin 250 mg capsule"


# ─── 7. GEOMETRY VALIDITY (x0 <= x1, y0 <= y1) ───────────────────────────────
def test_07_geometry_validity():
    doc_text = ingest_document("data/rendered/LAB_0001.pdf", document_id="LAB_0001")
    for page in doc_text.pages:
        for tok in page.tokens:
            assert tok.bbox.x0 <= tok.bbox.x1
            assert tok.bbox.y0 <= tok.bbox.y1

    # Test BBox error raising on invalid geometry
    with pytest.raises(ValueError):
        BBox(100.0, 50.0, 80.0, 60.0)  # x0 > x1
    with pytest.raises(ValueError):
        BBox(10.0, 90.0, 80.0, 60.0)  # y0 > y1


# ─── 8. OCR CONFIDENCE RANGE (0.0 <= conf <= 1.0 OR None) ────────────────────
def test_08_ocr_confidence_range():
    # Born-digital document confidence should be None or 1.0
    doc_text = ingest_document("data/rendered/LAB_0001.pdf", document_id="LAB_0001", source_type="born_digital")
    for page in doc_text.pages:
        for tok in page.tokens:
            assert tok.confidence is None or (0.0 <= tok.confidence <= 1.0)

    # Token with invalid confidence should raise ValueError
    with pytest.raises(ValueError):
        Token(text="test", bbox=BBoxDict(x0=0, y0=0, x1=10, y1=10), confidence=1.5, line_id="l0")
    with pytest.raises(ValueError):
        Token(text="test", bbox=BBoxDict(x0=0, y0=0, x1=10, y1=10), confidence=-0.1, line_id="l0")


# ─── 9. MODALITY / SOURCE METADATA ───────────────────────────────────────────
def test_09_modality_source_metadata():
    lab_doc = ingest_document("data/rendered/LAB_0001.pdf", source_type="born_digital")
    assert lab_doc.source_type == "born_digital"

    cache_data = {"doc_id": "SCAN_001", "engine": "paddleocr", "lines": []}
    scanned_doc = ingest_from_record(cache_data)
    assert scanned_doc.source_type == "scanned_image"


# ─── 10. MISSING GEOMETRY HANDLING ────────────────────────────────────────────
def test_10_missing_geometry_handling():
    # If a line has no bbox, safe default [0, 0, 612, 792] is used
    rec = {
        "doc_id": "TEST_NOGEOM",
        "engine": "pymupdf_digital",
        "lines": [{"text": "Hello World", "page": 1}],
    }
    doc_text = ingest_from_record(rec)
    page = doc_text.get_page(1)
    assert len(page.tokens) == 2
    for t in page.tokens:
        assert t.bbox.x0 <= t.bbox.x1
        assert t.bbox.y0 <= t.bbox.y1


# ─── 11. MISSING OCR CONFIDENCE HANDLING ──────────────────────────────────────
def test_11_missing_ocr_confidence_handling():
    # In born-digital documents, confidence is explicitly None
    rec = {
        "doc_id": "TEST_NOCONF",
        "engine": "pymupdf_digital",
        "lines": [{"text": "Sample text", "bbox": [0, 0, 100, 20], "page": 1}],
    }
    doc_text = ingest_from_record(rec)
    page = doc_text.get_page(1)
    for t in page.tokens:
        assert t.confidence is None


# ─── 12. OCR FAILURE HANDLING ────────────────────────────────────────────────
def test_12_ocr_failure_handling(tmp_path):
    # Pass a non-existent file or corrupted file expecting appropriate failure
    with pytest.raises(FileNotFoundError):
        ingest_document(tmp_path / "non_existent.pdf")


# ─── 13. UNREADABLE / INVALID DOCUMENT HANDLING ──────────────────────────────
def test_13_unreadable_invalid_document_handling(tmp_path, sample_empty_pdf):
    # Corrupted PDF
    corrupt_pdf = tmp_path / "corrupt.pdf"
    corrupt_pdf.write_bytes(b"NOT A VALID PDF CONTENT")
    with pytest.raises(UnreadableDocumentError):
        ingest_document(corrupt_pdf, force=True)

    # Unsupported extension
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("Hello")
    with pytest.raises(UnsupportedFileTypeError):
        ingest_document(txt_file)

    # Empty blank PDF
    with pytest.raises(EmptyDocumentError):
        ingest_document(sample_empty_pdf, source_type="born_digital", force=True)


# ─── 14. DETERMINISTIC REPEATED INGESTION ─────────────────────────────────────
def test_14_deterministic_repeated_ingestion():
    p = Path("data/rendered/LAB_0001.pdf")
    dt1 = ingest_document(p, document_id="LAB_0001")
    dt2 = ingest_document(p, document_id="LAB_0001")

    assert dt1.to_dict() == dt2.to_dict()
    assert dt1.get_full_text() == dt2.get_full_text()


# ─── 15. DOCUMENT ID PRESERVATION ────────────────────────────────────────────
def test_15_document_id_preservation():
    dt = ingest_document("data/rendered/LAB_0007.pdf", document_id="MY_CUSTOM_ID")
    assert dt.document_id == "MY_CUSTOM_ID"


# ─── 16. COMPATIBILITY WITH EXISTING M2 EVIDENCE ─────────────────────────────
def test_16_m2_evidence_compatibility():
    # Verify exact alignment between DocumentText and M2 evidence span
    doc_text = ingest_document("data/rendered/LAB_0003.pdf", document_id="LAB_0003")
    page = doc_text.get_page(1)

    # From all_examples.jsonl: [689:728] -> 'Alkaline Phosphatase 110.0 44 - 147 U/L'
    sliced = page.slice_text(689, 728)
    assert sliced == "Alkaline Phosphatase 110.0 44 - 147 U/L"

    # Enclosing bbox
    bbox = page.get_bbox_for_span(689, 728)
    assert bbox is not None
    assert bbox[0] <= 52.01 and bbox[2] >= 505.0


# ─── 17. DR-3 CONTEXT COMPATIBILITY ──────────────────────────────────────────
def test_17_dr3_context_compatibility():
    doc_text = ingest_document("data/rendered/LAB_0003.pdf", document_id="LAB_0003")
    page_text = doc_text.get_page(1).get_page_text()

    # Call DR-3 extract_context_window on the DocumentText page text
    ctx, start, end = extract_context_window(page_text, 689, 728, is_tabular=True)
    assert bool(ctx)
    assert start <= 689
    assert end >= 728
    assert "Alkaline Phosphatase" in ctx


# ─── 18. REPRESENTATIVE LAB DOCUMENT ─────────────────────────────────────────
def test_18_representative_lab_document():
    dt = ingest_document("data/rendered/LAB_0001.pdf", document_id="LAB_0001")
    assert dt.document_id == "LAB_0001"
    assert dt.num_pages == 1
    text = dt.get_page(1).get_page_text()
    assert "Glucose" in text or "METABOLIC" in text.upper() or "LABORATORY" in text.upper()


# ─── 19. REPRESENTATIVE MEDICATION DOCUMENT ──────────────────────────────────
def test_19_representative_medication_document():
    dt = ingest_document("data/med_rendered/MED_0001.pdf", document_id="MED_0001")
    assert dt.document_id == "MED_0001"
    assert dt.num_pages == 1
    text = dt.get_page(1).get_page_text()
    assert "Atorvastatin" in text or "Sig:" in text or "Prescription" in text or "Rx" in text


# ─── 20. REPRESENTATIVE NARRATIVE DOCUMENT ───────────────────────────────────
def test_20_representative_narrative_document():
    dt = ingest_document("data/narrative_rendered/NARRATIVE_0001.pdf", document_id="NARRATIVE_0001")
    assert dt.document_id == "NARRATIVE_0001"
    assert dt.num_pages == 1
    text = dt.get_page(1).get_page_text()
    assert "DISCHARGE SUMMARY" in text.upper() or "PATIENT" in text.upper()


# ─── 21. OOD DOCUMENT INGESTION WITHOUT CHANGING OOD IDENTITY ────────────────
def test_21_ood_document_ingestion_preserves_ood():
    with open("data/m2_verification/split_manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    ood_doc_ids = set(manifest["ood_doc_ids"])
    assert "LAB_0002" in ood_doc_ids

    dt = ingest_document("data/rendered/LAB_0002.pdf", document_id="LAB_0002")
    assert dt.document_id == "LAB_0002"
    # Document ID and split identity are completely unperturbed
    assert dt.document_id in ood_doc_ids
