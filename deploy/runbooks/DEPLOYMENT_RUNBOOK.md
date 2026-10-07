# ClinDoc Production Deployment Runbook

**Phase 8.11 — Air-Gapped / Private Deployment Operations**  
**Classification:** Operational Runbook — Read-Only Production Specification  
**Authority:** `PRODUCTIZATION_SPEC.md` §9, §10, §13, `PROJECT_SPEC.md` §12, `PRODUCTIZATION_DECISIONS.md` PD-001, PD-003, PD-004, PD-006

---

## 1. Architectural Inventory

The ClinDoc clinical document verification platform consists of the following deployment components:

### 1.1 Components Inventory & Classification

| Component | Technology | Runtime Requirement | Network Boundary | Persistent Storage |
|---|---|---|---|---|
| **ClinDoc API** | FastAPI / Uvicorn (Python 3.11) | **Required at runtime** | Hospital Ingress (8000), `app_internal`, `gateway_network`, `monitoring_internal` | Read-only models mount, certificates mount |
| **Relational Database** | PostgreSQL 15 | **Required at runtime** | `app_internal` (Port 5432) | `pg_data` volume (WAL, tables, indices) |
| **Encrypted Blob Storage** | `EncryptedFileBlobStore` (AES-256-GCM) | **Required at runtime** | Local filesystem access via API & Worker | `blob_data` volume (`/var/lib/clindoc/blob_store`) |
| **Background Worker** | Python 3.11 daemon (`clindoc.jobs.worker`) | **Required at runtime** | `app_internal`, `gateway_network`, `monitoring_internal` | Read/write blob store, read-only models |
| **ModernBERT NLI** | PyTorch checkpoint (`text_only_v3`) | **Required at runtime** | In-process execution inside Worker | Read-only volume (`/opt/clindoc/models`) |
| **Qwen 2.5 7B vLLM** | vLLM 0.6.3 (`Qwen2.5-7B-Instruct-AWQ`) | **Required at runtime** | `model_serving_internal` only (Port 8000, zero WAN) | `vllm_cache` volume (`/root/.cache/huggingface`) |
| **Nginx TLS Reverse Proxy** | Nginx 1.25 Alpine | **Required at runtime** | Bridges `gateway_network` (8443) to `model_serving_internal` (8000) | Read-only TLS certs (`/etc/ssl/certs`) |
| **Prometheus** | Prometheus v2.50.1 | **Optional operational service** | `monitoring_internal` (Scrapes API 8000), Host loopback 9090 | `prometheus_data` volume (TSDB metrics) |
| **Grafana** | Grafana 10.3.3 | **Optional operational service** | `monitoring_internal` (Queries Prometheus), Host loopback 3000 | `grafana_data` volume (dashboards & state) |

### 1.2 Classification Summary
- **Runtime Mandatory**: ClinDoc API, PostgreSQL, Encrypted Blob Storage, Background Worker, ModernBERT checkpoint, Qwen/vLLM GPU host, Nginx TLS Proxy.
- **Provisioning/Build Only**: Docker image registry, package wheelhouses, Hugging Face weight downloader, OpenSSL certificate authority generator. *None of these are permitted or required during production runtime.*
- **Operational/Monitoring (Optional)**: Prometheus and Grafana. The clinical verification cascade functions autonomously if monitoring containers are stopped.

---

## 2. Private Network Topology & Isolation

The production environment operates across four distinct Docker bridge networks or private VPC subnets:

```text
       [ Hospital Core / Ingress Network ]
                      │ (HTTPS port 8000 / Internal IP)
                      ▼
               [ ClinDoc API ]
             ┌────────┴────────┐
             │                 │
             ▼                 ▼
     [ PostgreSQL DB ]  [ Blob Store ]
             ▲                 ▲
             │                 │
             └────────┬────────┘
                      │ (Job Queue & Persistence)
                      ▼
              [ ClinDoc Worker ]
                      │
                      │ HTTPS (port 8443) over gateway_network
                      ▼
         [ vLLM TLS Reverse Proxy (Nginx) ]
                      │
                      │ HTTP (port 8000) over model_serving_internal [internal: true, no WAN]
                      ▼
            [ Qwen vLLM GPU Server ]
```

