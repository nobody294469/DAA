"""Extraction Router — canonical pipeline orchestrator.

Authority: DEC-001, DEC-004, DEC-017, DEC-019
PHASE0_ARCHITECTURE_RESOLUTION §5 (Canonical Data Flow)

Pipeline:
  DocumentText
      ↓
  RegionClassifier (DEC-001: rule-based heuristic)
      ↓
  ┌─────────────────────────────────────────────────────────┐
  │ route='deterministic'                                   │
  │   DeterministicExtractor → claims with confidence      │
  │   if claim.extraction_confidence < DEC-004 threshold:  │
  │       GLiNERFallbackExtractor (Tier 1 fields only)      │
  └─────────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────────┐
  │ route='llm_narrative'                                   │
  │   NarrativeExtractor (LLM, schema-constrained)          │
  └─────────────────────────────────────────────────────────┘

All paths produce:
  RoutingDecision → ExtractionResult → (Evidence[], Claim[])

Routing is auditable: every claim carries:
  - extraction_method ('deterministic' | 'gliner' | 'llm_narrative')
  - extraction_confidence
  - region_id linking to the RoutingDecision
  - evidence_id linking to the Evidence store

STOP boundary: this module produces Claim + Evidence.
It does NOT implement terminology normalization, FHIR mapping,
or verification cascade.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Optional

import yaml

from clindoc.extract.contracts import (
    Claim, DocumentExtractionOutput, Evidence,
    ExtractionResult, RoutingDecision, TIER_1_FIELDS, TIER_2_FIELDS,
)
from clindoc.extract.deterministic import DeterministicExtractor
from clindoc.extract.gliner_fallback import GLiNERFallbackExtractor, MockGLiNERProvider
from clindoc.extract.llm_extractor import MockLLMProvider, NarrativeExtractor
from clindoc.extract.region_classifier import RegionClassifier
from clindoc.ocr.document_text import DocumentPage, DocumentText


# ─── CONFIG LOADING ──────────────────────────────────────────────────────────

_PROJ_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG_PATH = _PROJ_ROOT / "configs" / "extraction.yaml"

_DEFAULT_DETERMINISTIC_THRESHOLD = 0.75  # DEC-004 provisional default
_DEFAULT_ESCALATION_RATE_CAP = 0.30


def _load_extraction_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load extraction configuration from YAML, falling back to defaults."""
    path = config_path or _DEFAULT_CONFIG_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


# ─── EXTRACTION ROUTER ────────────────────────────────────────────────────────

