"""Tabular layout and structured table row reconstruction for clinical laboratory reports.

Conforms to PROJECT_SPEC §7.1 and Phase 5.1 requirements.
Provides robust spatial table structure detection:
- Cell row grouping using baseline alignment and vertical bounding box overlap.
- Detection of likely table regions and column intervals.
- Handling of wrapped multi-line analyte names without losing value associations.
- Handling of multi-line table headers (e.g. 'Observed\\nValue').
- Safe fallback to deterministic horizontal grouping.
- Prevention of cross-row or cross-column value collisions.
"""

from __future__ import annotations

import math
import re
from typing import Any, Sequence

LAB_HEADER_KEYWORDS = {
    "test description",
    "test name",
    "analyte",
    "component",
    "observed value",
    "result",
    "value",
    "reference interval",
    "reference range",
    "normal range",
    "units",
    "unit",
    "flag",
    "status",
    "interpretation",
}

COMMON_CLINICAL_UNITS = {
    "mg/dl",
    "mmol/l",
    "g/dl",
    "u/l",
    "iu/l",
    "meq/l",
    "mcg/dl",
    "ng/ml",
    "pg/ml",
    "fl",
    "%",
    "k/ul",
    "m/ul",
    "10*3/ul",
    "10*6/ul",
    "gm%",
    "g%",
    "cells/cumm",
    "cumm",
    "/cumm",
    "mg/l",
    "ug/dl",
    "g/l",
    "iu/ml",
    "miu/ml",
    "ng/dl",
    "meq/24hr",
}

# Tokens indicating medication dosing or administration that disqualify laboratory row continuation
_MED_INDICATOR_TOKENS = frozenset({
    "tab", "tabs", "cap", "caps", "syp", "inj", "oral", "po", "iv", "im", "sc", "sq", "subq",
    "sl", "pr", "top", "daily", "bid", "tid", "qid", "prn", "qhs", "stat", "mg", "mcg",
    "take", "prescribed", "dispense", "disp", "tablet", "tablets", "capsule", "capsules",
})


def _validate_and_extract_bbox(bbox: Any) -> tuple[float, float, float, float] | None:
    """Validate and extract (x0, y0, x1, y1) coordinates from any bounding box representation.

    Guarantees:
      - Returns None if bbox is None or not a recognized container.
      - Requires all 4 coordinates to be present and finite numbers (not NaN, not Inf).
      - Enforces geometry invariants: x0 >= 0, y0 >= 0, x1 > x0, y1 > y0 (non-inverted, positive area).
      - Rejects non-numeric types, booleans, NaN, and +/-Inf.
      - Returns (x0, y0, x1, y1) as floats rounded to 4 decimals.
    """
    if bbox is None:
        return None

    # Handle object with x0, y0, x1, y1 attributes (BBox, BBoxDict, BBoxModel, etc.)
    if hasattr(bbox, "x0") and hasattr(bbox, "y0") and hasattr(bbox, "x1") and hasattr(bbox, "y1"):
        try:
            x0, y0, x1, y1 = bbox.x0, bbox.y0, bbox.x1, bbox.y1
        except Exception:
            return None
    # Handle dictionary representation
    elif isinstance(bbox, dict):
        if not ("x0" in bbox and "y0" in bbox and "x1" in bbox and "y1" in bbox):
            return None
        x0, y0, x1, y1 = bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]
    # Handle list or tuple representation
    elif isinstance(bbox, (list, tuple)):
        if len(bbox) != 4:
            return None
        x0, y0, x1, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
    else:
        return None

    # Validate numeric types and finiteness (reject booleans, NaN, +Inf, -Inf, strings)
    if isinstance(x0, bool) or isinstance(y0, bool) or isinstance(x1, bool) or isinstance(y1, bool):
        return None

    try:
        fx0 = float(x0)
        fy0 = float(y0)
        fx1 = float(x1)
        fy1 = float(y1)
    except (TypeError, ValueError):
        return None

    if not (math.isfinite(fx0) and math.isfinite(fy0) and math.isfinite(fx1) and math.isfinite(fy1)):
        return None

    # Invariants: non-negative coordinates, non-inverted, positive dimensions
    if fx0 < 0.0 or fy0 < 0.0 or fx1 <= fx0 or fy1 <= fy0:
        return None

    return (round(fx0, 4), round(fy0, 4), round(fx1, 4), round(fy1, 4))


