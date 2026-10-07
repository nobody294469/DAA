"""Model Health / Readiness Probes (Phase 8.7 Step 3).

Authority:
  - PRODUCTIZATION_SPEC.md SS3.3, SS3.5, SS10.4, SS13 (Failures F-05, F-06)
  - PRODUCTIZATION_DECISIONS.md Phase 8.7 Step 3

Provides lightweight, deterministic, fail-closed readiness checks for:
  A. ModernBERT (Stage B) - READY / NOT_READY
  B. Qwen/vLLM (Stage C) - READY / TEMPORARILY_UNAVAILABLE / CIRCUIT_OPEN

Design constraints:
  - No full clinical inference request is made.
  - ModernBERT readiness reads in-memory model state only; no reload on every probe.
  - vLLM readiness uses circuit-breaker state then a lightweight HTTP GET /health call.
  - No PHI, secrets, bearer tokens, or provider internals are surfaced in results.
  - Fail-closed for production: absence of registered verifiers implies NOT_READY.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

from clindoc.observability.metrics import get_metrics

logger = logging.getLogger(__name__)


# --- Status Enums ---

class ModernBERTStatus(str, Enum):
    """Readiness status of the ModernBERT Stage B component."""
    READY = "ready"
    NOT_READY = "not_ready"


class VLLMStatus(str, Enum):
    """Readiness status of the Qwen/vLLM Stage C component."""
    READY = "ready"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    CIRCUIT_OPEN = "circuit_open"


# --- ModernBERT Probe ---

def probe_modernbert(stage_b: Any) -> tuple:
    """Check ModernBERT readiness from a LearnedVerifier instance.

    Reads is_model_loaded / predictor state only - never loads/reloads the model.

    Returns:
        (ModernBERTStatus, reason_str) where reason is safe for public response.
    """
    if stage_b is None:
        return ModernBERTStatus.NOT_READY, "stage_b_not_configured"

    # Check for MockModelPredictor: production-forbidden, always not ready
    predictor = getattr(stage_b, "_predictor", None)
    if predictor is not None:
        cls_name = type(predictor).__name__
        if cls_name == "MockModelPredictor":
            return ModernBERTStatus.NOT_READY, "mock_predictor_not_permitted_in_production"
        # Any non-mock custom predictor is considered ready
        return ModernBERTStatus.READY, "custom_predictor_active"

    # Check neural model loaded via is_model_loaded property
    is_loaded = getattr(stage_b, "is_model_loaded", None)
    if callable(is_loaded):
        is_loaded = is_loaded()
    if is_loaded:
        return ModernBERTStatus.READY, "neural_model_loaded"

    # Model not loaded and no predictor
    return ModernBERTStatus.NOT_READY, "model_not_loaded"


# --- vLLM Probe ---

def probe_vllm(provider: Any, probe_timeout: float = 3.0) -> tuple:
    """Check Qwen/vLLM readiness from a VLLMProvider instance.

    Strategy (in order):
      1. If circuit breaker is OPEN -> CIRCUIT_OPEN (fast-path, no HTTP).
      2. If circuit breaker is HALF_OPEN -> TEMPORARILY_UNAVAILABLE.
      3. GET {base_url}/health - lightweight vLLM server-status endpoint (no inference).
      4. On timeout/connection error -> TEMPORARILY_UNAVAILABLE.

    Returns:
        (VLLMStatus, reason_str) - reason is safe for health response (no secrets/tokens).
    """
    if provider is None:
        return VLLMStatus.TEMPORARILY_UNAVAILABLE, "provider_not_configured"

    # 1. Check circuit breaker state - no HTTP needed
    cb = getattr(provider, "circuit_breaker", None)
    if cb is not None:
        try:
            from clindoc.services.circuit_breaker import CircuitState
            cb_state = cb.state
            if cb_state == CircuitState.OPEN:
                return VLLMStatus.CIRCUIT_OPEN, "circuit_breaker_open"
            if cb_state == CircuitState.HALF_OPEN:
                return VLLMStatus.TEMPORARILY_UNAVAILABLE, "circuit_breaker_half_open"
        except Exception as cb_exc:
            logger.debug("vLLM circuit-breaker state check failed: %s", cb_exc)

    # 2. Lightweight HTTP health probe (GET /health - no inference payload, no auth token)
    base_url = getattr(provider, "base_url", None)
    if not base_url:
        return VLLMStatus.TEMPORARILY_UNAVAILABLE, "base_url_not_configured"

    health_url = "{}/health".format(base_url.rstrip("/"))
    try:
        if httpx is None:
            return VLLMStatus.TEMPORARILY_UNAVAILABLE, "httpx_not_available"
        with httpx.Client(timeout=probe_timeout) as client:
            resp = client.get(health_url)
        if resp.status_code == 200:
            return VLLMStatus.READY, "server_healthy"
        return VLLMStatus.TEMPORARILY_UNAVAILABLE, "server_unhealthy"
    except Exception:
        # Do NOT log exception details - they may contain internal addresses.
        return VLLMStatus.TEMPORARILY_UNAVAILABLE, "server_unreachable"


# --- Registry ---

class ModelHealthRegistry:
    """Holds references to live model components for health probing.

    Registered once at application startup. The health endpoint reads from
    this registry without triggering model loads or inference requests.

    Security: only stores references, never credentials, tokens, or PHI.
    """

    def __init__(self) -> None:
        self._stage_b: Any = None        # LearnedVerifier (ModernBERT)
        self._vllm_provider: Any = None  # VLLMProvider (Qwen)

    def register_stage_b(self, stage_b: Any) -> None:
        """Register the active LearnedVerifier (Stage B / ModernBERT)."""
        self._stage_b = stage_b

    def register_vllm_provider(self, provider: Any) -> None:
        """Register the active VLLMProvider (Qwen/vLLM Stage C)."""
        self._vllm_provider = provider

    def check_modernbert(self) -> tuple:
        """Return ModernBERT readiness without loading or reloading the model."""
        return probe_modernbert(self._stage_b)

    def check_vllm(self, probe_timeout: float = 3.0) -> tuple:
        """Return Qwen/vLLM readiness via circuit-breaker state + lightweight HTTP probe."""
        return probe_vllm(self._vllm_provider, probe_timeout=probe_timeout)

    def readiness_summary(self, vllm_probe_timeout: float = 3.0) -> dict:
        """Compute full model readiness summary.

        Returns a dict safe to embed in a public health response - no PHI,
        no credentials, no internal addresses, no bearer tokens.
        """
        mb_status, mb_reason = self.check_modernbert()
        vllm_status, vllm_reason = self.check_vllm(probe_timeout=vllm_probe_timeout)

        mb_is_ready = (mb_status == ModernBERTStatus.READY)
        vllm_is_ready = (vllm_status == VLLMStatus.READY)
        metrics = get_metrics()
        metrics.set_model_readiness("modernbert", mb_is_ready)
        metrics.set_model_readiness("qwen", vllm_is_ready)
        if not mb_is_ready:
            metrics.record_model_unavailable("modernbert", mb_reason)
        if not vllm_is_ready:
            metrics.record_model_unavailable("qwen", vllm_reason)

        return {
            "modernbert": mb_status.value,
            "modernbert_detail": mb_reason,
            "qwen_vllm": vllm_status.value,
            "qwen_vllm_detail": vllm_reason,
        }


# --- Application-level singleton ---

_GLOBAL_MODEL_HEALTH_REGISTRY: Optional[ModelHealthRegistry] = None


def get_model_health_registry() -> ModelHealthRegistry:
    """Get or create the application-wide ModelHealthRegistry singleton."""
    global _GLOBAL_MODEL_HEALTH_REGISTRY
    if _GLOBAL_MODEL_HEALTH_REGISTRY is None:
        _GLOBAL_MODEL_HEALTH_REGISTRY = ModelHealthRegistry()
    return _GLOBAL_MODEL_HEALTH_REGISTRY


def set_model_health_registry(registry: Optional[ModelHealthRegistry]) -> None:
    """Replace the global ModelHealthRegistry (used in tests and lifecycle hooks)."""
    global _GLOBAL_MODEL_HEALTH_REGISTRY
    _GLOBAL_MODEL_HEALTH_REGISTRY = registry
