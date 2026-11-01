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
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

from clindoc.extract.contracts import (
    BBoxModel, Claim, Evidence, ExtractionMethod,
    TIER_1_FIELDS, TIER_2_FIELDS, TemporalStatus,
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
    "pg/mL", "pg/ml", "cells/uL", "cells/ul", "mg/g", "mg/mg",
    "mcg/ml", "mcg/mL",
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

# Duration patterns (DEC-019 Tier 2 deterministic)
_WRITTEN_DURATION_NUMS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "twelve": "12", "fourteen": "14", "twenty": "20", "twenty-one": "21",
    "thirty": "30",
}

_DURATION_EXPLICIT_DIGIT = re.compile(
    r"""(?<!\bevery\s)(?<!\bq\s)(?<!\bq)(?:for\s+|x\s*|duration:\s*)(\d+)\s*(days?|weeks?|months?|years?)\b""",
    re.IGNORECASE,
)
_DURATION_EXPLICIT_WRITTEN = re.compile(
    r"""(?<!\bevery\s)(?<!\bq\s)(?<!\bq)(?:for\s+|x\s*|duration:\s*)(one|two|three|four|five|six|seven|eight|nine|ten|twelve|fourteen|twenty|twenty-one|thirty)\s*(days?|weeks?|months?|years?)\b""",
    re.IGNORECASE,
)
_DURATION_AS_NEEDED = re.compile(r"\bduration:\s*as\s+needed\b", re.IGNORECASE)
_DURATION_UNTIL_FINISHED = re.compile(r"\b(?:take\s+|duration:\s*)?(until\s+(?:finished|gone))\b", re.IGNORECASE)
_DURATION_TABLE_FIELD = re.compile(r"\bduration:\s*(\d+)\s*(days?|weeks?|months?|years?)\b", re.IGNORECASE)




# ─── LAB EXTRACTION EXCLUSION & CONTEXT PATTERNS ─────────────────────────────

_DATE_TIME_PAT = re.compile(
    r"""\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|[0-9O]{4}-[0-9O]{2}-[0-9O]{2})\b|\b\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:AM|PM|EST|EDT|CST|CDT|PST|PDT|UTC))?\b""",
    re.IGNORECASE,
)
_YEAR_PAT = re.compile(r"""\b(?:19|20)\d{2}\b""")
_DEMO_FIELD_PAT = re.compile(
    r"""\b(?:encounter|visit|date|dob|birth|born|age|sex|gender|mrn|patient(?:\s*(?:id|no\.?|#))?|accession|specimen(?:\s*(?:id|no\.?|#))?|provider|physician|npi|tel|phone|fax|(?:icu\s+)?bed|room)\s*:?\s*#?[\w\d-]+""",
    re.IGNORECASE,
)
_CLEAR_DEMOGRAPHIC_LINE = re.compile(
    r"""\b(?:patient(?:\s*(?:name|id|no\.?|#))?|mrn|dob|date\s+of\s+birth|born|age\s*:\s*\d+|gender|sex\s*:\s*[mf]|physician|provider|npi\s*:\s*\d+|encounter\s*(?:id|#|number|date)?|visit\s*(?:id|#|number|date)?|admit\s*date|admission\s*date|discharge\s*date|tel\s*:|phone\s*:|fax\s*:|(?:icu\s+)?bed\s*:\s*#?[\w\d-]+|room\s*:\s*#?[\w\d-]+)\b""",
    re.IGNORECASE,
)
_PHONE_PAT = re.compile(r"""\b\d{3}[-.]\d{3}[-.]\d{4}\b""")
_MED_DOSE_PAT = re.compile(
    r"""\b(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)*)\s*(mg|mcg|g|mL|units?|puffs?|tablets?|capsules?|tabs?|caps?|drops?|patches?|sprays?|mEq)\b(?!/(?:dL|min|g|mg|mL|L|uL|kg|hpf|lpf|mm3))""",
    re.IGNORECASE,
)
_MED_QTY_PAT = re.compile(
    r"""\b(?:dispense|refills?|quantity|qty)\s*:?\s*#?\s*\d+\b|#\s*\d+\b""",
    re.IGNORECASE,
)
_VITAL_PAT = re.compile(
    r"""\b(?:BP\s*:?\s*\d{2,3}/\d{2,3}(?:\s*mmHg)?|\d{2,3}/\d{2,3}\s*mmHg|(?:Pulse|Heart\s*Rate|HR)\s*:?\s*\d{2,3}(?:\s*bpm)?|\d{2,3}\s*bpm|(?:Temp|Temperature)\s*:?\s*\d{2,3}(?:\.\d+)?\s*(?:[FC]|deg\s*[FC])|\d{2,3}(?:\.\d+)?\s*°?[FC]|(?:SpO2|SaO2|O2\s*sat(?:uration)?)\s*:?\s*\d{2,3}%?|\d{2,3}%\s*(?:on\s+room\s+air|on\s+RA|room\s+air)|(?:RR|Resp)\s*:?\s*\d{1,2})\b""",
    re.IGNORECASE,
)
_CLINICAL_SCORE_PAT = re.compile(
    r"""\b(?:EF|LVEF)\s*:?\s*\d{1,2}%|\d{1,2}%\s*(?:EF|LVEF)|\b(?:NYHA|ECOG)\s*(?:Class\s*)?[I|V|X\d]+\b""",
    re.IGNORECASE,
)
_QUALITATIVE_LAB_PAT = re.compile(
    r"""\b(negative(?!\s+for)|positive(?!\s+for)|non[\s-]reactive|nonreactive|reactive|not\s+detected|detected|indeterminate|equivocal|trace|no\s+growth|no\s+organisms?(?:\s+seen)?|normal\s+flora)\b""",
    re.IGNORECASE,
)
_LAB_NUM_WITH_INEQ_PAT = re.compile(
    r"""(?<![\w.-])([<>]=?\s*-?\d+(?:\.\d+)?|-?\d+(?:\.\d+)?)(?![\w-])(?!\.\d)"""
)
_TIME_DURATION_PAT = re.compile(
    r"""\b(?:in\s+)?\d+(?:\.\d+)?\s*(?:hours?|hrs?|days?|weeks?|months?|minutes?|mins?|seconds?|secs?)\b""",
    re.IGNORECASE,
)


