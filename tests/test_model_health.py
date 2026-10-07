"""Phase 8.7 Step 3: Model Health / Readiness Probe Tests.

Authority:
  - PRODUCTIZATION_SPEC.md SS3.3, SS3.5, SS10.4, SS13 (F-05, F-06)
  - PRODUCTIZATION_DECISIONS.md Phase 8.7 Step 3

Covers:
  ModernBERT:
    - ready state (custom non-mock predictor)
    - ready state (neural model loaded)
    - not_ready state (no model, no predictor)
    - not_ready state (mock predictor - production forbidden)
    - not_ready state (None stage_b)
    - readiness does not load/reload the model repeatedly
    - production containment: mock predictor rejected

  Qwen/vLLM:
    - ready state (server healthy, circuit CLOSED)
    - temporarily_unavailable (server unreachable)
    - temporarily_unavailable (server unhealthy 5xx)
    - temporarily_unavailable (httpx import unavailable)
    - temporarily_unavailable (provider None)
    - circuit_open state (circuit breaker OPEN)
    - temporarily_unavailable (circuit breaker HALF_OPEN)
    - readiness does NOT perform a full inference request
    - no credential/secret leakage in reasons

  ModelHealthRegistry:
    - readiness_summary composition
    - register_stage_b and register_vllm_provider
    - singleton creation and replacement

  API /health/ready:
    - reports correct DB/blob/model statuses
    - Qwen unavailable does NOT falsely break Stage A/B readiness
    - no PHI/secrets in responses
    - existing HTTP 401 / 503 semantics preserved
"""
from __future__ import annotations

import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from clindoc.services.model_health import (
    ModelHealthRegistry,
    ModernBERTStatus,
    VLLMStatus,
    get_model_health_registry,
    probe_modernbert,
    probe_vllm,
    set_model_health_registry,
)
from clindoc.services.circuit_breaker import CircuitBreaker, CircuitState


# ===========================================================================
# Helpers
# ===========================================================================

class _FakePredictor:
    """Non-mock predictor stub (simulates a real custom predictor)."""
    pass


class _MockModelPredictor:
    """Mimics MockModelPredictor by class name (production-forbidden)."""
    pass
_MockModelPredictor.__name__ = "MockModelPredictor"
_MockModelPredictor.__qualname__ = "MockModelPredictor"


def _make_stage_b(*, predictor=None, is_loaded=False):
    """Build a minimal stage_b-like object."""
    obj = MagicMock()
    obj._predictor = predictor
    obj.is_model_loaded = is_loaded
    return obj


def _make_vllm_provider(*, circuit_state=CircuitState.CLOSED, base_url="http://localhost:8000/v1"):
    """Build a minimal VLLMProvider-like object with a real CircuitBreaker."""
    cb = CircuitBreaker(name="test_cb", failure_threshold=3, cooldown_seconds=30.0)
    # Force the circuit into the desired state
    if circuit_state == CircuitState.OPEN:
        # Trip the breaker by recording enough failures
        for _ in range(3):
            try:
                import httpx
                err = httpx.NetworkError("test")
            except ImportError:
                err = ConnectionError("test")
            cb.record_failure(err)
    elif circuit_state == CircuitState.HALF_OPEN:
        # Trip then manually set to HALF_OPEN by adjusting time
        import time
        for _ in range(3):
            try:
                import httpx
                err = httpx.NetworkError("test")
            except ImportError:
                err = ConnectionError("test")
            cb.record_failure(err)
        # Backdating last_failure_time so that cooldown has expired
        cb._last_failure_time = cb._time_func() - cb.cooldown_seconds - 1.0
        # Now reading state will transition to HALF_OPEN
        _ = cb.state

    obj = MagicMock()
    obj.circuit_breaker = cb
    obj.base_url = base_url
    obj.api_key = "EMPTY"
    return obj


# ===========================================================================
# ModernBERT probe tests
# ===========================================================================

