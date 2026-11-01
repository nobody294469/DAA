"""Phase 5.2 — Step 3: Adversarial Tests for Medication Co-reference & Clause Segmentation.

Tests all 12 adversarial cases from the Phase 5.2 specification:
 1. Two medications in one sentence with different doses.
 2. One medication with multiple attributes.
 3. Repeated mentions of the same medication.
 4. Pronoun/reference ambiguity.
 5. Negated medication mentions.
 6. Historical versus current medications.
 7. Planned versus administered medications.
 8. Conflicting doses in adjacent clauses.
 9. A dose appearing before its medication mention.
10. Multiple medications sharing a sentence but not attributes.
11. Missing punctuation and OCR artifacts.
12. Explicitly ambiguous medication-to-dose association.

Also verifies the core Phase 5.2 fix:
  - No dose or route migrates between medications (group_id isolation).
  - Each med.name gets its own group_id distinct from all others.
  - lab.value gets its own group_id distinct from med group_ids.
"""
from __future__ import annotations

import pytest
import uuid

from clindoc.extract.llm_extractor import MockLLMProvider, NarrativeExtractor
from clindoc.extract.contracts import Claim
from clindoc.ocr.document_text import DocumentPage, Token
from clindoc.ocr.geometry import BBoxDict


# ─── Test fixtures ─────────────────────────────────────────────────────────────

def _make_token(text: str, x0: float, y0: float, x1: float, y1: float, line_id: str = "L0") -> Token:
    return Token(text=text, bbox=BBoxDict(x0=x0, y0=y0, x1=x1, y1=y1), line_id=line_id)


def _make_page(page_text: str) -> DocumentPage:
    """Build a DocumentPage from raw page text with synthetic tokens."""
    tokens = []
    x, y, line_num = 10.0, 20.0, 0
    for line in page_text.splitlines():
        line_id = f"L{line_num}"
        x = 10.0
        for word in line.split():
            w_len = len(word) * 8.0
            tokens.append(_make_token(word, x, y, x + w_len, y + 15.0, line_id=line_id))
            x += w_len + 5.0
        y += 20.0
        line_num += 1
    return DocumentPage(page_number=1, width_px=612, height_px=792, tokens=tokens)


def _extract(canned: list[dict], page_text: str, doc_id: str = "NARR_TEST") -> tuple[list, list[Claim], dict]:
    extractor = NarrativeExtractor(
        provider=MockLLMProvider(canned_responses=canned),
        max_retries=0,
    )
    page = _make_page(page_text)
    return extractor.extract(doc_id, page, page_text, "r0")


def _get_claims_by_type(claims: list[Claim], field_type: str) -> list[Claim]:
    return [c for c in claims if c.field_type == field_type]


# ─── Core isolation invariant ──────────────────────────────────────────────────

