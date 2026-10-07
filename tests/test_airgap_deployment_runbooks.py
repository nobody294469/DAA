"""Tests for Phase 8.11 — Air-Gapped / Private Deployment & Operational Runbooks.

Validates:
  1. Deployment configuration manifests exist, are syntactically valid YAML, and enforce security boundaries.
  2. Zero hardcoded secrets exist in deployment templates or runbooks.
  3. No prohibited public ports are exposed (vLLM and DB ports are private; monitoring is loopback-only).
  4. Air-gapped / offline runtime invariants are enforced (no runtime model downloading).
  5. Operational runbooks exist, are comprehensive, and reference real endpoints and constants.
  6. Gitignore protects .env, secrets, and private keys.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import yaml
import pytest

from clindoc.config import (
    CANONICAL_AUDITED_VLLM_MODEL_NAME,
    CANONICAL_MODERNBERT_CHECKPOINT,
    ProductionPipelineConfig,
    ProductionProfileError,
    RuntimeProfile,
)
from clindoc.manifest import CANONICAL_MODERNBERT_CHECKPOINT_SHA256

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DIR = PROJECT_ROOT / "deploy"
RUNBOOKS_DIR = DEPLOY_DIR / "runbooks"


# ─── 1. FILE EXISTENCE & READABILITY ─────────────────────────────────────────

def test_deployment_and_runbook_files_exist():
    """Verify that all mandatory deployment manifests and runbooks exist and are non-empty."""
    required_files = [
        DEPLOY_DIR / "docker-compose.prod.yml",
        DEPLOY_DIR / ".env.example",
        DEPLOY_DIR / "vllm" / "docker-compose.yml",
        DEPLOY_DIR / "vllm" / "nginx.conf",
        DEPLOY_DIR / "vllm" / ".env.example",
        DEPLOY_DIR / "observability" / "docker-compose.yml",
        DEPLOY_DIR / "observability" / "prometheus.yml",
        DEPLOY_DIR / "observability" / "alerting_rules.yml",
        RUNBOOKS_DIR / "DEPLOYMENT_RUNBOOK.md",
        RUNBOOKS_DIR / "STARTUP_SHUTDOWN_RUNBOOK.md",
        RUNBOOKS_DIR / "BACKUP_RESTORE_RUNBOOK.md",
        RUNBOOKS_DIR / "DISASTER_RECOVERY_RUNBOOK.md",
        RUNBOOKS_DIR / "TROUBLESHOOTING_RUNBOOK.md",
        RUNBOOKS_DIR / "AIR_GAPPED_PROVISIONING.md",
    ]

    for file_path in required_files:
        assert file_path.exists(), f"Required deployment file missing: {file_path}"
        assert file_path.stat().st_size > 0, f"File is empty: {file_path}"


# ─── 2. ZERO HARDCODED SECRETS IN TEMPLATES & RUNBOOKS ───────────────────────

def test_env_examples_contain_zero_real_secrets():
    """Verify that .env.example files use obvious placeholders and no real secrets."""
    env_files = [
        DEPLOY_DIR / ".env.example",
        DEPLOY_DIR / "vllm" / ".env.example",
    ]

    for env_file in env_files:
        content = env_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                var_name, var_val = line.split("=", 1)
                var_val = var_val.strip("\"' ")
                # Secret-like variables must contain obvious placeholders
                if any(k in var_name.upper() for k in ("KEY", "SECRET", "PASSWORD", "TOKEN")):
                    assert any(
                        token in var_val.upper()
                        for token in ("CHANGE_ME", "REPLACE", "DEFAULT", "EMPTY", "REQUIRED")
                    ) or var_val == "", f"Potential real secret detected in {env_file.name} for {var_name}: {var_val}"


def test_no_private_keys_or_real_secrets_in_runbooks():
    """Verify that operational runbooks do not contain real private keys or leaked credentials."""
    for md_file in RUNBOOKS_DIR.glob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        # Ensure no actual unredacted RSA/EC private key blocks exist
        assert "-----BEGIN RSA PRIVATE KEY-----" not in content
        assert "-----BEGIN OPENSSH PRIVATE KEY-----" not in content
        assert "-----BEGIN EC PRIVATE KEY-----" not in content


# ─── 3. PORT & NETWORK ISOLATION IN DOCKER COMPOSE ───────────────────────────

def test_docker_compose_prod_network_and_port_isolation():
    """Verify that production compose manifests strictly isolate private services."""
    prod_compose_path = DEPLOY_DIR / "docker-compose.prod.yml"
    with open(prod_compose_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    services = config.get("services", {})
    networks = config.get("networks", {})

    # 1. vLLM container must NOT have public host ports
    vllm_service = services.get("vllm")
    assert vllm_service is not None, "vllm service missing in prod compose"
    assert "ports" not in vllm_service, "vllm service MUST NOT expose host ports"

    # 2. Postgres container must NOT have public host ports
    postgres_service = services.get("postgres")
    assert postgres_service is not None, "postgres service missing in prod compose"
    assert "ports" not in postgres_service, "postgres service MUST NOT expose host ports"

    # 3. Model serving internal network must be internal: true
    assert "model_serving_internal" in networks
    assert networks["model_serving_internal"].get("internal") is True, (
        "model_serving_internal MUST have internal: true to deny WAN routing"
    )

    # 4. Monitoring services must bind strictly to 127.0.0.1
    for mon_svc in ("prometheus", "grafana"):
        svc_config = services.get(mon_svc)
        assert svc_config is not None, f"{mon_svc} service missing"
        ports = svc_config.get("ports", [])
        for p in ports:
            assert p.startswith("127.0.0.1:"), (
                f"{mon_svc} port mapping '{p}' must be bound exclusively to loopback 127.0.0.1"
            )


def test_vllm_docker_compose_offline_and_isolated():
    """Verify that deploy/vllm/docker-compose.yml enforces egress deny and offline flags."""
    vllm_compose_path = DEPLOY_DIR / "vllm" / "docker-compose.yml"
    with open(vllm_compose_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    vllm_svc = config["services"]["vllm"]
    assert "ports" not in vllm_svc, "vllm service in deploy/vllm must have no host ports"

    # Verify offline environment flags
    env_vars = vllm_svc.get("environment", [])
    env_dict = {}
    for entry in env_vars:
        if "=" in entry:
            k, v = entry.split("=", 1)
            env_dict[k] = v

    assert env_dict.get("HF_HUB_OFFLINE") == "1", "HF_HUB_OFFLINE must be set to 1 in vllm compose"
    assert env_dict.get("TRANSFORMERS_OFFLINE") == "1", "TRANSFORMERS_OFFLINE must be set to 1 in vllm compose"

    # Verify network isolation
    assert config["networks"]["model_serving_internal"].get("internal") is True


# ─── 4. AIR-GAPPED & OFFLINE RUNTIME INVARIANTS IN CODE ───────────────────────

def test_codebase_defaults_forbid_runtime_downloads():
    """Verify that runtime code paths default to offline execution."""
    from clindoc.normalize.hybrid import SapBERTEmbedder
    from clindoc.m2.baselines import PubMedBERTNLIPredictor

    # 1. SAPBERT normalization must default to ALLOW_SAPBERT_DOWNLOAD=0
    # Inspect normalize/hybrid.py
    hybrid_file = PROJECT_ROOT / "src" / "clindoc" / "normalize" / "hybrid.py"
    hybrid_code = hybrid_file.read_text(encoding="utf-8")
    assert 'os.environ.get("ALLOW_SAPBERT_DOWNLOAD", "0")' in hybrid_code, (
        "ALLOW_SAPBERT_DOWNLOAD must default to '0'"
    )

    # 2. NLI model must default to ALLOW_NLI_DOWNLOAD=0
    baselines_file = PROJECT_ROOT / "src" / "clindoc" / "m2" / "baselines.py"
    baselines_code = baselines_file.read_text(encoding="utf-8")
    assert 'os.environ.get("ALLOW_NLI_DOWNLOAD", "0")' in baselines_code, (
        "ALLOW_NLI_DOWNLOAD must default to '0'"
    )


def test_production_profile_fails_closed_without_local_checkpoint(tmp_path):
    """Verify ProductionPipelineConfig fails closed when checkpoint is missing rather than downloading."""
    non_existent = tmp_path / "missing_model.pt"
    config = ProductionPipelineConfig(
        profile=RuntimeProfile.PRODUCTION,
        modernbert_checkpoint_path=non_existent,
    )
    with pytest.raises(ProductionProfileError, match="ModernBERT checkpoint file not found"):
        config.validate()


# ─── 5. RUNBOOK REFERENCES INTEGRITY ─────────────────────────────────────────

def test_runbooks_reference_audited_constants():
    """Verify that runbook documents reference canonical model IDs, hashes, and endpoints."""
    # 1. AIR_GAPPED_PROVISIONING.md must reference canonical SHA-256 for ModernBERT
    airgap_guide = (RUNBOOKS_DIR / "AIR_GAPPED_PROVISIONING.md").read_text(encoding="utf-8")
    assert CANONICAL_MODERNBERT_CHECKPOINT_SHA256 in airgap_guide, (
        "AIR_GAPPED_PROVISIONING.md must contain the audited ModernBERT SHA-256 hash"
    )
    assert CANONICAL_AUDITED_VLLM_MODEL_NAME in airgap_guide, (
        "AIR_GAPPED_PROVISIONING.md must reference the audited Qwen model ID"
    )

    # 2. DISASTER_RECOVERY_RUNBOOK.md must cover all 13 required scenarios
    dr_guide = (RUNBOOKS_DIR / "DISASTER_RECOVERY_RUNBOOK.md").read_text(encoding="utf-8")
    for i in range(1, 14):
        assert f"Scenario {i}:" in dr_guide, f"DISASTER_RECOVERY_RUNBOOK.md missing Scenario {i}"

    # 3. STARTUP_SHUTDOWN_RUNBOOK.md must reference the real health endpoints
    startup_guide = (RUNBOOKS_DIR / "STARTUP_SHUTDOWN_RUNBOOK.md").read_text(encoding="utf-8")
    assert "/api/v1/health" in startup_guide
    assert "/api/v1/health/ready" in startup_guide
    assert "/metrics" in startup_guide


# ─── 6. GITIGNORE PROTECTS SECRETS & KEYS ────────────────────────────────────

def test_gitignore_protects_env_and_keys():
    """Verify that .gitignore excludes production secrets, .env files, and private keys."""
    gitignore_path = PROJECT_ROOT / ".gitignore"
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")

    assert ".env" in content, ".gitignore must exclude .env files"
    assert "*.pem" in content or "certs/" in content or "*.key" in content, (
        ".gitignore must protect certificate and private key files"
    )
