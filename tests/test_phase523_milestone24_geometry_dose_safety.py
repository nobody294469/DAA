"""Milestone 2.4: Regression and Adversarial Tests for Geometry Fail-Closed & Medication Dose Hardening.

Location: tests/test_phase523_milestone24_geometry_dose_safety.py
Authority: Phase 5.2.3 Milestone 2.4
License: Apache-2.0 / MIT

Required adversarial tests:
1. Valid vertically aligned laboratory result with complete geometry.
2. Missing previous bounding box.
3. Missing current bounding box.
4. Both bounding boxes missing.
5. Inverted and malformed bounding boxes.
6. NaN and infinite coordinates.
7. Valid laboratory rows at different supported resolutions (72, 150, 300 DPI).
8. Paracetamol\\n500 with no unit (grounded, correct entity association, blocked from unreviewed export).
9. Paracetamol 500 mg with explicit unit.
10. Two adjacent medication orders (group isolation).
11. A laboratory result immediately followed by a medication.
12. Evidence crossing independent logical rows must still be rejected.
"""
from __future__ import annotations

import math
import pytest

from clindoc.db.enums import ClaimReviewState
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.extract.llm_extractor import MockLLMProvider, NarrativeExtractor
from clindoc.extract.segmentation import segment_clinical_text
from clindoc.ocr.document_text import DocumentPage, Token
from clindoc.ocr.geometry import BBoxDict
from clindoc.ocr.table_layout import (
    _validate_and_extract_bbox,
    is_vertical_lab_component_continuation,
)
from clindoc.services.fhir_export import FHIRExportService


def _make_page_from_lines(
    lines: list[tuple[str, float, float, float, float]],
    width_px: int = 612,
    height_px: int = 792,
) -> tuple[str, DocumentPage]:
    """Helper creating page_text and a DocumentPage from a list of (line_text, x0, y0, x1, y1)."""
    text_parts = [l[0] for l in lines]
    page_text = "\n".join(text_parts)

    tokens = []
    for line_idx, (line_text, x0, y0, x1, y1) in enumerate(lines):
        line_id = f"L{line_idx}"
        words = line_text.split()
        if not words:
            continue
        total_chars = max(sum(len(w) for w in words), 1)
        w_span = (x1 - x0) / len(words)
        curr_x = x0
        for w in words:
            tok_w = (len(w) / total_chars) * (x1 - x0)
            tokens.append(Token(
                text=w,
                bbox=BBoxDict(x0=round(curr_x, 2), y0=round(y0, 2), x1=round(curr_x + tok_w, 2), y1=round(y1, 2)),
                line_id=line_id,
            ))
            curr_x += tok_w + 2.0

    page = DocumentPage(page_number=1, width_px=width_px, height_px=height_px, tokens=tokens)
    return page_text, page


