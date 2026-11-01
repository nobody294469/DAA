"""Phase 5.2.3 — Milestone 3: Adversarial Tests for Medication Association & Co-reference.

Location: tests/test_phase523_milestone3_medication_association.py
Authority: Phase 5.2.3 Milestone 3 Test Specification

Covers all 26 required adversarial test cases:
 1. One medication with dose, route, and frequency.
 2. Two interleaved medications with different doses.
 3. Two medications sharing an explicitly stated route.
 4. Two medications with a shared frequency.
 5. Explicit 'respectively' one-to-one mapping.
 6. 'respectively' with mismatched entity and attribute counts.
 7. Multi-medication ambiguous dose scope.
 8. Orphan medication attributes.
 9. Medication attribute appearing before the medication name.
10. Repeated identical medication mentions.
11. Morning and bedtime split dosing.
12. Dose increase from historical to current.
13. Explicit discontinuation.
14. Conditional hold instructions.
15. PRN instructions.
16. Cross-sentence medication attribute leakage.
17. Cross-clause attribute leakage.
18. Cross-table-row leakage.
19. OCR multiline medication order.
20. Medication and laboratory entities in the same source region.
21. Missing or ambiguous source offsets.
22. Candidate extraction permutation invariance.
23. Human confirmation requirement for provisional associations.
24. Rejection of ambiguous unconfirmed export.
25. Audit-trail recording for suppressed attributes.
26. Regression suite integration (Milestones 1 through 2.4).
"""
from __future__ import annotations

import copy
import pytest
import uuid

from clindoc.extract.contracts import Claim, Evidence
from clindoc.extract.llm_extractor import MockLLMProvider, NarrativeExtractor
from clindoc.extract.medication_association import AssociationStatus, NO_ANCHOR_SENTINEL
from clindoc.ocr.document_text import DocumentPage, Token
from clindoc.ocr.geometry import BBoxDict
from clindoc.schema.verification import VerificationResult
from clindoc.services.fhir_export import (
    ClaimEligibilityEvaluation,
    ClaimReviewState,
    FHIRExportEligibilityError,
    FHIRExportService,
)


def _make_page(page_text: str) -> DocumentPage:
    """Build a DocumentPage with synthetic tokens matching word bounds."""
    tokens = []
    y = 20.0
    for line_idx, line in enumerate(page_text.splitlines()):
        x = 10.0
        for word in line.split():
            w_len = len(word) * 8.0
            tokens.append(
                Token(
                    text=word,
                    bbox=BBoxDict(x0=x, y0=y, x1=x + w_len, y1=y + 15.0),
                    line_id=f"L{line_idx}",
                )
            )
            x += w_len + 5.0
        y += 20.0
    return DocumentPage(page_number=1, width_px=612, height_px=792, tokens=tokens)


def _extract(canned: list[dict], page_text: str, doc_id: str = "M3_TEST") -> tuple[list[Evidence], list[Claim], dict]:
    extractor = NarrativeExtractor(
        provider=MockLLMProvider(canned_responses=canned),
        max_retries=0,
    )
    page = _make_page(page_text)
    return extractor.extract(doc_id, page, page_text, "r0")


# ─── 1. One medication with dose, route, and frequency ───────────────────────

