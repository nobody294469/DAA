# ClinicalDoc — Phase 5.2.3 Sub-Milestone 4.2.0.1: Audit Integrity & Lifecycle Clarification

**Execution Mode: STRICTLY READ-ONLY ARCHITECTURAL & CONTRACT REVIEW**  
**Author: Antigravity Agent (Pair Programming with Engineering Team)**  
**Date: October 31, 2026**  
**Status: FINAL CONTRACT ADDENDUM — AWAITING IMPLEMENTATION AUTHORIZATION**  
**Reference Document: `phase523_milestone420_review_contract.md`**

---

## Executive Summary

This addendum resolves the remaining five architectural and security questions for **Sub-Milestone 4.2: Mode 0 Review Service & Durable Audit Contract**. It clarifies the offline audit-ledger trust anchor, relational audit database protections, canonical RFC 8785 hash envelope construction, review lifecycle edge cases, and provides the exhaustive implementation readiness checklist.

---

## 1. Offline Audit-Ledger Trust Anchor

### 1.1 Plain Manifest vs. HMAC-Protected Checkpoint

A fundamental distinction must be drawn between integrity against accidental corruption and anti-tamper security against intentional falsification:

1. **Plain Manifest File (`.review_audit.manifest.json`)**:
   - A manifest containing `{"last_hash": H_N, "total_events": N, "document_sha256": "..."}` stored in the same local directory as the `.review_audit.jsonl` ledger provides **integrity against bit-rot or accidental truncation only**.
   - It provides **ZERO anti-tamper security** against an attacker with filesystem write access: an attacker who modifies an audit event can simply recompute all downstream SHA-256 hashes, update the final line of `.review_audit.jsonl`, and write the forged terminal hash into `manifest.json`. Both files will match perfectly, and unauthenticated verification will silently report the forged history as valid.

2. **HMAC-Protected Checkpoint (`.review_audit.checkpoint`)**:
   - To achieve genuine tamper detection, the checkpoint must be cryptographically bound to a secret key $K$ that is **not accessible to the local data modifier or document directory**:
     $$\text{Checkpoint Signature} = \text{HMAC-SHA256}\Big(K, \text{doc\_id} \mathbin{\Vert} \text{doc\_sha256} \mathbin{\Vert} \text{seq}_N \mathbin{\Vert} H_N \mathbin{\Vert} \text{timestamp}\Big)$$
   - Because the attacker cannot forge the HMAC signature without key $K$, rewriting the JSONL ledger and the local manifest is immediately detected by Gate 9 as an invalid cryptographic checkpoint.

```
┌────────────────────────────────────────────────────────┐
│                   Local Artifact Dir                   │
│                                                        │
│  ┌───────────────────────┐   ┌──────────────────────┐  │
│  │ .review_audit.jsonl   │   │ .review_audit.chkpt  │  │
│  │ (Sequenced JSONL rows)│   │ (Envelope + HMAC)    │  │
│  └───────────┬───────────┘   └──────────┬───────────┘  │
└──────────────┼──────────────────────────┼──────────────┘
               │                          │
               ▼                          ▼
      ┌─────────────────┐        ┌─────────────────┐
      │ Recompute SHA256│        │ Verify HMAC-256 │
      │   Hash Chain    │        │  with Secret K  │
      └────────┬────────┘        └────────┬────────┘
               │                          │
               └───────────┬──────────────┘
                           │ Matches & Verified?
                           ▼
                 [ GATE 9 EXPORT GATE ]
```

### 1.2 HMAC Key Storage, Isolation & Protection

- **Storage Location**: The HMAC secret key must **never** be collocated with document artifacts or in the git working tree. It must be provided via:
  1. *Primary*: Dedicated environment variable `CLINDOC_AUDIT_HMAC_KEY` (minimum 256-bit high-entropy secret).
  2. *Container/Kubernetes Deployment*: Mounted secret volume at `/run/secrets/clindoc_audit_key` with file permissions `0400`, owned by the service process user.
  3. *Enterprise Vault / KMS*: Key fetched during service startup via AWS KMS, GCP KMS, or HashiCorp Vault.
- **Access Segregation**: The pipeline worker executing document extraction may be granted read-only access to documents, but the signing key should only be accessible by the authenticated `ReviewService` daemon.

