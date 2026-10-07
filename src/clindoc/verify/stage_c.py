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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
import numpy as np

from clindoc.extract.contracts import Claim, Evidence
from clindoc.m2.calibration import IsotonicConfidenceCalibrator
from clindoc.schema.verification import ContradictionType, VerificationLabel
from clindoc.config import (
    ProductionProfileError,
    RuntimeProfile,
    get_active_profile,
    get_circuit_breaker_failure_threshold,
    get_circuit_breaker_cooldown_seconds,
    get_model_concurrency_limit,
    get_backoff_base_delay,
    get_backoff_max_delay,
)
from clindoc.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    ConcurrencyLimiter,
    ExponentialBackoff,
    FALLBACK_REASON_LLM_SCHEMA_INVALID,
    FALLBACK_REASON_LLM_UNAVAILABLE,
    classify_fallback_reason,
    get_shared_circuit_breaker,
    get_shared_concurrency_limiter,
    is_retryable_error,
)
from clindoc.observability.metrics import get_metrics


STAGE_C_PROMPT_VERSION = "V8_STAGE_C_LABFLAG_PROMPT_V1"

LAB_FLAG_TABULAR_INSTRUCTION = (
    "When evaluating lab.flag claims against structured laboratory tables:\n"
    "1. Explicit abnormal column indicators take strict precedence: a trailing 'H' or 'HIGH' explicitly denotes HIGH; a trailing 'L' or 'LOW' explicitly denotes LOW; an unflagged or blank abnormal entry denotes NORMAL.\n"
    "2. Do NOT attempt to recompute, recalculate, or verify numerical intervals if an explicit flag indicator ('H', 'L', 'HIGH', 'LOW', or blank) is present in the source table row."
)


DEFAULT_STAGE_C_SYSTEM_PROMPT = """You are a rigorous clinical document verification judge, NOT a general medical question-answering or diagnostic assistant.
Your task is to determine whether an extracted clinical claim is SUPPORTED, DISCREPANCY, or INSUFFICIENT_EVIDENCE based STRICTLY and ONLY on the provided source document evidence and local context.

CRITICAL BOUNDARIES:
1. Do NOT use external medical knowledge, clinical guideline knowledge, or general plausibility assumptions.
2. Evaluate ONLY what is explicitly stated in the document snippet. If a fact is medically sound or plausible but absent from the text, it is NOT supported.
3. Example: If the document states 'Glucose: 66 mg/dL', the claim 'Glucose: 66 mg/dL' is SUPPORTED, even though 66 mg/dL is clinically hypoglycemic.
4. Do NOT guess or fill in missing clinical information.
5. If the evidence text is missing, blank, or completely unrelated to the claim, return label 'INSUFFICIENT_EVIDENCE' with contradiction_type null.
6. If the claim directly conflicts with the evidence text (e.g. different value, different unit, wrong medication name, or opposite negation), return label 'DISCREPANCY' and specify the exact contradiction_type.
7. Return ONLY valid JSON adhering strictly to the requested schema."""


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
    attempts: int = 1
    model_name: Optional[str] = None
    prompt_version: str = STAGE_C_PROMPT_VERSION
    is_fallback: bool = False
    fallback_reason: Optional[str] = None
    error: Optional[str] = None


class LLMClientProtocol(Protocol):
    """Protocol for LLM generation backends."""

    def generate(self, prompt: str, schema: type[BaseModel]) -> str:
        """Generate schema-conforming JSON string."""
        ...


