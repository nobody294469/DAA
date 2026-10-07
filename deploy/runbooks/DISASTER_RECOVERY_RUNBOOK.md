# ClinDoc Disaster Recovery & Operational Failure Runbook

**Phase 8.11 — Air-Gapped / Private Deployment Operations**  
**Classification:** Operational Runbook — Read-Only Production Specification  
**Authority:** `PRODUCTIZATION_SPEC.md` §13 (Failures F-01 to F-19), `PRODUCTIZATION_DECISIONS.md` Phase 8.7 Step 3, Phase 8.10

---

## Operational Failure Matrix & Procedures

This runbook outlines mandatory actions for 13 operational incident scenarios.

---

### Scenario 1: ClinDoc API Unavailable
- **Symptom**: Ingress gateway returns 502/503/504; review workbench UI cannot load documents; HTTP health checks time out.
- **Observability Signal**: `probe_success == 0` on API port 8000; alert `ClinDocAppNotReady` fires; absence of inbound HTTP traffic in Prometheus.
- **Immediate Operator Action**: Notify clinicians that workbench and upload portal are temporarily unavailable. Inbound document ingestion paused.
- **Recovery Action**:
  ```bash
  docker logs clindoc-api --tail 100
  docker restart clindoc-api
  ```
  Check memory limits and verify port 8000 bindings.
- **Verification After Recovery**: `curl -f http://localhost:8000/api/v1/health` returns `{"status": "healthy"}`.
- **Clinical Processing Impact**: **STOPPED**. No new documents can be ingested; review actions cannot be persisted.

---

### Scenario 2: Background Worker Unavailable
- **Symptom**: New documents remain indefinitely in `INGESTED` state; processing queue backlog grows; claims are not being verified.
- **Observability Signal**: `clindoc_jobs_in_progress == 0` while pending jobs exist; `rate(clindoc_jobs_total{status="completed"}[5m]) == 0`; review queue remains static.
- **Immediate Operator Action**: Ingestion can continue, but alert clinicians that new verification results are delayed.
- **Recovery Action**:
  ```bash
  docker logs clindoc-worker --tail 100
  # Check for unhandled exceptions or OOM kill
  docker restart clindoc-worker
  ```
  Worker will automatically reclaim expired leases (`CLINDOC_JOB_LEASE_SECONDS=300`) upon restart.
- **Verification After Recovery**: `docker logs clindoc-worker` shows active polling; `clindoc_jobs_total{status="started"}` increments.
- **Clinical Processing Impact**: **TEMPORARILY DELAYED**. Verification paused until worker restarts; no clinical misclassification occurs.

---

### Scenario 3: Relational Database Unavailable
- **Symptom**: API and Worker raise `OperationalError: could not connect to server`; all endpoints return 500/503.
- **Observability Signal**: `clindoc_model_readiness_state{model="app"} == 0`; `/api/v1/health/ready` returns 503 with `"database": "disconnected"`.
- **Immediate Operator Action**: Halt all incoming traffic at ingress.
- **Recovery Action**:
  ```bash
  docker logs clindoc-postgres --tail 100
  docker restart clindoc-postgres
  docker exec clindoc-postgres pg_isready -U clindoc_user -d clindoc_db
  ```
  If data corruption occurred, initiate point-in-time restore from `BACKUP_RESTORE_RUNBOOK.md`.
- **Verification After Recovery**: `GET /api/v1/health/ready` returns 200 with `"database": "connected"`.
- **Clinical Processing Impact**: **STOPPED**. All state transitions, audit logging, and review operations require database connectivity.

---

### Scenario 4: Encrypted Blob Storage Unavailable
- **Symptom**: Document uploads fail with 500; Worker fails during OCR/Ingestion stage with `BlobNotFoundError` or I/O error.
- **Observability Signal**: `clindoc_security_events_total{event_type="blob_security_rejection"}` or `clindoc_documents_total{status="rejected"}` spikes.
- **Immediate Operator Action**: Check storage volume mounts and filesystem availability.
- **Recovery Action**:
  1. Inspect volume mount: `df -h /var/lib/clindoc/blob_store`.
  2. Verify disk permissions: `chmod 700 /var/lib/clindoc/blob_store`.
  3. Validate `CLINDOC_BLOB_ENCRYPTION_KEY` in environment.
- **Verification After Recovery**: Run test blob store write/read via Python CLI sanity check.
- **Clinical Processing Impact**: **STOPPED**. Existing documents cannot be retrieved; new documents cannot be safely encrypted and persisted.

---

