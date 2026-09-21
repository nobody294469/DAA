"""Data models for OCR and text extraction caches."""

from typing import Any
from pydantic import BaseModel, Field


class WordBox(BaseModel):
    """Word-level text token with bounding box, confidence, and page."""

    text: str = Field(..., description="Extracted token text")
    bbox: list[float] = Field(..., description="Bounding box [x0, y0, x1, y1]")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Extraction or OCR confidence [0.0 - 1.0]")
    page: int = Field(..., ge=1, description="Page number (1-indexed)")


class LineBox(BaseModel):
    """Line-level text with bounding box, confidence, and constituent words."""

    text: str = Field(..., description="Extracted line text")
    bbox: list[float] = Field(..., description="Bounding box [x0, y0, x1, y1]")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Extraction or average OCR confidence [0.0 - 1.0]")
    page: int = Field(..., ge=1, description="Page number (1-indexed)")
    words: list[WordBox] = Field(default_factory=list, description="Word tokens within this line")


class DocumentOCRRecord(BaseModel):
    """Complete document-level OCR and text extraction cache."""

    doc_id: str = Field(..., description="Document identifier, e.g. LAB_0001")
    source_path: str = Field(..., description="Path to source PDF or image")
    engine: str = Field(..., description="Extraction engine: 'paddleocr' or 'pymupdf_digital'")
    device: str = Field(default="cpu", description="Execution device: 'cpu' or 'cuda'")
    num_pages: int = Field(..., ge=1, description="Number of pages")
    lines: list[LineBox] = Field(..., description="Ordered text lines with bounding boxes")
    words: list[WordBox] = Field(default_factory=list, description="All word tokens with bounding boxes")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata such as processing time")
