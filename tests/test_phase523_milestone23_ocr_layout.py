"""Milestone 2.3: Regression and Adversarial Tests for OCR Layout Compatibility.

Location: tests/test_phase523_milestone23_ocr_layout.py
Authority: Phase 5.2.3 Milestone 2.3
License: Apache-2.0 / MIT

Verifies layout-aware logical row reconstruction and regression repair:
1. Vertically formatted laboratory result (e.g. HAEMOGLOBIN\\n12.6\\nGM%).
2. Two independent laboratory rows (HAEMOGLOBIN vs POTASSIUM).
3. Two medication orders on separate lines (Metoprolol vs Lisinopril).
4. Flattened multi-column medication table.
5. Multiline evidence crossing independent medication rows (rejected fail-closed).
6. Multiline evidence crossing independent laboratory rows (rejected fail-closed).
7. Whitespace-normalized evidence with exact original source offsets.
8. Bounding-box preservation across lines after layout-aware grouping.
9. Permutation invariance of candidate extractions under layout-aware grouping.
"""
import itertools
import pytest

from clindoc.extract.contracts import BBoxModel, Evidence
from clindoc.extract.llm_extractor import MockLLMProvider, NarrativeExtractor
from clindoc.extract.segmentation import find_segment_idx, segment_clinical_text
from clindoc.ocr.document_text import DocumentPage, Token
from clindoc.ocr.geometry import BBoxDict


def _make_page_from_text(page_text: str) -> DocumentPage:
    """Helper creating a DocumentPage with spatial tokens for each word."""
    tokens = []
    y = 20.0
    line_num = 0
    for line in page_text.splitlines():
        line_id = f"L{line_num}"
        x = 10.0
        for word in line.split():
            w_len = len(word) * 8.0
            tokens.append(Token(
                text=word,
                bbox=BBoxDict(x0=x, y0=y, x1=x + w_len, y1=y + 15.0),
                line_id=line_id,
            ))
            x += w_len + 5.0
        y += 20.0
        line_num += 1
    return DocumentPage(page_number=1, width_px=612, height_px=792, tokens=tokens)


