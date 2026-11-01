"""Stage A: Deterministic Rule-Based Clinical Claim Verifier.

Authority:
  - PROJECT_SPEC §5 #8, §7.6, §10, §12.1, §12.2, §20.1
  - DEC-019: Field verification tiering (Tier 1 vs Tier 2)
  - DEC-002: Terminology retrieval confidence thresholding
  - DEC-007: Deterministic UCUM unit normalization

Stage A verifies claims against verbatim evidence using deterministic rules,
regular expressions, numerical value comparisons with UCUM unit normalization,
and categorical token matching.

It does NOT use neural models or LLMs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Final, Optional, Sequence, Union

REASON_PARENT_ANCHOR_UNVERIFIED: Final[str] = "PARENT_ANCHOR_UNVERIFIED"

MED_ATTRIBUTE_FIELDS: Final[frozenset[str]] = frozenset({
    "med.dose",
    "med.route",
    "med.frequency",
    "med.duration",
})

NO_ANCHOR_SENTINEL: Final[str] = "__no_anchor__"

from clindoc.extract.contracts import (
    Claim,
    Evidence,
    FieldType,
    TIER_1_FIELDS,
    TIER_2_FIELDS,
)
from clindoc.normalize.ucum import normalize_ucum
from clindoc.schema.verification import ContradictionType, VerificationLabel

# Project root and config loader for catalog aliases
_PROJ_ROOT = Path(__file__).resolve().parents[3]
_LOINC_MAP: Optional[dict[str, Any]] = None
_CANON_TO_ALIASES: dict[str, set[str]] = {}
_CODE_TO_ALIASES: dict[str, set[str]] = {}


def _init_catalog_aliases() -> None:
    """Initialize catalog alias lookups from configs/loinc_subset.json."""
    global _LOINC_MAP, _CANON_TO_ALIASES, _CODE_TO_ALIASES
    if _LOINC_MAP is not None:
        return
    p = _PROJ_ROOT / "configs" / "loinc_subset.json"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            _LOINC_MAP = json.load(f)
    else:
        _LOINC_MAP = {}

    for alias, meta in _LOINC_MAP.items():
        canon = meta.get("canonical_name", alias)
        code = meta.get("loinc_code")

        canon_lower = canon.lower().strip()
        alias_lower = alias.lower().strip()

        _CANON_TO_ALIASES.setdefault(canon_lower, set()).update({alias, canon})
        _CANON_TO_ALIASES.setdefault(alias_lower, set()).update({alias, canon})
        if code:
            _CODE_TO_ALIASES.setdefault(code, set()).update({alias, canon})

# Standard clinical negation triggers (DEC-003, §20.1)
# Note: Actual negative laboratory result terms (e.g., 'no growth', 'not detected')
# are result values, not clinical negation triggers. Negation lookaheads prevent false
# negation_mismatch discrepancies on completed negative results (Phase 3.3.1).
NEGATION_PATTERNS: tuple[str, ...] = (
    r"\bno\s+(?!growth|organisms?)\b",
    r"\bnot\s+(?!detected|reactive)\b",
    r"\bdenies\b",
    r"\bnegative\s+for\b",
    r"\bwithout\b",
    r"\babsent\b",
    r"\bdiscontinued\b",
    r"\bnone\b",
    r"\bnever\b",
    r"\bzero\b",
    r"(?<!cannot\s)(?<!can\'t\s)\b(?:rules?|ruled)\s+out\b",
)

# Standard route synonyms mapping to canonical route tokens
ROUTE_SYNONYMS: dict[str, str] = {
    "po": "oral",
    "by mouth": "oral",
    "oral": "oral",
    "iv": "intravenous",
    "intravenous": "intravenous",
    "im": "intramuscular",
    "intramuscular": "intramuscular",
    "sc": "subcutaneous",
    "sq": "subcutaneous",
    "subcut": "subcutaneous",
    "subcutaneous": "subcutaneous",
    "sl": "sublingual",
    "sublingual": "sublingual",
    "pr": "rectal",
    "rectal": "rectal",
    "topical": "topical",
    "top": "topical",
    "inh": "inhalation",
    "inhalation": "inhalation",
    "ophthalmic": "ophthalmic",
    "eye": "ophthalmic",
    "otic": "otic",
    "nasal": "nasal",
}

# Standard frequency synonyms mapping to canonical frequency tokens
FREQUENCY_SYNONYMS: dict[str, str] = {
    "qd": "daily",
    "daily": "daily",
    "once daily": "daily",
    "qday": "daily",
    "bid": "twice daily",
    "twice daily": "twice daily",
    "twice a day": "twice daily",
    "tid": "three times daily",
    "three times daily": "three times daily",
    "three times a day": "three times daily",
    "qid": "four times daily",
    "four times daily": "four times daily",
    "four times a day": "four times daily",
    "prn": "as needed",
    "as needed": "as needed",
    "qhs": "at bedtime",
    "at bedtime": "at bedtime",
    "q4h": "every 4 hours",
    "every 4 hours": "every 4 hours",
    "q6h": "every 6 hours",
    "every 6 hours": "every 6 hours",
    "q8h": "every 8 hours",
    "every 8 hours": "every 8 hours",
    "q12h": "every 12 hours",
    "every 12 hours": "every 12 hours",
    "weekly": "weekly",
    "once weekly": "weekly",
}

# Standard lab flag canonical groups
FLAG_HIGH_TOKENS: set[str] = {"h", "high", "hh", "crit high", "critical high", "+"}
FLAG_LOW_TOKENS: set[str] = {"l", "low", "ll", "crit low", "critical low", "-"}
FLAG_NORMAL_TOKENS: set[str] = {"normal", "norm", "wds", "wnl", "within normal limits"}
FLAG_ABNORMAL_TOKENS: set[str] = {"a", "abnormal", "abn", "*"}


@dataclass(frozen=True)
class StageAResult:
    """Intermediate verdict produced by Stage A deterministic verification."""

    label: Optional[VerificationLabel]
    contradiction_type: Optional[ContradictionType] = None
    confidence: float = 1.0
    normalized_code_agreement: Optional[bool] = None
    details: dict[str, Any] = field(default_factory=dict)


class DeterministicVerifier:
    """Stage A: Deterministic Rule-Based Verifier.

    Evaluates Claim against linked Evidence and document context.
    For Tier 2 fields, resolves the verdict definitively.
    For Tier 1 fields, provides a deterministic pre-check. If the field
    requires deeper semantic judgment, returns label=None so Stage B takes precedence.
    """

    def verify(
        self,
        claim: Claim,
        evidence: Optional[Evidence] = None,
        context_text: Optional[str] = None,
    ) -> StageAResult:
        """Execute Stage A deterministic verification for a claim.

        Args:
            claim: Extracted and normalized Claim.
            evidence: Linked Evidence span grounding the claim.
            context_text: Surrounding text window (DEC-003) for negation/temporal checks.

        Returns:
            StageAResult containing intermediate label, contradiction_type, and details.
        """
        # 1. Evidence Existence Check (§12.2)
        if evidence is None or not evidence.text or not evidence.text.strip():
            return StageAResult(
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                details={"reason": "missing_or_empty_evidence"},
            )

        ev_text = evidence.text.strip()
        search_scope = (context_text or ev_text).lower()

        # 2. Negation Check (§10, §20.1) - strictly scoped to evidence span and immediate mention window
        negation_detected = self._detect_negation(ev_text, context_text)
        if claim.negated != negation_detected:
            # If claim is marked not negated but text explicitly negates it
            if not claim.negated and negation_detected:
                return StageAResult(
                    label="DISCREPANCY",
                    contradiction_type="negation_mismatch",
                    confidence=1.0,
                    details={"evidence_negated": True, "claim_negated": False},
                )
            # If claim is marked negated but text affirms it without negation
            elif claim.negated and not negation_detected:
                return StageAResult(
                    label="DISCREPANCY",
                    contradiction_type="negation_mismatch",
                    confidence=1.0,
                    details={"evidence_negated": False, "claim_negated": True},
                )

        # 3. Field-Specific Verification Routing
        handler_map = {
            "lab.value": self._verify_numeric_value,
            "med.dose": self._verify_numeric_dose,
            "lab.unit": self._verify_unit,
            "lab.flag": self._verify_flag,
            "lab.reference_range": self._verify_reference_range,
            "lab.test_name": self._verify_test_name,
            "med.name": self._verify_med_name,
            "med.route": self._verify_route,
            "med.frequency": self._verify_frequency,
            "med.duration": self._verify_duration,
        }

        handler = handler_map.get(claim.field_type)
        if handler is not None:
            return handler(claim, ev_text, search_scope)

        # Default fallback for unrecognized field type: defer to learned stages
        return StageAResult(label=None, confidence=0.0)

    # ─── NUMERIC VERIFIERS (lab.value, med.dose) ─────────────────────────────

    def _verify_numeric_value(
        self, claim: Claim, ev_text: str, search_scope: str
    ) -> StageAResult:
        """Verify lab.value claim against evidence text with entity-awareness."""
        target_entity = self._extract_claim_entity(claim)
        field_val = self._extract_field_value(claim)

        # 1. Entity Compatibility Guard
        if target_entity is not None:
            if not self._is_entity_present_in_text(target_entity, search_scope):
                return StageAResult(
                    label="INSUFFICIENT_EVIDENCE",
                    confidence=1.0,
                    details={"reason": "entity_not_found_in_evidence", "target_entity": target_entity},
                )
            eval_text = self._extract_entity_local_block(target_entity, ev_text)
        else:
            eval_text = ev_text

        claim_num = self._extract_numeric_value(claim)
        if claim_num is None:
            # Non-numeric lab value (e.g. "positive", "negative")
            if field_val.strip().lower() in eval_text.lower():
                return StageAResult(label="SUPPORTED", confidence=1.0)
            return StageAResult(
                label="DISCREPANCY",
                contradiction_type="value_mismatch",
                confidence=1.0,
                details={"expected": field_val, "evidence_text": eval_text},
            )

        # Extract primary candidate value numbers from the entity-scoped block
        ev_numbers = self._extract_primary_lab_numbers(eval_text)
        if not ev_numbers and eval_text != ev_text:
            ev_numbers = self._extract_primary_lab_numbers(ev_text)

        if not ev_numbers:
            return StageAResult(
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                details={"reason": "no_primary_numbers_found_in_evidence", "claim_num": claim_num},
            )

        # Check unit mismatch first if unit is present
        unit_discrepancy = self._check_unit_consistency(claim, eval_text)
        if unit_discrepancy:
            return unit_discrepancy

        # Value comparison: exact match with tolerance
        if any(abs(n - claim_num) < 1e-4 for n in ev_numbers):
            return StageAResult(
                label="SUPPORTED",
                confidence=1.0,
                details={"matched_number": claim_num},
            )

        # Numbers exist in evidence but none match
        return StageAResult(
            label="DISCREPANCY",
            contradiction_type="value_mismatch",
            confidence=1.0,
            details={"claim_value": claim_num, "evidence_numbers": ev_numbers},
        )

    def _verify_numeric_dose(
        self, claim: Claim, ev_text: str, search_scope: str
    ) -> StageAResult:
        """Verify med.dose claim against evidence text with entity-awareness."""
        target_entity = self._extract_claim_entity(claim)
        field_val = self._extract_field_value(claim)

        # 1. Entity Compatibility Guard
        if target_entity is not None:
            if not self._is_entity_present_in_text(target_entity, search_scope):
                return StageAResult(
                    label="INSUFFICIENT_EVIDENCE",
                    confidence=1.0,
                    details={"reason": "entity_not_found_in_evidence", "target_entity": target_entity},
                )
            eval_text = ev_text
        else:
            eval_text = ev_text

        claim_num = self._extract_numeric_value(claim)
        if claim_num is None:
            if field_val.strip().lower() in eval_text.lower():
                return StageAResult(label="SUPPORTED", confidence=1.0)
            return StageAResult(
                label="DISCREPANCY",
                contradiction_type="dose_mismatch",
                confidence=1.0,
            )

        ev_numbers = self._extract_all_numbers(eval_text)
        if not ev_numbers and eval_text != ev_text:
            ev_numbers = self._extract_all_numbers(ev_text)

        if not ev_numbers:
            return StageAResult(
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                details={"reason": "no_dose_numbers_found_in_evidence"},
            )

        # Check unit mismatch first
        unit_discrepancy = self._check_unit_consistency(claim, eval_text)
        if unit_discrepancy:
            return unit_discrepancy

        if any(abs(n - claim_num) < 1e-4 for n in ev_numbers):
            return StageAResult(
                label="SUPPORTED",
                confidence=1.0,
                details={"matched_dose": claim_num},
            )

        return StageAResult(
            label="DISCREPANCY",
            contradiction_type="dose_mismatch",
            confidence=1.0,
            details={"claim_dose": claim_num, "evidence_numbers": ev_numbers},
        )

    def _check_unit_consistency(self, claim: Claim, ev_text: str) -> Optional[StageAResult]:
        """Check if unit in claim contradicts canonical unit in evidence."""
        claim_unit_raw = claim.unit_raw
        if not claim_unit_raw and claim.normalized_code and claim.code_system == "UCUM":
            claim_unit_raw = claim.normalized_code

        if not claim_unit_raw:
            return None

        claim_ucum, _ = normalize_ucum(claim_unit_raw)
        if not claim_ucum:
            return None

        # Look for potential units in evidence text
        tokens = re.findall(r"[a-zA-Z/%^0-9*\[\]{}]+", ev_text)
        ev_ucum_units = set()
        for tok in tokens:
            canon, conf = normalize_ucum(tok)
            if canon and conf >= 0.9:
                ev_ucum_units.add(canon)

        if ev_ucum_units and claim_ucum not in ev_ucum_units:
            return StageAResult(
                label="DISCREPANCY",
                contradiction_type="unit_mismatch",
                confidence=1.0,
                details={"claim_unit": claim_ucum, "evidence_units": list(ev_ucum_units)},
            )
        return None

    # ─── CATEGORICAL & TOKEN VERIFIERS ───────────────────────────────────────

    def _verify_flag(
        self, claim: Claim, ev_text: str, search_scope: str
    ) -> StageAResult:
        """Verify lab.flag claim against evidence tokens with entity-awareness."""
        target_entity = self._extract_claim_entity(claim)
        field_val = self._extract_field_value(claim)

        # 1. Entity Compatibility Guard
        if target_entity is not None:
            if not self._is_entity_present_in_text(target_entity, search_scope):
                return StageAResult(
                    label="INSUFFICIENT_EVIDENCE",
                    confidence=1.0,
                    details={"reason": "entity_not_found_in_evidence", "target_entity": target_entity},
                )
            eval_text = self._extract_entity_local_block(target_entity, ev_text)
        else:
            eval_text = ev_text

        val_clean = field_val.strip().lower()

        # Categorize claim flag
        claim_cat = self._categorize_flag(val_clean)
        if not claim_cat:
            return StageAResult(label=None, confidence=0.0)

        # Search for flag tokens in evidence
        ev_tokens = [t.strip(".,;:()[]{}\"'-") for t in re.split(r"[\s,|]+", eval_text.lower()) if t.strip()]
        ev_flag_cats = {self._categorize_flag(t) for t in ev_tokens} - {None}

        if not ev_flag_cats:
            # If evidence has no flag token at all
            return StageAResult(
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                details={"reason": "no_flag_found_in_evidence"},
            )

        if claim_cat in ev_flag_cats:
            return StageAResult(label="SUPPORTED", confidence=1.0)

        return StageAResult(
            label="DISCREPANCY",
            contradiction_type="value_mismatch",
            confidence=1.0,
            details={"claim_flag_category": claim_cat, "evidence_flag_categories": list(ev_flag_cats)},
        )

    def _categorize_flag(self, token: str) -> Optional[str]:
        if token in FLAG_HIGH_TOKENS:
            return "HIGH"
        if token in FLAG_LOW_TOKENS:
            return "LOW"
        if token in FLAG_NORMAL_TOKENS:
            return "NORMAL"
        if token in FLAG_ABNORMAL_TOKENS:
            return "ABNORMAL"
        return None

    def _verify_unit(
        self, claim: Claim, ev_text: str, search_scope: str
    ) -> StageAResult:
        """Verify Tier 2 lab.unit claim deterministically via UCUM with entity-awareness."""
        target_entity = self._extract_claim_entity(claim)
        field_val = self._extract_field_value(claim)

        if target_entity is not None and not self._is_entity_present_in_text(target_entity, search_scope):
            return StageAResult(
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                details={"reason": "entity_not_found_in_evidence", "target_entity": target_entity},
            )

        claim_canon, _ = normalize_ucum(field_val)
        if not claim_canon and claim.unit_raw:
            claim_canon, _ = normalize_ucum(claim.unit_raw)

        if not claim_canon:
            # Raw string fallback
            if field_val.strip().lower() in ev_text.lower():
                return StageAResult(label="SUPPORTED", confidence=1.0)
            return StageAResult(label="DISCREPANCY", contradiction_type="unit_mismatch", confidence=1.0)

        tokens = re.findall(r"[a-zA-Z/%^0-9*\[\]{}]+", ev_text)
        for tok in tokens:
            ev_canon, _ = normalize_ucum(tok)
            if ev_canon and ev_canon == claim_canon:
                return StageAResult(label="SUPPORTED", confidence=1.0)

        if any(normalize_ucum(tok)[0] is not None for tok in tokens):
            return StageAResult(label="DISCREPANCY", contradiction_type="unit_mismatch", confidence=1.0)

        return StageAResult(label="INSUFFICIENT_EVIDENCE", confidence=1.0)

    def _verify_reference_range(
        self, claim: Claim, ev_text: str, search_scope: str
    ) -> StageAResult:
        """Verify Tier 2 lab.reference_range claim deterministically with entity-awareness."""
        target_entity = self._extract_claim_entity(claim)
        field_val = self._extract_field_value(claim)

        if target_entity is not None and not self._is_entity_present_in_text(target_entity, search_scope):
            return StageAResult(
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                details={"reason": "entity_not_found_in_evidence", "target_entity": target_entity},
            )

        claim_clean = re.sub(r"\s+", "", field_val.lower())
        ev_clean = re.sub(r"\s+", "", ev_text.lower())

        if claim_clean in ev_clean:
            return StageAResult(label="SUPPORTED", confidence=1.0)

        # Check numerical interval format (low - high)
        m_claim = re.search(r"(\d+(?:\.\d+)?)\s*[-–—to]+\s*(\d+(?:\.\d+)?)", field_val)
        m_ev = re.search(r"(\d+(?:\.\d+)?)\s*[-–—to]+\s*(\d+(?:\.\d+)?)", ev_text)

        if m_claim and m_ev:
            c_low, c_high = float(m_claim.group(1)), float(m_claim.group(2))
            e_low, e_high = float(m_ev.group(1)), float(m_ev.group(2))
            if abs(c_low - e_low) < 1e-4 and abs(c_high - e_high) < 1e-4:
                return StageAResult(label="SUPPORTED", confidence=1.0)
            return StageAResult(
                label="DISCREPANCY",
                contradiction_type="value_mismatch",
                confidence=1.0,
                details={"claim_interval": [c_low, c_high], "evidence_interval": [e_low, e_high]},
            )

        if not m_ev:
            return StageAResult(label="INSUFFICIENT_EVIDENCE", confidence=1.0)

        return StageAResult(label="DISCREPANCY", contradiction_type="value_mismatch", confidence=1.0)

    def _verify_route(
        self, claim: Claim, ev_text: str, search_scope: str
    ) -> StageAResult:
        """Verify med.route claim against evidence with entity-awareness."""
        target_entity = self._extract_claim_entity(claim)
        field_val = self._extract_field_value(claim)

        # 1. Entity Compatibility Guard
        if target_entity is not None:
            if not self._is_entity_present_in_text(target_entity, search_scope):
                return StageAResult(
                    label="INSUFFICIENT_EVIDENCE",
                    confidence=1.0,
                    details={"reason": "entity_not_found_in_evidence", "target_entity": target_entity},
                )
            eval_text = ev_text
        else:
            eval_text = ev_text

        claim_route_raw = field_val.strip().lower()
        canon_claim_route = ROUTE_SYNONYMS.get(claim_route_raw, claim_route_raw)

        # Extract words from evidence
        ev_words = re.findall(r"\b[a-zA-Z]+\b", eval_text.lower())
        ev_canonical_routes = {ROUTE_SYNONYMS.get(w, w) for w in ev_words}

        if canon_claim_route in ev_canonical_routes or claim_route_raw in eval_text.lower():
            return StageAResult(label="SUPPORTED", confidence=1.0)

        # Check if an alternative recognized route token is present in evidence
        recognized_routes_in_ev = ev_canonical_routes.intersection(set(ROUTE_SYNONYMS.values()))
        if recognized_routes_in_ev:
            return StageAResult(
                label="DISCREPANCY",
                contradiction_type="value_mismatch",
                confidence=1.0,
                details={"claim_route": canon_claim_route, "evidence_routes": list(recognized_routes_in_ev)},
            )

        return StageAResult(label="INSUFFICIENT_EVIDENCE", confidence=1.0)

    def _verify_frequency(
        self, claim: Claim, ev_text: str, search_scope: str
    ) -> StageAResult:
        """Verify med.frequency claim against evidence with entity-awareness."""
        target_entity = self._extract_claim_entity(claim)
        field_val = self._extract_field_value(claim)

        # 1. Entity Compatibility Guard
        if target_entity is not None:
            if not self._is_entity_present_in_text(target_entity, search_scope):
                return StageAResult(
                    label="INSUFFICIENT_EVIDENCE",
                    confidence=1.0,
                    details={"reason": "entity_not_found_in_evidence", "target_entity": target_entity},
                )
            eval_text = ev_text
        else:
            eval_text = ev_text

        claim_freq_raw = field_val.strip().lower()
        canon_claim_freq = FREQUENCY_SYNONYMS.get(claim_freq_raw, claim_freq_raw)
        ev_lower = eval_text.lower()

        if claim_freq_raw in ev_lower or canon_claim_freq in ev_lower:
            return StageAResult(label="SUPPORTED", confidence=1.0)

        # Check all matching frequency synonyms in evidence
        matching_canons = set()
        discrepant_canons = set()
        for k, canon in FREQUENCY_SYNONYMS.items():
            if re.search(r"\b" + re.escape(k) + r"\b", ev_lower):
                if canon == canon_claim_freq:
                    matching_canons.add(canon)
                else:
                    discrepant_canons.add(canon)

        if matching_canons:
            return StageAResult(label="SUPPORTED", confidence=1.0)
        if discrepant_canons:
            return StageAResult(
                label="DISCREPANCY",
                contradiction_type="value_mismatch",
                confidence=1.0,
                details={"claim_freq": canon_claim_freq, "evidence_freq": list(discrepant_canons)},
            )

        # Also search surrounding context
        if canon_claim_freq in search_scope or claim_freq_raw in search_scope:
            return StageAResult(label="SUPPORTED", confidence=1.0)

        return StageAResult(label="INSUFFICIENT_EVIDENCE", confidence=1.0)

    def _verify_duration(
        self, claim: Claim, ev_text: str, search_scope: str
    ) -> StageAResult:
        """Verify Tier 2 med.duration claim deterministically with entity-awareness."""
        target_entity = self._extract_claim_entity(claim)
        field_val = self._extract_field_value(claim)

        if target_entity is not None and not self._is_entity_present_in_text(target_entity, search_scope):
            return StageAResult(
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                details={"reason": "entity_not_found_in_evidence", "target_entity": target_entity},
            )

        claim_dur = field_val.strip().lower()
        if claim_dur in ev_text.lower():
            return StageAResult(label="SUPPORTED", confidence=1.0)

        # Compare numeric duration and unit (e.g. 7 days vs 10 days)
        m_claim = re.search(r"(\d+)\s*(day|week|month|year)", claim_dur)
        m_ev = re.search(r"(\d+)\s*(day|week|month|year)", ev_text.lower())
        if m_claim and m_ev:
            c_val, c_unit = int(m_claim.group(1)), m_claim.group(2)
            e_val, e_unit = int(m_ev.group(1)), m_ev.group(2)
            if c_val == e_val and c_unit == e_unit:
                return StageAResult(label="SUPPORTED", confidence=1.0)
            return StageAResult(
                label="DISCREPANCY",
                contradiction_type="value_mismatch",
                confidence=1.0,
            )

        if not m_ev:
            return StageAResult(label="INSUFFICIENT_EVIDENCE", confidence=1.0)

        return StageAResult(label="DISCREPANCY", contradiction_type="value_mismatch", confidence=1.0)

    def _verify_test_name(
        self, claim: Claim, ev_text: str, search_scope: str
    ) -> StageAResult:
        """Verify Tier 2 lab.test_name claim deterministically with catalog alias awareness."""
        field_val = self._extract_field_value(claim)
        aliases = self._get_entity_aliases(field_val, code=claim.normalized_code)
        norm_ev = self._normalize_lexical(ev_text)

        for alias in aliases:
            norm_alias = self._normalize_lexical(alias)
            if not norm_alias:
                continue

            # Short acronyms/tokens (e.g. <=3 chars like 'WBC', 'PT', 'ALP') require word boundary
            if len(norm_alias) <= 3:
                if re.search(r"\b" + re.escape(norm_alias) + r"\b", norm_ev):
                    return StageAResult(
                        label="SUPPORTED",
                        confidence=1.0,
                        normalized_code_agreement=True if claim.normalized_code else None,
                    )
            else:
                if norm_alias in norm_ev or norm_ev in norm_alias:
                    return StageAResult(
                        label="SUPPORTED",
                        confidence=1.0,
                        normalized_code_agreement=True if claim.normalized_code else None,
                    )
                if re.search(r"\b" + re.escape(norm_alias) + r"\b", norm_ev):
                    return StageAResult(
                        label="SUPPORTED",
                        confidence=1.0,
                        normalized_code_agreement=True if claim.normalized_code else None,
                    )

        return StageAResult(
            label="DISCREPANCY",
            contradiction_type="wrong_entity",
            confidence=1.0,
            normalized_code_agreement=False if claim.normalized_code else None,
        )

    def _verify_med_name(
        self, claim: Claim, ev_text: str, search_scope: str
    ) -> StageAResult:
        """Verify Tier 1 med.name claim deterministically when possible, else defer to Stage B."""
        field_val = self._extract_field_value(claim)
        norm_claim = self._normalize_lexical(field_val)
        norm_ev = self._normalize_lexical(ev_text)

        # Verbatim match or containment
        if norm_claim in norm_ev:
            return StageAResult(
                label="SUPPORTED",
                confidence=1.0,
                normalized_code_agreement=True if claim.normalized_code else None,
            )

        # If completely unrelated and no letters in common, flag discrepancy
        # Otherwise return None so ModernBERT Stage B performs dense semantic verification
        return StageAResult(label=None, confidence=0.0)

    # ─── HELPER METHODS ──────────────────────────────────────────────────────

    @classmethod
    def _extract_claim_entity(cls, claim: Claim) -> Optional[str]:
        """Extract clinical entity name (medication or lab test) from claim if present."""
        val = claim.value.strip()
        if ":" not in val:
            return None
        prefix, after = val.split(":", 1)
        prefix = prefix.strip()
        if prefix.lower() in ("medication", "drug", "rx", "test name", "lab test"):
            return after.strip()
        qualifiers = ("dose", "frequency", "route", "flag", "value", "unit", "reference range", "duration")
        prefix_lower = prefix.lower()
        for q in qualifiers:
            if prefix_lower.endswith(" " + q):
                return prefix[:-len(q)-1].strip()
        return prefix

    @classmethod
    def _extract_field_value(cls, claim: Claim) -> str:
        """Extract field value from claim, stripping entity prefix if present."""
        val = claim.value.strip()
        if ":" not in val:
            return val
        prefix, after = val.split(":", 1)
        prefix = prefix.strip()
        if prefix.lower() in ("medication", "drug", "rx", "test name", "lab test"):
            return after.strip()
        return after.strip()

    @classmethod
    def _get_entity_aliases(cls, entity: str, code: Optional[str] = None) -> list[str]:
        """Return search alias strings for an entity name, including approved catalog synonyms."""
        _init_catalog_aliases()
        aliases = [entity.strip()]
        m = re.search(r"^(.*?)\s*\((.*?)\)$", entity.strip())
        if m:
            if m.group(1).strip():
                aliases.append(m.group(1).strip())
            if m.group(2).strip():
                aliases.append(m.group(2).strip())

        ent_lower = entity.strip().lower()
        if ent_lower in _CANON_TO_ALIASES:
            for a in _CANON_TO_ALIASES[ent_lower]:
                if a not in aliases:
                    aliases.append(a)

        if code and code in _CODE_TO_ALIASES:
            for a in _CODE_TO_ALIASES[code]:
                if a not in aliases:
                    aliases.append(a)

        return aliases

    @classmethod
    def _is_entity_present_in_text(cls, entity: str, text: str) -> bool:
        """Deterministically check if target entity is present in text."""
        if not entity or not text:
            return False
        aliases = cls._get_entity_aliases(entity)
        text_lower = text.lower()
        for a in aliases:
            a_lower = a.lower()
            if re.search(r"\b" + re.escape(a_lower) + r"\b", text_lower):
                return True
            clean_a = re.sub(r"[^a-z0-9]", "", a_lower)
            clean_t = re.sub(r"[^a-z0-9]", "", text_lower)
            if len(clean_a) >= 4 and clean_a in clean_t:
                return True
        return False

    @classmethod
    def _extract_entity_local_block(cls, entity: str, text: str) -> str:
        """Extract the specific row or local sentence block belonging to an entity."""
        aliases = cls._get_entity_aliases(entity)
        lines = text.splitlines()
        if len(lines) > 1:
            matched_lines = []
            for i, line in enumerate(lines):
                line_lower = line.lower()
                matched = False
                for a in aliases:
                    a_lower = a.lower()
                    if re.search(r"\b" + re.escape(a_lower) + r"\b", line_lower):
                        matched = True
                        break
                    clean_a = re.sub(r"[^a-z0-9]", "", a_lower)
                    clean_l = re.sub(r"[^a-z0-9]", "", line_lower)
                    if len(clean_a) >= 4 and clean_a in clean_l:
                        matched = True
                        break
                if matched:
                    matched_lines.append(line)
                    # If this line has no numbers, look ahead up to 2 lines
                    if not re.search(r"\d", line):
                        for j in range(i + 1, min(len(lines), i + 3)):
                            matched_lines.append(lines[j])
                            if re.search(r"\d", lines[j]):
                                break
            if matched_lines:
                return "\n".join(matched_lines)

        # Single line or fallback: locate entity and take local window
        text_lower = text.lower()
        for a in aliases:
            m = re.search(r"\b" + re.escape(a.lower()) + r"\b", text_lower)
            if m:
                start = max(0, m.start() - 30)
                end = min(len(text), m.end() + 100)
                return text[start:end]

        return text

    @staticmethod
    def _extract_numeric_value(claim: Claim) -> Optional[float]:
        """Extract float numeric value from claim."""
        if claim.value_numeric is not None:
            return float(claim.value_numeric)
        m = re.search(r"[-+]?\d+(?:\.\d+)?", claim.value)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_all_numbers(text: str) -> list[float]:
        """Extract all float numbers from text."""
        matches = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
        results = []
        for m in matches:
            try:
                results.append(float(m))
            except ValueError:
                pass
        return results

    @classmethod
    def _extract_primary_lab_numbers(cls, text: str) -> list[float]:
        """Extract candidate measured lab value numbers, filtering out reference ranges and dates."""
        cleaned = text
        # Filter dates like 10/12/2026 or 2026-09-29
        cleaned = re.sub(r"\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b", " ", cleaned)
        # Filter times like 14:30
        cleaned = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", " ", cleaned)
        # Filter two-number intervals (reference ranges e.g. 8.5 - 10.2, 70-99, 12.0 - 15.5)
        cleaned = re.sub(r"\b\d+(?:\.\d+)?\s*[-–—to]+\s*\d+(?:\.\d+)?\b", " ", cleaned)
        # Filter reference range patterns in brackets/parentheses
        cleaned = re.sub(
            r"\((?:ref(?:erence)?(?:\s+range)?\s*:?)?[^)]*\)",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\[(?:ref(?:erence)?(?:\s+range)?\s*:?)?[^\]]*\]",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cls._extract_all_numbers(cleaned)

    @staticmethod
    def _detect_negation(ev_text: str, context_text: Optional[str] = None) -> bool:
        """Detect whether evidence or its immediate local window contains a negation trigger."""
        ev_lower = ev_text.lower()
        # 1. Primary check: negation trigger directly inside evidence text
        if any(re.search(pat, ev_lower) for pat in NEGATION_PATTERNS):
            return True

        # 2. Local window check in context_text immediately preceding or enclosing ev_text
        if context_text and ev_lower in context_text.lower():
            ctx_lower = context_text.lower()
            idx = ctx_lower.find(ev_lower)
            if idx != -1:
                # Check tight prefix (up to 40 chars before the evidence mention, within the same clause)
                prefix_start = max(0, idx - 40)
                prefix = ctx_lower[prefix_start:idx]
                for boundary in [".", "!", "?", ";"]:
                    if boundary in prefix:
                        prefix = prefix.rsplit(boundary, 1)[-1]
                if any(re.search(pat, prefix) for pat in NEGATION_PATTERNS):
                    return True

        return False

    @staticmethod
    def _normalize_lexical(text: str) -> str:
        """Normalize string for lexical comparison."""
        s = text.lower().strip()
        s = re.sub(r"[^\w\s]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def verify_medication_group_integrity(
        self,
        claims: Sequence[Claim],
        verification_results: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply fail-closed topological integrity check across medication claims.

        Authority: Phase 5.2.3 Sub-Milestone 4.1
        Enforces parent-child verification dependencies:
        If a parent medication anchor ('med.name') fails verification or is unverified,
        dependent medication attributes ('med.dose', 'med.route', 'med.frequency', 'med.duration')
        in that same medication group cannot remain independently verified or eligible for unreviewed export.

        Args:
            claims: Sequence of Claim instances containing group_id annotations.
            verification_results: Mapping of claim_id -> StageAResult (or VerificationResult / dict).

        Returns:
            Updated dictionary mapping claim_id -> verification result where dependent
            attributes of unverified parent anchors are transitioned to fail-closed.
        """
        updated_results = dict(verification_results)

        # 1. Index claims by group_id and identify parent anchors ('med.name')
        group_anchors: dict[str, list[Claim]] = {}
        for c in claims:
            gid = getattr(c, "group_id", None)
            if gid and gid != NO_ANCHOR_SENTINEL and c.field_type == "med.name":
                group_anchors.setdefault(gid, []).append(c)

        # 2. Evaluate each dependent medication attribute claim
        for c in claims:
            if c.field_type not in MED_ATTRIBUTE_FIELDS:
                continue

            cid = c.claim_id
            gid = getattr(c, "group_id", None)

            anchor_claims = group_anchors.get(gid, []) if (gid and gid != NO_ANCHOR_SENTINEL) else []

            is_anchor_verified = False
            anchor_failure_detail = None
            parent_anchor_id = None

            if not anchor_claims:
                is_anchor_verified = False
                anchor_failure_detail = "MISSING_ANCHOR"
            elif len(anchor_claims) > 1:
                is_anchor_verified = False
                anchor_failure_detail = "AMBIGUOUS_MULTI_ANCHOR"
                parent_anchor_id = anchor_claims[0].claim_id
            else:
                parent_anchor = anchor_claims[0]
                parent_anchor_id = parent_anchor.claim_id
                parent_res = verification_results.get(parent_anchor_id)

                if parent_res is None:
                    is_anchor_verified = False
                    anchor_failure_detail = "MISSING_RESULT"
                else:
                    p_label = getattr(parent_res, "label", None)
                    if hasattr(p_label, "value"):
                        p_label = p_label.value
                    elif p_label is not None:
                        p_label = str(p_label).upper()

                    if p_label in ("SUPPORTED", "VERIFIED"):
                        is_anchor_verified = True
                    elif p_label is None:
                        is_anchor_verified = False
                        anchor_failure_detail = "PENDING_VERIFICATION"
                    else:
                        is_anchor_verified = False
                        anchor_failure_detail = f"ANCHOR_{p_label}"

            # 3. If parent anchor is verified, child retains its independent result
            if is_anchor_verified:
                continue

            # 4. If parent anchor is NOT verified, child fails closed with PARENT_ANCHOR_UNVERIFIED
            existing_res = updated_results.get(cid)

            if existing_res is None:
                updated_results[cid] = StageAResult(
                    label="INSUFFICIENT_EVIDENCE",
                    contradiction_type=None,
                    confidence=1.0,
                    details={
                        "reason": REASON_PARENT_ANCHOR_UNVERIFIED,
                        "anchor_failure_detail": anchor_failure_detail,
                        "parent_anchor_claim_id": parent_anchor_id,
                    },
                )
            elif isinstance(existing_res, StageAResult):
                if existing_res.label == "DISCREPANCY":
                    # Preserve original discrepancy and contradiction type
                    new_details = dict(existing_res.details)
                    new_details["parent_anchor_unverified"] = True
                    new_details["parent_anchor_claim_id"] = parent_anchor_id
                    new_details["anchor_failure_detail"] = anchor_failure_detail
                    updated_results[cid] = StageAResult(
                        label=existing_res.label,
                        contradiction_type=existing_res.contradiction_type,
                        confidence=existing_res.confidence,
                        normalized_code_agreement=existing_res.normalized_code_agreement,
                        details=new_details,
                    )
                else:
                    # SUPPORTED, INSUFFICIENT_EVIDENCE, or None (pending) -> Fail-closed
                    new_details = dict(existing_res.details)
                    new_details["original_label"] = existing_res.label
                    new_details["reason"] = REASON_PARENT_ANCHOR_UNVERIFIED
                    new_details["anchor_failure_detail"] = anchor_failure_detail
                    new_details["parent_anchor_claim_id"] = parent_anchor_id
                    updated_results[cid] = StageAResult(
                        label="INSUFFICIENT_EVIDENCE",
                        contradiction_type=None,
                        confidence=1.0,
                        normalized_code_agreement=existing_res.normalized_code_agreement,
                        details=new_details,
                    )
            elif hasattr(existing_res, "verification_id"):
                # VerificationResult instance
                res_label = str(getattr(existing_res, "label", "")).upper()
                if res_label == "DISCREPANCY":
                    # Preserve original discrepancy
                    pass
                else:
                    from clindoc.schema.verification import VerificationResult
                    updated_results[cid] = VerificationResult(
                        verification_id=existing_res.verification_id,
                        claim_id=c.claim_id,
                        label="INSUFFICIENT_EVIDENCE",
                        confidence=0.0,
                        stage_reached=existing_res.stage_reached,
                        stage_a_label="INSUFFICIENT_EVIDENCE",
                        stage_b_label=existing_res.stage_b_label,
                        stage_b_raw_confidence=existing_res.stage_b_raw_confidence,
                        contradiction_type=None,
                        normalized_code_agreement=existing_res.normalized_code_agreement,
                        rationale=None,
                        evidence_id=c.evidence_id,
                        escalated=existing_res.escalated,
                        is_fallback=True,
                        fallback_reason=REASON_PARENT_ANCHOR_UNVERIFIED,
                    )
            elif isinstance(existing_res, dict):
                res_label = str(existing_res.get("label", "")).upper()
                if res_label != "DISCREPANCY":
                    new_dict = dict(existing_res)
                    new_dict["original_label"] = existing_res.get("label")
                    new_dict["label"] = "INSUFFICIENT_EVIDENCE"
                    new_dict["contradiction_type"] = None
                    new_dict["reason"] = REASON_PARENT_ANCHOR_UNVERIFIED
                    new_dict["anchor_failure_detail"] = anchor_failure_detail
                    new_dict["parent_anchor_claim_id"] = parent_anchor_id
                    updated_results[cid] = new_dict

        return updated_results

    def verify_claims(
        self,
        claims: Sequence[Claim],
        evidence_map: dict[str, Evidence],
        context_text_map: Optional[dict[str, str]] = None,
        context_text: Optional[str] = None,
        enforce_topological_integrity: bool = True,
    ) -> dict[str, StageAResult]:
        """Verify a collection of claims with deterministic Stage A rules and topological integrity.

        Args:
            claims: Sequence of extracted Claims.
            evidence_map: Lookup dictionary mapping evidence_id -> Evidence.
            context_text_map: Optional mapping of document_id or evidence_id -> context text.
            context_text: Optional global context text fallback.
            enforce_topological_integrity: If True (default), propagates parent anchor failures.

        Returns:
            Dictionary mapping claim_id -> StageAResult.
        """
        raw_results: dict[str, StageAResult] = {}
        for c in claims:
            ev = evidence_map.get(c.evidence_id)
            ctx = None
            if context_text_map:
                ctx = context_text_map.get(c.evidence_id) or context_text_map.get(c.document_id)
            if ctx is None:
                ctx = context_text

            raw_results[c.claim_id] = self.verify(c, evidence=ev, context_text=ctx)

        if enforce_topological_integrity:
            return self.verify_medication_group_integrity(claims, raw_results)
        return raw_results


def apply_medication_group_integrity(
    claims: Sequence[Claim],
    verification_results: dict[str, Any],
) -> dict[str, Any]:
    """Module-level entry point for verify_medication_group_integrity (Phase 5.2.3 Sub-Milestone 4.1)."""
    return DeterministicVerifier().verify_medication_group_integrity(claims, verification_results)
