# ClinDoc Air-Gapped Provisioning Guide

**Phase 8.11 — Air-Gapped / Private Deployment Operations**  
**Classification:** Operational Runbook — Read-Only Production Specification  
**Authority:** `PRODUCTIZATION_SPEC.md` §10, `PRODUCTIZATION_DECISIONS.md` Phase 8.7, Phase 8.11

---

## 1. Overview & Two-Stage Lifecycle

In an air-gapped hospital environment, production servers have **zero outbound or inbound internet connectivity**. To achieve deployment readiness, deployment media must be prepared on a secure, Internet-connected staging build workstation, cryptographically audited, and transferred via physical media or a controlled diode gateway into the isolated environment.

```text
┌───────────────────────────────────────────────┐
│  STAGE 1: INTERNET-CONNECTED BUILD MACHINE   │
│  - Export Python wheelhouse                   │
│  - Export Docker container images             │
│  - Download Qwen AWQ weights & ModernBERT pt  │
│  - Generate SHA-256 integrity manifests       │
└───────────────────────┬───────────────────────┘
                        │
                        ▼ Controlled Physical Transfer (USB / Optical / Diode)
┌───────────────────────────────────────────────┐
│    STAGE 2: AIR-GAPPED HOSPITAL PRODUCTION    │
│  - Import container images (`docker load`)    │
│  - Mount pre-staged model weights             │
│  - Initialize database schema                 │
│  - Set offline environment flags              │
│  - Zero Internet Access Runtime               │
└───────────────────────────────────────────────┘
```

---

## 2. Stage 1: Provisioning with Controlled Network Access

Perform these steps on an authenticated staging build workstation running Linux or Windows with Docker and Python 3.11 installed.

### 2.1 Export Application Container Images
Pull and archive all required production container images into a single tarball:
```bash
mkdir -p clindoc_airgap_bundle/images

# Pull base images
docker pull postgres:15-alpine
docker pull nginx:1.25-alpine
docker pull vllm/vllm-openai:v0.6.3
docker pull prom/prometheus:v2.50.1
docker pull grafana/grafana:10.3.3

# Build ClinDoc API and Worker images
docker build -t clindoc/api:production-v1.0.0 -f Dockerfile.api .
docker build -t clindoc/worker:production-v1.0.0 -f Dockerfile.worker .

# Save container images to tar archives
docker save -o clindoc_airgap_bundle/images/postgres-15.tar postgres:15-alpine
docker save -o clindoc_airgap_bundle/images/nginx-1.25.tar nginx:1.25-alpine
docker save -o clindoc_airgap_bundle/images/vllm-0.6.3.tar vllm/vllm-openai:v0.6.3
docker save -o clindoc_airgap_bundle/images/prometheus-2.50.tar prom/prometheus:v2.50.1
docker save -o clindoc_airgap_bundle/images/grafana-10.3.tar grafana/grafana:10.3.3
docker save -o clindoc_airgap_bundle/images/clindoc-api.tar clindoc/api:production-v1.0.0
docker save -o clindoc_airgap_bundle/images/clindoc-worker.tar clindoc/worker:production-v1.0.0
```

### 2.2 Pre-Download Qwen 2.5 7B AWQ Weights
Pre-populate the Hugging Face cache directory with the exact audited model snapshot:
```bash
mkdir -p clindoc_airgap_bundle/models/vllm_cache

python -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='Qwen/Qwen2.5-7B-Instruct-AWQ',
    local_dir='clindoc_airgap_bundle/models/vllm_cache/Qwen/Qwen2.5-7B-Instruct-AWQ',
    local_dir_use_symlinks=False
)
"
```

### 2.3 Package ModernBERT Qualified Checkpoint
Copy the audited, verified ModernBERT NLI checkpoint:
```bash
mkdir -p clindoc_airgap_bundle/models/checkpoints

cp data/m2_results_v3/checkpoints/modernbert_text_only_v3_fold0_seed42.pt \
   clindoc_airgap_bundle/models/checkpoints/

# Verify checksum matches audited manifest:
sha256sum clindoc_airgap_bundle/models/checkpoints/modernbert_text_only_v3_fold0_seed42.pt
# Must match: d24add6135634f660d2fdcf1c51545e201cc65c00fd2d060452eac95be9e7902
```

### 2.4 Package Deployment Configuration & Manifests
```bash
mkdir -p clindoc_airgap_bundle/deploy
cp -r deploy/* clindoc_airgap_bundle/deploy/
```

### 2.5 Generate Cryptographic SHA-256 Manifest
```bash
cd clindoc_airgap_bundle
find . -type f -exec sha256sum {} + > MANIFEST.sha256
```

---

## 3. Stage 2: Offline Installation on Air-Gapped Production Host

Transfer `clindoc_airgap_bundle` to the isolated host using approved hospital data transfer protocols (e.g. encrypted write-once optical disc or scanned secure USB drive).

### 3.1 Verify Bundle Integrity
```bash
cd /opt/clindoc_bundle
sha256sum -c MANIFEST.sha256
```

### 3.2 Load Docker Container Images
```bash
for img in images/*.tar; do
  docker load -i "$img"
done
```

### 3.3 Stage Persistent Volumes & Directory Structure
```bash
# 1. Create directory structure
mkdir -p /opt/clindoc/models/checkpoints
mkdir -p /opt/clindoc/models/cache
mkdir -p /var/lib/clindoc/blob_store

# 2. Populate model weights
cp models/checkpoints/* /opt/clindoc/models/checkpoints/
cp -r models/vllm_cache/* /opt/clindoc/models/cache/

# 3. Set strict permissions
chmod -R 755 /opt/clindoc/models
chmod -R 700 /var/lib/clindoc/blob_store
```

### 3.4 Configure Environment & Enforce Offline Invariants
Copy `deploy/.env.example` to `deploy/.env` and ensure the following flags are present:
```dotenv
CLINDOC_PROFILE=production
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
ALLOW_SAPBERT_DOWNLOAD=0
ALLOW_NLI_DOWNLOAD=0
MODERNBERT_CHECKPOINT_PATH=/opt/clindoc/models/checkpoints/modernbert_text_only_v3_fold0_seed42.pt
VLLM_BASE_URL=https://clindoc-vllm-tls-proxy:8443/v1
CLINDOC_BLOB_ROOT_DIR=/var/lib/clindoc/blob_store
```

### 3.5 Launch Services via Production Manifest
```bash
docker compose -f deploy/docker-compose.prod.yml up -d
```

### 3.6 Verify Offline Autonomy
Execute the verification probe:
```bash
curl -f http://localhost:8000/api/v1/health/ready
```
Verify that no network requests attempt to reach external IP addresses.
