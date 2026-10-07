# Phase 8.10 — Observability & Operational Monitoring Verification Report

## Executive Summary

Phase 8.10 implements a complete production-oriented operational observability layer for the ClinDoc platform without introducing clinical inference or altering the verification cascade logic.

The instrumentation provides real-time visibility across all operational subsystems:
- **Application & Ingestion Lifecycle**: Ingested, rejected, quarantined, completed documents, and security validation screening events.
- **Job Processing & Work queues**: In-progress workers, duration histograms, completion/failure rates.
- **Claim Verification Cascade**: Resolution distributions and latency breakdowns across Stage A (Deterministic), Stage B (ModernBERT NLI), and Stage C (Qwen LLM Judge).
- **Model Serving & Resilience**: Inference counts, latency histograms, circuit-breaker state transitions (CLOSED, OPEN, HALF-OPEN), concurrency limiter saturation, and model readiness status.
- **Fallback Pathways**: Tracking reasons for human review routing (`llm_unavailable`, `llm_schema_invalid`, `narrative_not_processed`).
- **Human-Review Workbench**: Claims entering review, review action/resolution throughput, and pending queue size.
- **FHIR R4B Export**: Export attempts, successes, durations, and eligibility/validation rejection categories.
- **API Traffic & Security**: Request count, duration histograms, HTTP status categories, and route templates normalized to prevent UUID/ID label cardinality explosion.
- **Deployment Infrastructure**: Prometheus scraping configuration, operational alert definitions (Critical & Warning), Grafana datasource/dashboard provisioning, and private bridge networking.

Strict boundaries were preserved throughout: zero Protected Health Information (PHI) enters metric names or labels, all labels are low-cardinality and sanitized, `/metrics` access is internal/network-restricted, and the legacy Streamlit UI and authoritative specifications remain untouched.

---

## Metrics Catalog

