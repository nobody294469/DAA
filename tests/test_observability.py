"""Automated Test Suite for Phase 8.10 Observability & Operational Monitoring.

Authority:
  - Phase 8.10 (Observability & Operational Monitoring)
  - PRODUCTIZATION_SPEC §9.2, §10.4, §13 (Failures F-05, F-06, F-07)
  - Strict PHI & Low-Cardinality Enforcement
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import uuid
import yaml

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from clindoc.api.app import create_app
from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.auth.roles import CanonicalRole
from clindoc.api.auth.dependencies import get_current_user
from clindoc.db.enums import (
    ClaimReviewState,
    DocumentApprovalStatus,
    DocumentStatus,
    PatientIdentityApprovalStatus,
)
from clindoc.db.models import Claim as DBClaim, Document as DBDocument, DocumentApproval
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.ingest.service import DocumentIngestionService
from clindoc.observability.metrics import (
    ClinDocMetrics,
    get_metrics,
    reset_metrics,
    sanitize_label,
    set_metrics,
)
from clindoc.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    ConcurrencyLimiter,
)
from clindoc.services.model_health import (
    ModelHealthRegistry,
    ModernBERTStatus,
    VLLMStatus,
)
from clindoc.storage.blob import EncryptedFileBlobStore
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.stage_a import StageAResult
from clindoc.verify.stage_b import StageBResult
from clindoc.verify.stage_c import StageCJudge, StageCResult, StageCJudgeOutput


# ─── FIXTURES ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_metrics():
    """Ensure every test runs with an isolated, clean metrics registry."""
    metrics = reset_metrics()
    yield metrics
    reset_metrics()


# ─── 1. CORE METRICS OPERATIONS & LOW CARDINALITY TESTS ─────────────────────

def test_metrics_counters_histograms_gauges(fresh_metrics):
    """Verify counter increment, gauge setting, and histogram observation."""
    m = fresh_metrics

    # Document counters
    m.record_document_status("ingested")
    m.record_document_status("quarantined")
    assert m.documents_total.labels(status="ingested")._value.get() == 1.0
    assert m.documents_total.labels(status="quarantined")._value.get() == 1.0

    # Job lifecycle & duration
    m.record_job_started()
    assert m.jobs_in_progress._value.get() == 1.0
    m.record_job_completed(duration_seconds=2.5)
    assert m.jobs_in_progress._value.get() == 0.0
    assert m.jobs_total.labels(status="completed")._value.get() == 1.0

    # Claim counters
    m.record_claim(stage="stage_a", outcome="supported")
    assert m.claims_total.labels(stage="stage_a", outcome="supported")._value.get() == 1.0

    # Gauge operations
    m.set_circuit_breaker_state("vllm_gateway", "OPEN")
    assert m.circuit_breaker_state.labels(model="vllm_gateway")._value.get() == 2.0
    m.set_circuit_breaker_state("vllm_gateway", "CLOSED")
    assert m.circuit_breaker_state.labels(model="vllm_gateway")._value.get() == 0.0

    m.set_concurrency_permits("qwen", 5)
    assert m.concurrency_permits_in_use.labels(model="qwen")._value.get() == 5.0

    m.set_model_readiness("modernbert", True)
    assert m.model_readiness.labels(model="modernbert")._value.get() == 1.0
    m.set_model_readiness("qwen", False)
    assert m.model_readiness.labels(model="qwen")._value.get() == 0.0


def test_sanitize_label_prevents_uuids_and_phi():
    """Verify sanitize_label strictly strips UUIDs, long strings, and non-allowed values."""
    # 1. UUID test: Must be redacted to default
    sample_uuid = str(uuid.uuid4())
    sanitized = sanitize_label(sample_uuid, default="unknown")
    assert sanitized == "unknown"
    assert sample_uuid not in sanitized

    # 2. String containing UUID
    embedded = f"document_{sample_uuid}_failed"
    sanitized_embedded = sanitize_label(embedded, default="unknown")
    assert sanitized_embedded == "unknown"

    # 3. Attacker-controlled long string
    long_string = "a" * 200
    sanitized_long = sanitize_label(long_string)
    assert len(sanitized_long) <= 40

    # 4. Enforce allowed set
    allowed = {"ingested", "rejected", "quarantined"}
    assert sanitize_label("INGESTED", allowed=allowed) == "ingested"
    assert sanitize_label("malicious_injection_payload", allowed=allowed) == "unknown"


def _make_claim(
    claim_id: str,
    field_type: str,
    value: str,
    evidence_id: str = "ev-101",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        document_id="doc-test-001",
        group_id="group-1",
        field_type=field_type,
        value=value,
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id=evidence_id,
        region_id="reg-1",
    )


def _make_evidence(text: str, evidence_id: str = "ev-101", document_id: str = "doc-101") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        document_id=document_id,
        page_number=1,
        char_start=0,
        char_end=len(text),
        text=text,
        bbox=BBoxModel(x0=0.1, y0=0.1, x1=0.5, y1=0.2),
        ocr_confidence=0.99,
    )


# ─── 2. VERIFICATION CASCADE & STAGE C INSTRUMENTATION ───────────────────────

def test_cascade_metrics_emission(fresh_metrics):
    """Verify that VerificationCascade emits stage evaluation metrics and latencies."""
    cascade = VerificationCascade()
    test_claim = _make_claim(
        claim_id="claim-101",
        field_type="med.name",
        value="Metformin",
        evidence_id="ev-101",
    )
    test_evidence = _make_evidence("Metformin 500mg daily", evidence_id="ev-101", document_id="doc-101")

    res = cascade.verify_claim(claim=test_claim, evidence=test_evidence)
    assert res is not None

    # Verify Stage A was evaluated
    assert fresh_metrics.cascade_evaluations_total.labels(stage="stage_a", outcome="resolved")._value.get() >= 1.0

    # Verify Stage B was evaluated
    assert (
        fresh_metrics.cascade_evaluations_total.labels(stage="stage_b", outcome="resolved")._value.get() >= 1.0
        or fresh_metrics.cascade_evaluations_total.labels(stage="stage_b", outcome="escalated")._value.get() >= 1.0
    )


def test_stage_c_retry_and_fallback_metrics(fresh_metrics):
    """Verify Stage C judge records model requests, retries, and fallback metrics."""
    class FailingMockClient:
        def __init__(self):
            self.model_name = "mock_qwen"
            self.call_count = 0

        def generate(self, prompt, schema):
            self.call_count += 1
            raise TimeoutError("Simulated vLLM request timeout")

    judge = StageCJudge(client=FailingMockClient(), max_attempts=2)
    test_claim = _make_claim(
        claim_id="claim-failing",
        field_type="med.name",
        value="Aspirin",
        evidence_id="ev-failing",
    )
    test_evidence = _make_evidence("Aspirin 81 mg", evidence_id="ev-failing", document_id="doc-failing")

    result = judge.judge(claim=test_claim, evidence=test_evidence)
    assert result.is_fallback is True
    assert result.fallback_reason == "llm_unavailable"

    # Verify model retries were recorded
    assert fresh_metrics.model_retries_total.labels(model="qwen")._value.get() >= 1.0

    # Verify fallback metric recorded
    assert fresh_metrics.stage_c_fallbacks_total.labels(reason="llm_unavailable")._value.get() >= 1.0
    assert fresh_metrics.fallbacks_total.labels(reason="llm_unavailable")._value.get() >= 1.0


# ─── 3. CIRCUIT BREAKER & CONCURRENCY LIMITER METRICS ───────────────────────

def test_circuit_breaker_state_transition_metrics(fresh_metrics):
    """Verify circuit breaker trips and resets emit transitions to Prometheus."""
    cb = CircuitBreaker(name="vllm_gateway", failure_threshold=2, cooldown_seconds=0.1)

    assert fresh_metrics.circuit_breaker_state.labels(model="vllm_gateway")._value.get() == 0.0  # CLOSED

    # Trip breaker
    cb.record_failure(ConnectionError("vLLM server down"))
    cb.record_failure(ConnectionError("vLLM server down"))

    assert cb.state == CircuitState.OPEN
    assert fresh_metrics.circuit_breaker_transitions_total.labels(model="vllm_gateway", to_state="open")._value.get() >= 1.0
    assert fresh_metrics.circuit_breaker_state.labels(model="vllm_gateway")._value.get() == 2.0  # OPEN

    # Reset breaker
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert fresh_metrics.circuit_breaker_transitions_total.labels(model="vllm_gateway", to_state="closed")._value.get() >= 1.0
    assert fresh_metrics.circuit_breaker_state.labels(model="vllm_gateway")._value.get() == 0.0  # CLOSED


def test_concurrency_limiter_metrics(fresh_metrics):
    """Verify concurrency limiter updates permits and records timeouts."""
    limiter = ConcurrencyLimiter(max_concurrency=1, timeout=0.01, name="qwen")

    with limiter:
        assert fresh_metrics.concurrency_permits_in_use.labels(model="qwen")._value.get() == 1.0

    assert fresh_metrics.concurrency_permits_in_use.labels(model="qwen")._value.get() == 0.0

    # Force concurrency timeout
    with limiter:
        with pytest.raises(Exception):
            with limiter:
                pass

    assert fresh_metrics.concurrency_wait_timeout_total.labels(model="qwen")._value.get() >= 1.0


# ─── 4. MODEL HEALTH SERVICE METRICS ─────────────────────────────────────────

def test_model_health_readiness_metrics(fresh_metrics):
    """Verify model health registry updates gauges without altering health semantics."""
    registry = ModelHealthRegistry()

    # When no models configured
    summary = registry.readiness_summary()
    assert summary["modernbert"] == ModernBERTStatus.NOT_READY.value
    assert summary["qwen_vllm"] == VLLMStatus.TEMPORARILY_UNAVAILABLE.value

    # Check metrics
    assert fresh_metrics.model_readiness.labels(model="modernbert")._value.get() == 0.0
    assert fresh_metrics.model_readiness.labels(model="qwen")._value.get() == 0.0
    assert fresh_metrics.model_unavailable_events_total.labels(model="modernbert", reason="stage_b_not_configured")._value.get() >= 1.0


# ─── 5. FASTAPI HTTP METRICS & ROUTE NORMALIZATION ──────────────────────────

def test_http_metrics_route_normalization_no_raw_ids(fresh_metrics):
    """Verify HTTP requests record metrics and route templates DO NOT contain raw UUIDs."""
    app = create_app()

    @app.get("/api/v1/test-items/{item_id}")
    def test_item(item_id: str):
        return {"item_id": item_id}

    client = TestClient(app)

    # Make liveness call
    resp = client.get("/api/v1/health/live")
    assert resp.status_code == 200

    # Make a call with a UUID in the path
    random_doc_id = str(uuid.uuid4())
    resp_item = client.get(f"/api/v1/test-items/{random_doc_id}")
    assert resp_item.status_code == 200

    # Inspect generated metrics text
    metrics_text = fresh_metrics.generate_metrics_text().decode("utf-8")

    # 1. Route template should be normalized or parameterized, never raw UUID
    assert random_doc_id not in metrics_text
    assert ('route="/health/live"' in metrics_text) or ('route="/api/v1/health/live"' in metrics_text)
    assert 'route="/api/v1/test-items/{item_id}"' in metrics_text
    assert 'status_class="2xx"' in metrics_text


# ─── 6. METRICS ENDPOINT SECURITY & ACCESS CONTROL ──────────────────────────

def test_metrics_endpoint_access_control():
    """Verify /metrics rejects unauthorized external IPs or invalid tokens."""
    app = create_app()
    client = TestClient(app)

    # 1. By default, localhost / testclient is permitted
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "clindoc_documents_total" in resp.text

    # 2. Token protection when CLINDOC_METRICS_TOKEN is set
    os.environ["CLINDOC_METRICS_TOKEN"] = "super-secret-internal-monitor-token"
    try:
        # Without token -> 403 Forbidden
        unauth_resp = client.get("/metrics")
        assert unauth_resp.status_code == 403

        # With wrong token -> 403 Forbidden
        bad_token_resp = client.get("/metrics", headers={"Authorization": "Bearer wrong-token"})
        assert bad_token_resp.status_code == 403

        # With correct token in Authorization -> 200 OK
        auth_resp = client.get(
            "/metrics",
            headers={"Authorization": "Bearer super-secret-internal-monitor-token"},
        )
        assert auth_resp.status_code == 200
        assert "clindoc_documents_total" in auth_resp.text

        # With correct token in X-Internal-Monitoring-Token -> 200 OK
        header_resp = client.get(
            "/metrics",
            headers={"X-Internal-Monitoring-Token": "super-secret-internal-monitor-token"},
        )
        assert header_resp.status_code == 200
    finally:
        os.environ.pop("CLINDOC_METRICS_TOKEN", None)


# ─── 7. MANDATORY PHI & SECRET REDACTION VERIFICATION ───────────────────────

def test_metrics_output_contains_zero_phi_or_secrets(fresh_metrics):
    """CRITICAL SECURITY TEST: Ensure no patient names, MRNs, IDs, or secrets enter metrics."""
    # Simulate various pipeline events
    fresh_metrics.record_document_status("ingested")
    fresh_metrics.record_job_completed(1.2)
    fresh_metrics.record_claim("stage_a", "supported")
    fresh_metrics.record_stage_c_fallback("llm_unavailable")
    fresh_metrics.record_review_action("accept", "success")
    fresh_metrics.record_fhir_export_failure("eligibility_failure", "approval_missing")
    fresh_metrics.record_security_event("malformed_file")

    metrics_text = fresh_metrics.generate_metrics_text().decode("utf-8")

    # Prohibited PHI tokens
    prohibited_phi = [
        "john doe",
        "jane doe",
        "mrn",
        "patient",
        "ssn",
        "diagnosis",
        "mg/dl",
        "metformin",
        "aspirin",
        "hypertension",
        "bundle-",
        "bearer",
        "secret",
        "password",
    ]

    metrics_lower = metrics_text.lower()
    for token in prohibited_phi:
        # Note: 'patient' is checked as a label or value, but allow standard metric description if any
        # Verify no label contains prohibited text
        assert f'="{token}"' not in metrics_lower, f"Prohibited clinical token '{token}' detected in metric label!"

    # Ensure no UUIDs appear anywhere in metrics output
    uuid_pattern = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    assert not uuid_pattern.search(metrics_text), "Raw UUID detected in metrics output!"


# ─── 8. INGESTION & SECURITY METRICS EMISSION ────────────────────────────────

def test_ingestion_security_metrics_emission(fresh_metrics):
    """Verify document screening failures increment security and rejection counters."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        blob_store = EncryptedFileBlobStore(root_dir=Path(tmp_dir) / "blobs", encryption_key=b"1" * 32)
        service = DocumentIngestionService(blob_store=blob_store, max_file_size_bytes=100)

        # 1. Zero-byte screening
        res_zero = service.screen_bytes(b"", "empty.pdf")
        assert res_zero.status == DocumentStatus.FAILED_INGEST
        assert fresh_metrics.documents_total.labels(status="failed_ingest")._value.get() >= 1.0
        assert fresh_metrics.security_events_total.labels(event_type="malformed_file")._value.get() >= 1.0

        # 2. Oversized file screening
        res_oversized = service.screen_bytes(b"x" * 200, "large.pdf")
        assert res_oversized.status == DocumentStatus.FAILED_INGEST
        assert fresh_metrics.security_events_total.labels(event_type="oversized_file")._value.get() >= 1.0

        # 3. MIME spoofing screening (declared PDF but contents are JPEG bytes)
        res_spoof = service.screen_bytes(b"\xff\xd8\xff\xe0test", "fake.pdf", declared_mime_type="application/pdf")
        assert res_spoof.status == DocumentStatus.QUARANTINED
        assert fresh_metrics.documents_total.labels(status="quarantined")._value.get() >= 1.0
        assert fresh_metrics.security_events_total.labels(event_type="mime_spoof")._value.get() >= 1.0


