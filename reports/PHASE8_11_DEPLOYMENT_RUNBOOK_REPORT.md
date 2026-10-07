# Phase 8.11 — Air-Gapped / Private Deployment & Operational Runbooks Report

## 1. Executive Summary & Files Created/Changed

Phase 8.11 prepares the ClinDoc clinical document verification platform for air-gapped, on-premises, and private-cloud hospital deployments without public internet dependencies. The phase establishes reproducible deployment manifests, production environment configuration templates with zero hardcoded credentials, TLS operational lifecycle standards, backup and disaster recovery procedures, and comprehensive operator runbooks.

### Files Created
- `deploy/.env.example`: Complete, documented environment configuration template covering database, blob encryption, model serving, OIDC identity, CORS, CSP, and metrics security.
- `deploy/docker-compose.prod.yml`: Production multi-container composition defining isolated internal bridge networks (`internal: true`), GPU model host isolation, loopback-restricted monitoring, and persistent volume definitions.
- `deploy/runbooks/DEPLOYMENT_RUNBOOK.md`: Component classification, network architecture, secrets injection protocols, and TLS operational procedures.
- `deploy/runbooks/STARTUP_SHUTDOWN_RUNBOOK.md`: Strict dependency-ordered startup, graceful shutdown with worker lease draining, and health/readiness probe semantic reference.
- `deploy/runbooks/BACKUP_RESTORE_RUNBOOK.md`: Step-by-step procedure for PostgreSQL dumps, AES-256-GCM blob snapshots, encryption key dependency documentation, and post-restore integrity checks.
- `deploy/runbooks/DISASTER_RECOVERY_RUNBOOK.md`: Documented failure handling procedures for 13 operational scenarios with symptoms, signals, recovery actions, and clinical processing impact.
- `deploy/runbooks/TROUBLESHOOTING_RUNBOOK.md`: Diagnostic workflows covering container logs, queue backlog recovery, circuit breaker trips, CUDA OOM mitigation, and ingestion rejections.
- `deploy/runbooks/AIR_GAPPED_PROVISIONING.md`: Staging-to-production transfer protocol separating internet-connected build preparation from offline installation and air-gapped runtime enforcement.
- `tests/test_airgap_deployment_runbooks.py`: Automated validation suite testing file existence, secret placeholders, network port isolation, offline runtime code invariants, and runbook cross-references.
- `reports/PHASE8_11_DEPLOYMENT_RUNBOOK_REPORT.md`: This comprehensive verification report.