def is_vertical_lab_component_continuation(
    prev_text: str,
    curr_text: str,
    prev_bbox: Any | None = None,
    curr_bbox: Any | None = None,
    x_tolerance: float = 15.0,
    max_y_gap: float = 35.0,
) -> bool:
    """Determine whether curr_text is a vertical continuation of prev_text in a structured lab finding.

    Conforms to Phase 5.2.3 Milestone 2.3 & 2.4: Layout-aware logical laboratory row reconstruction.
    Under Milestone 2.4, geometry is STRICTLY FAIL-CLOSED:
      - BOTH bounding boxes must be present and valid before continuation is allowed.
      - Missing, malformed, non-finite, inverted, or non-aligned geometry returns False.
      - No syntax-only continuation is permitted as a fallback.

    Evidence of a shared logical row/cell:
      1. Spatial layout alignment:
         - Both prev_bbox and curr_bbox must be strictly valid non-inverted finite boxes.
         - Shares the same column / left margin (abs(curr_bbox[0] - prev_bbox[0]) <= effective_x_tol or horizontal overlap).
         - Is vertically proximate (curr_bbox[1] - prev_bbox[3] <= effective_max_y).
      2. Structural / content complementarity:
         - curr_text is an incomplete constituent fragment:
           a) Pure numeric laboratory result / flag (e.g., '12.6', '4.2', '<0.01', 'Negative', 'Non-Reactive').
           b) Pure clinical laboratory unit (e.g., 'GM%', 'mEq/L', 'g/dL', 'mg/dL', 'mmol/L', 'U/L', '%').
           c) Pure reference interval (e.g., '12.0 - 16.0', '(ref 12-16)').
         - prev_text is not terminated by a sentence boundary (. ! ? ;), list marker, or colon.
         - curr_text does NOT contain medication dosing forms (tab, cap), administration routes (po, oral, iv),
           action verbs (take, disp, add), or a new analyte anchor name.
    """
    pt = prev_text.strip()
    ct = curr_text.strip()
    if not pt or not ct:
        return False

    # 1. Punctuation checks on prev_text:
    # If prev_text ended with a genuine sentence terminator or clause break (; or . ! ? not in decimal), cannot continue
    if pt.endswith(";") or pt.endswith(":"):
        return False
    if re.search(r"(?<!\d)[.!?]+\Z", pt):
        return False

    # 2. Medication contamination guard:
    # Neither prev_text nor curr_text may contain medication routing, dosing, or form tokens
    pt_tokens = set(re.findall(r"[A-Za-z]+", pt.lower()))
    ct_tokens = set(re.findall(r"[A-Za-z]+", ct.lower()))
    if (pt_tokens & _MED_INDICATOR_TOKENS) or (ct_tokens & _MED_INDICATOR_TOKENS):
        return False

    # 3. Content complementarity: is curr_text a constituent lab fragment?
    # 3a. Pure numeric value (with optional comparison operator <, >, <=, >=)
    is_numeric_val = bool(re.match(r"^[<>]?\s*-?\d+(?:\.\d+)?\s*$", ct))

    # 3b. Pure qualitative result flag
    is_qual_flag = bool(re.match(
        r"^(?:negative|non-reactive|nonreactive|normal|high|low|critical|trace|nil|positive|reactive)\s*$",
        ct,
        re.IGNORECASE,
    ))

    # 3c. Pure clinical laboratory unit
    ct_unit_clean = ct.lower().rstrip(".").strip()
    is_lab_unit = (ct_unit_clean in COMMON_CLINICAL_UNITS) or bool(re.match(
        r"^(?:gm%|g%|g/dl|mg/dl|meq/l|mmol/l|u/l|iu/l|mcg/dl|ng/ml|pg/ml|%|k/ul|m/ul|10\*\d/\w+|cells/cumm|cumm|/cumm|mg/l|ug/dl|g/l)\Z",
        ct_unit_clean,
        re.IGNORECASE,
    ))

    # 3d. Pure reference range
    is_ref_range = bool(re.match(
        r"^\(?\s*(?:ref\.?|reference)?\s*:?\s*-?\d+(?:\.\d+)?\s*[-–]\s*-?\d+(?:\.\d+)?\s*\)?$",
        ct,
        re.IGNORECASE,
    ))

    # 3e. Numeric value + lab unit (e.g. '12.6 GM%' or '4.2 mEq/L') when prev_text is solely an analyte name
    is_val_and_unit = False
    val_unit_m = re.match(r"^(-?\d+(?:\.\d+)?)\s+([A-Za-z%/\*0-9]+)\Z", ct, re.IGNORECASE)
    if val_unit_m:
        u = val_unit_m.group(2).lower().rstrip(".")
        if (u in COMMON_CLINICAL_UNITS) or bool(re.match(
            r"^(?:gm%|g%|g/dl|mg/dl|meq/l|mmol/l|u/l|iu/l|mcg/dl|ng/ml|pg/ml|%|k/ul|m/ul|10\*\d/\w+)\Z",
            u,
            re.IGNORECASE,
        )):
            # Only valid if prev_text has no numeric value (i.e. prev_text is the analyte header)
            if not re.search(r"\d", pt):
                is_val_and_unit = True

    if not (is_numeric_val or is_qual_flag or is_lab_unit or is_ref_range or is_val_and_unit):
        return False

    # 4. Spatial layout verification:
    # Phase 5.2.3 Milestone 2.4: Fail-closed geometry enforcement.
    # BOTH bounding boxes must be present and strictly valid before layout-aware continuation is permitted.
    # Missing, malformed, non-finite, inverted, negative, or degenerate geometry returns False (fail-closed).
    p_box = _validate_and_extract_bbox(prev_bbox)
    c_box = _validate_and_extract_bbox(curr_bbox)
    if p_box is None or c_box is None:
        return False

    px0, py0, px1, py1 = p_box
    cx0, cy0, cx1, cy1 = c_box

    # Resolution-adaptive tolerances based on previous line text height
    prev_h = py1 - py0
    effective_x_tol = max(x_tolerance, 1.0 * prev_h)
    effective_max_y = max(max_y_gap, 2.5 * prev_h)

    # Column alignment: left edges aligned or significant horizontal overlap
    aligned_x = (abs(px0 - cx0) <= effective_x_tol) or (max(px0, cx0) < min(px1, cx1))
    if not aligned_x:
        return False

    # Vertical order and proximity
    # curr_text must be below prev_text, with gap within effective_max_y
    y_gap = cy0 - py1
    if y_gap < -2.0 or y_gap > effective_max_y:
        return False

    return True