All metrics use Prometheus-compatible naming (`clindoc_*`), strict typing, and sanitized low-cardinality labels.

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `clindoc_documents_total` | Counter | `status` (`ingested`, `rejected`, `quarantined`, `completed`, `requires_review`) | Tracks cumulative document ingestion outcomes. |
| `clindoc_jobs_total` | Counter | `status` (`started`, `completed`, `failed`) | Tracks asynchronous processing job lifecycle counts. |
| `clindoc_jobs_in_progress` | Gauge | *None* | Measures currently executing jobs across worker processes. |
| `clindoc_job_duration_seconds` | Histogram | *None* | Measures total processing time per document job. |
| `clindoc_claims_processed_total` | Counter | `stage` (`stage_a`, `stage_b`, `stage_c`, `pre_cascade`), `status` (`supported`, `refuted`, `discrepancy`, `insufficient_evidence`, `unresolved`, `quarantined`) | Measures claim verification volume and resolution status across cascade tiers. |
| `clindoc_claims_by_stage_total` | Counter | `stage` (`stage_a`, `stage_b`, `stage_c`) | Tracks total claim evaluations entering each verification stage. |
| `clindoc_claims_requiring_human_review_total` | Counter | `reason` (`disagreement`, `low_confidence`, `insufficient_evidence`, `llm_unavailable`, `llm_schema_invalid`, `narrative_not_processed`) | Measures volume and root cause for claims routed to human review. |
| `clindoc_cascade_stage_duration_seconds` | Histogram | `stage` (`stage_a`, `stage_b`, `stage_c`) | Captures execution duration for each cascade verification tier. |
| `clindoc_cascade_escalations_total` | Counter | `from_stage` (`stage_a`, `stage_b`), `reason` (`discrepancy`, `low_confidence`, `insufficient_evidence`) | Measures inter-stage claim escalation frequency and rationale. |
| `clindoc_model_requests_total` | Counter | `model` (`qwen_2_5_14b_instruct`, `modernbert`), `status` (`success`, `timeout`, `schema_invalid`, `circuit_open`, `error`) | Tracks model-serving invocation volume and outcomes. |
| `clindoc_model_retries_total` | Counter | `model` (`qwen_2_5_14b_instruct`) | Measures retry attempts after transient inference failures. |
| `clindoc_model_inference_duration_seconds` | Histogram | `model` (`qwen_2_5_14b_instruct`) | Measures round-trip model-serving inference latency. |
| `clindoc_circuit_breaker_state` | Gauge | `service` (`qwen_vllm`) | Current circuit breaker state (0 = CLOSED, 1 = OPEN, 2 = HALF-OPEN). |
| `clindoc_circuit_breaker_transitions_total` | Counter | `service` (`qwen_vllm`), `from_state`, `to_state` | Counts circuit breaker state transition events. |
| `clindoc_concurrency_permits_in_use` | Gauge | `service` (`qwen_vllm`) | Active concurrent inference slots in use. |
| `clindoc_concurrency_requests_waiting` | Gauge | `service` (`qwen_vllm`) | Requests waiting for an available concurrency permit. |
| `clindoc_concurrency_timeouts_total` | Counter | `service` (`qwen_vllm`) | Inferences rejected due to concurrency permit timeout. |
| `clindoc_model_readiness_state` | Gauge | `model` (`qwen_vllm`, `modernbert`) | Model health readiness state (1 = Ready, 0 = Not Ready). |
| `clindoc_model_unavailable_events_total` | Counter | `model` (`qwen_vllm`, `modernbert`), `reason` (`circuit_open`, `server_unreachable`, `http_5xx`, `timeout`) | Measures model unavailability events and root causes. |
| `clindoc_fallback_events_total` | Counter | `reason` (`llm_unavailable`, `llm_schema_invalid`, `narrative_not_processed`) | Counts automated fallback activations. |
| `clindoc_review_claims_entered_total` | Counter | *None* | Cumulative count of claims entering human-review queue. |
| `clindoc_review_claims_actioned_total` | Counter | `action` (`accept`, `edit`, `reject`, `escalate`, `reopen`), `outcome` (`supported`, `refuted`, `discrepancy`, `insufficient_evidence`, `escalated`, `reopened`) | Tracks reviewer throughput and decision outcomes. |
| `clindoc_review_queue_size` | Gauge | *None* | Measures pending unreviewed claims in the workbench queue. |
| `clindoc_fhir_export_attempts_total` | Counter | *None* | Total FHIR R4B export requests initiated. |
| `clindoc_fhir_export_success_total` | Counter | *None* | Total successfully generated and validated FHIR Bundles. |
| `clindoc_fhir_export_failures_total` | Counter | `category` (`not_export_eligible`, `unresolved_claims`, `fallback_claim`, `approval_missing`, `patient_identity_not_confirmed`, `fhir_validation_failure`) | Counts FHIR export rejections categorized by cause. |
| `clindoc_fhir_export_duration_seconds` | Histogram | *None* | Captures bundle transformation and validation execution latency. |
| `clindoc_security_events_total` | Counter | `event_type` (`malformed_file`, `quarantine`, `oversized_file`, `excessive_pages`, `mime_spoof`, `encrypted_pdf`, `authorization_failure`, `blob_security_rejection`, `suspicious_request`) | Quantifies defensive rejections and ingestion anomalies. |
| `clindoc_http_requests_total` | Counter | `method` (`GET`, `POST`, `PUT`, `DELETE`), `route` (parameterized template, e.g. `/api/v1/documents/{document_id}/export`), `status_code` (`200`, `400`, `401`, `403`, `404`, `500`) | Measures inbound API traffic by endpoint and status code. |
| `clindoc_http_request_duration_seconds` | Histogram | `method` (`GET`, `POST`, etc.), `route` (parameterized template) | Inbound API request response time distribution. |

*Zero PHI Note*: All labels are validated against strict allowed strings or pattern-sanitized via `sanitize_label()`. Raw document IDs, claim IDs, patient names, MRNs, narrative texts, evidence strings, and prompts are explicitly excluded.

---

## Grafana Dashboard

The provisioned dashboard file `deploy/observability/grafana/dashboards/clindoc_operations.json` contains 20 panels categorized across 6 operational areas:

### 1. System Health
- **Application Readiness & Health**: Shows live `/health` and `/health/ready` probe statuses (Gauge). Answers: *Is the API healthy and ready to process traffic?*
- **Model Service Readiness (ModernBERT & Qwen)**: Displays readiness gauges for both inference backends. Answers: *Are ML models initialized and responsive?*
- **Qwen Circuit Breaker State**: Visualizes CLOSED (green/0), HALF-OPEN (yellow/2), and OPEN (red/1) states. Answers: *Is the LLM circuit breaker tripped?*
- **Active Workers & In-Progress Jobs**: Real-time count of jobs undergoing processing. Answers: *How many documents are being verified right now?*