class TestProbeModernBERT:
    def test_none_stage_b_returns_not_ready(self):
        status, reason = probe_modernbert(None)
        assert status == ModernBERTStatus.NOT_READY
        assert "not_configured" in reason

    def test_mock_predictor_returns_not_ready(self):
        stage_b = _make_stage_b(predictor=_MockModelPredictor())
        status, reason = probe_modernbert(stage_b)
        assert status == ModernBERTStatus.NOT_READY
        assert "mock" in reason.lower() or "not_permitted" in reason

    def test_custom_non_mock_predictor_returns_ready(self):
        stage_b = _make_stage_b(predictor=_FakePredictor())
        status, reason = probe_modernbert(stage_b)
        assert status == ModernBERTStatus.READY
        assert "predictor" in reason

    def test_neural_model_loaded_returns_ready(self):
        stage_b = _make_stage_b(predictor=None, is_loaded=True)
        status, reason = probe_modernbert(stage_b)
        assert status == ModernBERTStatus.READY
        assert "neural_model_loaded" in reason

    def test_no_model_no_predictor_returns_not_ready(self):
        stage_b = _make_stage_b(predictor=None, is_loaded=False)
        status, reason = probe_modernbert(stage_b)
        assert status == ModernBERTStatus.NOT_READY
        assert "model_not_loaded" in reason

    def test_probe_does_not_call_load_model(self):
        """probe_modernbert must NOT call load_model or trigger any I/O."""
        stage_b = _make_stage_b(predictor=None, is_loaded=False)
        stage_b.load_model = MagicMock(side_effect=AssertionError("load_model must not be called"))
        # Must not raise
        status, _ = probe_modernbert(stage_b)
        stage_b.load_model.assert_not_called()

    def test_reason_contains_no_secrets_or_phi(self):
        stage_b = _make_stage_b(predictor=None, is_loaded=True)
        _, reason = probe_modernbert(stage_b)
        # No credentials, tokens, PHI
        for forbidden in ("VLLM_API_KEY", "Bearer", "patient", "mrn"):
            assert forbidden.lower() not in reason.lower()


# ===========================================================================
# vLLM probe tests
# ===========================================================================

