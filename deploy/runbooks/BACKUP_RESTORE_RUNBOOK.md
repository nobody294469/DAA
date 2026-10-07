# ClinDoc Backup & Restore Runbook

**Phase 8.11 — Air-Gapped / Private Deployment Operations**  
**Classification:** Operational Runbook — Read-Only Production Specification  
**Authority:** `PRODUCTIZATION_SPEC.md` §3.6, §4, §9.2, `PRODUCTIZATION_DECISIONS.md` PD-001, PD-007

---

## 1. Persistence Model Overview

The ClinDoc platform manages state across four decoupled storage tiers:

1. **Relational Database (PostgreSQL)**:
   - Stores documents metadata, processing jobs, claims, evidence spans, verification results, audit events, triage policies, review actions, and FHIR export records.
2. **Encrypted Blob Storage (`EncryptedFileBlobStore`)**:
   - Stores the raw ingested clinical documents (PDF, TIFF, PNG, DocumentText JSON).
   - All files are encrypted at rest using AES-256-GCM envelope encryption.
3. **Application Secrets & Configuration**:
   - Environment variables (`.env`), TLS private keys, and specifically `CLINDOC_BLOB_ENCRYPTION_KEY`.
4. **Model Checkpoints**:
   - Immutable weights for ModernBERT (`.pt`) and Qwen 2.5 7B AWQ model cache.

> [!CAUTION]
> **CRITICAL ENCRYPTION-KEY DEPENDENCY**  
> Encrypted document blobs **CANNOT** be decrypted or restored without the exact 32-byte secret key defined in `CLINDOC_BLOB_ENCRYPTION_KEY`. If the key is lost, all raw document payloads are permanently irrecoverable, even if the blob storage disk backup is 100% intact. Store this key in an enterprise vault (e.g., HashiCorp Vault, hardware security module, or secure off-site key escrow).

---

## 2. Backup Procedure

Backups should be executed during scheduled operational windows or via automated cron jobs. To ensure cross-system referential consistency, back up the encrypted blob storage immediately before or after the database transaction dump.

### 2.1 Backup Execution Steps

#### Step 1: Export Application Secrets & Configuration
Ensure current configuration files and encryption keys are securely archived to the backup target:
```bash
BACKUP_DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/mnt/backups/clindoc/${BACKUP_DATE}"
mkdir -p "${BACKUP_DIR}/config"

# Archive configuration (ensure strict permissions)
cp deploy/.env "${BACKUP_DIR}/config/clindoc.env"
cp -r deploy/vllm/certs "${BACKUP_DIR}/config/certs"
chmod 600 "${BACKUP_DIR}/config/clindoc.env"
```

#### Step 2: Snapshot Encrypted Blob Storage
Archive the encrypted blob files. Because blobs are immutable write-once files keyed by SHA-256/UUID, `rsync` or `tar` captures consistent snapshots:
```bash
mkdir -p "${BACKUP_DIR}/blobs"
tar -czf "${BACKUP_DIR}/blobs/blob_store_${BACKUP_DATE}.tar.gz" -C /var/lib/clindoc blob_store
```

#### Step 3: Dump PostgreSQL Relational Database
Create a point-in-time PostgreSQL dump using `pg_dump` with custom or directory format:
```bash
docker exec clindoc-postgres pg_dump \
  -U clindoc_user \
  -d clindoc_db \
  -F c \
  -f "/tmp/clindoc_db_${BACKUP_DATE}.dump"

docker cp "clindoc-postgres:/tmp/clindoc_db_${BACKUP_DATE}.dump" "${BACKUP_DIR}/clindoc_db_${BACKUP_DATE}.dump"
docker exec clindoc-postgres rm "/tmp/clindoc_db_${BACKUP_DATE}.dump"
```

#### Step 4: Verify Backup Artifacts & Checksums
Generate SHA-256 manifests for the backup package:
```bash
cd "${BACKUP_DIR}" && sha256sum * */* > SHA256SUMS
```

