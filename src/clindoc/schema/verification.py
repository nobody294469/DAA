"""Atomic verification result schema conforming to schemas/verification_result.json.

Authority: PROJECT_SPEC §10, PHASE0_ARCHITECTURE_RESOLUTION AUDIT-16, DEC-018, DEC-019
"""
from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

VerificationLabel = Literal["SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE"]
StageReached = Literal["A", "B", "C"]
ContradictionType = Literal[
    "unit_mismatch",
    "value_mismatch",
    "dose_mismatch",
    "negation_mismatch",
    "temporal_mismatch",
    "wrong_entity",
    "code_mismatch",
]


class VerificationResult(BaseModel):
    """Atomic verification result produced by the verification cascade.

    Conforms strictly to schemas/verification_result.json (§10).
    Every field is required unless marked optional.
    """
    model_config = ConfigDict(extra="forbid")

    verification_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Globally unique identifier for this verification verdict (UUID)",
    )
    claim_id: str = Field(..., description="Foreign key to the verified Claim")
    label: VerificationLabel = Field(
        ..., description="Final verification label assigned to the claim"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Final CALIBRATED confidence score in [0, 1] (§17)"
    )
    stage_reached: StageReached = Field(
        ...,
        description="Final cascade stage that assigned the verdict (A: Deterministic, B: ModernBERT/NLI, C: LLM Judge)",
    )
    stage_a_label: Optional[VerificationLabel] = Field(
        default=None,
        description="Verdict from Stage A deterministic rules; null if Stage A not applicable",
    )
    stage_b_label: Optional[VerificationLabel] = Field(
        default=None,
        description="Verdict from Stage B learned NLI classifier; null if bypassed or not reached",
    )
    stage_b_raw_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Uncalibrated raw softmax probability from Stage B classifier",
    )
    contradiction_type: Optional[ContradictionType] = Field(
        default=None,
        description="Specific discrepancy taxonomy (§20.1); required when label is DISCREPANCY, null otherwise",
    )
    normalized_code_agreement: Optional[bool] = Field(
        default=None,
        description="True if claim code matches evidence code; null if either lacks a resolvable standard code",
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Natural language justification from LLM Judge; populated ONLY when stage_reached is C",
    )
    evidence_id: str = Field(
        ..., description="Foreign key to Evidence; must match claim.evidence_id"
    )
    escalated: bool = Field(
        default=False,
        description="True if Stage B escalated this claim to Stage C (due to disagreement or low confidence)",
    )

    @model_validator(mode="after")
    def validate_cascade_invariants(self) -> "VerificationResult":
        # Invariant 1: If label == DISCREPANCY, contradiction_type MUST NOT be null
        if self.label == "DISCREPANCY" and self.contradiction_type is None:
            raise ValueError(
                "contradiction_type must not be null when label is DISCREPANCY (PROJECT_SPEC §10)"
            )

        # Invariant 2: If label != DISCREPANCY, contradiction_type MUST be null
        if self.label != "DISCREPANCY" and self.contradiction_type is not None:
            raise ValueError(
                f"contradiction_type must be null when label is {self.label} (PROJECT_SPEC §10)"
            )

        # Invariant 3: Rationale MUST be null when stage_reached is A or B (PROJECT_SPEC §10)
        if self.stage_reached in ("A", "B") and self.rationale is not None:
            raise ValueError(
                f"rationale must be null when stage_reached is '{self.stage_reached}' (PROJECT_SPEC §10)"
            )

        return self
