"""Milestone 1.3: Mandatory Regression Tests for Anchor Ordering and Diagnostic Determinism.

Verifies:
1. Co-starting med.name and med.dose where dose span ends before name span.
2. The same case with a preceding medication, explicitly asserting that the dose
   CANNOT leak into the preceding medication group and binds to its own anchor.
3. Co-starting lab.value and lab.flag where flag span ends before value span.
4. Identical spans across anchor and dependent fields.
5. Permutation invariance for all of the above scenarios (order-invariant claims, evidences, groups).
6. Deterministic rejection diagnostics across all candidate permutations.
"""

import itertools
import pytest
from clindoc.extract.llm_extractor import NarrativeExtractor, MockLLMProvider
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


def test_costarting_dose_ending_before_name():
    """med.name [0:10] and med.dose [0:5] both start at 0, but dose ends first.

    Anchor priority must ensure med.name is sorted first, so dose belongs to the same group.
    """
    text = "Metoprolol 25 mg"
    exts = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "Metop", "start": 0, "end": 5},
    ]
    page = _make_page(text)
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_1", page, text, "r0")

    assert len(claims) == 2
    assert len(evs) == 2
    assert diags["rejected"] == []

    # Claims must be ordered [med.name, med.dose]
    assert claims[0].field_type == "med.name"
    assert claims[1].field_type == "med.dose"

    # Both must share the exact same non-sentinel group ID
    assert claims[0].group_id != "__no_anchor__"
    assert claims[0].group_id == claims[1].group_id


def test_costarting_dose_ending_before_name_with_preceding_med():
    """CRITICAL SAFETY TEST:

    Document contains: "Aspirin 81 mg. Metoprolol 25 mg"
    Metoprolol starts at 15. A co-starting dose span at [15:20] ends before [15:25].
    Assert:
    - Aspirin and its dose share group G_A.
    - Metoprolol and its dose share group G_M.
    - G_A != G_M (the 25 mg dose MUST NOT leak into Aspirin's group).
    """
    text = "Aspirin 81 mg. Metoprolol 25 mg"
    exts = [
        {"field_type": "med.name", "value": "Aspirin", "evidence_text": "Aspirin", "start": 0, "end": 7},
        {"field_type": "med.dose", "value": "81", "unit_raw": "mg", "evidence_text": "81 mg", "start": 8, "end": 13},
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 15, "end": 25},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "Metop", "start": 15, "end": 20},
    ]
    page = _make_page(text)
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_PREV", page, text, "r0")

    assert len(claims) == 4
    assert len(evs) == 4
    assert diags["rejected"] == []

    aspirin_claim = next(c for c in claims if c.field_type == "med.name" and c.value == "Aspirin")
    aspirin_dose = next(c for c in claims if c.field_type == "med.dose" and c.value == "81")
    metop_claim = next(c for c in claims if c.field_type == "med.name" and c.value == "Metoprolol")
    metop_dose = next(c for c in claims if c.field_type == "med.dose" and c.value == "25")

    # Aspirin and 81 mg share group
    assert aspirin_claim.group_id == aspirin_dose.group_id
    assert aspirin_claim.group_id != "__no_anchor__"

    # Metoprolol and 25 mg share group
    assert metop_claim.group_id == metop_dose.group_id
    assert metop_claim.group_id != "__no_anchor__"

    # Explicit isolation: Metoprolol dose did NOT leak into Aspirin group
    assert metop_dose.group_id != aspirin_claim.group_id


def test_costarting_lab_flag_ending_before_value():
    """lab.value [0:9] ('95 Normal') and lab.flag [0:2] ('95') share start 0.

    Flag ends before value. Anchor priority must ensure lab.value is sorted first.
    Both must share the same laboratory group ID.
    """
    text = "95 Normal lab"
    exts = [
        {"field_type": "lab.value", "value": "95 Normal", "evidence_text": "95 Normal", "start": 0, "end": 9},
        {"field_type": "lab.flag", "value": "Normal", "evidence_text": "95", "start": 0, "end": 2},
    ]
    page = _make_page(text)
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts), max_retries=0)
    evs, claims, diags = extractor.extract("DOC_LAB", page, text, "r0")

    assert len(claims) == 2
    assert len(evs) == 2
    assert diags["rejected"] == []

    val_claim = next(c for c in claims if c.field_type == "lab.value")
    flag_claim = next(c for c in claims if c.field_type == "lab.flag")

    assert val_claim.group_id != "__no_anchor__"
    assert val_claim.group_id == flag_claim.group_id