# ---------------------------------------------------------------------------
# 1. Valid vertically aligned laboratory result with complete geometry
# ---------------------------------------------------------------------------
def test_1_valid_vertically_aligned_lab_result_with_complete_geometry():
    """1. Valid vertically aligned laboratory result with complete, finite bounding boxes."""
    lines = [
        ("HAEMOGLOBIN", 10.0, 20.0, 110.0, 35.0),
        ("12.6", 10.0, 40.0, 50.0, 55.0),
        ("GM%", 10.0, 60.0, 50.0, 75.0),
    ]
    page_text, page = _make_page_from_lines(lines)
    segs = segment_clinical_text(page_text, page=page)

    # Must group all 3 lines into 1 cohesive laboratory finding
    assert len(segs) == 1
    assert segs[0].local_start == 0
    assert segs[0].local_end == len(page_text)

    canned = [
        {"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12.6 GM%", "unit_raw": "GM%"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_001", page, page_text, "r0")

    assert len(claims) == 1
    assert len(evidences) == 1
    assert len(diag["rejected"]) == 0
    assert claims[0].value == "12.6"
    assert claims[0].unit_raw == "GM%"
    assert evidences[0].text == "HAEMOGLOBIN\n12.6\nGM%"
    # Enclosing bounding box spans top of line 0 to bottom of line 2
    assert evidences[0].bbox.y0 <= 20.0
    assert evidences[0].bbox.y1 >= 75.0


# ---------------------------------------------------------------------------
# 2. Missing previous bounding box
# ---------------------------------------------------------------------------
def test_2_missing_previous_bounding_box():
    """2. Missing previous bounding box returns False (fail-closed, preserving newline)."""
    res = is_vertical_lab_component_continuation(
        "HAEMOGLOBIN",
        "12.6",
        prev_bbox=None,
        curr_bbox=[10.0, 40.0, 50.0, 55.0],
    )
    assert res is False


# ---------------------------------------------------------------------------
# 3. Missing current bounding box
# ---------------------------------------------------------------------------
def test_3_missing_current_bounding_box():
    """3. Missing current bounding box returns False (fail-closed, preserving newline)."""
    res = is_vertical_lab_component_continuation(
        "HAEMOGLOBIN",
        "12.6",
        prev_bbox=[10.0, 20.0, 110.0, 35.0],
        curr_bbox=None,
    )
    assert res is False


# ---------------------------------------------------------------------------
# 4. Both bounding boxes missing
# ---------------------------------------------------------------------------
def test_4_both_bounding_boxes_missing():
    """4. Both bounding boxes missing returns False; plain-text mode preserves newlines."""
    # Direct function check
    res = is_vertical_lab_component_continuation(
        "HAEMOGLOBIN",
        "12.6",
        prev_bbox=None,
        curr_bbox=None,
    )
    assert res is False

    # Plain text mode (page=None) cuts at newlines
    page_text = "HAEMOGLOBIN\n12.6\nGM%"
    segs = segment_clinical_text(page_text, page=None)
    assert len(segs) == 3


# ---------------------------------------------------------------------------
# 5. Inverted and malformed bounding boxes
# ---------------------------------------------------------------------------
def test_5_inverted_and_malformed_bounding_boxes():
    """5. Inverted and malformed bounding boxes are rejected fail-closed."""
    valid_box = [10.0, 40.0, 50.0, 55.0]

    # Inverted X (x0 > x1)
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [100.0, 20.0, 10.0, 35.0], valid_box) is False

    # Inverted Y (y0 > y1)
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [10.0, 35.0, 100.0, 20.0], valid_box) is False

    # Inverted vertical ordering between boxes (curr above prev)
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [10.0, 60.0, 100.0, 75.0], [10.0, 20.0, 50.0, 35.0]) is False

    # Malformed length (len != 4)
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [10.0, 20.0, 100.0], valid_box) is False
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [], valid_box) is False

    # Negative coordinates
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [-10.0, 20.0, 100.0, 35.0], valid_box) is False

    # Degenerate zero-width box
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [10.0, 20.0, 10.0, 35.0], valid_box) is False

    # Boolean values inside box (True evaluates to 1 in Python if unchecked)
    assert _validate_and_extract_bbox([True, 20.0, 100.0, 35.0]) is None


# ---------------------------------------------------------------------------
# 6. NaN and infinite coordinates
# ---------------------------------------------------------------------------
def test_6_nan_and_infinite_coordinates():
    """6. NaN and infinite coordinates in bounding boxes are rejected fail-closed."""
    valid_box = [10.0, 40.0, 50.0, 55.0]

    # NaN in prev_bbox
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [float("nan"), 20.0, 100.0, 35.0], valid_box) is False

    # +Infinity in prev_bbox
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [10.0, 20.0, float("inf"), 35.0], valid_box) is False

    # -Infinity in curr_bbox
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [10.0, 20.0, 100.0, 35.0], [float("-inf"), 40.0, 50.0, 55.0]) is False

    # NaN in curr_bbox
    assert is_vertical_lab_component_continuation("HAEMOGLOBIN", "12.6", [10.0, 20.0, 100.0, 35.0], [10.0, 40.0, 50.0, float("nan")]) is False


# ---------------------------------------------------------------------------
# 7. Valid laboratory rows at different supported resolutions
# ---------------------------------------------------------------------------
def test_7_valid_laboratory_rows_at_different_resolutions():
    """7. Adaptive resolution handling for 72 DPI, 150 DPI, and 300 DPI documents."""
    # 72 DPI (Standard PDF: line height ~15 px, gap ~5 px)
    assert is_vertical_lab_component_continuation(
        "HAEMOGLOBIN", "12.6",
        [10.0, 20.0, 100.0, 35.0],
        [10.0, 40.0, 50.0, 55.0],
    ) is True

    # 150 DPI (Mid-res scan: line height ~30 px, gap ~10 px)
    assert is_vertical_lab_component_continuation(
        "HAEMOGLOBIN", "12.6",
        [20.0, 40.0, 200.0, 70.0],
        [20.0, 80.0, 100.0, 110.0],
    ) is True

    # 300 DPI (High-res scan: line height ~60 px, gap ~20 px)
    assert is_vertical_lab_component_continuation(
        "HAEMOGLOBIN", "12.6",
        [40.0, 80.0, 400.0, 140.0],
        [40.0, 160.0, 200.0, 220.0],
    ) is True


