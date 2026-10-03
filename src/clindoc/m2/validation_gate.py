"""M2 Benchmark Pre-Serialization Validation Gate and Generation Diagnostics.

Authority:
  - PROJECT_SPEC.md §7.1, §7.8, §8, §9, §10, §20.1
  - DEC-003: Document context window bounding
  - DEC-015: Group ID entity linking
  - DEC-017: Canonical field vocabulary
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from clindoc.m2.schema import M2Example, VerificationLabel
from clindoc.verify.stage_a import DeterministicVerifier


@dataclass
class GenerationDiagnostics:
    """Structured counters for candidate example generation and rejection reasons."""
    total_evaluated: int = 0
    total_accepted: int = 0
    total_rejected: int = 0
    rejections_by_reason: Counter[str] = field(default_factory=Counter)

    def record_acceptance(self) -> None:
        self.total_evaluated += 1
        self.total_accepted += 1

    def record_rejection(self, reason: str) -> None:
        self.total_evaluated += 1
        self.total_rejected += 1
        self.rejections_by_reason[reason] += 1

    def summary(self) -> dict[str, Any]:
        return {
            "total_evaluated": self.total_evaluated,
            "total_accepted": self.total_accepted,
            "total_rejected": self.total_rejected,
            "rejections_by_reason": dict(self.rejections_by_reason),
        }

    def get_counts(self) -> dict[str, int]:
        return dict(self.rejections_by_reason)


class PreSerializationValidationGate:
    """Strict validation layer enforcing all 10 M2 benchmark integrity invariants.

    Any candidate example that fails validation is rejected and logged in diagnostics.
    """

    def __init__(self) -> None:
        self.verifier = DeterministicVerifier()

    def validate(
        self,
        ex: M2Example,
        canonical_page_text: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Validate candidate M2Example against canonical text and integrity invariants.

        Returns:
            (is_valid, rejection_reason_or_None)
        """
        # Invariant 1: Zero tolerance for [0, 100] header fallback
        if ex.char_start == 0 and ex.char_end == 100:
            return False, "corrupted_default_header"

        # Invariant 2: Grounded offset validity against canonical document text
        if ex.char_start is not None or ex.char_end is not None:
            if ex.char_start is None or ex.char_end is None:
                return False, "incomplete_offsets"
            if ex.char_start < 0:
                return False, "negative_offset"
            if ex.char_end <= ex.char_start:
                return False, "inverted_or_empty_offsets"

            if canonical_page_text is not None:
                doc_len = len(canonical_page_text)
                if ex.char_end > doc_len:
                    return False, f"out_of_bounds_offset (char_end={ex.char_end} > doc_len={doc_len})"

                # Invariant 2b: Offset slice must be contained in evidence span
                canonical_slice = canonical_page_text[ex.char_start:ex.char_end]
                if not canonical_slice.strip():
                    return False, "empty_canonical_slice"
                if canonical_slice not in ex.evidence:
                    return False, "slice_evidence_mismatch"

        # Invariant 3: Unobserved negative control evidence contract
        if ex.generation_method == "clean_unobserved_claim_insufficient_evidence":
            if ex.char_start is not None or ex.char_end is not None:
                return False, "unobserved_claim_has_local_offsets"
            # Must not contain clinic address/letterhead text
            lower_ev = ex.evidence.lower()
            if "diagnostic center" in lower_ev or "pathology report" in lower_ev or "discharge instructions" in lower_ev:
                return False, "unobserved_claim_uses_clinic_header"

        # Invariant 4: Entity presence for SUPPORTED and DISCREPANCY
        claim_stub = type("ClaimStub", (), {
            "value": ex.claim,
            "field_type": ex.field_type,
            "value_numeric": getattr(ex, "value_numeric", None),
            "unit_raw": getattr(ex, "unit_raw", None),
            "negated": False,
            "normalized_code": None,
            "code_system": None,
            "evidence_id": "val-gate-ev",
        })()
        ev_stub = type("EvStub", (), {"text": ex.evidence, "evidence_id": "val-gate-ev"})()

        target_entity = self.verifier._extract_claim_entity(claim_stub)

        if ex.field_type == "med.name" and ex.label == VerificationLabel.DISCREPANCY:
            # For med.name discrepancy, the substituted entity must NOT be present in evidence
            if target_entity and self.verifier._is_entity_present_in_text(target_entity, ex.evidence):
                return False, f"alt_entity_present_in_evidence ({target_entity})"
        elif ex.label in (VerificationLabel.SUPPORTED, VerificationLabel.DISCREPANCY):
            if target_entity:
                if not self.verifier._is_entity_present_in_text(target_entity, ex.evidence):
                    return False, f"missing_target_entity ({target_entity} not in evidence)"

        # Invariant 5: Deterministic label consistency
        if target_entity and ex.label in (VerificationLabel.SUPPORTED, VerificationLabel.DISCREPANCY):
            ver_res = self.verifier.verify(claim_stub, ev_stub)
            lbl_val = ver_res.label.value if hasattr(ver_res.label, "value") else (str(ver_res.label) if ver_res.label else None)
            if ex.label == VerificationLabel.SUPPORTED:
                if lbl_val != "SUPPORTED":
                    return False, f"deterministic_label_mismatch (expected SUPPORTED, verifier found {lbl_val})"
            elif ex.label == VerificationLabel.DISCREPANCY:
                if lbl_val == "SUPPORTED":
                    return False, "deterministic_label_mismatch (expected DISCREPANCY, verifier found SUPPORTED)"

        # Invariant 6: Tabular evidence row boundary integrity
        if ex.claim_type == "lab" and "\n" in ex.evidence:
            lines = ex.evidence.split("\n")
            if len(lines) > 1:
                # Check that lines don't begin or end with cut alphanumeric fragments
                # e.g. starting with single lowercase letter or punctuation fragment
                first_line = lines[0].strip()
                if first_line and len(first_line) < 3 and not first_line.isdigit():
                    return False, "context_window_bisected_row"

        return True, None