### Scenario 5: ModernBERT Unavailable (Checkpoint Missing / Corrupted)
- **Symptom**: Worker refuses jobs; jobs abort with `ProductionProfileError: ModernBERT checkpoint file not found or corrupted`.
- **Observability Signal**: `clindoc_model_readiness_state{model="modernbert"} == 0`; alert `ClinDocModernBertUnavailable` fires.
- **Immediate Operator Action**: Worker deliberately halts execution in production profile (`F-05` containment invariant).
- **Recovery Action**:
  1. Verify checkpoint path and integrity:
     ```bash
     sha256sum /opt/clindoc/models/checkpoints/modernbert_text_only_v3_fold0_seed42.pt
     # Must match audited SHA-256: d24add6135634f660d2fdcf1c51545e201cc65c00fd2d060452eac95be9e7902
     ```
  2. Re-mount or copy clean qualified checkpoint from local air-gapped installation media.
  3. Restart worker: `docker restart clindoc-worker`.
- **Verification After Recovery**: `GET /api/v1/health/ready` reports `"modernbert": "ready"`.
- **Clinical Processing Impact**: **STOPPED FOR NLI**. Worker refuses processing to prevent unverified heuristics or uncalibrated inferences.

---

### Scenario 6: Qwen / vLLM Unavailable (GPU Process Crash / Outage)
- **Symptom**: Stage C escalations encounter connection refusal; logs show `HTTPConnectionError` to `https://clindoc-vllm-tls-proxy:8443`.
- **Observability Signal**: `clindoc_model_unavailable_events_total{model="qwen_vllm"}` increments; `clindoc_fallback_events_total{reason="llm_unavailable"}` spikes; alert `ClinDocSustainedQwenOutage` fires.
- **Immediate Operator Action**: Do **NOT** stop the API or Worker.
- **Recovery Action**:
  ```bash
  # Check GPU status and VRAM utilization
  nvidia-smi
  docker logs clindoc-vllm-production --tail 100
  docker restart clindoc-vllm-production
  ```
  Wait for model reload (~60–90 seconds) until health check returns 200.
- **Verification After Recovery**: `curl -k https://localhost:8443/health` returns 200; `clindoc_model_readiness_state{model="qwen_vllm"} == 1`.
- **Clinical Processing Impact**: **PARTIAL CONTINUATION (DEGRADED)**.
  - Stage A (Deterministic) and Stage B (ModernBERT NLI) continue running normally.
  - Claims that require Stage C escalation are routed safely to human review with `fallback=True, fallback_reason="llm_unavailable"`.
  - Zero unverified claims are accepted autonomously.

---

### Scenario 7: Qwen Circuit Breaker OPEN
- **Symptom**: Stage C calls fail immediately without attempting network calls; error logs indicate `CircuitBreakerOpenError`.
- **Observability Signal**: `clindoc_circuit_breaker_state{service="qwen_vllm"} == 1`; alert `ClinDocCircuitBreakerOpenProlonged` fires.
- **Immediate Operator Action**: Review vLLM error logs to identify whether failure was caused by VRAM exhaustion, timeout, or bad prompt.
- **Recovery Action**:
  1. Once vLLM is confirmed healthy, the circuit breaker automatically tests recovery via `HALF-OPEN` probe after `CLINDOC_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30.0`.
  2. If manual reset is needed, restart the worker container: `docker restart clindoc-worker`.
- **Verification After Recovery**: `clindoc_circuit_breaker_state` transitions from `1` to `2` (HALF-OPEN), then to `0` (CLOSED).
- **Clinical Processing Impact**: **PARTIAL CONTINUATION**. Stage A/B continue; Stage C escalations fast-fail into human review workbench without latency delay.

---

### Scenario 8: TLS Certificate Failure or Expiry
- **Symptom**: ClinDoc API and Worker fail to communicate with vLLM proxy; `SSLError: certificate verify failed` or `SSL: CERTIFICATE_VERIFY_FAILED`.
- **Observability Signal**: Sudden spike in `clindoc_model_requests_total{status="error"}`; Stage C fallbacks jump to 100%.
- **Immediate Operator Action**: Inspect certificate expiration date:
  ```bash
  openssl x509 -enddate -noout -in deploy/vllm/certs/server.crt
  ```
- **Recovery Action**:
  1. Generate or import renewed certificate and private key.
  2. Copy to `deploy/vllm/certs/server.crt` and `server.key`.
  3. Reload Nginx: `docker exec clindoc-vllm-tls-proxy nginx -s reload`.
