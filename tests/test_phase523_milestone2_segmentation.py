"""Milestone 2: Mandatory Adversarial Tests for Sentence and Clause Boundary Segmentation.

Tests all 12 required adversarial scenarios:
1. Two different medications in consecutive sentences.
2. A medication attribute at the beginning of a new sentence.
3. Multiple medications separated by semicolons.
4. Decimal medication doses.
5. Clinical abbreviations containing periods.
6. Numbered medication lists.
7. Multiline medication tables or OCR-like text.
8. Empty segments and repeated delimiters.
9. Evidence spans immediately before and after boundaries.
10. Mixed medication and laboratory claims.
11. Multiple clauses within a sentence.
12. A medication mentioned in a heading followed by a separate medication statement.

Asserts:
- Correct segment boundaries.
- Exact source offsets.
- No cross-boundary group contamination.
- Existing evidence grounding remains intact.
- Deterministic behavior under candidate input permutations.
"""

import itertools
import pytest
from clindoc.extract.llm_extractor import NarrativeExtractor, MockLLMProvider
from clindoc.extract.segmentation import segment_clinical_text, find_segment_idx
from clindoc.ocr.document_text import DocumentPage, Token
from clindoc.ocr.geometry import BBoxDict


def _make_page(text: str) -> DocumentPage:
    tokens = []
    x, y = 10.0, 20.0
    for line in text.splitlines():
        for word in line.split():
            tokens.append(Token(text=word, bbox=BBoxDict(x0=x, y0=y, x1=x+40, y1=y+15), line_id="L0"))
            x += 45.0
        y += 20.0
        x = 10.0
    return DocumentPage(page_number=1, width_px=612, height_px=792, tokens=tokens)


# ---------------------------------------------------------------------------
# 1. Two different medications in consecutive sentences
# ---------------------------------------------------------------------------
def test_scenario_01_two_meds_consecutive_sentences():
    text = "Metoprolol 25 mg daily. Lisinopril 10 mg daily."
    segs = segment_clinical_text(text)
    assert len(segs) == 2
    assert segs[0].local_start == 0 and segs[0].local_end == 23
    assert segs[1].local_start == 23 and segs[1].local_end == 47

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 11, "end": 16},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 24, "end": 34},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg", "start": 35, "end": 40},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S1", _make_page(text), text, "r0")

    assert len(claims) == 4
    # Metoprolol and its dose share group
    assert claims[0].group_id == claims[1].group_id
    # Lisinopril and its dose share group
    assert claims[2].group_id == claims[3].group_id
    # Zero cross-sentence leakage
    assert claims[0].group_id != claims[2].group_id


# ---------------------------------------------------------------------------
# 2. Medication attribute at the beginning of a new sentence
# ---------------------------------------------------------------------------
def test_scenario_02_attribute_at_beginning_of_new_sentence():
    text = "Patient was taking Lisinopril 10 mg. Oral Metoprolol 25 mg was added."
    segs = segment_clinical_text(text)
    assert len(segs) == 2

    exts = [
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 19, "end": 29},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg", "start": 30, "end": 35},
        {"field_type": "med.route", "value": "oral", "evidence_text": "Oral", "start": 37, "end": 41},
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 42, "end": 52},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 53, "end": 58},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S2", _make_page(text), text, "r0")

    lis_claim = next(c for c in claims if c.field_type == "med.name" and c.value == "Lisinopril")
    oral_claim = next(c for c in claims if c.field_type == "med.route" and c.value == "oral")
    metop_claim = next(c for c in claims if c.field_type == "med.name" and c.value == "Metoprolol")

    # Oral MUST NOT leak into Lisinopril group!
    assert oral_claim.group_id != lis_claim.group_id
    # In Milestone 2 forward stream, Oral without prior anchor in sentence 2 is quarantined as __no_anchor__
    assert oral_claim.group_id == "__no_anchor__"


