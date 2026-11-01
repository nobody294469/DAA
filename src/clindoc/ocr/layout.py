"""Two-dimensional layout analysis and reading-order reconstruction for born-digital documents.

Conforms to PROJECT_SPEC §7.1 and Phase 5.1 requirements.
Provides explainable, deterministic 2D layout analysis:
- Column detection using vertical gutters (whitespace projection).
- Band/section separation for full-width headers, banners, and section dividers.
- Header and footer isolation.
- Column-first, top-to-bottom reading order (reading left column completely before right column).
- Multi-column, single-column, three-column, and sidebar support.
- Preservation of original token/line bounding boxes, offsets, and words.
"""

from __future__ import annotations

from typing import Sequence
from clindoc.ocr.schema import LineBox


def sort_page_lines_reading_order(
    lines: Sequence[LineBox],
    page_width: float = 612.0,
    page_height: float = 792.0,
    min_gutter_width: float = 12.0,
    full_width_threshold_ratio: float = 0.58,
) -> list[LineBox]:
    """Sort lines on a single page into natural clinical reading order.

    Algorithm:
      1. Isolate top-of-page headers and bottom-of-page footers.
      2. For body lines, group into vertical bands separated by full-width headings or horizontal gaps.
      3. Within each band, detect vertical gutters (whitespace intervals between columns).
      4. If gutters exist, partition lines into columns (left to right).
         Sort lines within each column top-to-bottom.
      5. If no gutter exists (single column or tabular line), sort lines top-to-bottom.
      6. Assemble: Headers -> Bands (in vertical order) -> Footers.

    Args:
      lines: Sequence of LineBox objects for the page.
      page_width: Width of page in points.
      page_height: Height of page in points.
      min_gutter_width: Minimum horizontal gutter width in points to qualify as a column boundary.
      full_width_threshold_ratio: Minimum width ratio of content span to consider a line 'full-width'.

    Returns:
      Re-ordered list of LineBox objects.
    """
    if len(lines) <= 1:
        return list(lines)

    # Calculate content bounding box
    min_x = min(l.bbox[0] for l in lines)
    max_x = max(l.bbox[2] for l in lines)
    content_width = max(max_x - min_x, 10.0)

    # Thresholds for headers, footers, and full-width headings
    header_y_limit = page_height * 0.08  # Top 8% of page
    footer_y_limit = page_height * 0.93  # Bottom 7% of page
    full_width_min_len = content_width * full_width_threshold_ratio

    headers: list[LineBox] = []
    footers: list[LineBox] = []
    body_lines: list[LineBox] = []

    for line in lines:
        y0, y1 = line.bbox[1], line.bbox[3]
        text_lower = line.text.strip().lower()

        # Check for footer patterns (e.g., page numbers, confidentiality notes in bottom margin)
        if y0 >= footer_y_limit or (
            y0 >= page_height * 0.88 and ("page " in text_lower or text_lower.startswith("confidential"))
        ):
            footers.append(line)
        # Check for header patterns (top margin)
        elif y1 <= header_y_limit and y1 <= page_height * 0.10:
            headers.append(line)
        else:
            body_lines.append(line)

    # Sort headers and footers top-to-bottom
    headers_sorted = sorted(headers, key=lambda l: (l.bbox[1], l.bbox[0]))
    footers_sorted = sorted(footers, key=lambda l: (l.bbox[1], l.bbox[0]))

    if not body_lines:
        return headers_sorted + footers_sorted

    # Process body lines into vertical bands
    # Sort body lines by y0 initially to detect horizontal cuts and full-width dividers
    body_lines_by_y = sorted(body_lines, key=lambda l: (l.bbox[1], l.bbox[0]))

    # Identify full-width dividers or significant vertical gaps that define band boundaries
    bands: list[list[LineBox]] = []
    current_band: list[LineBox] = []

    for line in body_lines_by_y:
        line_w = line.bbox[2] - line.bbox[0]
        # A line is a full-width section heading / divider across columns if:
        # It starts near the left margin AND extends near the right margin across candidate columns
        is_full_width = (
            (line.bbox[0] <= min_x + 0.15 * content_width)
            and (line.bbox[2] >= max_x - 0.15 * content_width)
            and (line_w >= content_width * 0.65)
        )

        if is_full_width:
            # If line spans across columns, it separates previous band from next band
            if current_band:
                bands.append(current_band)
                current_band = []
            bands.append([line])  # Full-width line forms its own single-line band
        else:
            current_band.append(line)

    if current_band:
        bands.append(current_band)

    # Order each band into reading order
    ordered_body: list[LineBox] = []
    for band in bands:
        if len(band) <= 1:
            ordered_body.extend(band)
            continue

        band_ordered = _order_band_lines(
            band,
            min_gutter_width=min_gutter_width,
            content_min_x=min_x,
            content_max_x=max_x,
        )
        ordered_body.extend(band_ordered)

    return headers_sorted + ordered_body + footers_sorted