- **Verification After Recovery**: `curl -k -f https://localhost:8443/health` succeeds.
- **Clinical Processing Impact**: **PARTIAL CONTINUATION**. Stage A/B continue; Stage C escalations route to human review.

---

### Scenario 9: Prometheus Unavailable
- **Symptom**: Grafana panels show "No data"; alerting rules do not evaluate.
- **Observability Signal**: Scrape failures; unable to load `http://127.0.0.1:9090`.
- **Immediate Operator Action**: Operational metrics are temporarily paused.
- **Recovery Action**:
  ```bash
  docker logs clindoc-prometheus --tail 50
  docker restart clindoc-prometheus
  ```
- **Verification After Recovery**: Access `http://127.0.0.1:9090/targets` and verify `clindoc-api` endpoint is `UP`.
- **Clinical Processing Impact**: **NONE**. Core clinical document ingestion, verification cascade, human review, and FHIR export continue without interruption.

---

### Scenario 10: Grafana Unavailable
- **Symptom**: Administrator dashboards inaccessible on port 3000.
- **Observability Signal**: Browser connection refused on `http://127.0.0.1:3000`.
- **Immediate Operator Action**: None required for clinical operations.
- **Recovery Action**:
  ```bash
  docker logs clindoc-grafana --tail 50
  docker restart clindoc-grafana
  ```
- **Verification After Recovery**: Login to `http://127.0.0.1:3000` and view `ClinDoc Operations Dashboard`.
- **Clinical Processing Impact**: **NONE**. Operational dashboards are decoupled from backend verification.

---

### Scenario 11: Corrupted or Quarantined Document
- **Symptom**: Document ingestion returns status `QUARANTINED` or `REJECTED`; document is not dispatched to worker queue.
- **Observability Signal**: `clindoc_documents_total{status="quarantined"}` or `clindoc_security_events_total{event_type="quarantine"}` increments.
- **Immediate Operator Action**: Inspect audit event log for the specific quarantine reason (e.g., MIME spoofing, password-protected PDF, excessive pages $>100$, malformed PDF header).
- **Recovery Action**:
  1. Contact the submitting clinical department to obtain an uncorrupted, standard clinical PDF.
  2. Quarantined blobs remain isolated in encrypted storage for audit inspection and cannot be processed by the worker.
- **Verification After Recovery**: Re-submitted valid document ingests with status `INGESTED` and transitions to `PROCESSING`.
- **Clinical Processing Impact**: **ISOLATED TO DOCUMENT**. Malformed document is rejected defensively; system integrity and remaining documents are unaffected.

---

### Scenario 12: Failed FHIR Export
- **Symptom**: Document cannot be exported to FHIR R4B; `/api/v1/documents/{id}/export` returns 422 Unprocessable Entity.
- **Observability Signal**: `clindoc_fhir_export_failures_total` increments with specific category (`not_export_eligible`, `unresolved_claims`, `fallback_claim`, `approval_missing`).
- **Immediate Operator Action**: Inspect rejection category in response payload.
- **Recovery Action**:
  - `approval_missing`: Clinician must formally sign off document in the review workbench (`POST /api/v1/review/documents/{id}/approve`).
  - `unresolved_claims`: Ensure all conflicting claims have been reviewed, accepted, or edited.
  - `fallback_claim`: Check whether an unverified fallback claim requires human adjudication.
- **Verification After Recovery**: Re-attempt export; status transitions to `EXPORTED` and returns FHIR Bundle JSON.
- **Clinical Processing Impact**: **SAFE CONTAINMENT**. Incomplete or unapproved documents are strictly prohibited from exporting to the EHR, ensuring clinical safety.

---

### Scenario 13: Database / Storage Disk Failure (Full Restore)
- **Symptom**: Host storage subsystem crashed; database files corrupted; files missing from `/var/lib/clindoc/blob_store`.
- **Observability Signal**: Complete API and worker failure; PostgreSQL crash loop.
- **Immediate Operator Action**: Declare formal operational maintenance window. Shut down all services immediately.
- **Recovery Action**: Follow the comprehensive step-by-step restoration sequence detailed in [`BACKUP_RESTORE_RUNBOOK.md`](file:///d:/daa/deploy/runbooks/BACKUP_RESTORE_RUNBOOK.md).
- **Verification After Recovery**: Execute full post-restore verification suite: readiness probe, claim record count, and blob decryption test.
- **Clinical Processing Impact**: **HALTED DURING RESTORE; RESTORED TO POINT-IN-TIME AFTER COMPLETION**.
