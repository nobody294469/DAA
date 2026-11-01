"""Production verification test suite for section-aware extraction (DEC-020 / PoC v3).

Location: tests/test_section_detection_production.py
Authority: DEC-020, DEC-015, DEC-016, DEC-019, PD-005
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch
import pytest

from clindoc.extract.contracts import Claim, Evidence
from clindoc.extract.deterministic import DeterministicExtractor
from clindoc.extract.router import ExtractionRouter
from clindoc.extract.section_detector import (
    ClinicalSectionSpan,
    MedSpaCySectionDetector,
    SectionDetectorError,
    get_section_detector,
    reset_section_detector,
)
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict


def _make_page(text: str, page_num: int = 1) -> DocumentPage:
    lines = text.splitlines()
    tokens = []
    for line_idx, line in enumerate(lines):
        line_id = f"L{line_idx}"
        words = line.split()
        if not words:
            tokens.append(
                Token(
                    text="",
                    bbox=BBoxDict(x0=0.0, y0=float(line_idx * 15), x1=0.0, y1=float(line_idx * 15)),
                    confidence=1.0,
                    line_id=line_id,
                )
            )
        else:
            for w in words:
                tokens.append(
                    Token(
                        text=w,
                        bbox=BBoxDict(x0=0.0, y0=float(line_idx * 15), x1=100.0, y1=float(line_idx * 15 + 12)),
                        confidence=1.0,
                        line_id=line_id,
                    )
                )
    return DocumentPage(
        page_number=page_num,
        width_px=1000,
        height_px=max(1000, len(lines) * 20),
        tokens=tokens,
    )


def _make_doc(text: str, doc_id: str = "TEST_DOC_01") -> DocumentText:
    return DocumentText(
        document_id=doc_id,
        source_type="born_digital",
        pages=[_make_page(text, 1)],
    )


class TestSectionDetectionDisabledParity:
    """Verify that when section detection is disabled (the default), baseline behavior is 100% preserved."""

    def test_default_config_is_disabled(self):
        """ExtractionRouter defaults to section_detection_enabled=False."""
        router = ExtractionRouter()
        assert router.section_detection_enabled is False
        assert router.deterministic.enable_section_detection is False

    def test_disabled_mode_extracts_headers_identically_to_baseline(self):
        """When disabled, deterministic extractor does not suppress numeric claims in headers."""
        text = (
            "Patient Name: John Doe  MRN: 987654  DOB: 1965-04-12  Age: 61\n"
            "LABORATORY RESULTS:\n"
            "Glucose 105 mg/dL 70-99 NORMAL\n"
        )
        doc = _make_doc(text, doc_id="LAB_TEST_01")
        page = doc.pages[0]

        # Disabled extractor (default)
        extractor_disabled = DeterministicExtractor(enable_section_detection=False)
        _, claims_disabled = extractor_disabled.extract_region(doc.document_id, page, text, is_lab=True)

        values_disabled = [c.value for c in claims_disabled if c.field_type == "lab.value"]
        # Baseline extractor ignores demographic header tokens unconditionally
        assert "105" in values_disabled
        assert "987654" not in values_disabled
        assert "61" not in values_disabled

    def test_disabled_mode_restores_exact_claim_emission_order(self):
        """Intra-row claim emission order matches baseline: test_name, value, unit, reference_range, flag."""
        text = "Glucose 105 mg/dL 70-99 NORMAL\n"
        doc = _make_doc(text, doc_id="ORDER_TEST_01")
        page = doc.pages[0]

        extractor = DeterministicExtractor(enable_section_detection=False)
        _, claims = extractor.extract_region(doc.document_id, page, text, is_lab=True)

        field_types = [c.field_type for c in claims]
        assert field_types == [
            "lab.test_name",
            "lab.value",
            "lab.unit",
            "lab.reference_range",
            "lab.flag",
        ]


class TestSectionDetectionEnabledBehavior:
    """Verify section-aware extraction behavior when enabled."""

    @pytest.fixture(autouse=True)
    def setup_detector(self):
        reset_section_detector()
        self.detector = get_section_detector()
        assert self.detector is not None

    def test_header_demographics_suppressed(self):
        """Header demographic lines are excluded from lab extraction when enabled."""
        text = (
            "Patient Name: John Doe  MRN: 987654  DOB: 1965-04-12  Age: 61\n"
            "Collection Date: 2026-10-24 08:30  Doc Ref: 102938\n\n"
            "LABORATORY RESULTS:\n"
            "Glucose 105 mg/dL 70-99 NORMAL\n"
        )
        doc = _make_doc(text, doc_id="LAB_TEST_01")
        page = doc.pages[0]

        extractor_enabled = DeterministicExtractor(
            section_detector=self.detector,
            enable_section_detection=True,
        )
        _, claims_enabled = extractor_enabled.extract_region(doc.document_id, page, text, is_lab=True)

        values_enabled = [c.value for c in claims_enabled if c.field_type == "lab.value"]
        assert "105" in values_enabled
        assert "987654" not in values_enabled
        assert "61" not in values_enabled
        assert "1965" not in values_enabled

    def test_unheaded_document_demographics_bounded(self):
        """Demographic header clamping prevents unheaded clinical body from being swallowed."""
        text = (
            "Patient: Arthur Vance  DOB: 1961-04-20  MRN: PAT-99210\n\n"
            "Cardiac consultation note without explicit section headers.\n"
            "Potassium 4.2 mEq/L NORMAL\n"
            "Prescribed Metoprolol 50 mg oral once daily.\n"
        )
        doc = _make_doc(text, doc_id="EXP_CARDIO_03")
        page = doc.pages[0]

        extractor_enabled = DeterministicExtractor(
            section_detector=self.detector,
            enable_section_detection=True,
        )
        _, claims_lab = extractor_enabled.extract_region(doc.document_id, page, text, is_lab=True)
        _, claims_med = extractor_enabled.extract_region(doc.document_id, page, text, is_lab=False)

        # Lab claims in unheaded body must be extracted
        test_names = [c.value for c in claims_lab if c.field_type == "lab.test_name"]
        values = [c.value for c in claims_lab if c.field_type == "lab.value"]
        assert any("Potassium" in t for t in test_names)
        assert "4.2" in values

        # Medication claims in unheaded body must be extracted
        med_names = [c.value for c in claims_med if c.field_type == "med.name"]
        assert "Metoprolol" in med_names

    def test_narrative_lab_anchored_extraction(self):
        """Narrative section lines extract lab.value only when anchored by recognized lab.test_name."""
        text = (
            "Patient: Alice Brown  MRN: 112233\n\n"
            "HOSPITAL COURSE:\n"
            "Patient was admitted 3 days ago with dyspnea.\n"
            "Discharge Hemoglobin was 13.2 g/dL.\n"
        )
        doc = _make_doc(text, doc_id="EXP_CARDIO_02")
        page = doc.pages[0]

        extractor_enabled = DeterministicExtractor(
            section_detector=self.detector,
            enable_section_detection=True,
        )
        _, claims = extractor_enabled.extract_region(doc.document_id, page, text, is_lab=True)

        values = [c.value for c in claims if c.field_type == "lab.value"]
        test_names = [c.value for c in claims if c.field_type == "lab.test_name"]

        # Line 'Patient was admitted 3 days ago' has no test_name -> '3' must NOT be extracted as lab.value
        assert "3" not in values
        # Line 'Discharge Hemoglobin was 13.2 g/dL' has test_name Hemoglobin -> '13.2' MUST be extracted
        assert any("Hemoglobin" in t for t in test_names)
        assert "13.2" in values

    def test_medication_sections_suppress_lab_values(self):
        """Lines under medications headings suppress lab.value and lab.reference_range."""
        text = (
            "Patient: Bob White  MRN: 445566\n\n"
            "MEDICATIONS:\n"
            "Lisinopril 10-20 mg oral daily. Dispense: 30 tablets. Refills: 3.\n"
        )
        doc = _make_doc(text, doc_id="EXP_MED_01")
        page = doc.pages[0]

        extractor_enabled = DeterministicExtractor(
            section_detector=self.detector,
            enable_section_detection=True,
        )
        _, claims_lab = extractor_enabled.extract_region(doc.document_id, page, text, is_lab=True)

        # No lab values or reference ranges should be extracted from the medication section
        lab_values = [c.value for c in claims_lab if c.field_type == "lab.value"]
        lab_ranges = [c.value for c in claims_lab if c.field_type == "lab.reference_range"]
        assert not lab_values
        assert not lab_ranges

    def test_medication_extraction_unconstrained(self):
        """Medication extraction runs identically regardless of section."""
        text = (
            "Patient: Jane Doe  MRN: 778899\n\n"
            "ASSESSMENT AND PLAN:\n"
            "Initiate Atorvastatin 40 mg oral once daily.\n"
        )
        doc = _make_doc(text, doc_id="EXP_NARRATIVE_01")
        page = doc.pages[0]

        extractor_enabled = DeterministicExtractor(
            section_detector=self.detector,
            enable_section_detection=True,
        )
        _, claims_med = extractor_enabled.extract_region(doc.document_id, page, text, is_lab=False)

        med_names = [c.value for c in claims_med if c.field_type == "med.name"]
        doses = [c.value for c in claims_med if c.field_type == "med.dose"]
        assert "Atorvastatin" in med_names
        assert "40" in doses


class TestProvenanceAndEvidenceInvariants:
    """Verify that section-aware extraction strictly preserves provenance and character offsets."""

    def test_evidence_offsets_and_verbatim_text_intact(self):
        """Every claim emitted with section detection enabled preserves exact verbatim evidence text and offsets."""
        text = (
            "Patient: Arthur Vance  MRN: 12345\n\n"
            "LABORATORY RESULTS:\n"
            "Glucose 105 mg/dL 70-99 NORMAL\n"
            "Potassium 4.2 mEq/L 3.5-5.0 NORMAL\n"
        )
        doc = _make_doc(text, doc_id="EXP_LAB_01")
        page = doc.pages[0]
        detector = get_section_detector()

        extractor = DeterministicExtractor(
            section_detector=detector,
            enable_section_detection=True,
        )
        evidences, claims = extractor.extract_region(doc.document_id, page, text, is_lab=True)

        ev_map = {e.evidence_id: e for e in evidences}

        for c in claims:
            assert c.evidence_id in ev_map
            ev = ev_map[c.evidence_id]
            assert ev.char_start < ev.char_end
            # Verify exact verbatim slice match
            expected_slice = text[ev.char_start:ev.char_end]
            assert ev.text == expected_slice
            assert c.group_id is not None


class TestFailureModesAndGracefulFallback:
    """Verify that section detection failures log structured warnings and fail open to baseline."""

    def test_detector_initialization_failure_fallback(self, caplog):
        """When section detector raises an error during router initialization, router falls back to baseline."""
        with caplog.at_level(logging.WARNING):
            with patch("clindoc.extract.section_detector.get_section_detector", side_effect=Exception("Simulated Spacy Load Crash")):
                router = ExtractionRouter(enable_section_detection=True)
                assert router.section_detector is None

        assert any("SECTION_DETECTOR_INIT_FAILED" in rec.message for rec in caplog.records)

    def test_detector_runtime_failure_fallback(self, caplog):
        """When section detector raises an error during page processing, extraction falls back to baseline."""
        mock_detector = MagicMock()
        mock_detector.detect_sections.side_effect = Exception("Simulated PyRuSH Segmentation Crash")

        text = "Patient: John Doe\nGlucose 105 mg/dL NORMAL\n"
        doc = _make_doc(text, doc_id="FALLBACK_01")

        with caplog.at_level(logging.WARNING):
            router = ExtractionRouter(
                enable_section_detection=True,
                section_detector=mock_detector,
            )
            output = router.extract_document(doc, is_lab_default=True)

        assert any("SECTION_DETECTION_RUNTIME_FAILED" in rec.message for rec in caplog.records)
        # Extraction still succeeded via baseline fallback
        claims = [c for r in output.regions for c in r.claims]
        assert any(c.field_type == "lab.value" and c.value == "105" for c in claims)