def test_identical_spans_across_anchor_and_dependent_fields():
    """Identical start and end coordinates:

    med.name [0:10] and med.dose [0:10].
    lab.value [0:6] and lab.flag [0:6].
    Anchor priority must ensure anchor is created first and attribute joins it.
    """
    # Med test
    text_med = "Metoprolol 25 mg"
    exts_med = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "Metoprolol", "start": 0, "end": 10},
    ]
    page_med = _make_page(text_med)
    ext_med_inst = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts_med), max_retries=0)
    _, claims_med, _ = ext_med_inst.extract("DOC_M", page_med, text_med, "r0")
    assert len(claims_med) == 2
    assert claims_med[0].group_id != "__no_anchor__"
    assert claims_med[0].group_id == claims_med[1].group_id

    # Lab test
    text_lab = "Normal result"
    exts_lab = [
        {"field_type": "lab.value", "value": "Normal", "evidence_text": "Normal", "start": 0, "end": 6},
        {"field_type": "lab.flag", "value": "Normal", "evidence_text": "Normal", "start": 0, "end": 6},
    ]
    page_lab = _make_page(text_lab)
    ext_lab_inst = NarrativeExtractor(provider=MockLLMProvider(canned_responses=exts_lab), max_retries=0)
    _, claims_lab, _ = ext_lab_inst.extract("DOC_L", page_lab, text_lab, "r0")
    assert len(claims_lab) == 2
    assert claims_lab[0].group_id != "__no_anchor__"
    assert claims_lab[0].group_id == claims_lab[1].group_id


def test_permutation_invariance_for_co_starting_and_overlapping():
    """Test all permutations of candidates for co-starting/overlapping inputs.

    Asserts that normalized claims, evidences, and group relationships are 100% identical.
    """
    text = "Aspirin 81 mg. Metoprolol 25 mg oral"
    base_exts = [
        {"field_type": "med.name", "value": "Aspirin", "evidence_text": "Aspirin", "start": 0, "end": 7},
        {"field_type": "med.dose", "value": "81", "unit_raw": "mg", "evidence_text": "81 mg", "start": 8, "end": 13},
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 15, "end": 25},
        {"field_type": "med.dose", "value": "25", "unit_raw": "mg", "evidence_text": "Metop", "start": 15, "end": 20},
        {"field_type": "med.route", "value": "oral", "evidence_text": "oral", "start": 31, "end": 35},
    ]
    page = _make_page(text)

    baseline_claims = None
    baseline_evs = None

    for perm in itertools.permutations(base_exts):
        extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=list(perm)), max_retries=0)
        evs, claims, diags = extractor.extract("DOC_P", page, text, "r0")

        # Map group IDs to equivalence classes
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


def test_diagnostic_rejection_determinism_across_permutations():
    """Assert diagnostics['rejected'] is strictly bitwise deterministic across candidate permutations."""
    text = "Normal finding. Patient status Normal. Metoprolol 25 mg."
    candidates = [
        {"field_type": "lab.value", "value": "Normal", "evidence_text": "Normal"},
        {"field_type": "lab.flag", "value": "Normal", "evidence_text": "Normal"},
        {"field_type": "invalid.field", "value": "Unknown", "evidence_text": "Unknown"},
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
    ]
    page = _make_page(text)

    baseline_rejected = None

    for perm in itertools.permutations(candidates):
        extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=list(perm)), max_retries=0)
        _, _, diags = extractor.extract("DOC_REJ", page, text, "r0")
        rejected = diags["rejected"]

        if baseline_rejected is None:
            baseline_rejected = rejected
        else:
            assert rejected == baseline_rejected
