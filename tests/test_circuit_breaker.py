"""tests/test_circuit_breaker.py — Unit and integration tests for Model Serving Resilience.

Authority:
  - PROJECT_SPEC.md §3, §12, §13.3
  - PRODUCTIZATION_SPEC.md §3.3, §3.4, §3.5, §13 (Failures F-05, F-06, F-07)
  - PRODUCTIZATION_DECISIONS.md Phase 8.7 (Secure Model Serving Infrastructure)

Covers:
  1. CLOSED -> OPEN after N consecutive qualifying failures.
  2. OPEN fail-fast behavior without invoking backend.
  3. OPEN -> HALF_OPEN after configurable cool-down.
  4. HALF_OPEN success -> CLOSED and failure reset.
  5. HALF_OPEN failure -> OPEN.
  6. Consecutive failure reset after intermittent success.
  7. Concurrency cap enforcement and deterministic rejection/timeout.
  8. Exponential backoff calculation and factor scaling.
  9. Randomized jitter bounds ([0, raw_delay]).
  10. Retry count limits (Judge: 3 attempts / max 2 retries; Extractor: max 2 retries).
  11. Non-retryable error fail-fast behavior (CircuitBreakerOpenError, 401, etc.).
  12. Production configuration fail-closed invariants for resilience settings.
  13. Production profile containment for mock/model endpoints.
  14. Integration with actual VLLMJudgeClient and VLLMProvider.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any
import httpx
import pytest

from clindoc.config import (
    CANONICAL_MODERNBERT_CHECKPOINT,
    ProductionPipelineConfig,
    ProductionProfileError,
    RuntimeProfile,
)
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.ocr.document_text import DocumentPage
from clindoc.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    ConcurrencyLimiter,
    ConcurrencyLimitExceededError,
    ExponentialBackoff,
    is_qualifying_breaker_failure,
    is_retryable_error,
    reset_shared_registries,
)
from clindoc.verify.stage_c import (
    DefaultMockLLMJudge,
    StageCJudge,
    StageCJudgeOutput,
    VLLMJudgeClient,
)
from clindoc.extract.llm_extractor import (
    MockLLMProvider,
    NarrativeExtractor,
    VLLMProvider,
)


@pytest.fixture(autouse=True)
def clean_registries():
    """Ensure shared circuit breaker and concurrency limiter registries are reset."""
    reset_shared_registries()
    yield
    reset_shared_registries()


def _make_claim(
    field_type: str = "med.name",
    value: str = "Lisinopril 10mg",
) -> Claim:
    return Claim(
        claim_id="claim-cb-001",
        document_id="doc-cb-001",
        group_id="group-cb-001",
        field_type=field_type,
        value=value,
        extraction_method="deterministic",
        extraction_confidence=1.0,
        evidence_id="ev-cb-001",
        region_id="reg-cb-001",
    )


def _make_evidence(text: str = "Lisinopril 10mg PO daily") -> Evidence:
    return Evidence(
        evidence_id="ev-cb-001",
        document_id="doc-cb-001",
        page_number=1,
        text=text,
        char_start=0,
        char_end=len(text),
        bbox=BBoxModel(x0=10.0, y0=20.0, x1=100.0, y1=40.0),
        ocr_confidence=0.99,
    )


# ─── 1. CLOSED -> OPEN after N consecutive failures ─────────────────────────

def test_circuit_breaker_closed_to_open_after_n_failures():
    """Requirement 1: CLOSED -> OPEN after N consecutive qualifying failures."""
    mock_time = 1000.0
    cb = CircuitBreaker(
        name="test_cb",
        failure_threshold=3,
        cooldown_seconds=10.0,
        time_func=lambda: mock_time,
    )

    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0

    # 1st failure
    cb.record_failure(httpx.ConnectError("Connection refused"))
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 1

    # 2nd failure
    cb.record_failure(httpx.ReadTimeout("Timeout reading socket"))
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 2

    # 3rd qualifying failure -> trips to OPEN
    cb.record_failure(httpx.HTTPStatusError("503 Server Error", request=None, response=httpx.Response(503)))
    assert cb.state == CircuitState.OPEN
    assert cb.consecutive_failures == 3
    assert cb.last_failure_time == 1000.0


# ─── 2. OPEN Fail-Fast Behavior ──────────────────────────────────────────────

def test_circuit_breaker_open_fail_fast_without_request():
    """Requirement 2: In OPEN state, calls fail fast with CircuitBreakerOpenError without invoking backend."""
    mock_time = 1000.0
    cb = CircuitBreaker(
        name="test_cb",
        failure_threshold=2,
        cooldown_seconds=30.0,
        time_func=lambda: mock_time,
    )

    # Trip breaker
    cb.record_failure(httpx.ConnectError("Err 1"))
    cb.record_failure(httpx.ConnectError("Err 2"))
    assert cb.state == CircuitState.OPEN

    # Call inside context manager should fail fast immediately
    called = False

    def target_call():
        nonlocal called
        called = True
        return "success"

    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        cb.execute(target_call)

    assert called is False
    assert "Circuit breaker 'test_cb' is OPEN" in str(exc_info.value)
    assert exc_info.value.cooldown_remaining == pytest.approx(30.0)


# ─── 3. OPEN -> HALF_OPEN After Cool-down ────────────────────────────────────

def test_circuit_breaker_open_to_half_open_after_cooldown():
    """Requirement 3: OPEN transitions to HALF_OPEN after configurable cool-down expires."""
    current_time = 1000.0
    cb = CircuitBreaker(
        name="test_cb",
        failure_threshold=1,
        cooldown_seconds=15.0,
        time_func=lambda: current_time,
    )

    cb.record_failure(httpx.ConnectError("Down"))
    assert cb.state == CircuitState.OPEN

    # Advance time before cooldown expires
    current_time = 1010.0
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # Advance time past cooldown
    current_time = 1016.0
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.can_execute() is True


# ─── 4. HALF_OPEN Success -> CLOSED ─────────────────────────────────────────

def test_circuit_breaker_half_open_success_closes_and_resets():
    """Requirement 4: Successful probe request in HALF_OPEN transitions to CLOSED and resets failures."""
    current_time = 1000.0
    cb = CircuitBreaker(
        name="test_cb",
        failure_threshold=2,
        cooldown_seconds=20.0,
        time_func=lambda: current_time,
    )

    cb.record_failure(httpx.ConnectError("Err 1"))
    cb.record_failure(httpx.ConnectError("Err 2"))
    assert cb.state == CircuitState.OPEN

    # Elapse cooldown -> HALF_OPEN
    current_time = 1025.0
    assert cb.state == CircuitState.HALF_OPEN

    # Execute successful probe
    with cb:
        pass  # Successful execution

    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0
    assert cb.last_failure_time is None


# ─── 5. HALF_OPEN Failure -> OPEN ────────────────────────────────────────────

def test_circuit_breaker_half_open_failure_reopens():
    """Requirement 5: Failed probe request in HALF_OPEN transitions immediately back to OPEN."""
    current_time = 1000.0
    cb = CircuitBreaker(
        name="test_cb",
        failure_threshold=2,
        cooldown_seconds=20.0,
        time_func=lambda: current_time,
    )

    cb.record_failure(httpx.ConnectError("Err 1"))
    cb.record_failure(httpx.ConnectError("Err 2"))
    assert cb.state == CircuitState.OPEN

    # Advance past cooldown
    current_time = 1025.0
    assert cb.state == CircuitState.HALF_OPEN

    # Failed probe execution
    with pytest.raises(httpx.ConnectError):
        with cb:
            raise httpx.ConnectError("Probe connection failed")

    assert cb.state == CircuitState.OPEN
    assert cb.last_failure_time == 1025.0


# ─── 6. Consecutive Failure Reset After Success ──────────────────────────────

def test_circuit_breaker_consecutive_failure_reset_after_success():
    """Requirement 6: Consecutive failure counter resets to 0 after an intermittent success in CLOSED state."""
    cb = CircuitBreaker(name="test_cb", failure_threshold=3, cooldown_seconds=10.0)

    cb.record_failure(httpx.ConnectError("Err 1"))
    cb.record_failure(httpx.ConnectError("Err 2"))
    assert cb.consecutive_failures == 2
    assert cb.state == CircuitState.CLOSED

    # Intermittent success
    cb.record_success()
    assert cb.consecutive_failures == 0
    assert cb.state == CircuitState.CLOSED

    # Another failure starts counter from 1 again
    cb.record_failure(httpx.ConnectError("Err 3"))
    assert cb.consecutive_failures == 1
    assert cb.state == CircuitState.CLOSED


# ─── 7. Concurrency Cap Enforcement ──────────────────────────────────────────

def test_concurrency_limiter_cap_and_timeout():
    """Requirement 7: Concurrency cap permits up to max_concurrency simultaneous calls; excess times out."""
    limiter = ConcurrencyLimiter(max_concurrency=2, timeout=0.05)
    acquired_count = 0
    blocked_error = None
    stop_event = threading.Event()

    def worker():
        nonlocal acquired_count, blocked_error
        try:
            with limiter:
                acquired_count += 1
                stop_event.wait(timeout=2.0)
        except ConcurrencyLimitExceededError as e:
            blocked_error = e

    # Launch 2 worker threads holding the 2 permits
    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()

    time.sleep(0.02)
    assert limiter.active_count == 2
    assert acquired_count == 2

    # A 3rd attempt must time out and raise ConcurrencyLimitExceededError
    with pytest.raises(ConcurrencyLimitExceededError) as exc_info:
        with limiter:
            pass

    assert "Model server concurrency limit (2) exceeded" in str(exc_info.value)
    assert exc_info.value.max_concurrency == 2

    # Release workers
    stop_event.set()
    t1.join(timeout=1.0)
    t2.join(timeout=1.0)
    assert limiter.active_count == 0


# ─── 8. Exponential Backoff Calculation ──────────────────────────────────────

def test_exponential_backoff_calculation():
    """Requirement 8: Backoff delay scales exponentially by attempt and respects max_delay."""
    backoff = ExponentialBackoff(
        base_delay=0.5,
        max_delay=5.0,
        factor=2.0,
        jitter=False,  # Un-jittered for exact calculation verification
    )

    # Attempt 1 (1st retry): base * (2 ** 0) = 0.5
    assert backoff.compute_delay(1) == 0.5
    # Attempt 2 (2nd retry): base * (2 ** 1) = 1.0
    assert backoff.compute_delay(2) == 1.0
    # Attempt 3 (3rd retry): base * (2 ** 2) = 2.0
    assert backoff.compute_delay(3) == 2.0
    # Attempt 4: base * (2 ** 3) = 4.0
    assert backoff.compute_delay(4) == 4.0
    # Attempt 5: base * (2 ** 4) = 8.0 -> capped at max_delay 5.0
    assert backoff.compute_delay(5) == 5.0


# ─── 9. Randomized Jitter Bounds ─────────────────────────────────────────────

def test_randomized_jitter_bounds():
    """Requirement 9: With jitter enabled, computed delay is strictly in [0.0, raw_delay]."""
    backoff = ExponentialBackoff(
        base_delay=1.0,
        max_delay=10.0,
        factor=2.0,
        jitter=True,
    )

    for attempt in range(1, 6):
        raw_upper = min(10.0, 1.0 * (2.0 ** (attempt - 1)))
        for _ in range(50):
            d = backoff.compute_delay(attempt)
            assert 0.0 <= d <= raw_upper


# ─── 10. Retry Count Limits (Judge & Extractor) ──────────────────────────────

def test_stage_c_judge_authoritative_three_attempts_limit():
    """Requirement 10A: StageCJudge strictly enforces 3 total attempts (max 2 retries)."""
    call_count = 0
    sleep_calls = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(503, text="Service Unavailable")

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    cb = CircuitBreaker(name="test_cb", failure_threshold=10, cooldown_seconds=30.0)
    client = VLLMJudgeClient(http_client=mock_client, circuit_breaker=cb)

    backoff = ExponentialBackoff(
        base_delay=0.1,
        max_delay=1.0,
        sleep_func=lambda s: sleep_calls.append(s),
    )
    judge = StageCJudge(client=client, max_attempts=3, backoff=backoff)
    res = judge.judge(claim=_make_claim(), evidence=_make_evidence())

    assert call_count == 3
    assert res.attempts == 3
    assert res.is_fallback is True
    # Backoff sleep executed between attempt 1->2 and 2->3 (exactly 2 retries)
    assert len(sleep_calls) == 2


def test_narrative_extractor_authoritative_two_retries_limit():
    """Requirement 10B: NarrativeExtractor strictly enforces max 2 retries (3 total attempts)."""
    call_count = 0
    sleep_calls = []

    class FailingProvider:
        def complete(self, sys: str, user: str) -> str:
            nonlocal call_count
            call_count += 1
            raise httpx.ReadTimeout("Timeout")

    backoff = ExponentialBackoff(
        base_delay=0.1,
        max_delay=1.0,
        sleep_func=lambda s: sleep_calls.append(s),
    )
    extractor = NarrativeExtractor(
        provider=FailingProvider(),
        max_retries=2,
        backoff=backoff,
    )

    page = DocumentPage(
        page_number=1,
        text="Clinical narrative note.",
        tokens=[],
        lines=[],
        paragraphs=[],
        width_px=612.0,
        height_px=792.0,
    )
    evidences, claims, diag = extractor.extract(
        document_id="doc-cb-001",
        page=page,
        region_text="Clinical narrative note.",
        region_id="reg-cb-001",
    )

    assert call_count == 3
    assert diag["retries"] == 2
    assert len(sleep_calls) == 2
    assert len(claims) == 0


# ─── 11. Non-Retryable Error Fail-Fast Behavior ──────────────────────────────

def test_non_retryable_error_fails_fast_without_retrying():
    """Requirement 11: Non-retryable errors (e.g. 401 Unauthorized, CircuitBreakerOpenError) terminate on attempt 1."""
    call_count = 0
    sleep_calls = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, text="Unauthorized: Invalid Token")

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    cb = CircuitBreaker(name="test_cb", failure_threshold=10, cooldown_seconds=30.0)
    client = VLLMJudgeClient(http_client=mock_client, circuit_breaker=cb)

    backoff = ExponentialBackoff(
        base_delay=0.1,
        max_delay=1.0,
        sleep_func=lambda s: sleep_calls.append(s),
    )
    judge = StageCJudge(client=client, max_attempts=3, backoff=backoff)
    res = judge.judge(claim=_make_claim(), evidence=_make_evidence())

    # Fails fast on 1st attempt! No sleeps, no retries
    assert call_count == 1
    assert res.attempts == 1
    assert res.is_fallback is True
    assert len(sleep_calls) == 0


def test_is_retryable_error_classification():
    """Requirement 11B: Verify error classification strictly distinguishes retryable vs non-retryable."""
    # Retryable: transport & timeouts
    assert is_retryable_error(httpx.ReadTimeout("Timeout")) is True
    assert is_retryable_error(httpx.ConnectError("Network error")) is True
    assert is_retryable_error(httpx.HTTPStatusError("503", request=None, response=httpx.Response(503))) is True
    assert is_retryable_error(httpx.HTTPStatusError("502", request=None, response=httpx.Response(502))) is True
    assert is_retryable_error(httpx.HTTPStatusError("429", request=None, response=httpx.Response(429))) is True

    # Retryable: schema / parsing
    assert is_retryable_error(json.JSONDecodeError("Expecting value", "{", 0)) is True
    assert is_retryable_error(ValueError("contradiction_type must not be null when label is DISCREPANCY")) is True

    # Non-retryable: client auth, circuit breaker open, programming bugs
    assert is_retryable_error(CircuitBreakerOpenError("Circuit open")) is False
    assert is_retryable_error(ConcurrencyLimitExceededError("Limit reached")) is False
    assert is_retryable_error(httpx.HTTPStatusError("401", request=None, response=httpx.Response(401))) is False
    assert is_retryable_error(httpx.HTTPStatusError("403", request=None, response=httpx.Response(403))) is False
    assert is_retryable_error(httpx.HTTPStatusError("404", request=None, response=httpx.Response(404))) is False
    assert is_retryable_error(TypeError("Unrelated bug")) is False
    assert is_retryable_error(KeyError("Missing key")) is False


# ─── 12. Production Configuration Fail-Closed Invariants ─────────────────────

def test_production_pipeline_config_resilience_invariants():
    """Requirement 12: ProductionPipelineConfig strictly validates circuit breaker, concurrency, and backoff."""
    valid_cfg = ProductionPipelineConfig(
        profile=RuntimeProfile.PRODUCTION,
        modernbert_checkpoint_path=CANONICAL_MODERNBERT_CHECKPOINT,
        circuit_breaker_failure_threshold=5,
        circuit_breaker_cooldown_seconds=30.0,
        model_concurrency_limit=10,
        backoff_base_delay=0.5,
        backoff_max_delay=8.0,
    )
    valid_cfg.validate()

    # Invalid failure threshold (< 1)
    with pytest.raises(ProductionProfileError, match="circuit_breaker_failure_threshold must be >= 1"):
        invalid_cfg = ProductionPipelineConfig(
            profile=RuntimeProfile.PRODUCTION,
            modernbert_checkpoint_path=CANONICAL_MODERNBERT_CHECKPOINT,
            circuit_breaker_failure_threshold=0,
        )
        invalid_cfg.validate()

    # Invalid cooldown (<= 0)
    with pytest.raises(ProductionProfileError, match="circuit_breaker_cooldown_seconds must be > 0"):
        invalid_cfg = ProductionPipelineConfig(
            profile=RuntimeProfile.PRODUCTION,
            modernbert_checkpoint_path=CANONICAL_MODERNBERT_CHECKPOINT,
            circuit_breaker_cooldown_seconds=-5.0,
        )
        invalid_cfg.validate()

    # Invalid concurrency limit (< 1)
    with pytest.raises(ProductionProfileError, match="model_concurrency_limit must be >= 1"):
        invalid_cfg = ProductionPipelineConfig(
            profile=RuntimeProfile.PRODUCTION,
            modernbert_checkpoint_path=CANONICAL_MODERNBERT_CHECKPOINT,
            model_concurrency_limit=0,
        )
        invalid_cfg.validate()

    # Invalid backoff (max < base)
    with pytest.raises(ProductionProfileError, match="backoff_max_delay .* must be >= backoff_base_delay"):
        invalid_cfg = ProductionPipelineConfig(
            profile=RuntimeProfile.PRODUCTION,
            modernbert_checkpoint_path=CANONICAL_MODERNBERT_CHECKPOINT,
            backoff_base_delay=5.0,
            backoff_max_delay=2.0,
        )
        invalid_cfg.validate()


# ─── 13. Production Profile Containment Intact ────────────────────────────────

def test_production_profile_containment_intact():
    """Requirement 13: StageCJudge in PRODUCTION profile forbids DefaultMockLLMJudge."""
    with pytest.raises(ProductionProfileError, match="DefaultMockLLMJudge is forbidden in PRODUCTION profile"):
        StageCJudge(
            client=DefaultMockLLMJudge(),
            profile=RuntimeProfile.PRODUCTION,
            allow_mock_client=False,
        )


# ─── 14. Integration with VLLMJudgeClient & VLLMProvider ─────────────────────

def test_vllm_judge_client_circuit_breaker_integration():
    """Requirement 14A: VLLMJudgeClient interacts with CircuitBreaker and fails fast when open."""
    current_time = 1000.0
    cb = CircuitBreaker(
        name="test_vllm_cb",
        failure_threshold=2,
        cooldown_seconds=60.0,
        time_func=lambda: current_time,
    )
    limiter = ConcurrencyLimiter(max_concurrency=5)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    client = VLLMJudgeClient(
        http_client=mock_client,
        circuit_breaker=cb,
        concurrency_limiter=limiter,
    )

    # 1st call fails
    with pytest.raises(httpx.HTTPStatusError):
        client.generate("Verify", StageCJudgeOutput)
    assert cb.consecutive_failures == 1
    assert cb.state == CircuitState.CLOSED

    # 2nd call fails -> trips breaker to OPEN
    with pytest.raises(httpx.HTTPStatusError):
        client.generate("Verify", StageCJudgeOutput)
    assert cb.consecutive_failures == 2
    assert cb.state == CircuitState.OPEN

    # 3rd call must fail fast with CircuitBreakerOpenError without HTTP transport call
    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        client.generate("Verify", StageCJudgeOutput)
    assert "Circuit breaker 'test_vllm_cb' is OPEN" in str(exc_info.value)


def test_vllm_provider_circuit_breaker_integration():
    """Requirement 14B: VLLMProvider interacts with CircuitBreaker and ConcurrencyLimiter."""
    cb = CircuitBreaker(name="test_provider_cb", failure_threshold=2, cooldown_seconds=60.0)
    limiter = ConcurrencyLimiter(max_concurrency=1, timeout=0.01)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Error")

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    provider = VLLMProvider(
        base_url="http://localhost:8000/v1",
        circuit_breaker=cb,
        concurrency_limiter=limiter,
        http_client=mock_client,
    )

    with pytest.raises(httpx.HTTPStatusError):
        provider.complete("System prompt", "User prompt")
    assert cb.consecutive_failures == 1

    with pytest.raises(httpx.HTTPStatusError):
        provider.complete("System prompt", "User prompt")
    assert cb.consecutive_failures == 2
    assert cb.state == CircuitState.OPEN

    # 3rd call fails fast
    with pytest.raises(CircuitBreakerOpenError):
        provider.complete("System prompt", "User prompt")

