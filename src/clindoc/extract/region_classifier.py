"""Region/Layout Classification for the Extraction Router.

Authority: DEC-001 (LOCKED)
Decision: Deterministic rule-based heuristic classifier.
  - Medication-regimen line grammar regex → deterministic (structured_med)
  - Lab result table column alignment heuristics → deterministic (structured_lab)
  - Default for unmatched text blocks → llm_narrative

No custom-trained layout model is used.
PP-Structure MAY be used as supplementary boundary signal (not implemented here).

Produces RoutingDecision objects per region consumed by ExtractionRouter.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Sequence

from clindoc.extract.contracts import RoutingDecision
from clindoc.ocr.document_text import DocumentPage, DocumentText


# ─── CLASSIFICATION CONSTANTS ─────────────────────────────────────────────────

# Medication regimen line grammar (DEC-001): DRUG DOSE ROUTE FREQUENCY [DURATION]
# Matches lines like: "Atorvastatin 10mg PO once daily"
#                     "Lisinopril 5 mg tablet by mouth BID"
_MED_LINE_PATTERN = re.compile(
    r"""
    (?:
        [A-Z][a-z]+(?:\s+[A-Za-z]+)*      # drug name (capitalized); [A-Z]?[a-z]+ was ambiguous: 2^words backtracking
        \s+
        \d+(?:\.\d+)?                       # numeric dose
        \s*
        (?:mg|mcg|g|mL|units?|puffs?)       # dose unit
        \s+
        (?:                                  # route keyword
            (?:PO|oral|by\s+mouth|inhalation|topical|subcutaneous|IV)\b
        )
    )
    |
    (?:
        # Sig-line pattern: Take 1 tablet PO BID
        (?:Take|Inhale|Apply|Inject)\s+\d+
        \s+(?:tablet|capsule|puff|mg|unit)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Lab table column alignment heuristics (DEC-001)
# A lab table row contains: analyte name, numeric value, unit, [reference range], [flag]
_LAB_VALUE_PATTERN = re.compile(
    r"""
    (?:
        # Analyte name followed by numeric value + unit
        [A-Za-z][A-Za-z\s\(\)\/]+          # analyte name
        \s+
        -?\d+(?:\.\d+)?                     # numeric value
        \s*
        (?:g/dL|mg/dL|mmol/L|U/L|10\*\d/\w+|%|\w+/\w+)  # unit
    )
    |
    (?:
        # Reference range pattern: "12.0 - 16.0" or "70 - 99"
        \d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?
    )
    |
    (?:
        # Flag keywords
        \b(?:NORMAL|HIGH|LOW|CRITICAL|H|L)\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Keywords that strongly indicate lab table context
_LAB_HEADER_KEYWORDS = {
    "test name", "analyte", "result", "value", "reference range", "ref range",
    "unit", "flag", "interpretation", "observed value", "normal range",
    "test description", "component", "in range", "out of range",
}

# Keywords indicating narrative prose
_NARRATIVE_INDICATORS = [
    r"\bpatient\s+(?:is|was|has|had|presents?|denies?|reports?)\b",
    r"\bhistory\s+of\b",
    r"\bdiagnosis\b",
    r"\bdischarge\s+(?:instructions?|plan|summary)\b",
    r"\bcourse\s+of\s+(?:the\s+)?(?:hospital|treatment)\b",
    r"\bfollow.?up\b",
    r"\badmitted\s+(?:for|with|on)\b",
]
_NARRATIVE_PATTERN = re.compile("|".join(_NARRATIVE_INDICATORS), re.IGNORECASE)

# Keywords indicating prescription document structure (DEC-001, FIX 7)
_PRESCRIPTION_INDICATORS = [
    r"\bRx\b",
    r"\bSig\s*:",
    r"\bDispense(?:\s+Quantity)?\s*:",
    r"\bRefills?\s*:",
    r"\bPrescriber\s*:",
    r"\bSubstitution\s+Permitted\b",
    r"\bDispense\s+as\s+Written\b",
    r"\bDEA\s*:\s*[A-Z0-9]+",
    r"\bNPI\s*:\s*\d+",
]
_PRESCRIPTION_PATTERN = re.compile("|".join(_PRESCRIPTION_INDICATORS), re.IGNORECASE)

# Minimum fraction thresholds (configurable via extraction.yaml)
_DEFAULT_MED_FRACTION = 0.4
_DEFAULT_LAB_FRACTION = 0.3


# ─── REGION REPRESENTATION ────────────────────────────────────────────────────

@dataclass
class PageRegion:
    """A contiguous block of lines from a document page."""
    document_id: str
    page_number: int
    region_index: int
    lines: list[str]  # text of each logical line in the region

    @property
    def region_id(self) -> str:
        return f"{self.document_id}::p{self.page_number}::r{self.region_index}"

    @property
    def full_text(self) -> str:
        return "\n".join(self.lines)

    @property
    def num_lines(self) -> int:
        return len(self.lines)


# ─── CLASSIFIER ───────────────────────────────────────────────────────────────

class RegionClassifier:
    """Deterministic rule-based region classifier (DEC-001).

    Applies regex pattern matching and column alignment heuristics to assign
    each document region to one of three extraction routes:
      - 'deterministic' (structured_lab or structured_med)
      - 'llm_narrative' (prose/unstructured)

    GLiNER is NOT assigned here; it is a fallback triggered by the router
    when deterministic confidence is below threshold (DEC-004).
    """

    def __init__(
        self,
        med_line_fraction_threshold: float = _DEFAULT_MED_FRACTION,
        lab_line_fraction_threshold: float = _DEFAULT_LAB_FRACTION,
    ):
        self.med_fraction = med_line_fraction_threshold
        self.lab_fraction = lab_line_fraction_threshold

    def classify_page(self, document_id: str, page: DocumentPage) -> list[RoutingDecision]:
        """Classify all regions on a single page into routing decisions.

        A page is segmented into regions by splitting at blank lines.
        Each region is independently classified.
        """
        # Segment page text into regions (split on blank lines)
        page_text = page.get_page_text()
        raw_regions = self._segment_into_regions(page_text)

        decisions: list[RoutingDecision] = []
        for region_idx, region_lines in enumerate(raw_regions):
            if not region_lines:
                continue
            region = PageRegion(
                document_id=document_id,
                page_number=page.page_number,
                region_index=region_idx,
                lines=region_lines,
            )
            decision = self._classify_region(region)
            decisions.append(decision)
        return decisions

    def classify_document(self, doc: DocumentText) -> list[RoutingDecision]:
        """Classify all regions across all pages of a document."""
        all_decisions: list[RoutingDecision] = []
        for page in doc.pages:
            page_decisions = self.classify_page(doc.document_id, page)
            all_decisions.extend(page_decisions)
        return all_decisions

    # ─── Private helpers ────────────────────────────────────────────────────

    def _segment_into_regions(self, page_text: str) -> list[list[str]]:
        """Split page text into contiguous non-empty line groups (regions)."""
        regions: list[list[str]] = []
        current: list[str] = []
        for line in page_text.splitlines():
            stripped = line.strip()
            if stripped:
                current.append(stripped)
            else:
                if current:
                    regions.append(current)
                    current = []
        if current:
            regions.append(current)
        # If page had no blank-line separators, treat whole page as one region
        if not regions and page_text.strip():
            regions = [[l.strip() for l in page_text.splitlines() if l.strip()]]
        return regions

    def _classify_region(self, region: PageRegion) -> RoutingDecision:
        """Apply DEC-001 heuristics to classify a single region."""
        lines = region.lines
        n = len(lines)

        # --- Lab table detection ---
        lab_score, lab_reason = self._score_lab(lines)
        if lab_score >= self.lab_fraction:
            return RoutingDecision(
                document_id=region.document_id,
                region_id=region.region_id,
                route="deterministic",
                reason=f"lab_table_heuristic: {lab_reason} (score={lab_score:.2f})",
                confidence=min(0.95, 0.60 + lab_score * 0.4),
            )

        # --- Medication line detection ---
        med_score, med_reason = self._score_med(lines)
        if med_score >= self.med_fraction:
            return RoutingDecision(
                document_id=region.document_id,
                region_id=region.region_id,
                route="deterministic",
                reason=f"med_regimen_heuristic: {med_reason} (score={med_score:.2f})",
                confidence=min(0.95, 0.60 + med_score * 0.4),
            )

        # --- Narrative fallback ---
        narrative_confidence = self._score_narrative(lines)
        return RoutingDecision(
            document_id=region.document_id,
            region_id=region.region_id,
            route="llm_narrative",
            reason="narrative_default: no structured lab or med pattern detected",
            confidence=narrative_confidence,
        )

    def _score_lab(self, lines: list[str]) -> tuple[float, str]:
        """Score region for lab table characteristics."""
        n = len(lines)
        if n == 0:
            return 0.0, "empty"

        # Check for lab header keywords in first 3 lines
        header_text = " ".join(lines[:3]).lower()
        has_header = any(kw in header_text for kw in _LAB_HEADER_KEYWORDS)

        # Count lines matching lab value pattern
        matching = sum(1 for l in lines if _LAB_VALUE_PATTERN.search(l))
        fraction = matching / n

        if has_header:
            fraction = min(1.0, fraction + 0.3)  # Boost for header presence

        reason = f"{matching}/{n} lines matched lab pattern"
        if has_header:
            reason += " + header detected"
        return fraction, reason

    def _score_med(self, lines: list[str]) -> tuple[float, str]:
        """Score region for medication regimen line characteristics."""
        n = len(lines)
        if n == 0:
            return 0.0, "empty"

        matching = sum(1 for l in lines if _MED_LINE_PATTERN.search(l))
        # Also count lines with dose+unit combos even without full grammar match
        dose_unit = sum(
            1 for l in lines
            if re.search(r"\d+\s*(?:mg|mcg|mL|g|units?)\b", l, re.IGNORECASE)
        )
        effective = max(matching, min(dose_unit, n // 2 + 1))
        fraction = effective / n

        # Check for prescription structural indicators (FIX 7: DEC-001)
        # In structured prescriptions, header/footer lines dilute the med-grammar fraction.
        # Strong structural cues (Rx, Sig:, Dispense Quantity:, Refills:, Prescriber:)
        # boost confidence that the block is a structured prescription format.
        text = " ".join(lines)
        rx_matches = _PRESCRIPTION_PATTERN.findall(text)
        has_prescription_structure = len(rx_matches) >= 2 or any(
            re.search(pat, text, re.IGNORECASE)
            for pat in (r"\bRx\b", r"\bSig\s*:", r"\bDispense(?:\s+Quantity)?\s*:")
        )

        has_med_content = matching > 0 or dose_unit > 0
        if has_prescription_structure and has_med_content:
            fraction = min(1.0, fraction + 0.35)

        reason = f"{matching}/{n} med-grammar, {dose_unit}/{n} dose-unit"
        if has_prescription_structure and has_med_content:
            reason += f" + prescription structural cues detected (cues={len(rx_matches)})"

        return fraction, reason

    def _score_narrative(self, lines: list[str]) -> float:
        """Estimate confidence that region is narrative prose."""
        text = " ".join(lines)
        narrative_hits = sum(
            1 for pat in _NARRATIVE_INDICATORS
            if re.search(pat, text, re.IGNORECASE)
        )
        # Base confidence: 0.75 for unmatched (narrative is the safe default)
        confidence = min(0.95, 0.75 + narrative_hits * 0.05)
        return confidence