class VLLMJudgeClient:
    """Concrete OpenAI-compatible vLLM judge client for Stage C (DEC-006, DEC-013).

    Targets Qwen/Qwen2.5-7B-Instruct running on vLLM with response_format JSON schema.
    """

    def __init__(
        self,
        model_name: str = "Qwen/Qwen2.5-7B-Instruct",
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        temperature: float = 0.0,
        seed: int = 42,
        max_tokens: int = 256,
        timeout: float = 30.0,
        http_client: Optional[Any] = None,
        system_prompt: Optional[str] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        concurrency_limiter: Optional[ConcurrencyLimiter] = None,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.temperature = float(temperature)
        self.seed = int(seed)
        self.max_tokens = int(max_tokens)
        self.timeout = float(timeout)
        self.system_prompt = system_prompt or DEFAULT_STAGE_C_SYSTEM_PROMPT
        self.circuit_breaker = circuit_breaker or get_shared_circuit_breaker(
            self.base_url,
            failure_threshold=get_circuit_breaker_failure_threshold(),
            cooldown_seconds=get_circuit_breaker_cooldown_seconds(),
        )
        self.concurrency_limiter = concurrency_limiter or get_shared_concurrency_limiter(
            self.base_url,
            max_concurrency=get_model_concurrency_limit(),
            timeout=self.timeout,
        )
        if http_client is None:
            try:
                import httpx
                self._http_client = httpx.Client(timeout=self.timeout)
            except Exception:
                self._http_client = None
        else:
            self._http_client = http_client

    def generate(self, prompt: str, schema: type[BaseModel]) -> str:
        """Generate schema-constrained JSON response using vLLM chat completions API."""
        try:
            import httpx
        except ImportError:
            raise RuntimeError(
                "httpx is required for VLLMJudgeClient. Please install httpx."
            )

        schema_dict = schema.model_json_schema() if hasattr(schema, "model_json_schema") else {}

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "stage_c_judge_output",
                    "schema": schema_dict,
                    "strict": True,
                },
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        with self.concurrency_limiter:
            with self.circuit_breaker:
                if self._http_client is not None:
                    resp = self._http_client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]

                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]


