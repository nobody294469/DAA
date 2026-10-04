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
        model_name: Optional[str] = None,  # None -> worker default (local copy, else hub id)
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
            ] + (["--model-name", self.model_name] if self.model_name else [])
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                raise RuntimeError(
                    f"GLiNER worker exited with code {proc.returncode}:\n{proc.stderr}"
                )
            if not out_file.exists():
                raise FileNotFoundError(f"GLiNER worker output not produced at: {out_file}")

            with open(out_file, "r", encoding="utf-8") as f:
                data = json.load(f)

        return data.get("results", [[] for _ in texts])


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
                    unit_m = re.search(r"\b(mg|mcg|g|mL|units?|puffs?)\b", span_text, re.IGNORECASE)
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
