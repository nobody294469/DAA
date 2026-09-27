"""Unified Terminology Normalizer for clinical claims.

Authority:
  - PROJECT_SPEC §5 (Component 6), §7.5, §8.3, §14, §15.3, §21.6
  - DEC-002: code_confidence thresholding (target >=95% precision on accepted codes; provisional default 0.75)
  - DEC-007: hybrid lexical + semantic retrieval for LOINC/RxNorm; deterministic UCUM lookup
  - PHASE0_ARCHITECTURE_RESOLUTION AUDIT-14

MUST Rules:
  - NEVER alter raw claim value (§14.3, auditability requirement).
  - Enforce code_confidence < threshold -> normalized_code: None, code_system: None (§7.5, §14.3).
  - UCUM unit normalization for lab.unit and unit_raw (§8.3, §14.2).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from clindoc.extract.contracts import Claim
from clindoc.normalize.hybrid import CandidateMatch, HybridTerminologyRetriever
from clindoc.normalize.ucum import UCUMNormalizer

DEFAULT_LOINC_PATH = Path("configs/loinc_subset.json")
DEFAULT_MED_PATH = Path("configs/medication_subset.json")
DEFAULT_UCUM_PATH = Path("configs/ucum_subset.json")


def _resolve_config_path(path: Path) -> Path:
    """Resolve configuration path relative to current or project root."""
    if path.exists():
        return path
    proj_root = Path(__file__).resolve().parents[3]
    alt = proj_root / path
    if alt.exists():
        return alt
    return path


class TerminologyNormalizer:
    """Production Terminology Normalization subsystem.

    Maps:
      - lab.test_name -> LOINC
      - med.name      -> RxNorm
      - lab.unit / unit_raw -> UCUM
    """

    def __init__(
        self,
        loinc_path: Optional[Path] = None,
        med_path: Optional[Path] = None,
        ucum_path: Optional[Path] = None,
        loinc_threshold: float = 0.75,   # DEC-002 provisional default
        rxnorm_threshold: float = 0.75,  # DEC-002 provisional default
        hybrid_alpha: float = 0.5,       # DEC-007 lexical vs semantic balance
    ) -> None:
        self.loinc_threshold = float(loinc_threshold)
        self.rxnorm_threshold = float(rxnorm_threshold)

        # 1. Initialize UCUM Deterministic Normalizer
        resolved_ucum = _resolve_config_path(ucum_path or DEFAULT_UCUM_PATH)
        self.ucum_normalizer = UCUMNormalizer(resolved_ucum)

        # 2. Initialize LOINC Hybrid Retriever
        self.loinc_retriever = HybridTerminologyRetriever(system_name="LOINC", alpha=hybrid_alpha)
        resolved_loinc = _resolve_config_path(loinc_path or DEFAULT_LOINC_PATH)
        if resolved_loinc.exists():
            with open(resolved_loinc, "r", encoding="utf-8") as f:
                self.loinc_retriever.index_concepts(json.load(f))

        # 3. Initialize RxNorm Hybrid Retriever
        self.rxnorm_retriever = HybridTerminologyRetriever(system_name="RxNorm", alpha=hybrid_alpha)
        resolved_med = _resolve_config_path(med_path or DEFAULT_MED_PATH)
        if resolved_med.exists():
            with open(resolved_med, "r", encoding="utf-8") as f:
                self.rxnorm_retriever.index_concepts(json.load(f))

        # Audit candidates store: keyed by claim_id -> list of candidate dicts
        self.audit_candidates: dict[str, list[dict[str, Any]]] = {}

    def get_candidates(self, field_type: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Retrieve candidates for a given clinical field type and query string (§7.5)."""
        if not query or not query.strip():
            return []

        if field_type == "lab.test_name":
            matches = self.loinc_retriever.retrieve(query, top_k=top_k)
            return [m.to_dict() for m in matches]
        elif field_type == "med.name":
            matches = self.rxnorm_retriever.retrieve(query, top_k=top_k)
            return [m.to_dict() for m in matches]
        elif field_type in ("lab.unit", "unit_raw"):
            canon, conf = self.ucum_normalizer.normalize(query)
            if canon:
                return [{"code": canon, "display": canon, "score": conf, "system": "UCUM"}]
            return []
        return []

    def normalize_claim(self, claim: Claim) -> Claim:
        """Enrich a single Claim with standardized terminology code and confidence.

        MUST NOT alter claim.value (§14.3).
        If code_confidence < threshold, normalized_code and code_system are set to None.
        """
        # Store original raw value to assert §14.3 invariant
        original_value = claim.value

        candidates: list[CandidateMatch] = []
        best_code: Optional[str] = None
        best_system: Optional[str] = None
        best_conf: float = 0.0

        if claim.field_type == "lab.test_name":
            threshold = self.loinc_threshold
            matches = self.loinc_retriever.retrieve(claim.value, top_k=5)
            candidates = matches
            if matches:
                top = matches[0]
                best_conf = round(top.score, 4)
                if best_conf >= threshold:
                    best_code = top.code
                    best_system = "LOINC"

        elif claim.field_type == "med.name":
            threshold = self.rxnorm_threshold
            matches = self.rxnorm_retriever.retrieve(claim.value, top_k=5)
            candidates = matches
            if matches:
                top = matches[0]
                best_conf = round(top.score, 4)
                if best_conf >= threshold:
                    best_code = top.code
                    best_system = "RxNorm"

        elif claim.field_type == "lab.unit":
            canon, conf = self.ucum_normalizer.normalize(claim.value)
            best_conf = round(conf, 4)
            if canon:
                best_code = canon
                best_system = "UCUM"
                candidates = [CandidateMatch(code=canon, display=canon, score=conf, system="UCUM")]

        # Normalize unit_raw if present on numeric claims (§8.3)
        if claim.unit_raw:
            canon_unit, _ = self.ucum_normalizer.normalize(claim.unit_raw)
            # If claim is lab.unit and was unresolvable from value, fallback to unit_raw
            if claim.field_type == "lab.unit" and best_code is None and canon_unit:
                best_code = canon_unit
                best_system = "UCUM"
                best_conf = 1.0

        # Record candidates for machine-readable audit report (§7.5)
        self.audit_candidates[claim.claim_id] = [c.to_dict() for c in candidates]

        # Invariant check: claim.value MUST remain completely untouched (§14.3)
        assert claim.value == original_value, f"Invariant violation: claim.value was modified (§14.3)"

        # Enrich claim fields
        claim.normalized_code = best_code
        claim.code_system = best_system
        claim.code_confidence = best_conf

        return claim

    def normalize_claims(self, claims: list[Claim]) -> list[Claim]:
        """Normalize a list of claims in place and return them."""
        for c in claims:
            self.normalize_claim(c)
        return claims
