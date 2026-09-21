"""Provenance and source evidence models for extracted clinical fields."""

from pydantic import BaseModel, Field


class SourceEvidence(BaseModel):
    """Traceable provenance linking an extracted field to its source document location."""

    text: str = Field(..., description="Verbatim source text snippet or line")
    page: int = Field(..., ge=1, description="Source page number (1-indexed)")
    bbox: list[float] = Field(..., description="Enclosing bounding box [x0, y0, x1, y1]")
    ocr_confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence of the source OCR/text tokens")
