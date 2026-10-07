"""Tests for Phase 8.7 Step 4: Secure vLLM Deployment Infrastructure.

Authority:
  - PRODUCTIZATION_SPEC.md §10, §13 (Failures F-05, F-06)
  - PRODUCTIZATION_DECISIONS.md Phase 8.7 Step 4
  - DEC-006, DEC-013

Validates static and configuration properties of deployment artifacts:
  1. Production deployment specifies --disable-log-requests.
  2. No ngrok or public tunnel utilities in production deployment artifacts.
  3. No hard-coded secrets in manifests, configurations, or templates.
  4. Authentication configuration is strictly required for inference.
  5. TLS configuration does not disable certificate verification.
  6. Model name and quantization match authoritative configuration.
  7. Health endpoint (/health) is distinct from inference (/v1/chat/completions).
  8. Network egress is denied by default on the model-serving network (internal: true).
  9. Persistent model cache volume is configured.
  10. Production mock containment remains intact.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from clindoc.config import (
    CANONICAL_AUDITED_VLLM_MODEL_NAME,
    ProductionPipelineConfig,
    ProductionProfileError,
    RuntimeProfile,
)
from clindoc.manifest import (
    CANONICAL_QWEN_MODEL_ID,
    CANONICAL_QWEN_QUANTIZATION,
)


DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy" / "vllm"
COMPOSE_PATH = DEPLOY_DIR / "docker-compose.yml"
NGINX_PATH = DEPLOY_DIR / "nginx.conf"
ENV_EXAMPLE_PATH = DEPLOY_DIR / ".env.example"
DEPLOY_DOC_PATH = DEPLOY_DIR / "DEPLOYMENT.md"


@pytest.fixture(scope="module")
def compose_data() -> dict:
    """Load and parse the production docker-compose.yml."""
    assert COMPOSE_PATH.exists(), f"Compose file missing: {COMPOSE_PATH}"
    with open(COMPOSE_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def compose_text() -> str:
    """Read raw text of docker-compose.yml."""
    assert COMPOSE_PATH.exists(), f"Compose file missing: {COMPOSE_PATH}"
    return COMPOSE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nginx_text() -> str:
    """Read raw text of nginx.conf."""
    assert NGINX_PATH.exists(), f"Nginx conf missing: {NGINX_PATH}"
    return NGINX_PATH.read_text(encoding="utf-8")


# ─── 1. REQUEST LOGGING PRIVACY ───────────────────────────────────────────────

def test_production_deployment_disables_request_logging(compose_text: str):
    """vLLM deployment MUST specify --disable-log-requests to avoid logging clinical PHI."""
    assert "--disable-log-requests" in compose_text, (
        "Production docker-compose.yml must contain '--disable-log-requests' to prevent PHI logging."
    )


# ─── 2. NO PUBLIC TUNNELS IN PRODUCTION ───────────────────────────────────────

def test_no_public_tunnels_in_production_artifacts():
    """Production deployment artifacts must NOT include ngrok or other public tunnels."""
    forbidden_terms = ["ngrok", "localtunnel", "pagekite", "cloudflared tunnel"]
    artifacts = [COMPOSE_PATH, NGINX_PATH, ENV_EXAMPLE_PATH]

    for artifact in artifacts:
        assert artifact.exists(), f"Expected artifact {artifact} to exist"
        content = artifact.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term not in content, (
                f"Found forbidden tunnel reference '{term}' in production artifact {artifact.name}"
            )


# ─── 3. NO HARD-CODED SECRETS ────────────────────────────────────────────────

def test_no_hardcoded_secrets_in_deployment_artifacts(compose_text: str, nginx_text: str):
    """Ensure API keys and bearer tokens are injected via environment/vault, not hard-coded."""
    # Compose should use ${VLLM_API_KEY} variable substitution, never a raw secret
    assert "${VLLM_API_KEY" in compose_text, (
        "docker-compose.yml must inject VLLM_API_KEY via environment variable substitution."
    )

    # Check for accidental hard-coded hex or high-entropy tokens
    secret_patterns = [
        r"api[-_]?key\s*[:=]\s*['\"][a-zA-Z0-9_\-]{24,}['\"]",
        r"bearer\s+[a-zA-Z0-9_\-]{24,}",
    ]
    for pattern in secret_patterns:
        assert not re.search(pattern, compose_text, re.IGNORECASE), (
            f"Possible hard-coded secret found in {COMPOSE_PATH}"
        )
        assert not re.search(pattern, nginx_text, re.IGNORECASE), (
            f"Possible hard-coded secret found in {NGINX_PATH}"
        )


def test_env_example_contains_only_placeholders():
    """.env.example should contain explicit placeholder instructions, not live keys."""
    assert ENV_EXAMPLE_PATH.exists()
    content = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "replace-with" in content.lower() or "placeholder" in content.lower() or "change-me" in content.lower()
    # Ensure it warns not to commit
    assert "NEVER commit" in content or "DO NOT COMMIT" in content


# ─── 4. AUTHENTICATION REQUIRED FOR INFERENCE ─────────────────────────────────

def test_authentication_required_in_vllm_command(compose_text: str):
    """vLLM container command must enforce --api-key parameter."""
    assert "--api-key" in compose_text, (
        "vLLM serve command must include '--api-key' flag to enforce authenticated inference."
    )


# ─── 5. TLS CONFIGURATION & CERTIFICATE VERIFICATION ──────────────────────────

def test_tls_configuration_supported_in_nginx(nginx_text: str):
    """Nginx TLS proxy must configure modern SSL with TLS 1.2/1.3 and valid certificate paths."""
    assert "listen 8443 ssl" in nginx_text
    assert "ssl_protocols TLSv1.2 TLSv1.3" in nginx_text
    assert "ssl_certificate" in nginx_text
    assert "ssl_certificate_key" in nginx_text


def test_client_code_does_not_disable_tls_verification():
    """Verify that Python client code does not set verify=False on httpx clients."""
    src_dir = Path(__file__).resolve().parents[1] / "src" / "clindoc"
    py_files = list(src_dir.rglob("*.py"))
    assert len(py_files) > 0

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        assert "verify=False" not in content, (
            f"Found insecure 'verify=False' in {py_file}"
        )


# ─── 6. MODEL NAME & QUANTIZATION MATCH SPECIFICATION ─────────────────────────

def test_model_name_and_quantization_match_canonical(compose_text: str):
    """vLLM command must target CANONICAL_AUDITED_VLLM_MODEL_NAME with AWQ quantization."""
    assert CANONICAL_AUDITED_VLLM_MODEL_NAME in compose_text, (
        f"vLLM command must serve canonical model '{CANONICAL_AUDITED_VLLM_MODEL_NAME}'."
    )
    assert CANONICAL_QWEN_MODEL_ID in compose_text
    assert "--quantization awq" in compose_text, (
        f"vLLM command must specify '--quantization awq' matching {CANONICAL_QWEN_QUANTIZATION}."
    )


# ─── 7. HEALTH ENDPOINT DISTINCT FROM INFERENCE ───────────────────────────────

def test_health_check_distinct_from_inference(compose_data: dict, nginx_text: str):
    """Health check must probe /health, not /v1/chat/completions."""
    vllm_service = compose_data["services"]["vllm"]
    healthcheck = vllm_service.get("healthcheck", {})
    test_cmd = str(healthcheck.get("test", ""))

    assert "/health" in test_cmd, "vLLM healthcheck must probe /health."
    assert "chat/completions" not in test_cmd, (
        "Healthcheck must not invoke /v1/chat/completions inference."
    )

    # Nginx must have distinct location blocks
    assert "location /health" in nginx_text
    assert "location /v1/" in nginx_text


# ─── 8. NETWORK EGRESS DENIED ON SERVING NETWORK ──────────────────────────────

def test_network_isolation_and_egress_deny(compose_data: dict):
    """The internal model serving network must have internal: true (no outbound routing)."""
    networks = compose_data.get("networks", {})
    assert "model_serving_internal" in networks, "model_serving_internal network must exist"

    serving_net = networks["model_serving_internal"]
    assert serving_net.get("internal") is True, (
        "model_serving_internal must set 'internal: true' for default egress denial."
    )

    # vLLM service must NOT publish external host ports
    vllm_service = compose_data["services"]["vllm"]
    assert "ports" not in vllm_service, (
        "vLLM service must not publish host ports directly; traffic routes via tls-proxy."
    )
    assert "model_serving_internal" in vllm_service.get("networks", [])


# ─── 9. MODEL CACHE VOLUME CONFIGURED ─────────────────────────────────────────

def test_persistent_model_cache_volume_configured(compose_data: dict):
    """Compose manifest must configure persistent cache volume for model weights."""
    volumes = compose_data.get("volumes", {})
    assert "vllm_cache" in volumes, "vllm_cache volume must be declared"

    vllm_service = compose_data["services"]["vllm"]
    service_vols = vllm_service.get("volumes", [])
    cache_mount_found = any("/root/.cache/huggingface" in str(v) for v in service_vols)
    assert cache_mount_found, (
        "vLLM service must mount persistent volume to /root/.cache/huggingface."
    )


# ─── 10. PRODUCTION MOCK CONTAINMENT INTACT ───────────────────────────────────

def test_production_profile_rejects_mocks():
    """ProductionPipelineConfig must fail-closed if allow_mocks=True."""
    with pytest.raises(ProductionProfileError, match="allow_mocks=True is forbidden"):
        cfg = ProductionPipelineConfig(
            profile=RuntimeProfile.PRODUCTION,
            allow_mocks=True,
        )
        cfg.validate()


def test_deployment_documentation_exists():
    """Verify that comprehensive deployment documentation is present."""
    assert DEPLOY_DOC_PATH.exists()
    doc_text = DEPLOY_DOC_PATH.read_text(encoding="utf-8")
    assert "Development vs. Production Topology" in doc_text
    assert "Qwen/Qwen2.5-7B-Instruct-AWQ" in doc_text
    assert "Egress Denial" in doc_text
    assert "--disable-log-requests" in doc_text
    assert "Health & Readiness Integration" in doc_text
