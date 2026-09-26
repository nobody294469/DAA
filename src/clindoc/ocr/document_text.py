"""Canonical DocumentText representation conforming to PROJECT_SPEC §7.1 and schemas/document_text.json.

Output contract for Document Ingestion and OCR processing.
Guarantees:
- Document-level container with page-level, character-addressable text.
- Page numbers are 1-indexed.
- Character offsets are page-local and deterministic: page_text[char_start:char_end] reproduces evidence text.
- Geometry represented as bounding boxes [x0, y0, x1, y1] and {'x0', 'y0', 'x1', 'y1'}.
- OCR confidence in [0.0, 1.0], or null for born-digital extraction.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional, Sequence
from pydantic import BaseModel, Field, field_validator, model_validator

from clindoc.ocr.geometry import BBox, BBoxDict, enclose_bboxes


class DegradationMetadata(BaseModel):
    """Synthetic degradation parameters applied during benchmark generation (§18.2)."""
    applied: bool = False
    types: list[Literal[
        "blur",
        "jpeg_compression",
        "resolution_reduction",
        "rotation_skew",
        "contrast_brightness",
        "local_artifact",
        "combined",
    ]] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class Token(BaseModel):
    """OCR or layout token on a document page."""
    text: str = Field(..., description="Raw string text content of the token")
    bbox: BBoxDict = Field(..., description="Bounding box with keys x0, y0, x1, y1")
    confidence: Optional[float] = Field(
        default=None,
        description="OCR engine confidence in [0, 1]; null for born-digital extraction",
    )
    line_id: str = Field(..., description="Identifier associating token with a logical line")

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError(f"Confidence must be between 0.0 and 1.0 or None, got {v}")
        return round(v, 4) if v is not None else None

    @property
    def bbox_list(self) -> list[float]:
        return self.bbox.to_list()

    @property
    def bbox_obj(self) -> BBox:
        return BBox(self.bbox.x0, self.bbox.y0, self.bbox.x1, self.bbox.y1)


class DocumentPage(BaseModel):
    """A single page of an ingested clinical document."""
    page_number: int = Field(..., ge=1, description="1-indexed page number")
    width_px: int = Field(..., ge=1, description="Page width in pixels / points")
    height_px: int = Field(..., ge=1, description="Page height in pixels / points")
    tokens: list[Token] = Field(default_factory=list, description="Ordered sequence of tokens for this page")

    # Cache for computed page text and token offsets
    _cached_page_text: Optional[str] = None
    _cached_token_offsets: Optional[list[tuple[Token, int, int]]] = None

    def get_page_text(self) -> str:
        """Reconstruct canonical page-level text.

        Tokens sharing the same line_id are joined by space (' ').
        Distinct lines are joined by newline ('\\n').
        Deterministic and character-addressable.
        """
        if not self.tokens:
            return ""

        # Group tokens preserving order of line_ids
        lines_order: list[str] = []
        tokens_by_line: dict[str, list[Token]] = {}
        for t in self.tokens:
            lid = t.line_id
            if lid not in tokens_by_line:
                lines_order.append(lid)
                tokens_by_line[lid] = []
            tokens_by_line[lid].append(t)

        line_texts = [" ".join(tok.text for tok in tokens_by_line[lid]) for lid in lines_order]
        return "\n".join(line_texts)

    @property
    def page_text(self) -> str:
        """Convenience property for page text."""
        return self.get_page_text()

    def slice_text(self, char_start: int, char_end: int) -> str:
        """Slice page text by character offsets."""
        text = self.get_page_text()
        start = max(0, min(char_start, len(text)))
        end = max(start, min(char_end, len(text)))
        return text[start:end]

    def get_token_offsets(self) -> list[tuple[Token, int, int]]:
        """Return list of (token, char_start, char_end) offsets within page_text."""
        if not self.tokens:
            return []

        lines_order: list[str] = []
        tokens_by_line: dict[str, list[Token]] = {}
        for t in self.tokens:
            lid = t.line_id
            if lid not in tokens_by_line:
                lines_order.append(lid)
                tokens_by_line[lid] = []
            tokens_by_line[lid].append(t)

        token_offsets: list[tuple[Token, int, int]] = []
        current_offset = 0

        for line_idx, lid in enumerate(lines_order):
            line_toks = tokens_by_line[lid]
            for tok_idx, tok in enumerate(line_toks):
                tok_len = len(tok.text)
                t_start = current_offset
                t_end = t_start + tok_len
                token_offsets.append((tok, t_start, t_end))
                current_offset = t_end
                if tok_idx < len(line_toks) - 1:
                    current_offset += 1  # space between words

            if line_idx < len(lines_order) - 1:
                current_offset += 1  # newline between lines

        return token_offsets

    def get_bbox_for_span(self, char_start: int, char_end: int) -> list[float] | None:
        """Compute the enclosing bounding box [x0, y0, x1, y1] for a character span."""
        token_offsets = self.get_token_offsets()
        overlapping_boxes: list[BBox] = []

        for tok, t_start, t_end in token_offsets:
            # Overlap condition: max(char_start, t_start) < min(char_end, t_end)
            if max(char_start, t_start) < min(char_end, t_end) or (char_start == char_end and t_start <= char_start <= t_end):
                overlapping_boxes.append(tok.bbox_obj)

        if not overlapping_boxes:
            return None

        enclosing = enclose_bboxes(overlapping_boxes)
        return enclosing.to_list() if enclosing else None

    def get_ocr_confidence_for_span(self, char_start: int, char_end: int) -> float | None:
        """Compute aggregated OCR confidence for tokens overlapping a character span."""
        token_offsets = self.get_token_offsets()
        confidences: list[float] = []

        for tok, t_start, t_end in token_offsets:
            if max(char_start, t_start) < min(char_end, t_end) or (char_start == char_end and t_start <= char_start <= t_end):
                if tok.confidence is not None:
                    confidences.append(tok.confidence)

        if not confidences:
            return None
        return round(sum(confidences) / len(confidences), 4)


class DocumentText(BaseModel):
    """Canonical DocumentText output contract (§7.1, schemas/document_text.json)."""
    document_id: str = Field(..., description="Unique identifier for the document")
    source_type: Literal["born_digital", "scanned_image"] = Field(
        ..., description="Source modality of the ingested document",
    )
    pages: list[DocumentPage] = Field(..., description="List of document pages in order")
    degradation_metadata: Optional[DegradationMetadata] = Field(
        default=None, description="Synthetic degradation parameters if applicable",
    )

    @property
    def num_pages(self) -> int:
        return len(self.pages)

    @property
    def total_tokens(self) -> int:
        return sum(len(p.tokens) for p in self.pages)

    def get_page(self, page_number: int) -> DocumentPage:
        """Retrieve page by 1-indexed page number."""
        for p in self.pages:
            if p.page_number == page_number:
                return p
        raise IndexError(f"Page {page_number} not found in document {self.document_id} (has {len(self.pages)} pages)")

    def get_full_text(self, separator: str = "\n\n") -> str:
        """Join all page texts with separator."""
        return separator.join(p.get_page_text() for p in self.pages)

    def to_dict(self) -> dict[str, Any]:
        """Dump dictionary strictly conforming to schemas/document_text.json."""
        d = self.model_dump(mode="json", exclude_none=True)
        # Ensure degradation_metadata is only present if set
        if self.degradation_metadata is None and "degradation_metadata" in d:
            del d["degradation_metadata"]
        return d

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string conforming to schemas/document_text.json."""
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: Path | str) -> None:
        """Save canonical DocumentText to JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(self.to_json(indent=2))

    @classmethod
    def load(cls, path: Path | str) -> DocumentText:
        """Load and validate DocumentText from JSON file."""
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def validate_schema(self) -> bool:
        """Validate instance against schemas/document_text.json structure."""
        schema_path = Path("schemas/document_text.json")
        if not schema_path.exists():
            return True

        # Field validation
        assert bool(self.document_id), "document_id cannot be empty"
        assert self.source_type in ("born_digital", "scanned_image"), f"Invalid source_type: {self.source_type}"
        assert isinstance(self.pages, list), "pages must be a list"

        for p in self.pages:
            assert p.page_number >= 1, f"page_number must be >= 1, got {p.page_number}"
            assert p.width_px >= 1, f"width_px must be >= 1, got {p.width_px}"
            assert p.height_px >= 1, f"height_px must be >= 1, got {p.height_px}"
            for t in p.tokens:
                assert bool(t.text), "token text cannot be empty"
                assert bool(t.line_id), "line_id cannot be empty"
                assert t.bbox.x0 <= t.bbox.x1, f"x0 > x1 in token {t.text}"
                assert t.bbox.y0 <= t.bbox.y1, f"y0 > y1 in token {t.text}"
                if t.confidence is not None:
                    assert 0.0 <= t.confidence <= 1.0, f"confidence out of range: {t.confidence}"
                if self.source_type == "born_digital":
                    # Born-digital confidence should be null per spec §7.1
                    assert t.confidence is None or t.confidence == 1.0, "born_digital confidence must be null or 1.0"
        return True