### 2. Throughput
- **Document Ingestion Rate**: Time-series rate (`rate(clindoc_documents_total[1m])`) split by status. Answers: *How many documents/min are ingesting, completing, or quarantining?*
- **Claim Processing Throughput**: Verification volume per minute by cascade stage. Answers: *What is the claim verification velocity?*
- **API Request Rate by Route**: Top API routes handling traffic. Answers: *What is the workload distribution across API endpoints?*

### 3. Latency
- **Document Job Processing Latency**: p50, p95, and p99 processing duration percentiles. Answers: *How long do full pipeline jobs take end-to-end?*
- **Cascade Stage Latency Breakdown**: Comparative p95 latency across Stage A, Stage B, and Stage C. Answers: *Which verification tier is the primary latency bottleneck?*
- **Model Inference Latency**: p50 and p95 vLLM response latency. Answers: *Is model inference slowing down under load?*
- **FHIR Export Latency**: p95 duration of FHIR bundle generation. Answers: *How fast are export operations executing?*

### 4. Verification Cascade & Fallback
- **Claim Resolution by Stage**: Distribution of claims resolved at Stage A, Stage B, vs escalated to Stage C. Answers: *What percentage of claims bypass expensive LLM inference?*
- **Inter-Stage Escalation Rate**: Rate of claims advancing from A to B and B to C. Answers: *Why are claims escalating through the cascade?*
- **Stage C Fallback Rate**: Rate of automated fallback triggers. Answers: *Is the LLM failing or returning invalid output?*
- **Fallback Reason Distribution**: Breakdown of `llm_unavailable`, `llm_schema_invalid`, and `narrative_not_processed`. Answers: *Are failures transport/availability or schema/formatting issues?*

### 5. Human Review Workbench
- **Pending Review Queue Size**: Current number of claims awaiting human clinician action. Answers: *Is the human review workload backing up?*
- **Review Decision Throughput & Outcomes**: Rates of accept, edit, reject, and escalate actions. Answers: *How fast are reviewers clearing the queue and what are their verdicts?*

### 6. Error & Security Monitoring
- **API Errors (4xx & 5xx)**: Rate of client and server errors by endpoint. Answers: *Are clients seeing errors on API calls?*
- **Model Errors & Timeouts**: Rate of model request timeouts, schema-invalids, and circuit rejections. Answers: *Are vLLM calls failing?*
- **Ingestion & Security Rejections**: Rate of malformed files, quarantine triggers, and size/mime rejections. Answers: *Are adversarial or corrupted files being screened?*
- **FHIR Export Failure Breakdown**: Detailed failure reasons for rejected exports. Answers: *Why are FHIR exports failing?*

---

## Operational Alert Rules

Defined in `deploy/observability/alerting_rules.yml`:

### Critical Alerts
1. **ClinDocAppNotReady**:
   - Condition: `clindoc_model_readiness_state{model="app"} == 0` for 2 minutes.
   - Severity: `critical`
   - Rationale: Core API health probe failure blocks ingestion and verification.
2. **ClinDocModernBertUnavailable**:
   - Condition: `clindoc_model_readiness_state{model="modernbert"} == 0` for 3 minutes.
   - Severity: `critical`
   - Rationale: ModernBERT is required for Stage B NLI inference; unavailable status halts automated verification.
3. **ClinDocSustainedQwenOutage**:
   - Condition: `clindoc_model_readiness_state{model="qwen_vllm"} == 0` for 5 minutes.
   - Severity: `critical`
   - Rationale: While Stage A/B can continue, sustained Qwen outage forces all escalated claims into review fallback.
4. **ClinDocCircuitBreakerOpenProlonged**:
   - Condition: `clindoc_circuit_breaker_state{service="qwen_vllm"} == 1` for 5 minutes.
   - Severity: `critical`
   - Rationale: Repeated vLLM failures have tripped the circuit breaker; requires immediate operational investigation.

### Warning Alerts
1. **ClinDocHighStageCFallbackRate**:
   - Condition: `rate(clindoc_fallback_events_total[5m]) > 5` for 5 minutes.
   - Severity: `warning`
   - Rationale: Indicates potential LLM degradation, high timeout volume, or parsing issues.
2. **ClinDocHighSchemaInvalidRate**:
   - Condition: `rate(clindoc_model_requests_total{status="schema_invalid"}[5m]) > 2` for 5 minutes.
   - Severity: `warning`
   - Rationale: Model output is failing JSON schema invariants, signaling prompt misalignment or model degradation.