def detect_tabular_layout(
    doc_id: str,
    lines: Sequence[dict[str, Any]] | None = None,
    is_tabular_override: bool | None = None,
) -> bool:
    """Determine whether document has a tabular layout (e.g., laboratory report).

    Checks:
      1. Explicit override if provided.
      2. Document ID naming convention (LAB_*, TEMPLATE_A/B/C).
      3. Content inspection for laboratory headers and structured column alignments.
    """
    if is_tabular_override is not None:
        return is_tabular_override

    doc_id_upper = doc_id.upper()
    if (
        doc_id_upper.startswith("LAB_")
        or "TEMPLATE_A" in doc_id_upper
        or "TEMPLATE_B" in doc_id_upper
        or "TEMPLATE_C" in doc_id_upper
        or "LABORATORY" in doc_id_upper
        or "PATHOLOGY" in doc_id_upper
    ):
        return True

    if not lines:
        return False

    # Content-based heuristic inspection
    all_text = " ".join(str(l.get("text", "")).lower() for l in lines)
    keyword_matches = sum(1 for kw in LAB_HEADER_KEYWORDS if kw in all_text)
    unit_matches = sum(1 for u in COMMON_CLINICAL_UNITS if f" {u}" in all_text or f"/{u}" in all_text)

    # If document has at least 2 table header keywords and clinical units
    if keyword_matches >= 2 and unit_matches >= 1:
        return True

    # Check for multi-column horizontal alignments: multiple lines sharing the same y
    y_coords = [round(l.get("bbox", [0, 0, 0, 0])[1], 1) for l in lines if "bbox" in l and len(l["bbox"]) >= 2]
    from collections import Counter
    y_counts = Counter(y_coords)
    # If 3 or more y-levels contain >= 3 distinct horizontally placed tokens
    aligned_rows = sum(1 for y, count in y_counts.items() if count >= 3)
    if aligned_rows >= 3:
        return True

    return False


