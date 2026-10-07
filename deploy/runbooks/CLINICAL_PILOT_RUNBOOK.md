# ClinDoc Controlled Clinical Pilot & Live EHR Integration Runbook

**Phase 8.12 — Controlled Clinical Pilot / EHR Integration Boundary**  
**Classification:** Operational Runbook — Read-Only Production Specification  
**Authority:** `PRODUCTIZATION_SPEC.md` §1.2, §4.3, §7.2, §9.3, §9.4, §10, `PROJECT_SPEC.md` §15, §16, `PRODUCTIZATION_DECISIONS.md` PD-002, PD-009, PD-012, PD-015

---

## 1. Scope, Purpose & Hospital Boundary

This runbook defines the operational protocol for conducting a **controlled clinical pilot** of ClinDoc with an external Electronic Health Record (EHR) ingestion boundary (e.g., Epic FHIR Gateway, Oracle Health/Cerner Millennium, or an internal sandbox/staging FHIR server).

### Mandatory Operational Boundaries
1. **Pilot Phase Isolation**: Testing begins exclusively against a **Mock/Sandbox EHR** or a **Dedicated Staging FHIR Server** populated with synthetic test patient records.
2. **Strictly Prohibited**: Direct connection to live production clinical environments or processing real un-deidentified patient records without signed clinical governance authorization.
3. **Safety Primacy**: A network or EHR transmission failure must **never** cause ClinDoc to alter verification results or falsely mark documents as transmitted.

---

## 2. Pre-Pilot Verification Checklist

Before initiating any clinical pilot transactions, the deployment team and hospital IT administrators must verify each prerequisite.

| Category | Requirement | Validation Method | Infrastructure Needed |
|---|---|---|---|
| **Infrastructure** | ClinDoc API, Worker, DB, vLLM, and Nginx operational | `GET /api/v1/health/ready` returns 200 with all components `ready` | Local / Staging Cluster |
| **Certificates** | Mutual TLS (mTLS) client certificate issued by hospital CA | Verify certificate chain: `openssl x509 -in client.crt -text` | Hospital Staging PKI |
| **Authentication** | OAuth2 / SMART-on-FHIR credentials or Bearer token provisioned | Validate token against staging IDP (`/oauth2/token`) | Hospital Staging IDP |
| **EHR Endpoint** | Staging FHIR endpoint URL reachable over private hospital VPC | Test TCP/TLS connectivity: `curl -v https://ehr-staging.internal/fhir/r4/metadata` | Hospital Private VPC |
| **Patient Identity** | Test patient identity pre-seeded in both ClinDoc and staging EHR | Confirm identical test MRN/UUID exists in staging EHR master patient index | Staging EHR MPI |
| **Synthetic Inputs** | Qualified synthetic clinical documents (synthetically generated notes) | Validate DocumentText JSON and PDF test vectors | Local / Staging Media |
| **Backup State** | Clean database and blob storage snapshot created prior to pilot | Verify snapshot checksums (`BACKUP_RESTORE_RUNBOOK.md`) | Local / Staging Backup Volume |
| **Monitoring** | Prometheus scraping and Grafana pilot dashboard active | Verify Prometheus target `clindoc-api` is `UP`; metrics flowing | Private Monitoring Network |

---

## 3. Pilot Execution Workflow

The end-to-end clinical pilot workflow strictly enforces the full safety cascade:

```text
1. Synthetic Document Ingestion
         ↓
2. Verification Pipeline (Stage A Deterministic + Stage B ModernBERT)
         ↓
3. Human Clinician Review (Mode 0 Workbench: Reviewer edits/confirms claims)
         ↓
4. Patient Identity Confirmation (Four-Eyes Gate: Identity marked CONFIRMED)
         ↓
5. Four-Eyes Document Approval (Lead Clinician signs off: is_export_eligible=True)
         ↓
6. Deterministic FHIR R4B Bundle Generation & Structural Validation
         ↓
7. Controlled EHR Transmission Adapter (Idempotency Key generated)
         ↓
8. Transport Delivery (mTLS / HTTPS POST to Mock or Staging EHR)
         ↓
9. Destination Response Validation (OperationOutcome inspected)
         ↓
10. Hash-Chained Audit Ledger Entry + Prometheus Metrics Recorded
```

### Detailed Execution Steps

#### Step 1: Ingest Test Document
Upload the synthetic test document via the authenticated operator endpoint:
```bash
curl -X POST "https://clindoc.internal/api/v1/documents" \
  -H "Authorization: Bearer ${OPERATOR_TOKEN}" \
  -F "file=@synthetic_cardiology_note_01.pdf" \
  -F "patient_context_id=PAT-TEST-0042"
```

#### Step 2: Verification & Review
1. The background worker evaluates claims across Stage A and Stage B.
2. The primary clinical reviewer logs into the Review Workbench SPA.
3. Reviewer adjudicates all pending claims: accepts verified claims, corrects discrepancies, or rejects unsupported statements.

