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
from clindoc.extract.segmentation import segment_clinical_text, find_segment_idx
from clindoc.extract.medication_association import (
    associate_segment_medications,
    AssociationStatus,
    NO_ANCHOR_SENTINEL,
)
from clindoc.ocr.document_text import DocumentPage
from clindoc.config import (
    get_circuit_breaker_failure_threshold,
    get_circuit_breaker_cooldown_seconds,
    get_model_concurrency_limit,
    get_backoff_base_delay,
    get_backoff_max_delay,
)
from clindoc.services.circuit_breaker import (
    CircuitBreaker,
    ConcurrencyLimiter,
    ExponentialBackoff,
    get_shared_circuit_breaker,
    get_shared_concurrency_limiter,
    is_retryable_error,
)


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
  - lab.value    : quantitative numeric laboratory test result value (e.g. 13.5, 95) OR grounded qualitative laboratory determination (e.g. Negative, Positive, Reactive, Non-Reactive, Detected, Not Detected, Equivocal)
  - lab.flag     : interpretation or severity flag ONLY when explicitly stated in the source text (NORMAL, HIGH, LOW, CRITICAL only)
  - med.name     : medication active ingredient or brand name
  - med.dose     : numeric dose quantity ONLY (e.g. 10, 500, 2.5, 0.5, 6.25, 1)
  - med.route    : anatomical route of administration (e.g. oral, by mouth, PO, intravenous, IV, subcutaneous, SC, inhalation, topical)
  - med.frequency: dosing frequency or administration schedule (e.g. once daily, twice daily, BID, TID, every 8 hours, every 3 weeks, PRN, as needed, as and when needed)

CRITICAL EXTRACTION RULES:
1. DOSE VS UNIT SEPARATION & DURATION BOUNDARY:
   - med.dose contains ONLY the physical numeric quantity per administration (e.g. "0.5", "6.25", "10", "1", "500").
   - unit_raw contains the unit of measure (e.g. "ml", "mg", "g", "mcg", "units").
   - NEVER put the unit inside med.dose.
   - NEVER use evidence_text, field names, or surrounding prose as the dose value.
   - DOSE VS TREATMENT DURATION: Course durations or treatment periods (e.g. "6 weeks", "5 days", "3 months", "for 10 days", "for 30 days") represent the total treatment duration, NOT the dose and NOT the frequency. NEVER extract treatment durations or time periods into med.dose or med.frequency.
   - Physical medication doses (e.g. "500 mg", "1 g", "5 mL", "10 units") must remain med.dose.
2. FREQUENCY VS ROUTE & DOSAGE FORM BOUNDARY:
   - med.frequency represents WHEN or HOW OFTEN the drug is taken (e.g. "once daily", "twice daily", "three times daily", "every 8 hours", "every 3 weeks", "PRN", "as needed", "as and when needed"). NEVER classify frequency or timing as med.route.
   - med.route represents WHERE or HOW the drug enters the body (e.g. "oral", "by mouth", "PO", "intravenous", "IV", "subcutaneous", "SC").
   - VERBATIM ROUTE RULE: Extract the route wording EXACTLY as written in the source text (e.g. if the text says "by mouth", extract value "by mouth", do NOT replace with "oral"; if the text says "PO", extract value "PO"). Canonical route normalization occurs downstream.
   - DOSAGE FORM SEPARATION: Dosage forms such as "tablet", "tab", "capsule", "cap", "syrup", "injection" are DOSAGE FORMS, NOT routes, NOT frequencies, and NOT doses. NEVER extract dosage forms into med.route, med.frequency, or med.dose.
3. MEDICATION NAMES & DOSAGE FORMS:
   - Strip standalone dosage-form prefixes such as "TAB", "SYP", "CAP", "INJ", "Tab" from med.name when separated by whitespace (e.g. "TAB ALDACTONE" -> value: "Aldactone", "SYP FUROPED" -> value: "Furoped").
   - Preserve source-faithful tokens for abbreviated compound medications (e.g. "C.Autrin" -> value: "C.Autrin", "T.Shelcal" -> value: "T.Shelcal"). Do not strip prefixes when part of the source token.
   - Do NOT include dosage forms, route, or frequency in med.name.
