"""Rendering and visualization utilities for the Clindoc Reviewer UI.

Authority: PROJECT_SPEC.md §7.8, §21; DEC-016
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image, ImageDraw, ImageFont
import pymupdf

from clindoc.extract.contracts import Evidence
from clindoc.ocr.document_text import DocumentText

# Color palette for verification verdict bounding boxes
VERDICT_COLORS = {
    "SUPPORTED": (40, 167, 69),            # Green
    "DISCREPANCY": (220, 53, 69),          # Red
    "INSUFFICIENT_EVIDENCE": (253, 126, 20),# Orange
    "DEFAULT": (108, 117, 125),            # Gray
}

VERDICT_BG_COLORS = {
    "SUPPORTED": "#d4edda",
    "DISCREPANCY": "#f8d7da",
    "INSUFFICIENT_EVIDENCE": "#fff3cd",
    "DEFAULT": "#e2e3e5",
}

VERDICT_TEXT_COLORS = {
    "SUPPORTED": "#155724",
    "DISCREPANCY": "#721c24",
    "INSUFFICIENT_EVIDENCE": "#856404",
    "DEFAULT": "#383d41",
}


def get_verdict_badge_html(label: str) -> str:
    """Generate HTML badge for a verification verdict."""
    lbl_upper = (label or "UNKNOWN").upper()
    bg = VERDICT_BG_COLORS.get(lbl_upper, VERDICT_BG_COLORS["DEFAULT"])
    fg = VERDICT_TEXT_COLORS.get(lbl_upper, VERDICT_TEXT_COLORS["DEFAULT"])
    icon = "✓" if lbl_upper == "SUPPORTED" else ("✗" if lbl_upper == "DISCREPANCY" else "⚠")
    return (
        f'<span style="background-color: {bg}; color: {fg}; padding: 3px 8px; '
        f'border-radius: 4px; font-weight: 600; font-size: 0.85rem; display: inline-block;">'
        f'{icon} {lbl_upper}</span>'
    )


def get_stage_badge_html(stage: str) -> str:
    """Generate HTML badge for a cascade stage."""
    stage_upper = (stage or "A").upper()
    stage_names = {
        "A": "Stage A (Deterministic)",
        "B": "Stage B (ModernBERT / NLI)",
        "C": "Stage C (LLM Judge)",
    }
    name = stage_names.get(stage_upper, f"Stage {stage_upper}")
    bg = "#e9ecef"
    fg = "#495057"
    if stage_upper == "C":
        bg = "#e8daef"
        fg = "#6c3483"
    elif stage_upper == "B":
        bg = "#d6eaf8"
        fg = "#1b4f72"
    return (
        f'<span style="background-color: {bg}; color: {fg}; padding: 2px 7px; '
        f'border-radius: 3px; font-size: 0.8rem; font-weight: 500;">'
        f'{name}</span>'
    )


def format_confidence(conf: Optional[float]) -> str:
    """Format float confidence score as percentage."""
    if conf is None:
        return "N/A"
    return f"{conf * 100.0:.1f}%"


def render_document_page_with_bboxes(
    file_path: Path | str,
    page_number: int = 1,
    evidence_annotations: Optional[Sequence[tuple[Evidence, str, str]]] = None,
    dpi: int = 150,
) -> Image.Image:
    """Render a document page as a PIL image with overlaid, color-coded evidence bounding boxes.

    Args:
        file_path: Path to the source document (.pdf, image, or DocumentText .json).
        page_number: 1-indexed page number to render.
        evidence_annotations: List of (Evidence, verdict_label, field_type) tuples.
        dpi: DPI resolution for PDF rendering.

    Returns:
        PIL Image with highlighted bounding boxes.
    """
    path = Path(file_path)
    annotations = evidence_annotations or []

    # 1. Handle PDF Documents
    if path.suffix.lower() == ".pdf":
        doc = pymupdf.open(str(path))
        target_idx = max(0, min(page_number - 1, len(doc) - 1))
        page = doc[target_idx]
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        scale_x = pix.width / max(float(page.rect.width), 1.0)
        scale_y = pix.height / max(float(page.rect.height), 1.0)

        _draw_boxes_on_image(img, annotations, page_number, scale_x, scale_y)
        return img

    # 2. Handle Image Documents (.png, .jpg, .jpeg, .tiff, etc.)
    elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tiff", ".tif"}:
        img = Image.open(path).convert("RGB")
        _draw_boxes_on_image(img, annotations, page_number, 1.0, 1.0)
        return img

    # 3. Handle DocumentText JSON files
    elif path.suffix.lower() == ".json":
        try:
            doc_text = DocumentText.load(path)
            target_page = next((p for p in doc_text.pages if p.page_number == page_number), None)
            if target_page is None and doc_text.pages:
                target_page = doc_text.pages[0]

            width = target_page.width_px if target_page else 612
            height = target_page.height_px if target_page else 792
            img = Image.new("RGB", (max(width, 400), max(height, 500)), "white")
            draw = ImageDraw.Draw(img)

            # Draw token text
            if target_page:
                for token in target_page.tokens:
                    tb = token.bbox
                    draw.text((tb.x0, tb.y0), token.text, fill=(50, 50, 50))

            _draw_boxes_on_image(img, annotations, page_number, 1.0, 1.0)
            return img
        except Exception:
            # Fallback canvas
            img = Image.new("RGB", (612, 792), "white")
            _draw_boxes_on_image(img, annotations, page_number, 1.0, 1.0)
            return img

    # Default fallback
    img = Image.new("RGB", (612, 792), "white")
    _draw_boxes_on_image(img, annotations, page_number, 1.0, 1.0)
    return img


def _draw_boxes_on_image(
    img: Image.Image,
    annotations: Sequence[tuple[Evidence, str, str]],
    page_number: int,
    scale_x: float,
    scale_y: float,
) -> None:
    """Draw bounding boxes and label tags onto a PIL Image."""
    draw = ImageDraw.Draw(img)

    for ev, verdict, field_type in annotations:
        if ev.page_number != page_number:
            continue

        bbox = ev.bbox
        x0 = bbox.x0 * scale_x
        y0 = bbox.y0 * scale_y
        x1 = bbox.x1 * scale_x
        y1 = bbox.y1 * scale_y

        color = VERDICT_COLORS.get(verdict.upper(), VERDICT_COLORS["DEFAULT"])

        # Draw outer rectangle
        draw.rectangle([x0, y0, x1, y1], outline=color, width=3)

        # Draw small tag label above rectangle
        tag_text = f"{field_type} [{verdict}]"
        tag_y = max(0.0, y0 - 14.0)
        draw.rectangle([x0, tag_y, x0 + min(len(tag_text) * 7.0, 200.0), tag_y + 12.0], fill=color)
        draw.text((x0 + 2.0, tag_y + 1.0), tag_text[:28], fill="white")