class DefaultMockLLMJudge:
    """Deterministic, provider-independent mock judge for offline execution and testing."""

    def __init__(self, model_name: str = "mock-judge-v1") -> None:
        self.model_name = model_name

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
    Includes bounded retry (max 3 total attempts) with safe clinical fallback (DEC-013).
    """

    def __init__(
        self,
        client: Optional[LLMClientProtocol] = None,
        calibrator: Optional[IsotonicConfidenceCalibrator] = None,
        max_attempts: int = 3,
        profile: Optional[Union[RuntimeProfile, str]] = None,
        allow_mock_client: Optional[bool] = None,
        backoff: Optional[ExponentialBackoff] = None,
    ) -> None:
        self.profile = RuntimeProfile.from_string(profile) if profile else get_active_profile()
        if allow_mock_client is not None:
            self.allow_mock_client = allow_mock_client
        else:
            self.allow_mock_client = (self.profile == RuntimeProfile.DEV)

        if self.profile == RuntimeProfile.PRODUCTION or not self.allow_mock_client:
            if client is None:
                raise ProductionProfileError(
                    "Stage C client must be explicitly configured in PRODUCTION profile; DefaultMockLLMJudge is forbidden."
                )
            if isinstance(client, DefaultMockLLMJudge):
                raise ProductionProfileError(
                    "DefaultMockLLMJudge is forbidden in PRODUCTION profile."
                )
            if isinstance(client, VLLMJudgeClient):
                from urllib.parse import urlparse
                parsed = urlparse(getattr(client, "base_url", ""))
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    raise ProductionProfileError(
                        f"Invalid vLLM base_url '{getattr(client, 'base_url', '')}' in PRODUCTION profile. Must be http:// or https://."
                    )
                if not getattr(client, "model_name", "").strip():
                    raise ProductionProfileError(
                        "Invalid vLLM model_name in PRODUCTION profile."
                    )

        self.client = client or DefaultMockLLMJudge()
        self.calibrator = calibrator or self._create_default_calibrator()
        self.max_attempts = max_attempts
        self.prompt_version = STAGE_C_PROMPT_VERSION
        self.backoff = backoff or ExponentialBackoff(
            base_delay=get_backoff_base_delay(),
            max_delay=get_backoff_max_delay(),
        )

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
        """Perform Stage C adjudication with bounded retry and safe clinical fallback.

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

        model_name = getattr(self.client, "model_name", type(self.client).__name__)
        last_error: Optional[Exception] = None
        attempts_executed = 0

        # Bounded retry: maximum 3 total generation attempts (DEC-013: max 2 retries)
        for attempt in range(1, self.max_attempts + 1):
            attempts_executed = attempt
            if attempt > 1:
                get_metrics().record_model_retry("qwen")
            try:
                import time
                t0 = time.perf_counter()
                raw_response = self.client.generate(prompt, StageCJudgeOutput)
                parsed_output = self._parse_and_validate(raw_response)
                gen_duration = time.perf_counter() - t0
                get_metrics().record_model_request("qwen", "success", duration_seconds=gen_duration)

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
                    attempts=attempt,
                    model_name=model_name,
                    prompt_version=STAGE_C_PROMPT_VERSION,
                    is_fallback=False,
                    error=None,
                )
            except Exception as exc:
                last_error = exc

                # Classify error outcome for metrics
                if isinstance(exc, (json.JSONDecodeError, ValidationError, ValueError)):
                    err_outcome = "schema_invalid"
                elif isinstance(exc, CircuitBreakerOpenError):
                    err_outcome = "circuit_open"
                elif isinstance(exc, TimeoutError):
                    err_outcome = "timeout"
                else:
                    err_outcome = "failure"
                get_metrics().record_model_request("qwen", err_outcome)

                # If this was a schema validation failure after generate succeeded, record failure on breaker
                cb = getattr(self.client, "circuit_breaker", None)
                if cb is not None and isinstance(exc, (json.JSONDecodeError, ValidationError, ValueError)):
                    cb.record_failure(exc)

                # Bounded retry policy (§3.5): fail fast on non-retryable errors
                if not is_retryable_error(exc):
                    break

                # Exponential backoff with jitter before next attempt
                if attempt < self.max_attempts:
                    self.backoff.sleep(attempt)

        # Safe clinical fallback after all attempts fail (F-06, F-07)
        fallback_reason = classify_fallback_reason(last_error) or FALLBACK_REASON_LLM_UNAVAILABLE
        get_metrics().record_stage_c_fallback(fallback_reason)
        return StageCResult(
            label="INSUFFICIENT_EVIDENCE",
            raw_confidence=0.50,
            calibrated_confidence=0.50,
            contradiction_type=None,
            rationale=(
                f"Stage C verification fallback ({fallback_reason}): generation/validation failed after "
                f"{attempts_executed} attempts ({type(last_error).__name__}: {last_error})."
            ),
            attempts=attempts_executed,
            model_name=model_name,
            prompt_version=STAGE_C_PROMPT_VERSION,
            is_fallback=True,
            fallback_reason=fallback_reason,
            error=f"{type(last_error).__name__}: {last_error}" if last_error else "unknown_error",
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
        """Construct user prompt strictly bounded by local evidence and document context."""
        ev_text = evidence.text if (evidence and evidence.text) else "None"
        ctx = context_text or ev_text
        norm_code = getattr(claim, "normalized_code", None) or "None"
        numeric_str = str(claim.value_numeric) if claim.value_numeric is not None else "None"

        parts = [
            "Evaluate whether the following clinical claim is grounded in the source document snippet.",
            "",
            "[CLAIM CONTEXT]",
            f"- Field Type: {claim.field_type}",
            f"- Claim Representation: {claim.value}",
            f"- Numeric Value: {numeric_str}",
            f"- Unit: {claim.unit_raw or 'None'}",
            f"- Negated: {claim.negated}",
            f"- Normalized Code / Term: {norm_code}",
            "",
        ]

        field_type_str = str(getattr(claim.field_type, "value", claim.field_type))
        if field_type_str == "lab.flag":
            parts.extend([
                "[FIELD-SPECIFIC INSTRUCTIONS]",
                LAB_FLAG_TABULAR_INSTRUCTION,
                "",
            ])

        parts.extend([
            "[SOURCE EVIDENCE]",
            f"- Verbatim Evidence Span: {ev_text}",
            f"- Document Local Context Window: {ctx}",
            "",
            "[CASCADE STAGE CONTEXT]",
            f"- Stage A Pre-check: {stage_a_label or 'None'}",
            f"- Stage B Prediction: {stage_b_label or 'None'} (confidence: {stage_b_confidence or 0.0:.2f})",
            "",
            "Respond in JSON format conforming to StageCJudgeOutput schema with fields: label, confidence, contradiction_type, rationale.",
        ])
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