def reconstruct_tabular_page_text(
    page_lines: list[dict[str, Any]],
    page_number: int = 1,
    vertical_tolerance: float = 3.5,
    vertical_overlap_ratio: float = 0.40,
) -> tuple[str, list[dict[str, Any]]]:
    """Reconstruct canonical page text and token rows from OCR lines for tabular documents.

    Groups cells by vertical baseline and vertical overlap into coherent rows.
    Handles wrapped analyte names and multi-line headers while preserving exact offsets.
    """
    if not page_lines:
        return "", []

    # 1. Group tokens into horizontal rows based on vertical proximity and overlap
    raw_rows: list[list[dict[str, Any]]] = []

    for l in page_lines:
        placed = False
        l_bbox = l.get("bbox", [0.0, 0.0, 0.0, 0.0])
        l_y0 = l_bbox[1] if len(l_bbox) >= 2 else 0.0
        l_y1 = l_bbox[3] if len(l_bbox) >= 4 else (l_y0 + 10.0)
        l_h = max(l_y1 - l_y0, 1.0)

        for r in raw_rows:
            r_bbox = r[0].get("bbox", [0.0, 0.0, 0.0, 0.0])
            r_y0 = r_bbox[1] if len(r_bbox) >= 2 else 0.0
            r_y1 = r_bbox[3] if len(r_bbox) >= 4 else (r_y0 + 10.0)
            r_h = max(r_y1 - r_y0, 1.0)

            # Check baseline proximity
            if abs(r_y0 - l_y0) <= vertical_tolerance:
                r.append(l)
                placed = True
                break

            # Check vertical interval overlap
            v_overlap = max(0.0, min(l_y1, r_y1) - max(l_y0, r_y0))
            if v_overlap / min(l_h, r_h) >= vertical_overlap_ratio:
                r.append(l)
                placed = True
                break

        if not placed:
            raw_rows.append([l])

    # Sort each row horizontally by x0
    for r in raw_rows:
        r.sort(key=lambda x: x.get("bbox", [0.0])[0] if "bbox" in x else 0.0)

    # Sort all rows vertically by the min y0 of each row
    raw_rows.sort(key=lambda r: min(x.get("bbox", [0.0, 0.0])[1] for x in r if "bbox" in x))

    # 2. Wrapped Analyte Continuation Merging
    # If a row has only 1 element in the left-hand analyte column and NO value/unit/range,
    # and immediately follows a row that has analyte + values, merge the wrapped text into the analyte.
    merged_rows: list[list[dict[str, Any]]] = []

    for r in raw_rows:
        if not merged_rows:
            merged_rows.append(r)
            continue

        prev_row = merged_rows[-1]
        prev_min_y = min(x.get("bbox", [0.0, 0.0])[1] for x in prev_row if "bbox" in x)
        curr_min_y = min(x.get("bbox", [0.0, 0.0])[1] for x in r if "bbox" in x)
        y_gap = curr_min_y - prev_min_y

        # Check if current row is a single-element wrapped continuation
        if len(r) == 1 and len(prev_row) >= 2 and (0.0 < y_gap <= 22.0):
            curr_elem = r[0]
            curr_x0 = curr_elem.get("bbox", [0.0])[0] if "bbox" in curr_elem else 0.0
            curr_x1 = curr_elem.get("bbox", [0.0, 0.0, 0.0])[2] if "bbox" in curr_elem and len(curr_elem["bbox"]) >= 3 else 0.0
            prev_x0 = prev_row[0].get("bbox", [0.0])[0] if "bbox" in prev_row[0] else 0.0
            prev_val_x0 = prev_row[1].get("bbox", [0.0])[0] if "bbox" in prev_row[1] else 999.0

            # If current element is within the analyte column and before the value column
            curr_text = curr_elem.get("text", "").strip()
            # Must not be a separate standalone test or header
            is_parenthetical_or_continuation = (
                curr_text.startswith("(")
                or curr_text.endswith(")")
                or curr_x0 <= prev_x0 + 40.0
            ) and curr_x1 < prev_val_x0 and not any(kw in curr_text.lower() for kw in LAB_HEADER_KEYWORDS)

            if is_parenthetical_or_continuation:
                # Merge into the first element (analyte name) of prev_row
                prev_analyte = prev_row[0]
                prev_analyte["text"] = f"{prev_analyte.get('text', '')} {curr_text}"
                # Expand bounding box
                if "bbox" in prev_analyte and "bbox" in curr_elem:
                    p_box = prev_analyte["bbox"]
                    c_box = curr_elem["bbox"]
                    prev_analyte["bbox"] = [
                        min(p_box[0], c_box[0]),
                        min(p_box[1], c_box[1]),
                        max(p_box[2], c_box[2]),
                        max(p_box[3], c_box[3]),
                    ]
                continue

        # Check for multi-line table header:
        # e.g. Prev row has "Observed", curr row has "Value"
        if len(prev_row) >= 2 and len(r) >= 2 and (0.0 < y_gap <= 22.0):
            prev_text_low = " ".join(x.get("text", "").lower() for x in prev_row)
            curr_text_low = " ".join(x.get("text", "").lower() for x in r)
            both_header = (
                any(kw in prev_text_low for kw in ("test", "observed", "reference", "status"))
                and any(kw in curr_text_low for kw in ("description", "value", "interval", "units", "range"))
            )
            if both_header and len(prev_row) == len(r):
                # Merge matching column headers pairwise
                for p_elem, c_elem in zip(prev_row, r):
                    p_elem["text"] = f"{p_elem.get('text', '')} {c_elem.get('text', '')}".strip()
                    if "bbox" in p_elem and "bbox" in c_elem:
                        p_box = p_elem["bbox"]
                        c_box = c_elem["bbox"]
                        p_elem["bbox"] = [
                            min(p_box[0], c_box[0]),
                            min(p_box[1], c_box[1]),
                            max(p_box[2], c_box[2]),
                            max(p_box[3], c_box[3]),
                        ]
                continue

        merged_rows.append(r)

    # 3. Construct row records and canonical page text
    row_records: list[dict[str, Any]] = []
    row_texts: list[str] = []
    current_offset = 0

    for r in merged_rows:
        text = " ".join(x.get("text", "") for x in r)
        x0 = min(x["bbox"][0] for x in r if "bbox" in x)
        y0 = min(x["bbox"][1] for x in r if "bbox" in x)
        x1 = max(x["bbox"][2] for x in r if "bbox" in x)
        y1 = max(x["bbox"][3] for x in r if "bbox" in x)
        conf = sum(x.get("confidence", 1.0) for x in r) / max(len(r), 1)

        row_records.append({
            "text": text,
            "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
            "char_start": current_offset,
            "char_end": current_offset + len(text),
            "confidence": round(conf, 4),
            "page": page_number,
        })
        row_texts.append(text)
        current_offset += len(text) + 1  # newline separator

    return "\n".join(row_texts), row_records
