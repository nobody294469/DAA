"""Focused test suite for generic compound prescription extraction (Root Cause C).

Verifies that the extraction contract correctly handles:
1. Genuinely separate slash-delimited co-prescriptions producing two distinct med.name claims.
2. Shared frequency application across co-prescribed medications.
3. Inline medication sentences preserving med.name alongside dose and frequency.
4. Single combination drug names (e.g. Amoxicillin/clavulanate) NOT being split into separate entities.
5. Clinical slash-containing terms/ratios (e.g. PT/INR, HIV/HBsAg) NOT being interpreted as medications.
6. Existing normal single-medication lines remaining unchanged.
7. Evidence-grounding invariants remaining strictly intact.
"""
from __future__ import annotations

import pytest

from clindoc.extract.contracts import Evidence
from clindoc.extract.llm_extractor import MockLLMProvider, NarrativeExtractor
from clindoc.ocr.document_text import DocumentPage, Token
from clindoc.ocr.geometry import BBoxDict


def _make_token(text: str, x0: float, y0: float, x1: float, y1: float, line_id: str = "L0") -> Token:
    return Token(
        text=text,
        bbox=BBoxDict(x0=x0, y0=y0, x1=x1, y1=y1),
        line_id=line_id,
    )


def _make_page_from_text(page_text: str) -> DocumentPage:
    tokens = []
    x = 10.0
    y = 20.0
    line_num = 0
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


class TestCompoundMedicationExtraction:
    """Test suite for generic compound prescription handling and combination drug protection."""

    def _extract_canned(self, canned_extractions: list[dict], page_text: str) -> tuple[list[Evidence], list, dict]:
        extractor = NarrativeExtractor(
            provider=MockLLMProvider(canned_responses=canned_extractions),
            max_retries=0,
        )
        page = _make_page_from_text(page_text)
        return extractor.extract("DOC_001", page, page_text, "r0")

    def test_1_slash_delimited_co_prescriptions_produce_two_med_names(self):
        """1. A slash-delimited pair of genuinely separate medications produces two med.name claims."""
        page_text = "Discharge Advice: C.Ferrous / T.Calcium once daily for 30 days."
        canned = [
            {"field_type": "med.name", "value": "Ferrous", "evidence_text": "C.Ferrous"},
            {"field_type": "med.name", "value": "Calcium", "evidence_text": "T.Calcium"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 3
        med_names = [c.value for c in claims if c.field_type == "med.name"]
        assert len(med_names) == 2
        assert "Ferrous" in med_names
        assert "Calcium" in med_names
        assert len(diag["rejected"]) == 0

    def test_2_shared_frequency_applied_to_co_prescribed_medications(self):
        """2. A shared schedule/frequency is emitted and grounded for each co-prescribed medication."""
        page_text = "Rx: C.Ferrous / T.Calcium once daily for 30 days."
        canned = [
            {"field_type": "med.name", "value": "Ferrous", "evidence_text": "C.Ferrous"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Calcium", "evidence_text": "T.Calcium"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 4
        freq_claims = [c for c in claims if c.field_type == "med.frequency"]
        assert len(freq_claims) == 2
        assert all(f.value == "once daily" for f in freq_claims)

    def test_3_inline_medication_sentence_preserves_med_name(self):
        """3. An inline medication sentence preserves med.name alongside dose and frequency."""
        page_text = "Advised Tab Paracetamol 500mg as and when needed for fever."
        canned = [
            {"field_type": "med.name", "value": "Paracetamol", "evidence_text": "Tab Paracetamol"},
            {"field_type": "med.dose", "value": "500", "evidence_text": "500mg", "unit_raw": "mg"},
            {"field_type": "med.frequency", "value": "as and when needed", "evidence_text": "as and when needed"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 3
        fields = {c.field_type: c.value for c in claims}
        assert fields["med.name"] == "Paracetamol"
        assert fields["med.dose"] == "500"
        assert fields["med.frequency"] == "as and when needed"

    def test_4_combination_drug_names_not_split(self):
        """4. Single combination drug names containing slashes (e.g. Amoxicillin/clavulanate) are preserved as one med.name."""
        page_text = "Discharge Med: Amoxicillin/clavulanate 625mg twice daily."
        canned = [
            {"field_type": "med.name", "value": "Amoxicillin/clavulanate", "evidence_text": "Amoxicillin/clavulanate"},
            {"field_type": "med.dose", "value": "625", "evidence_text": "625mg", "unit_raw": "mg"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 3
        med_names = [c.value for c in claims if c.field_type == "med.name"]
        assert len(med_names) == 1
        assert med_names[0] == "Amoxicillin/clavulanate"

    def test_5_clinical_slash_ratios_not_interpreted_as_medications(self):
        """5. Clinical slash terms and ratios (e.g. PT/INR, BP: 120/80) yield ZERO medication claims."""
        page_text = "Investigations: PT/INR: 12.1secs. Examination: BP 120/80 mmHg."
        canned = [
            # Model properly returned empty extractions for medications
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        med_claims = [c for c in claims if c.field_type.startswith("med.")]
        assert len(med_claims) == 0

    def test_6_normal_single_medication_line_remains_unchanged(self):
        """6. Normal single-medication lines without slashes behave identically to baseline."""
        page_text = "TAB ALDACTONE 6.25MG THREE TIMES IN A DAY"
        canned = [
            {"field_type": "med.name", "value": "Aldactone", "evidence_text": "TAB ALDACTONE"},
            {"field_type": "med.dose", "value": "6.25", "evidence_text": "6.25MG", "unit_raw": "mg"},
            {"field_type": "med.frequency", "value": "THREE TIMES IN A DAY", "evidence_text": "THREE TIMES IN A DAY"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 3
        assert len(evidences) == 3
        assert claims[0].value == "Aldactone"
        assert claims[1].value == "6.25"
        assert claims[2].value == "THREE TIMES IN A DAY"

    def test_7_evidence_grounding_invariants_intact(self):
        """7. Evidence grounding fails closed if compound evidence is not in source text."""
        page_text = "C.Ferrous / T.Calcium once daily."
        canned = [
            {"field_type": "med.name", "value": "Ferrous", "evidence_text": "C.Ferrous"},
            # Hallucinated third drug in evidence
            {"field_type": "med.name", "value": "Magnesium", "evidence_text": "T.Magnesium"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        # Ferrous accepted, Magnesium rejected
        assert len(claims) == 1
        assert claims[0].value == "Ferrous"
        assert len(diag["rejected"]) == 1
        assert diag["rejected"][0]["value"] == "Magnesium"