# Multi-analyte and medication order discrimination patterns (Phase 2 & 2.1)
_MED_SIG_PATTERNS = re.compile(
    r"\b(?:"
    r"po\b|oral|tablets?|capsules?|tabs?|subcutaneous|sublingually?|"
    r"daily|bid|tid|qid|q\d+h|prn|twice daily|once daily|three times daily|"
    r"every morning|at bedtime|qhs|qam|qpm|"
    r"take\b|prescribed|refill|dispense|sig\b|"
    r"by mouth|iv\b|intravenous|im\b|intramuscular|subq\b|sq\b"
    r")",
    re.IGNORECASE,
)

_TDM_DRUG_NAMES: frozenset[str] = frozenset({
    "tacrolimus", "vancomycin", "lithium", "levetiracetam",
    "lamotrigine", "digoxin", "carbamazepine", "phenytoin",
    "valproic acid", "cyclosporine",
})

_LAB_SPECIFIC_MARKERS = re.compile(
    r"\b(?:trough|peak|level|screen|panel|fasting|serum|plasma|specimen|lab|reference|normal interval|drawn)\b",
    re.IGNORECASE,
)

_ANALYTE_DESCRIPTIVE_PREFIXES = re.compile(
    r"\b(?:serum|plasma|urine|blood|fasting|random|peak|trough|repeat|initial|morning|bedtime|total|free|lab)\b",
    re.IGNORECASE,
)


_COMMON_LAB_NAMES: frozenset[str] = frozenset({
    "potassium", "sodium", "chloride", "bicarbonate", "co2", "glucose",
    "calcium", "magnesium", "phosphorus", "bun", "blood urea nitrogen",
    "creatinine", "serum creatinine", "hemoglobin", "hematocrit", "wbc",
    "white blood cell count", "rbc", "red blood cell count", "platelets",
    "platelet count", "alt", "ast", "alkaline phosphatase", "total bilirubin",
    "direct bilirubin", "indirect bilirubin", "total protein", "albumin",
    "globulin", "troponin", "troponin i", "troponin t", "bnp", "pro-bnp",
    "nt-probnp", "ck", "ck-mb", "lactate", "base excess", "anion gap",
    "hba1c", "a1c", "hemoglobin a1c", "tsh", "free t4", "total cholesterol",
    "ldl", "hdl", "triglycerides", "inr", "pt", "ptt", "d-dimer", "ferritin",
    "iron", "tibc", "crp", "esr", "hbsag", "hiv", "vdrl", "rpr", "hcv",
    "covid", "covid-19", "influenza", "strep",
    "ph", "vbg", "abg",
})

# Phase 3.1 — Observation Integrity Gating patterns
_UNRESULTED_ORDER_OR_PLANNED_PAT = re.compile(
    r"\b(?:"
    r"order(?:ed|s|ing)?|re-?order(?:ed|s|ing)?|to\s+order|"
    r"repeat(?:ed|ing)?|"
    r"check(?:ing)?|"
    r"schedul(?:ed|e|ing)|"
    r"pend(?:ing|ed|s)?|"
    r"monitor(?:ing|ed|s)?|"
    r"follow-?up|"
    r"registry|"
    r"drawn|"
    r"due\s+(?:on|in|next|tomorrow)|"
    r"plan(?:ned|ning)?|"
    r"request(?:ed|ing)?"
    r")\b",
    re.IGNORECASE,
)

_PANEL_HEADER_LINE = re.compile(
    r"^\s*(?:[A-Z\s]+PANEL|[A-Z\s]+PROFILE|[A-Z\s]+TESTS?)(?:\s*\([^)]*\))?\s*:?\s*$",
    re.IGNORECASE,
)

_COMPLETED_RESULT_WORDING = re.compile(
    r"\b(?:included|showed|revealed|demonstrated|noted|labs?|chemistries|results?)\b",
    re.IGNORECASE,
)

# Phase 3.2: Temporal Classification Patterns
_HISTORICAL_TIMELINE_PAT = re.compile(
    r"\b(?:"
    r"prior\s+to\s+arrival|pta|"
    r"previous(?:ly)?|prior|earlier|former(?:ly)?|"
    r"(?:previous|prior|last|earlier|past)\s+(?:admission|encounter|visit|hospitalization|stay|admission's)|"
    r"pre-?admission|"
    r"historical(?:ly)?|"
    r"(?:at\s+)?baseline(?:\s+(?:labs?|level|value))?|"
    r"last\s+(?:year|month|week)|"
    r"in\s+(?:19\d\d|20[0-2]\d)|"
    r"history\s+of|h/o"
    r")\b",
    re.IGNORECASE,
)

