"""tests/test_stage_c_llm_judge.py — Comprehensive tests for Stage C LLM Judge.

Verifies:
  A. VLLMJudgeClient successful structured response.
  B. Correct request payload including response_format JSON schema.
  C. HTTP failure retry.
  D. Malformed JSON retry.
  E. Schema validation failure retry.
  F. Three total attempts maximum.
  G. Safe INSUFFICIENT_EVIDENCE fallback after all attempts fail.
  H. DefaultMockLLMJudge remains unchanged and functional.
  I. Full VerificationCascade works with an injected fake VLLM client without network access.
  J. Full claim context reaches the Stage-C prompt.
"""
from __future__ import annotations

import json
from typing import Any
import httpx
import pytest

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.verify.stage_c import (
    DEFAULT_STAGE_C_SYSTEM_PROMPT,
    LAB_FLAG_TABULAR_INSTRUCTION,
    STAGE_C_PROMPT_VERSION,
    StageCJudge,
    StageCJudgeOutput,
    StageCResult,
    DefaultMockLLMJudge,
    VLLMJudgeClient,
)
from clindoc.verify.stage_a import DeterministicVerifier
from clindoc.verify.stage_b import LearnedVerifier, MockModelPredictor
from clindoc.verify.cascade import VerificationCascade


def _make_claim(
    field_type: str = "med.name",
    value: str = "Medication: Lisinopril 10mg",
    negated: bool = False,
    unit_raw: str | None = None,
    value_numeric: float | None = None,
) -> Claim:
    return Claim(
        claim_id="claim-001",
        document_id="doc-001",
        group_id="group-001",
        field_type=field_type,
        value=value,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        negated=negated,
        extraction_method="deterministic",
        extraction_confidence=1.0,
        evidence_id="ev-001",
        region_id="reg-001",
    )


def _make_evidence(text: str = "Lisinopril 10mg PO daily") -> Evidence:
    return Evidence(
        evidence_id="ev-001",
        document_id="doc-001",
        page_number=1,
        text=text,
        char_start=0,
        char_end=len(text),
        bbox=BBoxModel(x0=10.0, y0=20.0, x1=100.0, y1=40.0),
        ocr_confidence=0.99,
    )


# ─── A & B: VLLMJudgeClient Success & Payload Schema ──────────────────────────

def test_vllm_judge_client_success_and_payload_structure():
    """Requirement A & B: Verify payload includes response_format JSON schema and parses response."""
    captured_requests = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        body = json.loads(request.content)
        # Verify OpenAI-compatible payload structure
        assert body["model"] == "Qwen/Qwen2.5-7B-Instruct"
        assert body["temperature"] == 0.0
        assert body["seed"] == 42
        assert body["max_tokens"] == 256
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == DEFAULT_STAGE_C_SYSTEM_PROMPT
        assert body["messages"][1]["role"] == "user"

        # Requirement B: response_format JSON schema
        assert "response_format" in body
        rf = body["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "stage_c_judge_output"
        assert rf["json_schema"]["strict"] is True
        schema = rf["json_schema"]["schema"]
        assert "properties" in schema
        assert "label" in schema["properties"]
        assert "confidence" in schema["properties"]
        assert "contradiction_type" in schema["properties"]
        assert "rationale" in schema["properties"]

        res_content = json.dumps({
            "label": "SUPPORTED",
            "confidence": 0.96,
            "contradiction_type": None,
            "rationale": "Claim Lisinopril is supported verbatim by evidence.",
        })
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": res_content}}]},
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    client = VLLMJudgeClient(http_client=mock_client)

    resp_str = client.generate("Verify claim: Lisinopril", StageCJudgeOutput)
    parsed = json.loads(resp_str)
    assert parsed["label"] == "SUPPORTED"
    assert parsed["confidence"] == 0.96
    assert len(captured_requests) == 1


# ─── C: HTTP Failure Retry ───────────────────────────────────────────────────

def test_stage_c_judge_http_failure_retry():
    """Requirement C: Retry on HTTP transport failures (up to 3 total attempts)."""
    attempt_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 2:
            return httpx.Response(500, text="Internal Server Error")
        # Succeed on 2nd attempt
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "label": "SUPPORTED",
                "confidence": 0.95,
                "contradiction_type": None,
                "rationale": "Verified after recovery.",
            })}}]},
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    stage_c = StageCJudge(client=VLLMJudgeClient(http_client=mock_client))
    res = stage_c.judge(claim=_make_claim(), evidence=_make_evidence())

    assert res.label == "SUPPORTED"
    assert res.attempts == 2
    assert attempt_count == 2


