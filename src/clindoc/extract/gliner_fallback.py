"""GLiNER fallback abstraction for the extraction subsystem.

Authority: DEC-001, DEC-004, DEC-019
Scope: Fallback for Tier 1 fields in structured regions where deterministic
extraction confidence is below the DEC-004 threshold (provisional: 0.75).

Design requirements:
  - Model abstraction: never hard-code model loading throughout the pipeline.
  - Lazy loading: model/subprocess only initialised on first call.
  - CPU execution for tests (no GPU required).
  - Mock path: testable without model downloads.
  - Maps GLiNER entity labels to canonical FieldType values.
  - Preserves confidence from model output.
  - Rejects unsupported entity labels (DEC-017 boundary enforcement).
  - Subprocess isolation: the real model runs in .venv_ner to avoid
    GLiNER's dependency conflicts with the main venv.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional, Protocol

from clindoc.extract.contracts import (
    BBoxModel, Claim, Evidence, ExtractionMethod,
    TIER_1_FIELDS,
)
from clindoc.ocr.document_text import DocumentPage


# ─── CANONICAL LABEL MAP ─────────────────────────────────────────────────────
# Maps GLiNER entity labels → canonical field_type (DEC-017)
# Only Tier 1 labels are accepted; others are rejected.
_GLINER_LABEL_TO_FIELD: dict[str, str] = {
    "medication": "med.name",
    "dosage": "med.dose",
    "route": "med.route",
    "frequency": "med.frequency",
    "lab_value": "lab.value",
    "lab value": "lab.value",
    "lab_flag": "lab.flag",
    "lab flag": "lab.flag",
}

# Labels NOT accepted by this extractor (out of scope or Tier 2 — handled deterministically)
# DEC-019: Tier 2 fields (including med.duration) must remain strictly deterministic.
_REJECTED_LABELS: frozenset[str] = frozenset({
    "diagnosis", "condition", "procedure", "allergy",
    "vital_sign", "social_history", "family_history",
    "dose_unit",  # captured as part of med.dose unit_raw
    "duration",   # med.duration is Tier 2 (DEC-019); must NOT be emitted by GLiNER
})

# Validation patterns for GLiNER claims
_VITAL_PAT = re.compile(
    r"""\b(?:BP\s+\d+/\d+|Pulse\s+\d+|Temp\s+\d+(?:\.\d+)?|SpO2\s+\d+%?|Heart\s+Rate\s+\d+|RR\s+\d+)\b""",
    re.IGNORECASE,
)
_VITAL_LINE_PAT = re.compile(
    r"""\b(?:vitals?|vital\s+signs?|blood\s+pressure|heart\s+rate|temperature|pulse|spo2|oxygen\s+saturation)\s*:""",
    re.IGNORECASE,
)
_DATE_PAT = re.compile(r"""\b(?:\d{4}[-/.][01O]\d[-/.][0-3O]\d|[01O]\d[-/.][0-3O]\d[-/.]\d{2,4})\b""")
_OCR_DATE_PAT = re.compile(r"""\b(?:[12][0-9O]{3}[-/.][0-9O]{2}[-/.][0-9O]{2})\b""")
_METADATA_PAT = re.compile(r"""\b(?:mrn|npi|tel|phone|fax|dob|zip|pat|patient)\s*:?-?\s*[\w-]+""", re.IGNORECASE)
_ID_CODE_PAT = re.compile(r"""\b[A-Za-z]{2,}-\d+\b""")
_REF_INTERVAL_PAT = re.compile(r"""^\d+(?:\.\d+)?\s*[-–—]\s*\d+(?:\.\d+)?(?:\s+[a-zA-Z/%]+)?$""")
_ANALYTE_SUFFIX_PAT = re.compile(r"""\b(?:protein|count|panel|screen|test|level|analyte|specimen|profile)\b""", re.IGNORECASE)

_QUALITATIVE_LAB_TERMS = frozenset({
    "negative", "positive", "non-reactive", "nonreactive", "reactive",
    "detected", "not detected", "not-detected", "equivocal", "trace",
    "present", "absent", "indeterminate",
})

_LAB_ANALYTE_OR_FLAG_WORDS = frozenset({
    "normal", "high", "low", "critical", "elevated", "abnormal", "wnl",
    "serum creatinine", "hemoglobin", "white blood cell count", "platelet count",
    "bun", "serum sodium", "serum alt", "npi", "potassium", "serum potassium",
    "creatinine", "total cholesterol", "hemoglobin a1c", "ast", "hematocrit",
    "calcium", "total bilirubin", "serum tsh", "free t4", "free t3", "serum glucose",
    "troponin i", "troponin t", "bnp", "peak troponin i", "peak troponin t",
    "sodium", "chloride", "bicarbonate", "co2", "anion gap", "magnesium",
    "phosphorus", "albumin", "total protein", "alkaline phosphatase", "alt",
    "bilirubin", "inr", "pt", "ptt",
    "cd4 count", "cd4", "paco2", "pao2", "hco3", "24-hour urine protein",
    "urine protein", "total t3", "total t4", "viral load", "hiv viral load",
})

_CONCENTRATION_UNIT_PAT = re.compile(r"/(?:dL|min|g|mL|L|uL|mm3|hpf|lpf|kg)\b", re.IGNORECASE)
_DOSE_UNIT_PAT = re.compile(
    r"""\b(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)*)\s*(mg|mcg|g|mL|units?|puffs?|tablets?|capsules?|tabs?|caps?|drops?|patches?|sprays?|mEq)\b(?!/(?:dL|min|g|mL|L|uL))""",
    re.IGNORECASE,
)
_DISPENSE_PAT = re.compile(r"""(?:\b(?:dispense|refills?|quantity|qty)\s*:?\s*#?|#)\s*\d+""", re.IGNORECASE)


def is_valid_gliner_lab_value(val: str, ctx: str = "") -> bool:
    """Validate that an extracted GLiNER entity actually represents a lab value.

    Rejects analyte names, categorical flags, vital signs, metadata tokens,
    demographics, and reference intervals. Preserves numeric, signed, decimal,
    and inequality-prefixed values, compound numeric values with units,
    analyte-prefixed results (e.g. 'INR 2.3'), and standard qualitative lab
    determinations.
    """
    v_clean = val.strip().lower()
    if v_clean in _LAB_ANALYTE_OR_FLAG_WORDS:
        return False
    if v_clean in _QUALITATIVE_LAB_TERMS:
        return True
    if not re.search(r"\d", val):
        return False
    if _VITAL_PAT.search(val):
        return False
    if ctx and _VITAL_LINE_PAT.search(ctx) and _VITAL_PAT.search(ctx):
        return False

    # Metadata & demographic entity suppression
    if _METADATA_PAT.search(val) or _ID_CODE_PAT.search(val) or _DATE_PAT.fullmatch(v_clean) or _OCR_DATE_PAT.fullmatch(val.strip()):
        return False

    # Standalone 10-digit NPI / phone numbers
    if re.fullmatch(r"\d{10}", val.strip()):
        return False

    # Contextual age: e.g. "Age: 63"
    if ctx and re.search(r"\bage\s*:\s*" + re.escape(val.strip()) + r"\b", ctx, re.IGNORECASE):
        # Only reject if val is just the bare number without a lab concentration unit
        if not re.search(r"\b(?:mg|mcg|g|mL|mmol|uL|U|IU|mEq|%|pg|cells)\b", val, re.IGNORECASE):
            return False

    # Contextual date / order prefix: e.g. "Date: 2O26-O4-28" or "Order: RX-19482"
    if ctx and re.search(r"\b(?:date|order|rx)\s*:\s*" + re.escape(val.strip()), ctx, re.IGNORECASE):
        if not re.search(r"\b(?:mg|mcg|g|mL|mmol|uL|U|IU|mEq|%|pg|cells)\b", val, re.IGNORECASE):
            return False

    # Hyphenated reference intervals: e.g. "7.35 - 7.45", "30 - 100 ng/mL"
    if _REF_INTERVAL_PAT.search(val.strip()):
        return False

    # Comparator-plus-unit reference limit preceded by a result value on the same line:
    # e.g. "eGFR 28 > 60 mL/min LOW" where "28" is the result and "> 60 mL/min" is the reference range.
    if re.match(r"^[<>]=?\s*-?\d+(?:\.\d+)?", val.strip()):
        if ctx:
            idx_in_ctx = ctx.find(val)
            if idx_in_ctx > 0:
                preceding_ctx = ctx[:idx_in_ctx].strip()
                if re.search(r"\b\d+(?:\.\d+)?\s*$", preceding_ctx):
                    return False

    # Analyte names with digits:
    # 1. Names ending with descriptive analyte nouns (e.g. "24-hour urine protein", "CD4 Count")
    if _ANALYTE_SUFFIX_PAT.search(val):
        m_num = re.search(r"(?<![a-zA-Z\d.-])([<>]=?\s*-?\d+(?:\.\d+)?)(?![a-zA-Z\d])", val)
        if not m_num or (m_num.start() == 0 and re.search(r"^\d+(?:-hour|-hr)\b", val, re.IGNORECASE)):
            return False

    # 2. Analyte codes where all digits are fused to letters without an independent measurement:
    # e.g. "PaCO2", "PaO2", "HCO3", "Free T3"
    tokens = val.split()
    if all(re.search(r"[a-zA-Z]+\d+", tok) or not re.search(r"\d", tok) for tok in tokens):
        return False

    return True


def is_valid_gliner_med_dose(val: str, ctx: str = "") -> bool:
    """Validate that an extracted GLiNER entity actually represents a medication dose.

    Rejects laboratory concentration units, dispense quantities, refill counts,
    and unrelated numbers. Supports mass/volume units, dosage forms, and decimals.
    """
    v_clean = val.strip().lower()
    if _CONCENTRATION_UNIT_PAT.search(val):
        return False
    if _DISPENSE_PAT.search(val):
        return False
    if _METADATA_PAT.search(val) or _DATE_PAT.search(val):
        return False
    if _DOSE_UNIT_PAT.search(val):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)*", val.strip()):
        if ctx and _DOSE_UNIT_PAT.search(ctx):
            return True
        return False
    return False