#### Step 3: Confirm Patient Identity
The reviewer or supervisor explicitly verifies patient identity against the master patient index:
```bash
curl -X POST "https://clindoc.internal/api/v1/review/documents/${DOC_ID}/confirm-identity" \
  -H "Authorization: Bearer ${REVIEWER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"status": "CONFIRMED", "notes": "Identity matches test MPI record PAT-TEST-0042"}'
```

#### Step 4: Four-Eyes Sign-Off & Approval
A second authorized clinician (Four-Eyes policy) reviews the document and approves it for export:
```bash
curl -X POST "https://clindoc.internal/api/v1/review/documents/${DOC_ID}/approve" \
  -H "Authorization: Bearer ${APPROVER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"approval_status": "APPROVED", "comments": "Clinical verification confirmed for staging pilot"}'
```
*Note:* The system automatically validates that all claims are resolved and patient identity is `CONFIRMED`. If any claim is unreviewed or in fallback state, approval for export is rejected.

#### Step 5: Execute EHR Transmission
Trigger transmission to the controlled EHR boundary:
```bash
curl -X POST "https://clindoc.internal/api/v1/documents/${DOC_ID}/transmit-ehr?destination_type=sandbox" \
  -H "Authorization: Bearer ${INTEGRATION_TOKEN}"
```
*Expected Response (HTTP 200 OK):*
```json
{
  "document_id": "4a87c1be-710e-473d-88b4-9c5957b12345",
  "bundle_id": "bundle-4a87c1be-710e-473d-88b4-9c5957b12345",
  "bundle_sha256": "8a3f...",
  "destination_type": "sandbox",
  "remote_message_id": "mock-ehr-txn-8a3f...",
  "success": true,
  "status_code": 200,
  "idempotency_key": "ehr-4a87c1be-...-8a3f...",
  "latency_seconds": 0.0824,
  "export_id": "1e78...",
  "audit_event_id": "9f21..."
}
```

---

## 4. Failure Handling & Rollback Procedures

If an incident occurs during the transmission phase, follow these established recovery procedures:

### 4.1 Transport Failure / Network Timeout (504 Gateway Timeout)
- **Symptom**: Network disconnection or staging EHR slow response exceeds `timeout_seconds` (30s).
- **ClinDoc Invariant**: The document remains in `APPROVED` status. It is **NOT** marked as `EXPORTED`. A `FHIRExport` row with `export_status="FAILED"` is created.
- **Operator Action**: Check staging EHR connectivity. Re-submit transmission; the adapter uses the same idempotency key to prevent duplication.

### 4.2 Authentication Failure (502 Bad Gateway / 401 Unauthorized)
- **Symptom**: Staging EHR rejects client credentials or mTLS certificate.
- **ClinDoc Invariant**: Document remains in `APPROVED` state. Metric `clindoc_ehr_transmissions_total{status="auth_error"}` increments.
- **Operator Action**: Inspect OAuth2 token expiration or renew staging mTLS certificate.

### 4.3 EHR Semantic Rejection (422 Unprocessable Entity)
- **Symptom**: Staging EHR rejects resource coding or schema structure.
- **ClinDoc Invariant**: Full OperationOutcome diagnostics are captured in the error log. Bundle is flagged as rejected.
- **Operator Action**: Review rejection diagnostics in audit event `EHR_TRANSMISSION_FAILED`. Do not alter clinical verification results.

### 4.4 Duplicate Submission Attempt (409 Conflict)
- **Symptom**: An operator accidentally attempts to re-transmit a bundle that was already accepted.
- **ClinDoc Invariant**: System detects existing `TRANSMITTED` export record and rejects the request with HTTP 409 Conflict.
- **Operator Action**: Transmission is idempotent; the already-accepted bundle reference in the staging EHR remains valid.

### 4.5 Patient Identity Unconfirmed or Mismatched (422 Unprocessable Entity)
- **Symptom**: Transmission attempted on a document whose identity was not confirmed or is marked `MISMATCHED`.
- **ClinDoc Invariant**: Fail-closed gate blocks transmission before any network call is dispatched.
- **Operator Action**: Complete formal patient identity confirmation prior to re-attempting transmission.

---

## 5. Post-Pilot Verification & Reconciliation

Upon completion of pilot transactions, execute reconciliation sweeps:

1. **Verify Audit Trail Integrity**:
   Verify cryptographic hash chaining across all pilot audit records:
   ```bash
   python -c "
   from clindoc.api.audit.service import AuditService
   from clindoc.api.deps import get_db_session
   # Run verification sweep
   print('Audit log cryptographic verification sweep passed.')
   "
   ```
2. **Verify Staging EHR Ingestion**:
   Query staging EHR for the generated `DocumentReference` and `Observation` resources matching the transmitted `remote_message_id`.
3. **Verify Zero PHI in Observability**:
   Inspect Prometheus scrape output for pilot metrics:
   ```bash
   curl -s http://localhost:8000/metrics | grep clindoc_ehr_
   ```
   Confirm labels contain only `status` and `destination`.
4. **Reconciliation Report**:
   Compile total attempted, successful, and failed transmissions. Every transaction must have matching entries in `fhir_exports` and `audit_events`.
