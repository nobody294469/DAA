"""Verification Cascade Engine.

Authority:
  - PROJECT_SPEC §5 #8, §7.6, §10, §12.1, §12.2, §17.1, §21.5
  - DEC-003: Document-local context window bounding
  - DEC-005: Stage B escalation threshold (provisional default: 0.60)
  - DEC-009: Temperature scaling calibration for Stage B
  - DEC-013: Grammar-constrained Stage C output
  - DEC-018: 1D confidence calibration for Stage C
  - DEC-019: Field verification tiering (Tier 1 vs Tier 2)

Orchestrates the multi-stage verification pipeline:
  Claim + Evidence + Local Context
               ↓
  Stage A: Deterministic Rule Verifier
               ↓
  Stage B: ModernBERT Semantic Verifier (Tier 1)
               ↓  [escalation condition met]
  Stage C: Selective LLM Judge
               ↓
  Canonical VerificationResult (§10, schemas/verification_result.json)
"""
from __future__ import annotations

from typing import Optional, Sequence, Union

from clindoc.extract.contracts import (
    Claim,
    Evidence,
    TIER_1_FIELDS,
    TIER_2_FIELDS,
)
from clindoc.m2.context import extract_context_window
from clindoc.schema.verification import (
    ContradictionType,
    VerificationLabel,
    VerificationResult,
)
from clindoc.verify.stage_a import DeterministicVerifier, StageAResult
from clindoc.verify.stage_b import LearnedVerifier, StageBResult
from clindoc.verify.stage_c import StageCJudge, StageCResult
from clindoc.config import ProductionProfileError, RuntimeProfile, get_active_profile


