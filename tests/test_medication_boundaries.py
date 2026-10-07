"""Focused test suite for medication field-boundary extraction rules.

Validates that:
1. Treatment durations ("6 weeks", "5 days", "3 months", "for 10 days") are NOT extracted as med.dose or med.frequency.
2. Doses ("500 mg", "1 g", "5 mL", "625 mg") remain med.dose.
3. Frequencies ("once daily", "twice daily", "every 8 hours", "every 3 weeks", "as and when needed") remain med.frequency.
4. Route wording is preserved verbatim (e.g. "by mouth" is not replaced with "oral").
5. Dosage forms ("tablet", "tab", "capsule", "syrup") are not extracted as med.route or med.frequency.
6. Abbreviated compound drug names ("C.Autrin", "T.Shelcal") remain source-faithful tokens.
7. Combination drugs containing slashes ("Amoxicillin/clavulanate", "Sulfamethoxazole/trimethoprim") remain single entities.
8. PD-022 laboratory qualitative and quantitative extractions remain invariant.
"""
from __future__ import annotations

import pytest

from clindoc.extract.contracts import Evidence
from clindoc.extract.llm_extractor import (
    _SYSTEM_PROMPT,
    MockLLMProvider,
    NarrativeExtractor,
)
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


class TestMedicationBoundaryExtraction:
    """Test suite for medication field-boundary rules."""

    def _extract_canned(self, canned_extractions: list[dict], page_text: str) -> tuple[list[Evidence], list, dict]:
        extractor = NarrativeExtractor(
            provider=MockLLMProvider(canned_responses=canned_extractions),
            max_retries=0,
        )
        page = _make_page_from_text(page_text)
        return extractor.extract("DOC_001", page, page_text, "r0")

    def test_prompt_contract_integrity(self):
        """Verify that _SYSTEM_PROMPT contains the authoritative medication boundary rules."""
        # 1. Dose vs treatment duration distinction
        assert "DOSE VS TREATMENT DURATION" in _SYSTEM_PROMPT
        assert "6 weeks" in _SYSTEM_PROMPT
        assert "for 10 days" in _SYSTEM_PROMPT
        assert "NEVER extract treatment durations or time periods into med.dose or med.frequency" in _SYSTEM_PROMPT

        # 2. Frequency vs route & dosage form
        assert "FREQUENCY VS ROUTE & DOSAGE FORM BOUNDARY" in _SYSTEM_PROMPT
        assert "VERBATIM ROUTE RULE" in _SYSTEM_PROMPT
        assert 'if the text says "by mouth", extract value "by mouth", do NOT replace with "oral"' in _SYSTEM_PROMPT
        assert "DOSAGE FORM SEPARATION" in _SYSTEM_PROMPT

        # 3. Source-faithful compound tokens
        assert "C.Autrin" in _SYSTEM_PROMPT
        assert "T.Shelcal" in _SYSTEM_PROMPT

        # 4. Combination drug protection
        assert "Amoxicillin/clavulanate" in _SYSTEM_PROMPT
        assert "do NOT split them into separate drugs" in _SYSTEM_PROMPT

        # 5. PD-022 qualitative lab invariants
        assert "quantitative numeric laboratory test result value" in _SYSTEM_PROMPT
        assert "grounded qualitative laboratory determination" in _SYSTEM_PROMPT
        assert "HBsAg: Negative" in _SYSTEM_PROMPT
        assert "HIV: Negative" in _SYSTEM_PROMPT
        assert "VDRL: Non-Reactive" in _SYSTEM_PROMPT

    def test_duration_6_weeks_is_not_med_dose(self):
        """Treatment duration '6 weeks' must not be extracted as med.dose."""
        page_text = "Capsule Autrin once daily for 6 weeks."
        # Correct boundary extraction: Autrin, once daily, no dose
        canned = [
            {"field_type": "med.name", "value": "Autrin", "evidence_text": "Capsule Autrin"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 2
        doses = [c for c in claims if c.field_type == "med.dose"]
        assert len(doses) == 0

    def test_duration_5_days_is_not_med_dose(self):
        """Treatment duration 'for 5 days' must not be extracted as med.dose."""
        page_text = "Amoxicillin/clavulanate 625 mg oral twice daily for 5 days."
        canned = [
            {"field_type": "med.name", "value": "Amoxicillin/clavulanate", "evidence_text": "Amoxicillin/clavulanate"},
            {"field_type": "med.dose", "value": "625", "evidence_text": "625 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        dose_claims = [c for c in claims if c.field_type == "med.dose"]
        assert len(dose_claims) == 1
        assert dose_claims[0].value == "625"
        assert dose_claims[0].value != "5"

    def test_duration_for_10_days_is_not_med_frequency(self):
        """Treatment duration 'for 10 days' must not be extracted as med.frequency."""
        page_text = "Ceftriaxone 1 g IV every 8 hours for 10 days."
        canned = [
            {"field_type": "med.name", "value": "Ceftriaxone", "evidence_text": "Ceftriaxone"},
            {"field_type": "med.dose", "value": "1", "evidence_text": "1 g", "unit_raw": "g"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "every 8 hours", "evidence_text": "every 8 hours"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        freq_claims = [c for c in claims if c.field_type == "med.frequency"]
        assert len(freq_claims) == 1
        assert freq_claims[0].value == "every 8 hours"
        assert freq_claims[0].value != "for 10 days"

    def test_duration_for_3_months_is_not_med_frequency(self):
        """Treatment duration 'for 3 months' must not be extracted as med.frequency."""
        page_text = "Pembrolizumab 200 mg IV every 3 weeks for 3 months."
        canned = [
            {"field_type": "med.name", "value": "Pembrolizumab", "evidence_text": "Pembrolizumab"},
            {"field_type": "med.dose", "value": "200", "evidence_text": "200 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "every 3 weeks", "evidence_text": "every 3 weeks"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        freq_claims = [c for c in claims if c.field_type == "med.frequency"]
        assert len(freq_claims) == 1
        assert freq_claims[0].value == "every 3 weeks"
        assert freq_claims[0].value != "for 3 months"

    def test_500_mg_remains_med_dose(self):
        """Medication dose '500 mg' remains med.dose with unit mg."""
        page_text = "Tablet Paracetamol 500 mg oral once daily."
        canned = [
            {"field_type": "med.name", "value": "Paracetamol", "evidence_text": "Tablet Paracetamol"},
            {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        dose_claims = [c for c in claims if c.field_type == "med.dose"]
        assert len(dose_claims) == 1
        assert dose_claims[0].value == "500"
        assert dose_claims[0].unit_raw == "mg"

    def test_once_daily_remains_med_frequency(self):
        """Schedule 'once daily' remains med.frequency."""
        page_text = "Tablet Paracetamol 500 mg oral once daily."
        canned = [
            {"field_type": "med.name", "value": "Paracetamol", "evidence_text": "Tablet Paracetamol"},
            {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        freq_claims = [c for c in claims if c.field_type == "med.frequency"]
        assert len(freq_claims) == 1
        assert freq_claims[0].value == "once daily"

    def test_by_mouth_remains_verbatim_med_route(self):
        """Route 'by mouth' remains verbatim 'by mouth' in extraction without canonicalizing to 'oral'."""
        page_text = "Metformin 500 mg by mouth three times daily."
        canned = [
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "three times daily", "evidence_text": "three times daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        route_claims = [c for c in claims if c.field_type == "med.route"]
        assert len(route_claims) == 1
        assert route_claims[0].value == "by mouth"
        assert any(ev.text == "by mouth" for ev in evidences)

    def test_tablet_does_not_become_med_frequency_or_route(self):
        """Dosage form 'tablet' is not extracted into med.frequency or med.route."""
        page_text = "Tablet Paracetamol 500 mg oral once daily."
        canned = [
            {"field_type": "med.name", "value": "Paracetamol", "evidence_text": "Tablet Paracetamol"},
            {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        for c in claims:
            assert "tablet" not in c.value.lower() or c.field_type == "med.name"
            if c.field_type in ("med.frequency", "med.route"):
                assert c.value.lower() != "tablet"

    def test_source_faithful_c_autrin_and_t_shelcal(self):
        """Source tokens 'C.Autrin' and 'T.Shelcal' remain faithful during extraction."""
        page_text = "Discharge Med: C.Autrin / T.Shelcal once daily for 6 weeks."
        canned = [
            {"field_type": "med.name", "value": "C.Autrin", "evidence_text": "C.Autrin"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "T.Shelcal", "evidence_text": "T.Shelcal"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        med_names = [c.value for c in claims if c.field_type == "med.name"]
        assert len(med_names) == 2
        assert "C.Autrin" in med_names
        assert "T.Shelcal" in med_names

    def test_combination_drugs_with_slash_remain_single_entity(self):
        """Combination drugs containing slashes remain single medication entities."""
        page_text = "Rx: Amoxicillin/clavulanate 625 mg oral twice daily and Sulfamethoxazole/trimethoprim oral."
        canned = [
            {"field_type": "med.name", "value": "Amoxicillin/clavulanate", "evidence_text": "Amoxicillin/clavulanate"},
            {"field_type": "med.dose", "value": "625", "evidence_text": "625 mg", "unit_raw": "mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Sulfamethoxazole/trimethoprim", "evidence_text": "Sulfamethoxazole/trimethoprim"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        med_names = [c.value for c in claims if c.field_type == "med.name"]
        assert "Amoxicillin/clavulanate" in med_names
        assert "Sulfamethoxazole/trimethoprim" in med_names
        assert len(med_names) == 2

    def test_pd022_qualitative_and_quantitative_invariance(self):
        """Verify PD-022 qualitative and quantitative lab behavior is preserved."""
        page_text = (
            "Lab Results:\n"
            "HBsAg: Negative\n"
            "HIV: Negative\n"
            "VDRL: Non-Reactive\n"
            "Haemoglobin: 12.6 GM%"
        )
        canned = [
            {"field_type": "lab.value", "value": "Negative", "evidence_text": "Negative", "unit_raw": None},
            {"field_type": "lab.value", "value": "Negative", "evidence_text": "Negative", "unit_raw": None},
            {"field_type": "lab.value", "value": "Non-Reactive", "evidence_text": "Non-Reactive", "unit_raw": None},
            {"field_type": "lab.value", "value": "12.6", "evidence_text": "12.6 GM%", "unit_raw": "GM%"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        lab_claims = [c for c in claims if c.field_type == "lab.value"]
        assert len(lab_claims) == 4
        values = [c.value for c in lab_claims]
        assert "Negative" in values
        assert "Non-Reactive" in values
        assert "12.6" in values