# ---------------------------------------------------------------------------
# 1. Vertically formatted laboratory result
# ---------------------------------------------------------------------------
def test_vertically_formatted_laboratory_result():
    page_text = "HAEMOGLOBIN\n12.6\nGM%"
    page = _make_page_from_text(page_text)
    segs = segment_clinical_text(page_text, page=page)
    # The 3 lines form a single logical laboratory finding
    assert len(segs) == 1
    assert segs[0].local_start == 0
    assert segs[0].local_end == len(page_text)

    canned = [
        {"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12.6 GM%", "unit_raw": "GM%"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_VLAB", page, page_text, "r0")

    assert len(claims) == 1
    assert len(evidences) == 1
    assert len(diag["rejected"]) == 0
    assert claims[0].value == "12.6"
    assert claims[0].unit_raw == "GM%"
    # Exact source evidence text preserves newlines
    assert evidences[0].text == "HAEMOGLOBIN\n12.6\nGM%"


# ---------------------------------------------------------------------------
# 2. Two independent laboratory rows
# ---------------------------------------------------------------------------
def test_two_independent_laboratory_rows():
    page_text = "HAEMOGLOBIN\n12.6\nGM%\nPOTASSIUM\n4.2\nmEq/L"
    page = _make_page_from_text(page_text)
    segs = segment_clinical_text(page_text, page=page)
    # Must separate into exactly 2 segments (one per lab finding)
    assert len(segs) == 2
    assert "HAEMOGLOBIN" in segs[0].text
    assert "POTASSIUM" in segs[1].text

    canned = [
        {"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12.6 GM%", "unit_raw": "GM%"},
        {"field_type": "lab.value", "value": "4.2", "evidence_text": "POTASSIUM 4.2 mEq/L", "unit_raw": "mEq/L"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_2LABS", page, page_text, "r0")

    assert len(claims) == 2
    assert len(evidences) == 2
    assert len(diag["rejected"]) == 0
    # Group IDs MUST be distinct: zero cross-laboratory association!
    assert claims[0].group_id != claims[1].group_id
    assert claims[0].value == "12.6"
    assert claims[1].value == "4.2"


# ---------------------------------------------------------------------------
# 3. Two medication orders on separate lines
# ---------------------------------------------------------------------------
def test_two_medication_orders_on_separate_lines():
    page_text = "Metoprolol 25 mg oral daily\nLisinopril 10 mg oral daily"
    page = _make_page_from_text(page_text)
    segs = segment_clinical_text(page_text, page=page)
    assert len(segs) == 2

    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_2MEDS", page, page_text, "r0")

    assert len(claims) == 4
    assert len(diag["rejected"]) == 0
    # Metoprolol and its dose share group 0
    assert claims[0].group_id == claims[1].group_id
    # Lisinopril and its dose share group 1
    assert claims[2].group_id == claims[3].group_id
    # Metoprolol and Lisinopril have distinct groups
    assert claims[0].group_id != claims[2].group_id


# ---------------------------------------------------------------------------
# 4. Flattened multi-column medication table
# ---------------------------------------------------------------------------
def test_flattened_multicolumn_medication_table():
    page_text = "Metoprolol    Lisinopril\n25 mg oral    10 mg oral"
    page = _make_page_from_text(page_text)
    segs = segment_clinical_text(page_text, page=page)
    # The newline separates the drug names from the doses
    assert len(segs) == 2

    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg"},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_FLAT_TBL", page, page_text, "r0")

    assert len(claims) == 4
    # Doses on line 2 must not be assigned to Metoprolol or Lisinopril (quarantined fail-closed)
    assert claims[2].group_id == "__no_anchor__"
    assert claims[3].group_id == "__no_anchor__"


# ---------------------------------------------------------------------------
# 5. Multiline evidence crossing independent medication rows (rejected fail-closed)
# ---------------------------------------------------------------------------
def test_multiline_evidence_crossing_independent_medication_rows():
    page_text = "Metoprolol 25 mg oral daily\nLisinopril 10 mg oral daily"
    page = _make_page_from_text(page_text)
    segs = segment_clinical_text(page_text, page=page)
    assert len(segs) == 2

    # Evidence span covers 'daily\nLisinopril 10 mg', crossing the row boundary
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg"},
        {"field_type": "med.dose", "value": "10", "evidence_text": "daily\nLisinopril 10 mg"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_CROSS_MED", page, page_text, "r0")

    # The cross-row extraction MUST be rejected
    assert len(diag["rejected"]) == 1
    assert "crosses segment boundary" in diag["rejected"][0]["reason"]
    # The valid extractions pass cleanly
    assert len(claims) == 3


# ---------------------------------------------------------------------------
# 6. Multiline evidence crossing independent laboratory rows (rejected fail-closed)
# ---------------------------------------------------------------------------
def test_multiline_evidence_crossing_independent_lab_rows():
    page_text = "HAEMOGLOBIN\n12.6\nGM%\nPOTASSIUM\n4.2\nmEq/L"
    page = _make_page_from_text(page_text)
    segs = segment_clinical_text(page_text, page=page)
    assert len(segs) == 2

    # Evidence span covers 'GM%\nPOTASSIUM', crossing the lab boundary
    canned = [
        {"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12.6 GM%", "unit_raw": "GM%"},
        {"field_type": "lab.value", "value": "99.9", "evidence_text": "GM%\nPOTASSIUM"},
        {"field_type": "lab.value", "value": "4.2", "evidence_text": "POTASSIUM 4.2 mEq/L", "unit_raw": "mEq/L"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_CROSS_LAB", page, page_text, "r0")

    assert len(diag["rejected"]) == 1
    assert "crosses segment boundary" in diag["rejected"][0]["reason"]
    assert len(claims) == 2
    assert claims[0].group_id != claims[1].group_id


# ---------------------------------------------------------------------------
# 7. Whitespace-normalized evidence with exact original source offsets
# ---------------------------------------------------------------------------
def test_whitespace_normalized_evidence_exact_offsets():
    page_text = "POTASSIUM  \t  4.2  \n\n  mEq/L"
    page = _make_page_from_text(page_text)
    canned = [
        {"field_type": "lab.value", "value": "4.2", "evidence_text": "POTASSIUM 4.2 mEq/L", "unit_raw": "mEq/L"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_WS_OFFSETS", page, page_text, "r0")

    assert len(claims) == 1
    assert len(evidences) == 1
    assert len(diag["rejected"]) == 0
    ev = evidences[0]
    # Exact verbatim source span with original whitespace
    assert ev.text == "POTASSIUM  \t  4.2  \n\n  mEq/L"
    assert ev.char_start == 0
    assert ev.char_end == len(page_text)
    assert page_text[ev.char_start:ev.char_end] == ev.text


# ---------------------------------------------------------------------------
# 8. Bounding-box preservation across lines after layout-aware grouping
# ---------------------------------------------------------------------------
def test_bounding_box_preservation_multiline():
    page_text = "HAEMOGLOBIN\n12.6\nGM%"
    page = _make_page_from_text(page_text)
    canned = [
        {"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12.6 GM%", "unit_raw": "GM%"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_BBOX", page, page_text, "r0")

    assert len(evidences) == 1
    ev = evidences[0]
    # Token 0 is at y0=20, token 2 is at y1=75
    assert ev.bbox.y0 <= 20.0
    assert ev.bbox.y1 >= 75.0
    assert ev.bbox.x0 == 10.0
    assert ev.bbox.x1 > ev.bbox.x0


# ---------------------------------------------------------------------------
# 9. Horizontal laboratory rows separated by newlines remain isolated
# ---------------------------------------------------------------------------
def test_horizontal_lab_rows_isolated():
    page_text = "GLUCOSE 95 mg/dL\nCREATININE 1.0 mg/dL"
    page = _make_page_from_text(page_text)
    segs = segment_clinical_text(page_text, page=page)
    assert len(segs) == 2

    canned = [
        {"field_type": "lab.value", "value": "95", "evidence_text": "GLUCOSE 95 mg/dL", "unit_raw": "mg/dL"},
        {"field_type": "lab.value", "value": "1.0", "evidence_text": "CREATININE 1.0 mg/dL", "unit_raw": "mg/dL"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evidences, claims, diag = extractor.extract("DOC_HORIZ_LABS", page, page_text, "r0")

    assert len(claims) == 2
    assert claims[0].group_id != claims[1].group_id


# ---------------------------------------------------------------------------
# 10. Permutation invariance of candidate extractions under OCR layout
# ---------------------------------------------------------------------------
def test_permutation_invariance_ocr_layout():
    page_text = "HAEMOGLOBIN\n12.6\nGM%\nPOTASSIUM\n4.2\nmEq/L"
    page = _make_page_from_text(page_text)

    base_candidates = [
        {"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12.6 GM%", "unit_raw": "GM%"},
        {"field_type": "lab.value", "value": "4.2", "evidence_text": "POTASSIUM 4.2 mEq/L", "unit_raw": "mEq/L"},
        # Cross-segment invalid extraction
        {"field_type": "lab.value", "value": "99.9", "evidence_text": "GM%\nPOTASSIUM"},
    ]

    results = []
    for perm in itertools.permutations(base_candidates):
        extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=list(perm)), max_retries=0)
        evs, claims, diags = extractor.extract("DOC_PERM", page, page_text, "r0")
        claim_sig = [(c.field_type, c.value, c.unit_raw) for c in claims]
        same_group = (claims[0].group_id == claims[1].group_id)
        rej_count = len(diags["rejected"])
        results.append((claim_sig, same_group, rej_count))

    first_res = results[0]
    for res in results[1:]:
        assert res == first_res, f"Permutation mismatch: {res} != {first_res}"