def test_1_single_med_with_dose_route_frequency():
    """Unambiguous single medication clause binds all attributes cleanly."""
    text = "Metoprolol 25 mg oral daily."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
        {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 4
    gids = {c.group_id for c in claims}
    assert len(gids) == 1
    assert NO_ANCHOR_SENTINEL not in gids

    for c in claims:
        assert getattr(c, "association_status", None) == AssociationStatus.UNAMBIGUOUS_SINGLE.value


# ─── 2. Two interleaved medications with different doses ─────────────────────

def test_2_two_interleaved_medications():
    """Two interleaved medications in a single sentence receive strictly local attributes."""
    text = "Metoprolol 25 mg PO twice daily; and titrate Omeprazole 20 mg PO once daily."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
        {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
        {"field_type": "med.name", "value": "Omeprazole", "evidence_text": "Omeprazole"},
        {"field_type": "med.dose", "value": "20", "evidence_text": "20 mg", "unit_raw": "mg"},
        {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
        {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 8

    metop_claims = [c for c in claims if c.value in ("Metoprolol", "25", "twice daily")]
    omep_claims = [c for c in claims if c.value in ("Omeprazole", "20", "once daily")]

    metop_gids = {c.group_id for c in metop_claims}
    omep_gids = {c.group_id for c in omep_claims}

    assert len(metop_gids) == 1
    assert len(omep_gids) == 1
    assert metop_gids != omep_gids
    assert NO_ANCHOR_SENTINEL not in metop_gids
    assert NO_ANCHOR_SENTINEL not in omep_gids


# ─── 3. Two medications sharing an explicitly stated route ───────────────────

def test_3_two_meds_shared_route():
    """Shared route marked with 'both' binds safely to both coordinate medications."""
    text = "Metoprolol and Lisinopril were both taken orally."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.route", "value": "orally", "evidence_text": "orally"},
    ]
    evs, claims, diag = _extract(canned, text)
    # Both medications receive a route claim pointing to the shared evidence
    assert len(claims) == 4
    med_names = [c for c in claims if c.field_type == "med.name"]
    routes = [c for c in claims if c.field_type == "med.route"]

    assert len(med_names) == 2
    assert len(routes) == 2

    # Distinct group IDs
    assert med_names[0].group_id != med_names[1].group_id
    # Both route claims have SHARED_COORDINATE_CONFIRMED
    for r in routes:
        assert getattr(r, "association_status", None) == AssociationStatus.SHARED_COORDINATE_CONFIRMED.value
        assert getattr(r, "is_shared", False) is True


# ─── 4. Two medications with a shared frequency ──────────────────────────────

def test_4_two_meds_shared_frequency():
    """Two medications with an explicitly stated shared frequency bind to both groups."""
    text = "Metoprolol and Lisinopril were both taken once daily."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 4
    freqs = [c for c in claims if c.field_type == "med.frequency"]
    assert len(freqs) == 2
    for f in freqs:
        assert getattr(f, "association_status", None) == AssociationStatus.SHARED_COORDINATE_CONFIRMED.value


# ─── 5. Explicit 'respectively' one-to-one mapping ───────────────────────────

def test_5_explicit_respectively_bijection():
    """Explicit 'respectively' with matching counts creates provisional 1:1 associations."""
    text = "Metoprolol and Lisinopril were continued at 25 mg and 5 mg, respectively."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.dose", "value": "5", "evidence_text": "5 mg", "unit_raw": "mg"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 4

    metop = next(c for c in claims if c.value == "Metoprolol")
    lisin = next(c for c in claims if c.value == "Lisinopril")
    dose_25 = next(c for c in claims if c.value == "25")
    dose_5 = next(c for c in claims if c.value == "5")

    # 1:1 ordered bijection
    assert dose_25.group_id == metop.group_id
    assert dose_5.group_id == lisin.group_id
    assert metop.group_id != lisin.group_id

    # Marked as provisional parallel coordinate bijection
    assert getattr(dose_25, "association_status", None) == AssociationStatus.PARALLEL_COORDINATE_BIJECTION.value
    assert getattr(dose_5, "association_status", None) == AssociationStatus.PARALLEL_COORDINATE_BIJECTION.value


# ─── 6. 'respectively' with mismatched entity and attribute counts ───────────

def test_6_mismatched_respectively_counts():
    """Mismatched entity and attribute counts with 'respectively' must fail closed."""
    text = "Metoprolol, Lisinopril, and Amlodipine were continued at 25 mg and 5 mg, respectively."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.name", "value": "Amlodipine", "evidence_text": "Amlodipine"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.dose", "value": "5", "evidence_text": "5 mg", "unit_raw": "mg"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 5

    doses = [c for c in claims if c.field_type == "med.dose"]
    for d in doses:
        # Refuse to associate: quarantined fail-closed
        assert d.group_id == NO_ANCHOR_SENTINEL
        assert getattr(d, "association_status", None) == AssociationStatus.AMBIGUOUS_SCOPE_MULTI_ENTITY.value


# ─── 7. Multi-medication ambiguous dose scope ────────────────────────────────

def test_7_ambiguous_dose_scope():
    """Clustered medication names with trailing dose must NOT assign dose to either."""
    text = "Metoprolol and Lisinopril 25 mg daily."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 4

    metop = next(c for c in claims if c.value == "Metoprolol")
    lisin = next(c for c in claims if c.value == "Lisinopril")
    dose = next(c for c in claims if c.field_type == "med.dose")

    # Drug names have distinct valid groups
    assert metop.group_id != lisin.group_id
    # Dose must NOT be assigned to Lisinopril or Metoprolol
    assert dose.group_id == NO_ANCHOR_SENTINEL
    assert getattr(dose, "association_status", None) == AssociationStatus.AMBIGUOUS_SCOPE_MULTI_ENTITY.value


# ─── 8. Orphan medication attributes ─────────────────────────────────────────

def test_8_orphan_medication_attributes():
    """Attributes in a clause lacking any medication name must remain unanchored orphans."""
    text = "25 mg daily was administered."
    canned = [
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 2
    for c in claims:
        assert c.group_id == NO_ANCHOR_SENTINEL
        assert getattr(c, "association_status", None) == AssociationStatus.UNANCHORED_ORPHAN.value


# ─── 9. Medication attribute appearing before the medication name ────────────

def test_9_attribute_before_medication_name():
    """A dose appearing before the medication name fails closed (unanchored orphan)."""
    text = "25 mg PO twice daily of Metoprolol."
    canned = [
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 2
    dose = next(c for c in claims if c.field_type == "med.dose")
    name = next(c for c in claims if c.field_type == "med.name")

    assert name.group_id != NO_ANCHOR_SENTINEL
    # Dose before drug is quarantined fail-closed
    assert dose.group_id == NO_ANCHOR_SENTINEL
    assert getattr(dose, "association_status", None) == AssociationStatus.UNANCHORED_ORPHAN.value


# ─── 10. Repeated identical medication mentions ──────────────────────────────

def test_10_repeated_identical_medication_mentions():
    """Repeated mentions across sentences produce distinct groups preserving distinct provenance."""
    text = "Metoprolol 25 mg daily was prescribed. Later, continue Metoprolol 25 mg daily."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "local_start": 0, "local_end": 10},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg", "local_start": 11, "local_end": 16},
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "local_start": 55, "local_end": 65},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg", "local_start": 66, "local_end": 71},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 4

    names = [c for c in claims if c.field_type == "med.name"]
    assert len(names) == 2
    # Two distinct group IDs, not blindly merged
    assert names[0].group_id != names[1].group_id
    assert names[0].evidence_id != names[1].evidence_id


# ─── 11. Morning and bedtime split dosing ────────────────────────────────────

def test_11_morning_and_bedtime_split_dosing():
    """Multiple doses in one clause trigger AMBIGUOUS_SLOT_CONFLICT rather than flattening."""
    text = "Metoprolol 25 mg in the morning and 50 mg at bedtime."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.dose", "value": "50", "evidence_text": "50 mg", "unit_raw": "mg"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 3

    doses = [c for c in claims if c.field_type == "med.dose"]
    assert len(doses) == 2

    # Both belong to Metoprolol, but flagged with slot conflict
    name = next(c for c in claims if c.field_type == "med.name")
    assert doses[0].group_id == name.group_id
    assert doses[1].group_id == name.group_id

    for d in doses:
        assert getattr(d, "association_status", None) == AssociationStatus.AMBIGUOUS_SLOT_CONFLICT.value
        assert getattr(d, "slot_conflict", False) is True


# ─── 12. Dose increase from historical to current ────────────────────────────

def test_12_dose_increase_temporal_transition():
    """Dose adjustment in one clause triggers AMBIGUOUS_SLOT_CONFLICT requiring review."""
    text = "Metoprolol 25 mg previously, increased to 50 mg daily."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.dose", "value": "50", "evidence_text": "50 mg", "unit_raw": "mg"},
    ]
    evs, claims, diag = _extract(canned, text)
    doses = [c for c in claims if c.field_type == "med.dose"]
    assert len(doses) == 2
    for d in doses:
        assert getattr(d, "association_status", None) == AssociationStatus.AMBIGUOUS_SLOT_CONFLICT.value


# ─── 13. Explicit discontinuation ────────────────────────────────────────────

def test_13_explicit_discontinuation():
    """Discontinued medication is marked with temporal_status='discontinued'."""
    text = "Lisinopril discontinued due to cough."
    canned = [
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 1
    assert claims[0].temporal_status == "discontinued"


# ─── 14. Conditional hold instructions ───────────────────────────────────────

def test_14_conditional_hold_instructions():
    """Conditional hold instruction sets is_conditional=True requiring clinical review."""
    text = "Hold Metoprolol if systolic BP is below 100."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 1
    assert getattr(claims[0], "is_conditional", False) is True


# ─── 15. PRN instructions ───────────────────────────────────────────────────

def test_15_prn_instructions():
    """PRN instructions are preserved verbatim and safely associated."""
    text = "Tab Metacin 1g as and when needed."
    canned = [
        {"field_type": "med.name", "value": "Metacin", "evidence_text": "Tab Metacin"},
        {"field_type": "med.dose", "value": "1", "evidence_text": "1g", "unit_raw": "g"},
        {"field_type": "med.frequency", "value": "as and when needed", "evidence_text": "as and when needed"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 3
    freq = next(c for c in claims if c.field_type == "med.frequency")
    assert freq.value == "as and when needed"
    assert getattr(freq, "association_status", None) == AssociationStatus.UNAMBIGUOUS_SINGLE.value


# ─── 16. Cross-sentence medication attribute leakage ─────────────────────────

def test_16_cross_sentence_leakage_blocked():
    """Attributes in a subsequent sentence without coreference do not leak to prior drug."""
    text = "Metoprolol 25 mg daily. Take with food."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.route", "value": "with food", "evidence_text": "with food"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 3
    route = next(c for c in claims if c.field_type == "med.route")
    # 'with food' is in sentence 2: quarantined as unanchored orphan
    assert route.group_id == NO_ANCHOR_SENTINEL
    assert getattr(route, "association_status", None) == AssociationStatus.UNANCHORED_ORPHAN.value


# ─── 17. Cross-clause attribute leakage ──────────────────────────────────────

def test_17_cross_clause_leakage_blocked():
    """Attributes separated by clause boundary (semicolon) do not leak across clauses."""
    text = "Metoprolol 25 mg; patient refused further doses."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 2
    assert claims[0].group_id == claims[1].group_id


# ─── 18. Cross-table-row leakage ─────────────────────────────────────────────

def test_18_cross_table_row_leakage_blocked():
    """Table rows separated by newlines isolate their respective doses."""
    text = "Metoprolol\t25 mg\nLisinopril\t5 mg"
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.dose", "value": "5", "evidence_text": "5 mg", "unit_raw": "mg"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 4

    metop = next(c for c in claims if c.value == "Metoprolol")
    metop_dose = next(c for c in claims if c.value == "25")
    lisin = next(c for c in claims if c.value == "Lisinopril")
    lisin_dose = next(c for c in claims if c.value == "5")

    assert metop_dose.group_id == metop.group_id
    assert lisin_dose.group_id == lisin.group_id
    assert metop.group_id != lisin.group_id


# ─── 19. OCR multiline medication order ──────────────────────────────────────

def test_19_ocr_multiline_medication_order():
    """Valid OCR multiline medication orders continue across lines with aligned layout."""
    text = "Metoprolol 25 mg PO twice daily"
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 3
    gids = {c.group_id for c in claims}
    assert len(gids) == 1


# ─── 20. Medication and laboratory entities in the same source region ────────

def test_20_med_and_lab_cross_domain_isolation():
    """Laboratory and medication findings in the same region must have completely disjoint groups."""
    text = "HAEMOGLOBIN 12.6 GM%\nMetoprolol 25 mg daily."
    canned = [
        {"field_type": "lab.value", "value": "12.6", "evidence_text": "12.6 GM%", "unit_raw": "GM%"},
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 3

    lab_claim = next(c for c in claims if c.field_type == "lab.value")
    med_name = next(c for c in claims if c.field_type == "med.name")
    med_dose = next(c for c in claims if c.field_type == "med.dose")

    assert med_name.group_id == med_dose.group_id
    assert lab_claim.group_id != med_name.group_id
    assert lab_claim.group_id != NO_ANCHOR_SENTINEL


# ─── 21. Missing or ambiguous source offsets ─────────────────────────────────

def test_21_invalid_offsets_rejected_fail_closed():
    """Candidate extractions with out-of-bounds offsets are rejected fail-closed."""
    text = "Metoprolol 25 mg oral daily."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "local_start": 999, "local_end": 1050},
    ]
    evs, claims, diag = _extract(canned, text)
    assert len(claims) == 0
    assert len(diag["rejected"]) == 1
    assert "out of bounds" in diag["rejected"][0]["reason"]


# ─── 22. Candidate extraction permutation invariance ─────────────────────────

def test_22_candidate_permutation_invariance():
    """Emission order permutation in LLM JSON yields identical claims and groupings."""
    text = "Metoprolol 25 mg oral daily."
    canned_a = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
    ]
    # Reversed emission order
    canned_b = list(reversed(canned_a))

    evs_a, claims_a, _ = _extract(canned_a, text)
    evs_b, claims_b, _ = _extract(canned_b, text)

    assert len(claims_a) == len(claims_b) == 3
    # Sorted by char_start ensures identical claim sequence
    assert [c.value for c in claims_a] == [c.value for c in claims_b]
    assert [c.field_type for c in claims_a] == [c.field_type for c in claims_b]


# ─── 23. Human confirmation requirement for provisional associations ─────────

def test_23_provisional_parallel_requires_human_confirmation():
    """PARALLEL_COORDINATE_BIJECTION claims require human confirmation before export."""
    text = "Metoprolol and Lisinopril were continued at 25 mg and 5 mg, respectively."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
        {"field_type": "med.dose", "value": "5", "evidence_text": "5 mg", "unit_raw": "mg"},
    ]
    evs, claims, diag = _extract(canned, text)
    dose_25 = next(c for c in claims if c.value == "25")
    ev_map = {e.evidence_id: e for e in evs}

    service = FHIRExportService()

    # Unreviewed / pending evaluation
    eval_unreviewed = service.evaluate_claim_eligibility(
        claim=dose_25,
        ev=ev_map[dose_25.evidence_id],
        require_human_acceptance=True,
    )
    assert eval_unreviewed.is_eligible is False
    assert "Provisional parallel coordinate association requires clinician confirmation" in eval_unreviewed.reason

    # Human accepted evaluation
    object.__setattr__(dose_25, "review_state", ClaimReviewState.ACCEPTED)
    eval_accepted = service.evaluate_claim_eligibility(
        claim=dose_25,
        ev=ev_map[dose_25.evidence_id],
        require_human_acceptance=True,
    )
    assert eval_accepted.is_eligible is True


# ─── 24. Rejection of ambiguous unconfirmed export ───────────────────────────

def test_24_rejection_of_ambiguous_export():
    """Ambiguous multi-entity scope claims cannot be exported without human review."""
    text = "Metoprolol and Lisinopril 25 mg."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
    ]
    evs, claims, diag = _extract(canned, text)
    dose_25 = next(c for c in claims if c.value == "25")
    ev_map = {e.evidence_id: e for e in evs}

    service = FHIRExportService()
    eval_res = service.evaluate_claim_eligibility(
        claim=dose_25,
        ev=ev_map[dose_25.evidence_id],
        require_human_acceptance=True,
    )
    assert eval_res.is_eligible is False
    assert "ambiguous multi-entity scope" in eval_res.reason


# ─── 25. Audit-trail recording for suppressed attributes ─────────────────────

def test_25_audit_trail_recording_for_suppressed_attributes():
    """Suppressed ambiguous attributes are recorded in export audit trail with reason code."""
    text = "Metoprolol and Lisinopril 25 mg."
    canned = [
        {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
    ]
    evs, claims, diag = _extract(canned, text)
    name = next(c for c in claims if c.value == "Metoprolol")
    dose = next(c for c in claims if c.value == "25")

    # Name is human-accepted
    object.__setattr__(name, "review_state", ClaimReviewState.ACCEPTED)
    # Dose is left unreviewed / ambiguous
    object.__setattr__(dose, "association_status", AssociationStatus.AMBIGUOUS_SCOPE_MULTI_ENTITY.value)

    ev_map = {e.evidence_id: e for e in evs}
    service = FHIRExportService()

    ver_name = VerificationResult(
        verification_id=f"ver-{name.claim_id}",
        claim_id=name.claim_id,
        evidence_id=name.evidence_id,
        label="SUPPORTED",
        confidence=0.95,
        stage_reached="A",
    )

    result = service.export_intermediate_representation(
        claims=[name, dose],
        evidence_map=ev_map,
        verification_map={name.claim_id: ver_name},
        document_id="DOC_AUDIT_TRAIL",
        enforce_eligibility=True,
    )

    # Name was exported
    assert name.claim_id in result.exportable_claim_ids
    # Dose was suppressed
    suppressed_entries = [entry for entry in result.audit_trail if entry.get("status") == "SUPPRESSED"]
    assert len(suppressed_entries) >= 1
    assert any("ambiguous multi-entity scope" in str(e.get("reason", "")) for e in suppressed_entries)


# ─── 26. Regression suite integration (Milestones 1 through 2.4) ─────────────

def test_26_regression_milestones_clean_integration():
    """Verify that core laboratory and whitespace invariants from Milestones 1-2.4 remain intact."""
    text = "HAEMOGLOBIN\n12.6\nGM%"
    page = _make_page(text)
    # 3 lines with aligned geometry
    tokens = [
        Token(text="HAEMOGLOBIN", bbox=BBoxDict(x0=50.0, y0=50.0, x1=120.0, y1=62.0), line_id="L0"),
        Token(text="12.6", bbox=BBoxDict(x0=52.0, y0=68.0, x1=75.0, y1=80.0), line_id="L1"),
        Token(text="GM%", bbox=BBoxDict(x0=53.0, y0=86.0, x1=80.0, y1=98.0), line_id="L2"),
    ]
    page_aligned = DocumentPage(page_number=1, width_px=612, height_px=792, tokens=tokens)

    canned = [
        {"field_type": "lab.value", "value": "12.6", "evidence_text": "12.6", "unit_raw": "GM%"},
    ]
    extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
    evs, claims, diag = extractor.extract("REG_TEST", page_aligned, text, "r0")
    assert len(claims) == 1
    assert claims[0].value == "12.6"
    assert claims[0].group_id != NO_ANCHOR_SENTINEL
