"""Verification Context Window and Canonical Text Utilities (DR-3, DEC-003).

Provides deterministic, reusable context-window extraction for both dataset
generation and inference time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union
import yaml

DEFAULT_CONFIG_PATH = Path("configs/context_window.yaml")


def load_context_config(config_path: Union[str, Path, None] = None) -> dict[str, Any]:
    """Load context window configuration from YAML."""
    p = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {
        "window_policy": {
            "primary_strategy": "paragraph_bounded",
            "max_characters": 500,
            "fallback_strategy": "fixed_symmetric_window",
            "fallback_characters": 250,
        },
        "dense_tabular_override": {
            "enabled": True,
            "strategy": "fixed_symmetric_window",
            "window_characters": 250,
        },
    }


def extract_context_window(
    page_text: str,
    char_start: int,
    char_end: int,
    is_tabular: bool = False,
    config: dict[str, Any] | None = None,
) -> tuple[str, int, int]:
    """Extract context window surrounding [char_start, char_end] per DR-3 policy.

    Rules:
      - If is_tabular and dense_tabular_override is enabled:
          Uses fixed symmetric window of window_characters (default 250) chars around span.
      - Else (narrative / paragraph text):
          Attempts paragraph-bounded window (up to max_characters, default 500),
          falling back to fixed symmetric window (fallback_characters, default 250)
          if no paragraph boundaries are found.

    Returns:
      (context_text, window_start, window_end)
    """
    if not page_text:
        return "", 0, 0

    if config is None:
        config = load_context_config()

    dense_override = config.get("dense_tabular_override", {})
    window_policy = config.get("window_policy", {})

    max_chars = int(window_policy.get("max_characters", 500))
    fallback_chars = int(window_policy.get("fallback_characters", 250))
    tabular_chars = int(dense_override.get("window_characters", 250))

    char_start = max(0, min(char_start, len(page_text)))
    char_end = max(char_start, min(char_end, len(page_text)))

    if is_tabular and dense_override.get("enabled", True):
        # Fixed symmetric window for tabular data
        start = max(0, char_start - tabular_chars)
        end = min(len(page_text), char_end + tabular_chars)
        return page_text[start:end], start, end

    # Paragraph-bounded extraction
    # Look for preceding paragraph boundary (\n\n) within max_chars
    search_start = max(0, char_start - max_chars)
    para_start = page_text.rfind("\n\n", search_start, char_start)
    if para_start == -1:
        start = max(0, char_start - fallback_chars)
    else:
        start = para_start + 2

    # Look for succeeding paragraph boundary (\n\n) within max_chars
    search_end = min(len(page_text), char_end + max_chars)
    para_end = page_text.find("\n\n", char_end, search_end)
    if para_end == -1:
        end = min(len(page_text), char_end + fallback_chars)
    else:
        end = para_end

    # Enforce maximum character hard cap
    if (end - start) > max_chars:
        excess = (end - start) - max_chars
        trim_left = min(char_start - start, excess // 2)
        start += trim_left
        end = min(len(page_text), start + max_chars)

    return page_text[start:end], start, end


def reconstruct_canonical_page_text(
    ocr_lines: list[dict[str, Any]],
    is_tabular: bool = False,
    page_number: int = 1,
) -> tuple[str, list[dict[str, Any]]]:
    """Reconstruct canonical page text and token rows from OCR lines.

    For tabular documents (e.g. lab reports), groups tokens by vertical baseline
    into ordered rows so that entire table rows form single coherent text lines.
    For non-tabular documents (prescriptions/notes), lines are concatenated in order.

    Returns:
      (canonical_page_text, reconstructed_rows_with_metadata)
    """
    page_lines = [l for l in ocr_lines if l.get("page", 1) == page_number]
    if not page_lines:
        return "", []

    if is_tabular:
        # Group tokens into horizontal rows
        rows: list[list[dict[str, Any]]] = []
        for l in page_lines:
            placed = False
            l_y0 = l["bbox"][1] if "bbox" in l and len(l["bbox"]) >= 2 else 0.0
            for r in rows:
                r_y0 = r[0]["bbox"][1] if "bbox" in r[0] and len(r[0]["bbox"]) >= 2 else 0.0
                if abs(r_y0 - l_y0) < 2.5:
                    r.append(l)
                    placed = True
                    break
            if not placed:
                rows.append([l])

        row_records: list[dict[str, Any]] = []
        row_texts: list[str] = []
        current_offset = 0

        for r in rows:
            # Sort horizontally by x0
            r_sorted = sorted(r, key=lambda x: x["bbox"][0] if "bbox" in x else 0.0)
            text = " ".join(x.get("text", "") for x in r_sorted)
            x0 = min(x["bbox"][0] for x in r_sorted if "bbox" in x)
            y0 = min(x["bbox"][1] for x in r_sorted if "bbox" in x)
            x1 = max(x["bbox"][2] for x in r_sorted if "bbox" in x)
            y1 = max(x["bbox"][3] for x in r_sorted if "bbox" in x)
            conf = sum(x.get("confidence", 1.0) for x in r_sorted) / max(len(r_sorted), 1)

            row_records.append({
                "text": text,
                "bbox": [x0, y0, x1, y1],
                "char_start": current_offset,
                "char_end": current_offset + len(text),
                "confidence": conf,
                "page": page_number,
            })
            row_texts.append(text)
            current_offset += len(text) + 1  # account for \n

        return "\n".join(row_texts), row_records

    # Non-tabular documents: lines in sequence
    row_records = []
    row_texts = []
    current_offset = 0
    for l in page_lines:
        text = l.get("text", "")
        bbox = l.get("bbox", [0.0, 0.0, 0.0, 0.0])
        conf = l.get("confidence", 1.0)
        row_records.append({
            "text": text,
            "bbox": bbox,
            "char_start": current_offset,
            "char_end": current_offset + len(text),
            "confidence": conf,
            "page": page_number,
        })
        row_texts.append(text)
        current_offset += len(text) + 1

    return "\n".join(row_texts), row_records
