"""Narrative LLM extraction for prose clinical documents.

Authority: DEC-006, DEC-013, AUDIT-20, PHASE0_ARCHITECTURE_RESOLUTION §7
Scope: Extract ONLY the canonical Tier 1 fields from narrative clinical prose:
  - lab.value, lab.flag
  - med.name, med.dose, med.route, med.frequency

The extractor is NOT a general medical NLP tool. It is strictly bounded to the
canonical 10-field vocabulary (DEC-017). Fields outside this set are rejected at
the schema level (DEC-013 grammar-constrained output enforcement).

Design requirements (DEC-013):
  - LLM is isolated behind a provider/model abstraction.
  - Primary path: grammar-constrained JSON schema decoding via vLLM.
  - Fallback: prompt + JSON parse + bounded retry (max 2, DEC-013).
  - Mock path for unit tests (no external API or model download).
  - Output validated against canonical schema before emitting claims.
  - Evidence spans must be verbatim substrings of the source text (§7.4).
  - Never fabricate evidence spans not present in source text.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Optional, Protocol

from clindoc.extract.contracts import (
    BBoxModel, Claim, Evidence, TIER_1_FIELDS,
)
from clindoc.ocr.document_text import DocumentPage


# ─── PERMITTED NARRATIVE FIELDS (DEC-020 / AUDIT-20 scope boundary) ──────────
# The LLM extractor is ONLY permitted to produce these field types.
# Any other field_type in LLM output is rejected.
PERMITTED_NARRATIVE_FIELDS: frozenset[str] = frozenset({
    "lab.value",
    "lab.flag",
    "med.name",
    "med.dose",
    "med.route",
    "med.frequency",
})


# ─── LLM OUTPUT SCHEMA ───────────────────────────────────────────────────────
# This is the schema passed to grammar-constrained decoding (DEC-013).
# JSON Schema that constrains field_type to permitted_narrative_fields.
ENTITY_LINK_FIELDS: frozenset[str] = frozenset({"lab.test_name"})  # accepted only with a provider `group`

LLM_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["extractions"],
    "properties": {
        "extractions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field_type", "value", "evidence_text"],
                "properties": {
                    "field_type": {
                        "type": "string",
                        "enum": sorted(PERMITTED_NARRATIVE_FIELDS),
                    },
                    "value": {"type": "string"},
                    "evidence_text": {
                        "type": "string",
                        "description": "Verbatim substring of the source text",
                    },
                    "unit_raw": {"type": ["string", "null"]},
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}


# ─── SYSTEM PROMPT ───────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are a clinical document extraction assistant.
Your task is to extract ONLY medication regimen and laboratory result information from clinical text.
You MUST extract ONLY these field types:
  - lab.value    : numeric laboratory test result value
  - lab.flag     : interpretation flag (NORMAL, HIGH, LOW, CRITICAL only)
  - med.name     : medication active ingredient or brand name
  - med.dose     : numeric dose quantity (e.g. 10, 500, 2.5)
  - med.route    : route of administration (oral, intravenous, inhalation, topical, etc.)
  - med.frequency: dosing frequency (once daily, twice daily, BID, every 8 hours, etc.)

CRITICAL RULES:
1. Do NOT extract diagnoses, conditions, procedures, allergies, vital signs, or social/family history.
2. The evidence_text MUST be an exact verbatim substring of the input text.
3. If a field is not clearly stated in the text, do NOT include it.
4. Respond ONLY with valid JSON conforming to the schema. No commentary.

Output schema:
{"extractions": [{"field_type": "...", "value": "...", "evidence_text": "...", "unit_raw": null, "confidence": 0.9}]}
"""


def _build_user_prompt(text: str) -> str:
    return f"Extract clinical information from the following clinical text:\n\n{text}\n\nOutput JSON:"


# ─── PROVIDER PROTOCOL ───────────────────────────────────────────────────────

