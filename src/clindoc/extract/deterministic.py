"""Deterministic extraction engine for structured clinical documents.

Authority: DEC-001, DEC-004, DEC-017, DEC-019
Scope:
  - ALWAYS handles Tier 2 fields (DEC-019):
      lab.unit, lab.reference_range, lab.test_name, med.duration
  - Handles Tier 1 fields from structured/tabular regions with high pattern confidence:
      lab.value, lab.flag, med.name, med.dose, med.route, med.frequency
  - Returns extraction_confidence per claim so the router can apply DEC-004 threshold.

This module does NOT call GLiNER or any learned model.
GLiNER fallback is triggered by the ExtractionRouter when confidence < DEC-004 threshold.

Design principles:
  - Keep extraction logic modular and testable.
  - Do not create an uncontrolled regex collection.
  - Each extraction function is independently callable for unit testing.
  - Evidence spans must preserve verbatim text + character offsets from DocumentPage.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from clindoc.extract.contracts import (
    BBoxModel, Claim, Evidence, ExtractionMethod,
    TIER_1_FIELDS, TIER_2_FIELDS,
)
from clindoc.ocr.document_text import DocumentPage


# ─── RESOURCE PATHS ─────────────────────────────────────────────────────────

_PROJ_ROOT = Path(__file__).resolve().parents[3]


def _load_json_config(relative_path: str) -> dict[str, Any]:
    """Load a JSON config from configs/ with project-root resolution."""
    p = _PROJ_ROOT / relative_path
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# Loaded lazily at first use
_LOINC_MAP: dict[str, Any] | None = None
_UCUM_MAP: dict[str, Any] | None = None
_MED_CATALOG: dict[str, Any] | None = None


def _loinc_map() -> dict[str, Any]:
    global _LOINC_MAP
    if _LOINC_MAP is None:
        _LOINC_MAP = _load_json_config("configs/loinc_subset.json")
    return _LOINC_MAP


def _ucum_map() -> dict[str, Any]:
    global _UCUM_MAP
    if _UCUM_MAP is None:
        _UCUM_MAP = _load_json_config("configs/ucum_subset.json")
    return _UCUM_MAP


def _med_catalog() -> dict[str, Any]:
    global _MED_CATALOG
    if _MED_CATALOG is None:
        _MED_CATALOG = _load_json_config("configs/medication_subset.json")
    return _MED_CATALOG


# ─── KNOWN VOCABULARIES ──────────────────────────────────────────────────────

_KNOWN_UNITS = {
    "g/dl", "mg/dl", "mmol/l", "10*3/ul", "10*6/ul", "u/l", "%",
    "g/dL", "mg/dL", "mmol/L", "10*3/uL", "10*6/uL", "U/L",
    "iu/l", "IU/L", "ng/ml", "ng/mL", "ug/ml", "ug/mL", "mEq/L", "meq/l",
    "pg/mL", "pg/ml", "cells/uL", "cells/ul",
}

_FLAG_MAP = {
    "H": "HIGH", "HIGH": "HIGH", "HI": "HIGH", "ELEVATED": "HIGH",
    "L": "LOW", "LOW": "LOW", "LO": "LOW",
    "N": "NORMAL", "NORMAL": "NORMAL", "WNL": "NORMAL",
    "CRITICAL": "CRITICAL", "PANIC": "CRITICAL",
    "ABNORMAL": "HIGH",
}

# Medication routes (canonical forms per PHASE0_ARCHITECTURE_RESOLUTION)
_ROUTE_VOCAB = {
    "po": "oral", "oral": "oral", "by mouth": "oral",
    "iv": "intravenous", "intravenous": "intravenous",
    "im": "intramuscular", "intramuscular": "intramuscular",
    "sq": "subcutaneous", "sc": "subcutaneous", "subcutaneous": "subcutaneous",
    "inhalation": "inhalation", "inhaled": "inhalation", "by inhalation": "inhalation",
    "topical": "topical",
    "sublingual": "sublingual", "sl": "sublingual",
    "rectal": "rectal", "pr": "rectal",
    "transdermal": "transdermal", "patch": "transdermal",
    "nasal": "nasal",
    "ophthalmic": "ophthalmic",
}

# Frequency abbreviations
_FREQ_ABBREV = {
    "qd": "once daily", "qday": "once daily",
    "bid": "twice daily", "b.i.d.": "twice daily",
    "tid": "three times daily", "t.i.d.": "three times daily",
    "qid": "four times daily", "q.i.d.": "four times daily",
    "qhs": "every bedtime", "qpm": "every evening",
    "prn": "as needed", "p.r.n.": "as needed",
}

# Reference range pattern: "70 - 99" or "12.0 – 16.0"
_REF_RANGE_PATTERN = re.compile(
    r"""((?:\d+(?:\.\d+)?)\s*[-–]\s*(?:\d+(?:\.\d+)?))""",
    re.VERBOSE,
)

# Numeric value pattern
_NUMERIC_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?$")

# Duration pattern
_DURATION_PATTERN = re.compile(
    r"""(?:for\s+|x\s+|duration:\s*)?(\d+\s*(?:days?|weeks?|months?))""",
    re.IGNORECASE,
)
_DURATION_AS_NEEDED = re.compile(r"duration:\s*as needed", re.IGNORECASE)


# ─── FIELD EXTRACTORS ────────────────────────────────────────────────────────

def extract_lab_value(text: str) -> tuple[Optional[str], Optional[float], float]:
    """Extract lab.value from text string.

    Returns: (raw_value_str, numeric_value, confidence)
    """
    # Match isolated numeric tokens
    m = re.search(r"(?<!\w)(-?\d+(?:\.\d+)?)(?!\w)", text)
    if m:
        raw = m.group(1)
        try:
            numeric = float(raw)
            return raw, numeric, 0.90
        except ValueError:
            pass
    return None, None, 0.0


def extract_lab_flag(text: str) -> tuple[Optional[str], float]:
    """Extract lab.flag from text string.

    Multi-char flags (NORMAL, HIGH, LOW, CRITICAL, ELEVATED, etc.) are matched
    anywhere in the text.  Single-char flags (H, L, N) are ONLY matched when
    they appear as a standalone whole-word token that is NOT immediately
    preceded by a '/' (to avoid matching the 'L' in 'mEq/L' or 'g/dL').

    Returns: (canonical_flag, confidence)
    """
    # Multi-character flags first (highest priority)
    multi_char_flags = [
        "NORMAL", "HIGH", "LOW", "CRITICAL", "ELEVATED", "ABNORMAL", "WNL",
        "PANIC", "HI", "LO",
    ]
    text_upper = text.upper()
    for flag_kw in multi_char_flags:
        if re.search(r"\b" + re.escape(flag_kw) + r"\b", text_upper):
            return _FLAG_MAP[flag_kw], 0.95

    # Single-char flags (H, L, N): only match as standalone tokens NOT preceded by '/'
    # Pattern: whitespace (or start) followed by H/L/N followed by whitespace or end
    # Negative lookbehind: not preceded by letter (unit boundary)
    for single_char in ["H", "L", "N"]:
        # Must be a whole word NOT immediately after '/' (unit suffix)
        pattern = r"(?<![A-Za-z/])" + single_char + r"(?![A-Za-z/])"
        if re.search(pattern, text_upper):
            return _FLAG_MAP[single_char], 0.90

    return None, 0.0


def extract_lab_unit(text: str) -> tuple[Optional[str], float]:
    """Extract lab.unit from text string. Tier 2 deterministic.

    Returns: (unit_str, confidence)
    """
    ucum = _ucum_map()
    text_lower = text.lower()

    # Check against known unit set (ordered: longer units first to avoid partial match)
    for unit in sorted(_KNOWN_UNITS | set(ucum.keys()), key=len, reverse=True):
        pattern = r"(?<![A-Za-z])" + re.escape(unit) + r"(?![A-Za-z])"
        if re.search(pattern, text, re.IGNORECASE):
            return unit, 0.95

    return None, 0.0


def extract_lab_reference_range(text: str) -> tuple[Optional[str], float]:
    """Extract lab.reference_range from text string. Tier 2 deterministic.

    Returns: (range_text, confidence)
    """
    m = _REF_RANGE_PATTERN.search(text)
    if m:
        return m.group(1).strip(), 0.95
    return None, 0.0


def extract_lab_test_name(text: str, loinc_map: dict[str, Any] | None = None) -> tuple[Optional[str], float]:
    """Extract lab.test_name from text. Tier 2 deterministic.

    Checks against LOINC catalog for exact match.
    Returns: (test_name, confidence)
    """
    if loinc_map is None:
        loinc_map = _loinc_map()

    text_stripped = text.strip()
    # Exact match against catalog
    if text_stripped in loinc_map:
        return loinc_map[text_stripped].get("canonical_name", text_stripped), 0.95

    # Case-insensitive match
    text_lower = text_stripped.lower()
    for key, meta in loinc_map.items():
        if key.lower() == text_lower:
            return meta.get("canonical_name", key), 0.90

    # Partial match with whitespace normalization
    for key, meta in loinc_map.items():
        if key.lower() in text_lower or text_lower in key.lower():
            return meta.get("canonical_name", key), 0.75

    return None, 0.0


def extract_med_name(text: str, catalog: dict[str, Any] | None = None) -> tuple[Optional[str], float]:
    """Extract med.name from text using medication catalog.

    Requirements (FIX 5):
      - Compound medication names take precedence over constituent components.
      - Hyphenated and slash-separated compound names are handled.
      - Component names (e.g. 'amoxicillin') must NOT match as complete medications
        when embedded in compounds (e.g. 'amoxicillin-clavulanate' or 'amoxicillin/clavulanate').
      - Longer/more specific entries win.
    """
    if catalog is None:
        catalog = _med_catalog()

    text_lower = text.lower().strip()
    # Exact match
    if text_lower in catalog:
        return catalog[text_lower]["canonical_name"], 0.92

    # Check compounds and specific drugs (sort by key length descending)
    best_name: Optional[str] = None
    best_key_len = 0
    best_conf = 0.0

    for key, entry in sorted(catalog.items(), key=lambda kv: len(kv[0]), reverse=True):
        pattern = r"(?<![-/\w])" + re.escape(key) + r"(?![-/\w])"
        if re.search(pattern, text_lower):
            if len(key) > best_key_len:
                best_name = entry["canonical_name"]
                best_key_len = len(key)
                best_conf = 0.90 if ("-" in key or "/" in key) else 0.85

    if best_name:
        return best_name, best_conf

    return None, 0.0


def extract_med_dose(text: str) -> tuple[Optional[str], Optional[float], Optional[str], float]:
    """Extract med.dose (and unit_raw) from text.

    Returns: (raw_value_str, numeric_value, unit_raw, confidence)
    """
    # Prefer specific mass/volume units
    m = re.search(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|mL|units?|puffs?)\b", text, re.IGNORECASE)
    if m:
        raw = m.group(1)
        unit = m.group(2).lower()
        return raw, float(raw), unit, 0.90
    return None, None, None, 0.0


def extract_med_route(text: str) -> tuple[Optional[str], float]:
    """Extract med.route from text using canonical route vocabulary.

    Returns: (canonical_route, confidence)
    """
    text_lower = text.lower()
    # Check multi-word routes first (longest first)
    for phrase in sorted(_ROUTE_VOCAB.keys(), key=len, reverse=True):
        if re.search(r"\b" + re.escape(phrase) + r"\b", text_lower):
            return _ROUTE_VOCAB[phrase], 0.90
    return None, 0.0


def extract_med_frequency(text: str) -> tuple[Optional[str], float]:
    """Extract med.frequency from text.

    Returns: (frequency_str, confidence)
    """
    text_lower = text.lower()
    # Check abbreviations first
    for abbrev, expanded in _FREQ_ABBREV.items():
        if re.search(r"\b" + re.escape(abbrev) + r"\b", text_lower):
            return expanded, 0.88

    # Natural language patterns (ordered by specificity, longest first)
    freq_patterns = [
        (r"once\s+daily\s+in\s+the\s+morning\s+with\s+food", "once daily in the morning with food"),
        (r"once\s+daily\s+before\s+breakfast", "once daily before breakfast"),
        (r"once\s+daily\s+at\s+bedtime", "once daily at bedtime"),
        (r"once\s+daily\s+in\s+the\s+morning", "once daily in the morning"),
        (r"every\s+morning\s+on\s+an\s+empty\s+stomach", "every morning on an empty stomach"),
        (r"every\s+4\s+to\s+6\s+hours?\s+as\s+needed", "every 4 to 6 hours as needed"),
        (r"every\s+4-6\s+hours?\s+(?:prn|as\s+needed)", "every 4-6 hours as needed"),
        (r"four\s+times\s+(?:a\s+)?daily", "four times daily"),
        (r"three\s+times\s+(?:a\s+)?daily", "three times daily"),
        (r"twice\s+(?:a\s+)?daily", "twice daily"),
        (r"once\s+daily", "once daily"),
        (r"every\s+(\d+)\s+hours?", None),  # dynamic: captured group
        (r"every\s+morning", "every morning"),
        (r"every\s+evening", "every evening"),
        (r"at\s+bedtime", "at bedtime"),
        (r"as\s+needed", "as needed"),
        (r"daily", "daily"),
    ]
    for pattern, result in freq_patterns:
        m = re.search(pattern, text_lower)
        if m:
            if result is None:
                # Dynamic: "every N hours"
                return f"every {m.group(1)} hours", 0.88
            return result, 0.88
    return None, 0.0


def extract_med_duration(text: str) -> tuple[Optional[str], float]:
    """Extract med.duration from text. Tier 2 deterministic.

    Returns: (duration_str, confidence)
    """
    m_need = _DURATION_AS_NEEDED.search(text)
    if m_need:
        return "as needed", 0.90
    m = _DURATION_PATTERN.search(text)
    if m:
        return m.group(1).lower().strip(), 0.90
    return None, 0.0


# ─── EVIDENCE BUILDER ────────────────────────────────────────────────────────

def build_evidence(
    document_id: str,
    page: DocumentPage,
    text_snippet: str,
    char_start: int,
    char_end: int,
) -> Evidence:
    """Construct an Evidence object from a DocumentPage span.

    Resolves bounding box and OCR confidence from the DocumentPage token offsets.
    Falls back to a zero-area bbox if span is not mapped (e.g., born-digital no geometry).
    """
    # Get bounding box from page geometry
    bbox_list = page.get_bbox_for_span(char_start, char_end)
    if bbox_list is None:
        # Born-digital or span not mapped: use a degenerate bbox
        bbox_model = BBoxModel(x0=0.0, y0=0.0, x1=0.0, y1=0.0)
    else:
        x0, y0, x1, y1 = bbox_list
        # Defensive: ensure valid geometry
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        bbox_model = BBoxModel(x0=x0, y0=y0, x1=x1, y1=y1)

    ocr_conf = page.get_ocr_confidence_for_span(char_start, char_end)

    return Evidence(
        document_id=document_id,
        page_number=page.page_number,
        text=text_snippet,
        char_start=char_start,
        char_end=char_end,
        bbox=bbox_model,
        ocr_confidence=ocr_conf,
    )


# ─── DETERMINISTIC EXTRACTION PASS ──────────────────────────────────────────

class DeterministicExtractor:
    """Deterministic structured extraction engine.

    Processes a DocumentPage for a single region (identified by region_text and offsets)
    and extracts Claim + Evidence pairs for all detectable fields.

    Handles:
      Tier 2 (always deterministic, high confidence):
        lab.unit, lab.reference_range, lab.test_name, med.duration
      Tier 1 structured (deterministic attempt, may be insufficient → router routes to GLiNER):
        lab.value, lab.flag, med.name, med.dose, med.route, med.frequency
    """

    def __init__(
        self,
        loinc_map: dict[str, Any] | None = None,
        ucum_map: dict[str, Any] | None = None,
        med_catalog: dict[str, Any] | None = None,
    ):
        # Lazy-loaded on first use if not explicitly injected (e.g., for tests)
        self._loinc_map = loinc_map
        self._ucum_map = ucum_map
        self._med_catalog = med_catalog

    def _get_loinc(self) -> dict:
        return self._loinc_map if self._loinc_map is not None else _loinc_map()

    def _get_ucum(self) -> dict:
        return self._ucum_map if self._ucum_map is not None else _ucum_map()

    def _get_catalog(self) -> dict:
        return self._med_catalog if self._med_catalog is not None else _med_catalog()

    def extract_lab_row(
        self,
        document_id: str,
        page: DocumentPage,
        row_text: str,
        char_start: int,
        char_end: int,
        group_id: Optional[str] = None,
        region_id: str = "region_0",
    ) -> tuple[list[Evidence], list[Claim]]:
        """Extract all lab fields from a single table row text span.

        Returns (evidences, claims) — may contain multiple claims sharing one evidence.
        """
        if group_id is None:
            group_id = str(uuid.uuid4())

        evidence = build_evidence(document_id, page, row_text, char_start, char_end)
        claims: list[Claim] = []

        # --- lab.test_name (Tier 2) ---
        test_name, test_conf = extract_lab_test_name(row_text, loinc_map=self._get_loinc())
        if test_name is not None:
            claims.append(Claim(
                document_id=document_id,
                group_id=group_id,
                field_type="lab.test_name",
                value=test_name,
                value_numeric=None,
                unit_raw=None,
                extraction_method="deterministic",
                extraction_confidence=test_conf,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # --- lab.value (Tier 1 — deterministic attempt) ---
        raw_val, num_val, val_conf = extract_lab_value(row_text)
        if raw_val is not None:
            claims.append(Claim(
                document_id=document_id,
                group_id=group_id,
                field_type="lab.value",
                value=raw_val,
                value_numeric=num_val,
                unit_raw=None,
                extraction_method="deterministic",
                extraction_confidence=val_conf,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # --- lab.unit (Tier 2) ---
        unit, unit_conf = extract_lab_unit(row_text)
        if unit is not None:
            claims.append(Claim(
                document_id=document_id,
                group_id=group_id,
                field_type="lab.unit",
                value=unit,
                value_numeric=None,
                unit_raw=unit,
                extraction_method="deterministic",
                extraction_confidence=unit_conf,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # --- lab.reference_range (Tier 2) ---
        ref_range, ref_conf = extract_lab_reference_range(row_text)
        if ref_range is not None:
            claims.append(Claim(
                document_id=document_id,
                group_id=group_id,
                field_type="lab.reference_range",
                value=ref_range,
                value_numeric=None,
                unit_raw=None,
                extraction_method="deterministic",
                extraction_confidence=ref_conf,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # --- lab.flag (Tier 1 — deterministic attempt) ---
        flag, flag_conf = extract_lab_flag(row_text)
        if flag is not None:
            claims.append(Claim(
                document_id=document_id,
                group_id=group_id,
                field_type="lab.flag",
                value=flag,
                value_numeric=None,
                unit_raw=None,
                extraction_method="deterministic",
                extraction_confidence=flag_conf,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        return [evidence], claims

    def extract_med_line(
        self,
        document_id: str,
        page: DocumentPage,
        line_text: str,
        char_start: int,
        char_end: int,
        group_id: Optional[str] = None,
        region_id: str = "region_0",
    ) -> tuple[list[Evidence], list[Claim]]:
        """Extract all medication fields from a single line/block text span.

        Returns (evidences, claims) — may contain multiple claims sharing one evidence.
        """
        if group_id is None:
            group_id = str(uuid.uuid4())

        evidence = build_evidence(document_id, page, line_text, char_start, char_end)
        claims: list[Claim] = []
        catalog = self._get_catalog()

        # --- med.name (Tier 1 — deterministic attempt) ---
        med_name, name_conf = extract_med_name(line_text, catalog=catalog)
        if med_name is not None:
            claims.append(Claim(
                document_id=document_id,
                group_id=group_id,
                field_type="med.name",
                value=med_name,
                value_numeric=None,
                unit_raw=None,
                extraction_method="deterministic",
                extraction_confidence=name_conf,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # --- med.dose (Tier 1 — deterministic attempt) ---
        dose_raw, dose_num, dose_unit, dose_conf = extract_med_dose(line_text)
        if dose_raw is not None:
            claims.append(Claim(
                document_id=document_id,
                group_id=group_id,
                field_type="med.dose",
                value=dose_raw,
                value_numeric=dose_num,
                unit_raw=dose_unit,
                extraction_method="deterministic",
                extraction_confidence=dose_conf,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # --- med.route (Tier 1 — deterministic attempt) ---
        route, route_conf = extract_med_route(line_text)
        if route is not None:
            claims.append(Claim(
                document_id=document_id,
                group_id=group_id,
                field_type="med.route",
                value=route,
                value_numeric=None,
                unit_raw=None,
                extraction_method="deterministic",
                extraction_confidence=route_conf,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # --- med.frequency (Tier 1 — deterministic attempt) ---
        freq, freq_conf = extract_med_frequency(line_text)
        if freq is not None:
            claims.append(Claim(
                document_id=document_id,
                group_id=group_id,
                field_type="med.frequency",
                value=freq,
                value_numeric=None,
                unit_raw=None,
                extraction_method="deterministic",
                extraction_confidence=freq_conf,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # --- med.duration (Tier 2) ---
        duration, dur_conf = extract_med_duration(line_text)
        if duration is not None:
            claims.append(Claim(
                document_id=document_id,
                group_id=group_id,
                field_type="med.duration",
                value=duration,
                value_numeric=None,
                unit_raw=None,
                extraction_method="deterministic",
                extraction_confidence=dur_conf,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        return [evidence], claims

    def extract_region(
        self,
        document_id: str,
        page: DocumentPage,
        region_text: str,
        region_id: str = "region_0",
        is_lab: bool = True,
        char_offset: int = 0,
    ) -> tuple[list[Evidence], list[Claim]]:
        """Extract all fields from a structured region's full text.

        Splits the region into logical rows/lines and dispatches each to
        the appropriate sub-extractor.

        Char offsets are computed relative to char_offset (the region's offset on the page)
        and advanced monotonically so repeated text (e.g. 'NORMAL', 'mg/dL') does not bind
        to earlier occurrences on the page (FIX 6).
        """
        all_evidences: list[Evidence] = []
        all_claims: list[Claim] = []

        page_text = page.get_page_text()
        lines = region_text.splitlines()
        local_search_pos = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Find character offset within region text first, advancing monotonically
            local_idx = region_text.find(stripped, local_search_pos)
            if local_idx >= 0:
                char_start = char_offset + local_idx
                char_end = char_start + len(stripped)
                local_search_pos = local_idx + len(stripped)
            else:
                # Fallback: search in page text from char_offset
                page_idx = page_text.find(stripped, char_offset)
                if page_idx >= 0:
                    char_start = page_idx
                    char_end = page_idx + len(stripped)
                else:
                    char_start = char_offset
                    char_end = char_offset + len(stripped)

            group_id = str(uuid.uuid4())

            if is_lab:
                evs, clms = self.extract_lab_row(
                    document_id, page, stripped, char_start, char_end,
                    group_id=group_id, region_id=region_id,
                )
            else:
                evs, clms = self.extract_med_line(
                    document_id, page, stripped, char_start, char_end,
                    group_id=group_id, region_id=region_id,
                )

            all_evidences.extend(evs)
            all_claims.extend(clms)

        return all_evidences, all_claims