_CURRENT_TIMELINE_PAT = re.compile(
    r"\b(?:"
    r"today(?:'s)?|current(?:ly)?|now|at\s+present|"
    r"discharge\s+labs?|on\s+discharge|at\s+discharge|"
    r"(?<!prior\s)(?<!previous\s)(?<!last\s)admission\s+labs?|on\s+admission|at\s+admission|upon\s+admission|presenting\s+labs?|"
    r"repeat\s+labs?|repeat\s+level|"
    r"this\s+(?:morning|afternoon|evening|visit|admission|encounter)|"
    r"recent(?:ly)?\s+drawn"
    r")\b",
    re.IGNORECASE,
)

_PRESENT_TENSE_IS_PAT = re.compile(
    r"\b(?:is|are)\s+(?:now\s+)?(?:\d+(?:\.\d+)?|negative|positive|normal|high|low|critical|reactive|non-?reactive|undetectable|detected)\b",
    re.IGNORECASE,
)

_HISTORICAL_DIAGNOSIS_ONLY_PAT = re.compile(
    r"\b(?:history\s+of|h/o)\b",
    re.IGNORECASE,
)

_COMPARATIVE_CLAUSE_SPLIT_PAT = re.compile(
    r"""(?:\s*;\s*|,\s*(?:compared\s+(?:with|to)|vs\.?|versus)\s*|\s+(?:compared\s+(?:with|to)|vs\.?|versus)\s*|\s+(?:and|but)\s+(?:is|was)\s+)""",
    re.IGNORECASE,
)

_NEGATION_TRIGGER_PAT = re.compile(
    r"""\b(?:no\s+evidence\s+of|without\s+evidence\s+of|negative\s+for|without(?:\s+any)?|denies(?:\s+a|\s+any)?|(?<!cannot\s)(?<!can\'t\s)(?:rules?|ruled)\s+out|absent|no\s+(?:evidence\s+of|elevation(?:\s+in)?|demonstrated|significant\s+elevation)|not\s+elevated)\b""",
    re.IGNORECASE,
)

_UNCERTAINTY_PAT = re.compile(
    r"""\b(?:cannot\s+rule\s+out|can't\s+rule\s+out|unlikely(?:\s+to\s+represent)?|possible(?:\s+to\s+represent)?|questionable|borderline)\b""",
    re.IGNORECASE,
)








# ─── FIELD EXTRACTORS ────────────────────────────────────────────────────────