4. COMPOUND PRESCRIPTIONS & INLINE ORDERS:
   - A single prescription line or sentence may contain multiple medication entities.
   - When genuinely separate medications are listed together in a slash-delimited or co-prescribed order (e.g. "C.Autrin / T.Shelcal once daily for 6 weeks"):
     a) Extract EACH distinct medication as its own med.name claim (e.g. "C.Autrin", "T.Shelcal").
     b) Propagate the shared dosing frequency to EACH co-prescribed medication (e.g. "once daily" for each).
     c) Do NOT extract the duration ("6 weeks") as a dose or frequency.
   - In inline medication sentences (e.g. "Tab Paracetamol 500mg as and when needed", "Tab Metacin 1g as and when needed"):
     ALWAYS extract the medication active name as med.name (e.g. "Paracetamol", "Metacin"); do NOT omit the medication name merely because dose, frequency, or indication follow it.
5. DO NOT SPLIT COMBINATION DRUGS OR CLINICAL SLASH TERMS:
   - Genuine single combination medications containing slashes or hyphens (e.g. "Amoxicillin/clavulanate", "Trimethoprim/sulfamethoxazole", "Sulfamethoxazole/trimethoprim") are single drug entities: extract the full combination name as a single med.name; do NOT split them into separate drugs.
   - Clinical slash terms, ratios, or lab tests (e.g. "PT/INR", "HIV/HBsAg", "BP: 120/80") are NOT medication co-prescriptions. Do NOT treat them as medications.
6. LABORATORY FINDINGS (QUANTITATIVE VS QUALITATIVE):
   - lab.value has dual semantics:
     a) Quantitative numeric findings: extract the numeric quantity into value (e.g. "14.2") and the unit into unit_raw (e.g. "g/dL", "mg/dL").
     b) Grounded qualitative determinations: extract the qualitative determination into value (e.g. "Negative", "Positive", "Reactive", "Non-Reactive", "Detected", "Not Detected", "Equivocal"). unit_raw MUST be null.
   - lab.value VS lab.flag DISTINCTION:
     - lab.value represents the actual test determination (e.g. "Negative", "Non-Reactive", "14.2").
     - lab.flag represents an explicit interpretation/severity rating (e.g. "NORMAL", "HIGH", "LOW", "CRITICAL").
     - Do NOT automatically infer or synthesize lab.flag="NORMAL" merely because lab.value is "Negative" or "Non-Reactive".
     - lab.value and lab.flag may coexist ONLY when independently and explicitly grounded in the text.
   - ARBITRARY COMMENTARY IS NOT A QUALITATIVE RESULT:
     - Specimen commentary, descriptive remarks, or processing notes (e.g. "specimen slightly hemolyzed", "clotted sample", "repeat requested") are NOT qualitative laboratory determinations. Do NOT extract them as lab.value.
7. NEGATIVE EXTRACTION RULES (STRICTLY FORBIDDEN):
   - Do NOT extract document titles, headers, patient identifiers, or hospital departments (e.g. "DISCHARGE SUMMARY", "UHID: 12345").
   - Do NOT extract diagnoses, conditions, surgical procedures, or clinical notes (e.g. "DIAGNOSIS: TETRALOGY OF FALLOT", "PAEDIATRIC CARDIOTHORACIC SURGERY"). These must yield ZERO extractions.
   - Do NOT extract discharge disposition or general adjectives (e.g. "Discharge Type: NORMAL") as lab.flag.
8. VERBATIM EVIDENCE GROUNDING:
   - The evidence_text MUST be an exact verbatim substring copied from the input text. Do not normalize, invent, or alter evidence_text.
