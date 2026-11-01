"""Phase 5.2.3 — Milestone 1: Deterministic Extraction Ordering & Evidence Grounding Tests.

Mandatory Test Suite:
1. Random permutations of the same extraction list produce identical claims, evidence, and grouping.
2. Duplicate evidence spans.
3. Repeated identical evidence phrases at different document positions.
4. Partially overlapping spans.
5. Missing or invalid source offsets.
6. Unmatched evidence text.
7. Multiple fields referencing the same source phrase.
8. Existing medication and laboratory grouping behavior remains unchanged where the input is unambiguous.
"""
from __future__ import annotations

import json
import random
import pytest

from clindoc.extract.llm_extractor import MockLLMProvider, NarrativeExtractor
from clindoc.extract.contracts import Claim, Evidence
from clindoc.ocr.document_text import DocumentPage, Token
from clindoc.ocr.geometry import BBoxDict


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_page(page_text: str) -> DocumentPage:
    """Build a DocumentPage from raw text."""
    tokens = []
    x, y, line_num = 10.0, 20.0, 0
    for line in page_text.splitlines():
        line_id = f"L{line_num}"
        x = 10.0
        for word in line.split():
            w_len = len(word) * 8.0
            tokens.append(Token(text=word, bbox=BBoxDict(x0=x, y0=y, x1=x + w_len, y1=y + 15.0), line_id=line_id))
            x += w_len + 5.0
        y += 20.0
        line_num += 1
    return DocumentPage(page_number=1, width_px=612, height_px=792, tokens=tokens)


def _extract(canned: list[dict], page_text: str, doc_id: str = "P523_M1_TEST") -> tuple[list[Evidence], list[Claim], dict]:
    extractor = NarrativeExtractor(
        provider=MockLLMProvider(canned_responses=canned),
        max_retries=0,
    )
    page = _make_page(page_text)
    return extractor.extract(doc_id, page, page_text, "r0")


# ─── Test Suite ───────────────────────────────────────────────────────────────