# ---------------------------------------------------------------------------
# 3. Multiple medications separated by semicolons
# ---------------------------------------------------------------------------
def test_scenario_03_medications_separated_by_semicolons():
    text = "Metoprolol 25 mg; Lisinopril 10 mg; Aspirin 81 mg"
    segs = segment_clinical_text(text)
    assert len(segs) == 3

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 11, "end": 16},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 18, "end": 28},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg", "start": 29, "end": 34},
        {"field_type": "med.name", "value": "Aspirin", "evidence_text": "Aspirin", "start": 36, "end": 43},
        {"field_type": "med.dose", "value": "81", "unit_raw": "mg", "evidence_text": "81 mg", "start": 44, "end": 49},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S3", _make_page(text), text, "r0")

    assert len(claims) == 6
    g1 = claims[0].group_id
    g2 = claims[2].group_id
    g3 = claims[4].group_id

    assert g1 == claims[1].group_id
    assert g2 == claims[3].group_id
    assert g3 == claims[5].group_id

    # Complete pairwise isolation across semicolons
    assert len({g1, g2, g3}) == 3


# ---------------------------------------------------------------------------
# 4. Decimal medication doses
# ---------------------------------------------------------------------------
def test_scenario_04_decimal_medication_doses():
    text = "Patient prescribed Metoprolol 2.5 mg daily and Amlodipine 0.5 mg."
    segs = segment_clinical_text(text)
    # Decimal point must NOT create segment boundaries
    assert len(segs) == 1

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 19, "end": 29},
        {"field_type": "med.dose", "value": "2.5", "unit_raw": "mg", "evidence_text": "2.5 mg", "start": 30, "end": 36},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S4", _make_page(text), text, "r0")

    assert len(claims) == 2
    assert claims[0].group_id == claims[1].group_id
    assert claims[1].value_numeric == 2.5


# ---------------------------------------------------------------------------
# 5. Clinical abbreviations containing periods
# ---------------------------------------------------------------------------
def test_scenario_05_clinical_abbreviations_containing_periods():
    text = "Take Tab. Metoprolol 25 mg p.o. b.i.d. daily."
    segs = segment_clinical_text(text)
    # Abbreviations Tab., p.o., b.i.d. must NOT split sentence
    assert len(segs) == 1

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 10, "end": 20},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 21, "end": 26},
        {"field_type": "med.route", "value": "oral", "evidence_text": "p.o.", "start": 27, "end": 31},
        {"field_type": "med.frequency", "value": "b.i.d.", "evidence_text": "b.i.d.", "start": 32, "end": 38},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S5", _make_page(text), text, "r0")

    assert len(claims) == 4
    gid = claims[0].group_id
    assert all(c.group_id == gid for c in claims)
    assert gid != "__no_anchor__"


# ---------------------------------------------------------------------------
# 6. Numbered medication lists
# ---------------------------------------------------------------------------
def test_scenario_06_numbered_medication_lists():
    text = "1. Metoprolol 50 mg daily\n2. Lisinopril 20 mg daily\n3. Aspirin 81 mg daily"
    segs = segment_clinical_text(text)
    assert len(segs) == 3

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 3, "end": 13},
        {"field_type": "med.dose", "value": "50", "unit_raw": "mg", "evidence_text": "50 mg", "start": 14, "end": 19},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 29, "end": 39},
        {"field_type": "med.dose", "value": "20", "unit_raw": "mg", "evidence_text": "20 mg", "start": 40, "end": 45},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S6", _make_page(text), text, "r0")

    assert len(claims) == 4
    assert claims[0].group_id == claims[1].group_id
    assert claims[2].group_id == claims[3].group_id
    assert claims[0].group_id != claims[2].group_id


# ---------------------------------------------------------------------------
# 7. Multiline medication tables or OCR-like text
# ---------------------------------------------------------------------------
def test_scenario_07_multiline_tables_ocr_text():
    text = "Metoprolol 25 mg oral\nLisinopril 10 mg oral\nAspirin 81 mg oral"
    segs = segment_clinical_text(text)
    assert len(segs) == 3

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 11, "end": 16},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 22, "end": 32},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg", "start": 33, "end": 38},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S7", _make_page(text), text, "r0")

    assert len(claims) == 4
    assert claims[0].group_id != claims[2].group_id


# ---------------------------------------------------------------------------
# 8. Empty segments and repeated delimiters
# ---------------------------------------------------------------------------
def test_scenario_08_empty_segments_and_repeated_delimiters():
    text = "Metoprolol 25 mg...\n\n;;  \nLisinopril 10 mg."
    segs = segment_clinical_text(text)
    # Repeated ellipsis, newlines, and semicolons safely collapsed into 2 valid segments
    assert len(segs) == 2

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 30, "end": 40},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S8", _make_page(text), text, "r0")

    assert len(claims) == 2
    assert claims[0].group_id != claims[1].group_id