class LLMProvider(Protocol):
    """Protocol for LLM inference providers.

    Implementations:
      - VLLMProvider: Grammar-constrained local vLLM serving
      - MockLLMProvider: Deterministic mock for unit tests
    """

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Run a completion and return the raw response text."""
        ...


# ─── VLLM PROVIDER ───────────────────────────────────────────────────────────

class VLLMProvider:
    """Grammar-constrained vLLM provider for production use.

    Requires a running vLLM server (DEC-006: Colab T4 environment).
    NOT used on the local RTX 3050 during tests.
    """

    def __init__(
        self,
        model_name: str = "Qwen/Qwen2.5-7B-Instruct",
        base_url: str = "http://localhost:8000/v1",
        api_key_env: str = "VLLM_API_KEY",
        temperature: float = 0.0,
        timeout: int = 60,
    ):
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = os.environ.get(api_key_env, "EMPTY")
        self.temperature = temperature
        self.timeout = timeout

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Call vLLM OpenAI-compatible API with grammar-constrained JSON output."""
        try:
            import httpx
        except ImportError:
            raise RuntimeError(
                "httpx is required for VLLMProvider. Install with: pip install httpx"
            )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "narrative_extraction",
                    "schema": LLM_OUTPUT_SCHEMA,
                    "strict": True,
                },
            },
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        return data["choices"][0]["message"]["content"]


# ─── MOCK PROVIDER ───────────────────────────────────────────────────────────

class MockLLMProvider:
    """Deterministic mock LLM provider for unit tests.

    Parses simple patterns from text to simulate LLM extractions.
    Returns well-formed JSON conforming to LLM_OUTPUT_SCHEMA.
    """

    def __init__(self, canned_responses: list[dict] | None = None):
        """
        Args:
            canned_responses: Optional list of extraction dicts to return.
                              If None, uses regex-based heuristic extraction.
        """
        self._canned = canned_responses

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if self._canned is not None:
            return json.dumps({"extractions": self._canned})

        # Heuristic extraction from user_prompt text
        # Extract the text portion after the prompt prefix
        text_match = re.search(
            r"Extract clinical information from the following clinical text:\s*(.+?)Output JSON:",
            user_prompt,
            re.DOTALL,
        )
        text = text_match.group(1).strip() if text_match else user_prompt

        extractions = self._extract_heuristic(text)
        return json.dumps({"extractions": extractions})

    def _extract_heuristic(self, text: str) -> list[dict]:
        """Simple heuristic extraction mimicking LLM behavior for test purposes."""
        extractions = []

        # med.name
        med_m = re.search(
            r"(?<![-/\w])(Amoxicillin[-/]clavulanate|Atorvastatin|Metformin|Lisinopril|Amoxicillin|Aspirin|Metoprolol|"
            r"Omeprazole|Ciprofloxacin|Losartan|Levothyroxine|Warfarin|"
            r"[A-Z][a-z]+(?:[-/][a-zA-Z]+)?)(?![-/\w])",
            text,
            re.IGNORECASE,
        )
        if med_m:
            extractions.append({
                "field_type": "med.name",
                "value": med_m.group(0),
                "evidence_text": med_m.group(0),
                "unit_raw": None,
                "confidence": 0.82,
            })

        # med.dose
        dose_m = re.search(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|mL|g)\b", text, re.IGNORECASE)
        if dose_m:
            extractions.append({
                "field_type": "med.dose",
                "value": dose_m.group(1),
                "evidence_text": dose_m.group(0),
                "unit_raw": dose_m.group(2).lower(),
                "confidence": 0.85,
            })

        # med.route
        route_m = re.search(r"\b(oral|PO|by mouth|inhalation|topical|subcutaneous)\b", text, re.IGNORECASE)
        if route_m:
            extractions.append({
                "field_type": "med.route",
                "value": route_m.group(0).lower(),
                "evidence_text": route_m.group(0),
                "unit_raw": None,
                "confidence": 0.88,
            })

        # med.frequency
        freq_m = re.search(
            r"\b(once daily|twice daily|three times daily|BID|TID|QID|every \d+ hours?|daily|as needed)\b",
            text, re.IGNORECASE,
        )
        if freq_m:
            extractions.append({
                "field_type": "med.frequency",
                "value": freq_m.group(0).lower(),
                "evidence_text": freq_m.group(0),
                "unit_raw": None,
                "confidence": 0.86,
            })

        # lab.value
        lab_m = re.search(
            r"\b(\d+(?:\.\d+)?)\s*(g/dL|mg/dL|mmol/L|U/L|10\*\d+/\w+|%)\b",
            text, re.IGNORECASE,
        )
        if lab_m:
            extractions.append({
                "field_type": "lab.value",
                "value": lab_m.group(1),
                "evidence_text": lab_m.group(0),
                "unit_raw": lab_m.group(2),
                "confidence": 0.83,
            })

        # lab.flag
        flag_m = re.search(r"\b(HIGH|LOW|NORMAL|CRITICAL)\b", text)
        if flag_m:
            extractions.append({
                "field_type": "lab.flag",
                "value": flag_m.group(0),
                "evidence_text": flag_m.group(0),
                "unit_raw": None,
                "confidence": 0.90,
            })

        return extractions