9. IF A FIELD IS NOT EXPLICITLY STATED in the text, do NOT include it.
10. RESPOND ONLY WITH VALID JSON conforming to the schema. No conversational commentary.

FEW-SHOT EXAMPLES:

Example 1 — Liquid suspension / pediatric syrup:
Input: "SYP FUROPED 0.5ML TWO TIMES IN A DAY TILL NEXT ORDER"
Output:
{
  "extractions": [
    {"field_type": "med.name", "value": "Furoped", "evidence_text": "SYP FUROPED", "unit_raw": null, "confidence": 0.95},
    {"field_type": "med.dose", "value": "0.5", "evidence_text": "0.5ML", "unit_raw": "ml", "confidence": 0.92},
    {"field_type": "med.frequency", "value": "TWO TIMES IN A DAY", "evidence_text": "TWO TIMES IN A DAY", "unit_raw": null, "confidence": 0.94}
  ]
}

Example 2 — Dot-leader formatting and tablet regimen:
Input: "TAB ALDACTONE................6.25MG...................THREE TIMES IN A DAY"
Output:
{
  "extractions": [
    {"field_type": "med.name", "value": "Aldactone", "evidence_text": "TAB ALDACTONE", "unit_raw": null, "confidence": 0.95},
    {"field_type": "med.dose", "value": "6.25", "evidence_text": "6.25MG", "unit_raw": "mg", "confidence": 0.93},
    {"field_type": "med.frequency", "value": "THREE TIMES IN A DAY", "evidence_text": "THREE TIMES IN A DAY", "unit_raw": null, "confidence": 0.94}
  ]
}