# ---------------------------------------------------------------------------
# 9. Evidence spans immediately before and after boundaries
# ---------------------------------------------------------------------------
def test_scenario_09_spans_immediately_adjacent_to_boundaries():
    text = "Metoprolol 25 mg.Lisinopril 10 mg"
    segs = segment_clinical_text(text)
    assert len(segs) == 2

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 11, "end": 16},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 17, "end": 27},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg", "start": 28, "end": 33},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S9", _make_page(text), text, "r0")

    assert len(claims) == 4
    assert claims[0].group_id == claims[1].group_id
    assert claims[2].group_id == claims[3].group_id
    assert claims[0].group_id != claims[2].group_id


# ---------------------------------------------------------------------------
# 10. Mixed medication and laboratory claims
# ---------------------------------------------------------------------------
def test_scenario_10_mixed_medication_and_laboratory_claims():
    text = "Glucose was 95 mg/dL. Metoprolol 25 mg prescribed."
    segs = segment_clinical_text(text)
    assert len(segs) == 2

    exts = [
        {"field_type": "lab.value", "value": "95", "evidence_text": "95", "start": 12, "end": 14},
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 22, "end": 32},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 33, "end": 38},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S10", _make_page(text), text, "r0")

    assert len(claims) == 3
    # Lab value in seg 0
    assert claims[0].field_type == "lab.value"
    # Med claims in seg 1 share distinct med group
    assert claims[1].group_id == claims[2].group_id
    assert claims[0].group_id != claims[1].group_id


# ---------------------------------------------------------------------------
# 11. Multiple clauses within a sentence
# ---------------------------------------------------------------------------
def test_scenario_11_multiple_clauses_within_sentence():
    text = "Patient took Metoprolol 25 mg; however, Lisinopril 10 mg was stopped."
    segs = segment_clinical_text(text)
    assert len(segs) == 2

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 13, "end": 23},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 24, "end": 29},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 40, "end": 50},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg", "start": 51, "end": 56},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S11", _make_page(text), text, "r0")

    assert len(claims) == 4
    # Metoprolol and Lisinopril separated by semicolon are isolated
    assert claims[0].group_id == claims[1].group_id
    assert claims[2].group_id == claims[3].group_id
    assert claims[0].group_id != claims[2].group_id


# ---------------------------------------------------------------------------
# 12. Medication in heading followed by separate statement
# ---------------------------------------------------------------------------
def test_scenario_12_medication_in_heading_followed_by_separate_statement():
    text = "CURRENT MEDICATIONS:\nMetoprolol 25 mg daily\nDISCHARGE:\nLisinopril 10 mg"
    segs = segment_clinical_text(text)
    assert len(segs) >= 2

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 21, "end": 31},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 32, "end": 37},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 55, "end": 65},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg", "start": 66, "end": 71},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_S12", _make_page(text), text, "r0")

    assert len(claims) == 4
    assert claims[0].group_id == claims[1].group_id
    assert claims[2].group_id == claims[3].group_id
    assert claims[0].group_id != claims[2].group_id


# ---------------------------------------------------------------------------
# Permutation Invariance Test Across Multi-Sentence Scenarios
# ---------------------------------------------------------------------------
def test_permutation_invariance_across_sentences():
    text = "Metoprolol 25 mg daily. Lisinopril 10 mg daily."
    base_exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 11, "end": 16},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 24, "end": 34},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg", "start": 35, "end": 40},
    ]
    page = _make_page(text)

    baseline_claims = None
    baseline_evs = None

    for perm in itertools.permutations(base_exts):
        extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=list(perm)), max_retries=0)
        evs, claims, diags = extractor.extract("DOC_PERM_S", page, text, "r0")

        g_map = {}
        norm_claims = []
        for c in claims:
            if c.group_id not in g_map:
                g_map[c.group_id] = f"G_{len(g_map)}"
            norm_claims.append((c.field_type, c.value, c.unit_raw, g_map[c.group_id]))

        norm_evs = [(e.text, e.char_start, e.char_end) for e in evs]

        if baseline_claims is None:
            baseline_claims = norm_claims
            baseline_evs = norm_evs
        else:
            assert norm_claims == baseline_claims
            assert norm_evs == baseline_evs