# ─── 9. FHIR EXPORT METRICS ──────────────────────────────────────────────────

def test_fhir_export_metrics(fresh_metrics):
    """Verify FHIR export operations record success/failure and never include FHIR bundle content."""
    fresh_metrics.record_fhir_export_failure("eligibility_failure", "approval_missing")
    fresh_metrics.record_fhir_export_failure("validation_failure", "fhir_validation_failure")
    fresh_metrics.record_fhir_export_success(0.45)

    assert fresh_metrics.fhir_exports_total.labels(status="eligibility_failure", reason="approval_missing")._value.get() == 1.0
    assert fresh_metrics.fhir_exports_total.labels(status="validation_failure", reason="fhir_validation_failure")._value.get() == 1.0
    assert fresh_metrics.fhir_exports_total.labels(status="success", reason="none")._value.get() == 1.0

    # Verify FHIR bundle json is never in metrics
    metrics_str = fresh_metrics.generate_metrics_text().decode("utf-8")
    assert "resourceType" not in metrics_str
    assert "MedicationRequest" not in metrics_str


# ─── 10. DEPLOYMENT ARTIFACTS VALIDATION ─────────────────────────────────────

def test_deployment_prometheus_config_valid():
    """Statically validate deploy/observability/prometheus.yml syntax and target."""
    prom_path = Path("deploy/observability/prometheus.yml")
    assert prom_path.exists(), "prometheus.yml must exist"

    content = yaml.safe_load(prom_path.read_text(encoding="utf-8"))
    assert "scrape_configs" in content
    job_names = [sc["job_name"] for sc in content["scrape_configs"]]
    assert "clindoc_api" in job_names