3. **ClinDocHighApiErrorRate**:
   - Condition: `sum(rate(clindoc_http_requests_total{status_code=~"5.."}[5m])) / sum(rate(clindoc_http_requests_total[5m])) > 0.05` for 5 minutes.
   - Severity: `warning`
   - Rationale: API 5xx error rate exceeds 5% of total traffic.
4. **ClinDocIngestionRejectionSpike**:
   - Condition: `rate(clindoc_documents_total{status=~"rejected|quarantined"}[5m]) > 10` for 5 minutes.
   - Severity: `warning`
   - Rationale: Spike in malformed, oversized, or quarantined files may indicate upstream feeder defects or malicious activity.
5. **ClinDocGrowingReviewQueue**:
   - Condition: `clindoc_review_queue_size > 100` for 15 minutes.
   - Severity: `warning`
   - Rationale: Review workload is accumulating faster than clinicians can process, risking turnaround SLA breaches.
6. **ClinDocSustainedLatencySpike**:
   - Condition: `histogram_quantile(0.95, sum(rate(clindoc_cascade_stage_duration_seconds_bucket[5m])) by (le, stage)) > 30` for 10 minutes.
   - Severity: `warning`
   - Rationale: Cascade execution duration exceeds 30s at p95.
7. **ClinDocConcurrencySaturation**:
   - Condition: `clindoc_concurrency_requests_waiting > 10` for 5 minutes.
   - Severity: `warning`
   - Rationale: vLLM concurrency slots are fully saturated, causing queueing delays.

*Note on Thresholds*: The thresholds above represent sensible operational baselines suitable for staging and initial production calibration. Final values should be tuned based on production traffic baselines.

---

## Security & PHI Prevention Architecture

