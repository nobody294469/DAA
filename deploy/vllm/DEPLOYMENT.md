# Secure vLLM Model Serving Deployment Architecture

**Phase 8.7 — Step 4: Secure Model Serving Infrastructure**  
**Authority:** `PRODUCTIZATION_SPEC.md` §10, §13 (Failures F-05, F-06), `DEC-006`, `DEC-013`

---

## 1. Development vs. Production Topology

### Development Topology (Experimental / Non-Production)
During research, prototyping, and benchmark qualification:
```
Workstation/Laptop (RTX 3050 6GB)
       ↓ (Public WAN tunnel via ngrok)
Google Colab GPU (Tesla T4 16GB)
       ↓ (Unauthenticated localhost vLLM)
Qwen/Qwen2.5-7B-Instruct-AWQ
```
- **Boundary Warning:** The Google Colab + ngrok topology was used strictly for offline synthetic benchmark evaluations. It is **forbidden** for real patient records or production deployments (`PRODUCTIZATION_SPEC.md` §10.4).

### Production Reference Topology
In production, all model serving is moved inside the organization's private trust boundary:
```
Hospital / Private Infrastructure
       ↓ Internal Network
Authenticated AI Gateway / ClinDoc Backend
       ↓ Strict TLS (HTTPS port 8443)
TLS Termination Reverse Proxy (Nginx)
       ↓ Isolated Internal Bridge (No Host Ports, No WAN)
Dedicated GPU Host / Container (vLLM)
       ↓ In-Memory Execution
Qwen/Qwen2.5-7B-Instruct-AWQ
```
- **No Public Tunnels:** Neither ngrok nor any third-party public tunneling service is used in production.
- **Provider Decoupling:** The ClinDoc backend (`VLLMJudgeClient` and `VLLMProvider`) connects to `vllm_base_url` over standard HTTP/HTTPS. It remains completely decoupled from whether vLLM is executed on bare metal, containerized on-premises, or in a private cloud VPC.

---

## 2. Infrastructure Requirements & Options

### GPU Hardware Sizing
- **Quantized Architecture:** `Qwen/Qwen2.5-7B-Instruct-AWQ` utilizes 4-bit Activation-aware Weight Quantization (AWQ).
- **VRAM Sizing:** The model requires ~5.5 GB VRAM for model weights plus ~3–5 GB for vLLM KV-cache paging at `max_model_len=4096`.
- **Reference Evidence:** Audited benchmark evaluations executed on an NVIDIA Tesla T4 (16 GB VRAM). Any modern enterprise CUDA-compatible GPU with $\ge 16\text{ GB}$ VRAM (e.g., NVIDIA T4, A10G, L4, A30, or RTX 4090/6000) provides sufficient memory.
- **CPU & System Memory:** Host requires $\ge 4$ CPU cores and $\ge 16\text{ GB}$ host RAM.

### Hospital Deployment Models
The platform does **not** mandate that every hospital purchase on-premises GPU hardware:
1. **Option A: Hospital On-Premises GPU Server**
   - The hospital IT department provisions an on-premise GPU server located within its internal data center.
   - Container manifests in `deploy/vllm/docker-compose.yml` run directly on the local server.
2. **Option B: Dedicated Private Cloud VPC**
   - The hospital or healthcare provider deploys the GPU serving node inside a dedicated, isolated Virtual Private Cloud (VPC) located in the appropriate national data jurisdiction (e.g., AWS, Azure, GCP, or sovereign private cloud).
   - Connected to the hospital core network via private interconnect (Direct Connect / ExpressRoute / site-to-site IPsec VPN).
   - **No Public IP:** The GPU host receives only a private IP address within the private subnet.

In both models, the application-level API contract (`VLLM_BASE_URL` and `VLLM_API_KEY`) remains byte-for-byte identical.

---

## 3. Security & Isolation Controls

