"""Focused regression test suite for whitespace-invariant grounding (§7.4 / Root Cause A).

Verifies that NarrativeExtractor allows whitespace variations (spaces, tabs, newlines)
between tokens without weakening the verbatim anti-hallucination boundary.

Requirements tested:
1. Exact existing substring still passes.
2. OCR spaces vs model spaces passes.
3. OCR newlines vs model spaces passes (the CMC Vellore failure case).
4. Multiple whitespace characters pass (tabs, irregular spaces, multiple newlines).
5. Different actual words do NOT pass (fail-closed).
6. Partial unrelated text does NOT pass (fail-closed).
7. Punctuation/character substitutions do NOT become accepted (fail-closed).
8. Existing grounding behavior remains unchanged for normal cases.
9. Actual source span and bounding-box derivation remain geometrically correct.
"""
from __future__ import annotations

import pytest

from clindoc.extract.contracts import BBoxModel, Evidence
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
    """Helper creating a DocumentPage with spatial tokens for each word."""
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


class TestWhitespaceInvariantGrounding:
    """Test suite for Root Cause A whitespace normalization in NarrativeExtractor."""

    def _extract_canned(self, canned_extractions: list[dict], page_text: str) -> tuple[list[Evidence], list, dict]:
        extractor = NarrativeExtractor(
            provider=MockLLMProvider(canned_responses=canned_extractions),
            max_retries=0,
        )
        page = _make_page_from_text(page_text)
        return extractor.extract("DOC_001", page, page_text, "r0")

    def test_1_exact_substring_passes(self):
        """1. Exact existing substring still passes identical to baseline."""
        page_text = "Patient takes Metformin 500 mg orally daily."
        canned = [
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 2
        assert len(evidences) == 2
        assert len(diag["rejected"]) == 0
        assert evidences[0].text == "Metformin"
        assert evidences[1].text == "500 mg"

    def test_2_ocr_spaces_vs_model_spaces_passes(self):
        """2. Single spaces in OCR text matched by single spaces in model output."""
        page_text = "HAEMOGLOBIN 12.6 GM%"
        canned = [
            {"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12.6 GM%", "unit_raw": "GM%"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 1
        assert len(evidences) == 1
        assert len(diag["rejected"]) == 0
        assert evidences[0].text == "HAEMOGLOBIN 12.6 GM%"

    def test_3_ocr_newlines_vs_model_spaces_passes(self):
        """3. OCR vertical newlines matched by model space-separated evidence text (CMC Vellore defect)."""
        page_text = "HAEMOGLOBIN\n12.6\nGM%"
        canned = [
            {"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12.6 GM%", "unit_raw": "GM%"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 1
        assert len(evidences) == 1
        assert len(diag["rejected"]) == 0
        # Stored source evidence text MUST be the actual OCR source span containing newlines
        assert evidences[0].text == "HAEMOGLOBIN\n12.6\nGM%"
        assert claims[0].value == "12.6"

    def test_4_multiple_whitespace_characters_pass(self):
        """4. Irregular whitespace, mixed tabs, multiple spaces, and blank lines pass."""
        page_text = "POTASSIUM  \t  4.2  \n\n  mEq/L"
        canned = [
            {"field_type": "lab.value", "value": "4.2", "evidence_text": "POTASSIUM 4.2 mEq/L", "unit_raw": "mEq/L"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 1
        assert len(evidences) == 1
        assert len(diag["rejected"]) == 0
        assert evidences[0].text == "POTASSIUM  \t  4.2  \n\n  mEq/L"

    def test_5_different_actual_words_do_not_pass(self):
        """5. Different actual words are rejected fail-closed (anti-hallucination)."""
        page_text = "HAEMOGLOBIN\n12.6\nGM%"
        canned = [
            # Model hallucinated 14.5 instead of 12.6 in evidence
            {"field_type": "lab.value", "value": "14.5", "evidence_text": "HAEMOGLOBIN 14.5 GM%", "unit_raw": "GM%"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 0
        assert len(evidences) == 0
        assert len(diag["rejected"]) == 1
        assert "not a verbatim substring" in diag["rejected"][0]["reason"]

    def test_6_partial_unrelated_text_does_not_pass(self):
        """6. Unrelated tokens or extra words are rejected fail-closed."""
        page_text = "Patient has no history of diabetes."
        canned = [
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Patient takes Metformin daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 0
        assert len(evidences) == 0
        assert len(diag["rejected"]) == 1
        assert "not a verbatim substring" in diag["rejected"][0]["reason"]

    def test_7_punctuation_substitutions_do_not_pass(self):
        """7. Punctuation substitutions or differences (e.g. comma vs dot, hyphen) do NOT match."""
        page_text = "HAEMOGLOBIN 12.6 GM%"
        canned = [
            # Comma instead of decimal point
            {"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12,6 GM%"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 0
        assert len(evidences) == 0
        assert len(diag["rejected"]) == 1

    def test_8_existing_grounding_normal_cases_unchanged(self):
        """8. Normal standard cases without multiline formatting behave identically."""
        page_text = "C.Autrin / T.Shelcal once daily for 6 weeks"
        canned = [
            {"field_type": "med.name", "value": "C.Autrin", "evidence_text": "C.Autrin / T.Shelcal once daily for 6 weeks"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ]
        evidences, claims, diag = self._extract_canned(canned, page_text)
        assert len(claims) == 2
        assert len(evidences) == 2
        assert len(diag["rejected"]) == 0
        assert evidences[1].text == "once daily"

    def test_9_bounding_box_derivation_correct(self):
        """9. Actual source span bounding-box coordinates enclose all underlying multiline tokens."""
        page_text = "HAEMOGLOBIN\n12.6\nGM%"
        page = _make_page_from_text(page_text)
        canned = [
            {"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12.6 GM%", "unit_raw": "GM%"},
        ]
        extractor = NarrativeExtractor(provider=MockLLMProvider(canned_responses=canned), max_retries=0)
        evidences, claims, diag = extractor.extract("DOC_001", page, page_text, "r0")
        assert len(evidences) == 1
        ev = evidences[0]
        # Character slice matches exact underlying text
        assert page_text[ev.char_start:ev.char_end] == "HAEMOGLOBIN\n12.6\nGM%"
        # Bounding box must enclose y from line 0 (y=20) through line 2 (y=60)
        assert ev.bbox.y0 <= 20.0
        assert ev.bbox.y1 >= 60.0
        assert ev.bbox.x1 > ev.bbox.x0