### 1.3 Key Lifecycle, Missing-Key, Invalid-Key & Rotation Behavior

1. **Missing Key Handling**:
   - **Production / Clinical Export Mode (`CLINDOC_ENV=production`)**: **Fail-Closed**.
     If `CLINDOC_AUDIT_HMAC_KEY` is not configured, the review service refuses to sign checkpoints, and Gate 9 raises `AuditAnchorMissingError`, suppressing all provisional and high-risk claims from FHIR export (`SUPPRESSED_UNVERIFIED_AUDIT_LEDGER`).
   - **Local Developer / Offline Benchmark Mode (`CLINDOC_ENV=development`)**:
     If no HMAC key is present, the system emits a prominent security log warning:
     `WARNING: Offline audit ledger running in UNANCHORED mode. Tamper evidence is restricted to accidental corruption detection only.`
     A plain SHA-256 manifest is written. To permit export in development mode, the caller must explicitly pass `--allow-unanchored-dev-ledger`.
2. **Invalid Key / Verification Failure**:
   - If the computed HMAC does not match the checkpoint signature, raises `AuditLedgerTamperDetectedError`.
   - Gate 9 immediately halts export of the document.
3. **Key Rotation Protocol**:
   - The checkpoint payload includes an explicit `key_id`:
     ```json
     {
       "key_id": "clindoc-audit-k1",
       "algorithm": "HMAC-SHA256",
       "checkpoint_sequence": 42,
       "terminal_hash": "a1b2c3...",
       "document_sha256": "e3b0c4...",
       "hmac_signature": "9f8e7d..."
     }
     ```
   - The review service maintains a keyring mapping `key_id` to key values. When rotating keys, a new `clindoc-audit-k2` is designated as the active signing key, while `clindoc-audit-k1` is retained as a valid verification key for legacy ledgers.
4. **Recovery Behavior**:
   - A tampered or unanchored ledger cannot be repaired or overwritten in-place.
   - Recovery requires an explicit supervisory action: the entire document review session must be invalidated, and all claims reset to `PENDING` for a clean re-review.

### 1.4 Explicit Project Limitation & Architectural Realism

> [!CAUTION]
> **Architectural Boundary Statement**: In standalone offline CLI usage running on a standard workstation without OS user privilege separation or a remote HSM/KMS, **absolute tamper-proof protection against a local user with administrative access is impossible**. If the user has access to the runtime environment, they have access to the environment variables and memory.
>
> Therefore, within the ClinicalDoc architecture:
> - Offline JSONL + HMAC provides **tamper-evidence against untrusted downstream consumers, intermediate transit corruption, and unprivileged processes**.
> - Absolute legal non-repudiation requires **Relational Persistence Mode** backed by PostgreSQL role isolation or external WORM / KMS anchoring.

---

## 2. Relational Audit Protection

### 2.1 Database Roles & Permission Boundaries

In PostgreSQL production deployments, the application database role (`clindoc_app`) must be strictly constrained on `audit_events`:

```sql
-- Grant append and read permissions only
GRANT SELECT, INSERT ON TABLE audit_events TO clindoc_app;

-- Explicitly revoke destructive permissions
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_events FROM clindoc_app;
REVOKE ALL ON TABLE audit_events FROM PUBLIC;
```

### 2.2 Database-Level Append-Only Enforcement

To prevent even accidental updates from administrative ORM sessions or migration scripts, an immutable trigger is placed on the table:

```sql
CREATE OR REPLACE FUNCTION trg_prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Table audit_events is append-only. UPDATE and DELETE operations are forbidden.'
    USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_events_immutable
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION trg_prevent_audit_modification();
```

### 2.3 Concurrency Serialization & Transaction Boundaries

- **Advisory Locking**: All audit writes acquire the dedicated 64-bit PostgreSQL transaction advisory lock:
  `SELECT pg_advisory_xact_lock(0x5048495F415544);` (defined in `AuditService.AUDIT_CHAIN_LOCK_ID`).
- **Transaction Boundary**: The advisory lock, the review action row creation (`ReviewAction`), the claim review state mutation (`Claim.review_state`), and the audit event insertion (`AuditEvent`) take place **within a single atomic ACID transaction**.
- **Rollback Guarantee**: If any constraint fails (optimistic lock mismatch, PHI detection, foreign key violation), the transaction rolls back completely. The hash chain remains unbroken and monotonic.

