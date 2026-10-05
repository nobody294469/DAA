"""Triage Policy Engine (PRODUCTIZATION_SPEC §7.2, PRODUCTIZATION_DECISIONS PD-002).

Authority:
  - PRODUCTIZATION_SPEC.md §4.3 (triage_policies logical model), §7.1, §7.2 (Triage policy table)
  - PRODUCTIZATION_DECISIONS.md PD-002 (Mode 0: 100% human review for all claims)

Rules:
  1. Under Mode 0 (default / launch MVP), zero claims or documents may become autonomously exportable.
  2. Claims are categorized into:
     - AUTO_PROCESSED_AGREED: Stage A/B high-confidence agreement on SUPPORTED, no escalation,
       evidence present, normalization OK, no injection flag, OCR confidence adequate.
       Eligible for bulk confirmation in Mode 0 ONLY after opening evidence view.
     - HUMAN_REVIEW_REQUIRED: All discrepancies, Stage C resolutions, fallbacks, missing evidence,
       low OCR confidence, uncoded concepts, injection flags, and identity mismatches.
  3. Risk priority ordering is deterministic to feed the review queue (highest risk first).
  4. Provenance: Every classification retains the active policy version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Optional, Sequence
import uuid

from sqlalchemy.orm import Session

from clindoc.db.enums import (
    ClaimReviewState,
    ContradictionType,
    IdentityVerificationStatus,
    TriageCategory,
    TriagePolicyMode,
    TriageRiskTier,
    VerificationLabel,
    VerificationStage,
)
from clindoc.db.models import (
    Claim,
    Document,
    Evidence,
    TriagePolicy,
    VerificationResult,
)

logger = logging.getLogger(__name__)

# ─── CANONICAL MODE 0 CONSTANTS ───────────────────────────────────────────────

DEFAULT_MODE_0_POLICY_VERSION: str = "1.0.0-mode0"
DEFAULT_MODE_0_POLICY_NAME: str = "Mode 0: Full Human Review Policy"
MODE_0_POLICY_UUID: uuid.UUID = uuid.UUID("a0000000-0000-0000-0000-000000000085")

DEFAULT_MODE_0_CONFIG: dict[str, Any] = {
    "mode": TriagePolicyMode.MODE_0.value,
    "require_100_percent_human_decision": True,
    "allow_autonomous_export": False,
    "allow_bulk_confirm_agreed": True,
    "require_evidence_opened_for_bulk": True,
    "ocr_confidence_threshold": 0.70,
    "stage_b_confidence_threshold": 0.60,
    "qa_sampling_rate": 0.0,
}


# ─── DATA CONTRACTS ──────────────────────────────────────────────────────────

@dataclass
class ClaimTriageInput:
    """Input parameters for triaging an individual clinical claim."""

    claim_id: uuid.UUID
    field_type: str
    value: str
    normalized_code: Optional[str] = None
    code_system: Optional[str] = None
    verification_label: Optional[str] = None  # SUPPORTED, REFUTED, INSUFFICIENT_EVIDENCE
    stage_reached: Optional[str] = None       # STAGE_A, STAGE_B, STAGE_C
    escalated: bool = False
    is_fallback: bool = False
    confidence: float = 0.0
    contradiction_type: Optional[str] = None
    ocr_confidence: Optional[float] = None
    has_injection_flag: bool = False
    has_identity_mismatch: bool = False
    evidence_missing: bool = False
    review_state: Optional[str] = None

    @classmethod
    def from_orm(
        cls,
        claim: Claim,
        verification_result: Optional[VerificationResult] = None,
        evidence: Optional[Evidence] = None,
        flags: Optional[dict[str, Any]] = None,
    ) -> ClaimTriageInput:
        """Construct triage input adapter from relational database models."""
        flags = flags or {}

        # 1. Resolve OCR confidence from evidence column or payload if available
        ocr_conf: Optional[float] = flags.get("ocr_confidence")
        if ocr_conf is None and evidence is not None:
            if hasattr(evidence, "ocr_confidence") and evidence.ocr_confidence is not None:
                ocr_conf = float(evidence.ocr_confidence)
            elif evidence.payload:
                ocr_conf = evidence.payload.get("ocr_confidence")

        # 2. Extract verification details
        v_label: Optional[str] = None
        v_stage: Optional[str] = None
        v_escalated: bool = False
        v_fallback: bool = False
        v_confidence: float = 0.0
        v_contra: Optional[str] = None

        if verification_result is not None:
            v_label = (
                verification_result.label.value
                if hasattr(verification_result.label, "value")
                else str(verification_result.label)
            )
            stage_val = (
                getattr(verification_result, "verification_stage", None)
                or getattr(verification_result, "stage_reached", None)
            )
            v_stage = (
                stage_val.value
                if hasattr(stage_val, "value")
                else str(stage_val) if stage_val is not None else None
            )
            v_escalated = bool(verification_result.escalated)
            v_fallback = bool(verification_result.is_fallback)
            v_confidence = float(verification_result.calibrated_confidence or 0.0)
            if verification_result.contradiction_type:
                v_contra = (
                    verification_result.contradiction_type.value
                    if hasattr(verification_result.contradiction_type, "value")
                    else str(verification_result.contradiction_type)
                )

        # 3. Check review state
        r_state = (
            claim.review_state.value
            if hasattr(claim.review_state, "value")
            else str(claim.review_state)
        )

        return cls(
            claim_id=claim.id,
            field_type=claim.field_type,
            value=claim.value,
            normalized_code=claim.normalized_code,
            code_system=claim.code_system,
            verification_label=v_label,
            stage_reached=v_stage,
            escalated=v_escalated,
            is_fallback=v_fallback,
            confidence=v_confidence,
            contradiction_type=v_contra,
            ocr_confidence=ocr_conf,
            has_injection_flag=bool(flags.get("has_injection_flag", False)),
            has_identity_mismatch=bool(flags.get("has_identity_mismatch", False)),
            evidence_missing=(claim.evidence_id is None and evidence is None),
            review_state=r_state,
        )


@dataclass
class ClaimTriageResult:
    """Deterministic triage verdict and priority ranking for an individual claim."""

    claim_id: uuid.UUID
    field_type: str
    category: TriageCategory
    risk_tier: TriageRiskTier
    priority_rank: int  # 1 (highest risk / first in queue) to 9 (lowest risk)
    primary_reason: str
    reasons: list[str]
    is_bulk_confirmable: bool
    is_autonomously_exportable: bool  # Strictly False under Mode 0 (PD-002)
    policy_version: str
    confidence: float = 0.0
    flags: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentTriageSummary:
    """Document-level triage summary and sorted review queue feed."""

    document_id: uuid.UUID
    policy_version: str
    policy_mode: TriagePolicyMode
    total_claims: int
    auto_processed_count: int
    human_review_required_count: int
    discrepancy_count: int
    stage_c_count: int
    fallback_count: int
    low_ocr_count: int
    injection_flag_count: int
    uncoded_count: int
    sorted_claims: list[ClaimTriageResult]
    can_bulk_confirm_agreed: bool
    autonomous_export_allowed: bool = False  # Strictly False under Mode 0 (PD-002)


# ─── TRIAGE POLICY ENGINE ─────────────────────────────────────────────────────

class TriageEngine:
    """Evaluates clinical claims against versioned triage policies (PRODUCTIZATION_SPEC §7.2)."""

    def __init__(
        self,
        policy: Optional[TriagePolicy] = None,
        *,
        policy_version: str = DEFAULT_MODE_0_POLICY_VERSION,
        config: Optional[dict[str, Any]] = None,
    ) -> None:
        if policy is not None:
            self.policy_version = policy.version
            self.policy_name = policy.name
            self.mode = TriagePolicyMode(policy.mode) if isinstance(policy.mode, str) else policy.mode
            self.config = policy.config or DEFAULT_MODE_0_CONFIG
        else:
            self.policy_version = policy_version
            self.policy_name = DEFAULT_MODE_0_POLICY_NAME
            self.mode = TriagePolicyMode.MODE_0
            self.config = config or DEFAULT_MODE_0_CONFIG

        self.ocr_threshold = float(self.config.get("ocr_confidence_threshold", 0.70))
        self.stage_b_threshold = float(self.config.get("stage_b_confidence_threshold", 0.60))

    def classify_claim(self, claim_input: ClaimTriageInput) -> ClaimTriageResult:
        """Classify a single claim deterministically per §7.2 Mode 0 rules.

        Conditions evaluated:
          1. Injection-pattern flag (CRITICAL, Rank 1)
          2. Patient identity mismatch (CRITICAL, Rank 1)
          3. Discrepancy / Refuted (HIGH, Rank 2)
          4. Pipeline fallback (HIGH, Rank 3)
          5. Resolved by Stage C / Escalated (MEDIUM, Rank 4)
          6. Insufficient evidence / missing evidence (MEDIUM, Rank 5)
          7. Uncoded concept / null normalization (MEDIUM, Rank 6)
          8. Low OCR confidence (MEDIUM, Rank 7)
          9. Stage A/B agreement on SUPPORTED (LOW, Rank 9)
        """
        reasons: list[str] = []
        flags: dict[str, Any] = {
            "is_discrepancy": False,
            "is_stage_c": False,
            "is_fallback": False,
            "low_ocr": False,
            "is_uncoded": False,
            "injection_flag": False,
            "identity_mismatch": False,
            "insufficient_evidence": False,
        }

        assigned_category = TriageCategory.AUTO_PROCESSED_AGREED
        assigned_risk = TriageRiskTier.LOW
        assigned_rank = 9

        label = (claim_input.verification_label or "").upper()
        stage = (claim_input.stage_reached or "").upper()

        # ── 1. Injection Pattern Flag (T-11) ──────────────────────────────────
        if claim_input.has_injection_flag:
            reasons.append("Potential prompt injection pattern flagged in claim/source.")
            flags["injection_flag"] = True
            assigned_category = TriageCategory.HUMAN_REVIEW_REQUIRED
            assigned_risk = TriageRiskTier.CRITICAL
            assigned_rank = min(assigned_rank, 1)

        # ── 2. Patient Identity Mismatch (Wrong-Patient Risk) ─────────────────
        if claim_input.has_identity_mismatch:
            reasons.append("Patient identity mismatch flagged against target patient context.")
            flags["identity_mismatch"] = True
            assigned_category = TriageCategory.HUMAN_REVIEW_REQUIRED
            assigned_risk = TriageRiskTier.CRITICAL
            assigned_rank = min(assigned_rank, 1)

        # ── 3. Discrepancy / REFUTED ──────────────────────────────────────────
        if label == VerificationLabel.REFUTED.value or label == "DISCREPANCY":
            reasons.append("Discrepancy detected between extracted claim and source text.")
            flags["is_discrepancy"] = True
            assigned_category = TriageCategory.HUMAN_REVIEW_REQUIRED
            assigned_risk = max(assigned_risk, TriageRiskTier.HIGH, key=lambda r: _tier_severity(r))
            assigned_rank = min(assigned_rank, 2)

        # ── 4. Fallback Model / Unavailable LLM ──────────────────────────────
        if claim_input.is_fallback:
            reasons.append("Pipeline fallback occurred during verification.")
            flags["is_fallback"] = True
            assigned_category = TriageCategory.HUMAN_REVIEW_REQUIRED
            assigned_risk = max(assigned_risk, TriageRiskTier.HIGH, key=lambda r: _tier_severity(r))
            assigned_rank = min(assigned_rank, 3)

        # ── 5. Resolved by Stage C / Escalated ────────────────────────────────
        if stage == VerificationStage.STAGE_C.value or claim_input.escalated:
            reasons.append("Resolved by Stage C LLM judge; second-check performed (never pre-accepted).")
            flags["is_stage_c"] = True
            assigned_category = TriageCategory.HUMAN_REVIEW_REQUIRED
            assigned_risk = max(assigned_risk, TriageRiskTier.MEDIUM, key=lambda r: _tier_severity(r))
            assigned_rank = min(assigned_rank, 4)

        # ── 6. Insufficient Evidence ──────────────────────────────────────────
        if label == VerificationLabel.INSUFFICIENT_EVIDENCE.value or claim_input.evidence_missing:
            reasons.append("Insufficient evidence grounding in source document.")
            flags["insufficient_evidence"] = True
            assigned_category = TriageCategory.HUMAN_REVIEW_REQUIRED
            assigned_risk = max(assigned_risk, TriageRiskTier.MEDIUM, key=lambda r: _tier_severity(r))
            assigned_rank = min(assigned_rank, 5)

        # ── 7. Normalization Returned Null Code ───────────────────────────────
        if not claim_input.normalized_code or not claim_input.normalized_code.strip():
            reasons.append("Uncoded concept: terminology normalization returned null.")
            flags["is_uncoded"] = True
            assigned_category = TriageCategory.HUMAN_REVIEW_REQUIRED
            assigned_risk = max(assigned_risk, TriageRiskTier.MEDIUM, key=lambda r: _tier_severity(r))
            assigned_rank = min(assigned_rank, 6)

        # ── 8. Low OCR Confidence ─────────────────────────────────────────────
        if claim_input.ocr_confidence is not None and claim_input.ocr_confidence < self.ocr_threshold:
            reasons.append(
                f"Low OCR confidence at evidence span ({claim_input.ocr_confidence:.2f} < {self.ocr_threshold:.2f})."
            )
            flags["low_ocr"] = True
            assigned_category = TriageCategory.HUMAN_REVIEW_REQUIRED
            assigned_risk = max(assigned_risk, TriageRiskTier.MEDIUM, key=lambda r: _tier_severity(r))
            assigned_rank = min(assigned_rank, 7)

        # ── 9. Auto-processed Category Check ──────────────────────────────────
        if assigned_category == TriageCategory.AUTO_PROCESSED_AGREED:
            if label == VerificationLabel.SUPPORTED.value and stage in (
                VerificationStage.STAGE_A.value,
                VerificationStage.STAGE_B.value,
            ):
                reasons.append("Stage A/B high-confidence agreement on SUPPORTED.")
            else:
                # If label wasn't explicitly supported or unverified, force human review
                assigned_category = TriageCategory.HUMAN_REVIEW_REQUIRED
                assigned_risk = TriageRiskTier.MEDIUM
                assigned_rank = 8
                reasons.append("Claim requires human review: verification state is unconfirmed.")

        # Under Mode 0, bulk confirmation is allowed ONLY for AUTO_PROCESSED_AGREED
        is_bulk_confirmable = (assigned_category == TriageCategory.AUTO_PROCESSED_AGREED)
        primary_reason = reasons[0] if reasons else "Nominal claim state."

        return ClaimTriageResult(
            claim_id=claim_input.claim_id,
            field_type=claim_input.field_type,
            category=assigned_category,
            risk_tier=assigned_risk,
            priority_rank=assigned_rank,
            primary_reason=primary_reason,
            reasons=reasons,
            is_bulk_confirmable=is_bulk_confirmable,
            is_autonomously_exportable=False,  # Under Mode 0, ALWAYS FALSE (PD-002)
            policy_version=self.policy_version,
            confidence=claim_input.confidence,
            flags=flags,
        )

    def triage_document_claims(
        self,
        document: Document,
        claims: Sequence[Claim],
        verification_results: Optional[dict[uuid.UUID, VerificationResult]] = None,
        evidence_map: Optional[dict[uuid.UUID, Evidence]] = None,
        document_flags: Optional[dict[str, Any]] = None,
    ) -> DocumentTriageSummary:
        """Triage all claims of a document and return sorted review queue summary."""
        v_map = verification_results or {}
        e_map = evidence_map or {}
        doc_flags = document_flags or {}

        # Check document-level identity verification mismatch
        has_identity_mismatch = (
            document.identity_verification_status == IdentityVerificationStatus.MISMATCH
            or bool(doc_flags.get("has_identity_mismatch", False))
        )

        triaged_claims: list[ClaimTriageResult] = []
        auto_processed_count = 0
        human_review_count = 0
        discrepancy_count = 0
        stage_c_count = 0
        fallback_count = 0
        low_ocr_count = 0
        injection_count = 0
        uncoded_count = 0

        for claim in claims:
            v_res = v_map.get(claim.id)
            ev = e_map.get(claim.evidence_id) if claim.evidence_id else None

            claim_flags = dict(doc_flags)
            if has_identity_mismatch:
                claim_flags["has_identity_mismatch"] = True

            claim_input = ClaimTriageInput.from_orm(
                claim=claim,
                verification_result=v_res,
                evidence=ev,
                flags=claim_flags,
            )

            result = self.classify_claim(claim_input)
            triaged_claims.append(result)

            if result.category == TriageCategory.AUTO_PROCESSED_AGREED:
                auto_processed_count += 1
            else:
                human_review_count += 1

            if result.flags.get("is_discrepancy"):
                discrepancy_count += 1
            if result.flags.get("is_stage_c"):
                stage_c_count += 1
            if result.flags.get("is_fallback"):
                fallback_count += 1
            if result.flags.get("low_ocr"):
                low_ocr_count += 1
            if result.flags.get("injection_flag"):
                injection_count += 1
            if result.flags.get("is_uncoded"):
                uncoded_count += 1

        # Deterministic risk sort (highest risk / lowest priority rank first)
        sorted_claims = self.sort_claims_by_risk(triaged_claims)

        can_bulk = (
            self.mode == TriagePolicyMode.MODE_0
            and bool(self.config.get("allow_bulk_confirm_agreed", True))
            and auto_processed_count > 0
        )

        return DocumentTriageSummary(
            document_id=document.id,
            policy_version=self.policy_version,
            policy_mode=self.mode,
            total_claims=len(claims),
            auto_processed_count=auto_processed_count,
            human_review_required_count=human_review_count,
            discrepancy_count=discrepancy_count,
            stage_c_count=stage_c_count,
            fallback_count=fallback_count,
            low_ocr_count=low_ocr_count,
            injection_flag_count=injection_count,
            uncoded_count=uncoded_count,
            sorted_claims=sorted_claims,
            can_bulk_confirm_agreed=can_bulk,
            autonomous_export_allowed=False,  # Mode 0 invariant
        )

    @staticmethod
    def sort_claims_by_risk(claims: list[ClaimTriageResult]) -> list[ClaimTriageResult]:
        """Deterministically sort claims for review queue presentation.

        Ordering:
          1. Priority rank ascending (1 = CRITICAL first, 9 = LOW last)
          2. Confidence ascending (lower confidence earlier in review list)
          3. Stable UUID string tie-breaker
        """
        return sorted(
            claims,
            key=lambda c: (
                c.priority_rank,
                c.confidence,
                str(c.claim_id),
            ),
        )


def _tier_severity(tier: TriageRiskTier) -> int:
    """Numeric severity weighting for comparing risk tiers."""
    order = {
        TriageRiskTier.CRITICAL: 4,
        TriageRiskTier.HIGH: 3,
        TriageRiskTier.MEDIUM: 2,
        TriageRiskTier.LOW: 1,
    }
    return order.get(tier, 0)


# ─── IDEMPOTENT DATABASE SEEDING ──────────────────────────────────────────────

def seed_default_triage_policy(session: Session) -> TriagePolicy:
    """Idempotently seed the initial Mode 0 triage policy in the database."""
    policy = (
        session.query(TriagePolicy)
        .filter(TriagePolicy.version == DEFAULT_MODE_0_POLICY_VERSION)
        .first()
    )
    if policy is not None:
        return policy

    policy = TriagePolicy(
        id=MODE_0_POLICY_UUID,
        version=DEFAULT_MODE_0_POLICY_VERSION,
        name=DEFAULT_MODE_0_POLICY_NAME,
        mode=TriagePolicyMode.MODE_0,
        description=(
            "Mandatory 100% human review policy for initial production deployment (Mode 0). "
            "Every claim requires explicit human decision. Bulk confirmation is permitted "
            "only for auto-processed agreed claims after source evidence has been opened."
        ),
        is_active=True,
        config=dict(DEFAULT_MODE_0_CONFIG),
    )
    session.add(policy)
    session.flush()
    logger.info("Seeded canonical TriagePolicy %s (ID: %s)", policy.version, policy.id)
    return policy
