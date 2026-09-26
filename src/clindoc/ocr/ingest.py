"""Canonical Document Ingestion and DocumentText construction.

Conforms to PROJECT_SPEC §7.1 and schemas/document_text.json.
Converts source clinical documents (born-digital PDFs, scanned/degraded PDFs, and images)
into the authoritative DocumentText representation consumed by all downstream stages.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional, Sequence
import pymupdf

from clindoc.ocr.geometry import BBox, BBoxDict
from clindoc.ocr.document_text import (
    DocumentText, DocumentPage, Token, DegradationMetadata,
)
from clindoc.ocr.born_digital import extract_born_digital_pdf
from clindoc.ocr.schema import DocumentOCRRecord
from clindoc.m2.context import reconstruct_canonical_page_text

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"}


# ─── STRUCTURED INGESTION EXCEPTIONS ──────────────────────────────────────────

class IngestionError(Exception):
    """Base exception for all document ingestion failures."""
    pass


class UnreadableDocumentError(IngestionError):
    """Raised when source file is corrupted, unreadable, or missing."""
    pass


class UnsupportedFileTypeError(IngestionError):
    """Raised when file extension is not among supported document types."""
    pass


class EmptyDocumentError(IngestionError):
    """Raised when a document contains zero extractable content and no pages."""
    pass


class OCREngineError(IngestionError):
    """Raised when OCR execution fails or yields a malformed result."""
    pass


# ─── CORE INGESTION FUNCTIONS ────────────────────────────────────────────────

def _detect_tabular(doc_id: str, is_tabular: bool | None = None) -> bool:
    """Determine whether document has tabular layout (lab report)."""
    if is_tabular is not None:
        return is_tabular
    doc_id_upper = doc_id.upper()
    return doc_id_upper.startswith("LAB_") or "TEMPLATE_A" in doc_id_upper or "TEMPLATE_B" in doc_id_upper or "TEMPLATE_C" in doc_id_upper


def ingest_from_record(
    record: DocumentOCRRecord | dict[str, Any],
    doc_id: str | None = None,
    is_tabular: bool | None = None,
    degradation_metadata: dict[str, Any] | DegradationMetadata | None = None,
) -> DocumentText:
    """Construct canonical DocumentText from a DocumentOCRRecord or cache dict."""
    if isinstance(record, dict):
        record_data = record
    else:
        record_data = record.model_dump()

    resolved_doc_id = doc_id or record_data.get("doc_id", "unknown_doc")
    engine = record_data.get("engine", "pymupdf_digital")
    source_type: Literal["born_digital", "scanned_image"] = (
        "born_digital" if "digital" in engine else "scanned_image"
    )

    tabular = _detect_tabular(resolved_doc_id, is_tabular)

    lines = record_data.get("lines", [])
    num_pages = record_data.get("num_pages", 1)

    # Group lines by page
    lines_by_page: dict[int, list[dict[str, Any]]] = {}
    for l in lines:
        p_num = l.get("page", 1)
        if p_num not in lines_by_page:
            lines_by_page[p_num] = []
        lines_by_page[p_num].append(l)

    effective_num_pages = max(num_pages, max(lines_by_page.keys()) if lines_by_page else 1)
    pages: list[DocumentPage] = []

    for p_num in range(1, effective_num_pages + 1):
        p_lines = lines_by_page.get(p_num, [])

        # If page has no lines, create an empty DocumentPage
        if not p_lines:
            pages.append(DocumentPage(
                page_number=p_num,
                width_px=612,
                height_px=792,
                tokens=[],
            ))
            continue

        # Reconstruct canonical page text and row records using established M2 context logic
        page_text, row_records = reconstruct_canonical_page_text(
            p_lines, is_tabular=tabular, page_number=p_num,
        )

        tokens: list[Token] = []
        for r_idx, row in enumerate(row_records):
            line_id = f"line_{r_idx}"
            row_text = row.get("text", "")
            words = row_text.split()
            if not words:
                continue

            r_bbox = row.get("bbox", [0.0, 0.0, 612.0, 792.0])
            if len(r_bbox) < 4:
                r_bbox = [0.0, 0.0, 612.0, 792.0]

            x0, y0, x1, y1 = r_bbox[0], r_bbox[1], r_bbox[2], r_bbox[3]
            # Ensure valid geometry invariant x0 <= x1, y0 <= y1
            if x0 > x1:
                x0, x1 = x1, x0
            if y0 > y1:
                y0, y1 = y1, y0

            raw_conf = row.get("confidence", 1.0)
            token_conf = None if source_type == "born_digital" else (
                round(float(raw_conf), 4) if raw_conf is not None else None
            )

            # Check if constituent words are provided in p_lines for precise token geometry
            word_boxes_for_row: list[dict[str, Any]] = []
            for pl in p_lines:
                if pl.get("text", "").strip() in row_text:
                    for wb in pl.get("words", []):
                        word_boxes_for_row.append(wb)

            if len(word_boxes_for_row) == len(words):
                # Precise word-level bounding boxes available
                for w_idx, wb in enumerate(word_boxes_for_row):
                    w_bbox = wb.get("bbox", [x0, y0, x1, y1])
                    wx0, wy0, wx1, wy1 = w_bbox[0], w_bbox[1], w_bbox[2], w_bbox[3]
                    if wx0 > wx1:
                        wx0, wx1 = wx1, wx0
                    if wy0 > wy1:
                        wy0, wy1 = wy1, wy0
                    w_conf = None if source_type == "born_digital" else wb.get("confidence", token_conf)
                    tokens.append(Token(
                        text=words[w_idx],
                        bbox=BBoxDict(x0=round(wx0, 2), y0=round(wy0, 2), x1=round(wx1, 2), y1=round(wy1, 2)),
                        confidence=w_conf,
                        line_id=line_id,
                    ))
            else:
                # Distribute words evenly across the line bounding box
                w_w = (x1 - x0) / max(len(words), 1)
                for w_idx, w_text in enumerate(words):
                    wx0 = round(x0 + w_idx * w_w, 2)
                    wx1 = round(wx0 + w_w, 2)
                    tokens.append(Token(
                        text=w_text,
                        bbox=BBoxDict(x0=wx0, y0=y0, x1=wx1, y1=y1),
                        confidence=token_conf,
                        line_id=line_id,
                    ))

        pages.append(DocumentPage(
            page_number=p_num,
            width_px=612,
            height_px=792,
            tokens=tokens,
        ))

    deg_meta = None
    if degradation_metadata is not None:
        if isinstance(degradation_metadata, DegradationMetadata):
            deg_meta = degradation_metadata
        elif isinstance(degradation_metadata, dict):
            deg_meta = DegradationMetadata.model_validate(degradation_metadata)

    doc_text = DocumentText(
        document_id=resolved_doc_id,
        source_type=source_type,
        pages=pages,
        degradation_metadata=deg_meta,
    )
    doc_text.validate_schema()
    return doc_text


def _find_cached_record(doc_id: str, custom_dir: Path | None = None) -> Path | None:
    """Locate existing OCR cache file for doc_id across standard cache directories."""
    candidates = []
    if custom_dir:
        candidates.append(custom_dir / f"{doc_id}.json")
    # Standard repository cache directories
    candidates.extend([
        Path(f"data/ocr_cache/{doc_id}.json"),
        Path(f"data/med_ocr_cache/{doc_id}.json"),
        Path(f"data/narrative_ocr_cache/{doc_id}.json"),
    ])
    for c in candidates:
        if c.exists():
            return c
    return None


def ingest_document(
    path: str | Path,
    document_id: str | None = None,
    source_type: Literal["auto", "born_digital", "scanned_image"] = "auto",
    is_tabular: bool | None = None,
    ocr_cache_dir: str | Path | None = None,
    force: bool = False,
    dpi: int = 150,
) -> DocumentText:
    """Canonical Ingestion API: Convert clinical PDF or image into DocumentText.

    Parameters:
      path: Path to the input PDF or image document.
      document_id: Optional document identifier (defaults to file stem, e.g. 'LAB_0001').
      source_type: Ingestion modality ('auto', 'born_digital', 'scanned_image').
      is_tabular: Optional override for tabular row grouping (auto-detected if None).
      ocr_cache_dir: Optional custom OCR cache directory.
      force: If True, recomputes extraction ignoring existing cache.
      dpi: Rasterization DPI for scanned PDFs (default 150).

    Returns:
      Canonical DocumentText object conforming to schemas/document_text.json.

    Raises:
      FileNotFoundError: If input path does not exist.
      UnsupportedFileTypeError: If input file extension is not supported.
      UnreadableDocumentError: If PDF or image file is corrupted or unreadable.
      EmptyDocumentError: If document contains zero pages or no extractable content.
      OCREngineError: If OCR processing fails.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Document file does not exist: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{suffix}' for document {file_path.name}. "
            f"Supported extensions: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    doc_id = document_id or file_path.stem
    custom_cache_p = Path(ocr_cache_dir) if ocr_cache_dir else None

    # 1. Check existing OCR cache if not forcing
    if not force:
        cached_file = _find_cached_record(doc_id, custom_cache_p)
        if cached_file is not None:
            try:
                with open(cached_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return ingest_from_record(data, doc_id=doc_id, is_tabular=is_tabular)
            except Exception as e:
                # If cache is corrupted, proceed to re-extract
                pass

    # 2. Extract from PDF
    if suffix == ".pdf":
        try:
            pdf_doc = pymupdf.open(str(file_path))
        except Exception as e:
            raise UnreadableDocumentError(f"Failed to open PDF document {file_path}: {e}") from e

        num_pages = len(pdf_doc)
        if num_pages == 0:
            raise EmptyDocumentError(f"PDF document {file_path} contains 0 pages")

        # Determine modality if auto
        resolved_source_type = source_type
        if source_type == "auto":
            has_digital_text = any(bool(page.get_text().strip()) for page in pdf_doc)
            resolved_source_type = "born_digital" if has_digital_text else "scanned_image"

        if resolved_source_type == "born_digital":
            # Direct born-digital extraction via PyMuPDF
            try:
                record = extract_born_digital_pdf(file_path, doc_id=doc_id)
            except Exception as e:
                raise UnreadableDocumentError(f"Born-digital text extraction failed for {file_path}: {e}") from e

            # Verify that document is not completely blank in born_digital mode
            if not record.lines and not record.words:
                has_text = any(bool(p.get_text().strip()) for p in pdf_doc)
                if not has_text:
                    raise EmptyDocumentError(f"Born-digital PDF {file_path} has no extractable text")

            # Convert to DocumentText
            doc_text = ingest_from_record(record, doc_id=doc_id, is_tabular=is_tabular)

            # Update page dimensions from actual PDF pages
            for p_idx, p in enumerate(pdf_doc):
                if p_idx < len(doc_text.pages):
                    doc_text.pages[p_idx].width_px = int(round(p.rect.width))
                    doc_text.pages[p_idx].height_px = int(round(p.rect.height))

            return doc_text

        else:
            # Scanned / rasterized PDF: delegate to PaddleOCR pipeline
            try:
                from clindoc.ocr.pipeline import process_document
                out_dir = custom_cache_p or Path("data/ocr_cache")
                ocr_rec = process_document(
                    input_path=file_path,
                    output_dir=out_dir,
                    engine="paddleocr",
                    use_born_digital=False,
                    force=force,
                    dpi=dpi,
                )
                return ingest_from_record(ocr_rec, doc_id=doc_id, is_tabular=is_tabular)
            except Exception as e:
                raise OCREngineError(f"PaddleOCR execution failed on {file_path}: {e}") from e

    else:
        # Non-PDF image document (.png, .jpg, etc.): OCR processing
        try:
            from clindoc.ocr.pipeline import process_document
            out_dir = custom_cache_p or Path("data/ocr_cache")
            ocr_rec = process_document(
                input_path=file_path,
                output_dir=out_dir,
                engine="paddleocr",
                use_born_digital=False,
                force=force,
                dpi=dpi,
            )
            return ingest_from_record(ocr_rec, doc_id=doc_id, is_tabular=is_tabular)
        except Exception as e:
            raise OCREngineError(f"OCR execution failed on image {file_path}: {e}") from e


def batch_ingest(
    paths: Sequence[str | Path],
    source_type: Literal["auto", "born_digital", "scanned_image"] = "auto",
    is_tabular: bool | None = None,
    ocr_cache_dir: str | Path | None = None,
    force: bool = False,
) -> list[DocumentText]:
    """Ingest multiple documents in batch."""
    return [
        ingest_document(
            p,
            source_type=source_type,
            is_tabular=is_tabular,
            ocr_cache_dir=ocr_cache_dir,
            force=force,
        )
        for p in paths
    ]
