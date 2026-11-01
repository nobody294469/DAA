"""Evaluation-only adapter for Glaucoma_Med_Dataset text snippets.

Authority: Evaluation Audit / DEC-019
Location: external_eval/adapters/glaucoma_text_adapter.py
Rules:
- Evaluation-only adapter; zero production code modification.
- Wraps unsegmented plain clinical text snippets into canonical DocumentText / DocumentPage containers.
- Does NOT fabricate misleading spatial coordinates or claim to validate OCR/layout extraction.
"""
from __future__ import annotations

from typing import Optional
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict


def snippet_to_document_text(snippet_id: str, text: str) -> DocumentText:
    """Wrap an unsegmented text snippet into a 1-page DocumentText container.

    Note: Bounding boxes are set to degenerate default boxes (0, 0, 0, 0)
    because this dataset consists of plain text notes, not digitized page images.
    This adapter must NEVER be used to claim spatial OCR or layout evaluation.
    """
    words = text.split()
    tokens = [
        Token(
            text=w,
            bbox=BBoxDict(x0=0.0, y0=0.0, x1=0.0, y1=0.0),
            confidence=None,  # Born-digital plain text
            line_id="line_0",
        )
        for w in words
    ]
    page = DocumentPage(
        page_number=1,
        width_px=1000,
        height_px=1000,
        tokens=tokens,
    )
    return DocumentText(
        document_id=snippet_id,
        source_type="born_digital",
        pages=[page],
    )
