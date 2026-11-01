"""Deterministic sentence and clause boundary segmentation for clinical narrative extraction.

Location: src/clindoc/extract/segmentation.py
Authority: Phase 5.2.3 Milestone 2
License: Apache-2.0 / MIT

Provides deterministic sentence and clause segmentation for clinical narrative text:
1. Preserves original text and exact verbatim character offsets.
2. Identifies sentence boundaries (. ! ? followed by whitespace, uppercase, or EOF).
3. Identifies clause delimiters (semicolons ; and line breaks \\n).
4. Protects decimal values (e.g., 2.5 mg) from being split.
5. Protects common clinical abbreviations (e.g., Tab., Cap., p.o., b.i.d., mg., etc.) from being split.
6. Recognizes numbered list items (1. , 2) , etc.) and bullet structures (- , * , • ).
7. Isolates headings and safe empty segment handling.
8. Retains [local_start, local_end] character coordinates for every segment.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Final


@dataclass(frozen=True)
class ClinicalSegment:
    """A segmented clause or sentence with exact character coordinates in region_text."""
    segment_idx: int
    text: str
    local_start: int
    local_end: int
    delimiter_type: str


# Comprehensive set of clinical abbreviations where a period does NOT denote a sentence end.
CLINICAL_ABBREVIATIONS: Final[frozenset[str]] = frozenset({
    # Dosage forms & preparations
    "tab", "tabs", "cap", "caps", "syp", "syr", "inj", "sol", "susp", "oint", "crm", "supp", "amp", "vial",
    # Routes & Frequencies
    "po", "p.o", "pr", "p.r", "iv", "i.v", "im", "i.m", "sc", "s.c", "subq", "sq", "id", "i.d", "top", "sl", "s.l",
    "qd", "q.d", "bid", "b.i.d", "tid", "t.i.d", "qid", "q.i.d", "qhs", "q.h.s", "qam", "q.a.m", "qpm", "q.p.m",
    "prn", "p.r.n", "stat", "q4h", "q6h", "q8h", "q12h", "q24h", "qod", "q.o.d",
    # Units
    "mg", "g", "gm", "mcg", "ug", "kg", "ml", "cc", "l", "meq", "m.eq", "iu", "u", "oz", "tbsp", "tsp", "gtt",
    # Professional titles & references
    "dr", "mr", "mrs", "ms", "prof", "vs", "approx", "no", "vol", "wt", "ht", "ref", "dept", "sr", "jr",
    # Clinical common
    "rx", "dx", "hx", "tx", "sx", "pt", "yo", "m.d", "d.o", "r.n", "l.p.n",
})

# Units where a period is commonly used
UNIT_ABBREVIATIONS: Final[frozenset[str]] = frozenset({
    "mg", "g", "gm", "mcg", "ug", "kg", "ml", "cc", "l", "meq", "m.eq", "iu", "u", "oz", "tbsp", "tsp", "gtt",
})

# Established route and frequency abbreviations that frequently follow a unit abbreviation without ending the sentence
FOLLOWING_ROUTE_FREQ_TOKENS: Final[frozenset[str]] = frozenset({
    "po", "iv", "im", "sc", "sq", "subq", "id", "sl", "pr", "top",
    "prn", "bid", "tid", "qid", "qd", "qhs", "qam", "qpm", "stat",
    "q4h", "q6h", "q8h", "q12h", "q24h", "qod",
})

# Tokens that indicate the start of a new clinical clause or statement even when lowercased
CLAUSE_START_TOKENS: Final[frozenset[str]] = frozenset({
    "patient", "pt", "she", "he", "they", "switched", "started", "prescribed",
    "discontinued", "hold", "stopped", "added", "ordered", "advised", "recommended",
    "continue", "resume", "decrease", "increase", "titrate",
})


def _is_clinical_abbreviation(token: str) -> bool:
    """Check if token stripped of period is in clinical abbreviation list."""
    cleaned = token.rstrip(".").lower()
    return cleaned in CLINICAL_ABBREVIATIONS


from clindoc.ocr.geometry import enclose_bboxes
from clindoc.ocr.table_layout import is_vertical_lab_component_continuation


def _resolve_line_bbox(page: Any, char_start: int, char_end: int, line_text: str) -> list[float] | None:
    """Resolve bounding box for a line span, robust to whitespace normalization differences."""
    if page is None:
        return None

    words = line_text.strip().split()
    if not words:
        return None

    # Check whether character offset lookup is trustworthy (i.e. page_text at span matches line_text)
    if hasattr(page, "get_page_text") and hasattr(page, "get_bbox_for_span"):
        try:
            p_text = page.get_page_text()
            if p_text and len(p_text) >= char_end and p_text[char_start:char_end].strip() == line_text.strip():
                bbox = page.get_bbox_for_span(char_start, char_end)
                if bbox is not None:
                    return bbox
        except Exception:
            pass

    # Word-to-token matching from page.tokens (handles whitespace discrepancies between text and page)
    if hasattr(page, "tokens") and page.tokens:
        matching_boxes = []
        w_idx = 0
        for tok in page.tokens:
            tok_clean = tok.text.strip()
            if not tok_clean:
                continue
            if tok_clean == words[w_idx]:
                matching_boxes.append(tok.bbox_obj if hasattr(tok, "bbox_obj") else tok.bbox)
                w_idx += 1
                if w_idx == len(words):
                    break
            elif w_idx > 0:
                matching_boxes = []
                w_idx = 0
                if tok_clean == words[0]:
                    matching_boxes.append(tok.bbox_obj if hasattr(tok, "bbox_obj") else tok.bbox)
                    w_idx = 1

        if len(matching_boxes) == len(words):
            enclosing = enclose_bboxes(matching_boxes)
            if enclosing is not None:
                return enclosing.to_list()

    if hasattr(page, "get_bbox_for_span"):
        return page.get_bbox_for_span(char_start, char_end)

    return None


def segment_clinical_text(
    text: str,
    page: Any | None = None,
    char_offset: int = 0,
) -> list[ClinicalSegment]:
    """Segment clinical narrative text into deterministic sentence and clause spans.

    Args:
        text: Input clinical narrative region text.
        page: Optional DocumentPage providing OCR bounding box geometry.
        char_offset: Character offset of region_text in the page.

    Returns:
        List of non-overlapping ClinicalSegment objects in document order,
        each with exact [local_start, local_end] character coordinates in `text`.
    """
    if not text:
        return []

    cuts: list[tuple[int, str]] = []

    # 1. Newlines (\r?\n): Clause/line boundary unless layout-aware grouping confirms
    # a shared vertical laboratory result component (Phase 5.2.3 Milestone 2.3)
    for m in re.finditer(r"\r?\n+", text):
        nl_start = m.start()
        nl_end = m.end()

        # Extract preceding line
        prev_nl = text.rfind("\n", 0, nl_start)
        prev_line_start = 0 if prev_nl == -1 else (prev_nl + 1)
        prev_line_text = text[prev_line_start:nl_start]

        # Extract succeeding line
        next_nl = text.find("\n", nl_end)
        next_line_end = len(text) if next_nl == -1 else next_nl
        curr_line_text = text[nl_end:next_line_end]

        # Compute bounding boxes if page is provided
        prev_bbox = _resolve_line_bbox(page, char_offset + prev_line_start, char_offset + nl_start, prev_line_text)
        curr_bbox = _resolve_line_bbox(page, char_offset + nl_end, char_offset + next_line_end, curr_line_text)

        if is_vertical_lab_component_continuation(
            prev_line_text,
            curr_line_text,
            prev_bbox=prev_bbox,
            curr_bbox=curr_bbox,
        ):
            continue  # Do not cut: shared logical laboratory finding

        cuts.append((nl_start, "newline"))

    # 2. Semicolons (;): Always a clause boundary
    for m in re.finditer(r";+", text):
        cuts.append((m.end(), "semicolon"))

    # 3. Sentence terminators (. ! ?) followed by whitespace, uppercase letter, or EOF
    for m in re.finditer(r"([.!?]+)(?=\s|[A-Z]|$)", text):
        punct = m.group(1)
        punct_start = m.start(1)
        punct_end = m.end(1)

        # Decimal numbers: period between digits (e.g., 2.5) is never a boundary
        if "." in punct:
            if punct_start > 0 and punct_end < len(text):
                if text[punct_start - 1].isdigit() and text[punct_end].isdigit():
                    continue

            prefix = text[:punct_start]

            # Single letter abbreviation attached directly to uppercase word (e.g. C.Autrin, T.Shelcal)
            if re.search(r"\b[A-Za-z]\Z", prefix) and punct_end < len(text):
                if text[punct_end].isupper():
                    continue

            # Clinical abbreviations: check preceding token
            word_match = re.search(r"([A-Za-z.]+)\Z", prefix)
            if word_match:
                preceding_token = word_match.group(1)
                pt_lower = preceding_token.rstrip(".").lower()
                if _is_clinical_abbreviation(preceding_token):
                    rest = text[punct_end:].lstrip(" \t")
                    # If followed by newline or EOF, it terminates the sentence/entry
                    if not rest or rest.startswith("\n") or rest.startswith("\r"):
                        pass
                    else:
                        next_word_m = re.match(r"([A-Za-z0-9]+)", rest)
                        if next_word_m:
                            next_word = next_word_m.group(1)
                            next_word_lower = next_word.lower()

                            # 1. Unit abbreviation followed by established route/frequency token (e.g. '10 mg. PO daily', '10 mg. IV stat')
                            if pt_lower in UNIT_ABBREVIATIONS and next_word_lower in FOLLOWING_ROUTE_FREQ_TOKENS:
                                continue

                            # 2. Form/title abbreviations followed by uppercase (e.g., 'Tab. Drug', 'Dr. Smith')
                            if pt_lower in ("tab", "cap", "syp", "inj", "dr", "mr", "mrs", "ms"):
                                if next_word[0].isupper():
                                    continue

                            # 3. If followed by lowercase word:
                            if next_word[0].islower():
                                # 3a. Lowercase clause-starting subject or action verb: genuine sentence boundary
                                if next_word_lower in CLAUSE_START_TOKENS:
                                    pass  # split at punct_end
                                # 3b. Lowercase modifier immediately followed by capitalized token (e.g. 'oral Metoprolol was added')
                                elif re.match(r"[a-z]+\s+([A-Z][A-Za-z0-9]*)", rest):
                                    pass  # split at punct_end
                                else:
                                    # Regular lowercase continuation is inline (e.g., '25 mg. orally daily')
                                    continue

                            # 4. If followed by digits: inline reference (e.g. 'ref. 1234')
                            elif next_word[0].isdigit():
                                continue

                            # 5. Non-whitelisted uppercase word following unit/abbrev: genuine sentence boundary (e.g. '10 mg. Metoprolol was added')
                            # falls through to cuts.append((punct_end, "sentence_terminator"))

            # Numbered list marker prefix (e.g., '^1.' or '\n2.') is part of the marker, not a sentence end
            list_prefix = re.search(r"(?:^|[\r\n])\s*\d+\Z", prefix)
            if list_prefix:
                continue

        cuts.append((punct_end, "sentence_terminator"))

    # 4. Numbered list item starts: cut before inline item markers (e.g., ' 1. ' or ' 2) ')
    for m in re.finditer(r"(?<=\s)(\d+[\.\)]|[-*•])(?=\s+[A-Za-z])", text):
        cuts.append((m.start(), "list_marker"))

    # 5. Colons following uppercase section/item headers (e.g., 'MEDICATIONS:\n' or 'DISCHARGE:')
    for m in re.finditer(r"(?:^|[\r\n])\s*([A-Z\s]{3,35}:)(?=\s|$)", text):
        cuts.append((m.end(), "heading_colon"))

    # Deduplicate and sort cut coordinates within bounds (0, len(text))
    valid_cuts = sorted(set(offset for offset, _ in cuts if 0 < offset < len(text)))

    segments: list[ClinicalSegment] = []
    current_start = 0

    for cut in valid_cuts:
        if cut > current_start:
            seg_text = text[current_start:cut]
            # Emit segment if it contains word characters (skip empty / punctuation-only spans)
            if re.search(r"\w", seg_text):
                segments.append(ClinicalSegment(
                    segment_idx=len(segments),
                    text=seg_text,
                    local_start=current_start,
                    local_end=cut,
                    delimiter_type="boundary",
                ))
            current_start = cut

    # Final segment
    if current_start < len(text):
        seg_text = text[current_start:]
        if re.search(r"\w", seg_text):
            segments.append(ClinicalSegment(
                segment_idx=len(segments),
                text=seg_text,
                local_start=current_start,
                local_end=len(text),
                delimiter_type="eof",
            ))

    return segments


def find_segment_idx(ls: int, le: int, segments: list[ClinicalSegment]) -> int | None:
    """Find the segment index covering the given [ls, le] character span.

    Requires strict containment: the extraction span must lie completely inside
    exactly one valid segment. Spans crossing segment boundaries, inverted spans,
    or spans outside bounds return None.

    Args:
        ls: Span local start.
        le: Span local end.
        segments: Ordered list of ClinicalSegment instances.

    Returns:
        Segment index (0-based) strictly containing [ls, le], or None if invalid or crossing boundaries.
    """
    if not segments:
        return None

    if ls < 0 or le <= ls:
        return None

    for seg in segments:
        if seg.local_start <= ls and le <= seg.local_end:
            return seg.segment_idx

    return None
