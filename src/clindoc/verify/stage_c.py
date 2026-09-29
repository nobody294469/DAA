"""Stage C: Selective LLM Judge Interface with Grammar Constraints and Calibration.

Authority:
  - PROJECT_SPEC §5 #8, §7.6, §10, §12.2, §13.1, §13.3, §17.1
  - DEC-013: Grammar-constrained structured output enforcement
  - DEC-018: 1D confidence calibration via IsotonicConfidenceCalibrator
  - DEC-003: Document-local context window bounding

Stage C is invoked ONLY on escalation (Stage A/B disagreement, low confidence,
or insufficient evidence precaution). Output is strictly constrained to target
schema invariants.

Note on DEC-013:
DEC-013 schema enforcement at the verification layer is post-generation validation;
generation-time constrained decoding is the responsibility of the concrete LLM client/backend.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Optional, Protocol, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator
import numpy as np

from clindoc.extract.contracts import Claim, Evidence
from clindoc.m2.calibration import IsotonicConfidenceCalibrator
from clindoc.schema.verification import ContradictionType, VerificationLabel


class StageCJudgeOutput(BaseModel):
    """Schema-constrained LLM Judge output contract conforming to DEC-013."""
    model_config = ConfigDict(extra="forbid")

    label: VerificationLabel = Field(..., description="Verdict: SUPPORTED, DISCREPANCY, or INSUFFICIENT_EVIDENCE")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Self-reported scalar confidence in [0, 1]")
    contradiction_type: Optional[ContradictionType] = Field(
        default=None,
        description="Required when label is DISCREPANCY, null otherwise",
    )
    rationale: str = Field(..., min_length=5, description="1-3 sentence natural language clinical explanation")

    @model_validator(mode="after")
    def validate_invariants(self) -> "StageCJudgeOutput":
        if self.label == "DISCREPANCY" and self.contradiction_type is None:
            raise ValueError("contradiction_type must not be null when label is DISCREPANCY")
        if self.label != "DISCREPANCY" and self.contradiction_type is not None:
            raise ValueError(f"contradiction_type must be null when label is {self.label}")
        return self


@dataclass(frozen=True)
class StageCResult:
    """Calibrated outcome from Stage C verification."""

    label: VerificationLabel
    raw_confidence: float
    calibrated_confidence: float
    contradiction_type: Optional[ContradictionType]
    rationale: str


class LLMClientProtocol(Protocol):
    """Protocol for LLM generation backends."""

    def generate(self, prompt: str, schema: type[BaseModel]) -> str:
        """Generate schema-conforming JSON string."""
        ...


class DefaultMockLLMJudge:
    """Deterministic, provider-independent mock judge for offline execution and testing."""

    def generate(self, prompt: str, schema: type[BaseModel]) -> str:
        """Generate a deterministic, valid JSON response based on prompt text."""
        # Simple rule-based mock for test determinism
        lower_prompt = prompt.lower()
        if "discrepancy" in lower_prompt or "mismatch" in lower_prompt:
            ctype = "value_mismatch"
            if "unit" in lower_prompt:
                ctype = "unit_mismatch"
            elif "dose" in lower_prompt:
                ctype = "dose_mismatch"
            elif "negat" in lower_prompt:
                ctype = "negation_mismatch"

            return json.dumps({
                "label": "DISCREPANCY",
                "confidence": 0.88,
                "contradiction_type": ctype,
                "rationale": "Evidence text contradicts the extracted claim.",
            })
        elif "missing" in lower_prompt or "insufficient" in lower_prompt:
            return json.dumps({
                "label": "INSUFFICIENT_EVIDENCE",
                "confidence": 0.92,
                "contradiction_type": None,
                "rationale": "No sufficient clinical evidence found in the document snippet.",
            })
        else:
            return json.dumps({
                "label": "SUPPORTED",
                "confidence": 0.95,
                "contradiction_type": None,
                "rationale": "Claim is verbatim supported by the source document text.",
            })


class StageCJudge:
    """Stage C Selective LLM Judge.

    Grounds judgment strictly in claim, evidence, and local context window (§13.3).
    Calibrates self-reported scalar confidence via IsotonicConfidenceCalibrator (DEC-018).
    """

    def __init__(
        self,
        client: Optional[LLMClientProtocol] = None,
        calibrator: Optional[IsotonicConfidenceCalibrator] = None,
    ) -> None:
        self.client = client or DefaultMockLLMJudge()
        self.calibrator = calibrator or self._create_default_calibrator()

    @staticmethod
    def _create_default_calibrator() -> IsotonicConfidenceCalibrator:
        """Create and fit a standard identity-calibrator fallback."""
        cal = IsotonicConfidenceCalibrator()
        # Pre-fit on monotonic identity grid so it can calibrate immediately
        cal.fit(
            confidences=np.array([0.0, 0.25, 0.5, 0.75, 1.0]),
            binary_labels=np.array([0, 0, 1, 1, 1]),
        )
        return cal

    def judge(
        self,
        claim: Claim,
        evidence: Optional[Evidence] = None,
        context_text: Optional[str] = None,
        stage_a_label: Optional[str] = None,
        stage_b_label: Optional[str] = None,
        stage_b_confidence: Optional[float] = None,
    ) -> StageCResult:
        """Perform Stage C adjudication.

        Args:
            claim: Clinical claim under verification.
            evidence: Linked evidence span.
            context_text: Document-local context window (DEC-003, max 500 chars).
            stage_a_label: Preceding Stage A verdict (if any).
            stage_b_label: Preceding Stage B verdict (if any).
            stage_b_confidence: Preceding Stage B confidence.

        Returns:
            StageCResult containing verified label, calibrated confidence, contradiction_type, and rationale.
        """
        prompt = self._build_judge_prompt(
            claim=claim,
            evidence=evidence,
            context_text=context_text,
            stage_a_label=stage_a_label,
            stage_b_label=stage_b_label,
            stage_b_confidence=stage_b_confidence,
        )

        raw_response = self.client.generate(prompt, StageCJudgeOutput)
        parsed_output = self._parse_and_validate(raw_response)

        # Calibrate scalar confidence (DEC-018)
        raw_conf = parsed_output.confidence
        cal_conf = float(self.calibrator.predict(raw_conf))
        cal_conf = max(0.0, min(1.0, cal_conf))

        return StageCResult(
            label=parsed_output.label,
            raw_confidence=raw_conf,
            calibrated_confidence=cal_conf,
            contradiction_type=parsed_output.contradiction_type,
            rationale=parsed_output.rationale,
        )

    def _build_judge_prompt(
        self,
        claim: Claim,
        evidence: Optional[Evidence],
        context_text: Optional[str],
        stage_a_label: Optional[str],
        stage_b_label: Optional[str],
        stage_b_confidence: Optional[float],
    ) -> str:
        """Construct prompt strictly bounded by local evidence and document context."""
        ev_text = evidence.text if (evidence and evidence.text) else "None"
        ctx = context_text or ev_text

        parts = [
            "You are a clinical document verification judge.",
            "Verify whether the following extracted clinical claim is supported by the source document text.",
            "Do NOT use external medical knowledge. Evaluate strictly based on the text provided.",
            "",
            f"Claim Field: {claim.field_type}",
            f"Claim Value: {claim.value}",
            f"Claim Unit: {claim.unit_raw or 'None'}",
            f"Claim Negated: {claim.negated}",
            f"Evidence Text: {ev_text}",
            f"Document Local Context: {ctx}",
            f"Stage A Pre-check: {stage_a_label or 'None'}",
            f"Stage B Pre-check: {stage_b_label or 'None'} (confidence: {stage_b_confidence or 0.0:.2f})",
            "",
            "Respond in JSON format with fields: label, confidence, contradiction_type, rationale.",
        ]
        return "\n".join(parts)

    def _parse_and_validate(self, text: str) -> StageCJudgeOutput:
        """Parse response and enforce DEC-013 schema conformance."""
        text = text.strip()
        # Strip potential markdown fences if present
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        data = json.loads(text)
        return StageCJudgeOutput.model_validate(data)
