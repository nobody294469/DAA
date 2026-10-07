"""Prometheus-compatible operational metrics for ClinDoc.

Authority:
  - Phase 8.10 (Observability & Operational Monitoring)
  - PRODUCTIZATION_SPEC §9.2, §10.4, §13 (F-05, F-06, F-07)
  - Strict PHI-Free Mandate: Labels must remain low-cardinality and NEVER contain
    patient IDs, names, MRNs, claim IDs, document IDs, clinical text, prompts, or secrets.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Set
import threading

import prometheus_client
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    REGISTRY,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

logger = logging.getLogger(__name__)

# UUID pattern guard to strictly forbid raw identifiers in labels
_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Standard duration buckets (in seconds)
_HTTP_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_STAGE_DURATION_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_MODEL_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
_JOB_DURATION_BUCKETS = (0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0)
_EXPORT_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def sanitize_label(value: Any, allowed: Optional[Set[str]] = None, default: str = "unknown") -> str:
    """Sanitize label value to ensure low cardinality and strictly prevent PHI/UUID leakage."""
    if value is None:
        return default
    val_str = str(value).strip().lower()
    if not val_str:
        return default
    # If a UUID is detected, immediately reject and return default
    if _UUID_PATTERN.search(val_str):
        logger.warning("Attempted to use UUID in metric label: '%s'. Redacted to '%s'.", val_str, default)
        return default
    # Truncate any unbounded string length
    if len(val_str) > 40:
        val_str = val_str[:40]
    # Replace non-alphanumeric/underscore/hyphen characters
    val_str = re.sub(r"[^a-z0-9_\-/{}]", "_", val_str)
    if allowed is not None:
        if val_str not in allowed:
            return default
    return val_str


class ClinDocMetrics:
    """Central metrics collector for the ClinDoc platform."""

    def __init__(self, registry: Optional[CollectorRegistry] = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        self._lock = threading.Lock()

        # ─── 1. Documents ────────────────────────────────────────────────────
        self.documents_total = Counter(
            "clindoc_documents_total",
            "Total count of documents processed by terminal ingestion/review status.",
            ["status"],
            registry=self.registry,
        )

        # ─── 2. Processing Jobs ──────────────────────────────────────────────
        self.jobs_total = Counter(
            "clindoc_jobs_total",
            "Total count of background document processing jobs by terminal outcome.",
            ["status"],
            registry=self.registry,
        )
        self.jobs_in_progress = Gauge(
            "clindoc_jobs_in_progress",
            "Current number of background processing jobs currently executing.",
            registry=self.registry,
        )
        self.job_duration_seconds = Histogram(
            "clindoc_job_duration_seconds",
            "End-to-end duration of processing jobs in seconds.",
            buckets=_JOB_DURATION_BUCKETS,
            registry=self.registry,
        )

        # ─── 3. Verification Cascade & Claims ────────────────────────────────
        self.claims_total = Counter(
            "clindoc_claims_total",
            "Total count of clinical claims evaluated by verification stage and label outcome.",
            ["stage", "outcome"],
            registry=self.registry,
        )
        self.claims_requiring_review_total = Counter(
            "clindoc_claims_requiring_review_total",
            "Total count of claims routed to human review workbench.",
            registry=self.registry,
        )
        self.cascade_evaluations_total = Counter(
            "clindoc_cascade_evaluations_total",
            "Evaluations performed at each cascade stage with stage-specific outcome.",
            ["stage", "outcome"],
            registry=self.registry,
        )
        self.stage_duration_seconds = Histogram(
            "clindoc_stage_duration_seconds",
            "Execution latency of verification cascade stages in seconds.",
            ["stage"],
            buckets=_STAGE_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.stage_c_fallbacks_total = Counter(
            "clindoc_stage_c_fallbacks_total",
            "Count of Stage C LLM fallbacks categorized by safe failure reason code.",
            ["reason"],
            registry=self.registry,
        )

        # ─── 4. Model Serving & Reliability ──────────────────────────────────
        self.model_requests_total = Counter(
            "clindoc_model_requests_total",
            "Inference and verification calls dispatched to ML/LLM models.",
            ["model", "outcome"],
            registry=self.registry,
        )
        self.model_retries_total = Counter(
            "clindoc_model_retries_total",
            "Total retry attempts made during model invocations.",
            ["model"],
            registry=self.registry,
        )
        self.model_request_duration_seconds = Histogram(
            "clindoc_model_request_duration_seconds",
            "Latency of model inference requests in seconds.",
            ["model"],
            buckets=_MODEL_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.circuit_breaker_state = Gauge(
            "clindoc_circuit_breaker_state",
            "State of circuit breaker (0=CLOSED, 1=HALF_OPEN, 2=OPEN).",
            ["model"],
            registry=self.registry,
        )
        self.circuit_breaker_transitions_total = Counter(
            "clindoc_circuit_breaker_transitions_total",
            "Count of circuit breaker state transitions.",
            ["model", "to_state"],
            registry=self.registry,
        )
        self.concurrency_permits_in_use = Gauge(
            "clindoc_concurrency_permits_in_use",
            "Current active concurrency permits acquired for model calls.",
            ["model"],
            registry=self.registry,
        )
        self.concurrency_waiting_requests = Gauge(
            "clindoc_concurrency_waiting_requests",
            "Current requests waiting to acquire concurrency permit.",
            ["model"],
            registry=self.registry,
        )
        self.concurrency_wait_timeout_total = Counter(
            "clindoc_concurrency_wait_timeout_total",
            "Count of model requests that timed out waiting for a concurrency permit.",
            ["model"],
            registry=self.registry,
        )
        self.model_readiness = Gauge(
            "clindoc_model_readiness",
            "Reported model readiness probe state (1=READY, 0=NOT_READY/UNAVAILABLE).",
            ["model"],
            registry=self.registry,
        )
        self.model_unavailable_events_total = Counter(
            "clindoc_model_unavailable_events_total",
            "Model unavailable or readiness check failure events.",
            ["model", "reason"],
            registry=self.registry,
        )

        # ─── 5. Fallback Metrics ─────────────────────────────────────────────
        self.fallbacks_total = Counter(
            "clindoc_fallbacks_total",
            "Total pipeline fallback events by root-cause code.",
            ["reason"],
            registry=self.registry,
        )

        # ─── 6. Human Review Workbench ───────────────────────────────────────
        self.review_claims_total = Counter(
            "clindoc_review_claims_total",
            "Total human review actions executed on claims.",
            ["action", "outcome"],
            registry=self.registry,
        )
        self.review_queue_size = Gauge(
            "clindoc_review_queue_size",
            "Current number of claims in pending human review queue.",
            registry=self.registry,
        )
        self.review_duration_seconds = Histogram(
            "clindoc_review_duration_seconds",
            "Time spent by reviewers processing individual claims or documents.",
            buckets=(5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0),
            registry=self.registry,
        )

        # ─── 7. FHIR R4B Export ──────────────────────────────────────────────
        self.fhir_exports_total = Counter(
            "clindoc_fhir_exports_total",
            "Total FHIR R4B export attempts by outcome status and eligibility reason.",
            ["status", "reason"],
            registry=self.registry,
        )
        self.fhir_export_duration_seconds = Histogram(
            "clindoc_fhir_export_duration_seconds",
            "Duration of FHIR bundle generation, validation, and persistence.",
            buckets=_EXPORT_DURATION_BUCKETS,
            registry=self.registry,
        )

        # ─── 8. Security & Pre-Ingestion Screening ───────────────────────────
        self.security_events_total = Counter(
            "clindoc_security_events_total",
            "Security events, input rejections, and policy boundary violations.",
            ["event_type"],
            registry=self.registry,
        )

        # ─── 9. HTTP / API Performance ───────────────────────────────────────
        self.http_requests_total = Counter(
            "clindoc_http_requests_total",
            "Total HTTP requests handled by the API by method, normalized route, and status class.",
            ["method", "route", "status_class"],
            registry=self.registry,
        )
        self.http_request_duration_seconds = Histogram(
            "clindoc_http_request_duration_seconds",
            "HTTP request latency by method and normalized route.",
            ["method", "route"],
            buckets=_HTTP_DURATION_BUCKETS,
            registry=self.registry,
        )

        # ─── 10. Controlled EHR Integration & Pilot (Phase 8.12) ─────────────
        self.ehr_transmissions_total = Counter(
            "clindoc_ehr_transmissions_total",
            "Total EHR transmission attempts by outcome status and destination environment.",
            ["status", "destination"],
            registry=self.registry,
        )
        self.ehr_transmission_duration_seconds = Histogram(
            "clindoc_ehr_transmission_duration_seconds",
            "Duration of EHR network transmission and response parsing in seconds.",
            ["destination"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
            registry=self.registry,
        )

    # ─── Document Methods ────────────────────────────────────────────────────
    def record_document_status(self, status: str) -> None:
        safe_status = sanitize_label(
            status,
            allowed={"ingested", "processing", "pending_review", "quarantined", "failed_ingest", "failed", "completed", "exported"},
        )
        self.documents_total.labels(status=safe_status).inc()

    # ─── Job Methods ─────────────────────────────────────────────────────────
    def record_job_started(self) -> None:
        self.jobs_total.labels(status="started").inc()
        self.jobs_in_progress.inc()

    def record_job_completed(self, duration_seconds: float) -> None:
        self.jobs_total.labels(status="completed").inc()
        self.jobs_in_progress.dec()
        if duration_seconds >= 0:
            self.job_duration_seconds.observe(duration_seconds)

    def record_job_failed(self, duration_seconds: float) -> None:
        self.jobs_total.labels(status="failed").inc()
        self.jobs_in_progress.dec()
        if duration_seconds >= 0:
            self.job_duration_seconds.observe(duration_seconds)

    # ─── Claim & Cascade Methods ─────────────────────────────────────────────
    def record_claim(self, stage: str, outcome: str) -> None:
        safe_stage = sanitize_label(stage, allowed={"stage_a", "stage_b", "stage_c", "none"})
        safe_outcome = sanitize_label(
            outcome,
            allowed={"supported", "discrepancy", "insufficient_evidence", "escalated", "pending_review", "fallback"},
        )
        self.claims_total.labels(stage=safe_stage, outcome=safe_outcome).inc()

    def record_claim_requiring_review(self) -> None:
        self.claims_requiring_review_total.inc()

    def record_cascade_evaluation(self, stage: str, outcome: str, duration_seconds: Optional[float] = None) -> None:
        safe_stage = sanitize_label(stage, allowed={"stage_a", "stage_b", "stage_c"})
        safe_outcome = sanitize_label(
            outcome,
            allowed={"resolved", "escalated", "fallback", "schema_invalid", "unavailable", "discrepancy", "supported", "insufficient_evidence"},
        )
        self.cascade_evaluations_total.labels(stage=safe_stage, outcome=safe_outcome).inc()
        if duration_seconds is not None and duration_seconds >= 0:
            self.stage_duration_seconds.labels(stage=safe_stage).observe(duration_seconds)

    def record_stage_c_fallback(self, reason: str) -> None:
        safe_reason = sanitize_label(
            reason,
            allowed={"llm_unavailable", "llm_schema_invalid", "narrative_not_processed", "timeout", "circuit_breaker_open", "unknown"},
        )
        self.stage_c_fallbacks_total.labels(reason=safe_reason).inc()
        self.fallbacks_total.labels(reason=safe_reason).inc()

    # ─── Model Serving Methods ───────────────────────────────────────────────
    def record_model_request(self, model: str, outcome: str, duration_seconds: Optional[float] = None) -> None:
        safe_model = sanitize_label(model, allowed={"qwen", "modernbert", "vllm_gateway"})
        safe_outcome = sanitize_label(outcome, allowed={"success", "failure", "timeout", "schema_invalid", "circuit_open"})
        self.model_requests_total.labels(model=safe_model, outcome=safe_outcome).inc()
        if duration_seconds is not None and duration_seconds >= 0:
            self.model_request_duration_seconds.labels(model=safe_model).observe(duration_seconds)

    def record_model_retry(self, model: str) -> None:
        safe_model = sanitize_label(model, allowed={"qwen", "modernbert", "vllm_gateway"})
        self.model_retries_total.labels(model=safe_model).inc()

    def set_circuit_breaker_state(self, model: str, state: str) -> None:
        safe_model = sanitize_label(model, allowed={"qwen", "modernbert", "vllm_gateway"})
        safe_state = str(state).upper()
        # 0=CLOSED, 1=HALF_OPEN, 2=OPEN
        val = 0.0
        if safe_state == "HALF_OPEN":
            val = 1.0
        elif safe_state == "OPEN":
            val = 2.0
        self.circuit_breaker_state.labels(model=safe_model).set(val)

    def record_circuit_breaker_transition(self, model: str, to_state: str) -> None:
        safe_model = sanitize_label(model, allowed={"qwen", "modernbert", "vllm_gateway"})
        safe_to_state = sanitize_label(to_state, allowed={"closed", "half_open", "open"})
        self.circuit_breaker_transitions_total.labels(model=safe_model, to_state=safe_to_state).inc()
        self.set_circuit_breaker_state(safe_model, safe_to_state)

    def set_concurrency_permits(self, model: str, in_use: int) -> None:
        safe_model = sanitize_label(model, allowed={"qwen", "modernbert", "vllm_gateway"})
        self.concurrency_permits_in_use.labels(model=safe_model).set(max(0, int(in_use)))

    def set_concurrency_waiting(self, model: str, waiting: int) -> None:
        safe_model = sanitize_label(model, allowed={"qwen", "modernbert", "vllm_gateway"})
        self.concurrency_waiting_requests.labels(model=safe_model).set(max(0, int(waiting)))

    def record_concurrency_wait_timeout(self, model: str) -> None:
        safe_model = sanitize_label(model, allowed={"qwen", "modernbert", "vllm_gateway"})
        self.concurrency_wait_timeout_total.labels(model=safe_model).inc()

    def set_model_readiness(self, model: str, is_ready: bool) -> None:
        safe_model = sanitize_label(model, allowed={"qwen", "modernbert", "vllm_gateway"})
        self.model_readiness.labels(model=safe_model).set(1.0 if is_ready else 0.0)

    def record_model_unavailable(self, model: str, reason: str) -> None:
        safe_model = sanitize_label(model, allowed={"qwen", "modernbert", "vllm_gateway"})
        safe_reason = sanitize_label(
            reason,
            allowed={"circuit_breaker_open", "circuit_breaker_half_open", "server_unhealthy", "server_unreachable", "model_not_loaded", "stage_b_not_configured", "base_url_not_configured", "unknown"},
        )
        self.model_unavailable_events_total.labels(model=safe_model, reason=safe_reason).inc()

    # ─── Human Review Methods ────────────────────────────────────────────────
    def record_review_action(self, action: str, outcome: str = "success") -> None:
        safe_action = sanitize_label(action, allowed={"accept", "edit", "reject", "escalate", "reopen"})
        safe_outcome = sanitize_label(outcome, allowed={"success", "error", "unauthorized", "conflict"})
        self.review_claims_total.labels(action=safe_action, outcome=safe_outcome).inc()

    def set_review_queue_size(self, size: int) -> None:
        self.review_queue_size.set(max(0, int(size)))

    def record_review_duration(self, duration_seconds: float) -> None:
        if duration_seconds >= 0:
            self.review_duration_seconds.observe(duration_seconds)

    # ─── FHIR Export Methods ─────────────────────────────────────────────────
    def record_fhir_export_attempt(self) -> None:
        pass  # tracked in outcome methods

    def record_fhir_export_success(self, duration_seconds: float) -> None:
        self.fhir_exports_total.labels(status="success", reason="none").inc()
        if duration_seconds >= 0:
            self.fhir_export_duration_seconds.observe(duration_seconds)

    def record_fhir_export_failure(self, status: str, reason: str, duration_seconds: Optional[float] = None) -> None:
        safe_status = sanitize_label(status, allowed={"rejected", "validation_failure", "eligibility_failure", "internal_error"})
        safe_reason = sanitize_label(
            reason,
            allowed={
                "not_export_eligible",
                "unresolved_claims",
                "fallback_claim",
                "approval_missing",
                "patient_identity_not_confirmed",
                "fhir_validation_failure",
                "document_not_found",
                "audit_failure",
                "unknown",
            },
        )
        self.fhir_exports_total.labels(status=safe_status, reason=safe_reason).inc()
        if duration_seconds is not None and duration_seconds >= 0:
            self.fhir_export_duration_seconds.observe(duration_seconds)

    # ─── Security Methods ────────────────────────────────────────────────────
    def record_security_event(self, event_type: str) -> None:
        safe_event = sanitize_label(
            event_type,
            allowed={
                "malformed_file",
                "quarantine",
                "oversized_file",
                "excessive_pages",
                "mime_spoof",
                "blob_access_denied",
                "auth_failure",
                "forbidden_admin_phi",
                "suspicious_input",
                "encrypted_pdf",
            },
        )
        self.security_events_total.labels(event_type=safe_event).inc()

    # ─── HTTP API Methods ────────────────────────────────────────────────────
    def record_http_request(self, method: str, route: str, status_code: int, duration_seconds: float) -> None:
        safe_method = sanitize_label(method, allowed={"get", "post", "put", "patch", "delete", "options", "head"}).upper()
        # Route template normalization: prevent any UUID or raw ID
        safe_route = _UUID_PATTERN.sub("{id}", route.strip())
        if len(safe_route) > 80:
            safe_route = safe_route[:80]
        if not safe_route.startswith("/"):
            safe_route = "/" + safe_route

        # Status class
        if 200 <= status_code < 300:
            status_class = "2xx"
        elif 300 <= status_code < 400:
            status_class = "3xx"
        elif 400 <= status_code < 500:
            status_class = "4xx"
        elif 500 <= status_code < 600:
            status_class = "5xx"
        else:
            status_class = "other"

        self.http_requests_total.labels(method=safe_method, route=safe_route, status_class=status_class).inc()
        if duration_seconds >= 0:
            self.http_request_duration_seconds.labels(method=safe_method, route=safe_route).observe(duration_seconds)

    # ─── EHR Transmission Methods (Phase 8.12) ───────────────────────────────
    def record_ehr_transmission(
        self,
        status: str,
        destination: str = "sandbox",
        duration_seconds: Optional[float] = None,
    ) -> None:
        """Record an EHR transmission event with low-cardinality status and destination."""
        safe_status = sanitize_label(
            status,
            allowed={"attempt", "success", "failure", "timeout", "rejected", "duplicate", "auth_error", "validation_error"},
        )
        safe_dest = sanitize_label(
            destination,
            allowed={"sandbox", "mock", "staging", "production", "unknown"},
        )
        self.ehr_transmissions_total.labels(status=safe_status, destination=safe_dest).inc()
        if duration_seconds is not None and duration_seconds >= 0:
            self.ehr_transmission_duration_seconds.labels(destination=safe_dest).observe(duration_seconds)

    # ─── Export / Output ─────────────────────────────────────────────────────
    def generate_metrics_text(self) -> bytes:
        """Generate Prometheus exposition format bytes from the registry."""
        return generate_latest(self.registry)


# ─── Global Application Singleton ────────────────────────────────────────────

_GLOBAL_METRICS: Optional[ClinDocMetrics] = None
_GLOBAL_LOCK = threading.Lock()


def get_metrics() -> ClinDocMetrics:
    """Obtain or initialize the application-wide ClinDocMetrics instance."""
    global _GLOBAL_METRICS
    with _GLOBAL_LOCK:
        if _GLOBAL_METRICS is None:
            _GLOBAL_METRICS = ClinDocMetrics()
        return _GLOBAL_METRICS


def set_metrics(metrics: Optional[ClinDocMetrics]) -> None:
    """Explicitly override the global ClinDocMetrics instance (for test isolation)."""
    global _GLOBAL_METRICS
    with _GLOBAL_LOCK:
        _GLOBAL_METRICS = metrics


def reset_metrics() -> ClinDocMetrics:
    """Reset to a fresh CollectorRegistry and metrics instance."""
    new_metrics = ClinDocMetrics()
    set_metrics(new_metrics)
    return new_metrics