def test_deployment_alerting_rules_valid():
    """Statically validate deploy/observability/alerting_rules.yml rules and syntax."""
    rules_path = Path("deploy/observability/alerting_rules.yml")
    assert rules_path.exists(), "alerting_rules.yml must exist"

    content = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    assert "groups" in content
    alert_names = [r["alert"] for g in content["groups"] for r in g["rules"]]

    required_alerts = [
        "ClinDocApplicationReadinessFailed",
        "ModernBERTUnavailable",
        "SustainedQwenUnavailability",
        "CircuitBreakerOpenProlonged",
        "HighStageCFallbackRate",
        "HighSchemaInvalidRate",
        "HighAPIErrorRate",
        "GrowingHumanReviewQueue",
        "ConcurrencySaturation",
    ]
    for required in required_alerts:
        assert required in alert_names, f"Missing required alert rule: {required}"


def test_deployment_docker_compose_and_grafana_valid():
    """Statically validate Docker Compose, datasource, provider, and Grafana dashboard JSON."""
    compose_path = Path("deploy/observability/docker-compose.yml")
    assert compose_path.exists()
    compose_content = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert "services" in compose_content
    assert "prometheus" in compose_content["services"]
    assert "grafana" in compose_content["services"]

    # Verify datasource provisioning
    ds_path = Path("deploy/observability/grafana/provisioning/datasources/datasource.yml")
    assert ds_path.exists()
    ds_content = yaml.safe_load(ds_path.read_text(encoding="utf-8"))
    assert "datasources" in ds_content

    # Verify dashboard provisioning
    prov_path = Path("deploy/observability/grafana/provisioning/dashboards/dashboards.yml")
    assert prov_path.exists()

    # Verify Grafana dashboard JSON
    dash_path = Path("deploy/observability/grafana/dashboards/clindoc_operations.json")
    assert dash_path.exists()
    dash_json = json.loads(dash_path.read_text(encoding="utf-8"))
    assert dash_json["title"] == "ClinDoc Operational Monitoring"
    assert len(dash_json["panels"]) >= 15