# ---------------------------------------------------------------------------
# 8. Paracetamol\n500 with no unit
# ---------------------------------------------------------------------------
def test_8_paracetamol_500_with_no_unit():
    """8. Paracetamol\\n500: associates correctly, preserves evidence, blocked from unreviewed export."""
    lines = [
        ("Paracetamol", 10.0, 20.0, 100.0, 35.0),
        ("500", 10.0, 40.0, 40.0, 55.0),
    ]
    page_text, page = _make_page_from_lines(lines)

    canned = [
        {"field_type": "med.name", "value": "Paracetamol", "evidence_text": "Paracetamol"},
        {"field_type": "med.dose", "value": "500", "evidence_text": "500", "unit_raw": None},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_MED_001", page, page_text, "r0")

    assert len(claims) == 2
    med_name_claim = next(c for c in claims if c.field_type == "med.name")
    med_dose_claim = next(c for c in claims if c.field_type == "med.dose")

    # 1. Associated with the correct medication entity
    assert med_dose_claim.group_id == med_name_claim.group_id

    # 2. Source text and evidence preserved verbatim; unit is NOT guessed or invented
    assert med_dose_claim.value == "500"
    assert med_dose_claim.unit_raw is None
    dose_ev = next(e for e in evidences if e.evidence_id == med_dose_claim.evidence_id)
    assert dose_ev.text == "500"

    # 3. Export eligibility gate: unitless dose CANNOT become exportable without human review
    pending_eval = FHIRExportService.evaluate_claim_eligibility(
        med_dose_claim,
        ver={"label": "SUPPORTED", "is_fallback": False},
        ev=dose_ev,
        require_human_acceptance=True,
    )
    assert pending_eval.is_eligible is False
    assert "unit of measure" in pending_eval.reason.lower()

    # 4. With explicit human clinical review acceptance (Mode 0), it becomes exportable
    object.__setattr__(med_dose_claim, "review_state", ClaimReviewState.ACCEPTED)
    accepted_eval = FHIRExportService.evaluate_claim_eligibility(
        med_dose_claim,
        ver={"label": "SUPPORTED", "is_fallback": False},
        ev=dose_ev,
        require_human_acceptance=True,
    )
    assert accepted_eval.is_eligible is True


# ---------------------------------------------------------------------------
# 9. Paracetamol 500 mg with explicit unit
# ---------------------------------------------------------------------------
def test_9_paracetamol_500_mg_with_explicit_unit():
    """9. Paracetamol with explicit unit (500 mg) passes dose completeness gate."""
    lines = [
        ("Paracetamol 500 mg oral daily", 10.0, 20.0, 220.0, 35.0),
    ]
    page_text, page = _make_page_from_lines(lines)

    canned = [
        {"field_type": "med.name", "value": "Paracetamol", "evidence_text": "Paracetamol"},
        {"field_type": "med.dose", "value": "500", "unit_raw": "mg", "evidence_text": "500 mg"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_MED_002", page, page_text, "r0")

    dose_claim = next(c for c in claims if c.field_type == "med.dose")
    dose_ev = next(e for e in evidences if e.evidence_id == dose_claim.evidence_id)

    assert dose_claim.unit_raw == "mg"

    # With human acceptance, fully exportable
    object.__setattr__(dose_claim, "review_state", ClaimReviewState.ACCEPTED)
    eval_res = FHIRExportService.evaluate_claim_eligibility(
        dose_claim,
        ver={"label": "SUPPORTED", "is_fallback": False},
        ev=dose_ev,
        require_human_acceptance=True,
    )
    assert eval_res.is_eligible is True


# ---------------------------------------------------------------------------
# 10. Two adjacent medication orders
# ---------------------------------------------------------------------------
def test_10_two_adjacent_medication_orders():
    """10. Two adjacent medication orders remain strictly isolated across lines."""
    lines = [
        ("Metoprolol 25 mg oral daily", 10.0, 20.0, 220.0, 35.0),
        ("Lisinopril 10 mg oral daily", 10.0, 45.0, 210.0, 60.0),
    ]
    page_text, page = _make_page_from_lines(lines)
    segs = segment_clinical_text(page_text, page=page)
    assert len(segs) == 2

    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_MED_003", page, page_text, "r0")

    assert len(claims) == 4
    metop_name = next(c for c in claims if c.value == "Metoprolol")
    metop_dose = next(c for c in claims if c.value == "25")
    lisin_name = next(c for c in claims if c.value == "Lisinopril")
    lisin_dose = next(c for c in claims if c.value == "10")

    # Group IDs are bound to correct parents and disjoint from each other
    assert metop_dose.group_id == metop_name.group_id
    assert lisin_dose.group_id == lisin_name.group_id
    assert metop_name.group_id != lisin_name.group_id


# ---------------------------------------------------------------------------
# 11. A laboratory result immediately followed by a medication
# ---------------------------------------------------------------------------
def test_11_laboratory_result_immediately_followed_by_medication():
    """11. A laboratory result followed by a medication order maintains cross-domain boundary."""
    lines = [
        ("HAEMOGLOBIN", 10.0, 20.0, 110.0, 35.0),
        ("12.6", 10.0, 40.0, 50.0, 55.0),
        ("GM%", 10.0, 60.0, 50.0, 75.0),
        ("Paracetamol 500 mg oral", 10.0, 85.0, 200.0, 100.0),
    ]
    page_text, page = _make_page_from_lines(lines)
    segs = segment_clinical_text(page_text, page=page)
    # Seg 0 is HAEMOGLOBIN\n12.6\nGM%, Seg 1 is \nParacetamol 500 mg oral
    assert len(segs) == 2
    assert "HAEMOGLOBIN" in segs[0].text
    assert "Paracetamol" in segs[1].text

    canned = [
        {"field_type": "lab.value", "value": "12.6", "unit_raw": "GM%", "evidence_text": "HAEMOGLOBIN 12.6 GM%"},
        {"field_type": "med.name", "value": "Paracetamol", "evidence_text": "Paracetamol"},
        {"field_type": "med.dose", "value": "500", "unit_raw": "mg", "evidence_text": "500 mg"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_MIXED", page, page_text, "r0")

    lab_claim = next(c for c in claims if c.field_type == "lab.value")
    med_name = next(c for c in claims if c.field_type == "med.name")
    med_dose = next(c for c in claims if c.field_type == "med.dose")

    assert med_dose.group_id == med_name.group_id
    assert lab_claim.group_id != med_name.group_id


# ---------------------------------------------------------------------------
# 12. Evidence crossing independent logical rows must still be rejected
# ---------------------------------------------------------------------------
def test_12_evidence_crossing_independent_logical_rows_rejected():
    """12. Evidence span crossing independent logical rows is rejected fail-closed."""
    lines = [
        ("HAEMOGLOBIN", 10.0, 20.0, 110.0, 35.0),
        ("12.6", 10.0, 40.0, 50.0, 55.0),
        ("GM%", 10.0, 60.0, 50.0, 75.0),
        ("POTASSIUM", 10.0, 85.0, 100.0, 100.0),
        ("4.2", 10.0, 105.0, 40.0, 120.0),
        ("mEq/L", 10.0, 125.0, 55.0, 140.0),
    ]
    page_text, page = _make_page_from_lines(lines)
    segs = segment_clinical_text(page_text, page=page)
    assert len(segs) == 2

    # Canned extraction with evidence text crossing from GM% into POTASSIUM
    canned = [
        {"field_type": "lab.value", "value": "12.6", "unit_raw": "GM%", "evidence_text": "HAEMOGLOBIN 12.6 GM%"},
        {"field_type": "lab.value", "value": "99.9", "evidence_text": "GM%\nPOTASSIUM"},
        {"field_type": "lab.value", "value": "4.2", "unit_raw": "mEq/L", "evidence_text": "POTASSIUM 4.2 mEq/L"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_CROSS", page, page_text, "r0")

    assert len(diag["rejected"]) == 1
    assert "crosses segment boundary" in diag["rejected"][0]["reason"]
    assert len(claims) == 2
    assert claims[0].group_id != claims[1].group_id