### 2.4 App-Level Tamper Resistance vs. Privileged DBA Modifications

1. **Application-Level Security**:
   - SQL injection, application exploits, or compromised worker tokens cannot modify or delete audit rows because the database engine rejects `UPDATE` and `DELETE` queries at the permission and trigger levels.
2. **Privileged DBA Tampering**:
   - A PostgreSQL superuser (`postgres` or RDS master) possesses the technical capability to disable triggers (`ALTER TABLE audit_events DISABLE TRIGGER ALL`), modify rows, and re-enable triggers.
3. **External Checkpointing Requirement**:
   - To detect privileged DBA modifications, ClinicalDoc specifies an **external periodic checkpointing task**:
   - A scheduled daemon reads the latest sequence $N$ and terminal hash $H_N$ and writes it to an external WORM (Write-Once-Read-Many) target, such as AWS S3 Object Lock in Compliance Mode, Google Cloud Storage Bucket Lock, or an external immutable log stream. Any retroactive modification by a DBA will diverge from the externally committed hash checkpoint.

---

## 3. Canonical Hash Construction

### 3.1 Single Canonical Event Envelope

To eliminate ad-hoc string concatenation and escaping ambiguities, every audit event is normalized into a single canonical dictionary envelope prior to hashing:

```json
{
  "action": "ACCEPT",
  "actor_id": "c3b8e721-5a9d-4c3e-8f2a-1b4e5d6c7a8b",
  "entity_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "entity_type": "claim",
  "event_type": "CLAIM_REVIEW_ACTION",
  "metadata": {
    "action_type": "ACCEPT",
    "actor_id": "c3b8e721-5a9d-4c3e-8f2a-1b4e5d6c7a8b",
    "claim_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "claim_version": 1,
    "document_id": "4a2f8c1e-9b3d-4e5a-8f7c-2b1a3d5e7f9a",
    "document_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "field_type": "med.dose",
    "new_state": "ACCEPTED",
    "previous_state": "PENDING",
    "reason_code": "SPLIT_REGIMEN_CONFIRMED"
  },
  "previous_event_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "sequence_number": 1,
  "timestamp": "2026-10-31T12:00:00.000000Z"
}
```

### 3.2 Canonicalization & Serialization Rules (RFC 8785)

1. **JCS Canonicalization (RFC 8785)**:
   - Dictionary keys must be sorted lexicographically by UTF-16 code units (standard ASCII sort).
   - No insignificant whitespace (separators must be `(',', ':')`).
   - Floats/numbers formatted without trailing zeros or exponential representations unless required by RFC 8785.
   - String literals encoded as UTF-8 without non-mandatory escaping.
2. **Timestamp Normalization**:
   - Timestamps must be UTC ISO-8601 strings formatted with microsecond precision:
     `YYYY-MM-DDTHH:MM:SS.ffffffZ` (e.g. `2026-10-31T12:00:00.000000Z`).
   - If a naive datetime is passed, it is explicitly localized to UTC.
3. **Null & Genesis Handling**:
   - For the genesis event ($\text{seq} = 1$), `previous_event_hash` is an empty string `""`.
   - Null values within `metadata` must serialize to JSON `null`. Optional top-level envelope fields are forbidden; all 8 envelope keys are required.
4. **Sequence & Duplicate-Event Rejection**:
   - Sequence numbers must be strictly sequential positive integers: $\text{seq}_n == \text{seq}_{n-1} + 1$.
   - **Duplicate-Event Rejection**: Database unique constraints `uq_audit_sequence_number` and `uq_audit_current_event_hash` prevent duplicate entries. In offline JSONL verification, any duplicate sequence number or duplicate event hash aborts processing with `DuplicateAuditEventError`.

---

## 4. Review Lifecycle Edge Cases

### 4.1 Reopening Claims (`REOPEN` Action)