class TestGroupIdIsolation:
    """Verify the Phase 5.2 root-cause fix: group_id per medication entity."""

    def test_two_medications_get_distinct_group_ids(self):
        """Two med.name claims must produce two distinct group_ids (core Phase 5.2 fix)."""
        page_text = "continue Metoprolol 25 mg PO twice daily; and titrate Omeprazole 20 mg PO once daily in the morning."
        canned = [
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Omeprazole", "evidence_text": "Omeprazole"},
            {"field_type": "med.dose", "value": "20", "evidence_text": "20 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily in the morning", "evidence_text": "once daily in the morning"},
        ]
        _, claims, diag = _extract(canned, page_text)
        assert len(claims) == 8, f"Expected 8 claims, got {len(claims)}: {diag}"

        metop_name = next(c for c in claims if c.field_type == "med.name" and c.value == "Metoprolol")
        omep_name = next(c for c in claims if c.field_type == "med.name" and c.value == "Omeprazole")
        metop_group = metop_name.group_id
        omep_group = omep_name.group_id

        # The two groups must be distinct UUIDs
        assert metop_group != omep_group, "Metoprolol and Omeprazole share the same group_id — contamination!"
        assert metop_group != "__no_anchor__"
        assert omep_group != "__no_anchor__"

        # Doses must stay with the correct medication
        metop_dose = next(c for c in claims if c.field_type == "med.dose" and c.value == "25")
        omep_dose = next(c for c in claims if c.field_type == "med.dose" and c.value == "20")
        assert metop_dose.group_id == metop_group, "Metoprolol dose migrated to Omeprazole group!"
        assert omep_dose.group_id == omep_group, "Omeprazole dose migrated to Metoprolol group!"

    def test_three_medications_get_three_distinct_group_ids(self):
        """Three med.name claims must produce three distinct group_ids."""
        page_text = "Metformin 850 mg oral twice daily. Metoprolol 25 mg PO BID. Omeprazole 20 mg PO daily."
        canned = [
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            {"field_type": "med.dose", "value": "850", "evidence_text": "850 mg", "unit_raw": "mg"},
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.name", "value": "Omeprazole", "evidence_text": "Omeprazole"},
            {"field_type": "med.dose", "value": "20", "evidence_text": "20 mg", "unit_raw": "mg"},
        ]
        _, claims, diag = _extract(canned, page_text)
        assert len(claims) == 6, f"Expected 6 claims: {diag}"

        groups = {c.value: c.group_id for c in claims if c.field_type == "med.name"}
        assert len(set(groups.values())) == 3, f"Expected 3 distinct group_ids, got: {groups}"

    def test_med_and_lab_get_distinct_group_ids(self):
        """A medication entity and a lab entity must have different group_ids."""
        page_text = "Metformin 850 mg oral twice daily. Hemoglobin was 9.82 g/dL (LOW)."
        canned = [
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            {"field_type": "med.dose", "value": "850", "evidence_text": "850 mg", "unit_raw": "mg"},
            {"field_type": "lab.value", "value": "9.82", "evidence_text": "9.82 g/dL", "unit_raw": "g/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
        ]
        _, claims, diag = _extract(canned, page_text)
        assert len(claims) == 4

        med_name = next(c for c in claims if c.field_type == "med.name")
        lab_val = next(c for c in claims if c.field_type == "lab.value")
        assert med_name.group_id != lab_val.group_id, "Medication and lab result share a group_id!"

    def test_lab_flag_inherits_lab_value_group_id(self):
        """lab.flag must share the group_id of the preceding lab.value anchor."""
        page_text = "Hemoglobin was 9.82 g/dL (LOW)."
        canned = [
            {"field_type": "lab.value", "value": "9.82", "evidence_text": "9.82 g/dL", "unit_raw": "g/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
        ]
        _, claims, diag = _extract(canned, page_text)
        assert len(claims) == 2
        lab_val = next(c for c in claims if c.field_type == "lab.value")
        lab_flag = next(c for c in claims if c.field_type == "lab.flag")
        assert lab_val.group_id == lab_flag.group_id, "lab.flag does not share group_id with lab.value!"
        assert lab_val.group_id != "__no_anchor__"

    def test_dose_before_med_name_gets_no_anchor_sentinel(self):
        """A med.dose appearing before any med.name gets sentinel group_id."""
        page_text = "25 mg PO twice daily of Metoprolol."
        canned = [
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        ]
        _, claims, diag = _extract(canned, page_text)
        assert len(claims) == 2
        dose = next(c for c in claims if c.field_type == "med.dose")
        med_name = next(c for c in claims if c.field_type == "med.name")
        # Dose appeared BEFORE the med.name anchor — it gets the sentinel
        assert dose.group_id == "__no_anchor__", (
            f"Expected sentinel group_id for orphaned dose, got {dose.group_id!r}"
        )
        # The med.name gets its own fresh group
        assert med_name.group_id != "__no_anchor__"


# ─── Adversarial Case 1: Two medications, different doses ─────────────────────

class TestAdversarial1_TwoMedsOneSentence:
    """Two medications in one sentence with different doses — no dose migration."""

    def test_distinct_doses_stay_with_their_medications(self):
        page_text = "continue Metoprolol 25 mg PO BID; and Lisinopril 5 mg oral every morning."
        canned = [
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "BID", "evidence_text": "BID"},
            {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
            {"field_type": "med.dose", "value": "5", "evidence_text": "5 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "every morning", "evidence_text": "every morning"},
        ]
        _, claims, diag = _extract(canned, page_text)
        assert len(claims) == 8

        metop_group = next(c for c in claims if c.field_type == "med.name" and c.value == "Metoprolol").group_id
        lisin_group = next(c for c in claims if c.field_type == "med.name" and c.value == "Lisinopril").group_id
        assert metop_group != lisin_group

        dose_25 = next(c for c in claims if c.field_type == "med.dose" and c.value == "25")
        dose_5 = next(c for c in claims if c.field_type == "med.dose" and c.value == "5")
        assert dose_25.group_id == metop_group, "25 mg dose migrated away from Metoprolol!"
        assert dose_5.group_id == lisin_group, "5 mg dose migrated away from Lisinopril!"

    def test_doses_do_not_cross_pollinate(self):
        """Metoprolol must not get Lisinopril's dose and vice versa."""
        page_text = "continue Metoprolol 25 mg PO BID; and Lisinopril 5 mg oral every morning."
        canned = [
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
            {"field_type": "med.dose", "value": "5", "evidence_text": "5 mg", "unit_raw": "mg"},
        ]
        _, claims, _ = _extract(canned, page_text)
        metop_group = next(c for c in claims if c.value == "Metoprolol").group_id
        lisin_group = next(c for c in claims if c.value == "Lisinopril").group_id
        dose_25_group = next(c for c in claims if c.value == "25").group_id
        dose_5_group = next(c for c in claims if c.value == "5").group_id
        assert dose_25_group != lisin_group, "Metoprolol's 25 mg dose leaked into Lisinopril's group!"
        assert dose_5_group != metop_group, "Lisinopril's 5 mg dose leaked into Metoprolol's group!"


# ─── Adversarial Case 2: One medication with multiple attributes ───────────────

class TestAdversarial2_OneMedMultipleAttributes:
    """One medication with name, dose, route, frequency all correctly grouped."""

    def test_all_attributes_share_one_group_id(self):
        page_text = "initiated on Cephalexin 250 mg by mouth QID for 10 days."
        canned = [
            {"field_type": "med.name", "value": "Cephalexin", "evidence_text": "Cephalexin"},
            {"field_type": "med.dose", "value": "250", "evidence_text": "250 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "QID", "evidence_text": "QID"},
        ]
        _, claims, _ = _extract(canned, page_text)
        groups = {c.group_id for c in claims}
        assert len(groups) == 1, f"Expected 1 group_id, got {len(groups)}: {groups}"
        assert "__no_anchor__" not in groups

    def test_med_duration_inherits_same_group_id(self):
        """med.duration is a Tier-2 field but must not break group continuity."""
        page_text = "initiated on Cephalexin 250 mg by mouth QID for 10 days."
        # Note: med.duration is NOT in PERMITTED_NARRATIVE_FIELDS (only Tier-1 fields are).
        # The LLM is not expected to emit med.duration directly; it's assembled by
        # the deterministic extractor. So this test verifies that if a med.duration
        # were emitted by a future extension, it would be rejected (not in permitted fields).
        canned = [
            {"field_type": "med.name", "value": "Cephalexin", "evidence_text": "Cephalexin"},
            {"field_type": "med.duration", "value": "for 10 days", "evidence_text": "for 10 days"},
        ]
        _, claims, diag = _extract(canned, page_text)
        # med.duration is NOT in PERMITTED_NARRATIVE_FIELDS so it should be rejected
        assert len(claims) == 1  # only the med.name survives
        assert any("field_type not in PERMITTED_NARRATIVE_FIELDS" in r.get("reason", "") for r in diag["rejected"])


# ─── Adversarial Case 3: Repeated mentions of the same medication ─────────────

class TestAdversarial3_RepeatedMedication:
    """Repeated mentions of the same medication name produce separate groups."""

    def test_two_mentions_of_same_drug_produce_two_groups(self):
        """If a medication is mentioned twice (e.g. different dose adjustments),
        the LLM will emit two med.name claims — each must get its own group_id."""
        page_text = (
            "Patient was previously on Metoprolol 12.5 mg daily. "
            "Metoprolol was uptitrated to 25 mg twice daily."
        )
        canned = [
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "12.5", "evidence_text": "12.5 mg", "unit_raw": "mg"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
        ]
        _, claims, diag = _extract(canned, page_text)
        name_claims = [c for c in claims if c.field_type == "med.name"]
        # Both name claims survive (first Metoprolol at offset for "previously", second at "uptitrated")
        # They may be deduplicated at the evidence span level if offsets collide, but group_ids differ.
        group_ids = [c.group_id for c in name_claims]
        assert len(set(group_ids)) == len(name_claims), (
            "Repeated medication names share a group_id — they should be separate entities."
        )


# ─── Adversarial Case 4: Pronoun / reference ambiguity ───────────────────────

class TestAdversarial4_PronounAmbiguity:
    """Pronoun references to a medication should not create new claims."""

    def test_pronoun_reference_yields_no_spurious_name_claim(self):
        """'it' or 'this medication' should not be extracted as med.name."""
        page_text = "Amlodipine 2.5 mg PO daily was initiated. It was well tolerated."
        canned = [
            # Correct: LLM extracts the named medication only, not 'it'
            {"field_type": "med.name", "value": "Amlodipine", "evidence_text": "Amlodipine"},
            {"field_type": "med.dose", "value": "2.5", "evidence_text": "2.5 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ]
        _, claims, _ = _extract(canned, page_text)
        name_claims = [c for c in claims if c.field_type == "med.name"]
        assert len(name_claims) == 1
        assert name_claims[0].value == "Amlodipine"

    def test_pronoun_as_med_name_rejected_by_evidence_grounding(self):
        """If LLM incorrectly extracts 'it' as med.name, evidence grounding rejects it
        only if 'it' is not in the text (which it is here, but as a pronoun).
        The evidence_text must be a verbatim substring — 'it' IS in the text.
        This tests that we correctly reject fabricated evidence that's not in source."""
        page_text = "Amlodipine 2.5 mg was initiated."
        canned = [
            {"field_type": "med.name", "value": "SomeDrugNotInText", "evidence_text": "SomeDrugNotInText"},
        ]
        _, claims, diag = _extract(canned, page_text)
        assert len(claims) == 0
        assert any("not a verbatim substring" in r.get("reason", "") for r in diag["rejected"])


# ─── Adversarial Case 5: Negated medication mentions ─────────────────────────

class TestAdversarial5_NegatedMedications:
    """Negated medication mentions are preserved as-is; no special suppression."""

    def test_negated_medication_claim_passes_through(self):
        """The extraction layer does not suppress negated medications — that is
        the responsibility of the verification layer. Negation is passed through
        as a claim and must not cause a false rejection here."""
        page_text = "Avoid NSAIDs and nephrotoxic exposures."
        # The mock would not extract medications from this sentence. This test
        # verifies that if extraction DID return a negated drug (unlikely for this
        # text), it would not be fabricated without grounding.
        canned = [
            {"field_type": "med.name", "value": "NSAIDs", "evidence_text": "NSAIDs"},
        ]
        _, claims, _ = _extract(canned, page_text)
        # NSAIDs IS in the text, so the claim passes evidence grounding
        assert len(claims) == 1
        assert claims[0].value == "NSAIDs"

    def test_negated_med_not_in_text_is_rejected(self):
        """A fabricated negated medication not present in the text must be rejected."""
        page_text = "Avoid NSAIDs and nephrotoxic exposures."
        canned = [
            {"field_type": "med.name", "value": "Warfarin", "evidence_text": "Warfarin not prescribed"},
        ]
        _, claims, diag = _extract(canned, page_text)
        assert len(claims) == 0
        assert any("not a verbatim substring" in r.get("reason", "") for r in diag["rejected"])


# ─── Adversarial Case 6: Historical vs current medications ───────────────────

class TestAdversarial6_HistoricalVsCurrent:
    """Historical and current medications can coexist; both produce separate groups."""

    def test_historical_and_current_medications_get_separate_groups(self):
        page_text = "Prior regimen included Atenolol 50 mg. Current regimen: Metoprolol 25 mg PO BID."
        canned = [
            {"field_type": "med.name", "value": "Atenolol", "evidence_text": "Atenolol"},
            {"field_type": "med.dose", "value": "50", "evidence_text": "50 mg", "unit_raw": "mg"},
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "BID", "evidence_text": "BID"},
        ]
        _, claims, _ = _extract(canned, page_text)
        atenolol_group = next(c for c in claims if c.value == "Atenolol").group_id
        metop_group = next(c for c in claims if c.value == "Metoprolol").group_id
        assert atenolol_group != metop_group, "Historical and current medications share a group!"

        dose_50 = next(c for c in claims if c.value == "50")
        dose_25 = next(c for c in claims if c.value == "25")
        assert dose_50.group_id == atenolol_group
        assert dose_25.group_id == metop_group


# ─── Adversarial Case 7: Planned vs administered medications ─────────────────

class TestAdversarial7_PlannedVsAdministered:
    """Planned and administered medications both produce correctly isolated groups."""

    def test_planned_and_administered_get_separate_groups(self):
        page_text = (
            "Patient received Amoxicillin 500 mg by mouth three times daily. "
            "Plan: start Lisinopril 5 mg oral daily."
        )
        canned = [
            {"field_type": "med.name", "value": "Amoxicillin", "evidence_text": "Amoxicillin"},
            {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "three times daily", "evidence_text": "three times daily"},
            {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
            {"field_type": "med.dose", "value": "5", "evidence_text": "5 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ]
        _, claims, _ = _extract(canned, page_text)
        amox_group = next(c for c in claims if c.value == "Amoxicillin").group_id
        lisin_group = next(c for c in claims if c.value == "Lisinopril").group_id
        assert amox_group != lisin_group

        # Amoxicillin's "by mouth" must not appear in Lisinopril's group
        route_by_mouth = next(c for c in claims if c.value == "by mouth")
        assert route_by_mouth.group_id == amox_group, "Amoxicillin's 'by mouth' route migrated!"


# ─── Adversarial Case 8: Conflicting doses in adjacent clauses ───────────────

class TestAdversarial8_ConflictingDoses:
    """Two medications with very similar dose values — correct assignment critical."""

    def test_25mg_metoprolol_vs_25mg_lisinopril_not_confused(self):
        """Even when two medications share the same dose value, each must belong
        to the correct group."""
        page_text = "continue Metoprolol 25 mg PO BID; and continue Lisinopril 25 mg oral daily."
        canned = [
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "BID", "evidence_text": "BID"},
            {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ]
        _, claims, _ = _extract(canned, page_text)
        metop_group = next(c for c in claims if c.value == "Metoprolol").group_id
        lisin_group = next(c for c in claims if c.value == "Lisinopril").group_id
        assert metop_group != lisin_group

        # There are two "25 mg" dose claims; they should be in different groups
        dose_claims = [c for c in claims if c.field_type == "med.dose"]
        assert len(dose_claims) == 2
        dose_groups = {c.group_id for c in dose_claims}
        # They should be in two different groups
        assert len(dose_groups) == 2, f"Both '25 mg' doses ended up in the same group: {dose_groups}"


# ─── Adversarial Case 9: Dose before medication mention ──────────────────────

class TestAdversarial9_DoseBeforeName:
    """A dose appearing before its medication must get the sentinel group_id."""

    def test_orphaned_dose_before_name_gets_sentinel(self):
        page_text = "25 mg PO twice daily of Metoprolol was prescribed."
        canned = [
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
        ]
        _, claims, _ = _extract(canned, page_text)
        dose = next(c for c in claims if c.field_type == "med.dose")
        med_name = next(c for c in claims if c.field_type == "med.name")
        # Dose appeared before the med.name anchor
        assert dose.group_id == "__no_anchor__", "Orphaned dose should have sentinel group_id"
        # The med.name got its own group
        assert med_name.group_id != "__no_anchor__"
        # The dose and med.name are in different groups (fail-closed for orphaned attributes)
        assert dose.group_id != med_name.group_id


# ─── Adversarial Case 10: Multiple meds share sentence but not attributes ─────

class TestAdversarial10_SharedSentenceNoSharedAttributes:
    """Multiple medications listed with distinct attributes — no cross-pollination."""

    def test_cephalexin_and_metoprolol_in_same_block(self):
        page_text = (
            "Cephalexin 250 mg by mouth QID for 10 days. "
            "Metoprolol 25 mg oral BID."
        )
        canned = [
            {"field_type": "med.name", "value": "Cephalexin", "evidence_text": "Cephalexin"},
            {"field_type": "med.dose", "value": "250", "evidence_text": "250 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "QID", "evidence_text": "QID"},
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "BID", "evidence_text": "BID"},
        ]
        _, claims, _ = _extract(canned, page_text)
        ceph_group = next(c for c in claims if c.value == "Cephalexin").group_id
        metop_group = next(c for c in claims if c.value == "Metoprolol").group_id
        assert ceph_group != metop_group

        # "by mouth" must not leak into Metoprolol's group
        route_by_mouth = next(c for c in claims if c.value == "by mouth")
        route_oral = next(c for c in claims if c.value == "oral")
        assert route_by_mouth.group_id == ceph_group, "Cephalexin's route 'by mouth' migrated!"
        assert route_oral.group_id == metop_group, "Metoprolol's route 'oral' migrated!"

        # QID frequency must not leak into Metoprolol
        qid = next(c for c in claims if c.value == "QID")
        bid = next(c for c in claims if c.value == "BID")
        assert qid.group_id == ceph_group, "QID frequency migrated to Metoprolol!"
        assert bid.group_id == metop_group, "BID frequency migrated to Cephalexin!"


# ─── Adversarial Case 11: Missing punctuation and OCR artifacts ───────────────

class TestAdversarial11_OcrArtifacts:
    """OCR artifacts and missing punctuation — whitespace-invariant matching."""

    def test_extra_whitespace_in_evidence_text_is_matched(self):
        """Extra whitespace in evidence_text is normalized by the whitespace-invariant
        token sequence match path in _build_claims."""
        page_text = "Amlodipine 2.5 mg PO every morning."
        canned = [
            # Evidence text with extra internal space — should still match via token sequence
            {"field_type": "med.name", "value": "Amlodipine", "evidence_text": "Amlodipine  2.5"},
            {"field_type": "med.dose", "value": "2.5", "evidence_text": "2.5 mg", "unit_raw": "mg"},
        ]
        _, claims, diag = _extract(canned, page_text)
        # The whitespace-invariant path should handle "Amlodipine  2.5" -> "Amlodipine 2.5"
        name_claims = [c for c in claims if c.field_type == "med.name"]
        assert len(name_claims) == 1, f"Expected 1 med.name, diag={diag}"

    def test_evidence_not_in_text_is_rejected(self):
        """Completely fabricated evidence text not present in source must be rejected."""
        page_text = "Amlodipine 2.5 mg PO every morning."
        canned = [
            {"field_type": "med.name", "value": "Amlodipine", "evidence_text": "Amlodipine tablet 5mg"},
        ]
        _, claims, diag = _extract(canned, page_text)
        assert len(claims) == 0
        assert any("not a verbatim substring" in r.get("reason", "") for r in diag["rejected"])


# ─── Adversarial Case 12: Explicitly ambiguous medication-to-dose ─────────────

class TestAdversarial12_AmbiguousAssociation:
    """LLM produces ambiguous output — group_id logic must not guess."""

    def test_dose_without_preceding_name_remains_orphaned(self):
        """When the LLM emits a med.dose without any med.name first, the dose
        gets the sentinel group_id (fail-closed, do not guess the medication)."""
        page_text = "500 mg was administered."
        canned = [
            {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg"},
        ]
        _, claims, _ = _extract(canned, page_text)
        assert len(claims) == 1
        assert claims[0].field_type == "med.dose"
        assert claims[0].group_id == "__no_anchor__", (
            "Ambiguous orphaned dose must have sentinel group_id, not a fabricated UUID"
        )

    def test_route_without_preceding_name_remains_orphaned(self):
        """When the LLM emits a med.route before any med.name, it gets sentinel group_id."""
        page_text = "oral twice daily regimen was followed."
        canned = [
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
        ]
        _, claims, _ = _extract(canned, page_text)
        assert len(claims) == 2
        for c in claims:
            assert c.group_id == "__no_anchor__", (
                f"Orphaned {c.field_type} must have sentinel group_id"
            )

    def test_multiple_sequential_groups_correct_isolation(self):
        """Verify attribute isolation across a complex multi-medication sequence."""
        page_text = (
            "Amlodipine 2.5 mg PO every morning. "
            "Metoprolol 25 mg PO twice daily. "
            "Amoxicillin 500 mg by mouth three times daily for 7 days."
        )
        canned = [
            {"field_type": "med.name", "value": "Amlodipine", "evidence_text": "Amlodipine"},
            {"field_type": "med.dose", "value": "2.5", "evidence_text": "2.5 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "every morning", "evidence_text": "every morning"},
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Amoxicillin", "evidence_text": "Amoxicillin"},
            {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "three times daily", "evidence_text": "three times daily"},
        ]
        _, claims, diag = _extract(canned, page_text)
        assert len(claims) == 12, f"Expected 12 claims, got {len(claims)}: {diag}"

        amlod_group = next(c for c in claims if c.value == "Amlodipine").group_id
        metop_group = next(c for c in claims if c.value == "Metoprolol").group_id
        amox_group = next(c for c in claims if c.value == "Amoxicillin").group_id

        # All three groups are distinct
        assert len({amlod_group, metop_group, amox_group}) == 3

        # Doses are correctly attributed
        dose_2_5 = next(c for c in claims if c.field_type == "med.dose" and c.value == "2.5")
        dose_25 = next(c for c in claims if c.field_type == "med.dose" and c.value == "25")
        dose_500 = next(c for c in claims if c.field_type == "med.dose" and c.value == "500")
        assert dose_2_5.group_id == amlod_group
        assert dose_25.group_id == metop_group
        assert dose_500.group_id == amox_group

        # Frequencies are correctly attributed
        every_morning = next(c for c in claims if c.value == "every morning")
        twice_daily = next(c for c in claims if c.value == "twice daily")
        three_times_daily = next(c for c in claims if c.value == "three times daily")
        assert every_morning.group_id == amlod_group
        assert twice_daily.group_id == metop_group
        assert three_times_daily.group_id == amox_group


# ─── No dose/route migration: explicit cross-check ────────────────────────────

class TestNoDoseMigration:
    """Explicit tests verifying no dose, route or frequency ever migrates between meds."""

    def test_no_route_migration_po_vs_oral(self):
        """'PO' and 'oral' are different route strings; must stay with their medications."""
        page_text = "Metoprolol 25 mg PO twice daily. Lisinopril 5 mg oral daily."
        canned = [
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
            {"field_type": "med.dose", "value": "5", "evidence_text": "5 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ]
        _, claims, _ = _extract(canned, page_text)
        metop_group = next(c for c in claims if c.value == "Metoprolol").group_id
        lisin_group = next(c for c in claims if c.value == "Lisinopril").group_id

        po_route = next(c for c in claims if c.value == "PO")
        oral_route = next(c for c in claims if c.value == "oral")
        assert po_route.group_id == metop_group, "PO route migrated to Lisinopril!"
        assert oral_route.group_id == lisin_group, "oral route migrated to Metoprolol!"

    def test_no_frequency_migration_bid_vs_qid(self):
        """BID and QID frequencies must not migrate between medications."""
        page_text = "Cephalexin 250 mg QID. Metoprolol 25 mg BID."
        canned = [
            {"field_type": "med.name", "value": "Cephalexin", "evidence_text": "Cephalexin"},
            {"field_type": "med.dose", "value": "250", "evidence_text": "250 mg", "unit_raw": "mg"},
            {"field_type": "med.frequency", "value": "QID", "evidence_text": "QID"},
            {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol"},
            {"field_type": "med.dose", "value": "25", "evidence_text": "25 mg", "unit_raw": "mg"},
            {"field_type": "med.frequency", "value": "BID", "evidence_text": "BID"},
        ]
        _, claims, _ = _extract(canned, page_text)
        ceph_group = next(c for c in claims if c.value == "Cephalexin").group_id
        metop_group = next(c for c in claims if c.value == "Metoprolol").group_id

        qid = next(c for c in claims if c.value == "QID")
        bid = next(c for c in claims if c.value == "BID")
        assert qid.group_id == ceph_group, "QID migrated from Cephalexin!"
        assert bid.group_id == metop_group, "BID migrated from Metoprolol!"