def extract_lab_value(
    text: str,
    loinc_map: Optional[dict[str, Any]] = None,
    is_structured_lab: bool = False,
) -> tuple[Optional[str], Optional[float], float]:
    """Extract lab.value from text string with context-awareness.

    Requires defensible laboratory context for unstructured lines without
    requiring units in every valid result. Suppresses non-lab numerical entities
    (dates, demographics, medication doses/quantities, vitals, clinical scores).
    Supports numeric values, signed/decimal values, inequality bounds (<, >),
    and qualitative determinations.

    Returns: (raw_value_str, numeric_value, confidence)
    """
    if not text or not text.strip():
        return None, None, 0.0

    text_lower = text.lower()

    if loinc_map is None:
        loinc_map = _loinc_map()

    # Context indicators
    test_name, _ = extract_lab_test_name(text, loinc_map=loinc_map)
    has_test_name = (test_name is not None) or any(
        re.search(r"\b" + re.escape(n) + r"\b", text_lower) for n in _COMMON_LAB_NAMES
    )
    unit, _ = extract_lab_unit(text)
    has_unit = (unit is not None)
    ref_range, _ = extract_lab_reference_range(text)
    has_ref_range = (ref_range is not None)
    flag, _ = extract_lab_flag(text)
    has_flag = (flag is not None)

    # Build non-lab exclusion spans (0-indexed character intervals)
    excluded_spans: list[tuple[int, int]] = []
    for pat in [
        _DATE_TIME_PAT,
        _TIME_DURATION_PAT,
        _DEMO_FIELD_PAT,
        _PHONE_PAT,
        _MED_DOSE_PAT,
        _MED_QTY_PAT,
        _VITAL_PAT,
        _CLINICAL_SCORE_PAT,
        _REF_RANGE_PATTERN,
    ]:
        for m in pat.finditer(text):
            excluded_spans.append((m.start(), m.end()))

    for m in _YEAR_PAT.finditer(text):
        excluded_spans.append((m.start(), m.end()))

    def is_in_excluded(start: int, end: int) -> bool:
        for ex_s, ex_e in excluded_spans:
            if max(start, ex_s) < min(end, ex_e):
                return True
        return False

    # Find numeric candidate values
    for m in _LAB_NUM_WITH_INEQ_PAT.finditer(text):
        c_start, c_end = m.start(), m.end()
        if is_in_excluded(c_start, c_end):
            continue

        raw = m.group(1).strip()
        num_m = re.search(r"(-?\d+(?:\.\d+)?)", raw)
        num = float(num_m.group(1)) if num_m else None

        has_ineq = any(op in raw for op in ["<", ">", "<=", ">="])
        has_lab_kw = any(
            w in text_lower
            for w in [
                "delta", "serum", "specimen", "analyte", "troponin",
                "chemistries", "blood", "urine", "lab", "result",
            ]
        )

        if is_structured_lab or has_test_name or has_unit or has_ref_range or has_flag or has_ineq or has_lab_kw:
            return raw, num, 0.90

    # If no numeric candidate matched, check for qualitative determination
    if is_structured_lab or has_test_name or has_unit or has_flag:
        qm = _QUALITATIVE_LAB_PAT.search(text)
        if qm:
            q_val = qm.group(1).strip()
            return q_val, None, 0.90

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
    # Mask structural non-flag phrases to prevent false matches:
    # 1. "NORMAL RANGE" / "NORMAL INTERVAL" / "NORMAL VALUES" (column headers)
    # 2. "CRITICAL CARE" / "CRITICAL UNIT" (medical specialty / note headers)
    masked_text = re.sub(r"\bNORMAL\s+(?:RANGE|INTERVAL|VALUES?)\b", "HEADER_REF_RANGE", text_upper)
    masked_text = re.sub(r"\bCRITICAL\s+(?:CARE|UNIT)\b", "SPECIALTY_HEADER", masked_text)

    for flag_kw in multi_char_flags:
        if re.search(r"\b" + re.escape(flag_kw) + r"\b", masked_text):
            return _FLAG_MAP[flag_kw], 0.95

    # Single-char flags (H, L, N): only match as standalone tokens NOT preceded by '/'
    # Pattern: whitespace (or start) followed by H/L/N followed by whitespace or end
    # Negative lookbehind: not preceded by letter (unit boundary)
    for single_char in ["H", "L", "N"]:
        # Must be a whole word NOT immediately after '/' (unit suffix)
        pattern = r"(?<![A-Za-z/])" + single_char + r"(?![A-Za-z/])"
        if re.search(pattern, masked_text):
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
    has_ref_kw = bool(re.search(r"\b(?:ref(?:erence)?|normal|interval|range)\b", text, re.IGNORECASE))

    for m in _REF_RANGE_PATTERN.finditer(text):
        val = m.group(1).strip()
        m_s, m_e = m.start(), m.end()

        # Reject matches that are part of an alphanumeric code/identifier
        # (e.g. "ACC-2026-88192" or "O4-26")
        prefix = text[:m_s]
        if re.search(r"[A-Za-z]+[-_]?$", prefix):
            continue

        # Date/time exclusion (including OCR-corrupted dates)
        is_date = False
        for dm in _DATE_TIME_PAT.finditer(text):
            if max(m_s, dm.start()) < min(m_e, dm.end()):
                is_date = True
                break
        if is_date:
            continue

        # Demographic / Accession field exclusion
        is_demo = False
        for dfm in _DEMO_FIELD_PAT.finditer(text):
            if max(m_s, dfm.start()) < min(m_e, dfm.end()):
                is_demo = True
                break
        if is_demo:
            continue

        # Reject medication dose combinations (e.g. "49-51 mg", "160-4.5 mcg", "25-100 mg")
        # when they lack genuine laboratory reference-range context
        is_med_dose = False
        for mdm in _MED_DOSE_PAT.finditer(text):
            if max(m_s, mdm.start()) < min(m_e, mdm.end()):
                if not has_ref_kw:
                    is_med_dose = True
                    break
        if is_med_dose:
            continue

        return val, 0.95
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

    # Word-boundary match against catalog keys (longer keys first to prevent partial shadow)
    for key, meta in sorted(loinc_map.items(), key=lambda kv: len(kv[0]), reverse=True):
        candidates = [key.lower()]
        base_name = re.sub(r"\s*\([^)]*\)", "", key.lower()).strip()
        if base_name and base_name != key.lower():
            candidates.append(base_name)
        for cand in candidates:
            if "/" in cand:
                cand_esc = re.sub(r"\s*/\s*", r"\\s*/\\s*", re.escape(cand))
            else:
                cand_esc = re.escape(cand)
            prefix = r"(?<![-A-Za-z0-9])" if not cand.startswith("-") else r"(?<![A-Za-z0-9])"
            suffix = r"(?![-A-Za-z0-9])" if not cand.endswith("-") else r"(?![A-Za-z0-9])"
            pattern = prefix + cand_esc + suffix
            if re.search(pattern, text_lower):
                return meta.get("canonical_name", key), 0.85

    return None, 0.0