---

## 3. Restore Procedure

In the event of hardware failure, disk corruption, or disaster recovery, follow this strict restore sequence.

### 3.1 Restore Dependency Sequence
```text
1. Configuration & Key Verification
         ↓
2. Model Checkpoints Mount
         ↓
3. Encrypted Blob Storage Filesystem
         ↓
4. PostgreSQL Database Restore
         ↓
5. Post-Restore Integrity Verification
```

### 3.2 Step-by-Step Restore Execution

#### Step 1: Restore Configuration and Verify Encryption Key
Restore `.env` and verify that `CLINDOC_BLOB_ENCRYPTION_KEY` matches the original deployment:
```bash
RESTORE_SOURCE="/mnt/backups/clindoc/20261006_153000"

cp "${RESTORE_SOURCE}/config/clindoc.env" deploy/.env
cp -r "${RESTORE_SOURCE}/config/certs" deploy/vllm/certs
chmod 600 deploy/.env

# Validate encryption key syntax (must be 32 raw bytes or 64 hex chars):
python -c "
import os, sys
from clindoc.storage.blob import EncryptedFileBlobStore
store = EncryptedFileBlobStore(root_dir='/tmp/test_key', encryption_key=b'1'*32)
print('Key validation module functional.')
"
```

#### Step 2: Restore Encrypted Blob Storage
Extract the blob archive into the designated storage volume:
```bash
mkdir -p /var/lib/clindoc
tar -xzf "${RESTORE_SOURCE}/blobs/blob_store_*.tar.gz" -C /var/lib/clindoc/
chown -R 1000:1000 /var/lib/clindoc/blob_store
chmod -R 700 /var/lib/clindoc/blob_store
```

#### Step 3: Start and Restore PostgreSQL Database
Start a clean database container and apply the dump:
```bash
docker compose -f deploy/docker-compose.prod.yml up -d postgres

# Wait for postgres readiness:
docker exec clindoc-postgres pg_isready -U clindoc_user -d clindoc_db

# Restore database dump:
docker cp "${RESTORE_SOURCE}/clindoc_db_*.dump" clindoc-postgres:/tmp/restore.dump
docker exec clindoc-postgres pg_restore \
  -U clindoc_user \
  -d clindoc_db \
  --clean \
  --if-exists \
  /tmp/restore.dump
docker exec clindoc-postgres rm /tmp/restore.dump
```

#### Step 4: Start Application Services
```bash
docker compose -f deploy/docker-compose.prod.yml up -d api worker vllm vllm-tls-proxy
```

---

## 4. Post-Restore Verification Checklist

After restoring, perform the following automated sanity checks before resuming clinical operations:

1. **Check Readiness Endpoint**:
   ```bash
   curl -f http://localhost:8000/api/v1/health/ready
   # Verify: "status": "ready", "database": "connected", "storage": "accessible"
   ```

2. **Verify Database Record Count**:
   ```bash
   docker exec clindoc-postgres psql -U clindoc_user -d clindoc_db -c \
     "SELECT count(*) AS total_documents FROM documents;"
   ```

3. **Verify Blob Decryption**:
   Execute an automated retrieval test to confirm that stored blobs decrypt without cipher errors:
   ```bash
   python -c "
   import os
   from clindoc.storage.blob import EncryptedFileBlobStore
   store = EncryptedFileBlobStore(root_dir=os.environ.get('CLINDOC_BLOB_ROOT_DIR', '/var/lib/clindoc/blob_store'))
   keys = store.list_keys()
   if keys:
       data = store.get(keys[0])
       print(f'Successfully decrypted blob: {keys[0]} ({len(data)} bytes)')
   else:
       print('Blob store is empty (clean install).')
   "
   ```

4. **Verify Audit Trail Consistency**:
   Ensure audit event sequence numbers and timestamps match database state.