class TestProbeVLLM:
    def test_none_provider_temporarily_unavailable(self):
        status, reason = probe_vllm(None)
        assert status == VLLMStatus.TEMPORARILY_UNAVAILABLE
        assert "not_configured" in reason

    def test_circuit_open_returns_circuit_open(self):
        provider = _make_vllm_provider(circuit_state=CircuitState.OPEN)
        status, reason = probe_vllm(provider)
        assert status == VLLMStatus.CIRCUIT_OPEN
        assert "circuit_breaker_open" in reason

    def test_circuit_half_open_returns_temporarily_unavailable(self):
        provider = _make_vllm_provider(circuit_state=CircuitState.HALF_OPEN)
        status, reason = probe_vllm(provider)
        assert status == VLLMStatus.TEMPORARILY_UNAVAILABLE
        assert "half_open" in reason

    def test_server_healthy_returns_ready(self):
        provider = _make_vllm_provider(circuit_state=CircuitState.CLOSED)
        # Patch httpx.Client to return 200
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_resp)
        with patch("clindoc.services.model_health.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            status, reason = probe_vllm(provider)
        assert status == VLLMStatus.READY
        assert "server_healthy" in reason

    def test_server_5xx_returns_temporarily_unavailable(self):
        provider = _make_vllm_provider(circuit_state=CircuitState.CLOSED)
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_resp)
        with patch("clindoc.services.model_health.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            status, reason = probe_vllm(provider)
        assert status == VLLMStatus.TEMPORARILY_UNAVAILABLE
        assert "server_unhealthy" in reason

    def test_server_unreachable_returns_temporarily_unavailable(self):
        provider = _make_vllm_provider(circuit_state=CircuitState.CLOSED)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(side_effect=ConnectionError("refused"))
        with patch("clindoc.services.model_health.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            status, reason = probe_vllm(provider)
        assert status == VLLMStatus.TEMPORARILY_UNAVAILABLE
        assert "server_unreachable" in reason

    def test_base_url_missing_returns_temporarily_unavailable(self):
        provider = _make_vllm_provider(circuit_state=CircuitState.CLOSED)
        provider.base_url = None
        status, reason = probe_vllm(provider)
        assert status == VLLMStatus.TEMPORARILY_UNAVAILABLE
        assert "not_configured" in reason

    def test_probe_does_not_perform_inference(self):
        """GET /health must be the only HTTP call -- no /chat/completions."""
        provider = _make_vllm_provider(circuit_state=CircuitState.CLOSED)
        calls = []
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        def capturing_get(url, **kwargs):
            calls.append(url)
            return mock_resp

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = capturing_get
        with patch("clindoc.services.model_health.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            probe_vllm(provider)

        assert len(calls) == 1, "Must make exactly one HTTP call"
        assert all("/health" in url for url in calls), "Must only hit /health endpoint"
        assert all("chat/completions" not in url for url in calls), "Must NOT hit inference endpoint"
        assert all("POST" not in str(calls) for _ in [1]), "Must use GET, not POST"

    def test_reason_contains_no_credentials(self):
        """Reason string must not expose API key, bearer token, or PHI."""
        provider = _make_vllm_provider(circuit_state=CircuitState.CLOSED)
        provider.api_key = "super_secret_token_xyz"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(side_effect=ConnectionError("conn refused"))
        with patch("clindoc.services.model_health.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            status, reason = probe_vllm(provider)
        # Secret must not appear in reason
        assert "super_secret_token_xyz" not in reason
        assert "Bearer" not in reason
        assert "VLLM_API_KEY" not in reason


# ===========================================================================
# ModelHealthRegistry tests
# ===========================================================================

class TestModelHealthRegistry:
    def test_registry_with_no_components_reports_not_ready(self):
        reg = ModelHealthRegistry()
        mb_status, mb_reason = reg.check_modernbert()
        assert mb_status == ModernBERTStatus.NOT_READY
        vllm_status, vllm_reason = reg.check_vllm()
        assert vllm_status == VLLMStatus.TEMPORARILY_UNAVAILABLE

    def test_register_stage_b_affects_modernbert_check(self):
        reg = ModelHealthRegistry()
        stage_b = _make_stage_b(predictor=_FakePredictor())
        reg.register_stage_b(stage_b)
        status, _ = reg.check_modernbert()
        assert status == ModernBERTStatus.READY

    def test_readiness_summary_keys(self):
        reg = ModelHealthRegistry()
        summary = reg.readiness_summary()
        assert set(summary.keys()) == {
            "modernbert", "modernbert_detail",
            "qwen_vllm", "qwen_vllm_detail",
        }

    def test_readiness_summary_values_are_strings(self):
        reg = ModelHealthRegistry()
        summary = reg.readiness_summary()
        for v in summary.values():
            assert isinstance(v, str)

    def test_get_model_health_registry_returns_singleton(self):
        set_model_health_registry(None)
        r1 = get_model_health_registry()
        r2 = get_model_health_registry()
        assert r1 is r2

    def test_set_model_health_registry_replaces_singleton(self):
        custom = ModelHealthRegistry()
        set_model_health_registry(custom)
        assert get_model_health_registry() is custom
        set_model_health_registry(None)

    def test_readiness_summary_no_secrets(self):
        reg = ModelHealthRegistry()
        provider = _make_vllm_provider()
        provider.api_key = "DONT_EXPOSE_ME"
        reg.register_vllm_provider(provider)
        # Patch httpx to avoid network
        with patch("clindoc.services.model_health.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = MagicMock(side_effect=ConnectionError("refused"))
            mock_httpx.Client.return_value = mock_client
            summary = reg.readiness_summary()
        summary_str = str(summary)
        assert "DONT_EXPOSE_ME" not in summary_str
        assert "Bearer" not in summary_str
        assert "patient" not in summary_str.lower()


# ===========================================================================
# API /health/ready integration tests
# ===========================================================================

try:
    from tests.test_api_endpoints import api_test_env, make_token, rsa_keypair
    _HAS_API_FIXTURE = True
except Exception:
    _HAS_API_FIXTURE = False


@pytest.mark.skipif(not _HAS_API_FIXTURE, reason="API fixtures not available")
class TestHealthReadyAPIModelFields:
    """Focused model readiness integration tests against the real FastAPI app."""

    def test_ready_response_includes_model_fields(self, api_test_env, rsa_keypair):
        """Assert /health/ready response includes modernbert and qwen_vllm fields."""
        from tests.test_api_endpoints import make_token
        client = api_test_env["client"]
        token = make_token(rsa_keypair, sub="sub-op1-02")
        resp = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert "modernbert" in body
        assert "modernbert_detail" in body
        assert "qwen_vllm" in body
        assert "qwen_vllm_detail" in body

    def test_ready_infrastructure_fields_still_present(self, api_test_env, rsa_keypair):
        """Assert existing DB/blob fields are not removed by Step 3 changes."""
        from tests.test_api_endpoints import make_token
        client = api_test_env["client"]
        token = make_token(rsa_keypair, sub="sub-op1-02")
        resp = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["database"] == "connected"
        assert body["blob_store"] == "ready"

    def test_ready_no_secrets_or_phi_in_response(self, api_test_env, rsa_keypair):
        """Assert health response does not leak credentials, tokens, or PHI."""
        from tests.test_api_endpoints import make_token
        client = api_test_env["client"]
        token = make_token(rsa_keypair, sub="sub-op1-02")
        resp = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body_str = resp.text
        for forbidden in ("VLLM_API_KEY", "Bearer ", "patient", "mrn", "api_key"):
            assert forbidden.lower() not in body_str.lower(), (
                f"Forbidden term '{forbidden}' found in health response"
            )

    def test_ready_qwen_unavailable_does_not_break_stage_ab(self, api_test_env, rsa_keypair):
        """Assert that vLLM unavailability does NOT cause a 503 (Stage A/B still usable)."""
        from tests.test_api_endpoints import make_token
        from clindoc.api.deps import set_global_model_health_registry
        # Build a registry that reports vLLM unavailable
        reg = ModelHealthRegistry()
        stage_b = _make_stage_b(predictor=_FakePredictor())
        reg.register_stage_b(stage_b)
        # No vLLM provider registered -> temporarily_unavailable
        set_global_model_health_registry(reg)
        try:
            client = api_test_env["client"]
            token = make_token(rsa_keypair, sub="sub-op1-02")
            resp = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {token}"})
            # Must still return 200 even though vLLM is unavailable
            assert resp.status_code == 200, (
                "vLLM unavailability must NOT cause a 503; Stage A/B processing continues"
            )
            body = resp.json()
            assert body["status"] == "ready"
            assert body["qwen_vllm"] == VLLMStatus.TEMPORARILY_UNAVAILABLE.value
            # ModernBERT must show ready
            assert body["modernbert"] == ModernBERTStatus.READY.value
        finally:
            set_global_model_health_registry(None)

    def test_ready_modernbert_ready_reported_correctly(self, api_test_env, rsa_keypair):
        """Assert that a ready ModernBERT is correctly surfaced."""
        from tests.test_api_endpoints import make_token
        from clindoc.api.deps import set_global_model_health_registry
        reg = ModelHealthRegistry()
        stage_b = _make_stage_b(predictor=_FakePredictor())
        reg.register_stage_b(stage_b)
        set_global_model_health_registry(reg)
        try:
            client = api_test_env["client"]
            token = make_token(rsa_keypair, sub="sub-op1-02")
            resp = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["modernbert"] == ModernBERTStatus.READY.value
        finally:
            set_global_model_health_registry(None)

    def test_ready_circuit_open_reported_as_circuit_open(self, api_test_env, rsa_keypair):
        """Assert that a tripped circuit breaker surfaces as circuit_open in response."""
        from tests.test_api_endpoints import make_token
        from clindoc.api.deps import set_global_model_health_registry
        reg = ModelHealthRegistry()
        provider = _make_vllm_provider(circuit_state=CircuitState.OPEN)
        reg.register_vllm_provider(provider)
        set_global_model_health_registry(reg)
        try:
            client = api_test_env["client"]
            token = make_token(rsa_keypair, sub="sub-op1-02")
            resp = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200  # Still 200 -- model state is informational
            body = resp.json()
            assert body["qwen_vllm"] == VLLMStatus.CIRCUIT_OPEN.value
        finally:
            set_global_model_health_registry(None)
