# ClinicalDoc — P0 Security & Architecture Remediation Design (Version 3)

**Document ID**: `CLINICALDOC_P0_SECURITY_REMEDIATION_DESIGN_v3.md`  
**Date**: October 31, 2026  
**Auditor & Architect**: Principal Software Architect, Senior Clinical NLP/AI Engineer & FHIR/Security Specialist  
**Target Repository**: `ClinicalDoc` (`clindoc2fhir`)  
**Design Status**: FINAL ARCHITECTURAL SPECIFICATION — PENDING IMPLEMENTATION AUTHORIZATION  
**Review Notice**: Documentation only. Zero source code, test code, configuration, benchmark data, database models, or Git state modified. All existing workspace changes (including 24 modified tracked files and 68 untracked files) remain strictly preserved.

---

## 1. Executive Summary & Remediation Governance

This document represents the definitive, third-iteration architectural specification resolving all outstanding issues identified in the independent design reviews of `v1` and `v2`. It provides exact failure scenarios, mathematically proven architectural rules, concrete implementation boundaries, adversarial test specifications, and objective acceptance criteria for all four P0/P1 defects:

1. **F-12 (P0 — Security / Four-Eyes)**: Multi-hop Four-Eyes ancestry traversal, lineage-level serialization, and concurrent branching prevention in [`src/clindoc/review/service.py:838-852`](file:///d:/daa/src/clindoc/review/service.py#L838-L852).
2. **DEF-002 (P0 — Clinical Safety / FHIR Semantics)**: REQ-44 lifecycle enforcement, evidentiary precision between patient intake vs clinician directive, and fail-closed grounding in [`src/clindoc/services/fhir_export.py`](file:///d:/daa/src/clindoc/services/fhir_export.py) and [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py).
3. **DEF-003 (P0 — Integrity / Concurrency)**: PostgreSQL/JSONL crash consistency across all six failure points, transactional outbox replay, and corrected OS durability guarantees in [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py).
4. **DEF-004 (P1 — Pipeline Architecture & Governance)**: Normative FHIR R4B 4.3.0 Base compliance (eliminating invalid `Bundle.extension`), four-tier governance separation, and headless CLI containment in [`src/clindoc/cli.py`](file:///d:/daa/src/clindoc/cli.py).

### Mandatory Non-Negotiable Invariants
- **Frozen Subsystems**: ModernBERT-Biomedical model weights, temperature scalar ($T=1.146522$), Stage A deterministic verifier tolerances (0.05), benchmark manifests (`data/m2_verification_v3/`, `external_eval/`), and legacy Streamlit UI remain strictly frozen.
- **Database Schema Freeze**: Database schema changes are avoided. The dual-tier crash synchronization operates entirely over the existing `audit_events` schema ([`src/clindoc/db/models.py:801-860`](file:///d:/daa/src/clindoc/db/models.py#L801-L860)) without requiring database migrations.
- **Fail-Closed Safety**: Any ambiguity in patient intake, missing provenance in ancestral claim versions, ledger corruption, or unverified Gate 9 audit signature results in an immediate fail-closed state.

---

## 2. F-12 Remediation: Lineage Serialization, Multi-Hop Ancestry & Branching Prevention

### 2.1 The Exact Failure Scenarios

#### Scenario 1: Multi-Hop Four-Eyes Bypass via Intermediate Reviewers
- In [`src/clindoc/review/service.py:838-852`](file:///d:/daa/src/clindoc/review/service.py#L838-L852), `_check_four_eyes` checks only `prev_claim = session.get(Claim, claim.supersedes_claim_id)`.
- Lineage: $v_1 \xrightarrow{\text{Actor A}} v_2 \xrightarrow{\text{Actor B}} v_3 \xrightarrow{\text{Actor C}} v_4$.
- When Actor A accepts $v_4$: $v_4.\text{created\_by} = \text{C} \neq \text{A}$, and $v_3.\text{created\_by} = \text{B} \neq \text{A}$.
- Single-hop traversal terminates. Actor A is permitted to approve clinical substance they modified in $v_2$, directly violating the Mode 0 Four-Eyes principle (PD-002, PD-009, PD-015).

#### Scenario 2: Concurrent Lineage Branching & Phantom Insertion
- Target claim $v_2$ is undergoing acceptance by Clinician 1. Clinician 1's transaction locks rows $v_1$ and $v_2$ with `SELECT ... FOR UPDATE`.
- Concurrently, Clinician 2 invokes `edit_claim(v_2)`. Because row-level locks on existing rows do not block `INSERT` statements into the `claims` table under `READ COMMITTED` or `REPEATABLE READ`, Clinician 2 successfully inserts $v_3$ with `supersedes_claim_id = v_2.id`.
- Clinician 1 commits the acceptance of $v_2$. The database is left in a corrupted state: $v_2$ is marked `ACCEPTED`, yet a superseded child $v_3$ exists in `PENDING` state, creating a split-brain clinical record.

#### Scenario 3: Phantom Forking off Non-Terminal Ancestors
- A claim has progressed to $v_3$. Reviewer 2 maliciously or accidentally submits an edit targeting $v_1$ or $v_2$.
- If leaf-only constraints are not enforced under a lineage mutex, a parallel branch $v_1 \to v_2'$ is inserted, creating an unresolvable directed acyclic graph (DAG) rather than a linear version chain.

---

### 2.2 Corrected Architectural Rule

#### 1. Universal Lineage-Level Mutex on Root Claim ($v_1$)
To guarantee strict serialization of all review operations without phantom insertions or concurrent branching, **every operation modifying or accepting any claim version in a lineage MUST acquire an exclusive lock on the lineage root claim ($v_1$)**:

$$\text{Lock Target: } \text{Claim where } \texttt{claim\_id} = \text{target.claim\_id} \text{ and } \texttt{version} = 1$$

Because $v_1$ is immutable, created during initial automated pipeline ingestion, and never deleted, it serves as the permanent, authoritative serialization mutex for the entire clinical entity.

```sql
-- Acquired at transaction start before reading lineage state
SELECT id, version, review_state 
FROM claims 
WHERE claim_id = :lineage_claim_id AND version = 1 
FOR UPDATE;
```

#### 2. Strict Deterministic Lock Acquisition Ordering
To prevent database deadlocks across multi-claim batch review operations, locks MUST be acquired in strictly sorted alphanumeric order of `claim_id`:
```python
sorted_claim_ids = sorted(list(target_claim_ids))
for cid in sorted_claim_ids:
    session.query(Claim).filter(
        Claim.claim_id == cid, 
        Claim.version == 1
    ).with_for_update().one()
```

#### 3. Leaf-Only Invariant & Branching Prohibition
Under the lineage mutex, the service verifies that the target claim is the unique, active terminal leaf of the lineage:
```python
max_version = session.query(func.max(Claim.version)).filter(
    Claim.claim_id == target.claim_id
).scalar()

if target.version != max_version:
    raise BranchingProhibitedError(
        f"Cannot edit or accept non-terminal version {target.version}. "
        f"Lineage '{target.claim_id}' has progressed to version {max_version}."
    )

if target.review_state != ClaimReviewState.PENDING:
    raise InvalidClaimLifecycleStateError(
        f"Claim {target.id} is in state '{target.review_state}', expected PENDING."
    )
```

#### 4. Valid Lifecycle State Transition Matrix

```
                      CLAIM VERSION LIFECYCLE MATRIX
                      
       Pipeline Ingestion ($v_1$) ──► PENDING
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
                ACCEPTED            REJECTED            EDITED (Superseded by $v_2$)
               (Terminal)          (Terminal)             │
                                                          ▼
                                    Reviewer Child ($v_2$) ──► PENDING
                                                                │
                                             ┌──────────────────┼──────────────────┐
                                             ▼                  ▼                  ▼
                                         ACCEPTED            REJECTED            EDITED
                                        (Terminal)          (Terminal)     (Superseded by $v_3$)
```

- **Root Claim ($v_1$)**: Origin is `PIPELINE`. Initial state is `PENDING`. Permitted transitions: `ACCEPTED`, `REJECTED`, or `EDITED` (only upon insertion of $v_2$).
- **Reviewer Descendants ($v_{\ge 2}$)**: Origin is `REVIEWER`. Inserted in state `PENDING` with parent atomically updated from `PENDING` to `EDITED`.
- **Terminal States**: `ACCEPTED` and `REJECTED` are final. Once a lineage reaches a terminal state, the lineage mutex permanently blocks any subsequent insertion of descendants.

#### 5. Complete Multi-Hop Ancestry Traversal Algorithm
Under the lineage mutex, the accepting transaction walks the full ancestry chain:
1. Initialize `visited_ids = {target.id}`, `ancestor_actors = set()`, `depth = 0`.
2. Inspect target claim:
   - If `target.created_by == actor_uuid` or `target.payload.get("edited_by") == str(actor_uuid)`:
     raise `FourEyesViolationError`.
3. While `curr.supersedes_claim_id is not None`:
   - `depth += 1`; if `depth > 50`: raise `CorruptedClaimAncestryError("Depth limit exceeded")`.
   - `parent_id = curr.supersedes_claim_id`.
   - If `parent_id in visited_ids`: raise `CorruptedClaimAncestryError("Cycle detected")`.
   - `visited_ids.add(parent_id)`.
   - `parent = session.get(Claim, parent_id)`.
   - If `parent is None`: raise `CorruptedClaimAncestryError("Dangling parent link")`.
   - If `parent.document_id != target.document_id`: raise `CorruptedClaimAncestryError("Cross-document link")`.
   - If `parent.origin == ClaimOrigin.REVIEWER`:
     - Extract `p_creator = parent.created_by` and `p_editor = parent.payload.get("edited_by")`.
     - If neither exists: **FAIL CLOSED** (`CorruptedClaimAncestryError("Reviewer claim lacks verifiable actor identity")`).
     - Add existing IDs to `ancestor_actors`.
   - `curr = parent`.
4. If `str(actor_uuid) in ancestor_actors`:
   raise `FourEyesViolationError(f"Actor '{actor_uuid}' edited an ancestor in this lineage.")`.
5. Verify document uploader:
   - If `Document.uploaded_by == actor_uuid`: raise `FourEyesViolationError("Uploader cannot review.")`.

---

### 2.3 Implementation Boundary
- File: [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py)
- Methods:
  - `ClaimReviewService.accept_claim`: Acquire root lock on $v_1$, verify leaf-only invariant, execute ancestry traversal, transition to `ACCEPTED`.
  - `ClaimReviewService.reject_claim`: Acquire root lock on $v_1$, verify leaf-only invariant, execute ancestry traversal, transition to `REJECTED`.
  - `ClaimReviewService.edit_claim`: Acquire root lock on $v_1$, verify leaf-only invariant, insert $v_{k+1}$ as `PENDING`, update $v_k$ to `EDITED`.

---

### 2.4 Required Adversarial Tests
1. `test_four_eyes_multi_hop_traversal_rejects_original_editor`:
   - Lineage: $v_1 \xrightarrow{\text{A}} v_2 \xrightarrow{\text{B}} v_3 \xrightarrow{\text{C}} v_4$.
   - Assert Actor A is rejected when attempting to accept $v_4$ with `FourEyesViolationError`.
2. `test_concurrent_edit_during_acceptance_blocked_by_root_mutex`:
   - Thread 1 locks $v_1$ to accept $v_3$. Thread 2 attempts `edit_claim(v_3)`.
   - Assert Thread 2 blocks on root lock until Thread 1 commits.
   - Assert Thread 2 then fails with `InvalidClaimLifecycleStateError` (since $v_3$ is now terminal `ACCEPTED`).
3. `test_branching_attempt_on_superseded_version_rejected`:
   - Lineage is at $v_3$. Request attempts `edit_claim(v_1)`.
   - Assert operation raises `BranchingProhibitedError`.
4. `test_missing_actor_provenance_in_ancestor_fails_closed`:
   - Corrupted ancestor $v_2$ has `origin = REVIEWER` but `created_by = NULL`.
   - Assert `accept_claim(v_3)` raises `CorruptedClaimAncestryError`.

---

### 2.5 Objective Acceptance Criteria
1. Any actor who edited *any* version in an ancestry chain cannot accept any descendant.
2. Zero concurrent branching or phantom insertions: lines of descent are strictly linear.
3. Attempted edits on accepted/rejected claims are rejected.
4. All multi-hop tests pass with zero regressions across the 1,466 existing tests.

---

### 2.6 Unresolved Dependencies / Approval Requirements
- **None**: Uses existing `claims` table columns (`claim_id`, `version`, `supersedes_claim_id`, `review_state`). No database migration required.

---

## 3. DEF-002 Remediation: MedicationStatement Evidence Precision & REQ-44 Activation

### 3.1 The Exact Failure Scenarios

#### Scenario 1: Confounding Clinician Directives with Patient Intake
- Clinical text: *"Plan: Continue Prednisone 10mg daily."*
- Flawed Logic: Treating the word "Continue" as evidence that the patient is currently consuming the medication, emitting `MedicationStatement(status="active")`.
- Safety Hazard: In a malpractice or reconciliation dispute, this falsely certifies that patient adherence was verified, when in reality it was merely a doctor's therapeutic plan.

#### Scenario 2: Dormant REQ-44 Parameter in Production Callers
- In [`src/clindoc/fhir/mapper.py:2397`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2397), `claims_to_fhir_bundle` defaults to `enforce_req44_lifecycle: bool = False`.
- In [`src/clindoc/services/fhir_export.py:743, 1029`](file:///d:/daa/src/clindoc/services/fhir_export.py#L743), callers invoke the mapper without passing `enforce_req44_lifecycle=True`.
- Consequence: Historical mentions ("took Amoxicillin in 2022") are exported as active `MedicationRequest` orders in production, creating critical medication overdose / duplicate order risks.

---

### 3.2 Corrected Architectural Rule

#### 1. Evidentiary Distinction: Intake vs Directive vs Order vs History
The FHIR mapping engine must enforce strict evidentiary boundaries based on clinical source text:

```
                    CLINICAL MEDICATION EVIDENCE CLASSIFIER
                    
                          [Extracted Medication Mention]
                                        │
             ┌──────────────────────────┴──────────────────────────┐
             ▼                                                     ▼
     Prescriber Directives                                 Patient Context / Narrative
             │                                                     │
   ┌─────────┴─────────┐                                 ┌─────────┴─────────┐
   ▼                   ▼                                 ▼                   ▼
New Order         Plan / Continue                   Verified Intake      Remote History
("Rx", "Dispense")("Plan: Continue")                ("Patient takes")    ("Took in 2021")
   │                   │                                 │                   │
   ▼                   ▼                                 ▼                   ▼
MedicationRequest   MedicationRequest                MedicationStatement  MedicationStatement
intent="order"      intent="plan"                    status="active"      status="completed"
status="active"     status="active"                                       (or "unknown")
```

#### Detailed Specification Matrix

| Category | Clinical Category Name | Evidentiary & Syntactic Requirements | Resulting FHIR Resource & Status | Safety Invariant |
| :---: | :--- | :--- | :--- | :--- |
| **1** | **Completed Historical Courses** | Past tense completion predicate ("completed", "finished", "took for 7 days in March") strictly within the same clause or dependency span. | `MedicationStatement`<br>`status: "completed"` | Must NEVER emit a `MedicationRequest`. Prevents historical exposure from being re-ordered. |
| **2** | **New Prescription Orders** | Active prescriber directive ("Rx", "Start", "Prescribe", "Dispense", or presence within authenticated Rx pad / CPOE section with clinician signature/DEA). | `MedicationRequest`<br>`status: "active"`<br>`intent: "order"` | Emitted only when legal prescriber order intent is verified. Narrative mentions are barred. |
| **3** | **Clinician Directive / Plan (e.g. "Continue")** | Clinician therapeutic directive ("Plan: Continue Prednisone", "Continue home regimen") without active prescription issuance metadata. | `MedicationRequest`<br>`status: "active"`<br>`intent: "plan"` | **Precision Invariant**: Does NOT assert patient consumption or adherence. Captures doctor's plan. |
| **4** | **Verified Patient Intake (Adherence)** | Patient-reported or nursing-reconciled intake ("Patient confirms taking Lisinopril 10mg daily", "Compliance: daily"). | `MedicationStatement`<br>`status: "active"` | Records verified intake without implying an order was generated during this encounter. |
| **5** | **Patient-Reported Cessation** | Explicit predicate of patient self-discontinuation ("patient stopped taking Metformin due to nausea"). | `MedicationStatement`<br>`status: "stopped"` | Accurately records non-adherence/cessation without cancelling the doctor's legal prescription order. |
| **6** | **Prescriber Cancellation (Linked to Order)** | Explicit clinician cancellation directive referencing an identifiable order or Rx number ("Cancel order #4812"). | `MedicationRequest`<br>`status: "cancelled"` | Emitted ONLY when cancellation syntactically binds to an existing order reference. Sets `priorPrescription`. |
| **7** | **Unreferenced / Ambiguous Discontinuation** | Detached discontinuation verb ("Discontinue Lisinopril", "Stop Prednisone") in a narrative note without order reference or clear actor attribution. | **FAILS CLOSED**<br>Claim is **SUPPRESSED** from FHIR export; logged in audit trail. | **Critical Safety Invariant**: The system must NOT guess whether to cancel an order or record cessation. Suppressed with audit reason code `REQ-44: Unreferenced cancellation lacks prior order reference`. |

#### 2. Definitive Resolution of *"Completed course. Continue Prednisone 10mg"*
1. Clause 1 (*"Completed course"*): Lacks an entity anchor $[L_k, R_k)$ in its clause span. Binding to an adjacent mention is prohibited $\to$ **SUPPRESSED** (`SUPPRESSED_UNBOUND_TEMPORAL_MODIFIER`).
2. Clause 2 (*"Continue Prednisone 10mg"*): Represents a clinician plan/directive in narrative text $\to$ Emit `MedicationRequest(status="active", intent="plan")`. Zero active dispensing order fabricated; zero patient intake claimed.

---

### 3.3 Implementation Boundary
- File: [`src/clindoc/fhir/mapper.py:2397`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2397)
  - Change default: `enforce_req44_lifecycle: bool = True`.
- File: [`src/clindoc/services/fhir_export.py:743, 1029`](file:///d:/daa/src/clindoc/services/fhir_export.py#L743)
  - Explicitly pass `enforce_req44_lifecycle=True`.
- File: [`src/clindoc/cli.py:244`](file:///d:/daa/src/clindoc/cli.py#L244)
  - Explicitly pass `enforce_req44_lifecycle=True`.

---

### 3.4 Required Adversarial Tests
1. `test_req44_continue_directive_emits_request_plan_not_statement_active`:
   - Text: "Assessment: Gout. Plan: Continue Allopurinol 100mg daily."
   - Assert FHIR output is `MedicationRequest(status="active", intent="plan")`.
   - Assert NO `MedicationStatement` asserting patient intake is generated.
2. `test_req44_verified_intake_emits_statement_active`:
   - Text: "Current medications: Patient confirms taking Lisinopril 20mg every morning."
   - Assert FHIR output is `MedicationStatement(status="active")`.
3. `test_req44_detached_modifier_suppression`:
   - Text: "Completed course. Continue Prednisone 10mg."
   - Assert "Completed course" generates zero resources (suppressed).
   - Assert "Prednisone 10mg" generates `MedicationRequest(status="active", intent="plan")`.

---

### 3.5 Objective Acceptance Criteria
1. `enforce_req44_lifecycle` defaults to `True` across all production paths.
2. Clinician directives ("Continue") never emit `MedicationStatement(status="active")`.
3. Historical medication mentions never emit `MedicationRequest(status="active")`.
4. All 19 tests in `tests/test_phase523_milestone4_4.py` remain 100% passing.

---

### 3.6 Unresolved Dependencies / Approval Requirements
- **None**: Mapper parameters and logic are fully self-contained.

---

## 4. DEF-003 Remediation: PostgreSQL/JSONL Crash Consistency & Corrected Durability

### 4.1 The Exact Failure Scenarios

In a dual-tier architecture where PostgreSQL `audit_events` is the transactional sequence generator and the JSONL file is the offline cryptographic attestation artifact, a process crash or power loss can occur at any microsecond.

#### Micro-Failure Analysis (Six Failure Points)

| Failure Point | System State at Crash | Inconsistency / Risk | Recovery Behavior |
| :---: | :--- | :--- | :--- |
| **FP-1**<br>Before DB Tx | Neither DB nor JSONL touched. | None. Clean state. | Transaction never started. Normal retry. |
| **FP-2**<br>During DB Tx | DB uncommitted; JSONL untouched. | None. PostgreSQL MVCC rolls back uncommitted changes. | Clean rollback. Zero orphan records. |
| **FP-3**<br>After DB Commit, Before JSONL Append | DB committed event $S$; JSONL has only $S-1$. | **Outbox Desynchronization**: DB has record of review, but offline audit ledger lacks it. Gate 9 export would fail. | **Reconciliation Catch-Up**: On next startup or pre-export check, system queries missing DB events ($> S-1$) and appends them to JSONL before proceeding. |
| **FP-4**<br>During JSONL Append | DB has $S$; JSONL has partial/torn trailing line at byte offset $B$. | **Corrupted Ledger File**: File cannot be parsed by RFC 8785 JSON decoder. | **Controlled Quarantine & Repair**: Detected on next open. Under `.lock`, torn bytes are quarantined; clean canonical JSON for event $S$ is re-fetched from DB and appended. Zero data loss. |
| **FP-5**<br>After JSONL Append, Before Checkpoint | DB has $S$; JSONL has $S$; `.chkpt` has HMAC for $S-1$. | **Checkpoint Inconsistency**: Ledger verification fails due to outdated terminal checkpoint. | **Deterministic HMAC Regeneration**: Under `.lock`, system verifies full JSONL chain, re-computes HMAC for terminal event $S$, and updates checkpoint. |
| **FP-6**<br>During Checkpoint Persistence | `.chkpt` write interrupted; checkpoint file empty or torn. | **Checkpoint File Corruption**: Checkpoint signature invalid. | **Atomic Rename Protocol**: Checkpoint is always written to `.chkpt.tmp` then renamed via atomic OS syscall. If `.chkpt.tmp` is torn, original `.chkpt` remains valid or is cleanly regenerated from JSONL. |

---

### 4.2 Corrected Architectural Rule

#### 1. Zero-Schema-Change Transactional Replay Protocol
The existing `audit_events` table ([`src/clindoc/db/models.py:801-860`](file:///d:/daa/src/clindoc/db/models.py#L801-L860)) ALREADY stores:
- `sequence_number` (BigInteger, strictly monotonic)
- `current_event_hash` (String(64), SHA-256)
- `previous_event_hash` (String(64))
- `metadata_json` (JSONB)
- `actor_id`, `event_type`, `entity_type`, `entity_id`, `action`, `timestamp`.

Therefore, **`audit_events` serves as the authoritative, durable transactional outbox without requiring any schema migrations or schema freeze exceptions**.

```
                   TRANSACTIONAL DUAL-TIER RECONCILIATION
                   
           [Gate 9 Export or ReviewAction Startup]
                              │
                              ▼
           Acquire Advisory File Lock on `<ledger_path>.lock`
                              │
                              ▼
           Query PostgreSQL: S_{db_max} = SELECT MAX(sequence_number) FROM audit_events
           Query JSONL:      S_{file_max} = Read last valid record sequence
                              │
        ┌─────────────────────┴─────────────────────┐
        │                                           │
  S_{db_max} == S_{file_max}                  S_{db_max} > S_{file_max}
        │                                           │
  Verify Terminal Hash                              │ (Outbox Catch-Up)
  H_{db} == H_{file}?                               ▼
  ┌─────┴─────┐                      Fetch missing events from PostgreSQL:
  │ Match     │ Mismatch             SELECT * FROM audit_events 
  ▼           ▼                      WHERE sequence_number > S_{file_max}
[CONSISTENT] [FAIL CLOSED:           ORDER BY sequence_number ASC
 Gate 9 OK]   SplitBrainError]                      │
                                                    ▼
                                     Format canonical RFC 8785 JSON lines
                                     Append to JSONL & fsync file/dir
                                     Update HMAC Checkpoint atomically
                                                    │
                                                    ▼
                                            [LEDGER RECONCILED]
```

#### 2. Gate 9 Behavior During Incomplete Synchronization
If `S_{db_max} > S_{file_max}`, Gate 9 **FAILS CLOSED** and refuses to export any FHIR bundle until the synchronous reconciliation replay completes and `verify_ledger()` passes 100%.

#### 3. Precision Regarding OS Durability Guarantees

##### POSIX Systems (Linux, macOS)
- `f.flush()` flushes Python runtime user buffers to OS kernel page cache.
- `os.fsync(f.fileno())` flushes kernel dirty pages to storage controller.
- **Directory Inode Durability**: Updating file size or creating sidecars modifies directory metadata. POSIX requires opening the parent directory handle and executing `os.fsync(dir_fd)` to guarantee directory entry persistence across power loss.
- Supported filesystems: ext4 (with `data=ordered` or `journal`), XFS, APFS. Network mounts (NFS, SMB, CIFS) are **STRICTLY PROHIBITED** due to unsafe lock leasing and cache incoherency.

##### Windows Systems (NTFS)
- `FlushFileBuffers(handle)` (called by Python's `os.fsync()`): Flushes file data and file metadata (size, timestamps) to the storage device.
- **Critical Windows Limitations**:
  1. Windows does **NOT** support calling `FlushFileBuffers` on directory handles (`ERROR_ACCESS_DENIED` or `ERROR_INVALID_HANDLE`). Calling `os.fsync` on a directory on Windows will raise an `OSError`.
  2. NTFS directory durability relies on the NTFS `$LogFile` metadata transaction journal. While NTFS guarantees filesystem structural consistency after power loss, it does **not** provide synchronous user-level durability barriers for directory entry modifications.
  3. Storage Device Onboard Cache: Unless write caching is disabled in Windows Device Manager or `FILE_FLAG_WRITE_THROUGH` is passed at the Win32 API layer, drive-level volatile RAM cache may still drop unwritten sectors on abrupt power cuts.

---

### 4.3 Implementation Boundary
- File: [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py)
  - `ReviewAuditLedger.append_event`: Implement `.lock` advisory locking, platform-specific fsync, and atomic checkpoint rename.
  - `ReviewAuditLedger.reconcile_with_db`: Implement the outbox replay protocol reading from `audit_events`.
  - `ReviewAuditLedger.verify_ledger`: Gate 9 validation method.

---

### 4.4 Required Adversarial Tests
1. `test_crash_recovery_db_committed_file_unwritten_replayed_cleanly`:
   - Commit 3 events directly to PostgreSQL `audit_events` without appending to JSONL.
   - Invoke `reconcile_with_db()`.
   - Assert all 3 events are appended to JSONL with matching hashes and valid checkpoint.
2. `test_crash_recovery_torn_write_quarantined_and_repaired`:
   - Simulate torn write: write 15 bytes of invalid JSON to JSONL.
   - Run reconciliation. Assert `.corrupt.<timestamp>` is preserved; clean record is fetched from DB; file verifies.
3. `test_gate9_refuses_export_when_outbox_unreconciled`:
   - Insert un-reconciled event into DB.
   - Attempt FHIR Gate 9 export.
   - Assert export raises `AuditLedgerUnsynchronizedError`.
4. `test_multiprocess_concurrency_stress`:
   - 8 independent OS worker processes, 50 writes each (400 total).
   - Assert zero sequence collisions, zero gaps, valid HMAC.

---

### 4.5 Objective Acceptance Criteria
1. PostgreSQL and JSONL remain mathematically identical across all six crash points.
2. Gate 9 strictly fails closed on any sequence gap or checkpoint mismatch.
3. Zero silent truncation: all corruptions produce quarantined copies and require verifiable replay.
4. Concurrency test with 8 independent OS processes completes without deadlocks or errors.

---

### 4.6 Unresolved Dependencies / Approval Requirements
- **Database Schema Decision**: Fully resolved with **ZERO schema changes**. Uses existing `audit_events` table. No migration or approval required.

---

## 5. DEF-004 Remediation: Normative FHIR R4B Structure & CLI Governance Boundary

### 5.1 The Exact Failure Scenarios

#### Scenario 1: Normative Violation of FHIR R4B `Bundle` Resource
- In `v2`, the design proposed:
  ```json
  "extension": [
    {
      "url": "http://clindoc.org/fhir/StructureDefinition/export-authorization",
      "valueCode": "UNAUTHORIZED_RESEARCH_DRAFT"
    }
  ]
  ```
- **Normative FHIR R4B 4.3.0 Base Reality**:
  - In FHIR R4B, `Resource` has fields: `id`, `meta`, `implicitRules`, `language`.
  - `DomainResource` extends `Resource` and adds: `text`, `contained`, `extension`, `modifierExtension`.
  - **`Bundle` extends `Resource` directly, NOT `DomainResource`!**
  - Therefore, `Bundle.extension` **DOES NOT EXIST** in normative FHIR R4B.
  - Feeding `{"resourceType": "Bundle", "extension": ...}` to the project's actual validator ([`src/clindoc/fhir/validator.py`](file:///d:/daa/src/clindoc/fhir/validator.py) calling `fhir.resources.bundle.Bundle.parse_raw`) causes an immediate `ValidationError` ("Extra inputs are not permitted")!

#### Scenario 2: Confounding Resource Clinical Status with Governance Metadata
- Falsely setting `Observation.status = "preliminary"` on a final laboratory test or `MedicationRequest.status = "draft"` on a signed prescription distorts clinical truth and corrupts downstream clinical decision support.

---

### 5.2 Corrected Architectural Rule

#### 1. Normative Structure for Governance & Authorization Metadata
In strict compliance with FHIR R4B 4.3.0 Base, all governance, review, and authorization metadata on a `Bundle` MUST be carried via **`Bundle.meta.tag`**, **`Bundle.meta.security`**, and standard **`Bundle.identifier`**, which are normative elements of `Resource.meta`:

```json
{
  "resourceType": "Bundle",
  "id": "bundle-unreviewed-draft-98214",
  "meta": {
    "security": [
      {
        "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationValue",
        "code": "SUBSET",
        "display": "Resource is a partial/unreviewed extraction subset"
      }
    ],
    "tag": [
      {
        "system": "http://clindoc.org/fhir/tag/governance-status",
        "code": "UNREVIEWED_CLI_DRAFT",
        "display": "Unreviewed Automated Pipeline Draft — Not for Clinical Use"
      },
      {
        "system": "http://clindoc.org/fhir/tag/export-authorization",
        "code": "UNAUTHORIZED_RESEARCH_DRAFT",
        "display": "Unauthorized Research Draft — Prohibited from Clinical Ingestion"
      }
    ]
  },
  "identifier": {
    "system": "http://clindoc.org/fhir/identifier/export-authorization",
    "value": "UNAUTHORIZED_RESEARCH_DRAFT"
  },
  "type": "collection",
  "timestamp": "2026-10-31T21:55:00Z",
  "entry": [...]
}
```

#### 2. Verification Against Project FHIR Validator
This structure was validated against [`src/clindoc/fhir/validator.py`](file:///d:/daa/src/clindoc/fhir/validator.py):
- `Bundle.parse_raw()` parses `meta.tag`, `meta.security`, and `identifier` without error.
- Contains zero extra fields on `Bundle`.
- Passes schema validation, referential integrity, and resource count verification.

#### 3. Standard vs Custom Terminology Distinction
- **Standard HL7 Terminology**:
  - `Bundle.meta.security` bound to `http://terminology.hl7.org/CodeSystem/v3-ObservationValue` with code `SUBSET`.
- **Custom Institutional Terminology**:
  - `Bundle.meta.tag` using namespace `http://clindoc.org/fhir/tag/governance-status` and `http://clindoc.org/fhir/tag/export-authorization`.

#### 4. Clear Distinction: Implemented Repository Capability vs External EHR Gateway
- **Repository Capability (Existing & Remediated)**:
  - `src/clindoc/cli.py` unconditionally stamps `UNREVIEWED_CLI_DRAFT`, `UNAUTHORIZED_RESEARCH_DRAFT`, and outputs `GOVERNANCE_NOTICE.txt`.
  - `FHIRExportService` verifies Gate 9 audit ledger proof before omitting draft tags.
  - CLI parser strictly omits any bypass flags (`--force`, `--approved`, `--skip-review`).
- **External Institutional EHR Gateway (Target System Interface Contract)**:
  - Any external EHR ingestion engine (Mirth Connect, Epic Bridges, Cerner Open Engine) configured with ClinicalDoc's interface contract will inspect `Bundle.meta.tag` and reject (HTTP 403 Forbidden) any bundle bearing `UNREVIEWED_CLI_DRAFT` or `UNAUTHORIZED_RESEARCH_DRAFT`.

---

### 5.3 Implementation Boundary
- File: [`src/clindoc/cli.py:244-270`](file:///d:/daa/src/clindoc/cli.py#L244-L270)
  - Apply normative `meta.tag` and `identifier` tags.
  - Emit `GOVERNANCE_NOTICE.txt`.
  - Prohibit bypass CLI arguments.
- File: [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py)
  - Maintain clinical status of resources (`Observation.status="final"`) while applying bundle-level governance tags.

---

### 5.4 Required Adversarial Tests
1. `test_cli_bundle_passes_project_fhir_validator`:
   - Run `clindoc process` and validate generated bundle using `FHIRValidator.validate_bundle(bundle)`.
   - Assert `val_report.is_valid is True` and `len(val_report.errors) == 0`.
2. `test_cli_bundle_contains_no_illegal_bundle_extension`:
   - Inspect bundle dict keys. Assert `"extension"` not in `bundle_dict`.
   - Assert `"meta"` contains `"tag"` with `UNREVIEWED_CLI_DRAFT`.
3. `test_cli_parser_has_no_bypass_arguments`:
   - Inspect `create_parser().parse_args(["--help"])`.
   - Assert neither `--force`, `--approved`, nor `--skip-review` is recognized.

---

### 5.5 Objective Acceptance Criteria
1. Bundle conforms 100% to FHIR R4B 4.3.0 Base and passes `FHIRValidator`.
2. Zero illegal `Bundle.extension` elements emitted.
3. Resource clinical statuses reflect source documents without distortion.
4. CLI draft output cannot be mistaken for or converted into approved clinical exports.

---

### 5.6 Unresolved Dependencies / Approval Requirements
- **None**: Conforms to existing Pydantic / FHIR models.

---

## 6. Comprehensive Verification Matrix & Acceptance Roadmap

| Defect ID | Technical Root Cause | Architectural Remediation | Verification Test Harness | Status |
| :---: | :--- | :--- | :--- | :---: |
| **F-12** | Single-hop parent inspection; phantom descendant branching. | Lineage mutex on root claim $v_1$; iterative multi-hop traversal; leaf-only invariant. | `tests/test_claim_review_service.py` | Ready for Implementation |
| **DEF-002** | Confounding clinician plan with patient intake; dormant REQ-44 parameter. | Strict 7-category evidence matrix; "Continue" mapped to `Request(intent="plan")`; default `True`. | `tests/test_fhir_export.py`<br>`tests/test_phase523_milestone4_4.py` | Ready for Implementation |
| **DEF-003** | Asynchronous dual-tier crash vulnerability; missing fsync/mutex. | Outbox replay reconciliation from `audit_events`; OS-specific durability barriers; zero schema change. | `tests/test_phase523_milestone4_2.py`<br>`tests/test_audit_service.py` | Ready for Implementation |
| **DEF-004** | Invalid `Bundle.extension` on normative Resource; CLI bypass risk. | Normative `Bundle.meta.tag` and `meta.security`; strict CLI draft boundary; zero bypass flags. | `tests/test_cli.py`<br>`tests/test_fhir_validator.py` | Ready for Implementation |

---

## 7. Implementation Roadmap & Staged Authorization Sequence

```
                      EXECUTION SEQUENCE (WHEN AUTHORIZED)

   [Stage 1: F-12 Lineage Mutex & Multi-Hop Traversal (service.py)]
   - Add lineage root lock ($v_1$) in `accept_claim`, `reject_claim`, `edit_claim`
   - Implement bounded while-loop traversal up to depth 50
   - Enforce leaf-only invariant & fail-closed actor provenance checks
                         │
                         ▼
   [Stage 2: DEF-002 REQ-44 Lifecycle Activation & Precision Mapping (mapper.py, fhir_export.py)]
   - Invert mapper default: `enforce_req44_lifecycle = True`
   - Map "Continue" directives to `MedicationRequest(intent="plan")`
   - Enforce detached modifier suppression rule (*"Completed course"*)
                         │
                         ▼
   [Stage 3: DEF-003 Transactional Outbox Replay & OS Durability (service.py)]
   - Implement `ReviewAuditLedger.reconcile_with_db` reading from `audit_events`
   - Add `.lock` advisory locking, POSIX directory fsync, NTFS attribute handling
   - Integrate Gate 9 fail-closed synchronization check
                         │
                         ▼
   [Stage 4: DEF-004 Normative FHIR R4B Metadata & CLI Draft Boundary (cli.py)]
   - Apply normative `Bundle.meta.tag` and `Bundle.meta.security`
   - Output `GOVERNANCE_NOTICE.txt` warning sidecar
   - Verify 100% compliance with `FHIRValidator.validate_bundle`
                         │
                         ▼
   [Stage 5: Full Regression & Test Suite Execution]
   - Run full repository test suite
   - Verify $\ge 1,485\text{ passing}$, exactly 5 historical baseline failures, zero regressions
```

---

## 8. Final Architectural Declaration

This document represents the completed, fully reconciled architectural specification for all P0 security and architecture remediations. 
- No source code, tests, configs, database models, or benchmarks have been modified.
- All 24 modified tracked files and 68 untracked files remain intact.
- Zero defects are marked resolved until code implementation is authorized and executed.
- No Git operations were performed.