_RECOGNIZED_LAB_FLAGS = frozenset({
    "normal", "high", "low", "critical", "abnormal", "elevated",
    "critical high", "critical low", "panic", "panic high", "panic low",
    "wnl", "hi", "lo",
    "h", "l",
    "neg", "pos", "negative", "positive",
    "reactive", "non-reactive", "nonreactive",
    "undetectable", "detectable",
    "equivocal", "indeterminate",
})

_QUANTITATIVE_FLAG_PAT = re.compile(r"^(?:[1-4]\+|\+{1,4})$")


def is_valid_gliner_lab_flag(val: str, ctx: str = "") -> bool:
    """Validate that an extracted GLiNER entity actually represents a clinical lab flag.

    Accepts standardized categorical clinical flags (HIGH, LOW, NORMAL, CRITICAL,
    ABNORMAL, ELEVATED, NEGATIVE, POSITIVE, REACTIVE, NON-REACTIVE, UNDETECTABLE,
    DETECTABLE, H, L) and quantitative flags (1+, 2+, 3+, 4+).

    Rejects header tokens, disclaimers ('PHI', 'NOT PHI', 'SYNTHETIC CLINICAL ENCOUNTER NOTE'),
    patient/provider/encounter identifiers ('PAT-819201', NPIs, MRNs), dates, timestamps,
    room/bed identifiers, pure numeric values, and long narrative phrases.
    """
    if not val or not val.strip():
        return False

    v_strip = val.strip()
    # Strip leading/trailing brackets, parentheses, colons, punctuation
    v_clean = re.sub(r"^[\s\(\[\{]+|[\s\)\]\}\.\,\:\;]+$", "", v_strip).strip().lower()
    if not v_clean:
        return False

    # Length & word count bounds: flags are short tokens
    if len(v_clean) > 25 or len(v_clean.split()) > 3:
        return False

    # Disclaimers, headers, metadata tokens
    if re.search(
        r"\b(?:phi|not\s+phi|synthetic|clinical|encounter|note|evaluation|only|disclaimer|report|attending|specimen)\b",
        v_clean,
    ):
        return False
    if _METADATA_PAT.search(v_strip) or _ID_CODE_PAT.search(v_strip):
        return False
    if _DATE_PAT.search(v_strip) or _OCR_DATE_PAT.search(v_strip):
        return False
    if re.search(r"\b\d{1,2}:\d{2}\b", v_strip):
        return False
    if re.search(r"\b(?:icu\s+)?bed\b|\broom\b", v_clean):
        return False

    # Pure numeric values (e.g. '12', '24', '145') are values/counts, not flags
    if re.fullmatch(r"^-?\d+(?:\.\d+)?$", v_clean):
        return False

    # Single-char flags (H, L): must not be a unit suffix (e.g., '/L' in 'mEq/L' or 'g/dL')
    if v_clean in {"h", "l"}:
        if ctx:
            idx = ctx.find(v_strip)
            if idx > 0 and ctx[idx - 1] in "/-":
                return False
            if idx >= 0 and idx + len(v_strip) < len(ctx):
                after_char = ctx[idx + len(v_strip)]
                if after_char.isalnum():
                    return False

    # Contextual check: adjective modifying non-lab nouns (e.g., 'high risk', 'low blood pressure')
    if v_clean in {"high", "low", "elevated", "abnormal"}:
        if ctx and re.search(
            r"\b" + re.escape(v_strip) + r"\s+(?:risk|blood\s+pressure|fever|potency|dose|doses|intake)\b",
            ctx,
            re.IGNORECASE,
        ):
            return False

    # Check against allowlist of clinical flags and quantitative patterns
    if v_clean in _RECOGNIZED_LAB_FLAGS:
        return True
    if _QUANTITATIVE_FLAG_PAT.fullmatch(v_clean):
        return True

    return False


