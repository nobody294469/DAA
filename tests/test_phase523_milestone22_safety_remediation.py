"""Milestone 2.2: Regression Tests for Critical Segmentation Safety Remediation.

Covers:
1. Cross-segment evidence spanning a semicolon.
2. Cross-segment evidence spanning a sentence boundary.
3. Empty and inverted spans.
4. Negative and out-of-range offsets.
5. Valid spans inside each segment.
6. Valid spans ending exactly at segment boundaries.
7. '10 mg. PO daily' (Unit + route/freq abbreviation).
8. '10 mg. IV stat' (Unit + route/freq abbreviation).
9. Genuine sentence boundaries after 'mg.'.
10. Lowercase sentence beginnings ('oral Metoprolol was added').
11. Clinical abbreviations followed by genuine new sentences.
12. Candidate permutation invariance under cross-segment rejections and boundary isolation.

Asserts both diagnostics["rejected"] and group_id assignments.
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
# 1. Cross-segment evidence spanning a semicolon
# ---------------------------------------------------------------------------
def test_cross_segment_evidence_spanning_semicolon():
    text = "Metoprolol 25 mg; Lisinopril 10 mg"
    segs = segment_clinical_text(text)
    assert len(segs) == 2

    # Evidence span [11:28] covers '25 mg; Lisinopril'
    assert find_segment_idx(11, 28, segs) is None

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
        {"field_type": "med.dose", "value": "25 mg; Lisinopril", "evidence_text": "25 mg; Lisinopril", "start": 11, "end": 28},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 18, "end": 28},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_CROSS_SEMI", _make_page(text), text, "r0")

    # The cross-segment extraction MUST be rejected
    rejected_reasons = [r["reason"] for r in diags["rejected"]]
    assert any("crosses segment boundary" in r for r in rejected_reasons)

    # Only the valid extractions become claims
    claim_fields = [c.field_type for c in claims]
    assert "med.dose" not in claim_fields
    assert claim_fields == ["med.name", "med.name"]
    # Distinct med names have distinct group IDs
    assert claims[0].group_id != claims[1].group_id


# ---------------------------------------------------------------------------
# 2. Cross-segment evidence spanning a sentence boundary
# ---------------------------------------------------------------------------
def test_cross_segment_evidence_spanning_sentence_boundary():
    text = "Lisinopril 10 mg. Metoprolol 25 mg."
    segs = segment_clinical_text(text)
    assert len(segs) == 2

    # Evidence span [11:28] covers '10 mg. Metoprolol' across the period
    assert find_segment_idx(11, 28, segs) is None

    exts = [
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 0, "end": 10},
        {"field_type": "med.dose", "value": "10 mg. Metoprolol", "evidence_text": "10 mg. Metoprolol", "start": 11, "end": 28},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_CROSS_SENT", _make_page(text), text, "r0")

    # Rejected due to cross-segment boundary
    assert len(diags["rejected"]) == 1
    assert "crosses segment boundary" in diags["rejected"][0]["reason"]
    # Only Lisinopril becomes a claim
    assert len(claims) == 1
    assert claims[0].value == "Lisinopril"


# ---------------------------------------------------------------------------
# 3. Empty and inverted spans
# ---------------------------------------------------------------------------
def test_empty_and_inverted_spans():
    text = "Metoprolol 25 mg daily."
    segs = segment_clinical_text(text)
    assert len(segs) == 1

    # Empty span [5, 5]
    assert find_segment_idx(5, 5, segs) is None
    # Inverted span [10, 5]
    assert find_segment_idx(10, 5, segs) is None

    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "", "local_start": 5, "local_end": 5},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25", "local_start": 10, "local_end": 5},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_EMPTY_INV", _make_page(text), text, "r0")

    # Inverted and empty spans are rejected
    assert len(claims) == 0
    assert len(diags["rejected"]) == 2


# ---------------------------------------------------------------------------
# 4. Negative and out-of-range offsets
# ---------------------------------------------------------------------------
def test_negative_and_out_of_range_offsets():
    text = "Metoprolol 25 mg daily."
    segs = segment_clinical_text(text)
    assert len(segs) == 1

    # Negative coordinates
    assert find_segment_idx(-5, 5, segs) is None
    assert find_segment_idx(-10, -5, segs) is None
    # Beyond length of text
    assert find_segment_idx(10, 100, segs) is None
    assert find_segment_idx(100, 110, segs) is None


# ---------------------------------------------------------------------------
# 5. Valid spans inside each segment
# ---------------------------------------------------------------------------
def test_valid_spans_inside_each_segment():
    text = "Metoprolol 25 mg; Lisinopril 10 mg"
    segs = segment_clinical_text(text)
    assert len(segs) == 2

    # Segment 0 spans
    assert find_segment_idx(0, 10, segs) == 0
    assert find_segment_idx(11, 16, segs) == 0
    # Segment 1 spans
    assert find_segment_idx(18, 28, segs) == 1
    assert find_segment_idx(29, 34, segs) == 1


# ---------------------------------------------------------------------------
# 6. Valid spans ending exactly at segment boundaries
# ---------------------------------------------------------------------------
def test_valid_spans_ending_at_segment_boundaries():
    text = "Metoprolol 25 mg; Lisinopril 10 mg."
    segs = segment_clinical_text(text)
    assert len(segs) == 2

    # Seg 0 ends at index 17 (inclusive of ';')
    # Span [0:17] strictly covers Seg 0
    assert find_segment_idx(0, segs[0].local_end, segs) == 0
    # Seg 1 spans from 17 to 35
    assert find_segment_idx(segs[1].local_start, segs[1].local_end, segs) == 1


# ---------------------------------------------------------------------------
# 7. '10 mg. PO daily' (Unit + uppercase route/frequency)
# ---------------------------------------------------------------------------
def test_unit_plus_route_abbreviation_po_daily():
    text = "Give 10 mg. PO daily."
    segs = segment_clinical_text(text)
    # Must NOT split between mg. and PO
    assert len(segs) == 1
    assert segs[0].text == "Give 10 mg. PO daily."

    exts = [
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg.", "start": 5, "end": 11},
        {"field_type": "med.route", "value": "oral", "evidence_text": "PO", "start": 12, "end": 14},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_PO", _make_page(text), text, "r0")

    assert len(claims) == 2
    assert len(diags["rejected"]) == 0


# ---------------------------------------------------------------------------
# 8. '10 mg. IV stat' (Unit + uppercase route/frequency)
# ---------------------------------------------------------------------------
def test_unit_plus_route_abbreviation_iv_stat():
    text = "Give 10 mg. IV stat."
    segs = segment_clinical_text(text)
    # Must NOT split between mg. and IV
    assert len(segs) == 1
    assert segs[0].text == "Give 10 mg. IV stat."

    exts = [
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg.", "start": 5, "end": 11},
        {"field_type": "med.route", "value": "intravenous", "evidence_text": "IV", "start": 12, "end": 14},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_IV", _make_page(text), text, "r0")

    assert len(claims) == 2
    assert len(diags["rejected"]) == 0


# ---------------------------------------------------------------------------
# 9. Genuine sentence boundaries after 'mg.'
# ---------------------------------------------------------------------------
def test_genuine_sentence_boundary_after_mg():
    text = "Take Lisinopril 10 mg. Metoprolol 25 mg was added."
    segs = segment_clinical_text(text)
    # Must split after mg. before Metoprolol
    assert len(segs) == 2
    assert segs[0].text.strip() == "Take Lisinopril 10 mg."
    assert segs[1].text.strip() == "Metoprolol 25 mg was added."

    exts = [
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 5, "end": 15},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg.", "start": 16, "end": 22},
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 23, "end": 33},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 34, "end": 39},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_GENUINE_SENT", _make_page(text), text, "r0")

    assert len(claims) == 4
    # Lisinopril group != Metoprolol group
    assert claims[0].group_id == claims[1].group_id
    assert claims[2].group_id == claims[3].group_id
    assert claims[0].group_id != claims[2].group_id


# ---------------------------------------------------------------------------
# 10. Lowercase sentence beginnings
# ---------------------------------------------------------------------------
def test_lowercase_sentence_beginning_after_abbreviation():
    text = "Patient took Lisinopril 10 mg. oral Metoprolol was added."
    segs = segment_clinical_text(text)
    # Correctly splits because 'oral' is immediately followed by capitalized 'Metoprolol'
    assert len(segs) == 2
    assert segs[0].text.strip() == "Patient took Lisinopril 10 mg."
    assert segs[1].text.strip() == "oral Metoprolol was added."

    exts = [
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 13, "end": 23},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg.", "start": 24, "end": 30},
        {"field_type": "med.route", "value": "oral", "evidence_text": "oral", "start": 31, "end": 35},
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 36, "end": 46},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_LOWER_SENT", _make_page(text), text, "r0")

    lis_claim = next(c for c in claims if c.value == "Lisinopril")
    oral_claim = next(c for c in claims if c.value == "oral")
    metop_claim = next(c for c in claims if c.value == "Metoprolol")

    # Oral is in segment 1 and MUST NOT leak into Lisinopril's group
    assert oral_claim.group_id != lis_claim.group_id
    # In Milestone 2.2, oral without prior anchor in segment 1 is quarantined as __no_anchor__
    assert oral_claim.group_id == "__no_anchor__"


# ---------------------------------------------------------------------------
# 11. Clinical abbreviations followed by genuine new sentences
# ---------------------------------------------------------------------------
def test_abbreviation_followed_by_genuine_new_sentence():
    text = "Take 25 mg b.i.d. Next statement begins here."
    segs = segment_clinical_text(text)
    assert len(segs) == 2
    assert segs[0].text.strip() == "Take 25 mg b.i.d."
    assert segs[1].text.strip() == "Next statement begins here."


# ---------------------------------------------------------------------------
# 12. Candidate permutation invariance under cross-segment rejections and boundary isolation
# ---------------------------------------------------------------------------
def test_permutation_invariance_with_cross_segment_rejections():
    text = "Metoprolol 25 mg; Lisinopril 10 mg."
    # Mix of valid extractions and one invalid cross-segment extraction
    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "25 mg", "start": 11, "end": 16},
        {"field_type": "med.dose", "value": "invalid", "evidence_text": "25 mg; Lisinopril", "start": 11, "end": 28},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "start": 18, "end": 28},
        {"field_type": "med.dose", "value": "10", "unit_raw": "mg", "evidence_text": "10 mg", "start": 29, "end": 34},
    ]

    def _normalize(claims, diags):
        gid_map = {}
        norm_claims = []
        for c in claims:
            if c.group_id not in gid_map:
                gid_map[c.group_id] = f"G{len(gid_map)}" if c.group_id != "__no_anchor__" else "__no_anchor__"
            norm_claims.append((c.field_type, c.value, gid_map[c.group_id]))
        norm_rejections = [
            (r.get("field_type"), r.get("value"), r.get("reason"))
            for r in diags.get("rejected", [])
        ]
        return norm_claims, norm_rejections

    reference = None
    # Test all permutations
    for perm in itertools.permutations(exts):
        extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=list(perm)), max_retries=0)
        evs, claims, diags = extractor.extract("DOC_PERM_INV", _make_page(text), text, "r0")
        norm = _normalize(claims, diags)
        if reference is None:
            reference = norm
            # Ensure invalid was rejected and 4 valid claims remained
            assert len(claims) == 4
            assert len(diags["rejected"]) == 1
            assert "crosses segment boundary" in diags["rejected"][0]["reason"]
        else:
            assert norm == reference, f"Permutation failed determinism: {perm}"