### 3.1 Network Boundary & Egress Denial
- **Default Egress Deny:** The `vllm` container is attached to an internal bridge network (`model_serving_internal`) configured with `internal: true`.
- **Routing Invariant:** Docker drops default gateway routing on `internal: true` networks. The container cannot initiate outbound connections to the Internet.
- **Zero Host Port Binding:** The vLLM container binds port 8000 only inside `model_serving_internal`. Only the adjacent TLS proxy container has access to this port.

### 3.2 Authentication & Secret Injection
- **Inference Authentication:** vLLM is launched with `--api-key ${VLLM_API_KEY}`. All `/v1/chat/completions` calls require `Authorization: Bearer <token>`.
- **No Hard-coded Secrets:** No keys, tokens, or passwords exist in Dockerfiles, Compose files, or committed manifests.
- **Injection:** In production, `${VLLM_API_KEY}` is injected at runtime via environment variables, container secrets, or an enterprise secret manager (e.g., HashiCorp Vault).

### 3.3 TLS Termination
- Traffic between the AI Gateway / ClinDoc Backend and the serving node is encrypted over TLS (HTTPS).
- The reference proxy (`tls-proxy`) enforces TLS 1.2 and TLS 1.3 with strict modern cipher suites (`HIGH:!aNULL:!MD5:!3DES:!RC4`).
- Certificates (`server.crt` and `server.key`) are mounted read-only into `/etc/ssl/certs/`. Real certificates are never committed to version control.
- ClinDoc client connections maintain strict certificate validation (`verify=True`).

### 3.4 Request & Response Privacy Logging
- **`--disable-log-requests`:** The vLLM serving container is started with `--disable-log-requests`. This prevents raw clinical prompt text and structured completion JSON from being written to stdout/stderr container logs.
- **Scrubbed Proxy Logs:** The Nginx reverse proxy logs only timestamp, HTTP method, URL path, and response code. Request and response bodies are excluded from proxy logs.
- *Notice:* While disabling request logs mitigates accidental credential and PHI exposure in logs, it does not by itself confer statutory compliance (HIPAA, GDPR, DPDP); operational procedures and data agreements must be established independently.

### 3.5 Model Persistence & Artifact Caching
- **Persistent Volume:** A dedicated volume (`vllm_cache`) is mounted at `/root/.cache/huggingface`.
- Once the model weights are populated into the cache volume, container restarts and node reboots read directly from local disk storage without re-downloading across the network.
- In fully air-gapped environments, weights are pre-loaded into `/root/.cache/huggingface` before network isolation is engaged.

---

## 4. Health & Readiness Integration

The deployment natively integrates with the **Phase 8.7 Step 3** health system:
- **Liveness & Readiness Endpoint:** The `/health` endpoint is exposed via the proxy on port 8443 (and internally on port 8000).
- **Non-Inference Probing:** `GET /health` tests HTTP server and model engine readiness without invoking autoregressive token generation (`/v1/chat/completions`).
- **No Token Required for Health:** Per the Step 3 design, `/health` is reachable inside the private network without an API key, allowing lightweight infrastructure liveness probes (Docker healthcheck, load balancer health checks, and ClinDoc `/health/ready` probe) to execute without distributing or validating inference bearer tokens.
- **No Sensitive Leakage:** Health responses contain zero model credentials, paths, or patient data.

---

## 5. Deployment Commands & Verification

### Initial Setup
1. Copy the environment template:
   ```bash
   cp deploy/vllm/.env.example deploy/vllm/.env
   ```
2. Populate `VLLM_API_KEY` in `deploy/vllm/.env` with a strong random secret.
3. Place TLS certificates in `deploy/vllm/certs/server.crt` and `deploy/vllm/certs/server.key`.

### Starting the Serving Node
```bash
docker compose -f deploy/vllm/docker-compose.yml --env-file deploy/vllm/.env up -d
```

### Checking Status
```bash
# Check container status and health
docker compose -f deploy/vllm/docker-compose.yml ps

# Verify unauthenticated health probe
curl -k https://localhost:8443/health

# Verify authenticated inference endpoint
curl -k https://localhost:8443/v1/models -H "Authorization: Bearer $VLLM_API_KEY"
```