def find_all_lab_test_names(
    text: str,
    loinc_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Find all non-overlapping lab test name matches in text, ordered by start position.

    Returns a list of dicts:
    {
        'canonical': str,
        'matched_text': str,
        'start': int,
        'end': int,
        'confidence': float,
    }
    """
    if not text:
        return []
    if loinc_map is None:
        loinc_map = _loinc_map()

    found_matches: list[dict[str, Any]] = []
    # Sort keys by descending length to prevent partial shadowing
    sorted_items = sorted(loinc_map.items(), key=lambda kv: len(kv[0]), reverse=True)
    occupied_spans: list[tuple[int, int]] = []

    for key, meta in sorted_items:
        canonical = meta.get("canonical_name", key)
        candidates = [key]
        base_name = re.sub(r"\s*\([^)]*\)", "", key).strip()
        if base_name and base_name.lower() != key.lower():
            candidates.append(base_name)

        for cand in candidates:
            if "/" in cand:
                cand_esc = re.sub(r"\s*/\s*", r"\\s*/\\s*", re.escape(cand))
            else:
                cand_esc = re.escape(cand)
            prefix = r"(?<![-A-Za-z0-9])" if not cand.startswith("-") else r"(?<![A-Za-z0-9])"
            suffix = r"(?![-A-Za-z0-9])" if not cand.endswith("-") else r"(?![A-Za-z0-9])"
            pattern = prefix + cand_esc + suffix
            for m in re.finditer(pattern, text, re.IGNORECASE):
                s, e = m.start(), m.end()
                if any(s < oe and e > os for os, oe in occupied_spans):
                    continue
                conf = 0.95 if cand.isupper() and len(cand) <= 5 else 0.90
                found_matches.append({
                    "canonical": canonical,
                    "matched_text": m.group(0),
                    "start": s,
                    "end": e,
                    "confidence": conf,
                })
                occupied_spans.append((s, e))

    found_matches.sort(key=lambda x: x["start"])
    return found_matches


def partition_line_for_analytes(
    row_text: str,
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Partition a multi-analyte narrative line into distinct segments, one per analyte.

    Each segment encapsulates the analyte and its associated value, unit, and flag.
    Returns a list of dicts:
    {
        'match': match_dict,
        'segment_text': str,
        'seg_start': int,
        'seg_end': int,
    }
    """
    n = len(matches)
    if n == 0:
        return []
    if n == 1:
        return [{
            "match": matches[0],
            "segment_text": row_text.strip(),
            "seg_start": 0,
            "seg_end": len(row_text),
        }]

    split_points: list[int] = []
    for i in range(n - 1):
        left_m = matches[i]
        right_m = matches[i + 1]
        inter_start = left_m["end"]
        inter_end = right_m["start"]
        inter_text = row_text[inter_start:inter_end]

        delims = list(re.finditer(r"[;.]|,\s*(?:and|but)?\s*|\s+(?:and|but)\s+|\s+&\s+", inter_text, re.IGNORECASE))
        if delims:
            chosen_delim = delims[-1]
            split_idx = inter_start + chosen_delim.end()
        else:
            prefix_match = _ANALYTE_DESCRIPTIVE_PREFIXES.search(inter_text)
            if prefix_match:
                split_idx = inter_start + prefix_match.start()
            else:
                split_idx = (inter_start + inter_end) // 2

        split_points.append(split_idx)

    segments: list[dict[str, Any]] = []
    seg_starts = [0] + split_points
    seg_ends = split_points + [len(row_text)]

    for i in range(n):
        s = seg_starts[i]
        e = seg_ends[i]
        seg_raw = row_text[s:e]

        leading_ws = len(seg_raw) - len(seg_raw.lstrip())
        trailing_ws = len(seg_raw) - len(seg_raw.rstrip())
        actual_start = s + leading_ws
        actual_end = e - trailing_ws
        seg_text = row_text[actual_start:actual_end]

        segments.append({
            "match": matches[i],
            "segment_text": seg_text,
            "seg_start": actual_start,
            "seg_end": actual_end,
        })

    return segments


def is_medication_order_line(text: str) -> bool:
    """Detect whether a line is a medication prescription/order rather than a lab observation.

    Checks for sig/route/frequency instructions while preserving genuine lab mentions
    (e.g., TDM drug levels like 'Tacrolimus trough level is 6.8 ng/mL').
    """
    if _LAB_SPECIFIC_MARKERS.search(text):
        return False
    if _MED_SIG_PATTERNS.search(text):
        return True
    if _MED_DOSE_PAT.search(text):
        text_lower = text.lower()
        if any(re.search(r"\b" + re.escape(drug) + r"\b", text_lower) for drug in _TDM_DRUG_NAMES):
            return True
    return False


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


# Dose patterns
_STRENGTH_DOSE_REGEX = re.compile(
    r"""(?<!#)\b(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)*)\s*(mg|mcg|g|mL|units?|puffs?|mEq)\b(?!/(?:dL|min|g|mL|L|uL))""",
    re.IGNORECASE,
)
_DISPENSE_PREFIX = re.compile(r"""\b(?:dispense|refills?|quantity|qty)\s*:?\s*#?$""", re.IGNORECASE)


def extract_med_dose(text: str) -> tuple[Optional[str], Optional[float], Optional[str], float]:
    """Extract med.dose (and unit_raw) from text.

    Supports single numeric quantities (e.g. "100 mg", "0.5 mg") and
    combination multi-component dosages (e.g. "49-51 mg", "160-4.5 mcg").
    Guards against laboratory concentration units (mg/dL, g/dL, mL/min)
    and dispense/refill counts.

    Returns: (raw_value_str, numeric_value, unit_raw, confidence)
    """
    for m in _STRENGTH_DOSE_REGEX.finditer(text):
        start = m.start()
        prefix = text[:start].strip()
        if _DISPENSE_PREFIX.search(prefix):
            continue
        raw = m.group(1)
        unit = m.group(2).lower()
        try:
            num = float(raw)
        except ValueError:
            num = None
        return raw, num, unit, 0.90
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

    Supports:
      - "for 10 days", "for 2 weeks", "for 12 months"
      - "x 7 days", "x 10 days", "x2 weeks"
      - Written numbers: "for two weeks", "x three days"
      - "duration: as needed", "as needed" (when explicit)
      - "until finished", "until gone"
      - Prescription table "Duration: 10 days"

    Guards against:
      - Interval frequencies ("every 2 weeks", "every 4 hours", "q2w")
      - Quantities and refills ("Dispense: #30", "Refills: 0")

    Returns: (duration_str, confidence)
    """
    text_lower = text.lower()

    # 1. "duration: as needed"
    if _DURATION_AS_NEEDED.search(text):
        return "as needed", 0.90

    # 2. "until finished" / "until gone"
    m_fin = _DURATION_UNTIL_FINISHED.search(text)
    if m_fin:
        return m_fin.group(1).lower().strip(), 0.90

    # 3. Explicit numeric duration: "for 10 days", "x 7 days", "duration: 10 days"
    for m in _DURATION_EXPLICIT_DIGIT.finditer(text):
        start = m.start()
        prefix = text_lower[max(0, start - 10):start]
        if "every" in prefix or "q" in prefix.split():
            continue
        num = m.group(1)
        unit = m.group(2).lower()
        return f"{num} {unit}", 0.90

    # 4. Explicit written duration: "for two weeks", "x three days"
    for m in _DURATION_EXPLICIT_WRITTEN.finditer(text):
        start = m.start()
        prefix = text_lower[max(0, start - 10):start]
        if "every" in prefix or "q" in prefix.split():
            continue
        word_num = m.group(1).lower()
        digit_num = _WRITTEN_DURATION_NUMS.get(word_num, word_num)
        unit = m.group(2).lower()
        return f"{digit_num} {unit}", 0.90

    # 5. Table field "Duration: N days"
    m_tab = _DURATION_TABLE_FIELD.search(text)
    if m_tab:
        return f"{m_tab.group(1)} {m_tab.group(2).lower()}", 0.90

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
        section_detector: Any | None = None,
        enable_section_detection: bool = False,
    ):
        # Lazy-loaded on first use if not explicitly injected (e.g., for tests)
        self._loinc_map = loinc_map
        self._ucum_map = ucum_map
        self._med_catalog = med_catalog
        self.section_detector = section_detector
        self.enable_section_detection = enable_section_detection

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
        section_category: Optional[str] = None,
        is_structured_lab: bool = False,
    ) -> tuple[list[Evidence], list[Claim]]:
        """Extract all lab fields from a single table row text span.

        Returns (evidences, claims) — may contain multiple claims sharing one evidence.
        """
        # When section awareness is active and row falls in header demographics, suppress all lab claims
        if section_category is not None and section_category in {"header_demographics", "header_preamble"}:
            return [], []

        # Medication order guard: suppress lab claims from medication orders
        if not is_structured_lab and is_medication_order_line(row_text):
            return [], []

        # Structured lab table panel header suppression (e.g., LIPID PANEL:, COMPREHENSIVE METABOLIC PANEL:)
        if is_structured_lab and _PANEL_HEADER_LINE.search(row_text):
            raw_v, _, _ = extract_lab_value(row_text, loinc_map=self._get_loinc(), is_structured_lab=True)
            flg, _ = extract_lab_flag(row_text)
            un, _ = extract_lab_unit(row_text)
            if raw_v is None and flg is None and un is None:
                return [], []

        # Suppress clear demographic / header lines without lab context
        if not is_structured_lab and _CLEAR_DEMOGRAPHIC_LINE.search(row_text):
            test_name_check, _ = extract_lab_test_name(row_text, loinc_map=self._get_loinc())
            unit_check, _ = extract_lab_unit(row_text)
            if test_name_check is None and unit_check is None:
                return [], []

        # Multi-analyte narrative line support (Phase 2 & Phase 3.2.1)
        matches = find_all_lab_test_names(row_text, loinc_map=self._get_loinc())

        # Coordinated comparative clauses sharing a single analyte subject (Phase 3.2.1)
        # e.g., "Glucose was 95 mg/dL last year and is 110 mg/dL today."
        # e.g., "Glucose is 110 mg/dL today, compared with 95 mg/dL previously."
        if not is_structured_lab and len(matches) == 1:
            split_m = _COMPARATIVE_CLAUSE_SPLIT_PAT.search(row_text)
            if split_m and split_m.start() > matches[0]["end"]:
                left_clause = row_text[:split_m.start()].strip()
                right_clause = row_text[split_m.end():].strip()
                v1, _, _ = extract_lab_value(left_clause, loinc_map=self._get_loinc(), is_structured_lab=False)
                v2, _, _ = extract_lab_value(right_clause, loinc_map=self._get_loinc(), is_structured_lab=False)
                if v1 is not None and v2 is not None:
                    matches = [
                        matches[0],
                        {
                            "canonical": matches[0]["canonical"],
                            "matched_text": matches[0]["matched_text"],
                            "start": split_m.end(),
                            "end": split_m.end(),
                            "confidence": matches[0]["confidence"],
                        },
                    ]

        if not is_structured_lab and len(matches) >= 2:
            segments = partition_line_for_analytes(row_text, matches)
            all_evidences: list[Evidence] = []
            all_claims: list[Claim] = []

            # Check if any segment on this line contains a completed value or flag
            row_has_completed_result = False
            for s in segments:
                rv, _, _ = extract_lab_value(s["segment_text"], loinc_map=self._get_loinc(), is_structured_lab=False)
                rf, _ = extract_lab_flag(s["segment_text"])
                if rv is not None or rf is not None:
                    row_has_completed_result = True
                    break

            for seg in segments:
                m = seg["match"]
                seg_text = seg["segment_text"]
                seg_char_start = char_start + seg["seg_start"]
                seg_char_end = char_start + seg["seg_end"]

                # Per-clause medication discrimination guard (Phase 2.1)
                if not is_structured_lab and is_medication_order_line(seg_text):
                    continue

                obs_group_id = str(uuid.uuid4())

                temporal_status = self.classify_temporal_status(
                    clause_text=seg_text,
                    row_text=row_text,
                    section_category=section_category,
                    is_structured_lab=is_structured_lab,
                )
                negated = self.classify_assertion_negation(
                    clause_text=seg_text,
                    row_text=row_text,
                )

                seg_evidence = build_evidence(document_id, page, seg_text, seg_char_start, seg_char_end)
                seg_claims: list[Claim] = []

                # --- lab.test_name (Tier 2) ---
                seg_claims.append(Claim(
                    document_id=document_id,
                    group_id=obs_group_id,
                    field_type="lab.test_name",
                    value=m["canonical"],
                    value_numeric=None,
                    unit_raw=None,
                    extraction_method="deterministic",
                    extraction_confidence=m["confidence"],
                    negated=negated,
                    temporal_status=temporal_status,
                    evidence_id=seg_evidence.evidence_id,
                    region_id=region_id,
                ))

                # --- lab.value and lab.reference_range gating (V3 rules) ---
                if section_category is None:
                    allow_val_ref = True
                else:
                    in_med_sec = (section_category == "medications")
                    in_lab_sec = (section_category == "labs_and_studies")
                    allow_val_ref = in_lab_sec or not in_med_sec

                if allow_val_ref:
                    raw_val, num_val, val_conf = extract_lab_value(
                        seg_text,
                        loinc_map=self._get_loinc(),
                        is_structured_lab=False,
                    )
                    if raw_val is not None:
                        seg_claims.append(Claim(
                            document_id=document_id,
                            group_id=obs_group_id,
                            field_type="lab.value",
                            value=raw_val,
                            value_numeric=num_val,
                            unit_raw=None,
                            extraction_method="deterministic",
                            extraction_confidence=val_conf,
                            negated=negated,
                            temporal_status=temporal_status,
                            evidence_id=seg_evidence.evidence_id,
                            region_id=region_id,
                        ))

                # --- lab.unit (Tier 2) ---
                unit, unit_conf = extract_lab_unit(seg_text)
                if unit is not None:
                    seg_claims.append(Claim(
                        document_id=document_id,
                        group_id=obs_group_id,
                        field_type="lab.unit",
                        value=unit,
                        value_numeric=None,
                        unit_raw=unit,
                        extraction_method="deterministic",
                        extraction_confidence=unit_conf,
                        negated=negated,
                        temporal_status=temporal_status,
                        evidence_id=seg_evidence.evidence_id,
                        region_id=region_id,
                    ))

                # --- lab.reference_range (Tier 2) ---
                if allow_val_ref:
                    ref_range, ref_conf = extract_lab_reference_range(seg_text)
                    if ref_range is not None:
                        seg_claims.append(Claim(
                            document_id=document_id,
                            group_id=obs_group_id,
                            field_type="lab.reference_range",
                            value=ref_range,
                            value_numeric=None,
                            unit_raw=None,
                            extraction_method="deterministic",
                            extraction_confidence=ref_conf,
                            negated=negated,
                            temporal_status=temporal_status,
                            evidence_id=seg_evidence.evidence_id,
                            region_id=region_id,
                        ))

                # --- lab.flag (Tier 1) ---
                flag, flag_conf = extract_lab_flag(seg_text)
                if flag is not None:
                    seg_claims.append(Claim(
                        document_id=document_id,
                        group_id=obs_group_id,
                        field_type="lab.flag",
                        value=flag,
                        value_numeric=None,
                        unit_raw=None,
                        extraction_method="deterministic",
                        extraction_confidence=flag_conf,
                        negated=negated,
                        temporal_status=temporal_status,
                        evidence_id=seg_evidence.evidence_id,
                        region_id=region_id,
                    ))

                # Observation Integrity Gate (Phase 3.1)
                if not self._observation_integrity_check(
                    seg_claims,
                    seg_text,
                    section_category,
                    is_structured_lab,
                    row_has_completed_result=row_has_completed_result,
                    row_text=row_text,
                ):
                    continue

                all_evidences.append(seg_evidence)
                all_claims.extend(seg_claims)

            return all_evidences, all_claims

        if group_id is None:
            group_id = str(uuid.uuid4())

        evidence = build_evidence(document_id, page, row_text, char_start, char_end)
        claims: list[Claim] = []

        temporal_status = self.classify_temporal_status(
            clause_text=row_text,
            row_text=row_text,
            section_category=section_category,
            is_structured_lab=is_structured_lab,
        )
        negated = self.classify_assertion_negation(
            clause_text=row_text,
            row_text=row_text,
        )

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
                negated=negated,
                temporal_status=temporal_status,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # --- lab.value and lab.reference_range gating (V3 rules) ---
        # If section_category is None, baseline behavior applies (always extract).
        # If section_category is provided, scoped suppression applies:
        #   - Allowed in labs_and_studies section or structured lab documents
        #   - In clinical lines: allowed only when anchored by a recognized test_name
        #   - Suppressed in medications sections
        if section_category is None:
            allow_val_ref = True
        else:
            in_med_sec = (section_category == "medications")
            in_lab_sec = (section_category == "labs_and_studies") or is_structured_lab
            allow_val_ref = in_lab_sec or (not in_med_sec and test_name is not None)

        # --- lab.value (Tier 1 — deterministic attempt) ---
        if allow_val_ref:
            raw_val, num_val, val_conf = extract_lab_value(
                row_text,
                loinc_map=self._get_loinc(),
                is_structured_lab=is_structured_lab,
            )
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
                    negated=negated,
                    temporal_status=temporal_status,
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
                negated=negated,
                temporal_status=temporal_status,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # --- lab.reference_range (Tier 2) ---
        if allow_val_ref:
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
                    negated=negated,
                    temporal_status=temporal_status,
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
                negated=negated,
                temporal_status=temporal_status,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            ))

        # Observation Integrity Gate (Phase 3.1)
        if not self._observation_integrity_check(
            claims, row_text, section_category, is_structured_lab
        ):
            return [], []

        return [evidence], claims

    @staticmethod
    def _observation_integrity_check(
        group_claims: list[Claim],
        text_segment: str,
        section_category: Optional[str],
        is_structured_lab: bool,
        row_has_completed_result: bool = False,
        row_text: str = "",
    ) -> bool:
        """Conservative observation integrity gate (Phase 3.1 & 3.2).

        Prevents narrative laboratory mentions from becoming false laboratory
        observations when no completed result exists (e.g. planned tests,
        pending tests, bare monitoring mentions, unresulted test orders,
        and historical diagnoses without actual test results).

        Core rule:
        For narrative text, do not emit a standalone lab.test_name observation
        unless there is evidence of a completed result.
        """
        has_test_name = any(c.field_type == "lab.test_name" for c in group_claims)
        if not has_test_name:
            return False

        if is_structured_lab:
            return True

        has_value = any(c.field_type == "lab.value" for c in group_claims)
        has_flag = any(c.field_type == "lab.flag" for c in group_claims)
        has_completed_evidence = has_value or has_flag

        is_order_or_planned = bool(_UNRESULTED_ORDER_OR_PLANNED_PAT.search(text_segment))
        if is_order_or_planned and not has_completed_evidence:
            return False

        # Historical diagnosis without actual test result (e.g., 'History of elevated cholesterol')
        if bool(_HISTORICAL_DIAGNOSIS_ONLY_PAT.search(text_segment)) and not has_value:
            return False

        # Phase 3.3.1: Negated clinical assertions or epistemic uncertainty without actual completed result
        # (e.g. 'No evidence of elevated troponin', 'Negative for elevated troponin',
        # 'Cannot rule out elevated troponin', 'Unlikely to represent elevated troponin')
        is_negated_or_uncertain = bool(_NEGATION_TRIGGER_PAT.search(text_segment)) or bool(_UNCERTAINTY_PAT.search(text_segment))
        if is_negated_or_uncertain and not has_value:
            return False

        if section_category == "labs_and_studies" and not is_order_or_planned:
            return True

        if has_completed_evidence:
            return True

        # Explicit completed-result wording supported by an actual result on the line
        if row_has_completed_result and bool(_COMPLETED_RESULT_WORDING.search(row_text)):
            return True

        return False

    @staticmethod
    def classify_temporal_status(
        clause_text: str,
        row_text: str = "",
        section_category: Optional[str] = None,
        is_structured_lab: bool = False,
    ) -> TemporalStatus:
        """Classify the clinical temporal qualification of a laboratory observation.

        Distinguishes current-encounter laboratory results from genuinely historical
        results mentioned in clinical narratives (Phase 3.2).
        """
        # 1. Clause-level historical indicators
        if _HISTORICAL_TIMELINE_PAT.search(clause_text):
            return "historical"

        # 2. Clause-level current indicators
        if _CURRENT_TIMELINE_PAT.search(clause_text):
            return "current"

        if _PRESENT_TENSE_IS_PAT.search(clause_text):
            return "current"

        # 3. Contextual indicators when clause is temporally neutral
        # If the entire row has an explicit historical header/marker and the clause is neutral:
        if row_text and _HISTORICAL_TIMELINE_PAT.search(row_text) and not _CURRENT_TIMELINE_PAT.search(row_text):
            return "historical"

        if is_structured_lab:
            return "current"

        if section_category == "labs_and_studies":
            return "current"

        if re.search(
            r"\b(?:laboratory\s+results?|current\s+labs?|discharge\s+labs?|(?<!prior\s)(?<!previous\s)admission\s+labs?)\b",
            row_text,
            re.IGNORECASE,
        ):
            return "current"

        return "unspecified"

    @staticmethod
    def classify_assertion_negation(
        clause_text: str,
        row_text: str = "",
    ) -> bool:
        """Classify clinical assertion negation for an extracted observation (Phase 3.3 & 3.3.1).

        Distinguishes actual negative laboratory results (e.g. 'negative',
        'not detected', 'no growth') from negated clinical assertions
        (e.g. 'no evidence of', 'negative for', 'without', 'denies', 'ruled out', 'absent').

        Conservative rule:
        Returns True only when explicit clause-level negation triggers are present,
        and excludes epistemic uncertainty language (e.g. 'cannot rule out', 'unlikely').
        """
        if _UNCERTAINTY_PAT.search(clause_text):
            return False

        if _NEGATION_TRIGGER_PAT.search(clause_text):
            return True

        return False

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
        sections: Optional[list[Any]] = None,
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

        # Lazy detect sections if enabled and not already provided
        active_sections = sections
        if self.enable_section_detection and active_sections is None and self.section_detector is not None:
            try:
                active_sections = self.section_detector.detect_sections(page_text)
            except Exception as e:
                logger.warning(
                    "SECTION_DETECTION_RUNTIME_FAILED in extract_region for doc %s, p%s: %s. Falling back to baseline.",
                    document_id, page.page_number, e,
                )
                active_sections = None

        is_structured_lab = document_id.upper().startswith("LAB_")

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

            sec_cat = None
            if active_sections is not None and self.section_detector is not None:
                sec = self.section_detector.get_section_for_offset(char_start, active_sections)
                sec_cat = sec.category if sec else "unknown"

            if is_lab:
                evs, clms = self.extract_lab_row(
                    document_id, page, stripped, char_start, char_end,
                    group_id=group_id, region_id=region_id,
                    section_category=sec_cat,
                    is_structured_lab=is_structured_lab,
                )
            else:
                evs, clms = self.extract_med_line(
                    document_id, page, stripped, char_start, char_end,
                    group_id=group_id, region_id=region_id,
                )

            all_evidences.extend(evs)
            all_claims.extend(clms)

        return all_evidences, all_claims
