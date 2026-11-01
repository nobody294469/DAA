# ClinicalDoc — P0 Security & Architecture Remediation Design (Version 2)

**Document ID**: `CLINICALDOC_P0_SECURITY_REMEDIATION_DESIGN_v2.md`  
**Date**: October 31, 2026  
**Auditor & Architect**: Principal Software Architect, Senior Clinical NLP/AI Engineer & FHIR/Security Specialist  
**Target Repository**: `ClinicalDoc` (`clindoc2fhir`)  
**Design Status**: REVISED ARCHITECTURAL SPECIFICATION — READY FOR REVIEW  
**Review Notice**: Documentation only. Zero source code, test code, configuration, benchmark data, or Git state modified. All existing workspace changes (including 24 modified tracked files and 68 untracked files) remain strictly preserved.

---

## 1. Executive Summary & Remediation Governance

This revised design specification supersedes `v1` and addresses all findings identified during independent technical and clinical review. It establishes mathematically and clinically rigorous remediations for the four critical security, safety, and architectural findings in the ClinicalDoc platform:

1. **F-12 (P0 — Security / Governance)**: Multi-hop Four-Eyes ancestry vulnerability in [`src/clindoc/review/service.py:838-852`](file:///d:/daa/src/clindoc/review/service.py#L838-L852).
2. **DEF-002 (P0 — Clinical Safety / FHIR Semantics)**: Dormant REQ-44 lifecycle enforcement in production callers and ambiguous temporal mapping in [`src/clindoc/services/fhir_export.py:743, 1029`](file:///d:/daa/src/clindoc/services/fhir_export.py#L743) and [`src/clindoc/fhir/mapper.py:2397`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2397).
3. **DEF-003 (P0 — Integrity / Concurrency)**: Unlocked concurrent writes and uncoordinated recovery in the offline JSONL audit ledger in [`src/clindoc/review/service.py:335-385`](file:///d:/daa/src/clindoc/review/service.py#L335-L385).
4. **DEF-004 (P1 — Pipeline Architecture & Governance)**: Stateless CLI governance bypass producing untagged FHIR bundles without review in [`src/clindoc/cli.py:244-270`](file:///d:/daa/src/clindoc/cli.py#L244-L270).

### Core Non-Negotiable Architectural Invariants
- **Fail-Closed Clinical Safety**: Any missing actor identity, dangling ancestor pointer, cyclic lineage, ungrounded temporal linkage, or audit file corruption halts processing immediately and fails closed.
- **Strict Separation of Governance Tiers**: Resource clinical status (e.g. `Observation.status`) is disentangled from bundle governance, claim review state, and export authorization.
- **Controlled Audit Recovery**: Zero silent automatic truncation of corrupted audit logs. The system fails closed on ledger inconsistency and mandates an authorized, quarantined recovery procedure.
- **Zero Regression on Baseline**: The 1,466 passing tests must remain 100% passing. The 5 historical baseline failures must remain isolated.
- **Frozen Benchmarks**: Zero modification to frozen benchmark manifests (`external_eval/`, `data/m2_verification_v3/`, `experiments/FINAL_REPORT_DATA_SHEET.md`), models, or split definitions.

---

## 2. F-12 Remediation: Comprehensive Ancestry Graph & Multi-Version Concurrency

### 2.1 The Vulnerability
In [`src/clindoc/review/service.py:838-852`](file:///d:/daa/src/clindoc/review/service.py#L838-L852), `ClaimReviewService._check_four_eyes` inspects only the target claim's direct parent (`prev_claim = session.get(Claim, claim.supersedes_claim_id)`). In an edit chain:
$$\text{Claim } v_1 \xrightarrow{\text{Actor A}} v_2 \xrightarrow{\text{Actor B}} v_3 \xrightarrow{\text{Actor C}} v_4$$
When Actor A attempts to execute `accept_claim` on $v_4$:
- $v_4.\text{created\_by} = \text{Actor C} \neq \text{Actor A}$.
- $v_4.\text{supersedes\_claim\_id}$ points to $v_3$.
- $v_3.\text{created\_by} = \text{Actor B} \neq \text{Actor A}$.
- Single-hop traversal terminates. Actor A is permitted to approve $v_4$, directly violating the Mode 0 Four-Eyes principle (PD-002, PD-009, PD-015) by accepting their own modified clinical substance.

---

### 2.2 Actor-Provenance Policy Across All Claim Versions

Every claim row in the database belongs to one of three origin categories, each carrying strict provenance invariants:

| Version Type | Origin (`claim.origin`) | Actor Identity Fields | Invariant & Fail-Closed Rule |
| :--- | :--- | :--- | :--- |
| **Pipeline Extraction ($v_1$)** | `ClaimOrigin.PIPELINE` | `created_by = NULL`<br>`payload["model_run_id"]` present | Attributed to automated pipeline Device. Document uploader identity (`Document.uploaded_by`) is bound to the document. If `uploaded_by` is missing for a clinical document, review fails closed. |
| **Reviewer Modification ($v_{\ge 2}$)** | `ClaimOrigin.REVIEWER` | `created_by` (UUID)<br>`payload["edited_by"]` (UUID str) | **Strict Provenance**: Both fields must be non-null, valid UUIDs referencing an existing, active `User` account. If either field is missing, null, or unresolvable, the ancestry is corrupted $\to$ **FAIL CLOSED** (`CorruptedClaimAncestryError`). |
| **System Ingestion / Import** | `ClaimOrigin.SYSTEM` | `created_by` (System User UUID)<br>`payload["service_account"]` | System service account UUID must be present and verified against authorized service roles. |

**Universal Policy**: If any claim version in an ancestry chain cannot establish verified actor identity where mandated by policy, the ancestry cannot be audited. The review action MUST fail closed.

---

### 2.3 Comprehensive Ancestry Traversal Algorithm

```
                           COMPLETE ANCESTRY TRAVERSAL
                           
             [Target Claim: v_k]  (Actor attempting ACCEPT: X)
                      │
                      ▼
         ┌────────────────────────────────────────────────────────┐
         │ 1. Acquire PESSIMISTIC LOCKS on full chain:            │
         │    Lock v_k, v_{k-1}, ..., v_1 with FOR UPDATE         │
         └────────────────────────┬───────────────────────────────┘
                                  │
                                  ▼
         ┌────────────────────────────────────────────────────────┐
         │ 2. Check Target Claim v_k:                             │
         │    Is X == v_k.created_by OR X == v_k.edited_by?       │
         └────────────────────────┬───────────────────────────────┘
                                  │
                         YES ─────┴─────► RAISE FourEyesViolationError
                                  │ NO
                                  ▼
         ┌────────────────────────────────────────────────────────┐
         │ 3. Walk Ancestry Chain: curr = v_k                     │
         │    While curr.supersedes_claim_id is not None:         │
         │      - Increment depth; if depth > 50: FAIL CLOSED     │
         │      - Check curr.id not in visited (Cycle Detection)  │
         │      - Fetch parent = session.get(Claim, parent_id)    │
         │      - If parent is None: FAIL CLOSED (Dangling Link)  │
         │      - If parent.doc_id != v_k.doc_id: FAIL CLOSED     │
         │      - If parent.origin == REVIEWER and actor missing: │
         │            FAIL CLOSED (Unverifiable Provenance)       │
         │      - If X == parent.created_by OR X == parent.editor:│
         │            RAISE FourEyesViolationError                │
         │      - If parent.review_state != EDITED:               │
         │            FAIL CLOSED (Ancestor State Invalidation)   │
         │      - curr = parent                                   │
         └────────────────────────┬───────────────────────────────┘
                                  │
                                  ▼
         ┌────────────────────────────────────────────────────────┐
         │ 4. Check Document Ingestion Provenance:                │
         │    Is X == Document.uploaded_by?                       │
         └────────────────────────┬───────────────────────────────┘
                                  │
                         YES ─────┴─────► RAISE FourEyesViolationError
                                  │ NO
                                  ▼
                          [ACCEPT PERMITTED]
```

#### Detailed Algorithm
1. **Initialize Traversal State**:
   - `actor_str = str(actor_uuid)`
   - `visited_ids: set[uuid.UUID] = {claim.id}`
   - `ancestor_actors: set[str] = set()`
   - `MAX_ANCESTRY_DEPTH = 50`
2. **Pessimistic Multi-Row Locking**:
   - Under database transaction (isolation level `READ COMMITTED` or `REPEATABLE READ`), execute iterative locking:
     ```python
     # Lock target claim row
     target = session.query(Claim).filter(Claim.id == claim_id).with_for_update().one_or_none()
     if target is None or target.version != expected_version:
         raise OptimisticLockError(f"Claim {claim_id} concurrently modified or missing")
     
     # Lock all ancestor rows in order
     curr = target
     locked_ancestors = [target]
     while curr.supersedes_claim_id is not None:
         parent = session.query(Claim).filter(Claim.id == curr.supersedes_claim_id).with_for_update().one_or_none()
         if parent is None:
             raise CorruptedClaimAncestryError(f"Dangling ancestry link: {curr.supersedes_claim_id}")
         locked_ancestors.append(parent)
         curr = parent
     ```
3. **Validate Target Claim**:
   - If `target.created_by is not None and str(target.created_by) == actor_str`:
     raise `FourEyesViolationError(f"Actor '{actor_str}' created version {target.version} and cannot accept it.")`
   - If `target.payload and str(target.payload.get("edited_by")) == actor_str`:
     raise `FourEyesViolationError(f"Actor '{actor_str}' edited version {target.version} and cannot accept it.")`
4. **Iterative Ancestry Inspection**:
   - `curr = target`
   - `depth = 0`
   - While `curr.supersedes_claim_id is not None`:
     - `depth += 1`
     - If `depth > MAX_ANCESTRY_DEPTH`:
       raise `CorruptedClaimAncestryError(f"Ancestry chain exceeded depth limit {MAX_ANCESTRY_DEPTH}")`
     - `parent_id = curr.supersedes_claim_id`
     - If `parent_id in visited_ids`:
       raise `CorruptedClaimAncestryError(f"Cyclic claim ancestry detected at node {parent_id}")`
     - `visited_ids.add(parent_id)`
     - `parent = session.get(Claim, parent_id)`
     - **Integrity Validation**:
       - If `parent is None`: raise `CorruptedClaimAncestryError(f"Missing ancestor {parent_id}")`
       - If `parent.document_id != target.document_id`: raise `CorruptedClaimAncestryError("Cross-document ancestry injection")`
       - If `parent.review_state != ClaimReviewState.EDITED and parent.review_state.value != "edited"`:
         raise `CorruptedClaimAncestryError(f"Ancestor {parent.id} has invalid review state '{parent.review_state}' (expected EDITED)")`
     - **Provenance Extraction**:
       - If `parent.origin == ClaimOrigin.REVIEWER`:
         - `p_creator = str(parent.created_by) if parent.created_by else None`
         - `p_editor = str(parent.payload.get("edited_by")) if (parent.payload and parent.payload.get("edited_by")) else None`
         - If not p_creator and not p_editor:
           raise `CorruptedClaimAncestryError(f"Reviewer ancestor {parent.id} lacks required actor provenance")`
         - If p_creator: `ancestor_actors.add(p_creator)`
         - If p_editor: `ancestor_actors.add(p_editor)`
       - If `parent.origin == ClaimOrigin.SYSTEM`:
         - `ancestor_actors.add(str(parent.created_by))`
     - `curr = parent`
5. **Assert Separation of Duties**:
   - If `actor_str in ancestor_actors`:
     raise `FourEyesViolationError(f"Actor '{actor_str}' modified ancestral claim in this lineage and cannot accept descendant {target.id}")`
6. **Assert Uploader Separation**:
   - If `target.document_id`:
     - `doc = session.get(Document, target.document_id)`
     - If `doc and doc.uploaded_by and str(doc.uploaded_by) == actor_str`:
       raise `FourEyesViolationError(f"Operator '{actor_str}' who uploaded document '{doc.id}' cannot accept clinical claims.")`

---

### 2.4 Multi-Version Concurrency Guarantees

#### Concurrency Failure Modes & Defenses
- **Concurrent Edit of Target Claim ($v_k \to v_{k+1}$)**:
  - Defended by `Claim.with_for_update()` and `_check_optimistic_lock(claim, expected_version)`. If another transaction edits $v_k$, its version increments to $k+1$. The acceptance transaction detects the version mismatch and rolls back with `OptimisticLockError`.
- **Concurrent Branching / Ancestor Modification**:
  - Defended by locking *all* ancestor rows with `with_for_update()`. Any concurrent transaction attempting to edit, re-parent, or delete any node in the lineage blocks until the acceptance transaction commits or aborts.
- **Transaction Isolation**:
  - PostgreSQL transaction isolation level MUST be at least `READ COMMITTED` with explicit row-level locking (`with_for_update()`), or `REPEATABLE READ`. Under `REPEATABLE READ`, any concurrent modification to an ancestor row triggers a serialization failure (`could not serialize access due to concurrent update`), cleanly rolling back the acceptance attempt.

---

### 2.5 Adversarial Test Specifications

1. `test_four_eyes_multi_hop_three_distinct_editors`:
   - Lineage: $v_1 \xrightarrow{\text{A}} v_2 \xrightarrow{\text{B}} v_3 \xrightarrow{\text{C}} v_4$.
   - Assert A, B, C are each independently rejected when attempting `accept_claim(v_4)`.
   - Assert independent clinician D succeeds.
2. `test_four_eyes_cyclic_ancestry_fails_closed`:
   - Corrupted lineage: $v_1 \to v_2 \to v_3 \to v_1$.
   - Assert `accept_claim` raises `CorruptedClaimAncestryError`.
3. `test_four_eyes_dangling_ancestor_pointer_fails_closed`:
   - Claim references non-existent `supersedes_claim_id = uuid.uuid4()`.
   - Assert `accept_claim` raises `CorruptedClaimAncestryError`.
4. `test_four_eyes_missing_reviewer_actor_id_fails_closed`:
   - Claim $v_2$ has `origin = REVIEWER` but `created_by = None` and `payload["edited_by"] = None`.
   - Assert `accept_claim` on $v_3$ raises `CorruptedClaimAncestryError`.
5. `test_four_eyes_concurrent_edit_during_ancestry_validation`:
   - Spawn two parallel processes: Process 1 calls `accept_claim(v_3)`, Process 2 calls `edit_claim(v_3)`.
   - Assert exactly one succeeds and the other fails with `OptimisticLockError` or serialization error. Zero split-brain states permitted.

---

## 3. DEF-002 Remediation: REQ-44 Lifecycle Semantics & Grounding Boundary

### 3.1 Production Caller Inventory & Activation
The parameter `enforce_req44_lifecycle` must be activated unconditionally across the system:

1. **Mapper Inversion**: In [`src/clindoc/fhir/mapper.py:2397`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2397), change default parameter from `False` to `True`:
   ```python
   def claims_to_fhir_bundle(
       claims: Sequence[Claim],
       evidence_map: dict[str, Evidence],
       verification_map: dict[str, VerificationResult],
       ...
       enforce_req44_lifecycle: bool = True,  # Inverted default
   ) -> Bundle:
   ```
2. **Explicit Enforcement at Production Call Sites**:
   - [`src/clindoc/services/fhir_export.py:743`](file:///d:/daa/src/clindoc/services/fhir_export.py#L743) (`export_document_as_fhir`): pass `enforce_req44_lifecycle=True`.
   - [`src/clindoc/services/fhir_export.py:1029`](file:///d:/daa/src/clindoc/services/fhir_export.py#L1029) (`export_batch_as_fhir`): pass `enforce_req44_lifecycle=True`.
   - [`src/clindoc/cli.py:244`](file:///d:/daa/src/clindoc/cli.py#L244) (`process_document`): pass `enforce_req44_lifecycle=True`.
   - [`src/clindoc/testing/synthetic_export.py:80`](file:///d:/daa/src/clindoc/testing/synthetic_export.py#L80): pass `enforce_req44_lifecycle=True`.

---

### 3.2 Formal Evidence Requirements for the Six Temporal Categories

To prevent semantic hallucination in FHIR output, the mapper must enforce strict syntactic and evidentiary criteria for each clinical scenario:

```
                  TEMPORAL LIFECYCLE DECISION TREE
                  
             [Extracted Medication Mention & Claims]
                                │
                                ▼
       Is there an explicit, syntactically bound temporal modifier?
                                │
        ┌───────────────────────┴───────────────────────┐
        │ YES                                           │ NO
        ▼                                               ▼
  Classify Modifier Type:                      Default Clinical Assertion:
  ├─ Past Completion ───────────► Category 1   ├─ Rx Pad / Order Block ──► Category 2
  ├─ Prescriber Directives ─────► Category 2   └─ Clinic Note Narrative ─► Category 3
  ├─ Ongoing / Active ──────────► Category 3
  ├─ Patient Non-Adherence ─────► Category 4
  ├─ Referenced Rx Cancellation ─► Category 5
  └─ Unreferenced Discontinue ──► Category 6
```

#### Detailed Specification Matrix

| Category | Clinical Category Name | Evidentiary & Syntactic Requirements | Resulting FHIR Resource & Status | Safety Justification & Failure Behavior |
| :---: | :--- | :--- | :--- | :--- |
| **1** | **Completed Historical Courses** | Past tense completion predicate ("completed", "finished", "took for 7 days in March") strictly within the same clause or dependency parse span as the drug anchor. | `MedicationStatement`<br>`status: "completed"` | Must NEVER emit a `MedicationRequest`. Fabricating an active prescription for remote past history creates acute clinical risk of accidental re-order. |
| **2** | **New Prescription Orders** | Active prescriber directive ("Rx", "Start", "Prescribe", "Dispense", or presence within an authenticated Rx Pad layout with prescriber identity). | `MedicationRequest`<br>`status: "active"`<br>`intent: "order"` | Emitted only when prescriber order intent is grounded. Unsigned historical mentions are barred from this category. |
| **3** | **Ongoing Medication Statements** | Narrative history or medication reconciliation ("Patient takes", "Continues on", "Home meds:") without current prescription issuance metadata. | `MedicationStatement`<br>`status: "active"` | Represents verified patient medication intake without asserting that a new order was issued. |
| **4** | **Patient-Reported Cessation** | Explicit predicate of patient-initiated cessation ("patient stopped", "patient discontinued due to rash", "patient ceased"). | `MedicationStatement`<br>`status: "stopped"` | Accurately records that the patient is no longer taking the drug, without falsely altering or cancelling the prescriber's official order. |
| **5** | **Prescriber Cancellation (Linked to Existing Order)** | Explicit clinician cancellation directive referencing an identifiable order or Rx number ("Cancel order #4812", "Discontinue Metoprolol order dated 10/12"). | `MedicationRequest`<br>`status: "cancelled"` | Emitted ONLY when the cancellation syntactically binds to an existing order reference. Sets `priorPrescription`. |
| **6** | **Unreferenced or Ambiguous Discontinuation** | Bare discontinuation verb ("Discontinue Lisinopril", "Stop Prednisone") in a narrative note without order reference or clear patient-vs-doctor actor attribution. | **FAILS CLOSED**<br>Claim is **SUPPRESSED** from FHIR export; audit ledger records suppression. | **Critical Safety Invariant**: The system must NOT guess whether to cancel an unreferenced order or record a patient cessation. Suppressed with audit code `REQ-44: Unreferenced cancellation lacks verifiable prior order reference`. |

---

### 3.3 Resolution of the Ambiguous Case: *"Completed course. Continue Prednisone 10mg"*

#### The Problem
In clinical notes, detached sentences such as *"Completed course. Continue Prednisone 10mg"* frequently appear. A naive heuristic might link "Completed course" to Prednisone, or conversely, fabricate an active `MedicationRequest` while discarding the completion token.

#### The Remediated Rule
1. **Analyze Clause 1 (*"Completed course"*):**
   - The phrase *"Completed course"* lacks a medication entity anchor within its syntactic clause boundary $[L_k, R_k)$.
   - Because no drug name is syntactically bound, the system **MUST NOT** attach this completion predicate to an adjacent drug mention.
   - **Action**: Any extraction claiming a completed course for Prednisone based on this detached token is **SUPPRESSED** (`SUPPRESSED_UNBOUND_TEMPORAL_MODIFIER`).
2. **Analyze Clause 2 (*"Continue Prednisone 10mg"*):**
   - Contains explicit drug anchor: `Prednisone`.
   - Contains dose: `10mg`.
   - Contains continuation predicate: `"Continue"`.
   - Context: In a narrative clinic note without Rx pad order metadata, this represents ongoing therapy, **NOT a new prescription order**.
   - **Action**: Emit `MedicationStatement(status="active")`.
   - **Prohibition**: The system **MUST NOT** emit `MedicationRequest(status="active")` because there is no prescriber order directive ("Rx", "Dispense", DEA signature).

---

### 3.4 Integration & Regression Test Plan for DEF-002

- `tests/test_fhir_export.py`:
  - `test_export_service_historical_mention_emits_statement_unknown`: Asserts that `FHIRExportService.export_document_as_fhir` converts remote history mentions into `MedicationStatement(status="unknown")`.
  - `test_export_service_suppresses_unreferenced_cancellation`: Asserts that unreferenced cancellations are suppressed from `Bundle.entry` and logged in `audit_trail`.
  - `test_export_batch_enforces_req44`: Asserts batch export respects REQ-44 lifecycle rules across multiple mixed documents.
- Preservation of the 19 existing passing tests in `tests/test_phase523_milestone4_4.py`:
  - `test_req44_leading_duration`
  - `test_req44_taxonomy_case_b_multiple_regimen_durations`
  - `test_req44_lifecycle_unreferenced_cancellation_fails_closed`
  - `test_req44_lifecycle_patient_cessation_emits_stopped`
  - `test_req44_lifecycle_historical_maps_to_unknown_not_completed`

---

## 4. DEF-003 Remediation: Audit Ledger Architecture, Concurrency & Controlled Recovery

### 4.1 Architectural Evaluation: JSONL vs PostgreSQL-Backed Sequencing

| Architecture Dimension | Relational Database (PostgreSQL `audit_events`) | Offline JSONL File (`ReviewAuditLedger`) | Dual-Tier Hybrid Architecture (Selected) |
| :--- | :--- | :--- | :--- |
| **Sequencing Authority** | **ACID Guaranteed**: Built-in monotonic sequence, row-level locks, transactional isolation. | **Fragile Under Concurrency**: Requires complex OS-level file locking and manual chain repair. | **Primary Sequencing Authority**: PostgreSQL manages monotonic sequencing and row locking for the operational application. |
| **Portability & Air-Gap** | Requires live database connection and database credentials to inspect. | **Self-Contained**: Portable single-file proof carrying canonical RFC 8785 JSON and HMAC checkpoints. | **Cryptographic Attestation Artifact**: JSONL ledger acts as the exportable, air-gapped cryptographic proof for Gate 9. |
| **Concurrency Scalability**| High concurrency via standard SQL MVCC and connection pooling. | Low-to-medium concurrency; lock contention on single file. | Operational review writes to PostgreSQL transaction; synchronous sidecar lock writes to JSONL ledger. |
| **Durability Guarantee** | WAL (Write-Ahead Logging) + synchronous commit. | Depends on explicit `fsync()` on file and parent directory. | Full WAL durability + strict filesystem fsync. |

**Selected Target Architecture**:
- For multi-user clinical operation, the relational `audit_events` table ([`src/clindoc/db/models.py:801-860`](file:///d:/daa/src/clindoc/db/models.py#L801-L860)) is the **authoritative sequence generator**.
- The `ReviewAuditLedger` JSONL file remains the **offline cryptographic attestation artifact** required for Gate 9 verification and air-gapped delivery.
- Both layers must maintain bitwise cryptographic consistency.

---

### 4.2 Cross-Platform Concurrency Control Specification

```
                          SECURE ATOMIC LEDGER APPEND
                          
            [Enter ReviewAuditLedger.append_event]
                              │
                              ▼
            Acquire Cross-Platform File Lock on `<ledger_path>.lock`
            (Timeout: 10.0s; Exponential Backoff: 10ms, 20ms, 40ms...)
                              │
                              ▼
            Inspect `<ledger_path>`:
            Read last line, parse JSON, verify sequence & hash
                              │
                    ┌─────────┴─────────┐
                    │ Valid             │ Corrupted / Partial / Mismatch
                    ▼                   ▼
            Calculate S_{k+1}    FAIL CLOSED: Raise AuditLedgerCorruptedError
            Calculate H_{k+1}    (DO NOT SILENTLY TRUNCATE)
                    │
                    ▼
            Format canonical RFC 8785 JSON line + "\n"
                    │
                    ▼
            Append to `<ledger_path>`
                    │
                    ▼
            Call file.flush()
            Call os.fsync(file.fileno())
                    │
                    ▼
            Call os.fsync(parent_dir_fd)  [POSIX Metadata Durability]
                    │
                    ▼
            Release File Lock on `<ledger_path>.lock`
                    │
                    ▼
            [RETURN VERIFIED AUDIT RECORD]
```

#### Durability Assumptions & Supported Filesystems
1. **Supported Filesystems**:
   - Strictly supported: Local POSIX filesystems (ext4, XFS, APFS) and Windows NTFS.
   - **STRICTLY PROHIBITED**: Network filesystems (NFS, SMB, CIFS). Advisory byte-range locking and `flock`/`msvcrt` over NFS/SMB exhibit known silent locking failures, cache incoherency, and split-brain phenomena. If `ledger_path` is detected on an NFS/SMB mount, initialization raises `UnsupportedFilesystemError`.
2. **Directory Metadata Durability**:
   - On POSIX systems, appending a line and updating file size modifies the parent directory's inode. In addition to `os.fsync(f.fileno())`, the parent directory MUST be opened (`dir_fd = os.open(parent_dir, os.O_RDONLY)`) and flushed (`os.fsync(dir_fd)`).
   - On Windows, NTFS automatically flushes metadata attributes during `FlushFileBuffers` / `os.fsync()`.
3. **Dedicated Sidecar Lock File**:
   - To avoid Windows file-sharing violations (`ERROR_SHARING_VIOLATION`) when opening an already-locked data file for reading, locking MUST occur on a distinct sidecar file: `f"{ledger_path}.lock"`.

---

### 4.3 Redesigned Fail-Closed Crash Recovery Procedure

#### Architectural Rejection of Silent Truncation
In `v1`, it was proposed that `append_event` automatically truncate partial trailing lines during normal operation. **This is rejected as an unsafe anti-pattern.** Silent truncation risks:
1. Discarding valid audit evidence from a review action that actually completed in the database.
2. Masking active disk corruption or concurrent writer conflicts.
3. Violating regulatory audit trail non-repudiation mandates (21 CFR Part 11 / HIPAA Security Rule §164.312).

#### The Fail-Closed Standard
If `ReviewAuditLedger.append_event` or `verify_ledger` encounters:
- A partial trailing line (invalid JSON, missing terminating newline),
- A sequence number gap or collision ($S_{k+1} \neq S_k + 1$),
- A hash mismatch ($H_{\text{prev}} \neq \text{hash of previous event}$), or
- An HMAC checkpoint mismatch,

**The ledger MUST FAIL CLOSED immediately**:
```python
raise AuditLedgerCorruptedError(
    f"Audit ledger corruption detected at line {line_no}. "
    f"Automatic append is blocked. Authorized operator recovery required."
)
```
Gate 9 export is instantly locked for all documents depending on this ledger.

#### Separately Controlled Operator Recovery Procedure (`clindoc-admin audit repair`)

When a crash or corruption occurs, recovery MUST follow an explicit, auditable operator workflow:

```
                     CONTROLLED RECOVERY PROCEDURE
                     
         [AuditLedgerCorruptedError Raised & Writes Halted]
                                │
                                ▼
         Operator executes: `clindoc-admin audit repair <ledger_path>`
                                │
                                ▼
         Step 1: PRESERVATION & QUARANTINE
         - Create bitwise copy: `<ledger_path>.corrupt.<timestamp>`
         - Create bitwise copy: `<checkpoint_path>.corrupt.<timestamp>`
         - Compute and record SHA-256 hashes of quarantined files
                                │
                                ▼
         Step 2: FORENSIC LINE-BY-LINE ANALYSIS
         - Read from genesis event (sequence 1)
         - Validate RFC 8785 canonical serialization and SHA-256 chain
         - Identify exact byte offset of first corrupt/torn record
                                │
                                ▼
         Step 3: OPERATOR DECISION GATE
         - Display forensic report:
           * Total valid events: N
           * Corrupted data at offset: X bytes
           * Discarded unverified content: [HEX / TEXT DUMP]
         - Mandate explicit confirmation: `--confirm-data-loss`
         - Record Operator Identity (User UUID) and Reason Code
                                │
                                ▼
         Step 4: RE-ESTABLISH HASH CHAIN & CHECKPOINT
         - Truncate ledger file strictly to offset of last valid record N
         - Re-compute terminal hash H_N
         - Generate new signed HMAC checkpoint with `recovery=True` metadata
         - Append a formal RECOVERY_AUDIT_EVENT documenting the repair
                                │
                                ▼
         Step 5: RE-VERIFICATION & UNLOCK
         - Execute `ReviewAuditLedger.verify_ledger()`
         - Assert 100% chain and HMAC validity
         - Release quarantine; writes and Gate 9 export unlocked
```

---

### 4.4 Multi-Process Concurrency & Adversarial Test Specifications

1. `test_audit_ledger_multiprocess_concurrency`:
   - Use Python `multiprocessing` to spawn **8 independent OS worker processes**.
   - Each process appends 50 audit events (400 total events) to a shared ledger file.
   - Assert zero sequence collisions, zero sequence gaps, and exactly 400 monotonically increasing records.
   - Assert `verify_ledger()` reports `is_valid=True` and `total_events=400`.
2. `test_audit_ledger_lock_timeout_fails_closed`:
   - Acquire exclusive lock on `.lock` in a background thread and hold it indefinitely.
   - Process 2 attempts `append_event(timeout=1.0)`.
   - Assert `AuditLedgerLockTimeoutError` is raised after 1.0s.
3. `test_audit_ledger_torn_write_fails_closed`:
   - Write 10 valid events, then append 25 bytes of partial JSON: `{"sequence_number": 11, "act`.
   - Call `append_event`.
   - Assert `AuditLedgerCorruptedError` is raised. Assert ledger file is NOT modified or truncated.
4. `test_audit_ledger_controlled_repair_utility`:
   - Execute the `clindoc-admin audit repair` workflow on the torn ledger from test 3.
   - Assert quarantine copy `<path>.corrupt.<timestamp>` is created with identical original bytes.
   - Assert truncated file verifies cleanly at 10 events.
   - Assert new HMAC checkpoint is signed and valid.
5. `test_audit_ledger_checkpoint_mismatch_fails_closed`:
   - Modify terminal event in ledger without updating `.chkpt`.
   - Assert `verify_ledger()` fails with `CheckpointSignatureMismatchError`.

---

## 5. DEF-004 Remediation: FHIR R4B Governance Semantics & Draft Boundary

### 5.1 Disentangling Clinical Status from Governance Metadata

A major finding of the architectural review is that confounding **resource clinical status** with **pipeline governance review status** is an anti-pattern that violates HL7 FHIR R4B 4.3.0 semantic specifications:
- Setting `Observation.status = "preliminary"` on a completed lab report distorts the clinical record (falsely asserting that the laboratory has not finalized the test).
- Setting `MedicationRequest.status = "draft"` on an ongoing or signed prescription distorts the order (falsely asserting that the physician has not signed or authorized the order).

#### Four-Tier Governance Separation Matrix

| Governance Tier | Meaning & Scope | FHIR R4B Implementation Location | Valid Value Set |
| :--- | :--- | :--- | :--- |
| **Tier 1: Resource Clinical Status** | The clinical state of the observation or medication as recorded in the source document. | `Observation.status`<br>`MedicationRequest.status`<br>`MedicationStatement.status` | Standard FHIR R4B value sets:<br>`Observation.status`: `final`, `amended`, etc.<br>`MedicationRequest.status`: `active`, `completed`, etc.<br>`MedicationStatement.status`: `active`, `completed`, `unknown`. |
| **Tier 2: Claim Review Status** | The human review decision recorded under Mode 0 governance. | Element-level extension: `http://clindoc.org/fhir/StructureDefinition/claim-provenance` | `PENDING`, `ACCEPTED`, `EDITED`, `REJECTED`, `UNREVIEWED`. |
| **Tier 3: Bundle Governance Metadata** | Overall processing tier and review completeness of the document extraction. | `Bundle.meta.tag`<br>`Bundle.meta.security` | `system`: `http://clindoc.org/fhir/tag/governance-status`<br>`code`: `UNREVIEWED_CLI_DRAFT` or `CLINICALLY_REVIEWED`.<br>`security`: `http://terminology.hl7.org/CodeSystem/v3-ObservationValue` $\to$ `SUBSET`. |
| **Tier 4: Export Authorization Status** | Cryptographic proof and institutional authorization for EHR transmission. | Bundle-level extension: `http://clindoc.org/fhir/StructureDefinition/export-authorization` | `status`: `UNAUTHORIZED_RESEARCH_DRAFT` (CLI)<br>`status`: `CLINICAL_TRANSMISSION_APPROVED` (API Gate 9 with verified audit ledger proof). |

---

### 5.2 The Robust Draft-Output Boundary

To guarantee that unreviewed CLI output can never be mistaken for approved clinical output or ingested into an EHR:

```
                            DRAFT OUTPUT BOUNDARY
                            
      ┌───────────────────────────┐         ┌───────────────────────────┐
      │   Stateless CLI Output    │         │    Stateful API Export    │
      │   (`clindoc process`)     │         │   (`FHIRExportService`)   │
      └─────────────┬─────────────┘         └─────────────┬─────────────┘
                    │                                     │
                    ▼                                     ▼
        Bundle Governance Tag:               Bundle Governance Tag:
        `UNREVIEWED_CLI_DRAFT`               `CLINICALLY_REVIEWED`
                    │                                     │
        Authorization Extension:             Authorization Extension:
        `UNAUTHORIZED_RESEARCH_DRAFT`        `CLINICAL_TRANSMISSION_APPROVED`
                    │                                     │
        Cryptographic Proof:                 Cryptographic Proof:
        NONE (Unsigned)                      Gate 9 Verified Ledger Signature
                    │                                     │
                    ▼                                     ▼
     ┌─────────────────────────────────────────────────────────────────┐
     │           EHR Integration Gateway / Ingestion Filter            │
     │   Rejects any bundle without CLINICAL_TRANSMISSION_APPROVED     │
     │   Rejects any bundle with UNREVIEWED_CLI_DRAFT                  │
     │   Rejects any bundle with missing / unverified Gate 9 proof     │
     └──────────────────────────────┬──────────────────────────────────┘
                                    │
                                    ├─ CLI Draft Bundle ──► HARD REJECT (403 Forbidden)
                                    │
                                    └─ Approved Bundle ──► ACCEPT FOR INGESTION
```

#### CLI Export Rules
1. **Mandatory Bundle Tags**:
   All bundles generated by `src/clindoc/cli.py` MUST contain:
   ```json
   "meta": {
     "tag": [
       {
         "system": "http://clindoc.org/fhir/tag/governance-status",
         "code": "UNREVIEWED_CLI_DRAFT",
         "display": "Unreviewed Automated Pipeline Draft — Not for Clinical Use"
       }
     ],
     "security": [
       {
         "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationValue",
         "code": "SUBSET",
         "display": "Resource is a partial/unreviewed extraction subset"
       }
     ]
   }
   ```
2. **Mandatory Authorization Extension**:
   ```json
   {
     "url": "http://clindoc.org/fhir/StructureDefinition/export-authorization",
     "valueCode": "UNAUTHORIZED_RESEARCH_DRAFT"
   }
   ```
3. **Prohibition of Bypass Overrides**:
   - The CLI parser MUST NOT provide flags such as `--approved`, `--force-final`, or `--skip-review`.
   - Any script or operator invoking the CLI receives strictly `UNAUTHORIZED_RESEARCH_DRAFT` bundles.
4. **Mandatory Warning Sidecar File**:
   Alongside `fhir_bundle.json`, `cli.py` writes `GOVERNANCE_NOTICE.txt`:
   ```
   ================================================================================
   WARNING: UNAUTHORIZED RESEARCH DRAFT — STRICTLY PROHIBITED FROM PATIENT CARE
   ================================================================================
   This FHIR R4B bundle was generated headlessly via the research CLI.
   It has NOT undergone Mode 0 clinical review, Four-Eyes separation verification,
   patient identity confirmation, or Gate 9 cryptographic audit ledger verification.
   Transmission to an Electronic Health Record (EHR) system violates governance policy.
   ================================================================================
   ```

---

### 5.3 Test Specifications for DEF-004

1. `test_cli_bundle_contains_mandatory_governance_tags`:
   - Execute `clindoc process` on a sample clinical document.
   - Inspect output `fhir_bundle.json`.
   - Assert `Bundle.meta.tag` contains `UNREVIEWED_CLI_DRAFT`.
   - Assert `Bundle.extension` contains `export-authorization = UNAUTHORIZED_RESEARCH_DRAFT`.
   - Assert `GOVERNANCE_NOTICE.txt` is created in output directory.
2. `test_cli_preserves_resource_clinical_status`:
   - Ingest lab report with final glucose result.
   - Assert `Observation.status == "final"` (Tier 1 clinical status preserved).
   - Assert `Bundle.meta.tag` is `UNREVIEWED_CLI_DRAFT` (Tier 3 governance tag applied).
3. `test_ehr_gateway_rejects_cli_draft_bundle`:
   - Feed CLI-generated `fhir_bundle.json` to `EHRIntegrationService.transmit_bundle`.
   - Assert operation raises `UnauthorizedClinicalExportError` (Hard Rejection).
4. `test_cli_cli_parser_has_no_bypass_flags`:
   - Inspect `clindoc.cli.create_parser()`.
   - Assert neither `--approved`, `--force`, nor `--skip-review` exists in option strings.

---

## 6. Objective Acceptance Criteria & Verification Matrix

| Defect ID | Remediation Objective | Test Verification Harness | Objective Acceptance Criteria |
| :--- | :--- | :--- | :--- |
| **F-12** | Complete ancestry traversal with multi-hop Four-Eyes separation and full-chain row locking. | `tests/test_claim_review_service.py`<br>`tests/test_phase523_milestone4_2.py` | 1. Any clinician who created or edited *any* version in an ancestry chain ($v_1 \dots v_k$) is blocked from accepting the final version with `FourEyesViolationError`.<br>2. Ancestry depth $> 50$, cyclic lineage, dangling parent pointers, or missing reviewer identities raise `CorruptedClaimAncestryError` (Fail Closed).<br>3. Multi-row locking (`FOR UPDATE`) prevents concurrent modifications across the full lineage during acceptance. |
| **DEF-002** | REQ-44 lifecycle enforcement enabled across all production callers; ambiguous linkage suppressed. | `tests/test_fhir_export.py`<br>`tests/test_phase523_milestone4_4.py` | 1. In `src/clindoc/fhir/mapper.py`, default `enforce_req44_lifecycle` is `True`.<br>2. `services/fhir_export.py` lines 743 and 1029 explicitly pass `enforce_req44_lifecycle=True`.<br>3. Historical mentions emit `MedicationStatement(status="unknown")`; zero active `MedicationRequest` orders fabricated.<br>4. Unreferenced cancellations and detached modifiers (*"Completed course. Continue Prednisone"*) suppress ambiguous claims without fabricating statuses.<br>5. All 19 tests in `test_phase523_milestone4_4.py` pass. |
| **DEF-003** | Multi-process advisory file locking, directory fsync, and fail-closed crash recovery on JSONL audit ledger. | `tests/test_phase523_milestone4_2.py`<br>`tests/test_audit_service.py` | 1. 8 concurrent OS worker processes writing 50 events each (400 total) produce zero sequence collisions, zero sequence gaps, and pass `verify_ledger()`.<br>2. On local POSIX/NTFS, writes execute `f.flush()`, `os.fsync(f.fileno())`, and directory fsync.<br>3. Partial writes, sequence collisions, or hash mismatches raise `AuditLedgerCorruptedError` (Fail Closed); zero automatic truncation.<br>4. `clindoc-admin audit repair` preserves bitwise quarantine copies, mandates operator authorization, and re-establishes valid hash chains. |
| **DEF-004** | Separation of resource clinical status vs bundle governance; robust draft boundary for CLI. | `tests/test_cli.py`<br>`tests/test_ehr_integration_pilot.py` | 1. CLI output bundles carry `UNREVIEWED_CLI_DRAFT` tag and `UNAUTHORIZED_RESEARCH_DRAFT` extension.<br>2. Resource clinical statuses (`Observation.status`) reflect document evidence without synthetic distortion.<br>3. CLI options parser contains no bypass flags (`--force`, `--approved`).<br>4. EHR gateway strictly rejects any bundle lacking Gate 9 verified audit ledger signature. |

---

## 7. Integration & Regression Protection Harness (Preserved Invariants)

### 7.1 Preserved Architecture & Subsystems

The following subsystems and files are under **strict freeze** and will receive zero modifications:

```
                            FROZEN SUBSYSTEMS
                            
  ┌─────────────────────────────────────────────────────────────────┐
  │ 1. Machine Learning Models & Calibrators:                       │
  │    - Stage B ModernBERT-Biomedical (`models/modernbert_v3/`)     │
  │    - Calibrated temperature scalar T = 1.146522                 │
  │    - Stage A Deterministic Verifier tolerances (0.05, exact)    │
  │    - Qwen2.5-7B AWQ prompt templates and decoding parameters    │
  ├─────────────────────────────────────────────────────────────────┤
  │ 2. Evaluation Datasets, Splits & Benchmarks:                    │
  │    - Certified 120-Document Benchmark (`data/m2_verification_v3/`)│
  │    - Synthetic 12-Doc and 60-Doc Testbeds (`external_eval/`)    │
  │    - All split manifests, hashes, and ground truth files        │
  │    - Final authoritative data sheet (`FINAL_REPORT_DATA_SHEET.md`)│
  ├─────────────────────────────────────────────────────────────────┤
  │ 3. Database Schema & Relational Tables:                         │
  │    - 14 core SQLAlchemy tables in `src/clindoc/db/models.py`    │
  │    - Relational `audit_events` schema and indexes               │
  ├─────────────────────────────────────────────────────────────────┤
  │ 4. Clause Segmentation & Coreference Core:                      │
  │    - 26 adversarial rules in `extract/segmentation.py`          │
  │    - Geometric bounding box association logic                   │
  └─────────────────────────────────────────────────────────────────┘
```

### 7.2 Regression Harness Baseline
- **Current Baseline**: 1,466 passed, 5 failed, 1,471 total in 263.35s.
- **Post-Remediation Acceptance Target**:
  - $\ge 1,485\text{ passed}$ (1,466 existing + $\ge 19$ new security/concurrency/lifecycle tests).
  - Exactly the 5 established historical baseline failures (zero regressions).
  - Zero performance degradation on cascade verification latency.

---

## 8. Implementation Sequence & Dependency Graph

```
                      DEPENDENCY-ORDERED IMPLEMENTATION ROADMAP
                      
   [STAGE 1: F-12 Multi-Hop Four-Eyes Ancestry Traversal]
   - Implement pessimistic ancestor row-locking in `ClaimReviewService.accept_claim`
   - Implement iterative while-loop traversal in `ClaimReviewService._check_four_eyes`
   - Add multi-hop unit & adversarial tests in `tests/test_claim_review_service.py`
                     │
                     ▼
   [STAGE 2: DEF-002 REQ-44 Lifecycle Activation & Semantic Grounding]
   - Invert default in `src/clindoc/fhir/mapper.py` (`enforce_req44_lifecycle = True`)
   - Explicitly pass `True` in `services/fhir_export.py:743, 1029` and `cli.py:244`
   - Implement detached modifier suppression rule (*"Completed course. Continue Prednisone"*)
   - Add integration export tests in `tests/test_fhir_export.py`
                     │
                     ▼
   [STAGE 3: DEF-003 Multi-Process Locking & Controlled Recovery]
   - Implement cross-platform sidecar file locking (`.lock`) in `ReviewAuditLedger.append_event`
   - Implement OS page-cache fsync and directory metadata fsync
   - Implement fail-closed `AuditLedgerCorruptedError` on corruption
   - Build `clindoc-admin audit repair` controlled quarantine & recovery utility
   - Add 8-process concurrency stress test in `tests/test_phase523_milestone4_2.py`
                     │
                     ▼
   [STAGE 4: DEF-004 CLI Governance Containment & Tier Separation]
   - Disentangle resource clinical status from governance metadata
   - Inject `UNREVIEWED_CLI_DRAFT` and `UNAUTHORIZED_RESEARCH_DRAFT` tags in `src/clindoc/cli.py`
   - Emit `GOVERNANCE_NOTICE.txt` warning sidecar
   - Add CLI governance & EHR gateway rejection tests in `tests/test_cli.py`
                     │
                     ▼
   [STAGE 5: Full Regression & Integration Verification]
   - Execute full repository test suite (`pytest`)
   - Verify 1,466+ passing, 5 known failures, zero drift on frozen data sheets
```

---

## 9. Final Pre-Implementation Checklist

- [ ] Architectural review of `CLINICALDOC_P0_SECURITY_REMEDIATION_DESIGN_v2.md` completed.
- [ ] No source code or tests modified during design phase; 24 modified tracked files and 68 untracked files preserved.
- [ ] Multi-hop Four-Eyes algorithm verified against cyclic graphs and dangling pointers.
- [ ] Full-chain pessimistic row locking (`with_for_update()`) verified against database deadlocks.
- [ ] REQ-44 six-category evidence matrix approved by clinical review.
- [ ] Detached modifier suppression rule approved.
- [ ] Automatic audit truncation rejected; controlled `audit repair` procedure approved.
- [ ] 8-process concurrency test harness specified.
- [ ] FHIR status semantics verified against FHIR R4B 4.3.0 Base.
- [ ] Formal authorization to commence code implementation granted.