class VerificationCascade:
    """The central verification decision cascade conforming to PROJECT_SPEC §12."""

    def __init__(
        self,
        stage_a: Optional[DeterministicVerifier] = None,
        stage_b: Optional[LearnedVerifier] = None,
        stage_c: Optional[StageCJudge] = None,
        always_stage_c: bool = False,  # For ablation / experimental comparison (§12.2)
        profile: Optional[Union[RuntimeProfile, str]] = None,
    ) -> None:
        self.profile = RuntimeProfile.from_string(profile) if profile else get_active_profile()
        if self.profile == RuntimeProfile.PRODUCTION:
            if stage_b is None:
                raise ProductionProfileError(
                    "Production VerificationCascade requires an explicitly configured, non-heuristic Stage B LearnedVerifier."
                )
            if getattr(stage_b, "allow_heuristic_fallback", True):
                raise ProductionProfileError(
                    "Production VerificationCascade requires Stage B with allow_heuristic_fallback=False."
                )
            if stage_c is None:
                raise ProductionProfileError(
                    "Production VerificationCascade requires an explicitly configured, non-mock Stage C StageCJudge."
                )
            if getattr(stage_c, "allow_mock_client", True):
                raise ProductionProfileError(
                    "Production VerificationCascade requires Stage C with allow_mock_client=False."
                )

        self.stage_a = stage_a or DeterministicVerifier()
        self.stage_b = stage_b or LearnedVerifier(profile=self.profile)
        self.stage_c = stage_c or StageCJudge(profile=self.profile)
        self.always_stage_c = always_stage_c
        self.last_stage_a_result: Optional[StageAResult] = None
        self.last_stage_b_result: Optional[StageBResult] = None
        self.last_stage_c_result: Optional[StageCResult] = None

        from clindoc.manifest import QualificationReport, QualificationStatus, evaluate_cascade_qualification
        self.qualification_report: QualificationReport = evaluate_cascade_qualification(self)

    @property
    def is_qualified(self) -> bool:
        """Check whether this cascade runs under the approved qualified production manifest."""
        return self.qualification_report.is_qualified

    @property
    def qualification_status(self) -> QualificationStatus:
        """Return the current qualification status (QUALIFIED or UNQUALIFIED)."""
        return self.qualification_report.status

    def verify_claim_all_stages(
        self,
        claim: Claim,
        evidence: Optional[Evidence] = None,
        page_text: Optional[str] = None,
    ) -> tuple[VerificationResult, Optional[StageAResult], Optional[StageBResult], Optional[StageCResult]]:
        """Verify a single Claim and return the canonical VerificationResult alongside all intermediate stage results."""
        res = self.verify_claim(claim=claim, evidence=evidence, page_text=page_text)
        return res, self.last_stage_a_result, self.last_stage_b_result, self.last_stage_c_result

    def verify_claim_detailed(
        self,
        claim: Claim,
        evidence: Optional[Evidence] = None,
        page_text: Optional[str] = None,
    ) -> tuple[VerificationResult, Optional[StageBResult]]:
        """Verify a single Claim and return both the canonical VerificationResult and StageBResult."""
        res = self.verify_claim(claim=claim, evidence=evidence, page_text=page_text)
        return res, self.last_stage_b_result

    def verify_claim(
        self,
        claim: Claim,
        evidence: Optional[Evidence] = None,
        page_text: Optional[str] = None,
    ) -> VerificationResult:
        """Verify a single Claim against Evidence and document text.

        Args:
            claim: Extracted and normalized Claim.
            evidence: Linked Evidence span.
            page_text: Verbatim full page text of the document for context window.

        Returns:
            Canonical VerificationResult adhering strictly to schemas/verification_result.json.
        """
        # 1. Missing Evidence Guard
        self.last_stage_a_result = None
        self.last_stage_b_result = None
        self.last_stage_c_result = None
        if evidence is None or not evidence.text or not evidence.text.strip():
            self.last_stage_a_result = StageAResult(
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                details={"reason": "missing_or_blank_evidence"},
            )
            ev_id = evidence.evidence_id if evidence else claim.evidence_id
            return VerificationResult(
                claim_id=claim.claim_id,
                label="INSUFFICIENT_EVIDENCE",
                confidence=1.0,
                stage_reached="A",
                stage_a_label="INSUFFICIENT_EVIDENCE",
                stage_b_label=None,
                stage_b_raw_confidence=None,
                contradiction_type=None,
                normalized_code_agreement=None,
                rationale=None,
                evidence_id=ev_id,
                escalated=False,
            )

        # 2. Extract Document Context Window (DEC-003)
        context_text = evidence.text
        if page_text and evidence.char_start >= 0 and evidence.char_end > evidence.char_start:
            is_tabular = claim.field_type.startswith("lab.")
            window_text, _, _ = extract_context_window(
                page_text=page_text,
                char_start=evidence.char_start,
                char_end=evidence.char_end,
                is_tabular=is_tabular,
            )
            if window_text:
                context_text = window_text

        # 3. Route by Field Verification Tier (DEC-019)
        field_type = claim.field_type

        # ─── TIER 2: Deterministic Verification (Stage A Only) ─────────────────
        if field_type in TIER_2_FIELDS and not self.always_stage_c:
            stage_a_res = self.stage_a.verify(claim, evidence, context_text=context_text)
            self.last_stage_a_result = stage_a_res
            if stage_a_res.label is not None:
                final_label = stage_a_res.label
                c_type: Optional[ContradictionType] = (
                    stage_a_res.contradiction_type if final_label == "DISCREPANCY" else None
                )
                if final_label == "DISCREPANCY" and c_type is None:
                    c_type = "value_mismatch"

                return VerificationResult(
                    claim_id=claim.claim_id,
                    label=final_label,
                    confidence=stage_a_res.confidence,
                    stage_reached="A",
                    stage_a_label=stage_a_res.label,
                    stage_b_label=None,
                    stage_b_raw_confidence=None,
                    contradiction_type=c_type,
                    normalized_code_agreement=stage_a_res.normalized_code_agreement,
                    rationale=None,
                    evidence_id=evidence.evidence_id,
                    escalated=False,
                )

        # ─── TIER 1: Learned Verification Cascade (Stages A, B, C) ───────────
        # Step A: Deterministic Pre-check
        stage_a_res = self.stage_a.verify(claim, evidence, context_text=context_text)
        self.last_stage_a_result = stage_a_res

        # Step B: Learned ModernBERT Verifier
        stage_b_res = self.stage_b.verify(
            claim=claim,
            evidence=evidence,
            stage_a_label=stage_a_res.label,
            context_text=context_text,
        )
        self.last_stage_b_result = stage_b_res

        should_escalate = stage_b_res.escalated or self.always_stage_c

        # Step C: Selective LLM Judge on Escalation
        if should_escalate:
            stage_c_res = self.stage_c.judge(
                claim=claim,
                evidence=evidence,
                context_text=context_text,
                stage_a_label=stage_a_res.label,
                stage_b_label=stage_b_res.label,
                stage_b_confidence=stage_b_res.calibrated_confidence,
            )
            self.last_stage_c_result = stage_c_res

            c_type = stage_c_res.contradiction_type if stage_c_res.label == "DISCREPANCY" else None
            if stage_c_res.label == "DISCREPANCY" and c_type is None:
                # Fallback to Stage A contradiction type or default
                c_type = stage_a_res.contradiction_type or self._default_contradiction_type(claim)

            return VerificationResult(
                claim_id=claim.claim_id,
                label=stage_c_res.label,
                confidence=stage_c_res.calibrated_confidence,
                stage_reached="C",
                stage_a_label=stage_a_res.label,
                stage_b_label=stage_b_res.label,
                stage_b_raw_confidence=stage_b_res.raw_confidence,
                contradiction_type=c_type,
                normalized_code_agreement=stage_a_res.normalized_code_agreement,
                rationale=stage_c_res.rationale,
                evidence_id=evidence.evidence_id,
                escalated=True,
            )

        # Not Escalated: Final Verdict from Stage B
        final_label = stage_b_res.label
        c_type = None
        if final_label == "DISCREPANCY":
            c_type = stage_a_res.contradiction_type or self._default_contradiction_type(claim)

        return VerificationResult(
            claim_id=claim.claim_id,
            label=final_label,
            confidence=stage_b_res.calibrated_confidence,
            stage_reached="B",
            stage_a_label=stage_a_res.label,
            stage_b_label=stage_b_res.label,
            stage_b_raw_confidence=stage_b_res.raw_confidence,
            contradiction_type=c_type,
            normalized_code_agreement=stage_a_res.normalized_code_agreement,
            rationale=None,
            evidence_id=evidence.evidence_id,
            escalated=False,
        )

    def verify_claims(
        self,
        claims: Sequence[Claim],
        evidence_map: dict[str, Evidence],
        page_text_map: Optional[dict[str, str]] = None,
    ) -> list[VerificationResult]:
        """Verify a batch of claims against an evidence lookup map.

        Args:
            claims: Collection of extracted claims.
            evidence_map: Map from evidence_id -> Evidence.
            page_text_map: Optional map from document_id or page_number -> page text.

        Returns:
            List of canonical VerificationResult objects.
        """
        results: list[VerificationResult] = []
        for claim in claims:
            ev = evidence_map.get(claim.evidence_id)
            page_text = None
            if page_text_map and ev:
                page_text = page_text_map.get(ev.document_id) or page_text_map.get(str(ev.page_number))

            result = self.verify_claim(claim=claim, evidence=ev, page_text=page_text)
            results.append(result)
        return results

    @staticmethod
    def _default_contradiction_type(claim: Claim) -> ContradictionType:
        """Infer appropriate contradiction type based on field type."""
        if claim.field_type in ("lab.unit", "unit_raw"):
            return "unit_mismatch"
        elif claim.field_type == "med.dose":
            return "dose_mismatch"
        elif claim.field_type in ("med.name", "lab.test_name"):
            return "wrong_entity"
        return "value_mismatch"
