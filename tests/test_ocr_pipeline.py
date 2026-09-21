"""Tests for OCR module, schema validation, and document processing."""

import json
from pathlib import Path
import tempfile
import pytest

from clindoc.ocr.born_digital import extract_born_digital_pdf
from clindoc.ocr.pipeline import process_document
from clindoc.ocr.schema import DocumentOCRRecord, LineBox, WordBox


def test_born_digital_extraction_single_doc():
    """Verify that PyMuPDF born-digital extraction produces valid DocumentOCRRecord."""
    pdf_path = Path("data/rendered/LAB_0001.pdf")
    assert pdf_path.exists(), "Sample PDF LAB_0001.pdf must exist"

    record = extract_born_digital_pdf(pdf_path)

    assert record.doc_id == "LAB_0001"
    assert record.engine == "pymupdf_digital"
    assert record.device == "cpu"
    assert record.num_pages >= 1
    assert len(record.lines) > 0
    assert len(record.words) > 0

    for line in record.lines:
        assert line.text.strip()
        assert len(line.bbox) == 4
        x0, y0, x1, y1 = line.bbox
        assert x0 <= x1
        assert y0 <= y1
        assert 0.0 <= line.confidence <= 1.0
        assert line.page >= 1
        assert len(line.words) > 0

    for word in record.words:
        assert word.text.strip()
        assert len(word.bbox) == 4
        assert 0.0 <= word.confidence <= 1.0
        assert word.page >= 1


def test_ocr_caching_and_roundtrip():
    """Verify that document processing caches to JSON and correctly reloads from cache."""
    pdf_path = Path("data/rendered/LAB_0001.pdf")

    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_dir = Path(tmp_dir)

        # 1. First run generates cache
        rec1 = process_document(pdf_path, output_dir=cache_dir, engine="pymupdf_digital")
        cached_json = cache_dir / "LAB_0001.json"
        assert cached_json.exists()

        # 2. Second run reads from cache
        rec2 = process_document(pdf_path, output_dir=cache_dir, engine="pymupdf_digital", force=False)

        assert rec1.doc_id == rec2.doc_id
        assert len(rec1.lines) == len(rec2.lines)
        assert len(rec1.words) == len(rec2.words)
        assert rec1.lines[0].text == rec2.lines[0].text
        assert rec1.lines[0].bbox == rec2.lines[0].bbox


def test_schema_constraints():
    """Verify that Pydantic enforces confidence bounds and positive page numbers."""
    # Valid
    w = WordBox(text="Glucose", bbox=[10.0, 20.0, 50.0, 30.0], confidence=0.95, page=1)
    assert w.text == "Glucose"

    # Invalid confidence > 1.0
    with pytest.raises(Exception):
        WordBox(text="Glucose", bbox=[10.0, 20.0, 50.0, 30.0], confidence=1.5, page=1)

    # Invalid page < 1
    with pytest.raises(Exception):
        WordBox(text="Glucose", bbox=[10.0, 20.0, 50.0, 30.0], confidence=0.95, page=0)


def test_paddleocr_output_schema_if_present():
    """Verify cached PaddleOCR JSON conforms to DocumentOCRRecord schema."""
    cached_path = Path("data/ocr_cache/LAB_0001.json")
    if cached_path.exists():
        with open(cached_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        record = DocumentOCRRecord.model_validate(data)
        assert record.doc_id == "LAB_0001"
        assert record.engine in ("paddleocr", "pymupdf_digital")
        assert len(record.lines) > 0
        assert len(record.words) > 0
        for line in record.lines:
            assert line.text
            assert len(line.bbox) == 4
            assert 0.0 <= line.confidence <= 1.0