### Files Modified
- `deploy/vllm/docker-compose.yml`: Added offline runtime environment flags (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`).

---

## 2. Existing Architecture Audited

The deployment inventory across Phases 8.7–8.10 was audited and classified into three operational categories:

| Component | Technology | Runtime Requirement | Network Isolation | Storage / State |
|---|---|---|---|---|
| **ClinDoc API** | FastAPI / Uvicorn (Python 3.11) | **Required at runtime** | Ingress 8000, private bridges (`app_internal`, `gateway_network`, `monitoring_internal`) | Ephemeral container; read-only models mount |
| **Relational Database** | PostgreSQL 15 | **Required at runtime** | `app_internal` (Port 5432) | `pg_data` volume |
| **Encrypted Blob Storage** | `EncryptedFileBlobStore` (AES-256-GCM) | **Required at runtime** | Local filesystem access via API & Worker | `blob_data` volume (`/var/lib/clindoc/blob_store`) |
| **Background Worker** | Python 3.11 daemon (`clindoc.jobs.worker`) | **Required at runtime** | `app_internal`, `gateway_network`, `monitoring_internal` | Reads/writes blob store and database |
| **ModernBERT NLI** | PyTorch checkpoint (`text_only_v3`) | **Required at runtime** | In-process execution within Worker | Read-only checkpoint volume |
| **Qwen 2.5 7B vLLM** | vLLM 0.6.3 (`Qwen2.5-7B-Instruct-AWQ`) | **Required at runtime** | `model_serving_internal` only (`internal: true`, zero WAN) | `vllm_cache` volume (`/root/.cache/huggingface`) |
| **Nginx TLS Reverse Proxy** | Nginx 1.25 Alpine | **Required at runtime** | Bridges `gateway_network` (8443) to `model_serving_internal` (8000) | Read-only TLS certificates |
| **Prometheus** | Prometheus v2.50.1 | **Optional operational service** | `monitoring_internal`, bound to loopback `127.0.0.1:9090` | `prometheus_data` volume |
| **Grafana** | Grafana 10.3.3 | **Optional operational service** | `monitoring_internal`, bound to loopback `127.0.0.1:3000` | `grafana_data` volume |

---

## 3. Air-Gapped & No-Internet Runtime Readiness

### Runtime Invariants
1. **Zero Runtime Package Downloads**: Production containers execute from pre-built images with all dependencies baked in; no `pip`, `uv`, or `apt` invocations occur during execution.
2. **Offline Hugging Face Execution**: Enforced via `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` in API, Worker, and vLLM environments.
3. **Disabled Model Downloads in Code**:
   - `SapBERTEmbedder` defaults to `ALLOW_SAPBERT_DOWNLOAD=0`.
   - `PubMedBERTNLIPredictor` defaults to `ALLOW_NLI_DOWNLOAD=0`.
   - `ProductionPipelineConfig.validate()` fails closed on startup if the ModernBERT checkpoint is missing or empty, strictly forbidding fallback to remote download.
4. **Physical Egress Denial**:
   - The GPU serving network (`model_serving_internal`) and application database network (`app_internal`) specify `internal: true`. Docker drops default gateway routing on these bridges, physically preventing outbound Internet requests.
5. **No Public Ingress for Model or Metrics**:
   - vLLM binds port 8000 only within `model_serving_internal`; no host ports are mapped.
   - Prometheus and Grafana are bound to `127.0.0.1`.
   - `/metrics` requires internal IP origin and optional Bearer token authorization.

---

## 4. Secret & Configuration Handling

### Audited Secrets & Vault Protocol
1. **No Committed Secrets**: Scanned version control and deployment directories. Verified zero hardcoded passwords, tokens, API keys, or private keys.
2. **Mandatory Runtime Variables**:
   - `CLINDOC_DATABASE_URL`: Injected via environment or Docker secret.
   - `CLINDOC_BLOB_ENCRYPTION_KEY`: Mandatory 32-byte (256-bit) secret for AES-GCM envelope encryption.
   - `VLLM_API_KEY`: Injected into both vLLM serving container (`--api-key`) and ClinDoc API/Worker.
   - `CLINDOC_METRICS_TOKEN`: Optional Bearer token securing Prometheus scrapes.
   - `TLS_PRIVATE_KEY`: Read-only file mount (`chmod 600`).
3. **Template Safety**: `deploy/.env.example` contains explicit `CHANGE_ME_*` placeholders with instructions and zero default credentials. `.gitignore` strictly excludes `.env` and `*.key`/`*.pem` files.

---

## 5. TLS Operational Handling

### Operations Summary
- **Mutual Isolation**: The Nginx TLS reverse proxy bridges the application gateway network and the isolated GPU host network.
- **Strict Protocol Standards**: Enforces TLS 1.2 and TLS 1.3 with modern forward-secret cipher suites (`HIGH:!aNULL:!MD5:!3DES:!RC4`).
- **Certificate Renewal Workflow**: New certificates placed in `deploy/vllm/certs/` can be loaded dynamically via `docker exec clindoc-vllm-tls-proxy nginx -s reload` with zero downtime.
- **Monitoring**: Alert rule `ClinDocTLSCertExpiringSoon` warns administrators when certificate validity falls below 30 days.

---

## 6. Backup & Restore Procedures

### Persistence Architecture & Key Dependency
- **PostgreSQL**: Logical point-in-time dumps using `pg_dump -F c`.
- **Blob Storage**: File-level snapshots of `/var/lib/clindoc/blob_store`.
- **Encryption Key Criticality**: The runbook prominently highlights that encrypted document blobs are permanently unrecoverable without `CLINDOC_BLOB_ENCRYPTION_KEY`.
- **Restore Ordering**:
  1. Restore and verify `.env` configuration and encryption key.
  2. Mount and verify model checkpoints.
  3. Extract blob storage filesystem archive.
  4. Restore PostgreSQL database dump (`pg_restore --clean`).
  5. Execute automated post-restore verification: probe `/health/ready`, check document count, and test blob decryption.

---

## 7. Disaster Recovery Procedures

Documented in `deploy/runbooks/DISASTER_RECOVERY_RUNBOOK.md` across 13 operational failure scenarios:

| # | Scenario | Health/Alert Signal | Immediate Action | Clinical Processing Impact |
|---|---|---|---|---|
| 1 | ClinDoc API Unavailable | `probe_success == 0` on 8000; `ClinDocAppNotReady` | Restart container, check OOM | **STOPPED** |
| 2 | Worker Unavailable | `clindoc_jobs_in_progress == 0` with backlog | Restart worker, reclaim leases | **TEMPORARILY DELAYED** |
| 3 | PostgreSQL Unavailable | `database: disconnected`; 503 error | Restart DB, check WAL/disk | **STOPPED** |
| 4 | Blob Storage Unavailable | `blob_security_rejection` spike | Verify mount permissions & key | **STOPPED** |
| 5 | ModernBERT Unavailable | `modernbert: not_ready`; `ClinDocModernBertUnavailable` | Verify checkpoint hash; restart | **STOPPED FOR NLI** |
| 6 | Qwen/vLLM Unavailable | `qwen_vllm: unavailable`; `ClinDocSustainedQwenOutage` | Restart GPU container | **PARTIAL CONTINUATION** (Stage A/B active; C routes to review fallback) |
| 7 | Qwen Circuit Breaker OPEN | `clindoc_circuit_breaker_state == 1` | Wait cooldown (30s) or restart | **PARTIAL CONTINUATION** (Fast-fallback to review) |
| 8 | TLS Certificate Expiry | 401/SSLError spikes on inference | Deploy renewed cert; reload Nginx | **PARTIAL CONTINUATION** (Stage A/B active; C routes to review) |
| 9 | Prometheus Unavailable | Scrape timeout; missing metrics | Restart Prometheus container | **NONE** (Core clinical processing unaffected) |
| 10 | Grafana Unavailable | Dashboard connection refused | Restart Grafana container | **NONE** (Dashboards decoupled from pipeline) |
| 11 | Quarantined Document | `clindoc_documents_total{quarantined}` | Inspect audit log, reject malformed | **ISOLATED TO DOCUMENT** |
| 12 | Failed FHIR Export | 422 Unprocessable Entity | Adjudicate unresolved claims/approval | **SAFE CONTAINMENT** (Unapproved documents blocked from EHR) |
| 13 | Disk Failure (Full Restore) | Database/filesystem crash loop | Execute `BACKUP_RESTORE_RUNBOOK.md` | **HALTED DURING RESTORE; RESUMED AT POINT-IN-TIME** |

---

## 8. Startup, Shutdown, and Health-Check Procedures

### Startup Order
1. Storage volumes and model checkpoint verification.
2. PostgreSQL database startup (`pg_isready` check).
3. vLLM GPU container startup (wait for model loading).
4. vLLM TLS reverse proxy startup (`/health` probe).
5. ClinDoc API backend startup (`/health/ready` check).
6. Background Worker daemon startup (queue lease polling).
7. Observability stack startup (Prometheus & Grafana).

### Shutdown Order
1. Ingress traffic drain (disable ingestion).
2. Graceful worker termination (allow in-flight jobs to complete or lease timeout).
3. Stop API server.
4. Stop vLLM & TLS proxy.
5. Stop PostgreSQL database (clean buffer flush).
6. Stop observability services.

---

## 9. Air-Gapped Provisioning Procedure

Documented in `deploy/runbooks/AIR_GAPPED_PROVISIONING.md`:
- **Stage 1 (Connected Staging Workstation)**:
  - Export all container images via `docker save`.
  - Snapshot `Qwen/Qwen2.5-7B-Instruct-AWQ` weights into offline cache.
  - Package verified ModernBERT checkpoint (`d24add6135634f660d2fdcf1c51545e201cc65c00fd2d060452eac95be9e7902`).
  - Generate cryptographic `MANIFEST.sha256`.
- **Stage 2 (Air-Gapped Production Host)**:
  - Transfer via physical media (optical disc / scanned secure USB).
  - Checksum verification against manifest.
  - Image loading via `docker load`.
  - Populate persistent volumes and enforce strict offline flags.

---

## 10. Automated Validation & Test Results

### Focused Test Suite (`tests/test_airgap_deployment_runbooks.py`)
Executed command: `.venv\Scripts\pytest.exe tests/test_airgap_deployment_runbooks.py -v`

| Test Case | Scope | Result |
|---|---|---|
| `test_deployment_and_runbook_files_exist` | Confirms all 14 manifests and runbooks exist and are non-empty | PASSED |
| `test_env_examples_contain_zero_real_secrets` | Confirms `.env.example` files use safe placeholders | PASSED |
| `test_no_private_keys_or_real_secrets_in_runbooks` | Scans runbooks for unredacted private keys and credentials | PASSED |
| `test_docker_compose_prod_network_and_port_isolation` | Confirms vLLM & DB have no host ports; monitoring is loopback-only | PASSED |
| `test_vllm_docker_compose_offline_and_isolated` | Confirms vLLM egress denial and offline flags | PASSED |
| `test_codebase_defaults_forbid_runtime_downloads` | Confirms SapBERT and NLI code paths default to offline | PASSED |
| `test_production_profile_fails_closed_without_local_checkpoint` | Confirms production configuration fails closed without local weights | PASSED |
| `test_runbooks_reference_audited_constants` | Confirms runbooks reference audited SHA-256 hashes and real endpoints | PASSED |
| `test_gitignore_protects_env_and_keys` | Confirms `.gitignore` protects `.env` and key files | PASSED |

**Result: 9 passed in 18.13s.**

### Full Regression Suite
Executed command: `.venv\Scripts\pytest.exe tests/test_observability.py tests/test_security_adversarial.py tests/test_fhir_export.py tests/test_circuit_breaker.py tests/test_model_health.py tests/test_structured_fallback_worker.py tests/test_verification_cascade.py -v`

- `tests/test_observability.py` (Phase 8.10): 15/15 PASSED
- `tests/test_security_adversarial.py` (Phase 8.9): 28/28 PASSED
- `tests/test_fhir_export.py` (Phase 8.8): 20/20 PASSED
- `tests/test_circuit_breaker.py` (Phase 8.7): 29/29 PASSED
- `tests/test_model_health.py` (Phase 8.7): 21/21 PASSED
- `tests/test_structured_fallback_worker.py`: 45/45 PASSED
- `tests/test_verification_cascade.py`: 21/21 PASSED

**Result: 179 passed in 34.15s.**  
**Combined Total: 188 passed, 0 failed.**

---

## 11. Infrastructure Limitations & Items Not Runtime-Validated

Per operating instructions, the following operational aspects could not be executed directly in this local development environment and must be validated in hospital staging infrastructure:
1. **Physical Air-Gap Disconnection**: A physical network disconnect drill was not executed; offline behavior was validated via configuration inspection, environment flags, and code invariant tests.
2. **Multi-Container Live Docker Stack**: While manifests were statically validated, Docker Compose YAML parsed, and individual services tested, a live 7-container Docker daemon stack was not spun up locally.
3. **Disaster Recovery Drill / Live Database Restore**: A live PostgreSQL catastrophic crash and restore drill was not run against an enterprise database cluster; procedures are documented based on PostgreSQL and filesystem semantics.
4. **Hospital HSM / Enterprise OIDC Provider**: Real integration with hospital hardware security modules (HSM) or enterprise Keycloak/Okta identity servers was not executed.

---

## 12. Authoritative Specifications & UI Confirmation

- `PROJECT_SPEC.md`: Untouched (read-only).
- `PRODUCTIZATION_SPEC.md`: Untouched (read-only).
- `PRODUCTIZATION_DECISIONS.md`: Untouched (read-only).
- `src/clindoc/ui/`: Untouched.

---

## 13. Git Status Confirmation

- `git status` confirms:
  - No commits created.
  - Nothing staged (`no changes added to commit`).
  - Nothing pushed to remote repository.

---

## 14. Final Determination

**Phase 8.11 — Air-Gapped / Private Deployment & Operational Runbooks**: **PASS**
*(Subject to operational staging validation of containerized Docker daemon on target hospital infrastructure)*.