def _order_band_lines(
    band_lines: list[LineBox],
    min_gutter_width: float,
    content_min_x: float,
    content_max_x: float,
) -> list[LineBox]:
    """Order lines within a vertical band that may contain 1, 2, or more columns.

    Detects vertical gutters (columns of empty space where no line spans).
    """
    if len(band_lines) <= 1:
        return band_lines

    # Sort lines by x0
    band_sorted_x = sorted(band_lines, key=lambda l: l.bbox[0])

    # Find candidate column intervals by merging overlapping [x0, x1] intervals of lines
    # Note: To avoid tiny individual word protrusions bridging columns, we look for substantial
    # vertical whitespace channels between line intervals.
    intervals: list[tuple[float, float, list[LineBox]]] = []

    # Cluster lines into columns based on horizontal overlap and gutters
    # First, let's identify gutters across the band
    gutters = _find_vertical_gutters(band_lines, min_gutter_width=min_gutter_width)

    if not gutters:
        # No vertical gutter found: treat as single column, sort top-to-bottom
        return sorted(band_lines, key=lambda l: (l.bbox[1], l.bbox[0]))

    # Split lines into columns based on gutters
    # Gutter: (gutter_start, gutter_end)
    columns: list[list[LineBox]] = [[] for _ in range(len(gutters) + 1)]

    for line in band_lines:
        line_mid_x = (line.bbox[0] + line.bbox[2]) / 2.0
        # Assign line to column
        assigned_col = 0
        for g_idx, (g_start, g_end) in enumerate(gutters):
            if line_mid_x < g_start:
                assigned_col = g_idx
                break
            elif line_mid_x > g_end:
                assigned_col = g_idx + 1
            else:
                # Midpoint falls inside gutter, check which side has more overlap
                left_overlap = max(0.0, g_start - line.bbox[0])
                right_overlap = max(0.0, line.bbox[2] - g_end)
                assigned_col = g_idx if left_overlap >= right_overlap else g_idx + 1
                break
        columns[assigned_col].append(line)

    # Order each column top-to-bottom, and concatenate columns left-to-right
    ordered_band: list[LineBox] = []
    for col_lines in columns:
        if col_lines:
            col_sorted = sorted(col_lines, key=lambda l: (l.bbox[1], l.bbox[0]))
            ordered_band.extend(col_sorted)

    return ordered_band


def _find_vertical_gutters(
    lines: list[LineBox],
    min_gutter_width: float = 12.0,
) -> list[tuple[float, float]]:
    """Identify vertical whitespace gutters between columns.

    A vertical gutter is a horizontal interval [g_start, g_end] where no line's
    bounding box significantly crosses (or lines strictly stay on left or right).
    """
    if len(lines) < 2:
        return []

    # Get span of content in this band
    band_x0 = min(l.bbox[0] for l in lines)
    band_x1 = max(l.bbox[2] for l in lines)
    total_w = band_x1 - band_x0

    if total_w < min_gutter_width * 3:
        return []

    # Collect line intervals [x0, x1]
    # For robust gutter detection, examine intervals where multiple lines coexist at the same y-ranges
    # A true column layout has lines that overlap vertically but are separated horizontally.
    has_side_by_side = False
    for i, l1 in enumerate(lines):
        for l2 in lines[i + 1 :]:
            # Check if lines have vertical overlap
            y_overlap = min(l1.bbox[3], l2.bbox[3]) - max(l1.bbox[1], l2.bbox[1])
            if y_overlap > 3.0:
                # Check horizontal separation
                h_gap = max(l1.bbox[0], l2.bbox[0]) - min(l1.bbox[2], l2.bbox[2])
                if h_gap >= min_gutter_width:
                    has_side_by_side = True
                    break
        if has_side_by_side:
            break

    if not has_side_by_side:
        return []

    # Find empty horizontal slices across the lines
    # Sort line x-intervals
    intervals = sorted([(l.bbox[0], l.bbox[2]) for l in lines], key=lambda x: x[0])

    # Merge overlapping intervals
    merged: list[list[float]] = []
    for iv in intervals:
        if not merged:
            merged.append([iv[0], iv[1]])
        else:
            prev = merged[-1]
            if iv[0] <= prev[1] + 2.0:  # Allow 2pt noise tolerance
                prev[1] = max(prev[1], iv[1])
            else:
                merged.append([iv[0], iv[1]])

    # Gutters are gaps between merged intervals
    gutters: list[tuple[float, float]] = []
    for i in range(len(merged) - 1):
        g_start = merged[i][1]
        g_end = merged[i + 1][0]
        g_width = g_end - g_start
        if g_width >= min_gutter_width:
            gutters.append((round(g_start, 2), round(g_end, 2)))

    return gutters