# The GLiNER worker's canonical label set (must match gliner_worker.py)
_WORKER_LABELS = [
    "medication", "dosage", "dose unit", "dosage form",
    "route", "frequency", "lab value", "lab flag",
]

_PROJ_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_VENV_PYTHON = _PROJ_ROOT / ".venv_ner" / "Scripts" / "python.exe"
_DEFAULT_WORKER_SCRIPT = Path(__file__).parent / "gliner_worker.py"


# ─── PROVIDER PROTOCOL ───────────────────────────────────────────────────────

class GLiNERProvider(Protocol):
    """Protocol for GLiNER inference providers.

    Implementations:
      - SubprocessGLiNERProvider: real model via .venv_ner subprocess
      - MockGLiNERProvider: deterministic mock for unit tests
    """

    def predict_batch(self, texts: list[str]) -> list[list[dict[str, Any]]]:
        """Run NER on a batch of texts.

        Returns a list of entity-span lists, one per input text.
        Each entity dict has keys: label, text, start, end, score.
        """
        ...


# ─── SUBPROCESS PROVIDER (real GLiNER) ───────────────────────────────────────

class SubprocessGLiNERProvider:
    """Runs GLiNER-BioMed via subprocess in the dedicated .venv_ner environment.

    Model loading is lazy: no subprocess is spawned until predict_batch() is called.
    """

    def __init__(
        self,
        venv_python: Path | None = None,
        worker_script: Path | None = None,
        model_name: str = "Ihor/gliner-biomed-base-v1.0",
        threshold: float = 0.30,
    ):
        self.venv_python = venv_python or _DEFAULT_VENV_PYTHON
        self.worker_script = worker_script or _DEFAULT_WORKER_SCRIPT
        self.model_name = model_name
        self.threshold = threshold

    def _check_available(self) -> bool:
        return self.venv_python.exists() and self.worker_script.exists()

    def predict_batch(self, texts: list[str]) -> list[list[dict[str, Any]]]:
        if not texts:
            return []
        if not self._check_available():
            raise RuntimeError(
                f"GLiNER .venv_ner environment not found at: {self.venv_python}. "
                "Run '.venv_ner' setup or use MockGLiNERProvider for tests."
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            in_file = tmp / "gliner_in.json"
            out_file = tmp / "gliner_out.json"

            with open(in_file, "w", encoding="utf-8") as f:
                json.dump({"texts": texts}, f)

            cmd = [
                str(self.venv_python),
                str(self.worker_script),
                "--input", str(in_file),
                "--output", str(out_file),
                "--model-name", self.model_name,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"GLiNER worker exited with code {proc.returncode}:\n{proc.stderr}"
                )
            if not out_file.exists():
                raise FileNotFoundError(f"GLiNER worker output not produced at: {out_file}")

            with open(out_file, "r", encoding="utf-8") as f:
                data = json.load(f)

        raw_results = data.get("results", [[] for _ in texts])
        filtered_results = []
        for text_item, ents in zip(texts, raw_results):
            valid_ents = []
            for ent in ents:
                lbl = ent.get("label", "")
                if lbl in ("lab_flag", "lab flag"):
                    span_text = ent.get("text", "")
                    start = ent.get("start", 0)
                    end = ent.get("end", len(span_text))
                    line_start = text_item.rfind("\n", 0, start) + 1
                    line_end = text_item.find("\n", end)
                    if line_end == -1:
                        line_end = len(text_item)
                    line_ctx = text_item[line_start:line_end]
                    if not is_valid_gliner_lab_flag(span_text, line_ctx):
                        continue
                valid_ents.append(ent)
            filtered_results.append(valid_ents)
        return filtered_results


# ─── MOCK PROVIDER (for tests, no model download) ────────────────────────────

class MockGLiNERProvider:
    """Deterministic mock GLiNER provider for unit tests.

    Uses simple regex patterns to simulate entity extraction without any
    model download or subprocess. This is NOT used in production.
    """

    # Simple extraction rules mimicking likely GLiNER outputs
    _RULES: list[tuple[str, str, re.Pattern]] = [
        ("medication", "med.name",
         re.compile(r"(?<![-/\w])([A-Z][a-z]+(?:[-/][A-Za-z]+)?(?:in|ol|am|il|ane|ide|ate|cin|mab|pril|sartan|statin)?)(?![-/\w])")),
        ("dosage", "med.dose",
         re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:mg|mcg|g|mL)\b", re.IGNORECASE)),
        ("route", "med.route",
         re.compile(r"\b(oral|PO|IV|inhalation|topical|subcutaneous|by mouth|by inhalation)\b", re.IGNORECASE)),
        ("frequency", "med.frequency",
         re.compile(r"\b(once daily|twice daily|three times daily|BID|TID|QID|daily|every \d+ hours?|as needed)\b", re.IGNORECASE)),
        ("duration", "med.duration",
         re.compile(r"\b(\d+\s*(?:days?|weeks?|months?))\b", re.IGNORECASE)),
        ("lab_value", "lab.value",
         re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:g/dL|mg/dL|mmol/L|U/L|10\*\d/\w+|%)\b", re.IGNORECASE)),
        ("lab_flag", "lab.flag",
         re.compile(r"\b(NORMAL|HIGH|LOW|CRITICAL|H|L)\b")),
    ]

    def predict_batch(self, texts: list[str]) -> list[list[dict[str, Any]]]:
        results = []
        for text in texts:
            entities = []
            for label, field_type, pattern in self._RULES:
                for m in pattern.finditer(text):
                    entities.append({
                        "label": label,
                        "text": m.group(0).strip(),
                        "start": m.start(),
                        "end": m.end(),
                        "score": 0.75,  # Fixed mock confidence
                    })
            results.append(entities)
        return results


# ─── GLINER FALLBACK EXTRACTOR ───────────────────────────────────────────────

class GLiNERFallbackExtractor:
    """GLiNER fallback for Tier 1 fields in structured regions.

    Called by ExtractionRouter when deterministic confidence < DEC-004 threshold.
    Also used for any structured region where deterministic extraction is insufficient.

    Does NOT fine-tune GLiNER. Does NOT invoke GLiNER for narrative regions (LLM handles those).
    """

    def __init__(self, provider: GLiNERProvider | None = None):
        """
        Args:
            provider: GLiNER inference provider.
                      Defaults to SubprocessGLiNERProvider (lazy, real model).
                      Use MockGLiNERProvider for tests.
        """
        # Lazy default: real provider is only instantiated if explicitly needed
        self._provider = provider
        self._initialized = False
        self.last_diagnostics: dict[str, list[str]] = {
            "unsupported_labels": [],
            "rejected_labels": [],
        }

    def _get_provider(self) -> GLiNERProvider:
        if self._provider is None:
            self._provider = SubprocessGLiNERProvider()
        return self._provider

    def extract(
        self,
        document_id: str,
        page: DocumentPage,
        region_text: str,
        region_id: str,
        char_offset: int = 0,
        diagnostics: Optional[dict] = None,
    ) -> tuple[list[Evidence], list[Claim]]:
        """Run GLiNER on a region text and produce Evidence + Claims.

        Args:
            document_id: Source document identifier.
            page: DocumentPage containing the region.
            region_text: The verbatim text of the region.
            region_id: RoutingDecision.region_id for this region.
            char_offset: Offset of region_text within the page text (for offsets).
            diagnostics: Optional dictionary to record diagnostic info into.

        Returns:
            (evidences, claims) lists.
        """
        self.last_diagnostics = {"unsupported_labels": [], "rejected_labels": []}
        if not region_text.strip():
            return [], []

        provider = self._get_provider()
        entity_lists = provider.predict_batch([region_text])
        entities = entity_lists[0] if entity_lists else []

        return self._build_claims(
            document_id, page, region_text, region_id, entities, char_offset, diagnostics=diagnostics
        )

    def extract_batch(
        self,
        document_id: str,
        page: DocumentPage,
        regions: list[tuple[str, str, int]],  # (region_text, region_id, char_offset)
        diagnostics: Optional[dict] = None,
    ) -> list[tuple[list[Evidence], list[Claim]]]:
        """Batch GLiNER inference across multiple regions from the same page.

        More efficient than calling extract() per region when many regions exist.
        """
        if not regions:
            return []

        texts = [r[0] for r in regions]
        provider = self._get_provider()
        all_entity_lists = provider.predict_batch(texts)

        results = []
        for (region_text, region_id, char_offset), entities in zip(regions, all_entity_lists):
            ev, cl = self._build_claims(
                document_id, page, region_text, region_id, entities, char_offset, diagnostics=diagnostics
            )
            results.append((ev, cl))
        return results

    def _build_claims(
        self,
        document_id: str,
        page: DocumentPage,
        region_text: str,
        region_id: str,
        entities: list[dict[str, Any]],
        char_offset: int,
        diagnostics: Optional[dict] = None,
    ) -> tuple[list[Evidence], list[Claim]]:
        """Map GLiNER entity spans to Evidence + Claim objects."""
        all_evidences: list[Evidence] = []
        all_claims: list[Claim] = []

        # Group entities by entity instance (same label → share group_id if overlapping)
        # Use a simple group_id: one per entity for now (no entity-level merging here)
        group_id = str(uuid.uuid4())  # All entities from one region share a group

        for ent in entities:
            label = ent.get("label", "")
            span_text = ent.get("text", "").strip()
            span_start = ent.get("start", 0)
            span_end = ent.get("end", 0)
            score = float(ent.get("score", 0.0))

            if not span_text:
                continue

            # Reject unsupported labels (DEC-017 boundary enforcement)
            if label in _REJECTED_LABELS:
                self.last_diagnostics.setdefault("rejected_labels", []).append(label)
                if diagnostics is not None:
                    diagnostics.setdefault("rejected_labels", []).append(label)
                continue

            # Map to canonical field_type
            field_type = _GLINER_LABEL_TO_FIELD.get(label)
            if field_type is None:
                # Label not in canonical map — record in unsupported_labels (FIX 8)
                self.last_diagnostics.setdefault("unsupported_labels", []).append(label)
                if diagnostics is not None:
                    diagnostics.setdefault("unsupported_labels", []).append(label)
                continue

            # Only accept Tier 1 fields (DEC-019: Tier 2 are strictly deterministic)
            if field_type not in TIER_1_FIELDS:
                self.last_diagnostics.setdefault("unsupported_labels", []).append(label)
                if diagnostics is not None:
                    diagnostics.setdefault("unsupported_labels", []).append(label)
                continue

            # Line context for contextual gating
            line_start = region_text.rfind("\n", 0, span_start) + 1
            line_end = region_text.find("\n", span_end)
            if line_end == -1:
                line_end = len(region_text)
            line_ctx = region_text[line_start:line_end]

            # Field-specific validation gates
            if field_type == "lab.value":
                if not is_valid_gliner_lab_value(span_text, line_ctx):
                    self.last_diagnostics.setdefault("rejected_claims", []).append({
                        "field_type": field_type,
                        "value": span_text,
                        "reason": "invalid_lab_value_or_vital_sign",
                    })
                    if diagnostics is not None:
                        diagnostics.setdefault("rejected_claims", []).append({
                            "field_type": field_type,
                            "value": span_text,
                            "reason": "invalid_lab_value_or_vital_sign",
                        })
                    continue
            elif field_type == "med.dose":
                if not is_valid_gliner_med_dose(span_text, line_ctx):
                    self.last_diagnostics.setdefault("rejected_claims", []).append({
                        "field_type": field_type,
                        "value": span_text,
                        "reason": "invalid_med_dose_or_quantity",
                    })
                    if diagnostics is not None:
                        diagnostics.setdefault("rejected_claims", []).append({
                            "field_type": field_type,
                            "value": span_text,
                            "reason": "invalid_med_dose_or_quantity",
                        })
                    continue
            elif field_type == "lab.flag":
                if not is_valid_gliner_lab_flag(span_text, line_ctx):
                    self.last_diagnostics.setdefault("rejected_claims", []).append({
                        "field_type": field_type,
                        "value": span_text,
                        "reason": "invalid_lab_flag_or_metadata",
                    })
                    if diagnostics is not None:
                        diagnostics.setdefault("rejected_claims", []).append({
                            "field_type": field_type,
                            "value": span_text,
                            "reason": "invalid_lab_flag_or_metadata",
                        })
                    continue

            # Compute page-local character offsets
            page_char_start = char_offset + span_start
            page_char_end = char_offset + span_end

            # Get bbox from page geometry
            bbox_list = page.get_bbox_for_span(page_char_start, page_char_end)
            if bbox_list is None:
                bbox_model = BBoxModel(x0=0.0, y0=0.0, x1=0.0, y1=0.0)
            else:
                x0, y0, x1, y1 = bbox_list
                x0, x1 = min(x0, x1), max(x0, x1)
                y0, y1 = min(y0, y1), max(y0, y1)
                bbox_model = BBoxModel(x0=x0, y0=y0, x1=x1, y1=y1)

            ocr_conf = page.get_ocr_confidence_for_span(page_char_start, page_char_end)

            evidence = Evidence(
                document_id=document_id,
                page_number=page.page_number,
                text=span_text,
                char_start=page_char_start,
                char_end=page_char_end,
                bbox=bbox_model,
                ocr_confidence=ocr_conf,
            )
            all_evidences.append(evidence)

            # Parse numeric value for numeric fields
            value_numeric: Optional[float] = None
            unit_raw: Optional[str] = None
            if field_type in ("lab.value", "med.dose"):
                m = re.search(r"(-?\d+(?:\.\d+)?)", span_text)
                if m:
                    try:
                        value_numeric = float(m.group(1))
                    except ValueError:
                        pass
                # Extract unit_raw for dose
                if field_type == "med.dose":
                    unit_m = re.search(
                        r"\b(mg|mcg|g|mL|units?|puffs?|tablets?|capsules?|tabs?|caps?|drops?|patches?|sprays?|mEq)\b",
                        span_text,
                        re.IGNORECASE,
                    )
                    if unit_m:
                        unit_raw = unit_m.group(1).lower()

            claim = Claim(
                document_id=document_id,
                group_id=group_id,
                field_type=field_type,
                value=span_text,
                value_numeric=value_numeric,
                unit_raw=unit_raw,
                extraction_method="gliner",
                extraction_confidence=score,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            )
            all_claims.append(claim)

        return all_evidences, all_claims
