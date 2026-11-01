# CLINICALDOC STEP 3 — DEF-003: AUDIT-LEDGER CONCURRENCY & CRASH-CONSISTENCY
## Implementation Execution Plan (Documentation-Only Baseline — Revision 2.7)

**Document Reference**: `CLINICALDOC_STEP3_DEF003_IMPLEMENTATION_EXECUTION_PLAN.md`  
**Security Defect**: DEF-003 (P0 — Audit Ledger Concurrency, Cross-Tier Synchronization & Crash-Consistency)  
**Standard Baseline**: HL7 FHIR R4B 4.3.0 / HIPAA Security Rule §164.312(b) Audit Controls / Python Canonical Sorted JSON / W3C XMLDSig / RFC 7515 (JWS)  
**Authoritative Storage**: PostgreSQL `audit_events`  
**Projection Target**: Offline JSONL cryptographic attestation ledger (`.jsonl`) & HMAC checkpoint (`.chkpt`)  
**Status**: REVISED FOR REVIEW — NO IMPLEMENTATION ACTIONS AUTHORIZED  

---

## Revision History

| Version | Date | Author | Summary of Changes |
| :---: | :---: | :---: | :--- |
| **1.0** | 2026-11-01 | Antigravity AI | Initial Step 3 DEF-003 planning document. |
| **2.0** | 2026-11-01 | Antigravity AI | Initial review revisions (quarantine, checkpoint categories, advisory locking). |
| **2.1** | 2026-11-01 | Antigravity AI | Addressed Gate 9 DB-first commit, global sequence constraints, outbox lifecycle, and OS-level file locking. |
| **2.2** | 2026-11-01 | Antigravity AI | Addressed independent quarantine verification, legacy migration separation, administrative lock ordering, canonical hashing precision, bundle digest definition, and secure credential input. |
| **2.3** | 2026-11-01 | Antigravity AI | Addressed quarantine source race, TC-19 enforcement boundaries, incomplete attestation across retrieval paths, Windows NTFS durability, and metadata delimiter assessment. |
| **2.4** | 2026-11-01 | Antigravity AI | Addressed quarantine write protection, TC-19 separation, W-02 formal retirement, and complete DB-to-JSONL attestation verification. |
| **2.5** | 2026-11-01 | Antigravity AI | Addressed quarantine descriptor reconciliation (`r+b`), Win32 write-sharing denial, 13 retained writers count, lock lifecycle duration, and crash matrix B-7. |
| **2.6** | 2026-11-01 | Antigravity AI | Prohibited automatic POSIX truncation without write-exclusion; established Three-Tier Bundle Integrity; reconciled B-7 recovery; clarified out-of-band direct insert limits. |
| **2.7** | 2026-11-01 | Antigravity AI | **Final Documentation Clarifications**: <br>1. **POSIX Quarantine Finalization Race Clarification (§5.1)**: Clarified that a second read/metadata check does not eliminate every race on POSIX; original POSIX source is never truncated automatically and remains authoritative forensic evidence; quarantine copy cannot be certified as complete snapshot if concurrent modification cannot be excluded; fails closed and requires verified process quiescence for administrative recovery; updated TC-26 with append injection before quarantine finalization.<br>2. **Exact Transport Wire Byte Binding (§7.1, §7.2)**: Defined exact serialized byte sequence separately for HTTPS FHIR payloads, HTTP export downloads, and MLLP-framed messages (framing explicitly excluded); serialized once to immutable bytes passed directly to transport; rejected claim that in-memory hashing proves wire identity; updated TC-24 to test actual transport boundary.<br>3. **Bundle.signature Verification Contract (§7.1)**: Fully specified RS256/ES256 JWS digital signature format, RFC 8785 canonical signed representation (excluding signature.data), trusted JWKS/X.509 acquisition, key rotation/revocation handling, fail-closed policy, and explicit binding to augmented bundle and audit provenance (rejecting SHA-256 digest equality alone as signer authentication); updated TC-27. |

---

## 1. Exhaustive Call Site Inventory & Analysis

An exhaustive code audit of the entire workspace establishes the exact inventory of **14 inventoried audit event call sites** (consisting of **13 retained production audit writers** and **1 retired dual-write call site**) and **8 audit/bundle verifier call sites**:

### 1.1 The 14 Inventoried Call Sites & 13 Retained Writers

Every audit call site in the repository interacts with `AuditService.record_event()` or `ReviewAuditLedger.append_event()`:

| Call Site ID | File & Exact Line Reference | Function / Method | Event Type (`event_type`) | Operation Description | Architectural Resolution |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **W-01** | [`src/clindoc/review/service.py:1260`](file:///d:/daa/src/clindoc/review/service.py#L1260) | `ClaimReviewService._record_audit_event()` | `CLAIM_REVIEW_ACTION` | Single claim accept, reject, edit, escalate, or reopen action. | **Retained Writer 1**: Routes to `AuditService.record_event()`. |
| **W-02** | [`src/clindoc/review/service.py:1276`](file:///d:/daa/src/clindoc/review/service.py#L1276) | `ClaimReviewService._record_audit_event()` | `CLAIM_REVIEW_ACTION` | Dual-write append to offline JSONL ledger. | **FORMALLY RETIRED CALL SITE**: Dual-write eliminated. Replaced by centralized post-commit `AuditProjectionEngine`. Does not acquire advisory lock. |
| **W-03** | [`src/clindoc/review/approval.py:277`](file:///d:/daa/src/clindoc/review/approval.py#L277) | `ApprovalEngine.request_approval()` | `DOCUMENT_APPROVAL_REQUESTED` | Operator initiates formal Four-Eyes document approval. | **Retained Writer 2**: DB advisory lock `0x5048495F415544`. |
| **W-04** | [`src/clindoc/review/approval.py:345`](file:///d:/daa/src/clindoc/review/approval.py#L345) | `ApprovalEngine.approve_document()` | `DOCUMENT_APPROVED` | Second operator approves document and patient identity (Mode 0). | **Retained Writer 3**: DB advisory lock `0x5048495F415544`. |
| **W-05** | [`src/clindoc/review/approval.py:516`](file:///d:/daa/src/clindoc/review/approval.py#L516) | `ApprovalEngine.reject_document()` | `DOCUMENT_REJECTED` | Second operator rejects document approval request. | **Retained Writer 4**: DB advisory lock `0x5048495F415544`. |
| **W-06** | [`src/clindoc/review/approval.py:604`](file:///d:/daa/src/clindoc/review/approval.py#L604) | `ApprovalEngine.revoke_approval()` | `DOCUMENT_APPROVAL_REVOKED` | Operator revokes prior document approval. | **Retained Writer 5**: DB advisory lock `0x5048495F415544`. |
| **W-07** | [`src/clindoc/services/fhir_export.py:1123`](file:///d:/daa/src/clindoc/services/fhir_export.py#L1123) | `FHIRExportService.export_document_to_fhir()` | `FHIR_BUNDLE_EXPORTED` | Successful validation, generation, and persistence of FHIR export bundle. | **Retained Writer 6**: DB advisory lock `0x5048495F415544`. |
| **W-08** | [`src/clindoc/services/ehr_integration.py:259`](file:///d:/daa/src/clindoc/services/ehr_integration.py#L259) | `EHRIntegrationService.send_fhir_bundle_to_ehr()` | `EHR_TRANSMISSION_ATTEMPTED` | Dispatch attempt over MLLP / HTTPS FHIR endpoint. | **Retained Writer 7**: DB advisory lock `0x5048495F415544`. |
| **W-09** | [`src/clindoc/services/ehr_integration.py:409`](file:///d:/daa/src/clindoc/services/ehr_integration.py#L409) | `EHRIntegrationService.send_fhir_bundle_to_ehr()` | `EHR_TRANSMISSION_SUCCEEDED` | Successful ACK receipt from EHR interface engine. | **Retained Writer 8**: DB advisory lock `0x5048495F415544`. |
| **W-10** | [`src/clindoc/services/ehr_integration.py:482`](file:///d:/daa/src/clindoc/services/ehr_integration.py#L482) | `EHRIntegrationService.send_fhir_bundle_to_ehr()` | `EHR_TRANSMISSION_FAILED` | NACK or timeout during EHR bundle dispatch. | **Retained Writer 9**: DB advisory lock `0x5048495F415544`. |
| **W-11** | [`src/clindoc/api/routes/documents.py:158`](file:///d:/daa/src/clindoc/api/routes/documents.py#L158) | `POST /documents/` (upload endpoint) | `DOCUMENT_INGESTED` | Ingestion of raw clinical PDF/image document. | **Retained Writer 10**: DB advisory lock `0x5048495F415544`. |
| **W-12** | [`src/clindoc/api/routes/documents.py:248`](file:///d:/daa/src/clindoc/api/routes/documents.py#L248) | `DELETE /documents/{id}` | `DOCUMENT_DELETED` | Soft deletion of document and associated claims. | **Retained Writer 11**: DB advisory lock `0x5048495F415544`. |
| **W-13** | [`src/clindoc/api/routes/documents.py:354`](file:///d:/daa/src/clindoc/api/routes/documents.py#L354) | `POST /documents/{id}/reprocess` | `DOCUMENT_REPROCESSED` | Pipeline re-extraction trigger. | **Retained Writer 12**: DB advisory lock `0x5048495F415544`. |
| **W-14** | [`src/clindoc/api/routes/jobs.py:68`](file:///d:/daa/src/clindoc/api/routes/jobs.py#L68) | `POST /jobs/{id}/cancel` | `JOB_CANCELLED` | Pipeline extraction worker cancellation. | **Retained Writer 13**: DB advisory lock `0x5048495F415544`. |

### 1.2 The 8 Verifier & Retrieval Call Sites

Every verifier performing cryptographic, lifecycle, or attestation verification:

| Verifier ID | File & Exact Line Reference | Function / Method | Verification Target | Existing Mechanism & Defect |
| :---: | :--- | :--- | :--- | :--- |
| **V-01** | [`src/clindoc/services/fhir_export.py:488-540`](file:///d:/daa/src/clindoc/services/fhir_export.py#L488-L540) | `FHIRExportService.evaluate_claim_eligibility()` | Gate 9 claim-level audit backing | Checks `audit_ledger.verify_ledger()`. Pure file check; ignores PostgreSQL. |
| **V-02** | [`src/clindoc/services/fhir_export.py:821-843,870-980`](file:///d:/daa/src/clindoc/services/fhir_export.py#L821-L843) | `FHIRExportService.export_document_to_fhir()` | Gate 9 document-level export eligibility | Evaluates Four-Eyes document approval. Lacks global advisory lock throughout export transaction. |
| **V-03** | [`src/clindoc/review/service.py:985-1100`](file:///d:/daa/src/clindoc/review/service.py#L985-L1100) | `ClaimReviewService._verify_historical_actor_audit_evidence()` | Historical creator/editor provenance | Calls `audit_service.verify_audit_chain(session, 1, seq)` and `audit_ledger.verify_ledger()`. |
| **V-04** | [`src/clindoc/api/audit/service.py:283-401`](file:///d:/daa/src/clindoc/api/audit/service.py#L283-L401) | `AuditService.verify_audit_chain()` | Authoritative PostgreSQL hash chain | Read query verifying sequences $1..N$, genesis linkage, previous hashes, and current hashes. |
| **V-05** | [`src/clindoc/review/service.py:486-660`](file:///d:/daa/src/clindoc/review/service.py#L486-L660) | `ReviewAuditLedger.verify_ledger()` | Offline JSONL file & HMAC checkpoint | Verifies JSONL hash chain and `.chkpt` HMAC signature. Pure file read; ignores DB state. |
| **V-06** | [`src/clindoc/api/routes/audit.py:50-80`](file:///d:/daa/src/clindoc/api/routes/audit.py#L50-L80) | `GET /audit/verify` & `POST /audit/verify` | Administrative audit inspection | Calls `audit_service.verify_audit_chain()` across requested sequence ranges. |
| **V-07** | [`src/clindoc/services/ehr_integration.py:215-228`](file:///d:/daa/src/clindoc/services/ehr_integration.py#L215-L228) | `EHRIntegrationService.send_fhir_bundle_to_ehr()` | Persisted `FHIRBundle` dispatch eligibility | Loads existing bundle from DB; **currently lacks attestation check**; dispatches unprojected bundles. |
| **V-08** | [`src/clindoc/api/routes/documents.py:439-445`](file:///d:/daa/src/clindoc/api/routes/documents.py#L439-L445) | `GET /documents/{document_id}/export` | Export download endpoint | Loads existing `FHIRBundle` from DB; **currently lacks attestation check**; returns uncertified bundles. |

---

## 2. Canonical Hashing Specification & Delimiter Collision Assessment

### 2.1 The Authoritative Hash Algorithm & Envelope
The ClinicalDoc system establishes [`AuditService.compute_audit_event_hash`](file:///d:/daa/src/clindoc/api/audit/service.py#L88-L150) as the sole authoritative hash standard. This directly fulfills the database contract codified in [`src/clindoc/db/models.py:858`](file:///d:/daa/src/clindoc/db/models.py#L858) (`doc="SHA-256(sequence || timestamp_iso || actor || event_type || entity_type || entity_id || action || metadata_json || prev_hash)"`).

#### A. Exact Bytes Hashed
The authoritative hash is computed over the UTF-8 bytes of a 9-field pipe-delimited payload:
```python
canonical_payload = (
    f"{sequence_number}|"
    f"{timestamp_iso}|"
    f"{actor_id.strip()}|"
    f"{event_type.strip()}|"
    f"{entity_type.strip()}|"
    f"{entity_id.strip()}|"
    f"{action.strip()}|"
    f"{canonical_metadata_str}|"
    f"{prev_hash_str}"
)
payload_bytes = canonical_payload.encode("utf-8")
current_event_hash = hashlib.sha256(payload_bytes).hexdigest()
```

#### B. Exact Field Normalization & Canonicalization Rules
1. **`sequence_number`**: Decimal ASCII string representation of integer sequence ($\ge 1$).
2. **`timestamp_iso`**: ISO-8601 string in UTC timezone.
   - Evaluated as `(timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()`.
   - If microsecond is 0, Python emits `YYYY-MM-DDTHH:MM:SS+00:00`.
   - If microsecond > 0, Python emits `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`.
3. **`actor_id`, `event_type`, `entity_type`, `entity_id`, `action`**: Stripped of leading/trailing whitespace.
4. **`canonical_metadata_str`**: 
   - **Specification**: Serialized using **Python Canonical Sorted JSON**:
     ```python
     json.dumps(clean_metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
     ```
   - **Standard Clarification**: This is *Python Canonical Sorted JSON* (ASCII-escaped unicode with `\uXXXX`, sorted keys, compact delimiters without whitespace). It is **not** raw RFC 8785 (which forbids `\uXXXX` escaping for non-ASCII UTF-8 characters). Documenting this standard accurately preserves 100% hash continuity with all historical database records.
5. **`prev_hash_str` (Genesis Rule)**:
   - For sequence 1 (genesis event), `previous_event_hash` in PostgreSQL is `NULL`.
   - In `compute_audit_event_hash`, `prev_hash_str` evaluates to the **empty string `""`**.
   - The end of the genesis payload string is explicitly `...|{canonical_metadata_str}|`. It is **NOT** `SHA256("")` (`e3b0c4...`).
   - For sequences $> 1$, `prev_hash_str` is the lowercase 64-character hexadecimal string of the preceding event's `current_event_hash`.

---

### 2.2 Metadata Delimiter Collision Assessment & Future Migration Path

#### A. Pipe Characters in Standard Fields vs Metadata Payload
1. **Header Fields (Fields 3–7)**: Rejecting pipe characters (`|`) in `actor_id`, `event_type`, `entity_type`, `entity_id`, and `action` strictly prevents delimiter boundary shifting among the fixed header fields.
2. **Literal Pipes Inside Serialized Metadata**:
   - `canonical_metadata_str` is a JSON object string (e.g. `{"reason":"test|value"}`).
   - If a clinical metadata value contains a literal pipe `|`, that pipe is enclosed within JSON quotes `{"key":"...|..."}`.
   - **One-Way Hash Integrity**: `compute_audit_event_hash` is a one-way hashing function (`SHA-256`). Neither `AuditService` nor `ReviewAuditLedger` ever splits or parses the concatenated pipe string during verification. Verification takes the structured database row (or parsed JSONL dictionary) and re-executes identical concatenation before hashing. Therefore, literal pipes inside JSON string values do not cause parser ambiguity because the string is never parsed by delimiter splitting.
3. **Theoretical Collision Analysis**:
   - Could an attacker craft two distinct events $(M_1, H_1)$ and $(M_2, H_2)$ that produce the identical concatenated string?
   - The payload format is `...|{canonical_metadata_str}|{prev_hash_str}`.
   - For a collision to occur, $M_1$ would have to contain `}|` followed by 64 hex characters matching $H_1$, while $M_2$ would have to terminate earlier and consume part of $M_1$ as its metadata.
   - However:
     * $M$ is strictly generated by `json.dumps(clean_metadata)`, ensuring valid JSON syntax and balanced braces.
     * $H$ must match the previous event's `current_event_hash` (which is cryptographically constrained by the preceding chain) and must satisfy the database column constraint `VARCHAR(64)`.
     * Therefore, under structured JSON serialization and fixed previous hash linkage, practical collision attacks are computationally infeasible.

#### B. Architectural Limitation & Safe Future Migration Path
- **Current Architecture Limitation**: Delimiter-based concatenation without length prefixing is an established historical design in `models.py:858`. Under the schema freeze, modifying this calculation is prohibited because it would alter hash outputs and invalidate the entire historical audit chain.
- **Post-Freeze Migration Path**: When a future database schema migration is authorized (e.g. in Phase 6 / v3.0), the system can introduce an explicit `audit_version` column and transition to a fully length-prefixed encoding (such as native RFC 8785 envelope hashing or netstrings). The transition will be sealed by an attested migration checkpoint event (`AUDIT_CHAIN_ALGORITHM_MIGRATED`), allowing old records to verify under algorithm v1 while all subsequent records verify under algorithm v2.

---

### 2.3 Strict Demarcation: Legacy Format Migration vs Unexplained Tampering

To prevent whitewashing and ensure forensic integrity, an offline ledger mismatch is strictly separated into two mutually exclusive categories:

#### Category A: Explicitly Identified Legacy Format
An offline ledger is classified as an authentic legacy format **ONLY** if it satisfies all of the following positive criteria:
1. Every line parses as a valid JSON dictionary containing the exact 8-key Phase 5 Milestone 4.2 envelope schema (`action`, `actor_id`, `entity_id`, `entity_type`, `event_type`, `metadata`, `previous_event_hash`, `sequence_number`, `timestamp`) and `current_event_hash`.
2. For line 1, `previous_event_hash` is `""`.
3. For every line $k$, `current_event_hash` matches `compute_canonical_envelope_hash(line_envelope)` (SHA-256 over RFC 8785 canonical JSON bytes of the dictionary).
4. For every line $k > 1$, `previous_event_hash == line[k-1]["current_event_hash"]`.
5. The terminal line matches the signature in `.chkpt`.

**Migration Procedure**:
- An identified legacy ledger is **NEVER** silently rewritten during runtime startup or export.
- It requires a formal, auditable administrative migration command:
  ```bash
  clindoc-admin audit migrate-legacy-ledger --ledger=/path/to/ledger.jsonl
  ```
- **Execution**:
  1. Authenticates dual operators (`SECURITY_ADMIN` + `COMPLIANCE_OFFICER`).
  2. Executes streaming copy of the complete legacy file to `<ledger>.legacy.<iso_ts>`. Flushes, fsyncs, and verifies SHA-256.
  3. Records an immutable audit event `AUDIT_LEDGER_MIGRATED` at sequence $N+1$ in PostgreSQL.
  4. Regenerates `<ledger>.jsonl` from authoritative PostgreSQL events using `compute_audit_event_hash`.
  5. Updates `.chkpt` at sequence $N+1$.

#### Category B: Unknown or Unexplained Hash Mismatch
- Any ledger file that does not match both the authoritative hash chain and the positive legacy format criteria (e.g. sequence gap, altered payload, broken hash chain, unparseable line, or invalid signature).
- **Enforcement**:
  - **STRICT FAIL CLOSED**: Raises `AuditLedgerTamperedError`.
  - Automatic rebuild, silent recreation, or overwrite is **STRICTLY PROHIBITED**.
  - Forensic quarantine is executed to preserve evidence.
  - Gate 9 export and EHR dispatch are **HARD BLOCKED**.

---

## 3. Proof of Global Monotonic Sequence & Enforcement Boundaries

### 3.1 Exhaustive Audit of Codebase Insertion Paths
An exhaustive audit of the entire workspace repository confirms:
- **Single Instantiation Point**: `AuditEvent` is instantiated in exactly **one** place in the entire codebase: [`src/clindoc/api/audit/service.py:265`](file:///d:/daa/src/clindoc/api/audit/service.py#L265) inside `AuditService.record_event()`.
- **Zero Raw SQL Bypasses**: There are zero raw `INSERT INTO audit_events` statements in any Python source or test file.
- **Table-Wide Unique Constraint**: [`src/clindoc/db/models.py:811`](file:///d:/daa/src/clindoc/db/models.py#L811) enforces `UniqueConstraint("sequence_number", name="uq_audit_sequence_number")` across the entire database table.

### 3.2 Monotonic Sequence Allocation Under Global Advisory Lock
Every call to `AuditService.record_event()` executes inside the caller's database transaction:
```sql
SELECT pg_advisory_xact_lock(hashtext('clindoc_audit_ledger_global'));
```
Under this lock:
1. `AuditService` executes `SELECT sequence_number, current_event_hash FROM audit_events ORDER BY sequence_number DESC LIMIT 1`.
2. Next sequence is strictly assigned as $S_{\text{latest}} + 1$ (or 1 for genesis).
3. The row is inserted into the session.

### 3.3 Rollback Analysis & Gap Invariance
- If transaction $T_1$ allocates sequence $N+1$ and then rolls back:
  1. $T_1$ holds `pg_advisory_xact_lock` continuously. No concurrent transaction can query or allocate sequence numbers while $T_1$ is active.
  2. Upon rollback, PostgreSQL discards the uncommitted row at sequence $N+1$.
  3. The advisory lock is released by PostgreSQL kernel.
  4. Subsequent transaction $T_2$ acquires the advisory lock, reads the committed tail (which is still sequence $N$), and assigns sequence $N+1$.
  5. **Conclusion**: Uncommitted rollbacks consume zero sequence numbers and leave **zero gaps**. Strict contiguity $1, 2, ..., N$ is guaranteed.

### 3.4 Direct-Insert Bypass Analysis & Explicit Multi-Tier Enforcement Boundaries

It is technically essential to distinguish what is enforced by each layer and what remains undetectable without database triggers:

```
                            ENFORCEMENT BOUNDARIES
                            
     [Layer 1: Application Architecture]
     - 100% of internal writers route through `AuditService.record_event()`
     - Advisory lock `pg_advisory_xact_lock` acquired for every write
     - Guarantees sequence contiguity and hash chaining across application
                    │
                    ▼
     [Layer 2: PostgreSQL Database Engine Constraints]
     - `uq_audit_sequence_number`: Prevents duplicate sequence numbers
     - `uq_audit_current_event_hash`: Prevents duplicate event hashes
     - `ck_audit_sequence_positive`: Prevents non-positive sequences
     - LIMITATION: Does NOT check if advisory lock was held during INSERT!
                    │
                    ▼
     [Layer 3: Cryptographic Chain Verification (V-04, V-05)]
     - Detects sequence gaps (e.g. 1, 2, 4)
     - Detects broken hash links (H_prev != H_prior)
     - Detects payload tampering (recomputed SHA256 != stored hash)
     - LIMITATION: CANNOT detect a valid-looking contiguous insert committed
       out-of-band when no authorized worker is holding the lock!
                    │
                    ▼
     [Layer 4: Database-Level Trigger / Privilege Boundary (POST-FREEZE)]
     - Enforcing advisory lock acquisition at the SQL engine level requires a
       `BEFORE INSERT FOR EACH ROW` trigger or revoking table-level INSERT
       privileges in favor of security-definer stored procedures.
     - OUTSIDE CURRENT SCHEMA FREEZE: Requires DDL migrations.
```

- **What Remains Undetectable Under Frozen Schema**: If a privileged SQL user (e.g. DBA) executes an out-of-band `INSERT INTO audit_events` between authorized transactions, assigning sequence $N+1$ with a correctly calculated `current_event_hash` linking to $N$, this row will satisfy all database constraints and all cryptographic verification checks ($1..N+1$). Cryptographic verification cannot detect that the writer omitted the PostgreSQL advisory lock because the advisory lock is an ephemeral in-memory lock that leaves no cryptographic record in the event row. (Only semantic/business verifiers like V-03 detect it if the actor/claim ID does not match an authentic claim version history).
- **Test Scenarios**: TC-19 is partitioned into:
  - **TC-19a (Racing Bypass Insert)**: An unsynchronized direct SQL insert attempts to write while an authorized transaction holds `pg_advisory_xact_lock`. Tests that the race causes serialization blocking, sequence collision on `uq_audit_sequence_number`, or a broken hash chain, failing closed.
  - **TC-19b (Sequential Out-of-Band Direct Insert)**: An unsynchronized direct SQL insert executes *between* authorized transactions, inserting a contiguous sequence $N+1$ with a validly calculated hash without acquiring the advisory lock. Demonstrates that this row commits and passes cryptographic hash verification ($1..N+1$), proving that advisory lock acquisition is an application-tier guarantee rather than a database-engine-enforced constraint under the frozen schema.

---

## 4. Refined Multi-Tier Crash Consistency & Persistence Matrix

Persistence guarantees are categorized across four operational failure tiers:
1. **Application Process Termination (`SIGKILL`, OOM, abnormal exit)**: The OS kernel continues running. File handles are closed by the kernel. Unwritten user-space stdio/libc buffers are discarded immediately and never reach the kernel page cache. Dirty OS page cache pages remain in memory and are asynchronously scheduled for writeback by kernel threads. **Crucial Durability Principle**: Data in the OS page cache is **NOT** physically durable; a kernel panic or power loss occurring before writeback will destroy un-fsynced pages.
2. **OS / Kernel Panic**: Operating system page cache is destroyed. Only data explicitly flushed to storage controller buffers via `os.fsync()` survives.
3. **Sudden Hardware Power Loss**: Drive controller volatile write buffers are lost unless the drive features Battery-Backed Write Cache (BBWC) or Power-Loss Protection (PLP). `os.fsync()` issues OS flush primitives (`FlushFileBuffers` on Windows, `fsync` on POSIX) pushing blocks to non-volatile medium.
4. **Filesystem Directory Entry Durability (POSIX vs Windows NTFS)**:
   - **POSIX**: File data is flushed via `fsync(file_fd)`. Creating, renaming (`os.replace`), or unlinking files modifies the parent directory inode. To guarantee directory metadata durability across power loss, POSIX requires explicitly calling `fsync(dir_fd)` on the parent directory handle.
   - **Windows NTFS**: Windows does not support `fsync` on directory handles (raising `PermissionError` or `OSError`). NTFS uses a journaled metadata architecture (`$LogFile` and USN Journal). When `os.replace` (`MoveFileExW`) completes, the metadata change is journaled in memory. While `FlushFileBuffers` on a file handle commits file data and direct MFT record attributes, Windows flushes the parent directory and `$LogFile` during periodic lazy-writer sweeps (typically 1 to 5 seconds).
   - **Power-Loss Reality on Windows**: If sudden power loss occurs immediately after `os.replace` but before the NTFS lazy-writer flushes to disk, the directory entry update may revert to the pre-rename state upon NTFS journal recovery at reboot.
   - **Deployment Requirement**: Enterprise deployments requiring absolute power-loss durability on Windows must use storage controllers with battery-backed cache or enterprise SSDs with hardware Power-Loss Protection (PLP). Consumer SSDs without PLP are classified as best-effort across power loss (though fully crash-consistent across process `SIGKILL` and clean OS reboot).

### 4.1 Exhaustive Boundary Matrix

| Boundary ID | Execution Step | Process Termination (`SIGKILL`) State | Kernel Panic / Power Loss State | Recovery Action on Next Invocation | Gate 9 Export Verdict |
| :---: | :--- | :--- | :--- | :--- | :---: |
| **B-1** | Before PostgreSQL transaction begins | DB unchanged; JSONL unchanged. | DB unchanged; JSONL unchanged. | None. Operation unstarted. | **PERMITTED** |
| **B-2** | After acquiring advisory lock, before write | DB unchanged; lock auto-released by PostgreSQL kernel. | DB unchanged; lock released on connection drop. | None. Advisory lock released automatically by PostgreSQL. | **PERMITTED** |
| **B-3** | After inserting `AuditEvent` in session, before `commit()` | PostgreSQL server transaction rolls back on connection drop; DB has $N$; JSONL has $N$. | Server rolls back on restart. DB has $N$; JSONL has $N$. | None. Uncommitted database row never materialized. | **PERMITTED** |
| **B-4** | Immediately after PostgreSQL `session.commit()` | PostgreSQL contains committed sequence $N+1$; JSONL has sequence $N$. | PostgreSQL WAL replay ensures sequence $N+1$ committed; JSONL has $N$. | **Automatic Reconciled Outbox Replay**: Detects $S_{\text{db}} > S_{\text{file}}$; fetches row $N+1$ from DB, appends to JSONL, fsyncs, updates checkpoint. | **BLOCKED** until replay completes; **PERMITTED** immediately after replay. |
| **B-5** | Before JSONL projection append (`<ledger>.lock` held) | PostgreSQL has $N+1$; JSONL has $N$. `.lock` file remains on disk. | Same as process termination. | `FileLock.acquire()` succeeds immediately because dead process handle was closed by OS kernel; outbox replay runs. | **BLOCKED** during recovery; **PERMITTED** post-replay. |
| **B-6** | During partial/torn JSONL append | JSONL has valid lines $1..N$ followed by incomplete bytes at EOF. | Same as process termination. | **Streaming Forensic Quarantine**: Quarantine entire file to `.corrupt.<ts>`; verify byte count & SHA-256; on Windows truncate tail under kernel write-lockout; on POSIX preserve original and require administrative recovery. | **BLOCKED** during quarantine; **PERMITTED** post-recovery. |
| **B-7** | After JSONL append, before `os.fsync()` | Unwritten libc buffers lost; in-flight OS page cache. Three outcomes: (1) line missing, (2) partial/torn at EOF, (3) complete line flushed. | Dirty buffer lost; line missing or torn at EOF. Same three outcomes. | If missing: B-4 replay. If torn: B-6 quarantine. If complete: verify line matches DB row $\to$ explicit `fsync()` $\to$ advance checkpoint. | **BLOCKED** until verified & checkpoint updated; **PERMITTED** post-reconciliation. |
| **B-8** | After `os.fsync()`, before `.chkpt` update | PostgreSQL has $N+1$; JSONL has $N+1$; `.chkpt` has sequence $N$. | Same as process termination. | **Attested Checkpoint Advancement**: Checkpoint at $N$ is valid; full-chain DB/JSONL verification passes up to $N+1$; advance checkpoint. | **BLOCKED** until checkpoint updated; **PERMITTED** post-update. |
| **B-9** | During checkpoint temporary-file write (`.chkpt.tmp.<pid>`) | Target `.chkpt` untouched at sequence $N$; orphan `.tmp` exists on disk. | Same as process termination. | Clean up orphan `.tmp` file; advance checkpoint from valid state B-8 via atomic rename. | **BLOCKED** during cleanup; **PERMITTED** post-update. |
| **B-10** | During atomic replace `os.replace(.chkpt.tmp, .chkpt)` | Filesystem directory entry points atomically to old or new inode. | Same. | If old inode: checkpoint at $N$, handled as B-8. If new inode: checkpoint at $N+1$, recovery complete. | **BLOCKED** until verified. |
| **B-11** | After checkpoint replace, before directory fsync | Directory metadata flushed by kernel writeback. | Directory entry might point to old inode; handled as B-8. | On POSIX, directory `fsync()` called explicitly; on Windows NTFS, directory entry change is journaled. | **PERMITTED** once verified. |
| **B-Q-1** | Crash during quarantine temporary file copy (`.corrupt.tmp.<pid>`) | Original ledger untouched; incomplete `.tmp` quarantine file exists. | Same. | Delete orphaned `.tmp` quarantine file. Re-attempt quarantine under active file lock. | **BLOCKED** during quarantine. |
| **B-Q-2** | Crash after quarantine replace, before ledger truncation | Quarantine file `.corrupt.<ts>` durably exists; original ledger still has torn bytes. | Same. | Detect torn bytes; observe existing quarantine; complete truncation to line $N$ (Windows) or await admin recovery (POSIX); replay $N+1$. | **BLOCKED** during recovery. |
| **B-Q-3** | Crash during ledger truncation or outbox replay | Ledger truncated or partially replayed. | Same. | Handled as B-4 or B-6. Original evidence safely preserved in quarantine. | **BLOCKED** during recovery. |
| **B-GEN-0** | Never-Created Genesis Ledger | DB has 0 rows; JSONL does not exist; `.chkpt` does not exist. | Same. | Benign initial state. First audit write creates genesis event 1. | **PERMITTED** (empty ledger) |
| **B-ANOM-0** | Missing / Deleted Ledger with Existing DB History | DB contains $N > 0$ committed events, but `<ledger>.jsonl` is missing. | Same. | **FAIL CLOSED**: Potential malicious file deletion. Automatic silent recreation is **STRICTLY PROHIBITED**. Require administrative authorization to rebuild. | **HARD BLOCKED** |
| **B-ANOM-1** | Split-Brain ($S_{\text{file}} > S_{\text{db}}$) | JSONL has records unknown to PostgreSQL (e.g. older DB backup restored). | Same. | **FAIL CLOSED**: Raise `SplitBrainLedgerError`. Quarantine file. Automatic overwrite strictly prohibited. | **HARD BLOCKED** |
| **B-ANOM-2** | Sequence Gap or Duplicate in JSONL | File damaged or edited out-of-band. | Same. | **FAIL CLOSED**: Raise `AuditLedgerGapError`. Quarantine file. Automatic repair prohibited. | **HARD BLOCKED** |
| **B-ANOM-3** | Hash Mismatch Between DB and JSONL | Data tampering in DB or JSONL. | Same. | **FAIL CLOSED**: Raise `AuditLedgerTamperedError`. Quarantine file. | **HARD BLOCKED** |
| **B-ANOM-4** | Middle Sequence Corruption in JSONL | Line $k < N$ is unparseable or tampered. | Same. | **FAIL CLOSED**: Raise `AuditLedgerTamperedError`. Quarantine file. Must NEVER be treated as a torn tail! | **HARD BLOCKED** |
| **B-ANOM-5** | Malformed or Signature-Invalid Checkpoint | `.chkpt` JSON unparseable or HMAC signature fails. | Same. | **FAIL CLOSED**: Forensic quarantine of `.chkpt`. Automatic regeneration prohibited. | **HARD BLOCKED** |

---

## 5. Corrected Forensic Quarantine Protocol

### 5.1 Independent Verification, Platform-Safe Truncation & Non-Cooperative Writer Policies
Addressing concurrency and potential race conditions during forensic quarantine requires strict distinction between operating system platforms:
- **Windows (Kernel-Enforced Mandatory Write-Sharing Denial via Win32 API)**:
  The Windows NT kernel provides mandatory file sharing control. Ordinary Python `open()` calls Win32 `CreateFileW` with `FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`, which does not deny write sharing. To enforce mandatory write lockout, the source handle is created via `_winapi.CreateFile` explicitly setting `dwShareMode = FILE_SHARE_READ` (denying `FILE_SHARE_WRITE`). Under the NT kernel, any concurrent process attempting to open the file with write access receives `ERROR_SHARING_VIOLATION` (0x20). The handle is wrapped using `msvcrt.open_osfhandle(win_handle, os.O_RDWR | os.O_BINARY)` into Python's `open(fd, "r+b")`. Under this kernel lockout, truncation of verified torn bytes is safe against external writers.
- **POSIX (Fail-Closed Policy — Non-Cooperative Writer Boundary & Prohibited Truncation)**:
  On POSIX/Linux, standard user-space file locking (`fcntl`) is advisory; it does not prevent non-cooperative external writers from appending to an open inode. Crucially, **a second source read and metadata comparison do NOT eliminate every possible race**: an external non-cooperative writer may append bytes after the post-copy source verification but before `os.replace()`, or during quarantine finalization and directory synchronization. Because user space cannot exclude concurrent appends throughout the entire operation without kernel-level write-exclusion, the POSIX quarantine protocol enforces strict fail-closed forensic preservation:
  * **Original POSIX Source Never Truncated Automatically**: Automatic truncation of the original POSIX ledger file is strictly prohibited under all circumstances.
  * **Authoritative Forensic Evidence Preserved**: The original source file remains the authoritative forensic evidence and is preserved **AS-IS** in place without modification.
  * **Quarantine Completeness Guarantee Boundary**: A quarantine copy must **NOT** be certified as a complete or authoritative snapshot if concurrent modification cannot be excluded throughout the entire copy, verification, and replacement sequence.
  * **Fail-Closed Unquiesced Handling**: If concurrent modification is detected at any point, or if completeness cannot be guaranteed (which is inherently the case on POSIX when unquiesced non-cooperative writers are active), the system:
    1. Retains the original source file completely intact and un-truncated.
    2. Marks the quarantine copy as incomplete/untrusted (`.corrupt.<iso_ts>.untrusted` or `.corrupt.<iso_ts>.unverified`).
    3. Fails closed by raising `UnquiescedLedgerModificationError`.
    4. Hard-blocks Gate 9 export and EHR dispatch.
    5. Requires verified process quiescence (pipeline services stopped or exclusive storage access) before administrative recovery (`clindoc-admin audit recover-torn-tail`) can be authorized.

#### Step-by-Step Protocol:
1. **Cooperative Concurrency Protection**: Executed strictly under kernel file lock $L_3$.
2. **Platform-Specific Source Handle & Write Protection**:
   - On Windows: Opened via `_winapi.CreateFile` with `FILE_SHARE_READ` (denying `FILE_SHARE_WRITE`).
   - On POSIX: Opened with `r+b`. Automatic truncation is strictly prohibited.
3. **Descriptor Read/Write Mode**: Opened in binary read/write mode (`r+b`). On Windows, truncation is performed through `f_src.truncate(valid_tail_offset)` on the exact open descriptor, avoiding descriptor recreation.
4. **Single Open Source Handle & File Identity Verification**:
   - The source file is opened **once** and held open continuously throughout verification, quarantine copy, and truncation.
   - File identity is recorded:
     * On POSIX: `stat_init = os.fstat(f_src.fileno())`, recording `(stat_init.st_dev, stat_init.st_ino, stat_init.st_size, stat_init.st_mtime_ns)`.
     * On Windows: `stat_init = os.fstat(f_src.fileno())`. In Python 3.8+ on Windows, `st_ino` contains the 64-bit NTFS file index and `st_dev` contains the volume serial number. If `st_ino == 0` (indicating a legacy filesystem lacking unique file indices), the system **fails closed** (`UnsupportedFilesystemError`).
5. **Stream-Copy with Checksumming**: Copies `f_src` to `.tmp.<pid>` in 64 KB chunks, computing `src_copy_hasher` and tracking `bytes_copied`. Calls `f_dst.flush()`, `os.fsync(f_dst.fileno())`, and closes `f_dst`.
6. **Independent Destination Disk Verification**: Reopens the closed temporary quarantine file from disk (`f_dst_verify = open(tmp_quarantine_path, "rb")`), streams it, and independently computes `dst_hasher` and `dst_bytes`. Asserts `dst_hasher == src_copy_hasher` and `dst_bytes == bytes_copied`.
7. **Post-Copy Source Re-Read & SHA-256 Verification**:
   - Seeks `f_src.seek(0)`.
   - Streams `f_src` from disk a second time to compute `src_post_hasher` and `src_post_bytes`.
   - Asserts `src_post_hasher == src_copy_hasher` and `src_post_bytes == bytes_copied`.
   - Re-checks `stat_final = os.fstat(f_src.fileno())` and path `ledger_path.stat()`. Asserts device and inode match `stat_init`.
   - If any discrepancy is detected, raises `ExternalConcurrentModificationError`, deletes `tmp_quarantine_path`, and aborts **without modifying or truncating the original ledger**.
8. **Finalizing Quarantine & POSIX Post-Replace Verification**:
   - `os.replace(tmp_quarantine_path, quarantine_path)` is executed first. On POSIX, `fsync(dir_fd)` is called.
   - On POSIX, source descriptor is immediately inspected after rename: `stat_post_replace = os.fstat(f_src.fileno())`. If `stat_post_replace.st_size != stat_init.st_size` or `stat_post_replace.st_mtime_ns != stat_init.st_mtime_ns`, a concurrent write occurred during finalization. The quarantine file is renamed to `.corrupt.<iso_ts>.untrusted`, the original file is preserved AS-IS, and `UnquiescedLedgerModificationError` is raised.
9. **Truncation Execution**:
   - On Windows: for a verified torn tail, `f_src.seek(valid_tail_offset)` followed by `f_src.truncate(valid_tail_offset)` is executed under kernel write-lockout. `f_src.flush()` and `os.fsync(f_src.fileno())` commit the truncation.
   - On POSIX: automatic truncation is prohibited; raises `UnquiescedLedgerModificationError`.

```python
def execute_verified_forensic_quarantine(
    ledger_path: Path,
    valid_tail_offset: Optional[int] = None,  # Byte offset of end of valid lines for torn-tail truncation
    chunk_size: int = 65536,  # 64 KB streaming chunks
) -> Path:
    """Execute verified forensic quarantine with independent disk verification and platform-safe truncation.
    
    Guarantees:
    - Original POSIX source is NEVER truncated automatically and remains authoritative forensic evidence.
    - Quarantine copy is NOT certified as a complete snapshot if concurrent modification cannot be excluded.
    - If modification is detected or completeness cannot be guaranteed, retains original, marks quarantine
      untrusted/incomplete, fails closed, and requires verified process quiescence for administrative recovery.
    - On Windows, mandatory write-sharing denial (FILE_SHARE_READ) prevents external writes throughout operation.
    """
    iso_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_path = ledger_path.with_name(f"{ledger_path.name}.corrupt.{iso_ts}")
    tmp_quarantine_path = quarantine_path.with_name(f"{quarantine_path.name}.tmp.{os.getpid()}")

    src_copy_hasher = hashlib.sha256()
    bytes_copied = 0

    try:
        # 1. Open source handle in binary read/write mode ("r+b")
        # On Windows: Open via Win32 API with FILE_SHARE_READ (0x1) to deny external write sharing
        # On POSIX: Open with "r+b" under cooperative FileLock
        if os.name == "nt":
            import msvcrt
            import _winapi
            # GENERIC_READ (0x80000000) | GENERIC_WRITE (0x40000000)
            # FILE_SHARE_READ (0x00000001) denies FILE_SHARE_WRITE and FILE_SHARE_DELETE
            win_handle = _winapi.CreateFile(
                str(ledger_path),
                0x80000000 | 0x40000000,
                0x00000001,
                0,
                3,  # OPEN_EXISTING
                0x80,  # FILE_ATTRIBUTE_NORMAL
                0,
            )
            fd = msvcrt.open_osfhandle(win_handle, os.O_RDWR | os.O_BINARY)
            f_src = open(fd, "r+b", closefd=True)
        else:
            f_src = open(ledger_path, "r+b")

        with f_src:
            stat_init = os.fstat(f_src.fileno())
            if os.name == "nt" and stat_init.st_ino == 0:
                raise UnsupportedFilesystemError("Filesystem does not support unique 64-bit file identifiers.")

            # 2. Stream-copy to temporary destination
            with open(tmp_quarantine_path, "wb") as f_dst:
                while True:
                    chunk = f_src.read(chunk_size)
                    if not chunk:
                        break
                    src_copy_hasher.update(chunk)
                    f_dst.write(chunk)
                    bytes_copied += len(chunk)

                f_dst.flush()
                os.fsync(f_dst.fileno())

            # 3. Independently reopen destination from disk and compute byte count & SHA-256
            dst_hasher = hashlib.sha256()
            dst_bytes = 0
            with open(tmp_quarantine_path, "rb") as f_dst_verify:
                while True:
                    chunk = f_dst_verify.read(chunk_size)
                    if not chunk:
                        break
                    dst_hasher.update(chunk)
                    dst_bytes += len(chunk)

            if dst_hasher.hexdigest() != src_copy_hasher.hexdigest() or dst_bytes != bytes_copied:
                raise ForensicQuarantineVerificationError(
                    f"Independent destination verification failed: copied={bytes_copied}, verified={dst_bytes}"
                )

            # 4. Post-copy source re-read: rewind open source handle and verify complete bytes
            f_src.seek(0)
            src_post_hasher = hashlib.sha256()
            src_post_bytes = 0
            while True:
                chunk = f_src.read(chunk_size)
                if not chunk:
                    break
                src_post_hasher.update(chunk)
                src_post_bytes += len(chunk)

            stat_final = os.fstat(f_src.fileno())
            path_stat_final = ledger_path.stat()

            # Verify source content and identity remained identical throughout copy
            if (
                src_post_hasher.hexdigest() != src_copy_hasher.hexdigest()
                or src_post_bytes != bytes_copied
                or stat_final.st_mtime_ns != stat_init.st_mtime_ns
                or stat_final.st_dev != stat_init.st_dev
                or stat_final.st_ino != stat_init.st_ino
                or path_stat_final.st_ino != stat_init.st_ino
            ):
                raise ExternalConcurrentModificationError(
                    f"Source file {ledger_path} was modified concurrently outside cooperative lock during quarantine."
                )

            # 5. Finalize quarantine copy via atomic rename before modifying original
            os.replace(tmp_quarantine_path, quarantine_path)
            if os.name != "nt":
                dir_fd = os.open(str(ledger_path.parent), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)

                # POSIX Quarantine Finalization Boundary Check:
                # Check whether non-cooperative writer appended after source re-read but before/during replace
                stat_post_replace = os.fstat(f_src.fileno())
                if (
                    stat_post_replace.st_size != stat_init.st_size
                    or stat_post_replace.st_mtime_ns != stat_init.st_mtime_ns
                ):
                    untrusted_quarantine = quarantine_path.with_name(f"{quarantine_path.name}.untrusted")
                    os.replace(quarantine_path, untrusted_quarantine)
                    raise UnquiescedLedgerModificationError(
                        f"Non-cooperative writer appended to {ledger_path} during quarantine finalization. "
                        f"Quarantine marked untrusted ({untrusted_quarantine}). "
                        f"Original file preserved AS-IS. Process quiescence required for recovery."
                    )

            # 6. Platform-Specific Truncation Policy
            if valid_tail_offset is not None:
                if os.name == "nt":
                    # Windows: Kernel denies FILE_SHARE_WRITE; safe to truncate under lock
                    f_src.seek(valid_tail_offset)
                    f_src.truncate(valid_tail_offset)
                    f_src.flush()
                    os.fsync(f_src.fileno())
                else:
                    # POSIX: Fail closed. Automatic truncation strictly prohibited without kernel write-exclusion.
                    raise UnquiescedLedgerModificationError(
                        f"Automatic truncation on POSIX is prohibited without kernel write-exclusion. "
                        f"Original file preserved AS-IS. Quarantine created at {quarantine_path}. "
                        f"Execute administrative quiescence recovery to truncate."
                    )

        return quarantine_path

    except Exception:
        if tmp_quarantine_path.exists():
            try:
                tmp_quarantine_path.unlink()
            except OSError:
                pass
        raise

```

---

## 6. Centralized DB-to-JSONL Outbox Projection Architecture

### 6.1 Architectural Mandate & Retirement of W-02
1. `AuditService` is designated the **Sole Authoritative Audit Writing Service**.
2. **W-02 Direct Append Formally Retired**: In legacy code, [`src/clindoc/review/service.py:1276`](file:///d:/daa/src/clindoc/review/service.py#L1276) attempted a direct un-synchronized append to `<ledger>.jsonl`. This dual-write was the fundamental root cause of DEF-003 split-brain. Under Step 3, direct JSONL appending in review workers is **completely eliminated**.
3. All audit writes across all 13 retained production audit writer paths route exclusively through `AuditService.record_event()` inside the caller's transaction under advisory lock $L_2$.
4. Projection to JSONL is centralized: upon database commit, `AuditProjectionEngine` synchronizes committed events to JSONL under file lock $L_3$.

### 6.2 The Post-Commit Outbox Reconciliation Lifecycle
To handle concurrent writers racing (e.g. Writer B committing sequence $N+2$ while Writer A is in the process of committing $N+1$):

```
                             OUTBOX RECONCILIATION
                                       │
                      Acquire `<ledger>.lock` File Lock
                                       │
                       Query Committed DB Sequence Max:
                       `S_db = MAX(sequence_number)`
                                       │
                       Inspect Projection Tail Sequence:
                       `S_file = tail_sequence(jsonl)`
                                       │
                        Is S_db > S_file (Lagging)?
                                ├── NO ──> Release `<ledger>.lock`; Return.
                                └── YES ─> Continue
                                       │
                    Fetch ALL Missing Committed Events:
            `SELECT * FROM audit_events WHERE sequence_number > S_file ORDER BY sequence_number ASC`
                                       │
                    For Each Missing Event (e.g. N+1, N+2):
                    - Format authoritative JSON line using compute_audit_event_hash
                    - Append line + '\n' to `<ledger>.jsonl`
                                       │
                    Call `f.flush()` followed by `os.fsync(f.fileno())`
                                       │
                    Update HMAC Checkpoint to S_db via atomic .tmp replace
                                       │
                    Release `<ledger>.lock` File Lock
```

- **Race Freedom Proof**: If Writer B ($N+2$) reaches projection before Writer A ($N+1$), Writer B's reconciliation query fetches BOTH $N+1$ and $N+2$ from PostgreSQL (since both are committed in DB). Writer B writes both in strict order $N+1$ then $N+2$. When Writer A subsequently acquires the file lock, it observes $S_{\text{file}} \ge N+1$ and safely no-ops.

---

## 7. Gate 9 Export Signing, Bundling & Three-Tier Attestation Integrity

### 7.1 Formal Separation of Three Distinct Bundle Digests & Bundle.signature Verification Contract
To prevent circular dependencies while eliminating ambiguity between clinical content, signature envelopes, and transport payloads, the architecture establishes three strictly separated, independent integrity tiers:

```
                          THREE-TIER BUNDLE INTEGRITY
                          
     [Tier 1: Clinical Content]
     - Canonical Document Bundle (Composition, Patient, clinical resources)
     - EXCLUDES: Bundle.signature, audit provenance extension
     - Serialization: RFC 8785 Canonical JSON over clinical tree
     - Digest: `clinical_content_sha256 = SHA256(canonical_clinical_bytes)`
     - Recorded in: `AuditEvent.metadata_json["clinical_content_sha256"]`
                    │
                    ▼
     [Tier 2: Augmented Signed Bundle Envelope]
     - Clinical Bundle + Embedded Audit Provenance Extension (Seq N+1, Hash, Digest)
     - Signed by: `Bundle.signature` (RS256 / ES256 JWS) over canonical augmented bundle
     - EXCLUDES ONLY: signature.data itself per FHIR standard
     - Signer Authentication: Public-key cryptography against trusted JWKS/CA store
     - Persisted in: `fhir_bundles.bundle_payload` (JSONB)
                    │
                    ▼
     [Tier 3: Exact Transport Wire Payload]
     - Serialized UTF-8 byte stream sent over MLLP / HTTPS or returned in HTTP 200
     - HTTPS / HTTP Export: Exact serialized UTF-8 bytes (transport framing excluded)
     - MLLP over TCP: Exact inner payload bytes (framing <VT> and <FS><CR> EXCLUDED)
     - Digest: `transport_sha256 = SHA256(exact_immutable_payload_bytes)`
     - Verified at wire boundary: `SHA256(wire_payload_bytes) == transport_sha256`
     - Recorded in: `FHIRExport.idempotency_key = f"ehr-{doc_uuid}-{transport_sha256}"`
```

#### Detailed Definitions & Verification Contracts:

#### 1. Tier 1 — Pre-Audit Clinical Content Digest (`clinical_content_sha256`)
- **Hashed Content**: Canonical Clinical Document Bundle (Composition, Patient, Encounter, Practitioner, and clinical entries).
- **Explicit Exclusions**: `Bundle.signature` and the audit provenance extension (`http://clindoc.internal/fhir/StructureDefinition/audit-provenance`).
- **Serialization & Canonicalization**: RFC 8785 Canonical JSON (`application/canonical-json`) encoded in UTF-8 bytes.
- **Purpose**: Cryptographic fingerprint of pure clinical assertions prior to audit sealing.
- **Persisted in**: `AuditEvent(event_type="FHIR_BUNDLE_EXPORTED", metadata_json={"clinical_content_sha256": ...})`.

#### 2. Tier 2 — Augmented Signed Bundle Envelope & `Bundle.signature` Verification Contract
- **Embedded Audit Provenance Extension**:
  The clinical bundle is augmented with an immutable audit provenance extension linking it to the PostgreSQL audit event:
  ```json
  {
    "url": "http://clindoc.internal/fhir/StructureDefinition/audit-provenance",
    "extension": [
      {"url": "auditSequenceNumber", "valueInteger": 105},
      {"url": "auditEventHash", "valueString": "a591a32b0f488f..."},
      {"url": "clinicalContentDigest", "valueString": "8f4a12bc..."}
    ]
  }
  ```
- **Signature Algorithm & Format**:
  * **Standard**: RFC 7515 JSON Web Signature (JWS Compact Serialization) represented within HL7 FHIR R4B `Bundle.signature` (`sigFormat = "application/jose"`).
  * **Supported Algorithms**: `RS256` (RSASSA-PKCS1-v1_5 with SHA-256 using minimum 2048-bit RSA keys) and `ES256` (ECDSA using NIST P-256 curve with SHA-256).
  * Insecure algorithms (`none`, symmetric HMAC keys, RSA < 2048 bits, SHA-1) are explicitly rejected and fail closed.
  * **`Bundle.signature` Element Structure**:
    ```json
    {
      "type": [
        {
          "system": "urn:iso-astm:E1762-95:2013",
          "code": "1.2.840.10065.1.12.1.1",
          "display": "Author's Signature"
        }
      ],
      "when": "2026-11-01T12:00:00Z",
      "who": {
        "reference": "Organization/clindoc-authoritative-export"
      },
      "sigFormat": "application/jose",
      "data": "<base64url_encoded_jws_signature>"
    }
    ```
  * **JWS Protected Header**: Base64url-encoded JSON object containing `{"alg": "RS256"|"ES256", "typ": "JOSE", "kid": "<key-id>"}`.
- **Exact Signed Representation & Canonicalization**:
  * The signed data is the canonical JSON of the entire augmented bundle (including the audit provenance extension), with `Bundle.signature.data` stripped/omitted per the FHIR R4B specification.
  * **Canonicalization Standard**: RFC 8785 Canonical JSON (`application/canonical-json`) to guarantee bitwise deterministic representation across runtimes (lexicographical sorting of JSON keys by UTF-16 code units, integers without trailing `.0`, minimal whitespace, UTF-8 without BOM).
  * **JWS Signing Input**: `ASCII(base64url(protected_header)) + "." + ASCII(base64url(canonical_augmented_bundle_bytes))`.
- **Trusted Public-Key / Certificate Acquisition**:
  * Signing keys are acquired exclusively from trusted administrative sources:
    - Local trusted X.509 certificate authority store (`/etc/clindoc/certs/export_signing_ca.crt` or Windows Certificate Store `LocalMachine\Root`).
    - Or an internal, pre-pinned JWKS endpoint (`CLINDOC_FHIR_SIGNING_JWKS_URL`) authenticated via mutual TLS (mTLS).
  * The verifier matches the `kid` from the JWS protected header against trusted keys in the trust store.
  * Public keys are NEVER retrieved from untrusted request inputs or arbitrary internet endpoints.
  * For X.509 certificate-backed keys, the full certificate path is validated against the pinned root CA.
- **Key Rotation and Revocation Handling**:
  * **Key Rotation**: The JWKS / trust store supports multiple concurrent valid keys indexed by unique `kid`. Active signing keys can be rotated smoothly without invalidating historical bundles signed with prior valid keys.
  * **Revocation**: The verification engine validates certificate validity timestamps (`notBefore <= current_utc <= notAfter`) and enforces revocation checks via Certificate Revocation Lists (CRL) or Online Certificate Status Protocol (OCSP).
  * Expired or revoked certificates are immediately rejected.
- **Fail-Closed Verification Policy**:
  * Missing signature: Raises `MissingBundleSignatureError`.
  * Malformed signature (corrupt base64url, invalid JWS header, unrecognized `alg`): Raises `MalformedBundleSignatureError`.
  * Cryptographically invalid signature (signature does not verify against public key): Raises `InvalidBundleSignatureError`.
  * Untrusted key or certificate (unrecognized `kid`, expired cert, revoked cert, CA chain failure): Raises `UntrustedSigningKeyError`.
  * Broken audit provenance binding (embedded sequence/hash does not match DB/JSONL): Raises `AuditProvenanceBindingMismatchError`.
  * Any failure strictly halts export download or EHR dispatch, suppresses bundle release, and logs a security incident.
- **Binding to Augmented Bundle and Audit Provenance (Signer Authentication vs Digest Equality)**:
  * **Signer Authentication Distinction**: Crucially, SHA-256 digest equality alone proves only data integrity/unaltered bits; it does NOT authenticate the signer or prove legal authorization. Only valid cryptographic signature verification under a trusted root public key authenticates the signing organization.
  * Verification binds the signature to:
    1. The clinical content of the bundle.
    2. The embedded audit provenance extension (`auditSequenceNumber`, `auditEventHash`, `clinicalContentDigest`).
    3. The authoritative PostgreSQL audit chain and offline sealed HMAC checkpoint.
- Persisted in: `fhir_bundles.bundle_payload` (JSONB).

#### 3. Tier 3 — Exact Transport Wire Byte Binding (`transport_sha256`)
**Critical Distinction**: Hashing an in-memory object does NOT prove the bytes eventually transmitted or returned are identical. In-memory object hashing only proves the state of the object at the moment of hashing. True transport binding requires serializing the payload **once** into an immutable `bytes` buffer, computing `transport_sha256` over those exact bytes, passing those identical bytes to the transport or client without re-serialization, and verifying them at the physical transport boundary.

Exact byte sequence definitions across transport modes:
- **HTTPS FHIR Payloads** (`EHRIntegrationService.send_fhir_bundle_to_ehr` over HTTPS POST):
  * **Framing**: HTTP request headers, TLS records, and TCP framing are **EXCLUDED**.
  * **Exact Byte Sequence**: The exact, immutable serialized UTF-8 bytes of the augmented FHIR Bundle JSON body (`Content-Type: application/fhir+json; charset=utf-8`).
  * **Serialization & Binding**: Serialized *once* in memory:
    ```python
    exact_payload_bytes = json.dumps(augmented_bundle, separators=(",", ":"), sort_keys=False).encode("utf-8")
    transport_sha256 = hashlib.sha256(exact_payload_bytes).hexdigest()
    ```
  * **Exact Bytes Passed to Transport**: The *identical* immutable `exact_payload_bytes` buffer is passed directly to the HTTP transport client (e.g. `requests.post(url, data=exact_payload_bytes)` or `httpx.post(url, content=exact_payload_bytes)`), strictly prohibiting client library or framework re-serialization.
  * **Transport Boundary Verification**: A custom transport adapter or request hook captures the raw byte stream as it leaves the application layer across the socket boundary and asserts `SHA256(socket_bytes) == transport_sha256`.
- **HTTP Export Downloads** (`GET /documents/{document_id}/export` endpoint in `documents.py`):
  * **Framing**: HTTP protocol headers, status line, and chunked transfer encoding are **EXCLUDED**. Only the HTTP response entity body bytes are covered.
  * **Exact Byte Sequence**: The exact, immutable serialized UTF-8 bytes of the exported FHIR Bundle JSON response body.
  * **Serialization & Binding**: Serialized *once* in memory:
    ```python
    exact_body_bytes = json.dumps(augmented_bundle, separators=(",", ":"), sort_keys=False).encode("utf-8")
    transport_sha256 = hashlib.sha256(exact_body_bytes).hexdigest()
    ```
  * **Exact Bytes Returned to Client**: The *identical* `exact_body_bytes` buffer is returned directly as raw bytes:
    ```python
    return Response(
        content=exact_body_bytes,
        media_type="application/fhir+json",
        headers={"X-Transport-SHA256": transport_sha256},
    )
    ```
    This prevents FastAPI/Starlette from re-serializing an in-memory model or dictionary.
  * **Transport Boundary Verification**: An ASGI response boundary filter asserts that the byte buffer emitted to the client stream matches `transport_sha256`.
- **MLLP-Framed Messages** (`EHRIntegrationService.send_fhir_bundle_to_ehr` via MLLP over TCP):
  * **Framing**: **Framing bytes are EXPLICITLY EXCLUDED from `transport_sha256`**.
    - Framing standard: Leading Start Block `<VT>` (`0x0B`, 1 byte) and trailing End Block `<FS><CR>` (`0x1C 0x0D`, 2 bytes).
    - `transport_sha256` covers *strictly the inner payload bytes* between `<VT>` and `<FS><CR>`.
  * **Exact Byte Sequence**: The exact immutable UTF-8 bytes of the inner HL7/FHIR message payload `inner_bytes = bundle_json_str.encode("utf-8")`.
  * **Wire Construction**: The transport socket writer constructs the wire stream as `wire_stream = b"\x0b" + inner_bytes + b"\x1c\r"`.
  * **Transport Boundary Verification**: In the dedicated socket transport writer, immediately before issuing the `socket.sendall(wire_stream)` kernel system call, the writer slices `wire_stream[1:-2]`, computes `SHA256(wire_stream[1:-2])`, and asserts that it strictly equals `transport_sha256`. If any interceptor, socket middleware, or in-memory corruption alters the payload, transmission fails closed (`EHRBundlePayloadTamperedError`) and no bytes are written to the socket.

#### Non-Circular Independent Verification Workflow:
1. **Cryptographic Signature Authentication**: Validates `Bundle.signature` over the RFC 8785 canonical augmented bundle bytes (excluding `signature.data`) against trusted public key in trust store. Verifies signer identity, certificate chain, and revocation status.
2. **Provenance Linkage**: Extracts `auditSequenceNumber` and `auditEventHash` from the embedded extension. Asserts `auditEventHash` matches sequence $N+1$ in PostgreSQL, matches `<ledger>.jsonl`, and is sealed by the HMAC checkpoint.
3. **Clinical Content Identity**: Strips `Bundle.signature` and the audit provenance extension to reconstruct the pre-audit clinical bundle, computes its RFC 8785 canonical SHA-256, and asserts it equals `clinicalContentDigest` and `db_event.metadata_json["clinical_content_sha256"]`.
4. **Transport Wire Boundary Verification**: The network dispatcher / HTTP response layer asserts `SHA256(wire_payload_bytes) == transport_sha256` at the physical socket or HTTP stream boundary.

---

### 7.2 Complete DB-to-JSONL Attestation & The Verification-to-Use Boundary

#### A. The Complete Attestation Verification Contract
Releasing, downloading, or transmitting any `FHIRBundle` requires **100% full-chain DB-to-JSONL event equivalence**:

```
                         COMPLETE ATTESTATION GATEWAY
                                     │
                    Retrieve Bundle from `fhir_bundles`
                                     │
                 Extract `auditSequenceNumber` from Bundle
                                     │
                 Acquire OS File Lock `<ledger>.lock` (L_3)
                                     │
                 Verify Complete Attestation (1..S_bundle):
                 1. Sequence Contiguity: S_file >= S_bundle
                 2. Checkpoint Sealing: S_chkpt >= S_bundle with valid HMAC
                 3. Full Event Equivalence for k = 1..S_bundle:
                    - Line k in JSONL matches row k in DB
                    - jsonl.current_event_hash == db.current_event_hash
                    - jsonl.previous_event_hash == db.previous_event_hash
                    - Bitwise payload identity (actor, action, metadata)
                                     │
                        Did ALL Checks Pass?
                     ├── YES ──> Release File Lock; Proceed to Transport Binding.
                     └── NO ───> HARD BLOCK: Raise IncompleteAttestationError.
```

#### B. Eliminating the Verification-to-Use TOCTOU Gap
To prevent an external modification or ledger truncation between releasing `<ledger>.lock` and transmitting the bundle:
1. **Monotonic Append-Only Invariant**: The PostgreSQL database and offline ledger are strictly append-only. Subsequent projections ($N+2, N+3$) advance the checkpoint but do not alter verified historical events $1..N+1$.
2. **Immutable Transport Byte Binding & Boundary Enforcement**:
   - In both `EHRIntegrationService.send_fhir_bundle_to_ehr()` and `GET /documents/{id}/export`:
     * The transport payload bytes are serialized in memory ONCE into an immutable `bytes` object and hashed: `transport_sha256 = hashlib.sha256(transport_bytes).hexdigest()`.
     * The exact immutable `bytes` buffer is passed directly to the network transport or ASGI response.
     * Before socket write or HTTP return, the transport boundary layer asserts:
       $$\text{SHA256(boundary\_bytes)} == \text{transport\_sha256}$$
     * If an in-memory modification, transport adapter mutation, or DB update alters the payload after attestation verification, the wire assertion fails closed (`EHRBundlePayloadTamperedError`), aborting socket transmission and dropping connections.

#### Unified Verification Contract Call Sites:
1. **`EHRIntegrationService.send_fhir_bundle_to_ehr()`** ([`src/clindoc/services/ehr_integration.py:215-240`](file:///d:/daa/src/clindoc/services/ehr_integration.py#L215-L240)):
   - Loads `existing_bundle` from `session.query(FHIRBundle)`.
   - Calls `AuditProjectionEngine.verify_bundle_complete_attestation(session, existing_bundle)`.
   - Binds `transport_sha256` to transport socket.
2. **`GET /documents/{document_id}/export` Endpoint** ([`src/clindoc/api/routes/documents.py:439-445`](file:///d:/daa/src/clindoc/api/routes/documents.py#L439-L445)):
   - Calls `AuditProjectionEngine.verify_bundle_complete_attestation()`.
   - Binds `transport_sha256` before returning HTTP 200 payload.
3. **`FHIRExportService.export_document_to_fhir()`**:
   - Returns the bundle only after Step 14 (checkpoint update) completes.


---

## 8. Reconciled Universal Lock Hierarchy & Deadlock Prevention Analysis

### 8.1 Universal Lock Hierarchy
$$\mathbf{L_1: \text{Claim Row Lock}} \longrightarrow \mathbf{L_{\text{doc}}: \text{Document Row Lock}} \longrightarrow \mathbf{L_2: \text{Global Advisory Lock}} \longrightarrow \left[ \text{COMMIT DB} \right] \longrightarrow \mathbf{L_3: \text{OS File Lock}}$$

### 8.2 Reconciled Lifecycle Across Operations

| Operation | Phase 1: DB Transaction Lifecycle (Holds $L_1, L_{\text{doc}}, L_2$) | Phase 2: Post-Commit Projection Lifecycle (Holds $L_3$) | Deadlock Safety Guarantee |
| :--- | :--- | :--- | :--- |
| **Review Writer (`accept_claim`)** | Acquires $L_1$ on target claim $\to$ acquires $L_2$ $\to$ inserts audit event $\to$ commits DB (releases $L_1, L_2$). | Acquires $L_3$ $\to$ projects committed event $\to$ fsync $\to$ updates checkpoint $\to$ releases $L_3$. | Deadlock free: Phase 1 & 2 disjoint. |
| **Batch Review (`batch_review_claims`)** | Sorts claim IDs ascending $\to$ acquires $L_1$ on all claims $\to$ acquires $L_2$ $\to$ inserts events $\to$ commits DB. | Acquires $L_3$ $\to$ projects all committed events $\to$ fsync $\to$ updates checkpoint $\to$ releases $L_3$. | Sorted acquisition prevents row deadlocks. |
| **Document Approval (`approve_document`)** | Acquires $L_{\text{doc}}$ on document $\to$ acquires $L_2$ $\to$ inserts audit event $\to$ commits DB (releases $L_{\text{doc}}, L_2$). | Acquires $L_3$ $\to$ projects committed event $\to$ fsync $\to$ updates checkpoint $\to$ releases $L_3$. | Deadlock free: Phase 1 & 2 disjoint. |
| **Gate 9 Export (`export_document_to_fhir`)** | Acquires $L_2$ at transaction start. **NEVER acquires $L_1$ or $L_{\text{doc}}$ row locks**. Commits DB (releases $L_2$). | Acquires $L_3$ $\to$ projects committed event $\to$ fsync $\to$ updates checkpoint $\to$ releases $L_3$. | **No circular wait possible**. |

- **Exact Guarantees of Each Lock Tier**:
  1. **Phase 1 (Database Transaction Locks $L_1, L_{\text{doc}}, L_2$)**:
     - Held from acquisition until `session.commit()` or `session.rollback()`.
     - $L_2$ (`pg_advisory_xact_lock`) strictly serializes the sequence allocation ($S_{\text{latest}} + 1$) and event hash generation in PostgreSQL.
     - **Advisory Lock Boundary**: $L_2$ is **released upon database commit**. It is **not** held during file I/O or JSONL projection.
  2. **Phase 2 (OS File Lock $L_3$)**:
     - Acquired strictly post-commit. Held during outbox reconciliation, file appends, fsyncs, and checkpoint renaming.
     - Protects against concurrent process file writing, torn line overlaps, and race conditions.
  3. **Disjoint Non-Overlapping Invariant**:
     - A writer *never* holds $L_3$ while acquiring or holding PostgreSQL locks ($L_1, L_{\text{doc}}, L_2$).
     - A writer *never* holds PostgreSQL locks while acquiring or waiting for $L_3$.
     - Cross-tier deadlocks are impossible.
  4. **Intra-Database Deadlock Freedom**: Review workers acquire $L_1 \to L_2$. Gate 9 acquires $L_2$ and reads claims under MVCC read-committed snapshots without `FOR UPDATE`. Because Gate 9 never requests $L_1$, circular wait ($A \to B \to A$) is impossible.

---

## 9. Authenticated Administrative Checkpoint Recovery

### 9.1 Secure Credential Input (Elimination of CLI Arguments)
To eliminate credential exposure in `/proc`, `ps aux`, shell history, and Windows Task Manager:
1. Operator invokes administrative CLI:
   ```bash
   clindoc-admin audit repair-checkpoint --ledger=/path/to/ledger.jsonl --reason="SEC-2026-904"
   ```
2. **Secure Token Acquisition**:
   - Interactive Mode: Prompts using masked TTY inputs:
     - `getpass.getpass("Enter SECURITY_ADMIN Bearer Token: ")`
     - `getpass.getpass("Enter COMPLIANCE_OFFICER Bearer Token: ")`
   - Automated Mode: Supports restricted file inputs `--operator-token-file=<path>` and `--authorizer-token-file=<path>` (with mandatory 0600 file permission validation and memory zeroization).
3. **Cryptographic Validation & Four-Eyes Enforcement**:
   - Validates cryptographic signatures against auth service public keys.
   - Asserts `operator_token.sub` has role `SECURITY_ADMIN`.
   - Asserts `authorizer_token.sub` has role `COMPLIANCE_OFFICER`.
   - Asserts `operator_token.sub != authorizer_token.sub` (**Four-Eyes Separation**).

### 9.2 Reconciled Disjoint Lock Lifecycle for Recovery
Administrative checkpoint recovery follows the exact same disjoint lifecycle as normal writers:

#### Phase 1: Database Verification & Event Commit (PostgreSQL Transaction)
1. Acquire global advisory lock $L_2$ (`SELECT pg_advisory_xact_lock(hashtext('clindoc_audit_ledger_global'))`).
2. Execute 100% full-chain verification of all events in PostgreSQL `audit_events` from sequence 1 to $N$.
3. Insert `AuditEvent(event_type="AUDIT_CHECKPOINT_RECONSTRUCTED", sequence_number=N+1)` recording operator IDs, reason, and terminal hash at sequence $N$.
4. Commit PostgreSQL transaction (`session.commit()`).
   - *Atomically releases advisory lock $L_2$*.

#### Phase 2: Post-Commit Projection & Checkpoint Signing (OS File Lock)
5. Acquire OS file lock $L_3$ (`<ledger>.lock`).
6. Execute streaming quarantine of existing `.chkpt` to `.chkpt.corrupt.<iso_ts>`.
7. Project event $N+1$ to `<ledger>.jsonl`, call `f.flush()`, `os.fsync()`.
8. Atomically install newly signed `.chkpt` at sequence $N+1$ via temporary file replacement.
9. Verify new checkpoint HMAC signature.
10. Release OS file lock $L_3$.

- **Crash Behavior Between Phases**:
  If the process terminates after Step 4 (DB commit) but before Step 8:
  Row $N+1$ is committed in PostgreSQL. The system enters *Incomplete Attestation State* ($S_{\text{db}} = N+1 > S_{\text{file}} = N$). Any Gate 9 export hard-blocks. On restart, outbox reconciliation projects row $N+1$ and advances the checkpoint. Auditability is preserved because the reconstruction event was committed in the authoritative database.

---

## 10. Stale Lock-File & OS-Level File Locking Semantics

### 10.1 Grounding in Kernel Lock Semantics
- In modern OS kernels (Linux `fcntl` and Windows `LockFileEx`), the lock file `<ledger>.lock` persists on disk permanently.
- The file lock is tied strictly to an **open file descriptor / handle in the kernel**:
  - **Linux / POSIX**: `fcntl(fd, F_SETLK, ...)` locks the inode. When the process terminates (even via `SIGKILL` or crash), the Linux kernel automatically closes all open file descriptors and releases the lock.
  - **Windows**: `LockFileEx` locks byte ranges on the file handle. When the process terminates, the Windows Object Manager closes open handles and releases byte range locks.
- **Rule**: Staleness is determined **exclusively by attempting kernel lock acquisition**:
  ```python
  from filelock import FileLock, Timeout

  lock = FileLock(f"{ledger_path}.lock", timeout=30.0)
  try:
      with lock:
          # Critical section
          ...
  except Timeout:
      # Active running process holds the lock; fails closed
      raise AuditLedgerConcurrencyTimeoutError("Could not acquire ledger lock within 30s")
  ```
- **Invariant**: The physical lock file `<ledger>.lock` is **NEVER deleted** as a test of staleness. Its existence on disk is completely normal and expected.

---

## 11. Comprehensive Real-PostgreSQL Multi-Process Test Matrix

All tests will be implemented in [`tests/test_audit_ledger_concurrency.py`](file:///d:/daa/tests/test_audit_ledger_concurrency.py) using real PostgreSQL multi-process fixtures:

| Test ID | Test Scenario | Fault Injection & Concurrency | Expected Ledger State | Gate 9 / EHR Verdict |
| :---: | :--- | :--- | :--- | :--- | :---: |
| **TC-01** | Two concurrent review workers | 2 OS processes concurrently call `accept_claim()`. | 2 valid events; monotonic sequences $N+1, N+2$; unbroken hash chain. | **PERMITTED** |
| **TC-02** | Writer racing with Gate 9 export | Worker calls `accept_claim()` while Gate 9 holds advisory lock. | Worker transaction blocks in PostgreSQL until Gate 9 commits. Zero race. | **PERMITTED** |
| **TC-03** | Gate 9 holds advisory lock | Gate 9 begins export. Worker initiates write with 1s timeout. | Worker query times out or waits. Database maintains strict serialization. | **PERMITTED** |
| **TC-04** | Crash after DB commit before JSONL | 5 events committed to DB. JSONL truncated to sequence 3. Reconcile invoked. | Sequences 4 and 5 replayed from DB; valid hash chain and HMAC updated. | **BLOCKED** initially; **PERMITTED** post-replay. |
| **TC-05** | Crash during JSONL append (torn write) | Write 10 valid events, then append 17 bytes of corrupted garbage without `\n`. | Garbage quarantined to `.corrupt.<ts>`; clean records preserved; event replayed. | **BLOCKED** initially; **PERMITTED** post-recovery. |
| **TC-06** | Forensic preservation verification | Injected torn line in JSONL. Run recovery. | Bitwise exact copy exists in quarantine; byte count & SHA-256 match original. | **PERMITTED** post-repair |
| **TC-07** | Middle sequence corruption | Inject syntax error or modified byte on line 3 of 10. Run Gate 9. | `AuditLedgerTamperedError` raised. Fails closed. Auto-repair PROHIBITED. | **HARD BLOCKED** |
| **TC-08** | Split-Brain ($S_{\text{file}} > S_{\text{db}}$) | JSONL contains sequence 6, but DB only has sequences 1..5. | `SplitBrainLedgerError` raised. Zero automatic overwrite of file or DB. | **HARD BLOCKED** |
| **TC-09** | Sequence gap in JSONL (1, 2, 4) | Remove sequence 3 from JSONL file. Run reconciliation. | `AuditLedgerGapError` raised. Fails closed. | **HARD BLOCKED** |
| **TC-10** | Duplicate sequence in JSONL | Inject two lines with `sequence_number: 3`. Run reconciliation. | `DuplicateAuditEventError` raised. Fails closed. | **HARD BLOCKED** |
| **TC-11** | Middle sequence payload tampering | Modify `actor_id` in sequence 3 of 10. Run Gate 9 export. | `AuditLedgerTamperedError` raised. Hash mismatch detected. | **HARD BLOCKED** |
| **TC-12** | Broken previous-event hash chain | Alter `previous_event_hash` in sequence 4. Run verification. | `AuditLedgerTamperedError` raised. Broken linkage detected. | **HARD BLOCKED** |
| **TC-13** | HMAC checkpoint signature mismatch | Modify 1 character of signature in `.chkpt`. Run Gate 9. | `AuditLedgerVerificationError` raised. Anti-whitewashing prevents regen. | **HARD BLOCKED** |
| **TC-14** | Missing checkpoint with DB history | Delete `.chkpt` when DB has sequence 10. Run Gate 9. | Fails closed. Anti-whitewashing prevents silent recreation. | **HARD BLOCKED** |
| **TC-15** | Crash during checkpoint update | Kill process while `.chkpt.tmp` is being written. | Old valid `.chkpt` intact; orphaned `.tmp` cleaned; recovery succeeds. | **PERMITTED** |
| **TC-16** | Outbox replay idempotency | Run reconciliation 5 consecutive times on fully synchronized ledger. | Zero modifications to JSONL or DB; identical hashes; zero side-effects. | **PERMITTED** |
| **TC-17** | Concurrent batch review & audit writes | Multi-process worker executing `batch_review_claims()` concurrent with single review. | All batch and single events serialized; contiguous sequence numbers; zero gaps. | **PERMITTED** |
| **TC-18** | Conflicting lock order stress test | Concurrent workers mixing claim edits, document approval, and Gate 9 export. | Zero deadlocks observed over 50 concurrent transactions. | **PERMITTED** |
| **TC-19a** | Racing direct SQL insert | Direct raw SQL insert races with authorized worker holding advisory lock. | Unsynchronized write collides on `uq_audit_sequence_number` or creates invalid previous hash; fails closed. | **HARD BLOCKED** |
| **TC-19b** | Sequential out-of-band direct SQL insert | Unsynchronized direct SQL insert executes between authorized transactions assigning valid next sequence and hash. | Direct insert succeeds in DB and passes cryptographic hash check; demonstrates application-tier vs DB-tier boundary. | **PERMITTED by hash check; BLOCKED by semantic check (V-03)** |
| **TC-20** | Quarantine verification under source race | File modified externally during streaming quarantine copy. | `ExternalConcurrentModificationError` raised; copy aborted; original untouched. | **HARD BLOCKED** |
| **TC-21** | EHR dispatch blocked during incomplete attestation | DB commits bundle at $N+1$; crash before JSONL; call `send_fhir_bundle_to_ehr()`. | `EHRIncompleteAttestationError` raised; transport dispatch strictly blocked until outbox replay. | **HARD BLOCKED** initially; **PERMITTED** post-replay. |
| **TC-22** | Bundle download endpoint blocked during incomplete attestation | DB commits bundle at $N+1$; crash before JSONL; call `GET /documents/{id}/export`. | Endpoint evaluates attestation; blocks uncertified bundle download until outbox replay. | **HARD BLOCKED** initially; **PERMITTED** post-replay. |
| **TC-23** | Checkpoint-valid but payload-tampered JSONL blocks bundle release | JSONL has valid checkpoint at $N+1$, but line $N+1$ payload differs from DB. | Equivalence check fails; `AttestationEquivalenceMismatchError` raised; dispatch blocked. | **HARD BLOCKED** |
| **TC-24** | Transport boundary wire-payload tampering | Intercepts byte stream at actual transport boundary (kernel socket writer for MLLP or ASGI stream for HTTPS/HTTP export) and mutates 1 byte. | Transport boundary assertion detects `SHA256(wire_payload_bytes) != transport_sha256`; fails closed (`EHRBundlePayloadTamperedError`); drops socket/stream; 0 corrupted bytes sent. Proves actual transport boundary binding rather than intermediate in-memory object hashing. | **HARD BLOCKED** |
| **TC-25** | POSIX concurrent write during quarantine fails closed without truncation | External process appends bytes between quarantine copy and truncation attempt on POSIX. | `UnquiescedLedgerModificationError` raised; original file preserved un-truncated; quarantine preserved; export blocked until admin quiescence recovery. | **HARD BLOCKED** |
| **TC-26** | POSIX append injected between source verification and quarantine finalization | Test hook injects external append after post-copy source verification but before `os.replace`. | Finalization check detects size/mtime change; quarantine copy marked `.untrusted` (never certified as complete snapshot); original POSIX source preserved AS-IS without automatic truncation; fails closed (`UnquiescedLedgerModificationError`); requires verified process quiescence for recovery. | **HARD BLOCKED** |
| **TC-27** | Bundle.signature cryptographic contract verification failure | Injects 5 failure modes: missing signature, malformed JWS, invalid signature, untrusted/expired/revoked key, or audit provenance binding mismatch. | Rejects SHA-256 digest equality alone as signer authentication; raises typed error (`MissingBundleSignatureError`, `MalformedBundleSignatureError`, `InvalidBundleSignatureError`, `UntrustedSigningKeyError`, `AuditProvenanceBindingMismatchError`); fails closed; suppresses export. | **HARD BLOCKED** |


---

## 12. Staged Implementation Order & Checkpoints

```
[Stage 3.1: Global Advisory Lock, Hash Unification & W-02 Retirement]
   - Unify advisory lock key to `hashtext('clindoc_audit_ledger_global')` in AuditService
   - Standardize compute_audit_event_hash across all 13 retained writers and verifiers
   - Formally retire W-02 direct append in ClaimReviewService._record_audit_event()
   - Wire advisory lock acquisition into FHIRExportService.export_document_to_fhir()
   - Verification: Lock blocking integration test (TC-02, TC-03, TC-19a, TC-19b)
             │
             ▼
[Stage 3.2: Multi-Process Kernel File Locking & Durability]
   - Implement FileLock("<ledger>.lock") around projection and verification
   - Implement f.flush() + os.fsync(f.fileno()) and platform-specific directory fsync
   - Verification: 8-worker concurrent append benchmark (TC-01, TC-18)
             │
             ▼
[Stage 3.3: Centralized Outbox Replay & Streaming Forensic Quarantine]
   - Implement ReviewAuditLedger.reconcile_with_db() with outbox replay
   - Implement independent quarantine verification with Win32 write-sharing denial and POSIX fail-closed policy
   - Enforce torn-tail vs middle-corruption distinction (middle fails closed)
   - Implement Authenticated Administrative Checkpoint Protocol with secure credential input
   - Verification: Crash injection and quarantine tests (TC-04 through TC-15, TC-20, TC-25, TC-26)
             │
             ▼
[Stage 3.4: Complete Attestation Gateway & Three-Tier Integrity Verification]
   - Wire corrected Gate 9 sequence: DB commit FIRST -> post-commit projection
   - Implement Three-Tier Bundle Integrity (clinical_content_sha256, Bundle.signature RS256/ES256, transport_sha256)
   - Wire complete attestation verification helper (full DB-to-JSONL equivalence) into:
     * EHRIntegrationService.send_fhir_bundle_to_ehr()
     * GET /documents/{id}/export endpoint
   - Verification: Full test suite tests/test_audit_ledger_concurrency.py (28/28 PASS)
```

---

## 13. Scope, Schema Freeze & Subsystem Invariants

The following components and invariants are explicitly protected and frozen:
1. **Database Schema Freeze**: Frozen. Zero migrations. Zero column additions to `audit_events`.
2. **Step 1 F-12 Four-Eyes Implementation**: Frozen. Ancestry validation and root claim locking remain unchanged.
3. **Step 2 REQ-44 Lifecycle Mapper**: Frozen. [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py) is untouched.
4. **Authoritative Hash Algorithm**: Frozen. Python Canonical Sorted JSON metadata with pipe-delimited payload (`compute_audit_event_hash`) is the sole authoritative standard.
5. **Separately Protected HMAC Checkpoint Key**: Preserved in environment and keyrings.
6. **Gate 9 & EHR Fail-Closed Behavior**: Every corrupt, torn, or unverified state fails closed and suppresses export/dispatch.

---

## 14. Platform Boundaries, Assumptions & Technical Invariants

1. **Single Database Primary Cluster Boundary**: PostgreSQL advisory locks are cluster-local; they do not serialize across multi-master or distributed active-active databases without external consensus.
2. **Storage Subsystem Locking**: Filesystem hosting `<ledger>.jsonl` and `<ledger>.lock` must support advisory locking (`fcntl` or `LockFileEx`). Remote mounts with disabled lock daemons (e.g. NFSv3 `nolock`) are unsupported.
3. **HMAC Key Custody**: If an adversary gains root read/write access to both filesystem storage and the HMAC signing key, offline attestation cannot prevent targeted forgery; authoritative verification must rely on PostgreSQL database WAL.
4. **Document Upload Parsing Duration**: Document ingestion parsing executes *before* the database transaction begins; the transaction only inserts the document row and audit event (< 10ms duration).
5. **Database Privilege Boundary Under Schema Freeze**: Enforcing that direct database inserts cannot bypass the advisory lock requires database-level `BEFORE INSERT` triggers or stored procedure privilege enforcement. Under the frozen schema, this is enforced at the application architecture layer and verified by semantic/business verifiers (V-03). A sequentially contiguous out-of-band direct insert committed between authorized transactions satisfies cryptographic hash verification, but fails semantic claim provenance checks.

---

## 15. Objective Completion Criteria

Step 3 implementation will be certified complete ONLY when all of the following conditions are met:

1. **Advisory Lock Unification**: Every one of the 13 retained production audit writers and Gate 9 export paths acquires `SELECT pg_advisory_xact_lock(hashtext('clindoc_audit_ledger_global'))`.
2. **W-02 Direct Append Retired**: Direct un-synchronized append in `ClaimReviewService._record_audit_event()` is eliminated; projection is 100% centralized in `AuditProjectionEngine`.
3. **PostgreSQL Transaction Serialization**: Gate 9 holds the advisory lock continuously throughout its database transaction (until commit), serializing export state.
4. **PostgreSQL Authoritative & DB-First Gate 9**: Export audit event and export records commit to PostgreSQL before JSONL projection and checkpoint update.
5. **Full-Chain Reconciliation**: Ledger verification checks all sequences $1..N$ and verifies 1:1 hash equivalence against PostgreSQL using the authoritative hash algorithm.
6. **Outbox Replay Verified**: Missing events in lagging JSONL files are automatically replayed idempotently.
7. **Verified Forensic Quarantine**: Quarantine files are independently verified from disk; on Windows, Win32 mandatory write-sharing denial prevents external modification; on POSIX, external modifications fail closed without truncation and require administrative quiescence recovery.
8. **Strict Anti-Whitewashing Invariant**: No HMAC checkpoint can be generated or updated unless full-chain database verification passes 100%. Administrative repair requires dual-custody authorization with secure credential input.
9. **Complete Attestation Gate**: Every path retrieving or transmitting FHIR bundles (`send_fhir_bundle_to_ehr`, `GET /documents/{id}/export`, Gate 9) hard-blocks access while attestation is incomplete ($S_{\text{db}} > S_{\text{file}}$) or if DB-to-JSONL payload equivalence is broken.
10. **Three-Tier Bundle Integrity Verified**: Pre-audit clinical content (`clinical_content_sha256`), digital signature envelope (`Bundle.signature` RS256/ES256), and exact transport payload (`transport_sha256`) are independently validated without circular dependencies.
11. **Cross-Platform Durability**: Kernel file locking and `os.fsync()` operate cleanly on both Windows (NTFS) and POSIX, with enterprise power-loss durability requiring PLP-backed hardware on Windows.
12. **Deadlock Freedom Proved**: Multi-process stress tests prove zero deadlocks across review, approval, and Gate 9 export.
13. **Test Suite Verification**: 100% passing tests in `tests/test_audit_ledger_concurrency.py` (28/28 real PostgreSQL concurrency tests).
14. **Zero Regressions**: All 121 existing tests across F-12, REQ-44, and FHIR export continue to pass with zero failures.
15. **Schema Freeze Preserved**: Zero modifications to database schemas or migrations.

---
*End of Implementation Execution Plan for DEF-003 (Revision 2.7). Awaiting review and explicit authorization.*