### Network Security Invariants
1. **Egress Deny (`internal: true`)**: `model_serving_internal` and `app_internal` are configured with `internal: true`. Docker does not provision a default gateway for these bridges, physically preventing container-initiated WAN traffic.
2. **Loopback Exposure for Monitoring**: Prometheus (port 9090) and Grafana (port 3000) are explicitly bound to host loopback (`127.0.0.1`), preventing exposure outside the local management console.
3. **Internal Endpoint Scrape Guard**: The `/metrics` endpoint is protected by IP range filtering (RFC 1918 and loopback only) and optional Bearer token authentication (`CLINDOC_METRICS_TOKEN`).

---

## 3. Configuration & Secrets Injection

All secrets must be supplied via environment variables or Docker secrets files. **Never commit secrets to version control or hard-code them in deployment manifests.**

### 3.1 Secrets Inventory

| Secret Name | Destination | Format / Minimum Spec | Rotation Impact |
|---|---|---|---|
| `POSTGRES_PASSWORD` | PostgreSQL & API/Worker `CLINDOC_DATABASE_URL` | $\ge 24$ alphanumeric characters | Requires updating DB and restarting API + Worker |
| `CLINDOC_BLOB_ENCRYPTION_KEY` | API & Worker | 32 raw bytes or 64 hex characters (AES-256-GCM) | **CRITICAL:** Existing document blobs cannot be read if changed without re-encryption migration |
| `VLLM_API_KEY` | vLLM container & API/Worker | $\ge 32$ random characters | Requires simultaneous restart of vLLM and API/Worker |
| `CLINDOC_METRICS_TOKEN` | API & Prometheus `prometheus.yml` | $\ge 32$ random characters | Scraper receives 401 until updated in Prometheus config |
| `TLS_PRIVATE_KEY` | Nginx reverse proxy | RSA 2048/4096 or ECDSA P-256 | Requires `nginx -s reload` |
| `GF_SECURITY_ADMIN_PASSWORD` | Grafana | $\ge 16$ alphanumeric characters | Resets admin UI credentials |

### 3.2 Secret Injection Protocol
1. Copy `deploy/.env.example` to `deploy/.env`.
2. Generate cryptographically strong secrets:
   ```bash
   # Generate AES-256 encryption key
   python -c "import secrets; print(secrets.token_hex(32))"

   # Generate vLLM API key
   python -c "import secrets; print(secrets.token_urlsafe(32))"

   # Generate DB password
   python -c "import secrets; print(secrets.token_urlsafe(24))"
   ```
3. Set file permissions on `.env` to read-only for the service owner (`chmod 600 deploy/.env`).

---

## 4. TLS & Certificate Operations

### 4.1 Certificate Architecture
- **Inbound Ingress**: If terminated by the hospital edge (e.g. F5, Envoy, ingress controller), internal traffic routes to `clindoc-api:8000`.
- **vLLM Inference Channel**: Traffic between ClinDoc API/Worker and the vLLM serving container traverses Nginx via TLS 1.2/1.3 on port 8443.
- **Locations**:
  - Certificate file: `/etc/ssl/certs/server.crt` (mounted from host `./deploy/vllm/certs/server.crt`)
  - Private key file: `/etc/ssl/certs/server.key` (mounted from host `./deploy/vllm/certs/server.key`)
  - CA Root (if enterprise CA): `/etc/ssl/certs/ca.crt`

### 4.2 Certificate Replacement Procedure
1. Place new PEM-encoded certificate and private key in `./deploy/vllm/certs/`.
2. Verify permissions (`chmod 644 server.crt`, `chmod 600 server.key`).
3. Validate certificate and key match:
   ```bash
   openssl x509 -noout -modulus -in deploy/vllm/certs/server.crt | openssl md5
   openssl rsa -noout -modulus -in deploy/vllm/certs/server.key | openssl md5
   ```
4. Reload Nginx without downtime:
   ```bash
   docker exec clindoc-vllm-tls-proxy nginx -s reload
   ```
5. Test endpoint availability and TLS handshake:
   ```bash
   openssl s_client -connect localhost:8443 -servername clindoc-vllm-tls-proxy </dev/null
   ```

### 4.3 Certificate Expiry Monitoring
- Alerting rule `ClinDocTLSCertExpiringSoon` triggers when certificate validity falls below 30 days.
- Regular cron inspection:
  ```bash
  openssl x509 -enddate -noout -in deploy/vllm/certs/server.crt
  ```