### Multi-Layer Redaction & Sanitization
1. **No PHI in Metric Declarations**: No Prometheus metric or label definition accepts patient names, dates of birth, MRNs, patient IDs, claim IDs, document IDs, clinical text, evidence snippets, or prompt contents.
2. **UUID & Pattern Redaction**: The `sanitize_label()` utility actively strips UUID strings, hexadecimal identifiers, and arbitrary free-text values, replacing them with generic placeholders (`unknown` or standard category codes).
3. **Route Template Normalization**: FastAPI `ObservabilityMiddleware` resolves incoming requests against registered Starlette routes (e.g. `/api/v1/documents/{document_id}/export`) rather than using raw request paths (e.g. `/api/v1/documents/4a87c1be-710e-473d-88b4-9c5957b12345/export`). Route paths not matching registered routes are collapsed into `/unknown_route`.
4. **Attacker-Controlled String Isolation**: Ingestion rejections (malformed files, mime spoofs, excessive pages) map to predefined enumerations (`malformed_file`, `oversized_file`, `mime_spoof`, etc.) rather than attacker-provided filenames or headers.
5. **Restricted `/metrics` Exposure**:
   - Internal Network Guard: By default, the `/metrics` endpoint rejects requests originating outside internal network ranges (RFC 1918 private subnets `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, and loopback `127.0.0.1/::1`).
   - Token Authentication: When `CLINDOC_METRICS_TOKEN` is configured, incoming scrapers must supply a matching `Bearer <token>` in the `Authorization` header.
   - Scrape output contains strictly Prometheus format counters, histograms, and gauges.

---

## Deployment Configuration

Deployment configuration resides in `deploy/observability/`:
```text
deploy/observability/
├── docker-compose.yml
├── prometheus.yml
├── alerting_rules.yml
└── grafana/
    ├── dashboards/
    │   └── clindoc_operations.json
    └── provisioning/
        ├── dashboards/
        │   └── dashboards.yml
        └── datasources/
            └── datasource.yml
```

### Network Architecture
- Private Docker Bridge Network: `clindoc_monitoring` with `internal: false` for scraper-to-API communication, keeping metrics traffic inside the container cluster.
- Prometheus Container: `prom/prometheus:v2.51.0`, bound to `127.0.0.1:9090` (local administrative port, not public).
- Grafana Container: `grafana/grafana:10.4.0`, bound to `127.0.0.1:3000` (local administrative port, not public).
- Scrape Configuration: Scrapes `clindoc-api:8000/metrics` every 15s with a 10s timeout, labeled with `env=production` and `app=clindoc`.

---

## Automated Test Results

### Observability Suite (`tests/test_observability.py`)
Executed: 15 passed, 0 failed in 8.57 seconds.

| Test Case | Scope | Result |
|---|---|---|
| `test_metrics_counters_histograms_gauges` | Core counters, histograms, gauges functionality | PASSED |
| `test_sanitize_label_prevents_uuids_and_phi` | UUID redaction and low-cardinality sanitizer | PASSED |
| `test_cascade_metrics_emission` | Stage A, B, C evaluation and latency recording | PASSED |
| `test_stage_c_retry_and_fallback_metrics` | Model request tracking, retries, and fallback recording | PASSED |
| `test_circuit_breaker_state_transition_metrics` | State transitions and status gauges | PASSED |
| `test_concurrency_limiter_metrics` | Active permits, queue gauge, and permit timeouts | PASSED |
| `test_model_health_readiness_metrics` | Readiness summary gauge synchronization | PASSED |
| `test_http_metrics_route_normalization_no_raw_ids` | Route template normalization and ID suppression | PASSED |
| `test_metrics_endpoint_access_control` | Internal IP restriction and Bearer token auth | PASSED |
| `test_metrics_output_contains_zero_phi_or_secrets` | Scrape output inspection for PHI/secret leaks | PASSED |
| `test_ingestion_security_metrics_emission` | Screening security counters and quarantine events | PASSED |
| `test_fhir_export_metrics` | FHIR export success, failure categories, and durations | PASSED |
| `test_deployment_prometheus_config_valid` | YAML schema and scrape target validation | PASSED |
| `test_deployment_alerting_rules_valid` | Alert rules syntax and threshold consistency | PASSED |
| `test_deployment_docker_compose_and_grafana_valid` | Docker compose and Grafana JSON schema validation | PASSED |

### Full Regression Suite
Executed: 164 passed, 0 failed in 17.96 seconds across all historical phases.
- `tests/test_security_adversarial.py` (Phase 8.9): 28/28 PASSED
- `tests/test_fhir_export.py` (Phase 8.8): 20/20 PASSED
- `tests/test_circuit_breaker.py` (Phase 8.7): 29/29 PASSED
- `tests/test_model_health.py` (Phase 8.7): 21/21 PASSED
- `tests/test_verification_cascade.py`: 21/21 PASSED
- `tests/test_structured_fallback_worker.py`: 45/45 PASSED

Total suite: **179 tests executed, 179 PASSED, 0 FAILED**.

---

## Known Infrastructure Limitations

1. **Docker Daemon Execution**:
   - The Docker Compose configuration, Prometheus YAML, alerting rules, and Grafana dashboards were validated statically and via automated schema/structure tests.
   - An active live Docker daemon was not executed in the local test environment; real container runtime behavior must be validated when deploying to containerized staging/production clusters.
2. **Prometheus Scrape Interval vs Alert Durations**:
   - Alert rule evaluation windows (`for: 2m`, `for: 5m`) assume a 15-second scrape interval. Environments configuring different scrape intervals should adjust evaluation periods accordingly.
3. **Network Boundary Enforcement**:
   - The `/metrics` endpoint includes application-level IP inspection. In production deployments fronted by reverse proxies (Envoy, NGINX), ensure `X-Forwarded-For` or network ingress security policies correctly isolate `/metrics` to monitoring VPCs.

---

## Final Verification Checklist

- [x] Prometheus-compatible metrics implemented.
- [x] Ingestion, jobs, claims, cascade stages, models, fallbacks, review, FHIR, API, and security events covered.
- [x] Labels strictly low-cardinality and sanitized.
- [x] Zero PHI in metric names, labels, or output.
- [x] Zero secrets in metric names, labels, or output.
- [x] Grafana operational dashboard provisioned and validated.
- [x] Alerting rules defined with clear operational thresholds.
- [x] `/metrics` endpoint access restricted.
- [x] Health endpoint (`/health`, `/health/ready`) semantics preserved.
- [x] Zero alterations to clinical decision logic or cascade routing.
- [x] Automated tests verified key instrumentation (15/15 passed).
- [x] Regression suite passed (164/164 passed).
- [x] Authoritative specifications (`PROJECT_SPEC.md`, `PRODUCTIZATION_SPEC.md`, `PRODUCTIZATION_DECISIONS.md`) untouched.
- [x] Legacy Streamlit UI (`src/clindoc/ui/`) untouched.
- [x] Nothing staged, committed, or pushed.
