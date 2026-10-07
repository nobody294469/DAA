# ClinDoc Startup, Shutdown, and Health-Check Runbook

**Phase 8.11 — Air-Gapped / Private Deployment Operations**  
**Classification:** Operational Runbook — Read-Only Production Specification  
**Authority:** `PRODUCTIZATION_SPEC.md` §9.2, §10, `PRODUCTIZATION_DECISIONS.md` Phase 8.7 Step 3

---

## 1. Startup Procedure (Dependency Order)

Starting the ClinDoc platform must strictly follow the architectural dependency sequence to ensure databases and model inference nodes are healthy before application workers begin polling jobs.

```text
Step 1: Storage & Volumes Check
      ↓
Step 2: PostgreSQL Database
      ↓ (pg_isready verification)
Step 3: vLLM GPU Host
      ↓ (Local healthcheck OK)
Step 4: vLLM TLS Proxy
      ↓ (TLS port 8443 probe OK)
Step 5: ClinDoc API Server
      ↓ (/health/ready returns 200)
Step 6: ClinDoc Worker Daemon
      ↓ (Queue lease polling active)
Step 7: Observability (Prometheus / Grafana)
```

### Detailed Execution Steps

#### Step 1: Storage and Volume Verification
Verify persistent storage mount points exist, have appropriate permissions, and contain the required models and encryption keys:
```bash
# Verify model artifacts exist
ls -lh /opt/clindoc/models/checkpoints/modernbert_text_only_v3_fold0_seed42.pt
ls -lh /opt/clindoc/models/cache/

# Verify blob storage root permissions
mkdir -p /var/lib/clindoc/blob_store
chmod 700 /var/lib/clindoc/blob_store
```

#### Step 2: Start PostgreSQL Database
```bash
docker compose -f deploy/docker-compose.prod.yml up -d postgres

# Verify readiness:
docker exec clindoc-postgres pg_isready -U clindoc_user -d clindoc_db
# Expected output: "/var/run/postgresql:5432 - accepting connections"
```

#### Step 3: Start vLLM Model Serving Container
```bash
docker compose -f deploy/docker-compose.prod.yml up -d vllm

# Monitor startup logs and weight initialization:
docker logs -f clindoc-vllm-production
# Look for: "Application startup complete. Uvicorn running on http://0.0.0.0:8000"
```

#### Step 4: Start vLLM TLS Reverse Proxy
```bash
docker compose -f deploy/docker-compose.prod.yml up -d vllm-tls-proxy

# Verify proxy health probe:
curl -k -f https://localhost:8443/health
# Expected: HTTP 200 OK
```

#### Step 5: Start ClinDoc API Server
```bash
docker compose -f deploy/docker-compose.prod.yml up -d api

# Verify API readiness probe:
curl -f http://localhost:8000/api/v1/health/ready
# Expected response: {"status": "ready", "database": "connected", ...}
```

#### Step 6: Start ClinDoc Background Processing Worker
```bash
docker compose -f deploy/docker-compose.prod.yml up -d worker

# Verify worker process:
docker logs clindoc-worker --tail 20
```

#### Step 7: Start Observability Services (Optional)
```bash
docker compose -f deploy/docker-compose.prod.yml up -d prometheus grafana
```

---

## 2. Safe Shutdown Procedure

To avoid data corruption, orphaned document leases, or incomplete FHIR exports, shut down the platform in reverse dependency order with a graceful worker drain period.

### Execution Steps

#### Step 1: Drain Inbound Traffic
Disable inbound document ingestion at the hospital ingress load balancer. Ensure no new `POST /api/v1/documents/ingest` requests enter the system.

#### Step 2: Gracefully Stop Background Worker
Allow the worker to finish in-flight document processing jobs:
```bash
# Send SIGTERM to the worker container (grants 30s graceful timeout)
docker stop --time 30 clindoc-worker

# Check for zero active worker processes:
docker ps | grep clindoc-worker
```
*Note on Incomplete Jobs:* Any job that does not finish within the lease timeout (`CLINDOC_JOB_LEASE_SECONDS=300`) will safely remain in `PENDING` status for subsequent reclamation upon restart.

#### Step 3: Stop ClinDoc API Server
```bash
docker stop clindoc-api
```

#### Step 4: Stop vLLM & TLS Proxy
```bash
docker stop clindoc-vllm-tls-proxy
docker stop clindoc-vllm-production
```

#### Step 5: Stop PostgreSQL Database (Flush Buffers)
```bash
docker stop clindoc-postgres
```

#### Step 6: Stop Observability Stack
```bash
docker stop clindoc-grafana clindoc-prometheus
```

---

## 3. Health & Readiness Signals Reference

The ClinDoc API exposes standardized health endpoints adhering to Phase 8.7 Step 3 specifications.

### 3.1 `GET /api/v1/health` (Liveness Probe)
- **Purpose**: Verifies that the FastAPI process is responsive and able to handle HTTP events.
- **Payload**:
  ```json
  {
    "status": "healthy",
    "timestamp": "2026-10-06T15:30:00Z"
  }
  ```
- **Operator Meaning**: If this fails (non-200 or timeout), the container has crashed, deadlocked, or run out of memory. Container orchestrator should restart the container.

### 3.2 `GET /api/v1/health/ready` (Readiness Probe)
- **Purpose**: Verifies that all underlying dependencies (database, ModernBERT, Qwen/vLLM) are capable of processing documents.
- **Payload Example**:
  ```json
  {
    "status": "ready",
    "database": "connected",
    "storage": "accessible",
    "modernbert": "ready",
    "qwen_vllm": "ready",
    "circuit_breaker": "closed",
    "timestamp": "2026-10-06T15:30:00Z"
  }
  ```
- **Semantics & Failure Behavior**:
  - `database != "connected"`: **503 Service Unavailable**. Processing cannot occur.
  - `modernbert != "ready"`: **503 Service Unavailable**. Stage B verification is impossible; worker refuses jobs.
  - `qwen_vllm == "temporarily_unavailable"`: **200 OK (Degraded / Warning)**. In accordance with Phase 8.7 §3 and Phase 8.10 §11, temporary unavailability of Qwen does **not** fail the application readiness probe, because Stage A and Stage B verifications continue processing autonomously, and escalated claims are routed safely to human review fallback (`llm_unavailable`).
  - `circuit_breaker == "open"`: **200 OK (Degraded)**. Qwen circuit breaker is tripped; fast-fallback to human review is active.

### 3.3 `GET /metrics` (Prometheus Metrics Scrape)
- **Purpose**: Exposes low-cardinality Prometheus metrics for scraper consumption.
- **Access Policy**: Restricted to internal monitoring network (RFC 1918) and optional Bearer token header (`Authorization: Bearer <token>`).
- **Operator Meaning**: Rejection with 403 indicates foreign IP outside internal subnet; rejection with 401 indicates missing or invalid metrics bearer token.