# ─── D: Malformed JSON Retry ─────────────────────────────────────────────────

def test_stage_c_judge_malformed_json_retry():
    """Requirement D: Retry when LLM generates unparseable malformed JSON."""
    attempt_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 2:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "INVALID NON-JSON OUTPUT {{"}}]},
            )
        # Succeed on 2nd attempt
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "label": "SUPPORTED",
                "confidence": 0.90,
                "contradiction_type": None,
                "rationale": "Valid JSON generated on second attempt.",
            })}}]},
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    stage_c = StageCJudge(client=VLLMJudgeClient(http_client=mock_client))
    res = stage_c.judge(claim=_make_claim(), evidence=_make_evidence())

    assert res.label == "SUPPORTED"
    assert res.attempts == 2
    assert attempt_count == 2


# ─── E: Schema Validation Failure Retry ──────────────────────────────────────

def test_stage_c_judge_schema_validation_failure_retry():
    """Requirement E: Retry when output violates Pydantic invariants (e.g. DISCREPANCY without contradiction_type)."""
    attempt_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            # Missing required contradiction_type on DISCREPANCY
            invalid = {"label": "DISCREPANCY", "confidence": 0.85, "contradiction_type": None, "rationale": "Mismatch."}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(invalid)}}]})
        # Fixed valid schema
        valid = {"label": "DISCREPANCY", "confidence": 0.85, "contradiction_type": "value_mismatch", "rationale": "Value mismatch detected."}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(valid)}}]})

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    stage_c = StageCJudge(client=VLLMJudgeClient(http_client=mock_client))
    res = stage_c.judge(claim=_make_claim(), evidence=_make_evidence())

    assert res.label == "DISCREPANCY"
    assert res.contradiction_type == "value_mismatch"
    assert res.attempts == 2
    assert attempt_count == 2


# ─── F & G: Max 3 Attempts & Safe Fallback ───────────────────────────────────

def test_stage_c_judge_three_attempts_maximum_and_safe_fallback():
    """Requirements F & G: Maximum 3 total generation attempts, followed by safe INSUFFICIENT_EVIDENCE fallback."""
    attempt_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        # Always fail with server error
        return httpx.Response(503, text="Service Unavailable")

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    stage_c = StageCJudge(client=VLLMJudgeClient(http_client=mock_client))
    res = stage_c.judge(claim=_make_claim(), evidence=_make_evidence())

    # Requirement F: Exactly 3 attempts
    assert attempt_count == 3
    assert res.attempts == 3

    # Requirement G: Safe fallback
    assert res.label == "INSUFFICIENT_EVIDENCE"
    assert res.raw_confidence == 0.50
    assert res.calibrated_confidence == 0.50
    assert res.contradiction_type is None
    assert "generation/validation failed after 3 attempts" in res.rationale


# ─── H: DefaultMockLLMJudge Remains Unchanged ────────────────────────────────

def test_default_mock_llm_judge_remains_unchanged():
    """Requirement H: Verify DefaultMockLLMJudge functions identically and deterministically."""
    mock = DefaultMockLLMJudge()
    resp_discrepancy = mock.generate("Prompt mentioning discrepancy in dose", StageCJudgeOutput)
    parsed_d = json.loads(resp_discrepancy)
    assert parsed_d["label"] == "DISCREPANCY"
    assert parsed_d["contradiction_type"] == "dose_mismatch"
    assert parsed_d["confidence"] == 0.88

    resp_insufficient = mock.generate("Prompt with missing evidence text", StageCJudgeOutput)
    parsed_i = json.loads(resp_insufficient)
    assert parsed_i["label"] == "INSUFFICIENT_EVIDENCE"
    assert parsed_i["confidence"] == 0.92

    resp_supported = mock.generate("Patient took standard dose", StageCJudgeOutput)
    parsed_s = json.loads(resp_supported)
    assert parsed_s["label"] == "SUPPORTED"
    assert parsed_s["confidence"] == 0.95


