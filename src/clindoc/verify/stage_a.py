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
import re
from typing import Any, Optional

from clindoc.extract.contracts import (
    Claim,
    Evidence,
    FieldType,
    TIER_1_FIELDS,
    TIER_2_FIELDS,
)
from clindoc.normalize.ucum import normalize_ucum
from clindoc.schema.verification import ContradictionType, VerificationLabel

# Standard clinical negation triggers (DEC-003, §20.1)
NEGATION_PATTERNS: tuple[str, ...] = (
    r"\bno\b",
    r"\bnot\b",
    r"\bdenies\b",
    r"\bnegative\s+for\b",
    r"\bwithout\b",
    r"\babsent\b",
    r"\bdiscontinued\b",
    r"\bnone\b",
    r"\bnever\b",
    r"\bzero\b",
    r"\bruled\s+out\b",
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
        """Verify lab.value claim against evidence text."""
        claim_num = self._extract_numeric_value(claim)
        if claim_num is None:
            # Non-numeric lab value (e.g. "positive", "negative")
            if claim.value.strip().lower() in ev_text.lower():
                return StageAResult(label="SUPPORTED", confidence=1.0)
            return StageAResult(
                label="DISCREPANCY",
                contradiction_type="value_mismatch",
                confidence=1.0,
                details={"expected": claim.value, "evidence_text": ev_text},
            )

        # Extract primary candidate value numbers, excluding reference ranges
        ev_numbers = self._extract_primary_lab_numbers(ev_text)
        if not ev_numbers:
            return StageAResult(
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                details={"reason": "no_primary_numbers_found_in_evidence", "claim_num": claim_num},
            )

        # Check unit mismatch first if unit is present
        unit_discrepancy = self._check_unit_consistency(claim, ev_text)
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
        """Verify med.dose claim against evidence text."""
        claim_num = self._extract_numeric_value(claim)
        if claim_num is None:
            if claim.value.strip().lower() in ev_text.lower():
                return StageAResult(label="SUPPORTED", confidence=1.0)
            return StageAResult(
                label="DISCREPANCY",
                contradiction_type="dose_mismatch",
                confidence=1.0,
            )

        ev_numbers = self._extract_all_numbers(ev_text)
        if not ev_numbers:
            return StageAResult(
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                details={"reason": "no_dose_numbers_found_in_evidence"},
            )

        # Check unit mismatch first
        unit_discrepancy = self._check_unit_consistency(claim, ev_text)
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
        # Common medical units
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
        """Verify lab.flag claim against evidence tokens."""
        val_clean = claim.value.strip().lower()

        # Categorize claim flag
        claim_cat = self._categorize_flag(val_clean)
        if not claim_cat:
            return StageAResult(label=None, confidence=0.0)

        # Search for flag tokens in evidence
        ev_tokens = [t.strip(".,;:()[]{}\"'-") for t in re.split(r"[\s,|]+", ev_text.lower()) if t.strip()]
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
        """Verify Tier 2 lab.unit claim deterministically via UCUM."""
        claim_canon, _ = normalize_ucum(claim.value)
        if not claim_canon and claim.unit_raw:
            claim_canon, _ = normalize_ucum(claim.unit_raw)

        if not claim_canon:
            # Raw string fallback
            if claim.value.strip().lower() in ev_text.lower():
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
        """Verify Tier 2 lab.reference_range claim deterministically."""
        claim_clean = re.sub(r"\s+", "", claim.value.lower())
        ev_clean = re.sub(r"\s+", "", ev_text.lower())

        if claim_clean in ev_clean:
            return StageAResult(label="SUPPORTED", confidence=1.0)

        # Check numerical interval format (low - high)
        m_claim = re.search(r"(\d+(?:\.\d+)?)\s*[-–—to]+\s*(\d+(?:\.\d+)?)", claim.value)
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
        """Verify med.route claim against evidence."""
        claim_route_raw = claim.value.strip().lower()
        canon_claim_route = ROUTE_SYNONYMS.get(claim_route_raw, claim_route_raw)

        # Extract words from evidence
        ev_words = re.findall(r"\b[a-zA-Z]+\b", ev_text.lower())
        ev_canonical_routes = {ROUTE_SYNONYMS.get(w, w) for w in ev_words}

        if canon_claim_route in ev_canonical_routes or claim_route_raw in ev_text.lower():
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
        """Verify med.frequency claim against evidence."""
        claim_freq_raw = claim.value.strip().lower()
        canon_claim_freq = FREQUENCY_SYNONYMS.get(claim_freq_raw, claim_freq_raw)
        ev_lower = ev_text.lower()

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
        """Verify Tier 2 med.duration claim deterministically."""
        claim_dur = claim.value.strip().lower()
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
        """Verify Tier 2 lab.test_name claim deterministically."""
        norm_claim = self._normalize_lexical(claim.value)
        norm_ev = self._normalize_lexical(ev_text)

        if norm_claim in norm_ev or norm_ev in norm_claim:
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
        norm_claim = self._normalize_lexical(claim.value)
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
        # Filter reference range patterns:
        # e.g., (Reference 70 - 99), (Ref: 70-99), [Ref: 70-99], (70 - 99), (70.0 - 99.0)
        cleaned = re.sub(
            r"\((?:ref(?:erence)?(?:\s+range)?\s*:?)?\s*\d+(?:\.\d+)?\s*[-–—to]+\s*\d+(?:\.\d+)?[^)]*\)",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\[(?:ref(?:erence)?(?:\s+range)?\s*:?)?\s*\d+(?:\.\d+)?\s*[-–—to]+\s*\d+(?:\.\d+)?[^\]]*\]",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\bref(?:erence)?(?:\s+range)?\s*:?\s*\d+(?:\.\d+)?\s*[-–—to]+\s*\d+(?:\.\d+)?",
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