# ─── NARRATIVE EXTRACTOR ──────────────────────────────────────────────────────

class NarrativeExtractor:
    """Schema-constrained LLM narrative extractor (DEC-006, DEC-013).

    Targets only the permitted_narrative_fields from clinical prose.
    Enforces verbatim evidence-substring validity (§7.4).
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        max_retries: int = 2,
    ):
        """
        Args:
            provider: LLM inference provider.
                      Defaults to MockLLMProvider (safe for tests).
                      Use VLLMProvider for production.
            max_retries: Bounded retry limit (DEC-013: max 2).
        """
        self._provider = provider or MockLLMProvider()
        self.max_retries = min(max_retries, 2)  # DEC-013: hard limit

    def extract(
        self,
        document_id: str,
        page: DocumentPage,
        region_text: str,
        region_id: str,
        char_offset: int = 0,
        prompt_text: str | None = None,
    ) -> tuple[list[Evidence], list[Claim], dict]:
        """Extract Tier 1 fields from narrative prose using the LLM.

        prompt_text: what the provider reads (e.g. table rows rebuilt from word positions); every span it returns
        is still validated as a verbatim substring of region_text.

        Returns:
            (evidences, claims, diagnostics)
            diagnostics: retry count, validation errors, rejected extractions
        """
        diagnostics: dict = {
            "retries": 0,
            "validation_errors": [],
            "rejected": [],
        }

        if not region_text.strip():
            return [], [], diagnostics

        user_prompt = _build_user_prompt(prompt_text or region_text)
        raw_response: str | None = None
        parsed: dict | None = None
        last_error: str = ""

        # Bounded retry loop (DEC-013: max 2 retries)
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                diagnostics["retries"] = attempt

            try:
                raw_response = self._provider.complete(_SYSTEM_PROMPT, user_prompt)
                parsed = self._parse_response(raw_response)
                break
            except json.JSONDecodeError as e:
                last_error = f"JSONDecodeError on attempt {attempt}: {e}"
                diagnostics["validation_errors"].append(last_error)
                parsed = None
            except Exception as e:
                last_error = f"LLM error on attempt {attempt}: {e}"
                diagnostics["validation_errors"].append(last_error)
                parsed = None

        if parsed is None:
            diagnostics["validation_errors"].append(f"All {self.max_retries + 1} attempts failed: {last_error}")
            return [], [], diagnostics

        # Validate and build Claim + Evidence
        evidences, claims = self._build_claims(
            document_id, page, region_text, region_id,
            parsed.get("extractions", []), char_offset, diagnostics,
        )
        return evidences, claims, diagnostics

    def _parse_response(self, raw: str) -> dict:
        """Parse and schema-validate the LLM JSON response."""
        # Strip markdown code blocks if present
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)

        if "extractions" not in data:
            raise json.JSONDecodeError("Missing 'extractions' key", raw, 0)
        if not isinstance(data["extractions"], list):
            raise json.JSONDecodeError("'extractions' must be a list", raw, 0)

        return data

    def _build_claims(
        self,
        document_id: str,
        page: DocumentPage,
        region_text: str,
        region_id: str,
        extractions: list[dict],
        char_offset: int,
        diagnostics: dict,
    ) -> tuple[list[Evidence], list[Claim]]:
        """Validate each extraction and build canonical Evidence + Claim objects."""
        all_evidences: list[Evidence] = []
        all_claims: list[Claim] = []
        group_id = str(uuid.uuid4())
        groups: dict[str, str] = {}  # optional per-entity `group` key from the provider (DEC-015)

        for ext in extractions:
            field_type = ext.get("field_type", "")
            value = ext.get("value", "")
            evidence_text = ext.get("evidence_text", "")
            unit_raw = ext.get("unit_raw")
            confidence = float(ext.get("confidence", 0.80))

            # REJECT: unsupported field_type (DEC-017 / AUDIT-20)
            # `group` marks a provider-linked entity (ClinicalTextProvider): the test name that lab values in
            # prose are verified against (DEC-015). Tier-2 stays out of the LLM-permitted set (AUDIT-20 FIX 3).
            entity_link = field_type in ENTITY_LINK_FIELDS and bool(ext.get("group"))
            if field_type not in PERMITTED_NARRATIVE_FIELDS and not entity_link:
                diagnostics["rejected"].append({
                    "field_type": field_type,
                    "reason": "field_type not in PERMITTED_NARRATIVE_FIELDS",
                })
                continue

            # REJECT: empty value
            if not value.strip():
                diagnostics["rejected"].append({
                    "field_type": field_type,
                    "reason": "empty value",
                })
                continue

            # Spans found in the row-rebuilt prompt text (table cells on one line) match the page text across its
            # line breaks; the evidence kept is the page's own text, so it stays verbatim (§7.4).
            if evidence_text and evidence_text not in region_text and " " in evidence_text:
                ws = re.search(r"\s+".join(map(re.escape, evidence_text.split())), region_text)
                if ws:
                    evidence_text = ws.group(0)

            # REJECT: evidence_text is not a verbatim substring (§7.4)
            if not evidence_text or evidence_text not in region_text:
                diagnostics["rejected"].append({
                    "field_type": field_type,
                    "value": value,
                    "evidence_text": evidence_text,
                    "reason": "evidence_text is not a verbatim substring of region_text",
                })
                continue

            # Locate evidence span within region text (FIX 6: never search entire page from zero)
            local_pos = region_text.find(evidence_text)
            if local_pos >= 0:
                char_start = char_offset + local_pos
                char_end = char_start + len(evidence_text)
            else:
                char_start = char_offset
                char_end = char_start + len(evidence_text)

            # Build bbox from page geometry
            bbox_list = page.get_bbox_for_span(char_start, char_end)
            if bbox_list is None:
                bbox_model = BBoxModel(x0=0.0, y0=0.0, x1=0.0, y1=0.0)
            else:
                x0, y0, x1, y1 = bbox_list
                x0, x1 = min(x0, x1), max(x0, x1)
                y0, y1 = min(y0, y1), max(y0, y1)
                bbox_model = BBoxModel(x0=x0, y0=y0, x1=x1, y1=y1)

            ocr_conf = page.get_ocr_confidence_for_span(char_start, char_end)

            evidence = Evidence(
                document_id=document_id,
                page_number=page.page_number,
                text=evidence_text,
                char_start=char_start,
                char_end=char_end,
                bbox=bbox_model,
                ocr_confidence=ocr_conf,
            )
            all_evidences.append(evidence)

            # Parse numeric value for numeric fields
            value_numeric: Optional[float] = None
            if field_type in ("lab.value", "med.dose"):
                num_m = re.search(r"(-?\d+(?:\.\d+)?)", value)
                if num_m:
                    try:
                        value_numeric = float(num_m.group(1))
                    except ValueError:
                        pass

            claim = Claim(
                document_id=document_id,
                group_id=groups.setdefault(ext["group"], str(uuid.uuid4())) if ext.get("group") else group_id,
                field_type=field_type,  # type: ignore[arg-type]
                value=value.strip(),
                value_numeric=value_numeric,
                unit_raw=unit_raw,
                extraction_method="llm_narrative",
                extraction_confidence=confidence,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
            )
            all_claims.append(claim)

        return all_evidences, all_claims