# ─── I: Full VerificationCascade Integration with VLLMJudgeClient ────────────

def test_verification_cascade_integration_with_vllm_client():
    """Requirement I: Full VerificationCascade works with an injected fake VLLM client without network access."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        res = {
            "label": "DISCREPANCY",
            "confidence": 0.91,
            "contradiction_type": "value_mismatch",
            "rationale": "Extracted value 180 contradicts source evidence value 100.",
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(res)}}]})

    mock_http = httpx.Client(transport=httpx.MockTransport(mock_handler))
    vllm_client = VLLMJudgeClient(http_client=mock_http)

    cascade = VerificationCascade(
        stage_a=DeterministicVerifier(),
        # Stage B low confidence triggers escalation to Stage C
        stage_b=LearnedVerifier(custom_predictor=MockModelPredictor(default_label="SUPPORTED", confidence=0.40)),
        stage_c=StageCJudge(client=vllm_client),
    )

    claim = _make_claim(field_type="lab.value", value="Glucose: 180 mg/dL", value_numeric=180.0, unit_raw="mg/dL")
    evidence = _make_evidence("Glucose: 100 mg/dL")

    v_res = cascade.verify_claim(claim=claim, evidence=evidence)

    assert v_res.stage_reached == "C"
    assert v_res.escalated is True
    assert v_res.label == "DISCREPANCY"
    assert v_res.contradiction_type == "value_mismatch"
    assert v_res.rationale == "Extracted value 180 contradicts source evidence value 100."
    assert 0.0 <= v_res.confidence <= 1.0


# ─── J: Full Claim Context Reaches the Stage-C Prompt ────────────────────────

def test_full_claim_context_reaches_stage_c_prompt():
    """Requirement J: Preserve full claim context (not stripped) in the prompt sent to Stage C."""
    captured_prompt = ""

    class CapturingClient:
        def generate(self, prompt: str, schema: type[Any]) -> str:
            nonlocal captured_prompt
            captured_prompt = prompt
            return json.dumps({
                "label": "SUPPORTED",
                "confidence": 0.95,
                "contradiction_type": None,
                "rationale": "Claim context verified in prompt.",
            })

    stage_c = StageCJudge(client=CapturingClient())
    full_claim_str = "Glucose: 66 mg/dL (fasting)"
    claim = _make_claim(
        field_type="lab.value",
        value=full_claim_str,
        value_numeric=66.0,
        unit_raw="mg/dL",
    )
    evidence = _make_evidence("Fasting blood glucose measured at 66 mg/dL.")

    res = stage_c.judge(claim, evidence)
    assert res.label == "SUPPORTED"

    # Verify that the full unstripped claim representation reached the prompt
    assert full_claim_str in captured_prompt
    assert "- Field Type: lab.value" in captured_prompt
    assert "- Numeric Value: 66.0" in captured_prompt
    assert "- Unit: mg/dL" in captured_prompt
    assert "Fasting blood glucose measured at 66 mg/dL." in captured_prompt


# ─── K: VLLMJudgeClient Timeout Initialization Regression Test ──────────────

def test_vllm_judge_client_default_initialization_has_timeout_and_enters_request_path(monkeypatch):
    """Regression test: Ensure VLLMJudgeClient initializes timeout and enters request/retry path without AttributeError."""
    # 1. Default initialization (http_client=None) must set timeout attribute
    client = VLLMJudgeClient(timeout=45.0)
    assert hasattr(client, "timeout")
    assert client.timeout == 45.0
    assert client._http_client is not None
    assert isinstance(client._http_client, httpx.Client)
    assert client._http_client.timeout.read == 45.0

    # 2. Verify request execution uses timeout without AttributeError
    captured_kwargs = {}

    def mock_post(self, url, **kwargs):
        captured_kwargs.update(kwargs)
        res_content = json.dumps({
            "label": "SUPPORTED",
            "confidence": 0.95,
            "contradiction_type": None,
            "rationale": "Verified through standard client post.",
        })
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": res_content}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    out = client.generate("Test prompt", StageCJudgeOutput)
    parsed = json.loads(out)
    assert parsed["label"] == "SUPPORTED"

    # 3. Verify fallback path in generate() when _http_client is None also accesses self.timeout safely
    client_fallback = VLLMJudgeClient(timeout=15.0)
    client_fallback._http_client = None

    captured_init_timeout = None
    orig_client_init = httpx.Client.__init__

    def mock_client_init(self, *args, **kwargs):
        nonlocal captured_init_timeout
        captured_init_timeout = kwargs.get("timeout")
        orig_client_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", mock_client_init)

    out2 = client_fallback.generate("Test fallback prompt", StageCJudgeOutput)
    assert captured_init_timeout == 15.0
    parsed2 = json.loads(out2)
    assert parsed2["label"] == "SUPPORTED"

    # 4. Verify StageCJudge retry loop runs without AttributeError
    call_count = 0

    def mock_failing_then_success_post(self, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Connection refused")
        res_content = json.dumps({
            "label": "SUPPORTED",
            "confidence": 0.92,
            "contradiction_type": None,
            "rationale": "Succeeded on retry.",
        })
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": res_content}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", mock_failing_then_success_post)

    stage_c = StageCJudge(client=VLLMJudgeClient(timeout=20.0))
    res = stage_c.judge(claim=_make_claim(), evidence=_make_evidence())

    assert res.label == "SUPPORTED"
    assert res.attempts == 2
    assert res.is_fallback is False
    assert res.error is None


# ─── L: Lab Flag Tabular Precedence Instruction Regression Test ─────────────

def test_stage_c_prompt_includes_lab_flag_tabular_instruction():
    """Requirement L: Verify lab.flag prompt includes tabular precedence instruction and logs prompt version."""
    captured_prompts: list[str] = []

    class CapturingClient:
        def generate(self, prompt: str, schema: type[Any]) -> str:
            captured_prompts.append(prompt)
            return json.dumps({
                "label": "SUPPORTED",
                "confidence": 0.98,
                "contradiction_type": None,
                "rationale": "Verified with tabular flag instruction.",
            })

    stage_c = StageCJudge(client=CapturingClient())

    # 1. Lab flag claim MUST include the tabular precedence instruction
    flag_claim = _make_claim(
        field_type="lab.flag",
        value="Albumin flag: HIGH",
    )
    flag_evidence = _make_evidence("Albumin 5.6 g/dL 3.5 - 5.0 H")

    res_flag = stage_c.judge(flag_claim, flag_evidence)
    assert res_flag.label == "SUPPORTED"
    assert res_flag.prompt_version == STAGE_C_PROMPT_VERSION
    assert res_flag.prompt_version == "V8_STAGE_C_LABFLAG_PROMPT_V1"

    assert len(captured_prompts) == 1
    prompt_flag = captured_prompts[0]
    assert "[FIELD-SPECIFIC INSTRUCTIONS]" in prompt_flag
    assert "When evaluating lab.flag claims against structured laboratory tables:" in prompt_flag
    assert "1. Explicit abnormal column indicators take strict precedence: a trailing 'H' or 'HIGH' explicitly denotes HIGH; a trailing 'L' or 'LOW' explicitly denotes LOW; an unflagged or blank abnormal entry denotes NORMAL." in prompt_flag
    assert "2. Do NOT attempt to recompute, recalculate, or verify numerical intervals if an explicit flag indicator ('H', 'L', 'HIGH', 'LOW', or blank) is present in the source table row." in prompt_flag

    # 2. Non-lab.flag claim (e.g. med.name) MUST NOT include the tabular instruction (one-variable isolation)
    med_claim = _make_claim(
        field_type="med.name",
        value="Medication: Lisinopril 10mg",
    )
    med_evidence = _make_evidence("Lisinopril 10mg PO daily")

    res_med = stage_c.judge(med_claim, med_evidence)
    assert res_med.label == "SUPPORTED"
    assert res_med.prompt_version == STAGE_C_PROMPT_VERSION

    assert len(captured_prompts) == 2
    prompt_med = captured_prompts[1]
    assert "[FIELD-SPECIFIC INSTRUCTIONS]" not in prompt_med
    assert "When evaluating lab.flag claims" not in prompt_med