class TestPhase523Milestone1Grounding:
    """Mandatory tests for Milestone 1 deterministic extraction ordering and evidence grounding."""

    def test_1_random_permutations_produce_identical_claims_and_grouping(self):
        """Random permutations of the same extraction list must yield identical claims,
        evidence spans, and medication grouping partitions."""
        page_text = (
            "Assessment and Plan: Patient was started on Metformin 500 mg oral twice daily for diabetes. "
            "Also continued Lisinopril 10 mg oral once daily for hypertension."
        )
        canned = [
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral", "local_start": 61, "local_end": 65},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
            {"field_type": "med.dose", "value": "10", "evidence_text": "10 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral", "local_start": 124, "local_end": 128},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ]

        base_evs, base_claims, _ = _extract(canned, page_text)
        assert len(base_claims) == 8
        assert len(base_evs) == 8

        # Extract baseline signature: list of (field_type, value, char_start, char_end, group_index)
        # We index group_ids by first appearance order to check relative partitioning
        group_id_order: list[str] = []
        for c in base_claims:
            if c.group_id not in group_id_order:
                group_id_order.append(c.group_id)

        base_sig = [
            (c.field_type, c.value, ev.char_start, ev.char_end, group_id_order.index(c.group_id))
            for c, ev in zip(base_claims, base_evs)
        ]

        # Test across 30 distinct random permutations
        for seed in range(30):
            shuffled = list(canned)
            random.seed(seed)
            random.shuffle(shuffled)

            perm_evs, perm_claims, _ = _extract(shuffled, page_text)
            perm_group_order: list[str] = []
            for c in perm_claims:
                if c.group_id not in perm_group_order:
                    perm_group_order.append(c.group_id)

            perm_sig = [
                (c.field_type, c.value, ev.char_start, ev.char_end, perm_group_order.index(c.group_id))
                for c, ev in zip(perm_claims, perm_evs)
            ]

            assert base_sig == perm_sig, f"Permutation difference detected on random seed {seed}!"

    def test_2_duplicate_evidence_spans(self):
        """Duplicate candidate extractions targeting identical character offsets
        must both be preserved and grounded without crashing or nondeterminism."""
        page_text = "Metformin 500 mg PO daily."
        canned = [
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            # Duplicate extraction of med.name with identical evidence text
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg"},
        ]
        evs, claims, diag = _extract(canned, page_text)
        assert len(claims) == 3
        assert len(evs) == 3
        # Both med.name claims share exact offsets
        name_evs = [ev for c, ev in zip(claims, evs) if c.field_type == "med.name"]
        assert len(name_evs) == 2
        assert name_evs[0].char_start == name_evs[1].char_start == 0
        assert name_evs[0].char_end == name_evs[1].char_end == 9

    def test_3_repeated_identical_evidence_phrases_at_different_positions(self):
        """When identical phrases appear at different document positions, explicit
        source offsets correctly ground each claim to its exact occurrence."""
        page_text = "Metoprolol 25 mg oral BID. Later, Metoprolol 50 mg oral BID."
        canned = [
            # First Metoprolol at start of text
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "local_start": 0, "local_end": 10},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            # Second Metoprolol later in text
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "local_start": 34, "local_end": 44},
            {"field_type": "med.dose", "value": "50", "evidence_text": "50 mg", "unit_raw": "mg"},
        ]
        evs, claims, diag = _extract(canned, page_text)
        assert len(claims) == 4
        assert len(diag["rejected"]) == 0

        first_med = next(c for c in claims if c.value == "Metoprolol" and c.group_id == claims[1].group_id)
        second_med = next(c for c in claims if c.value == "Metoprolol" and c.group_id == claims[3].group_id)
        assert first_med.group_id != second_med.group_id

        first_dose = next(c for c in claims if c.value == "25")
        second_dose = next(c for c in claims if c.value == "50")
        assert first_dose.group_id == first_med.group_id
        assert second_dose.group_id == second_med.group_id

    def test_4_partially_overlapping_spans(self):
        """Partially overlapping evidence spans representing different fields must both be preserved."""
        page_text = "Rx: Tab Metacin 1000 mg as needed."
        # Field 1: med.name cites "Tab Metacin" [4:15]
        # Field 2: med.name also cites "Metacin" [8:15] (sub-span)
        # Field 3: med.dose cites "1000 mg" [16:23]
        canned = [
            {"field_type": "med.name", "value": "Metacin", "evidence_text": "Tab Metacin", "local_start": 4, "local_end": 15},
            {"field_type": "med.name", "value": "Metacin", "evidence_text": "Metacin", "local_start": 8, "local_end": 15},
            {"field_type": "med.dose", "value": "1000", "evidence_text": "1000 mg", "unit_raw": "mg", "local_start": 16, "local_end": 23},
        ]
        evs, claims, diag = _extract(canned, page_text)
        assert len(claims) == 3
        assert len(evs) == 3
        spans = [(ev.char_start, ev.char_end, ev.text) for ev in evs]
        assert (4, 15, "Tab Metacin") in spans
        assert (8, 15, "Metacin") in spans
        assert (16, 23, "1000 mg") in spans

    def test_5_missing_or_invalid_source_offsets(self):
        """Invalid offsets (out-of-bounds, non-integer, or text mismatch) and ambiguous missing offsets fail closed."""
        page_text = "Patient takes Lisinopril 20 mg oral daily. Also Lisinopril 40 mg oral daily."

        # Case A: Out of bounds offset
        canned_oob = [
            {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "local_start": -5, "local_end": 10},
        ]
        _, claims_a, diag_a = _extract(canned_oob, page_text)
        assert len(claims_a) == 0
        assert any("out of bounds" in r["reason"] for r in diag_a["rejected"])

        # Case B: Text mismatch at offset
        canned_mismatch = [
            {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril", "local_start": 0, "local_end": 7},
        ]
        _, claims_b, diag_b = _extract(canned_mismatch, page_text)
        assert len(claims_b) == 0
        assert any("does not match evidence_text" in r["reason"] for r in diag_b["rejected"])

        # Case C: Missing offset when phrase appears multiple times in region (ambiguous)
        # "oral" appears at offset 31 and offset 65. Single un-offset extraction must fail closed.
        canned_ambig = [
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
        ]
        _, claims_c, diag_c = _extract(canned_ambig, page_text)
        assert len(claims_c) == 0
        assert any("appears multiple times" in r["reason"] for r in diag_c["rejected"])

    def test_6_unmatched_evidence_text(self):
        """Evidence text not present in source text must fail closed with explicit diagnostic rejection."""
        page_text = "Patient was prescribed Cephalexin 500 mg."
        canned = [
            {"field_type": "med.name", "value": "Amoxicillin", "evidence_text": "Amoxicillin 250mg"},
        ]
        evs, claims, diag = _extract(canned, page_text)
        assert len(claims) == 0
        assert len(evs) == 0
        assert len(diag["rejected"]) == 1
        assert "not a verbatim substring" in diag["rejected"][0]["reason"]

    def test_7_multiple_fields_referencing_same_source_phrase(self):
        """Multiple distinct fields (e.g. med.name and med.dose, or med.route) referencing
        the same verbatim source phrase are both preserved and correctly grouped."""
        page_text = "Ferrous sulfate 200 mg oral daily."
        # E.g. clinical model extracts both med.name and a combined token
        canned = [
            {"field_type": "med.name", "value": "Ferrous sulfate", "evidence_text": "Ferrous sulfate"},
            {"field_type": "med.dose", "value": "200", "evidence_text": "200 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ]
        evs, claims, diag = _extract(canned, page_text)
        assert len(claims) == 4
        assert len(evs) == 4
        assert len(diag["rejected"]) == 0
        med_group = claims[0].group_id
        assert all(c.group_id == med_group for c in claims)

    def test_8_existing_unambiguous_grouping_behavior_unchanged(self):
        """Unambiguous extractions preserve standard medication and laboratory grouping behavior."""
        page_text = (
            "Discharge Medications: "
            "1. Atorvastatin 40 mg oral nightly. "
            "2. Metformin 1000 mg oral twice daily with meals. "
            "Labs: Potassium 4.2 mEq/L (Normal), Glucose 98 mg/dL (Normal)."
        )
        canned = [
            # Med 1
            {"field_type": "med.name", "value": "Atorvastatin", "evidence_text": "Atorvastatin"},
            {"field_type": "med.dose", "value": "40", "evidence_text": "40 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral", "local_start": 45, "local_end": 49},
            {"field_type": "med.frequency", "value": "nightly", "evidence_text": "nightly"},
            # Med 2
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            {"field_type": "med.dose", "value": "1000", "evidence_text": "1000 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral", "local_start": 80, "local_end": 84},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            # Lab 1
            {"field_type": "lab.value", "value": "4.2", "evidence_text": "4.2 mEq/L", "unit_raw": "mEq/L"},
            {"field_type": "lab.flag", "value": "Normal", "evidence_text": "Normal", "local_start": 136, "local_end": 142},
            # Lab 2
            {"field_type": "lab.value", "value": "98", "evidence_text": "98 mg/dL", "unit_raw": "mg/dL"},
            {"field_type": "lab.flag", "value": "Normal", "evidence_text": "Normal", "local_start": 163, "local_end": 169},
        ]
        evs, claims, diag = _extract(canned, page_text)
        assert len(claims) == 12
        assert len(diag["rejected"]) == 0

        # Group assertions
        atorva_group = next(c for c in claims if c.value == "Atorvastatin").group_id
        metform_group = next(c for c in claims if c.value == "Metformin").group_id
        k_group = next(c for c in claims if c.value == "4.2").group_id
        gluc_group = next(c for c in claims if c.value == "98").group_id

        # All 4 groups distinct
        assert len({atorva_group, metform_group, k_group, gluc_group}) == 4

        # Attributes stay in their respective groups
        assert next(c for c in claims if c.value == "40").group_id == atorva_group
        assert next(c for c in claims if c.value == "1000").group_id == metform_group
        assert next(c for c in claims if c.field_type == "lab.flag" and c.evidence_id == evs[9].evidence_id).group_id == k_group
        assert next(c for c in claims if c.field_type == "lab.flag" and c.evidence_id == evs[11].evidence_id).group_id == gluc_group

    def test_9_deterministic_tie_breaking_at_identical_coordinates(self):
        """When multiple fields start at the exact same character offset,
        anchor fields (med.name, lab.value) sort before attributes, guaranteeing correct grouping."""
        page_text = "Metoprolol 25 mg oral BID."
        # Suppose LLM emitted dose before name, but with identical start coordinate:
        canned = [
            {"field_type": "med.dose", "value": "25", "evidence_text": "Metoprolol 25 mg", "local_start": 0, "local_end": 16},
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "local_start": 0, "local_end": 10},
        ]
        evs, claims, _ = _extract(canned, page_text)
        assert len(claims) == 2
        # med.name must be first claim
        assert claims[0].field_type == "med.name"
        assert claims[1].field_type == "med.dose"
        # med.dose must inherit med.name's group_id, NOT __no_anchor__
        assert claims[0].group_id == claims[1].group_id
        assert claims[0].group_id != "__no_anchor__"