class ExtractionRouter:
    """Canonical extraction pipeline orchestrator.

    Integrates RegionClassifier, DeterministicExtractor, GLiNERFallbackExtractor,
    and NarrativeExtractor per the project architectural contract.

    Configuration is read from configs/extraction.yaml at construction.
    All sub-components support mock injection for test isolation.
    """

    def __init__(
        self,
        deterministic_threshold: float | None = None,
        config_path: Path | None = None,
        # Sub-component overrides for testing
        gliner_provider: Any | None = None,
        llm_provider: Any | None = None,
        loinc_map: dict | None = None,
        ucum_map: dict | None = None,
        med_catalog: dict | None = None,
        max_llm_retries: int = 2,
    ):
        """
        Args:
            deterministic_threshold: DEC-004 re-routing threshold.
                                     Claims below this confidence → GLiNER fallback.
            config_path: Optional path to extraction.yaml.
            gliner_provider: Override GLiNER provider (use MockGLiNERProvider for tests).
            llm_provider: Override LLM provider (use MockLLMProvider for tests).
            loinc_map: Optional LOINC lookup injection (for tests).
            ucum_map: Optional UCUM lookup injection (for tests).
            med_catalog: Optional medication catalog injection (for tests).
            max_llm_retries: Bounded LLM retry count (DEC-013: max 2).
        """
        cfg = _load_extraction_config(config_path)
        det_cfg = cfg.get("deterministic_extraction", {})

        if deterministic_threshold is not None:
            self.deterministic_threshold = deterministic_threshold
        else:
            self.deterministic_threshold = det_cfg.get(
                "confidence_threshold", _DEFAULT_DETERMINISTIC_THRESHOLD
            )
        self.escalation_rate_cap = det_cfg.get(
            "escalation_rate_cap", _DEFAULT_ESCALATION_RATE_CAP
        )

        # Sub-component construction
        region_cfg = cfg.get("region_classification", {})
        self.classifier = RegionClassifier(
            med_line_fraction_threshold=region_cfg.get("med_line_fraction_threshold", 0.4),
            lab_line_fraction_threshold=region_cfg.get("lab_line_fraction_threshold", 0.3),
        )

        self.deterministic = DeterministicExtractor(
            loinc_map=loinc_map,
            ucum_map=ucum_map,
            med_catalog=med_catalog,
        )

        # GLiNER: use injected provider or default to subprocess (lazy)
        self.gliner = GLiNERFallbackExtractor(provider=gliner_provider)

        # LLM: use injected provider or default to mock (safe for tests)
        llm_cfg = cfg.get("llm_narrative", {})
        provider_name = llm_cfg.get("provider", "mock")
        if llm_provider is not None:
            effective_llm = llm_provider
        elif provider_name == "mock":
            effective_llm = MockLLMProvider()
        else:
            # Production: VLLMProvider (only used when explicitly configured)
            from clindoc.extract.llm_extractor import VLLMProvider
            effective_llm = VLLMProvider(
                model_name=llm_cfg.get("model_name", "Qwen/Qwen2.5-7B-Instruct"),
                base_url=llm_cfg.get("base_url", "http://localhost:8000/v1"),
                api_key_env=llm_cfg.get("api_key_env", "VLLM_API_KEY"),
                temperature=llm_cfg.get("temperature", 0.0),
                timeout=llm_cfg.get("timeout_seconds", 60),
            )
        self.narrative = NarrativeExtractor(
            provider=effective_llm,
            max_retries=min(llm_cfg.get("max_retries", 2), 2),
        )

    def extract_document(
        self,
        doc: DocumentText,
        is_lab_default: bool | None = None,
    ) -> DocumentExtractionOutput:
        """Run the full extraction pipeline on a DocumentText.

        Args:
            doc: The canonical DocumentText to extract from.
            is_lab_default: Hint for region classification (None = auto-detect).

        Returns:
            DocumentExtractionOutput with all RoutingDecisions, Evidence, and Claims.
        """
        output = DocumentExtractionOutput(document_id=doc.document_id)

        for page in doc.pages:
            page_results = self._process_page(doc, page, is_lab_default)
            output.regions.extend(page_results)

        return output

    def _process_page(
        self,
        doc: DocumentText,
        page: DocumentPage,
        is_lab_default: bool | None,
    ) -> list[ExtractionResult]:
        """Process a single page: classify regions then extract."""
        routing_decisions = self.classifier.classify_page(doc.document_id, page)

        if not routing_decisions:
            return []

        results: list[ExtractionResult] = []

        # Determine if this page looks like a lab document
        page_text = page.get_page_text()
        is_lab = self._detect_lab_page(doc.document_id, page_text, is_lab_default)

        for rd in routing_decisions:
            result = self._dispatch_region(doc, page, page_text, rd, is_lab)
            results.append(result)

        return results

    def _detect_lab_page(
        self,
        doc_id: str,
        page_text: str,
        override: bool | None,
    ) -> bool:
        """Determine whether a page is a lab report or medication document."""
        if override is not None:
            return override
        doc_id_upper = doc_id.upper()
        if doc_id_upper.startswith("LAB_") or "LAB" in doc_id_upper:
            return True
        if doc_id_upper.startswith("MED_") or "MEDICATION" in doc_id_upper or "PRESCRIPTION" in doc_id_upper:
            return False
        # Heuristic: lab pages have more numeric values + units
        lab_hints = len(re.findall(r"\d+(?:\.\d+)?\s*(?:g/dL|mg/dL|mmol/L|U/L|%)", page_text, re.IGNORECASE))
        med_hints = len(re.findall(r"\d+\s*(?:mg|mcg|mL)\s+(?:PO|oral|BID|TID|daily)", page_text, re.IGNORECASE))
        return lab_hints >= med_hints

    def _dispatch_region(
        self,
        doc: DocumentText,
        page: DocumentPage,
        page_text: str,
        rd: RoutingDecision,
        is_lab: bool,
    ) -> ExtractionResult:
        """Dispatch a region to the appropriate extractor based on RoutingDecision."""
        region_text = self._get_region_text(rd, page_text)

        first_line = region_text.splitlines()[0] if region_text else ""
        char_offset = page_text.find(first_line) if first_line else 0
        if char_offset < 0:
            char_offset = 0

        if rd.route == "deterministic":
            return self._run_deterministic_path(doc, page, region_text, rd, is_lab, char_offset=char_offset)
        elif rd.route == "llm_narrative":
            return self._run_narrative_path(doc, page, region_text, rd, char_offset=char_offset)
        else:
            # Unknown route — treat as narrative (safe default)
            return self._run_narrative_path(doc, page, region_text, rd, char_offset=char_offset)

    def _get_region_text(self, rd: RoutingDecision, page_text: str) -> str:
        """Reconstruct the region text from routing decision region_id.

        Region IDs have format: doc_id::pN::rI
        We reconstruct by re-segmenting the page text.
        """
        # Parse region index from region_id (format: ...::rI)
        m = re.search(r"::r(\d+)$", rd.region_id)
        if m is None:
            return page_text

        region_idx = int(m.group(1))
        # Re-segment page text to find this region
        regions = []
        current: list[str] = []
        for line in page_text.splitlines():
            stripped = line.strip()
            if stripped:
                current.append(stripped)
            else:
                if current:
                    regions.append("\n".join(current))
                    current = []
        if current:
            regions.append("\n".join(current))

        if region_idx < len(regions):
            return regions[region_idx]
        return page_text  # Fallback: use full page text

    def _run_deterministic_path(
        self,
        doc: DocumentText,
        page: DocumentPage,
        region_text: str,
        rd: RoutingDecision,
        is_lab: bool,
        char_offset: int = 0,
    ) -> ExtractionResult:
        """Run deterministic extraction, escalate low-confidence Tier 1 to GLiNER and LLM (DEC-004)."""
        diagnostics: dict = {
            "route": "deterministic",
            "escalated_to_gliner": [],
            "escalated_to_llm": [],
            "unresolved_fields": [],
        }

        # 1. Run deterministic extraction
        det_evidences, det_claims = self.deterministic.extract_region(
            doc.document_id, page, region_text, region_id=rd.region_id, is_lab=is_lab,
            char_offset=char_offset,
        )

        final_evidences: list[Evidence] = list(det_evidences)
        final_claims: list[Claim] = []

        # Categorize claims: Tier 2 always accepted (DEC-019), Tier 1 checked against threshold
        low_conf_tier1: list[Claim] = []
        for claim in det_claims:
            if claim.field_type in TIER_2_FIELDS:
                # Tier 2: always accept deterministic result (DEC-019)
                final_claims.append(claim)
            elif claim.extraction_confidence >= self.deterministic_threshold:
                # Tier 1 high confidence: accept
                final_claims.append(claim)
            else:
                # Tier 1 low confidence: candidate for escalation
                low_conf_tier1.append(claim)
                diagnostics["escalated_to_gliner"].append({
                    "field_type": claim.field_type,
                    "confidence": claim.extraction_confidence,
                    "threshold": self.deterministic_threshold,
                })

        # 1b. Unfamiliar layout: the template parsers found no Tier-1 claim at all, so nothing can escalate.
        #     A provider that sets `recovers_unparsed_regions` (ClinicalTextProvider) reads the region as free text.
        if (getattr(getattr(self.narrative, "_provider", None), "recovers_unparsed_regions", False)
                and not any(c.field_type in TIER_1_FIELDS for c in det_claims)):
            try:
                from clindoc.extract.text_provider import layout_text
                rec_evs, rec_clms, rec_diag = self.narrative.extract(
                    doc.document_id, page, region_text, rd.region_id, char_offset=char_offset,
                    prompt_text=layout_text(page, char_offset, char_offset + len(region_text)) or None)
                final_evidences.extend(rec_evs)
                final_claims.extend(rec_clms)
                diagnostics["text_recovery"] = {"claims": len(rec_clms), "rejected": len(rec_diag.get("rejected", []))}
            except Exception as e:  # never let recovery break a run
                diagnostics["text_recovery"] = {"error": str(e)}

        # 2. Stage 2: GLiNER cross-check for low-confidence Tier 1 fields
        unresolved_tier1: list[Claim] = []
        if low_conf_tier1:
            total_tier1 = sum(1 for c in det_claims if c.field_type in TIER_1_FIELDS)
            escalation_rate = len(low_conf_tier1) / max(total_tier1, 1)
            diagnostics["escalation_rate"] = escalation_rate

            gliner_evs: list[Evidence] = []
            gliner_clms: list[Claim] = []

            try:
                gliner_evs, gliner_clms = self.gliner.extract(
                    doc.document_id, page, region_text, rd.region_id,
                    char_offset=char_offset,
                    diagnostics=diagnostics,
                )
                diagnostics["gliner_claims_added"] = len(gliner_clms)
            except Exception as e:
                # GLiNER failure (e.g. environment missing, crash)
                diagnostics["gliner_error"] = str(e)
                diagnostics["gliner_claims_added"] = 0

            # Check which low_conf claims were resolved by GLiNER
            # Resolved if GLiNER produced claim of that field_type with confidence >= threshold
            resolved_fields: set[str] = set()
            for g_clm in gliner_clms:
                if g_clm.field_type in TIER_1_FIELDS and g_clm.extraction_confidence >= self.deterministic_threshold:
                    final_claims.append(g_clm)
                    resolved_fields.add(g_clm.field_type)
            final_evidences.extend(gliner_evs)

            for low_claim in low_conf_tier1:
                if low_claim.field_type not in resolved_fields:
                    unresolved_tier1.append(low_claim)

            # 3. Stage 3: LLM escalation if uncertainty persists (DEC-004)
            if unresolved_tier1:
                for uc in unresolved_tier1:
                    diagnostics["escalated_to_llm"].append({
                        "field_type": uc.field_type,
                        "deterministic_confidence": uc.extraction_confidence,
                        "threshold": self.deterministic_threshold,
                        "reason": "uncertainty_persisted_after_gliner",
                    })

                try:
                    llm_evs, llm_clms, llm_diag = self.narrative.extract(
                        doc.document_id, page, region_text, rd.region_id,
                        char_offset=char_offset,
                    )
                    diagnostics["llm_diagnostics"] = llm_diag
                    llm_resolved_fields: set[str] = set()

                    # Accept LLM claims for unresolved Tier 1 fields
                    for l_clm in llm_clms:
                        if l_clm.field_type in TIER_1_FIELDS and l_clm.extraction_confidence >= self.deterministic_threshold:
                            final_claims.append(l_clm)
                            llm_resolved_fields.add(l_clm.field_type)
                    final_evidences.extend(llm_evs)

                    # 4. Check if any fields failed all stages
                    for uc in unresolved_tier1:
                        if uc.field_type not in llm_resolved_fields:
                            diagnostics["unresolved_fields"].append({
                                "field_type": uc.field_type,
                                "deterministic_confidence": uc.extraction_confidence,
                                "status": "all_stages_failed",
                            })
                except Exception as e:
                    diagnostics["llm_error"] = str(e)
                    for uc in unresolved_tier1:
                        diagnostics["unresolved_fields"].append({
                            "field_type": uc.field_type,
                            "deterministic_confidence": uc.extraction_confidence,
                            "status": "all_stages_failed",
                            "error": str(e),
                        })

        return ExtractionResult(
            routing_decision=rd,
            evidence=final_evidences,
            claims=final_claims,
            diagnostics=diagnostics,
        )

    def _run_narrative_path(
        self,
        doc: DocumentText,
        page: DocumentPage,
        region_text: str,
        rd: RoutingDecision,
        char_offset: int = 0,
    ) -> ExtractionResult:
        """Run LLM narrative extraction for prose regions."""
        try:
            prompt_text = None
            if getattr(getattr(self.narrative, "_provider", None), "recovers_unparsed_regions", False):
                from clindoc.extract.text_provider import layout_text  # rows rebuilt from word positions
                prompt_text = layout_text(page, char_offset, char_offset + len(region_text)) or None
            evidences, claims, diagnostics = self.narrative.extract(
                doc.document_id, page, region_text, rd.region_id,
                char_offset=char_offset, prompt_text=prompt_text,
            )
            diagnostics["route"] = "llm_narrative"
        except Exception as e:
            evidences, claims = [], []
            diagnostics = {"route": "llm_narrative", "error": str(e)}

        return ExtractionResult(
            routing_decision=rd,
            evidence=evidences,
            claims=claims,
            diagnostics=diagnostics,
        )
