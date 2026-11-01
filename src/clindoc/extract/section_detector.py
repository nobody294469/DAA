"""Clinical document section detection engine.

Location: src/clindoc/extract/section_detector.py
Authority: DEC-020 / DEC Section-Detection PoC v3
License: Apache-2.0 / MIT

Provides rule-based section detection using MedSpaCy Sectionizer with:
1. Complete clinical laboratory headings (e.g. 'LABORATORY DATA:', 'DIAGNOSTIC WORKUP:',
   'SEROLOGIC MONITORING:', 'RAPID STAT LABS:', 'ARTERIAL BLOOD GAS PANEL:', etc.).
2. Evidence-backed prescription headings ('Order #', 'Dispense', 'Sig', 'Refills', etc.).
3. Bounded demographic header spans to prevent header spans from swallowing unheaded
   or missing-heading clinical documents.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class SectionDetectorError(Exception):
    """Raised when section detector initialization or processing encounters an unrecoverable error."""
    pass


@dataclass(frozen=True)
class ClinicalSectionSpan:
    """Represents a detected clinical section with exact verbatim character offsets."""
    category: str
    title: str
    char_start: int
    char_end: int
    body_char_start: int
    body_char_end: int

    def contains_offset(self, char_offset: int) -> bool:
        """Check if character offset falls within this section span."""
        return self.char_start <= char_offset < self.char_end


class MedSpaCySectionDetector:
    """MedSpaCy-powered clinical section detection with bounded demographic spans and expanded lab headings."""

    def __init__(self) -> None:
        try:
            import medspacy
            from medspacy.section_detection import SectionRule
        except ImportError as e:
            logger.error("SECTION_DETECTOR_DEPENDENCY_MISSING: medspacy or spacy not installed: %s", e)
            raise SectionDetectorError(f"MedSpaCy dependency missing: {e}") from e

        try:
            self.nlp = medspacy.load()
            self.sectionizer = self.nlp.add_pipe("medspacy_sectionizer")
            self._load_custom_rules(SectionRule)
            logger.info("SECTION_DETECTOR_INITIALIZED: MedSpaCy sectionizer successfully loaded with custom clinical rules.")
        except Exception as e:
            logger.error("SECTION_DETECTOR_INIT_FAILED: Failed to initialize MedSpaCy pipeline: %s", e)
            raise SectionDetectorError(f"Failed to initialize MedSpaCy: {e}") from e

    def _load_custom_rules(self, SectionRule: type) -> None:
        """Register domain-specific clinical, laboratory, and prescription section rules."""
        rules = [
            # --- Header & Demographic Boundaries ---
            SectionRule(literal="Patient Name:", category="header_demographics"),
            SectionRule(literal="Patient:", category="header_demographics"),
            SectionRule(literal="PATIENT:", category="header_demographics"),
            SectionRule(literal="Patient ID:", category="header_demographics"),
            SectionRule(literal="Requisition ID:", category="header_demographics"),
            SectionRule(literal="Document ID:", category="header_demographics"),
            SectionRule(literal="Doc ID:", category="header_demographics"),
            SectionRule(literal="Doc Ref:", category="header_demographics"),
            SectionRule(literal="MRN / ID:", category="header_demographics"),
            SectionRule(literal="MRN:", category="header_demographics"),
            SectionRule(literal="Collection Date:", category="header_demographics"),
            SectionRule(literal="Date of Birth:", category="header_demographics"),
            SectionRule(literal="DOB:", category="header_demographics"),
            SectionRule(literal="DOB / Gender:", category="header_demographics"),
            SectionRule(literal="Ordering Clinician:", category="header_demographics"),
            SectionRule(literal="Ordering MD:", category="header_demographics"),
            SectionRule(literal="Physician:", category="header_demographics"),
            SectionRule(literal="Prescribing Clinician:", category="header_demographics"),
            SectionRule(literal="Prescriber:", category="header_demographics"),
            SectionRule(literal="Attending Clinician:", category="header_demographics"),
            SectionRule(literal="Attending:", category="header_demographics"),
            SectionRule(literal="Ordering Provider:", category="header_demographics"),
            SectionRule(literal="ENCOUNTER:", category="header_demographics"),
            SectionRule(literal="Encounter:", category="header_demographics"),
            SectionRule(literal="Report Date:", category="header_demographics"),
            SectionRule(literal="Order Date:", category="header_demographics"),
            SectionRule(literal="Discharge Date:", category="header_demographics"),
            SectionRule(literal="DATE:", category="header_demographics"),
            SectionRule(literal="Date:", category="header_demographics"),
            SectionRule(literal="CLINIC:", category="header_demographics"),
            SectionRule(literal="Facility:", category="header_demographics"),

            # --- Complete Laboratory / Observation Sections ---
            SectionRule(literal="LABORATORY RESULTS:", category="labs_and_studies"),
            SectionRule(literal="LABORATORY VALUES:", category="labs_and_studies"),
            SectionRule(literal="LABORATORY DATA:", category="labs_and_studies"),
            SectionRule(literal="LABORATORY:", category="labs_and_studies"),
            SectionRule(literal="LAB RESULTS:", category="labs_and_studies"),
            SectionRule(literal="DISCHARGE LABS:", category="labs_and_studies"),
            SectionRule(literal="ADMISSION LABS:", category="labs_and_studies"),
            SectionRule(literal="DIAGNOSTIC TESTING:", category="labs_and_studies"),
            SectionRule(literal="DIAGNOSTICS:", category="labs_and_studies"),
            SectionRule(literal="DIAGNOSTIC WORKUP:", category="labs_and_studies"),
            SectionRule(literal="SEROLOGIC MONITORING:", category="labs_and_studies"),
            SectionRule(literal="INVESTIGATIONS:", category="labs_and_studies"),
            SectionRule(literal="TEST RESULTS:", category="labs_and_studies"),
            SectionRule(literal="STUDIES:", category="labs_and_studies"),
            SectionRule(literal="Panel:", category="labs_and_studies"),
            SectionRule(literal="SECTION: COMPLETE BLOOD COUNT", category="labs_and_studies"),
            SectionRule(literal="COMPREHENSIVE METABOLIC PANEL", category="labs_and_studies"),
            SectionRule(literal="COMPLETE BLOOD COUNT", category="labs_and_studies"),
            SectionRule(literal="LIPID PANEL", category="labs_and_studies"),
            SectionRule(literal="RAPID STAT LABS:", category="labs_and_studies"),
            SectionRule(literal="ARTERIAL BLOOD GAS PANEL:", category="labs_and_studies"),
            SectionRule(literal="URINALYSIS:", category="labs_and_studies"),
            SectionRule(literal="MICROBIOLOGY:", category="labs_and_studies"),
            SectionRule(literal="MICROBIOLOGY & LABS:", category="labs_and_studies"),
            SectionRule(literal="TOXICOLOGY:", category="labs_and_studies"),
            SectionRule(literal="TUMOR MARKERS:", category="labs_and_studies"),
            SectionRule(literal="HEMATOLOGY:", category="labs_and_studies"),
            SectionRule(literal="CHEMISTRY:", category="labs_and_studies"),
            SectionRule(literal="ENDOCRINE LABS:", category="labs_and_studies"),
            SectionRule(literal="CARDIAC LABS:", category="labs_and_studies"),
            SectionRule(literal="RENAL LABS:", category="labs_and_studies"),
            SectionRule(literal="HEPATIC LABS:", category="labs_and_studies"),
            SectionRule(literal="COAGULATION:", category="labs_and_studies"),
            SectionRule(literal="Test Description", category="labs_and_studies"),
            SectionRule(literal="Analyte", category="labs_and_studies"),
            SectionRule(literal="Test Name", category="labs_and_studies"),

            # --- Evidence-Backed Prescription Sections ---
            SectionRule(literal="Rx", category="medications"),
            SectionRule(literal="Rx:", category="medications"),
            SectionRule(literal="Prescription:", category="medications"),
            SectionRule(literal="Prescribed Discharge Medications", category="medications"),
            SectionRule(literal="Order #1:", category="medications"),
            SectionRule(literal="Order #2:", category="medications"),
            SectionRule(literal="Order #", category="medications"),
            SectionRule(literal="Sig:", category="medications"),
            SectionRule(literal="Instructions (Sig)", category="medications"),
            SectionRule(literal="Instructions:", category="medications"),
            SectionRule(literal="DIRECTIONS:", category="medications"),
            SectionRule(literal="Dispense Quantity:", category="medications"),
            SectionRule(literal="Dispense:", category="medications"),
            SectionRule(literal="Qty / Refills", category="medications"),
            SectionRule(literal="QTY:", category="medications"),
            SectionRule(literal="Refills:", category="medications"),
            SectionRule(literal="REFILLS LEFT:", category="medications"),
            SectionRule(literal="Refills", category="medications"),

            # --- Narrative Inpatient/Outpatient Medication Sections ---
            SectionRule(literal="MEDICATIONS:", category="medications"),
            SectionRule(literal="DISCHARGE MEDICATIONS:", category="medications"),
            SectionRule(literal="CURRENT MEDICATIONS:", category="medications"),
            SectionRule(literal="CURRENT THERAPY:", category="medications"),
            SectionRule(literal="THERAPY:", category="medications"),
            SectionRule(literal="TREATMENT:", category="medications"),
            SectionRule(literal="MEDICATION ORDERS:", category="medications"),

            # --- Narrative Clinical Sections ---
            SectionRule(literal="CHIEF COMPLAINT:", category="chief_complaint"),
            SectionRule(literal="HISTORY OF PRESENT ILLNESS:", category="history_of_present_illness"),
            SectionRule(literal="ASSESSMENT AND PLAN:", category="observation_and_plan"),
            SectionRule(literal="PLAN:", category="observation_and_plan"),
            SectionRule(literal="HOSPITAL COURSE:", category="hospital_course"),
            SectionRule(literal="PHYSICAL EXAMINATION:", category="physical_examination"),
            SectionRule(literal="REVIEW OF SYSTEMS:", category="review_of_systems"),
        ]
        self.sectionizer.add(rules)

    def detect_sections(self, text: str) -> list[ClinicalSectionSpan]:
        """Detect all sections in a document, returning continuous spans covering the text.

        Guarantees that demographic headers do not consume the entire note in
        unheaded or missing-heading clinical documents.
        """
        try:
            doc = self.nlp(text)
        except Exception as e:
            logger.warning("SECTION_DETECTION_PARSE_FAILED: %s", e)
            raise SectionDetectorError(f"Failed to parse text: {e}") from e

        sections: list[ClinicalSectionSpan] = []

        # Find demographic header boundary (e.g. first paragraph break or header block end)
        first_blank = text.find("\n\n")
        demographic_cutoff = first_blank if first_blank != -1 else 400

        for s in doc._.sections:
            title_text = doc[s.title_span[0]:s.title_span[1]].text if s.title_span else ""
            span_toks = doc[s.section_span[0]:s.section_span[1]]
            char_start = span_toks.start_char
            char_end = span_toks.end_char

            if s.body_span:
                body_toks = doc[s.body_span[0]:s.body_span[1]]
                b_start = body_toks.start_char
                b_end = body_toks.end_char
            else:
                b_start = char_end
                b_end = char_end

            cat = s.category if s.category is not None else "header_preamble"

            # If demographic section extends past the header block without a closing heading,
            # clamp its end so subsequent narrative text is treated as unheaded clinical body.
            if cat in {"header_demographics", "header_preamble"}:
                if char_end > demographic_cutoff and char_start < demographic_cutoff:
                    char_end = demographic_cutoff
                    b_end = min(b_end, demographic_cutoff)

            sections.append(ClinicalSectionSpan(
                category=cat,
                title=title_text,
                char_start=char_start,
                char_end=char_end,
                body_char_start=b_start,
                body_char_end=b_end,
            ))

        return sections

    def get_section_for_offset(
        self, char_offset: int, sections: list[ClinicalSectionSpan]
    ) -> Optional[ClinicalSectionSpan]:
        """Find the enclosing section for a given character offset."""
        for s in sections:
            if s.contains_offset(char_offset):
                return s
        return None

    def is_header_or_demographic(
        self, char_offset: int, sections: list[ClinicalSectionSpan]
    ) -> bool:
        """Check if a character offset falls in a header, demographic, or preamble section."""
        sec = self.get_section_for_offset(char_offset, sections)
        if sec is None:
            return False
        return sec.category in {"header_demographics", "header_preamble"}


# Singleton instance cache for lazy initialization
_DETECTOR_SINGLETON: Optional[MedSpaCySectionDetector] = None


def get_section_detector() -> Optional[MedSpaCySectionDetector]:
    """Get or initialize the reusable MedSpaCySectionDetector singleton.

    Returns:
        MedSpaCySectionDetector instance, or None if initialization fails (fail-open policy).
    """
    global _DETECTOR_SINGLETON
    if _DETECTOR_SINGLETON is None:
        try:
            _DETECTOR_SINGLETON = MedSpaCySectionDetector()
        except Exception as e:
            logger.warning(
                "SECTION_DETECTOR_LAZY_INIT_FAILED: %s. Section detection unavailable; falling back to baseline.",
                e,
            )
            return None
    return _DETECTOR_SINGLETON


def reset_section_detector() -> None:
    """Reset singleton instance (useful for test isolation)."""
    global _DETECTOR_SINGLETON
    _DETECTOR_SINGLETON = None