| Current State | Target State | Permitted Roles | Mandatory Reason Code Required? | Export Impact |
| :--- | :---: | :--- | :---: | :--- |
| **`ACCEPTED`** | `PENDING` | `CLINICAL_REVIEWER`, `CHIEF_MEDICAL_OFFICER` | **YES** | Immediately revokes export eligibility. |
| **`REJECTED`** | `PENDING` | `CLINICAL_REVIEWER`, `CHIEF_MEDICAL_OFFICER` | **YES** | Restores claim to unreviewed triage queue. |
| **`ESCALATED`**| `PENDING` | `SUPERVISORY_REVIEWER`, `CHIEF_MEDICAL_OFFICER` | **YES** | Returns claim from supervisor queue to general queue. |

- **Reopening Already-Exported Claims**: If a document has already been exported in a finalized FHIR bundle (`Document.status == 'EXPORTED'`), individual claims cannot be reopened directly. Reopening requires invoking the document-level `amend_document()` workflow, generating a superseding FHIR bundle version (PD-010).

### 4.2 Editing Successor Claims & Self-Acceptance Ban

Under the Four-Eyes principle:
- When Clinician A edits claim $C_{v1}$, a new claim $C_{v2}$ is created with:
  - `supersedes_claim_id = C_{v1}.id`
  - `origin = ClaimOrigin.REVIEWER`
  - `created_by = Clinician A.id`
- **Clinician A CANNOT accept $C_{v2}$**.
- Clinician A's attempt to execute `accept_claim(C_{v2}.id)` raises `FourEyesViolationError("Claim creator/modifier cannot accept their own edited claim")`.
- A second, distinct clinician (Clinician B) must review and accept $C_{v2}$.

### 4.3 Escalation Resolution Permissions

- Standard reviewers (`CLINICAL_REVIEWER`) may **initiate** an escalation (`PENDING -> ESCALATED`).
- Only actors holding the `SUPERVISORY_REVIEWER` or `CHIEF_MEDICAL_OFFICER` role may **resolve** an escalated claim (`ESCALATED -> ACCEPTED`, `ESCALATED -> REJECTED`, or `ESCALATED -> EDITED`).
- Resolving an escalated claim records an audit event with `action="ACCEPT"`, `metadata.escalation_resolved=True`, and `metadata.resolver_role=actor.role`.

### 4.4 Superseded-Version Export Prevention

- When claim $C_{v1}$ is edited, its `review_state` is set to `EDITED`.
- In `FHIRExportService.evaluate_claim_eligibility()`:
  - Claims with `review_state == ClaimReviewState.EDITED.value` are suppressed with reason:
    `SUPPRESSED_SUPERSEDED_VERSION`.
  - Gate 9 enforces that only the **terminal leaf version** of a claim lineage can be evaluated for export. Older superseded versions are never emitted as FHIR resources.

### 4.5 Concurrent Document Replacement or Claim Edits

1. **Concurrent Document Replacement**:
   - If Reviewer A is inspecting document $D$ while an operator uploads a replacement document $D'$ (or re-scans the PDF), the document hash changes: $\text{document\_sha256}' \neq \text{document\_sha256}$.
   - Reviewer A's review submission passes `document_sha256`.
   - `ReviewService` compares `document.document_sha256 == submission.document_sha256`.
   - If mismatched, raises `StaleDocumentVersionError("Document has been replaced or modified since review started")`. The transaction aborts with zero mutations.
2. **Concurrent Claim Edits (Optimistic Locking)**:
   - Reviewer A and Reviewer B simultaneously review claim $C_{v1}$.
   - Reviewer A submits an edit, updating $C_{v1}$ to `EDITED` and creating $C_{v2}$.
   - Reviewer B attempts to accept $C_{v1}$ with `expected_version=1`.
   - The query verifies `claim.version == expected_version AND claim.review_state == 'PENDING'`.
   - Because $C_{v1}$ is now `EDITED` (or version is incremented), `OptimisticLockError` is raised. Reviewer B's stale acceptance is rejected.

---

## 5. Final Implementation Readiness Checklist

This checklist defines the complete, rigorous verification plan for Sub-Milestone 4.2:

```markdown
### Sub-Milestone 4.2 Implementation Checklist

#### [A] ReviewService State Transitions
- [ ] PENDING -> ACCEPTED (Valid clinician actor)
- [ ] PENDING -> REJECTED (Requires non-empty reason code)
- [ ] PENDING -> EDITED (Spawns v+1 in PENDING with origin='reviewer'; marks prior as EDITED)
- [ ] PENDING -> ESCALATED (Routes to supervisory queue)
- [ ] ACCEPTED -> PENDING (REOPEN with reason code; revokes export eligibility)
- [ ] REJECTED -> PENDING (REOPEN with reason code)
- [ ] ESCALATED -> PENDING (REOPEN by supervisor)
- [ ] Illegal transitions rejected (e.g., EDITED -> ACCEPTED, ACCEPTED -> EDITED directly)

#### [B] Authentication & Role Authorization
- [ ] Anonymous, empty, or unverified actor_id fails closed with UnauthorizedReviewerError
- [ ] Only CLINICAL_REVIEWER or CHIEF_MEDICAL_OFFICER can accept standard claims
- [ ] Only SUPERVISORY_REVIEWER or CHIEF_MEDICAL_OFFICER can resolve ESCALATED claims
- [ ] Non-reviewer roles (OPERATOR, AUDITOR) cannot perform review state transitions

#### [C] Four-Eyes Enforcement
- [ ] Pipeline operator (uploader) cannot accept high-risk or provisional claims
- [ ] Reviewer who edited claim (creator of v+1) cannot accept v+1
- [ ] High-potency drugs, slot conflicts, and 'respectively' claims enforce distinct reviewer
- [ ] Violation raises FourEyesViolationError

#### [D] Relational & Offline Persistence
- [ ] Relational: Atomic write of ReviewAction, Claim state, and AuditEvent
- [ ] Relational: PostgreSQL advisory transaction lock prevents sequence races
- [ ] Offline JSONL: Atomic append of RFC 8785 canonical event envelope
- [ ] Offline Checkpoint: HMAC-SHA256 signature written to checkpoint file
- [ ] Offline Dev Mode: Safe unanchored fallback with loud warning and CLI flag requirement

#### [E] Audit Verification & Recovery
- [ ] Unbroken SHA-256 hash chain verification from seq 1 to N
- [ ] Strict timestamp ISO-8601 UTC microsecond normalization
- [ ] Forbidden PHI keys strictly rejected (validate_metadata_phi_free)
- [ ] Corrupted line, altered payload, or modified hash raises AuditLedgerVerificationError
- [ ] Missing sequence number, sequence gap, or replay attempt raises SequenceError
- [ ] Mismatched HMAC checkpoint raises AuditLedgerTamperDetectedError

#### [F] Gate 9 Export Integration
- [ ] In-memory claims with review_state='ACCEPTED' but lacking audit proof are suppressed
- [ ] Document SHA-256 mismatch between audit log and active document suppresses export
- [ ] Stale superseded versions suppressed under SUPPRESSED_SUPERSEDED_VERSION
- [ ] Deterministic suppression codes emitted: SUPPRESSED_UNVERIFIED_AUDIT_LEDGER, etc.

#### [G] Comprehensive Test Suite (`tests/test_phase523_milestone4_2.py`)
- [ ] test_state_machine_valid_and_invalid_transitions
- [ ] test_claim_edit_invalidates_prior_acceptance_and_spawns_pending_v2
- [ ] test_four_eyes_editor_cannot_accept_own_edit
- [ ] test_four_eyes_operator_cannot_accept_provisional_bijection
- [ ] test_audit_hash_chain_verification_positive
- [ ] test_audit_hash_chain_detects_tampered_payload
- [ ] test_audit_hash_chain_detects_deleted_event
- [ ] test_audit_hash_chain_detects_replayed_event
- [ ] test_hmac_checkpoint_verification_and_tamper_detection
- [ ] test_optimistic_locking_concurrent_review_conflict
- [ ] test_stale_document_sha256_invalidates_past_reviews
- [ ] test_gate9_blocks_in_memory_accepted_claim_without_audit_ledger
```

---

## 6. Stop Condition & Implementation Plan

- **Work Conducted**: Strictly read-only architectural analysis and formal specification addendum.
- **Production Files Modified**: **ZERO** (Zero code, test, or config changes made).
- **Working Tree**: Completely clean; all existing unstaged workspace files preserved.

**Awaiting user authorization to begin Sub-Milestone 4.2 implementation according to this addendum and the primary contract specification.**
