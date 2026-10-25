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
from pathlib import Path
import re
from typing import Any, Callable, Optional, Protocol, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator
import numpy as np

from clindoc.extract.contracts import Claim, Evidence
from clindoc.m2.calibration import IsotonicConfidenceCalibrator
from clindoc.schema.verification import ContradictionType, VerificationLabel


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
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.temperature = float(temperature)
        self.seed = int(seed)
        self.max_tokens = int(max_tokens)
        self.timeout = float(timeout)
        self.system_prompt = system_prompt or DEFAULT_STAGE_C_SYSTEM_PROMPT
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

        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self.timeout)
        resp = self._http_client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


_CASCADE_BLOCK = re.compile(r"\[CASCADE STAGE CONTEXT\].*?\n\n", re.DOTALL)


def local_judge_messages(prompt: str, system_prompt: str, schema: type[BaseModel], compact: bool = False) -> list[dict]:
    """Chat messages for a local judge; the single format shared by inference and LoRA training.

    compact=True (fine-tuned adapters): drop the Stage A/B hints, which training data cannot provide
    honestly, and the JSON schema text, whose shape the adapter has learned.
    """
    if compact:
        user = _CASCADE_BLOCK.sub("", prompt)
    else:
        schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        user = (f"{prompt}\n\nReply with ONLY one JSON object matching this JSON schema, "
                f"no prose and no code fences:\n{schema_json}")
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user}]


class LocalTransformersJudgeClient:
    """In-process Stage C judge: a local instruct LLM (e.g. Qwen2.5-3B-Instruct) via transformers.

    Same contract as VLLMJudgeClient (DEC-006 Qwen family) without a separate server. Local
    decoding has no grammar constraint (DEC-013), so the JSON schema goes in the prompt and the
    first JSON object is returned; StageCJudge still validates it and retries up to 3 times.
    ponytail: greedy decode, one claim at a time, behind the web server's global lock;
    batch or move to vLLM if escalation volume grows.
    """

    def __init__(
        self,
        model_path: str,
        device: Optional[str] = None,
        max_new_tokens: int = 256,
        system_prompt: Optional[str] = None,
    ) -> None:
        self.model_path = model_path
        self.model_name = Path(model_path).name if Path(model_path).exists() else model_path
        # A LoRA adapter dir (scripts/train_judge_lora.py) names its base model and uses the compact format.
        cfg = Path(model_path) / "adapter_config.json"
        self.adapter = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else None
        self.compact = bool(self.adapter)
        self.device = device
        self.max_new_tokens = int(max_new_tokens)
        self.system_prompt = system_prompt or DEFAULT_STAGE_C_SYSTEM_PROMPT
        self._model = None
        self._tokenizer = None

    def _load(self) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        base = self.adapter["base_model"] if self.adapter else self.model_path
        self._tokenizer = AutoTokenizer.from_pretrained(base)
        use_cuda = self.device != "cpu" and torch.cuda.is_available()
        dtype = torch.float16 if use_cuda else torch.float32
        if self.adapter:
            # LoRA needs every base weight materialised on one device (offload would silently drop it):
            # attach on CPU, then move the whole model; fall back to CPU if it does not fit in VRAM.
            from clindoc.verify.lora import load_lora

            self._model = AutoModelForCausalLM.from_pretrained(base, dtype=dtype)
            load_lora(self._model, Path(self.model_path))
            if use_cuda:
                try:
                    self._model.to("cuda")
                except torch.cuda.OutOfMemoryError:
                    self._model.to("cpu").float()
                    torch.cuda.empty_cache()
        else:
            # Plain base model: accelerate spills layers to CPU RAM when it exceeds free VRAM.
            self._model = AutoModelForCausalLM.from_pretrained(base, dtype=dtype, device_map="auto" if use_cuda else None)
        self._model.eval()

    def generate(self, prompt: str, schema: type[BaseModel]) -> str:
        import torch

        if self._model is None:
            self._load()
        messages = local_judge_messages(prompt, self.system_prompt, schema, compact=self.compact)
        ids = self._tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt")
        ids = ids.to(self._model.device)
        with torch.inference_mode():
            out = self._model.generate(ids, attention_mask=torch.ones_like(ids), max_new_tokens=self.max_new_tokens,
                                       do_sample=False, pad_token_id=self._tokenizer.eos_token_id)
        text = self._tokenizer.decode(out[0, ids.shape[1]:], skip_special_tokens=True)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        return match.group(0) if match else text


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
    ) -> None:
        self.client = client or DefaultMockLLMJudge()
        self.calibrator = calibrator or self._create_default_calibrator()
        self.max_attempts = max_attempts

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
        claim_text: Optional[str] = None,
    ) -> StageCResult:
        """Perform Stage C adjudication with bounded retry and safe clinical fallback.

        Args:
            claim: Clinical claim under verification.
            evidence: Linked evidence span.
            context_text: Document-local context window (DEC-003, max 500 chars).
            stage_a_label: Preceding Stage A verdict (if any).
            stage_b_label: Preceding Stage B verdict (if any).
            stage_b_confidence: Preceding Stage B confidence.
            claim_text: Descriptive claim (clindoc.verify.claim_text), e.g. "Hematocrit: 37.9 %". Added as
                an extra prompt line; omitted -> the frozen V1 prompt exactly.

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
            claim_text=claim_text,
        )
        prompt_version = STAGE_C_PROMPT_VERSION + ("+claim_text" if claim_text else "")

        model_name = getattr(self.client, "model_name", type(self.client).__name__)
        last_error: Optional[Exception] = None

        # Bounded retry: maximum 3 total generation attempts (DEC-013: max 2 retries)
        for attempt in range(1, self.max_attempts + 1):
            try:
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
                    attempts=attempt,
                    model_name=model_name,
                    prompt_version=prompt_version,
                    is_fallback=False,
                    error=None,
                )
            except Exception as exc:
                last_error = exc

        # Safe clinical fallback after all attempts fail
        return StageCResult(
            label="INSUFFICIENT_EVIDENCE",
            raw_confidence=0.50,
            calibrated_confidence=0.50,
            contradiction_type=None,
            rationale=(
                f"Stage C verification fallback: generation/validation failed after "
                f"{self.max_attempts} attempts ({type(last_error).__name__}: {last_error})."
            ),
            attempts=self.max_attempts,
            model_name=model_name,
            prompt_version=prompt_version,
            is_fallback=True,
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
        claim_text: Optional[str] = None,
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
            *([f"- Claim Text: {claim_text}"] if claim_text else []),
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
