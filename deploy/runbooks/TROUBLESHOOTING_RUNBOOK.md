# ClinDoc Operational Troubleshooting Runbook

**Phase 8.11 — Air-Gapped / Private Deployment Operations**  
**Classification:** Operational Runbook — Read-Only Production Diagnostics  
**Authority:** `PRODUCTIZATION_SPEC.md` §13, §14, `PRODUCTIZATION_DECISIONS.md` Phase 8.7, Phase 8.10

---

## 1. Centralized Log Inspection Commands

All components emit structured logs to container stdout/stderr. Inspect logs using Docker commands or export them to the hospital syslog/SIEM collector:

```bash
# 1. ClinDoc API Server Logs (HTTP requests, correlation IDs, routing)
docker logs -f --tail 100 clindoc-api

# 2. ClinDoc Background Worker Logs (OCR, extraction, verification cascade)
docker logs -f --tail 100 clindoc-worker

# 3. vLLM Serving Logs (Model inference, KV-cache, engine events)
docker logs -f --tail 100 clindoc-vllm-production

# 4. vLLM TLS Reverse Proxy Logs (Access logs, SSL handshakes, upstreams)
docker logs -f --tail 100 clindoc-vllm-tls-proxy

# 5. PostgreSQL Database Logs (Queries, connection pools, locks)
docker logs -f --tail 100 clindoc-postgres

# 6. Prometheus Server Logs (Scrape targets, alert evaluation)
docker logs -f --tail 100 clindoc-prometheus
```

---

## 2. Common Diagnostics & Resolution Workflows

### 2.1 Worker Queue Backlog & Stuck Jobs
**Problem**: Processing queue size (`clindoc_jobs_in_progress`) is high or jobs remain in `PROCESSING` state indefinitely.

**Root Causes**:
1. Worker crashed while holding an active job lease.
2. Ingestion volume exceeded worker processing capacity.
3. Complex, multi-page document exceeding OCR processing timeout.

**Diagnostic Steps**:
1. Query database for stalled processing jobs:
   ```bash
   docker exec clindoc-postgres psql -U clindoc_user -d clindoc_db -c "
     SELECT id, document_id, status, attempts, lease_expires_at, created_at
     FROM processing_jobs
     WHERE status = 'PROCESSING'
     ORDER BY created_at ASC LIMIT 10;
   "
   ```
2. Verify lease timeout behavior: Jobs past `lease_expires_at` are automatically reclaimed by healthy worker processes.
3. Scale worker concurrency: Start additional worker containers if CPU resources allow:
   ```bash
   docker compose -f deploy/docker-compose.prod.yml up -d --scale worker=2
   ```

---

### 2.2 Investigating Circuit Breaker Trips
**Problem**: Prometheus gauge `clindoc_circuit_breaker_state{service="qwen_vllm"} == 1` (OPEN).

**Diagnostic Steps**:
1. Check vLLM proxy health:
   ```bash
   curl -k -i https://localhost:8443/health
   ```
2. Inspect vLLM server logs for errors during the trip window:
   ```bash
   docker logs clindoc-vllm-production --since 10m | grep -iE "error|exception|cuda|oom"
   ```
3. Check circuit breaker state transitions in Prometheus:
   ```promql
   rate(clindoc_circuit_breaker_transitions_total[5m])
   ```
4. Reset Circuit Breaker:
   - Automated: The circuit breaker attempts a `HALF-OPEN` probe every 30 seconds (`CLINDOC_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30.0`).
   - Manual Force-Reset: Restarting the worker re-initializes the circuit breaker in `CLOSED` state:
     ```bash
     docker restart clindoc-worker
     ```

---

### 2.3 GPU Host VRAM Exhaustion (CUDA OOM)
**Problem**: vLLM container exits with `torch.cuda.OutOfMemoryError` or restarts repeatedly.

**Diagnostic Steps**:
1. Inspect current GPU allocation:
   ```bash
   nvidia-smi
   ```
2. Check `max-model-len` parameter:
   - In `deploy/docker-compose.prod.yml`, verify `--max-model-len 4096`. Values greater than 4096 may exceed 16GB VRAM on Tesla T4/L4 GPUs.
3. Check KV-cache memory fraction:
   - Ensure other GPU processes (e.g. OCR, PyTorch) are not sharing the same GPU device.
   - Set `CUDA_VISIBLE_DEVICES=0` exclusively for vLLM.

---

### 2.4 High Stage C Fallback & Schema-Invalid Output
**Problem**: High fallback rate (`clindoc_fallback_events_total`), but vLLM transport is responsive.

**Diagnostic Steps**:
1. Check metric `clindoc_model_requests_total{status="schema_invalid"}`.
2. Inspect worker logs for schema validation failures:
   ```bash
   docker logs clindoc-worker --tail 200 | grep -A 5 -i "schema_invalid"
   ```
3. Determine root cause:
   - If model output format is corrupted: Verify that model temperature is set to greedy ($T=0.0$) and sampling parameters match Phase 8.7 audited defaults.
   - If output violated schema invariants: Verify that the claim value or field type conforms to canonical verification schema.

---

### 2.5 Ingestion Screening Rejections
**Problem**: Document uploads fail with HTTP 400 or 422; documents flagged as `REJECTED` or `QUARANTINED`.

**Diagnostic Steps**:
1. Check Prometheus security counters:
   ```promql
   rate(clindoc_security_events_total[5m]) by (event_type)
   ```
2. Identify rejection category:
   - `malformed_file`: Uploaded file has invalid PDF/image magic bytes.
   - `mime_spoof`: Declared Content-Type does not match inspected magic bytes (e.g., EXE renamed to .pdf).
   - `oversized_file`: Document exceeds 50 MB payload limit.
   - `excessive_pages`: PDF exceeds 100 page defensive limit.
   - `encrypted_pdf`: PDF requires a decryption password.
3. Review audit event table for document UUID:
   ```bash
   docker exec clindoc-postgres psql -U clindoc_user -d clindoc_db -c "
     SELECT id, event_type, details, created_at
     FROM audit_events
     WHERE event_type = 'DOCUMENT_SCREENING_FAILED'
     ORDER BY created_at DESC LIMIT 5;
   "
   ```

---

### 2.6 Authentication & RBAC Authorization Rejections
**Problem**: Clinicians receive 401 Unauthorized or 403 Forbidden when accessing workbench routes.

**Diagnostic Steps**:
1. For 401 Unauthorized:
   - Validate bearer token presence: Client must send `Authorization: Bearer <JWT>`.
   - Validate token expiration (`exp` claim).
   - Validate issuer (`iss` matches `CLINDOC_OIDC_ISSUER`) and audience (`aud` matches `CLINDOC_OIDC_AUDIENCE`).
2. For 403 Forbidden:
   - Inspect user roles assigned in database:
     ```bash
     docker exec clindoc-postgres psql -U clindoc_user -d clindoc_db -c "
       SELECT u.username, r.name AS role_name
       FROM users u
       JOIN user_roles ur ON u.id = ur.user_id
       JOIN roles r ON ur.role_id = r.id;
     "
     ```
   - Required roles: `clinician` for review/approval, `admin` for policies/audit, `auditor` for read-only reports.