Example 3 — Compound co-prescription with shared schedule and course duration (duration excluded from dose/frequency, source tokens preserved):
Input: "C.Autrin / T.Shelcal once daily for 6 weeks"
Output:
{
  "extractions": [
    {"field_type": "med.name", "value": "C.Autrin", "evidence_text": "C.Autrin", "unit_raw": null, "confidence": 0.95},
    {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily", "unit_raw": null, "confidence": 0.95},
    {"field_type": "med.name", "value": "T.Shelcal", "evidence_text": "T.Shelcal", "unit_raw": null, "confidence": 0.95},
    {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily", "unit_raw": null, "confidence": 0.95}
  ]
}

Example 4 — Inline PRN prescription with dosage form prefix:
Input: "Tab Metacin 1g as and when needed"
Output:
{
  "extractions": [
    {"field_type": "med.name", "value": "Metacin", "evidence_text": "Tab Metacin", "unit_raw": null, "confidence": 0.95},
    {"field_type": "med.dose", "value": "1", "evidence_text": "1g", "unit_raw": "g", "confidence": 0.95},
    {"field_type": "med.frequency", "value": "as and when needed", "evidence_text": "as and when needed", "unit_raw": null, "confidence": 0.95}
  ]
}

Example 5 — Full regimen with verbatim route, physical dose, and course duration (duration excluded):
Input: "Metformin 500 mg by mouth twice daily for 10 days"
Output:
{
  "extractions": [
    {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin", "unit_raw": null, "confidence": 0.95},
    {"field_type": "med.dose", "value": "500", "evidence_text": "500 mg", "unit_raw": "mg", "confidence": 0.95},
    {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth", "unit_raw": null, "confidence": 0.95},
    {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily", "unit_raw": null, "confidence": 0.95}
  ]
}

Example 6 — Combination medication (negative split rule):
Input: "Amoxicillin/clavulanate 625 mg oral twice daily for 5 days"
Output:
{
  "extractions": [
    {"field_type": "med.name", "value": "Amoxicillin/clavulanate", "evidence_text": "Amoxicillin/clavulanate", "unit_raw": null, "confidence": 0.95},
    {"field_type": "med.dose", "value": "625", "evidence_text": "625 mg", "unit_raw": "mg", "confidence": 0.95},
    {"field_type": "med.route", "value": "oral", "evidence_text": "oral", "unit_raw": null, "confidence": 0.95},
    {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily", "unit_raw": null, "confidence": 0.95}
  ]
}

Example 7 — Non-medication clinical headers, diagnoses, and lab slash ratios (NEGATIVE EXAMPLES):
Input: "DISCHARGE SUMMARY\\nUHID: 12345\\nDIAGNOSIS: TETRALOGY OF FALLOT\\nPT/INR: 12.1secs\\nPAEDIATRIC CARDIOTHORACIC SURGERY"
Output:
{
  "extractions": []
}

Example 8 — Quantitative numeric laboratory finding:
Input: "Haemoglobin: 14.2 g/dL (ref 12.0 - 16.0)"
Output:
{
  "extractions": [
    {"field_type": "lab.value", "value": "14.2", "evidence_text": "14.2 g/dL", "unit_raw": "g/dL", "confidence": 0.95}
  ]
}

Example 9 — Grounded qualitative laboratory determinations (Negative, Non-Reactive):
Input: "HBsAg: Negative\\nHIV: Negative\\nVDRL: Non-Reactive"
Output:
{
  "extractions": [
    {"field_type": "lab.value", "value": "Negative", "evidence_text": "Negative", "unit_raw": null, "confidence": 0.95},
    {"field_type": "lab.value", "value": "Negative", "evidence_text": "Negative", "unit_raw": null, "confidence": 0.95},
    {"field_type": "lab.value", "value": "Non-Reactive", "evidence_text": "Non-Reactive", "unit_raw": null, "confidence": 0.95}
  ]
}

Example 10 — Specimen commentary and non-qualitative laboratory notes (NEGATIVE EXAMPLE):
Input: "Biochemistry: Specimen slightly hemolyzed. Clotted sample received."
Output:
{
  "extractions": []
}

Output schema format:
{"extractions": [{"field_type": "...", "value": "...", "evidence_text": "...", "unit_raw": null, "confidence": 0.85}]}
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
        circuit_breaker: Optional[CircuitBreaker] = None,
        concurrency_limiter: Optional[ConcurrencyLimiter] = None,
        http_client: Optional[Any] = None,
    ):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = os.environ.get(api_key_env, "EMPTY")
        self.temperature = temperature
        self.timeout = timeout
        self.http_client = http_client
        self.circuit_breaker = circuit_breaker or get_shared_circuit_breaker(
            self.base_url,
            failure_threshold=get_circuit_breaker_failure_threshold(),
            cooldown_seconds=get_circuit_breaker_cooldown_seconds(),
        )
        self.concurrency_limiter = concurrency_limiter or get_shared_concurrency_limiter(
            self.base_url,
            max_concurrency=get_model_concurrency_limit(),
            timeout=float(self.timeout),
        )

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

        with self.concurrency_limiter:
            with self.circuit_breaker:
                if self.http_client is not None:
                    resp = self.http_client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]

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
                "value": route_m.group(0),
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

        # lab.value (quantitative and qualitative per PD-022)
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
        else:
            qual_m = re.search(
                r"\b(Negative|Positive|Reactive|Non-Reactive|Non Reactive|Nonreactive|Detected|Not Detected|Equivocal)\b",
                text, re.IGNORECASE,
            )
            if qual_m:
                extractions.append({
                    "field_type": "lab.value",
                    "value": qual_m.group(0),
                    "evidence_text": qual_m.group(0),
                    "unit_raw": None,
                    "confidence": 0.90,
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
        backoff: Optional[ExponentialBackoff] = None,
    ):
        """
        Args:
            provider: LLM inference provider.
                      Defaults to MockLLMProvider (safe for tests).
                      Use VLLMProvider for production.
            max_retries: Bounded retry limit (DEC-013: max 2).
            backoff: Exponential backoff with jitter instance.
        """
        self._provider = provider or MockLLMProvider()
        self.max_retries = min(max_retries, 2)  # DEC-013: hard limit
        self._backoff = backoff or ExponentialBackoff(
            base_delay=get_backoff_base_delay(),
            max_delay=get_backoff_max_delay(),
        )

    def extract(
        self,
        document_id: str,
        page: DocumentPage,
        region_text: str,
        region_id: str,
        char_offset: int = 0,
    ) -> tuple[list[Evidence], list[Claim], dict]:
        """Extract Tier 1 fields from narrative prose using the LLM.

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

        user_prompt = _build_user_prompt(region_text)
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

                cb = getattr(self._provider, "circuit_breaker", None)
                if cb is not None:
                    cb.record_failure(e)

                if attempt < self.max_retries:
                    self._backoff.sleep(attempt + 1)
            except Exception as e:
                last_error = f"LLM error on attempt {attempt}: {e}"
                diagnostics["validation_errors"].append(last_error)
                parsed = None

                cb = getattr(self._provider, "circuit_breaker", None)
                if cb is not None:
                    cb.record_failure(e)

                if not is_retryable_error(e):
                    break

                if attempt < self.max_retries:
                    self._backoff.sleep(attempt + 1)

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
        """Validate each extraction, deterministically ground evidence spans, and build canonical Claims.

        Phase 5.2.3 Milestone 1 Architecture:
          1. Pre-validation:
             - Check permitted narrative fields (DEC-020 / AUDIT-20)
             - Check non-empty value and evidence_text
          2. Explicit vs Searched Offset Grounding:
             - If candidate specifies source offsets (char_start/char_end or local_start/local_end):
               validate integer types, region_text bounds, and verbatim substring match.
               Fail closed on invalid offset bounds or text mismatch.
             - If candidate offsets are missing:
               search region_text for all verbatim occurrences (exact substring or whitespace-normalized tokens).
               - 0 occurrences: fail closed (unmatched evidence text).
               - 1 occurrence: unique grounding.
               - >1 occurrences: fail closed if ambiguous; only map if candidate extractions with this evidence_text
                 have identical field values and exact 1:1 match to occurrences (preventing silent first-occurrence selection).
          3. Deterministic Sorting (Invariance to LLM JSON order):
             Sort grounded extractions by:
               (local_start, local_end, field_anchor_priority, field_type, value, unit_raw, -confidence)
             Ties at identical offsets place anchors (med.name, lab.value) first.
          4. State-Machine Group ID Assignment:
             Iterate through deterministically ordered extractions:
             - Each med.name generates a fresh UUID group_id.
             - Subsequent med attributes inherit current_med_group.
             - Each lab.value generates a fresh UUID group_id.
             - Subsequent lab attributes inherit current_lab_group.
             - Attributes appearing before any anchor receive '__no_anchor__' sentinel.
          5. Canonical Evidence & Claim Generation:
             Preserves exact character offsets and source span text.
        """
        all_evidences: list[Evidence] = []
        all_claims: list[Claim] = []

        def _find_occurrences(ev_text: str) -> list[tuple[int, int, str]]:
            """Find all matching spans (local_start, local_end, matched_text) in region_text."""
            if not ev_text or not ev_text.strip():
                return []

            prefix = r"(?<!\w)" if re.match(r"^\w", ev_text) else ""
            suffix = r"(?!\w)" if re.search(r"\w$", ev_text) else ""

            # 1. Word-boundary exact literal matches
            pattern_exact_wb = prefix + re.escape(ev_text) + suffix
            exact_wb_matches = [
                (m.start(), m.end(), region_text[m.start():m.end()])
                for m in re.finditer(pattern_exact_wb, region_text)
            ]
            if exact_wb_matches:
                return exact_wb_matches

            # 2. Word-boundary whitespace-normalized token sequence matches (Root Cause A)
            tokens = ev_text.split()
            if tokens:
                pattern_ws_wb = prefix + r"\s+".join(re.escape(tok) for tok in tokens) + suffix
                ws_wb_matches = [
                    (m.start(), m.end(), region_text[m.start():m.end()])
                    for m in re.finditer(pattern_ws_wb, region_text)
                ]
                if ws_wb_matches:
                    return ws_wb_matches

            # 3. Fallback: unconstrained substring matches (if boundaries involve punctuation/symbols)
            pattern_exact = re.escape(ev_text)
            exact_matches = [
                (m.start(), m.end(), region_text[m.start():m.end()])
                for m in re.finditer(pattern_exact, region_text)
            ]
            if exact_matches:
                return exact_matches

            if tokens:
                pattern_ws = r"\s+".join(re.escape(tok) for tok in tokens)
                ws_matches = [
                    (m.start(), m.end(), region_text[m.start():m.end()])
                    for m in re.finditer(pattern_ws, region_text)
                ]
                if ws_matches:
                    return ws_matches

            return []

        # Step 1: Pre-validation of field types, values, and evidence texts
        valid_candidates: list[dict] = []
        for ext in extractions:
            field_type = ext.get("field_type", "")
            value = ext.get("value", "")
            evidence_text = ext.get("evidence_text", "")

            if field_type not in PERMITTED_NARRATIVE_FIELDS:
                diagnostics["rejected"].append({
                    "field_type": field_type,
                    "reason": "field_type not in PERMITTED_NARRATIVE_FIELDS",
                })
                continue

            if not value or not str(value).strip():
                diagnostics["rejected"].append({
                    "field_type": field_type,
                    "reason": "empty value",
                })
                continue

            if not evidence_text or not str(evidence_text).strip():
                diagnostics["rejected"].append({
                    "field_type": field_type,
                    "reason": "empty evidence_text",
                })
                continue

            valid_candidates.append(ext)

        # Step 2: Separate candidates with explicit offsets from those needing occurrence search
        grounded: list[tuple[dict, int, int, str]] = []
        needs_search: list[dict] = []

        for ext in valid_candidates:
            evidence_text = str(ext.get("evidence_text", ""))
            has_local = ext.get("local_start") is not None and ext.get("local_end") is not None
            has_char = ext.get("char_start") is not None and ext.get("char_end") is not None

            if has_local or has_char:
                try:
                    if has_local:
                        ls = int(ext["local_start"])
                        le = int(ext["local_end"])
                    else:
                        cs = int(ext["char_start"])
                        ce = int(ext["char_end"])
                        if char_offset <= cs < char_offset + len(region_text):
                            ls = cs - char_offset
                            le = ce - char_offset
                        elif 0 <= cs < len(region_text):
                            ls = cs
                            le = ce
                        else:
                            ls, le = cs, ce
                except (ValueError, TypeError):
                    diagnostics["rejected"].append({
                        "field_type": ext.get("field_type"),
                        "value": ext.get("value"),
                        "evidence_text": evidence_text,
                        "reason": "invalid source offset: non-integer offset",
                    })
                    continue

                # Bounds check
                if ls < 0 or le <= ls or le > len(region_text):
                    diagnostics["rejected"].append({
                        "field_type": ext.get("field_type"),
                        "value": ext.get("value"),
                        "evidence_text": evidence_text,
                        "reason": f"invalid source offset: [{ls}:{le}] out of bounds for region_text (len {len(region_text)})",
                    })
                    continue

                # Text match check
                span_text = region_text[ls:le]
                if (
                    span_text == evidence_text
                    or re.sub(r"\s+", " ", span_text).strip() == re.sub(r"\s+", " ", evidence_text).strip()
                ):
                    grounded.append((ext, ls, le, span_text))
                else:
                    diagnostics["rejected"].append({
                        "field_type": ext.get("field_type"),
                        "value": ext.get("value"),
                        "evidence_text": evidence_text,
                        "reason": f"invalid source offset: text at [{ls}:{le}] '{span_text}' does not match evidence_text '{evidence_text}'",
                    })
            else:
                needs_search.append(ext)

        # Step 3: Ground candidates without explicit offsets
        by_ev_text: dict[str, list[dict]] = {}
        for ext in needs_search:
            ev_text = str(ext.get("evidence_text", ""))
            by_ev_text.setdefault(ev_text, []).append(ext)

        for ev_text, exts in by_ev_text.items():
            occs = _find_occurrences(ev_text)
            if len(occs) == 0:
                for ext in exts:
                    diagnostics["rejected"].append({
                        "field_type": ext.get("field_type"),
                        "value": ext.get("value"),
                        "evidence_text": ev_text,
                        "reason": "evidence_text is not a verbatim substring of region_text",
                    })
            elif len(occs) == 1:
                ls, le, span_text = occs[0]
                for ext in exts:
                    grounded.append((ext, ls, le, span_text))
            else:
                # Multiple occurrences in region_text
                all_identical = (
                    len({(e.get("field_type"), str(e.get("value")), str(e.get("unit_raw") or "")) for e in exts}) == 1
                )
                if all_identical and len(exts) == len(occs):
                    for ext, (ls, le, span_text) in zip(exts, occs):
                        grounded.append((ext, ls, le, span_text))
                else:
                    for ext in exts:
                        diagnostics["rejected"].append({
                            "field_type": ext.get("field_type"),
                            "value": ext.get("value"),
                            "evidence_text": ev_text,
                            "reason": f"ambiguous evidence span: evidence_text appears multiple times ({len(occs)}) in region_text without unique source offset",
                        })

        # Step 4: Deterministic sort by coordinates and anchor priority
        def _field_priority(ft: str) -> int:
            if ft == "med.name":
                return 0
            if ft in ("med.dose", "med.route", "med.frequency", "med.duration"):
                return 1
            if ft == "lab.value":
                return 0
            if ft == "lab.flag":
                return 1
            return 2

        grounded.sort(key=lambda item: (
            item[1],  # local_start (ascending)
            _field_priority(item[0].get("field_type", "")),
            item[2],  # local_end (ascending)
            str(item[0].get("field_type", "")),
            str(item[0].get("value", "")),
            str(item[0].get("unit_raw", "") or ""),
            -float(item[0].get("confidence", 0.8)),
        ))

        # Step 4.5: Segment region_text into sentence and clause spans (Milestone 2)
        segments = segment_clinical_text(region_text, page=page, char_offset=char_offset)

        # Step 5: Segment Containment, Layered Medication Association, and Claim/Evidence generation
        # 1. Enforce strict segment containment: reject any extraction crossing segment boundaries
        contained_by_seg: dict[int, list[tuple[dict, int, int, str]]] = {}
        for ext, local_start, local_end, source_span in grounded:
            seg_idx = find_segment_idx(local_start, local_end, segments)
            if seg_idx is None:
                diagnostics["rejected"].append({
                    "field_type": ext.get("field_type"),
                    "value": ext.get("value"),
                    "evidence_text": ext.get("evidence_text"),
                    "reason": f"invalid source offset: [{local_start}:{local_end}] uncontained or crosses segment boundary",
                })
                continue
            contained_by_seg.setdefault(seg_idx, []).append((ext, local_start, local_end, source_span))

        # 2. Process segment by segment
        assigned_items: list[tuple[dict, int, int, str, str, str, dict]] = []

        for seg_idx in sorted(contained_by_seg.keys()):
            seg_items = contained_by_seg[seg_idx]
            seg = segments[seg_idx]

            lab_items = [it for it in seg_items if it[0].get("field_type", "").startswith("lab.")]
            med_items = [it for it in seg_items if it[0].get("field_type", "").startswith("med.")]
            other_items = [
                it for it in seg_items
                if not it[0].get("field_type", "").startswith("lab.")
                and not it[0].get("field_type", "").startswith("med.")
            ]

            # Laboratory processing (Layer C: verified per-analyte UUID group assignment)
            current_lab_group = NO_ANCHOR_SENTINEL
            for ext, ls, le, span in lab_items:
                ft = ext.get("field_type", "")
                if ft == "lab.value":
                    current_lab_group = str(uuid.uuid4())
                gid = current_lab_group if ft in ("lab.value", "lab.flag") else str(uuid.uuid4())
                assoc = (
                    AssociationStatus.UNAMBIGUOUS_SINGLE.value
                    if gid != NO_ANCHOR_SENTINEL
                    else AssociationStatus.UNANCHORED_ORPHAN.value
                )
                assigned_items.append((ext, ls, le, span, gid, assoc, {}))

            # Medication processing (Layer C: Layered conservative association engine)
            if med_items:
                med_assigned = associate_segment_medications(seg.text, med_items)
                for ext, ls, le, span, gid, assoc_status, meta in med_assigned:
                    assigned_items.append((
                        ext, ls, le, span, gid,
                        assoc_status.value if hasattr(assoc_status, "value") else str(assoc_status),
                        meta,
                    ))

            # Other fields (if any)
            for ext, ls, le, span in other_items:
                assigned_items.append((ext, ls, le, span, str(uuid.uuid4()), AssociationStatus.UNAMBIGUOUS_SINGLE.value, {}))

        # 3. Sort assigned items deterministically by coordinates and priority
        assigned_items.sort(key=lambda item: (
            item[1],  # local_start
            _field_priority(item[0].get("field_type", "")),
            item[2],  # local_end
            str(item[0].get("field_type", "")),
            str(item[0].get("value", "")),
        ))

        # 4. Generate Evidence and Claims
        for ext, local_start, local_end, source_span, group_id, assoc_status_str, meta in assigned_items:
            field_type = ext.get("field_type", "")
            value = ext.get("value", "")
            unit_raw = ext.get("unit_raw")
            confidence = float(ext.get("confidence", 0.80))

            char_start = char_offset + local_start
            char_end = char_offset + local_end

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
                text=source_span,
                char_start=char_start,
                char_end=char_end,
                bbox=bbox_model,
                ocr_confidence=ocr_conf,
            )
            all_evidences.append(evidence)

            value_numeric: Optional[float] = None
            if field_type in ("lab.value", "med.dose"):
                num_m = re.search(r"(-?\d+(?:\.\d+)?)", value)
                if num_m:
                    try:
                        value_numeric = float(num_m.group(1))
                    except ValueError:
                        pass

            perm_temp = {"current", "historical", "discontinued", "planned", "unspecified"}
            temp_status = meta.get("temporal_status", "unspecified")
            if temp_status not in perm_temp:
                temp_status = "unspecified"
            if ext.get("temporal_status") in perm_temp and temp_status == "unspecified":
                temp_status = ext["temporal_status"]

            claim = Claim(
                document_id=document_id,
                group_id=group_id,
                field_type=field_type,  # type: ignore[arg-type]
                value=value.strip(),
                value_numeric=value_numeric,
                unit_raw=unit_raw,
                extraction_method="llm_narrative",
                extraction_confidence=confidence,
                evidence_id=evidence.evidence_id,
                region_id=region_id,
                temporal_status=temp_status,  # type: ignore[arg-type]
            )

            # Attach dynamic association metadata to Claim
            object.__setattr__(claim, "association_status", assoc_status_str)
            if meta.get("slot_conflict"):
                object.__setattr__(claim, "slot_conflict", True)
            if meta.get("provisional"):
                object.__setattr__(claim, "provisional", True)
            if meta.get("shared"):
                object.__setattr__(claim, "is_shared", True)
            if meta.get("orphan"):
                object.__setattr__(claim, "orphan", True)
            if meta.get("temporal_status") == "conditional" or meta.get("is_conditional"):
                object.__setattr__(claim, "is_conditional", True)
                object.__setattr__(claim, "conditional_instruction", True)

            all_claims.append(claim)

        diagnostics["rejected"].sort(key=lambda r: (
            str(r.get("field_type", "")),
            str(r.get("reason", "")),
            str(r.get("value", "")),
            str(r.get("evidence_text", "")),
        ))

        return all_evidences, all_claims
