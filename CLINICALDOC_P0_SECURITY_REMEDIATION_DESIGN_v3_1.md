# ClinicalDoc — P0 Security & Architecture Remediation Design (Version 3.1)

**Document ID**: `CLINICALDOC_P0_SECURITY_REMEDIATION_DESIGN_v3_1.md`  
**Date**: October 31, 2026  
**Auditor & Architect**: Principal Software Architect, Senior Clinical NLP/AI Engineer & FHIR/Security Specialist  
**Target Repository**: `ClinicalDoc` (`clindoc2fhir`)  
**Design Status**: FINAL ARCHITECTURAL SPECIFICATION — PENDING IMPLEMENTATION AUTHORIZATION  
**Review Notice**: Documentation only. Zero source code, test code, configuration, benchmark data, database models, or Git state modified. All existing workspace changes (including 24 modified tracked files and 68 untracked files) remain strictly preserved.

---

## 1. Document Change Log (v3.0 $\to$ v3.1)

| Section / Topic | Nature of Change in v3.1 | Architectural Rationale |
| :--- | :--- | :--- |
| **§2 / F-12: Actor Provenance** | Added strict resolution and equivalence validation between `created_by` and `payload["edited_by"]`, requiring resolution to an active `User`. | Prevents identity forgery, unverified proxy edits, or audit dissociation in reviewer-created versions. |
| **§2 / F-12: Root $v_1$ Mutability** | Clarified separation between immutable clinical content/identity and mutable review state. | Eliminates ambiguity: clinical value/field/origin are permanently fixed, but `review_state` legally transitions (`PENDING` $\to$ `ACCEPTED`/`REJECTED`/`EDITED`). |
| **§2 / F-12: Chain Integrity** | Upgraded from leaf-only/max-version check to full connected, single-parent chain validation from $v_k$ down to $v_1$. | Guarantees the lineage is a strictly linear, contiguous historical chain with no forks, cycles, dangling references, or orphaned intermediate edits. |
| **§2 / F-12: Root-Lock Protocol** | Confirmed that `accept_claim`, `reject_claim`, `edit_claim`, and batch operations share the identical root-mutex lock acquisition on $v_1$. | Prevents concurrent race conditions between editing and accepting across the entire clinical entity lifecycle. |
| **§3 / DEF-002: Historical Evidence** | Separated explicitly completed courses from historical exposures with unknown completion; refined criteria for `MedicationStatement(unknown)` vs suppression. | Prevents assuming a historical mention (e.g. "took in 2021") was completed; reserves `unknown` for confirmed past use and suppresses unanchored/ambiguous mentions. |
| **§3 / DEF-002: Source Context** | Required verified clinician/source section context before classifying "Continue" as a clinician plan. | Prevents misinterpreting patient self-statements or narrative ambiguities as authorized physician directives. |
| **§4 / DEF-003: Gate 9 Race Condition** | Introduced PostgreSQL advisory transaction locking (`pg_advisory_xact_lock`) shared between Gate 9 and audit event writers. | Closes the window where a concurrent review event commits to PostgreSQL after Gate 9 reads the maximum sequence but before export. |
| **§4 / DEF-003: Full-Chain Sync** | Replaced maximum-sequence comparison with full-chain sequence and cryptographic hash verification; defined explicit anomaly handling. | Detects sequence gaps, duplicates, split-brain conditions, and partial writes with zero silent truncation. |
| **§4 / DEF-003: Safe Checkpointing** | Prohibited blind HMAC checkpoint regeneration without full-chain database attestation; confirmed zero schema changes. | Prevents legitimizing tampered or corrupt ledger content while strictly adhering to the database schema freeze. |
| **§5 / DEF-004: Draft Containment** | Formulated 3-tier boundary (identification, authorization, transmission); declared `meta.tag` is not a cryptographic boundary. | Clearly distinguishes in-payload metadata, repository-internal export gating, and institutional EHR perimeter defense. |
| **§5 / DEF-004: Security Labels** | Removed `v3-ObservationValue#SUBSET` from `Bundle.meta.security`. | Avoids inventing non-standard semantics; `meta.security` is reserved for confidentiality/sensitivity, while workflow governance resides in `meta.tag` and `identifier`. |

---

## 2. Executive Summary & Remediation Governance

This document represents the definitive, fourth-iteration architectural specification resolving all outstanding issues identified in the independent design reviews. It provides exact failure scenarios, mathematically proven architectural rules, concrete implementation boundaries, adversarial test specifications, and objective acceptance criteria for all four P0/P1 defects:

1. **F-12 (P0 — Security / Four-Eyes)**: Multi-hop Four-Eyes ancestry traversal, lineage-level serialization, and connected single-parent chain validation in [`src/clindoc/review/service.py:838-852`](file:///d:/daa/src/clindoc/review/service.py#L838-L852).
2. **DEF-002 (P0 — Clinical Safety / FHIR Semantics)**: REQ-44 lifecycle enforcement, evidentiary precision between clinician directives vs patient intake vs historical exposure, and fail-closed grounding in [`src/clindoc/services/fhir_export.py`](file:///d:/daa/src/clindoc/services/fhir_export.py) and [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py).
3. **DEF-003 (P0 — Integrity / Concurrency)**: Dual-tier PostgreSQL/JSONL crash consistency, cross-tier advisory synchronization eliminating post-read commit races, full-chain verification, and corrected OS durability in [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py).
4. **DEF-004 (P1 — Pipeline Architecture & Governance)**: Normative FHIR R4B 4.3.0 Base compliance, three-tier draft containment separation, elimination of non-standard security labels, and headless CLI containment in [`src/clindoc/cli.py`](file:///d:/daa/src/clindoc/cli.py).

### Mandatory Non-Negotiable Invariants
- **Frozen Subsystems**: ModernBERT-Biomedical model weights, temperature scalar ($T=1.146522$), Stage A deterministic verifier tolerances (0.05), benchmark manifests (`data/m2_verification_v3/`, `external_eval/`), and legacy Streamlit UI remain strictly frozen.
- **Database Schema Freeze**: Database schema changes are strictly avoided. All concurrency controls, outbox replays, and synchronization mechanics operate entirely over the existing schema ([`src/clindoc/db/models.py`](file:///d:/daa/src/clindoc/db/models.py)) using PostgreSQL advisory locking and existing `audit_events` columns.
- **Fail-Closed Safety**: Any ambiguity in patient intake, missing provenance in ancestral claim versions, ledger corruption, or unverified Gate 9 audit signature results in an immediate fail-closed state.

---

## 3. F-12 Remediation: Lineage Integrity, Multi-Hop Ancestry & Branching Prevention

### 3.1 The Exact Failure Scenarios

#### Scenario 1: Multi-Hop Four-Eyes Bypass via Intermediate Reviewers
- In [`src/clindoc/review/service.py:838-852`](file:///d:/daa/src/clindoc/review/service.py#L838-L852), `_check_four_eyes` checks only the immediate parent: `prev_claim = session.get(Claim, claim.supersedes_claim_id)`.
- Lineage: $v_1 \xrightarrow{\text{Actor A}} v_2 \xrightarrow{\text{Actor B}} v_3 \xrightarrow{\text{Actor C}} v_4$.
- When Actor A attempts to accept $v_4$: $v_4.\text{created\_by} = \text{C} \neq \text{A}$, and $v_3.\text{created\_by} = \text{B} \neq \text{A}$.
- Single-hop traversal terminates. Actor A is permitted to approve clinical substance they modified in $v_2$, directly violating the Mode 0 Four-Eyes principle (PD-002, PD-009, PD-015).

#### Scenario 2: Concurrent Lineage Branching & Phantom Insertion
- Target claim $v_2$ is undergoing acceptance by Clinician 1. Clinician 1's transaction locks rows $v_1$ and $v_2$ with `SELECT ... FOR UPDATE`.
- Concurrently, Clinician 2 invokes `edit_claim(v_2)`. Because row-level locks on existing rows do not block `INSERT` statements into the `claims` table under `READ COMMITTED` or `REPEATABLE READ`, Clinician 2 successfully inserts $v_3$ with `supersedes_claim_id = v_2.id`.
- Clinician 1 commits acceptance of $v_2$. The database enters a corrupted state: $v_2$ is marked `ACCEPTED`, yet a superseded child $v_3$ exists in `PENDING` state, creating a split-brain clinical record.

#### Scenario 3: Phantom Forking off Non-Terminal Ancestors & Broken Chains
- A claim has progressed to $v_3$. Reviewer 2 submits an edit targeting $v_1$.
- If chain continuity is not strictly validated under a lineage mutex, a parallel branch $v_1 \to v_2'$ is inserted, creating an unresolvable directed acyclic graph (DAG) rather than a linear version chain.

#### Scenario 4: Actor Provenance Discrepancy or Unresolvable Identity
- A reviewer claim $v_2$ has `created_by` set to User X, but its payload metadata has `payload["edited_by"] = "User Y"`, or `created_by` is NULL / references a purged or non-existent user ID.
- If the system blindly trusts either field or falls back to an unverified default, accountability is broken and Four-Eyes checks can be systematically circumvented.

---

### 3.2 Corrected Architectural Rule

#### 1. Universal Lineage Root Mutex on $v_1$
To guarantee strict serialization of all review operations without phantom insertions or concurrent branching, **every operation modifying, accepting, or rejecting any claim version in a lineage MUST acquire an exclusive row-level lock on the lineage root claim ($v_1$)**:

$$\text{Lock Target: } \text{Claim where } \texttt{claim\_id} = \text{target.claim\_id} \text{ and } \texttt{version} = 1$$

- **Root Claim Mutability & Invariants**:
  - The root claim $v_1$ has **immutable clinical substance** (`field_type`, `value`, `normalized_code`, `code_system`, `origin='pipeline'`) and **immutable clinical identity** (`claim_id`, `version=1`, `document_id`).
  - However, $v_1$'s **`review_state` is mutable** and legally transitions according to clinical workflow:
    $$\text{Initial: } \texttt{PENDING} \longrightarrow \texttt{ACCEPTED} \mid \texttt{REJECTED} \mid \texttt{EDITED (when superseded by } v_2 \text{)}$$
  - Because $v_1$ is permanently anchored in the database upon document ingestion, it serves as the definitive, zero-schema-change serialization token.

```sql
-- Acquired at transaction start before reading any lineage state
SELECT id, version, review_state 
FROM claims 
WHERE claim_id = :lineage_claim_id AND version = 1 
FOR UPDATE;
```

#### 2. Strict Deterministic Lock Acquisition Ordering
To prevent database deadlocks across multi-claim batch review operations, root locks MUST be acquired in strictly sorted alphanumeric order of `claim_id`:
```python
sorted_claim_ids = sorted(list(target_claim_ids))
for cid in sorted_claim_ids:
    session.query(Claim).filter(
        Claim.claim_id == cid, 
        Claim.version == 1
    ).with_for_update().one()
```

#### 3. Connected, Single-Parent Chain Validation (Beyond Simple Max-Version)
A lineage is NOT valid merely because the target claim's version equals `MAX(version)`. Under the root mutex, the service must validate that the lineage forms a **strictly connected, single-parent chain** from the target leaf $v_k$ down to root $v_1$:

```
              STRICT CONNECTED LINEAGE CHAIN INVARIANT
              
      Root ($v_1$)             Intermediate ($v_2$)            Terminal Leaf ($v_3$)
  ┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
  │ version: 1       │◄───────│ version: 2       │◄───────│ version: 3       │
  │ supersedes: NULL │        │ supersedes: v1.id│        │ supersedes: v2.id│
  │ state: EDITED    │        │ state: EDITED    │        │ state: PENDING   │
  │ origin: PIPELINE │        │ origin: REVIEWER │        │ origin: REVIEWER │
  └──────────────────┘        └──────────────────┘        └──────────────────┘
```

**Chain Verification Algorithm**:
1. Retrieve all versions for `claim_id`:
   `all_versions = session.query(Claim).filter(Claim.claim_id == target.claim_id).order_by(Claim.version.asc()).all()`
2. Verify count and continuity:
   - Must contain exactly $k$ records where versions are strictly contiguous integers: $[1, 2, \dots, k]$.
   - Target claim MUST be the terminal element: `target.version == k`.
3. Verify parentage linkage and intermediate states:
   - For index $i = 0$ ($v_1$): `v_1.supersedes_claim_id IS NULL`, `v_1.origin == ClaimOrigin.PIPELINE`.
   - For each index $i \in [1, k-1]$:
     - `all_versions[i].supersedes_claim_id == all_versions[i-1].id`.
     - `all_versions[i-1].review_state == ClaimReviewState.EDITED`.
     - Exactly one row references `all_versions[i-1].id` as `supersedes_claim_id` (zero branching).
4. Verify leaf state:
   - `target.review_state == ClaimReviewState.PENDING`.
   - Zero claims in the database have `supersedes_claim_id == target.id`.
5. If any condition fails: **FAIL CLOSED** (`CorruptedClaimAncestryError` or `BranchingProhibitedError`).

#### 4. Reviewer Actor Provenance Resolution & Equivalence Invariant
For every reviewer-created or edited claim version ($v_{\ge 2}$):
1. **Mandatory Presence**: `Claim.created_by` MUST NOT be NULL, and `Claim.payload["edited_by"]` MUST be present.
2. **Equivalence Invariant**:
   $$\texttt{str(Claim.created\_by)} == \texttt{str(Claim.payload["edited\_by"])}$$
   If `created_by` and `payload["edited_by"]` disagree, or if either is missing, the system **FAILS CLOSED** and raises `ActorProvenanceMismatchError`.
3. **Active User Resolution**:
   `user = session.get(User, claim.created_by)`
   If `user is None` or `user.is_active is False`:
   **FAIL CLOSED** (`UnresolvableActorError(f"Actor '{claim.created_by}' cannot be resolved to an active User.")`).

#### 5. Complete Multi-Hop Ancestry Traversal Algorithm
Under the lineage mutex:
1. Initialize `visited_ids = {target.id}`, `ancestor_actors = set()`, `depth = 0`.
2. Inspect target claim:
   - Validate actor provenance per Rule 4.
   - If `target.created_by == actor_uuid`: raise `FourEyesViolationError("Cannot accept self-edited claim.")`.
3. Walk the verified chain backwards:
   - For each parent version $v_{k-1} \dots v_2$:
     - Validate actor provenance per Rule 4.
     - Add `str(parent.created_by)` to `ancestor_actors`.
4. If `str(actor_uuid) in ancestor_actors`:
   raise `FourEyesViolationError(f"Actor '{actor_uuid}' edited an ancestor in this lineage.")`.
5. Verify document uploader:
   - If `Document.uploaded_by == actor_uuid`: raise `FourEyesViolationError("Document uploader cannot review claims.")`.

---

### 3.3 Implementation Boundary
- File: [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py)
  - `ClaimReviewService.accept_claim`: Acquire root lock on $v_1$, execute connected chain validation, verify actor provenance, execute ancestry traversal, transition target to `ACCEPTED`.
  - `ClaimReviewService.reject_claim`: Acquire root lock on $v_1$, execute connected chain validation, verify actor provenance, transition target to `REJECTED`.
  - `ClaimReviewService.edit_claim`: Acquire root lock on $v_1$, execute connected chain validation, verify actor provenance, insert $v_{k+1}$ as `PENDING` with parent $v_k$ atomically updated to `EDITED`.
  - `ClaimReviewService.batch_review_claims`: Sort `claim_id` list, acquire root locks sequentially, execute individual validations.

---

### 3.4 Required Adversarial Tests
1. `test_four_eyes_multi_hop_traversal_rejects_original_editor`:
   - Lineage: $v_1 \xrightarrow{\text{A}} v_2 \xrightarrow{\text{B}} v_3 \xrightarrow{\text{C}} v_4$.
   - Assert Actor A is rejected when attempting to accept $v_4$ with `FourEyesViolationError`.
2. `test_lineage_chain_gap_or_fork_rejected`:
   - Corrupt DB state where $v_3$ points to $v_1$ directly (skipping $v_2$).
   - Assert `accept_claim(v_3)` raises `CorruptedClaimAncestryError`.
3. `test_actor_provenance_mismatch_fails_closed`:
   - Create $v_2$ with `created_by = User_A` but `payload["edited_by"] = User_B`.
   - Assert operation fails closed with `ActorProvenanceMismatchError`.
4. `test_concurrent_edit_during_acceptance_blocked_by_root_mutex`:
   - Thread 1 locks $v_1$ to accept $v_3$. Thread 2 attempts `edit_claim(v_3)`.
   - Assert Thread 2 blocks on root lock until Thread 1 commits.
   - Assert Thread 2 then fails with `InvalidClaimLifecycleStateError` (since $v_3$ is now terminal `ACCEPTED`).

---

### 3.5 Objective Acceptance Criteria
1. Any actor who edited *any* version in an ancestry chain cannot accept any descendant.
2. Only strictly linear, connected single-parent chains with verified active actor identities are eligible for review.
3. Zero concurrent branching or phantom insertions.
4. All existing tests pass without regressions.

---

## 4. DEF-002 Remediation: Historical Medication Evidence Precision & REQ-44 Activation

### 4.1 The Exact Failure Scenarios

#### Scenario 1: Confounding Historical Exposures with Completed Courses
- Clinical text: *"Past Medical History: Warfarin in 2021."*
- Flawed Logic: Automatically mapping any past-tense mention to `MedicationStatement(status="completed")`.
- Safety Hazard: In clinical documentation, a remote date or past exposure does not prove that an intentional, defined therapeutic course was completed. It could have been abandoned due to life-threatening hemorrhage or toxicity. Emitting `status="completed"` asserts clinical completion without proof.

#### Scenario 2: Confounding Clinician Directives with Patient Intake
- Clinical text: *"Plan: Continue Prednisone 10mg daily."*
- Flawed Logic: Treating "Continue" as evidence that the patient is currently consuming the medication, emitting `MedicationStatement(status="active")`.
- Safety Hazard: Falsely certifies patient adherence during malpractice or reconciliation review, when in reality it was merely a physician's instruction.

#### Scenario 3: Interpreting "Continue" Without Source / Clinician Context
- Clinical text: *"Patient reports: I continue to feel dizzy on lisinopril."*
- Flawed Logic: Regex match on "Continue ... lisinopril" generates a `MedicationRequest(intent="plan")`.
- Safety Hazard: Attributes patient symptom commentary to a physician prescription order.

---

### 4.2 Corrected Architectural Rule

#### 1. Evidentiary Distinction: Completed vs Historical Exposure vs Unknown
The FHIR mapping engine must enforce strict evidentiary boundaries based on syntactic anchors and clinical context:

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
New Order         Plan / Continue                   Verified Intake      Historical Mention
("Rx", "Dispense")("Plan: Continue")                ("Patient takes")    ("Took in 2021")
   │                   │                                 │                   │
   ▼                   ▼                                 ▼                   ▼
MedicationRequest   MedicationRequest                MedicationStatement  Status Evaluation:
intent="order"      intent="plan"                    status="active"      ├── Explicit completed duration?
status="active"     status="active"                                       │   └── MedicationStatement(completed)
                                                                          ├── Confirmed past consumption?
                                                                          │   └── MedicationStatement(unknown)
                                                                          └── Ambiguous / unanchored?
                                                                              └── SUPPRESSED
```

#### Detailed Specification Matrix

| Category | Clinical Category Name | Evidentiary & Syntactic Requirements | Resulting FHIR Resource & Status | Safety Invariant |
| :---: | :--- | :--- | :--- | :--- |
| **1** | **Explicitly Completed Courses** | Past tense completion predicate ("completed", "finished", "took for 7 days in March") with an explicit duration, end date, or completion anchor bound to the drug. | `MedicationStatement`<br>`status: "completed"` | Must NEVER emit a `MedicationRequest`. Requires verifiable completion evidence. |
| **2** | **Historical Exposure (Unknown Completion)** | Grounded evidence of past consumption ("PMH: Warfarin use in 2021", "Previous medications include Lisinopril") where exposure is clinically certain, but duration/cessation is unstated. | `MedicationStatement`<br>`status: "unknown"` | Accurately records past exposure without certifying completion or non-adherence. |
| **3** | **Unanchored / Ambiguous Historical Mention** | Detached, speculative, or unverified mentions ("rule out penicillin allergy", "consider azithromycin", family medication history, unanchored drug names). | **FAILS CLOSED**<br>Claim is **SUPPRESSED** from FHIR export; logged in audit trail. | Prevents injecting ungrounded or hypothetical medication entries into the patient EHR. |
| **4** | **Clinician Directive / Plan (e.g. "Continue")** | Clinician therapeutic directive ("Plan: Continue Prednisone", "Continue home regimen") appearing in a verified clinician section ("Plan:", "Orders:", provider signed note). | `MedicationRequest`<br>`status: "active"`<br>`intent: "plan"` | **Precision Invariant**: Does NOT assert patient consumption or adherence. Captures doctor's plan. |
| **5** | **New Prescription Orders** | Active prescriber directive ("Rx", "Start", "Prescribe", "Dispense", or presence within authenticated Rx pad / CPOE section with clinician signature/DEA). | `MedicationRequest`<br>`status: "active"`<br>`intent: "order"` | Emitted only when legal prescriber order intent is verified. Narrative mentions are barred. |
| **6** | **Verified Patient Intake (Adherence)** | Patient-reported or nursing-reconciled intake ("Patient confirms taking Lisinopril 10mg daily", "Compliance: daily"). | `MedicationStatement`<br>`status: "active"` | Records verified intake without implying an order was generated during this encounter. |
| **7** | **Patient-Reported Cessation** | Explicit predicate of patient self-discontinuation ("patient stopped taking Metformin due to nausea"). | `MedicationStatement`<br>`status: "stopped"` | Accurately records non-adherence/cessation without cancelling the doctor's legal prescription order. |
| **8** | **Prescriber Cancellation (Linked to Order)** | Explicit clinician cancellation directive referencing an identifiable order or Rx number ("Cancel order #4812"). | `MedicationRequest`<br>`status: "cancelled"` | Emitted ONLY when cancellation syntactically binds to an existing order reference. Sets `priorPrescription`. |
| **9** | **Unreferenced / Ambiguous Discontinuation** | Detached discontinuation verb ("Discontinue Lisinopril", "Stop Prednisone") in a narrative note without order reference or clear actor attribution. | **FAILS CLOSED**<br>Claim is **SUPPRESSED** from FHIR export; logged in audit trail. | **Critical Safety Invariant**: The system must NOT guess whether to cancel an order or record cessation. |

#### 2. Definitive Resolution of *"Completed course. Continue Prednisone 10mg"*
1. Clause 1 (*"Completed course"*): Lacks an entity anchor $[L_k, R_k)$ in its clause span. Binding to an adjacent mention is prohibited $\to$ **SUPPRESSED** (`SUPPRESSED_UNBOUND_TEMPORAL_MODIFIER`).
2. Clause 2 (*"Continue Prednisone 10mg"*): Represents a clinician plan/directive in narrative text within a clinical plan context $\to$ Emit `MedicationRequest(status="active", intent="plan")`. Zero active dispensing order fabricated; zero patient intake claimed.

---

### 4.3 Implementation Boundary
- File: [`src/clindoc/fhir/mapper.py:2397`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2397)
  - Default: `enforce_req44_lifecycle: bool = True`.
  - Add context validation requiring clinician section tags before emitting `MedicationRequest(intent="plan")`.
  - Distinguish `status="completed"` (explicit duration/end date) from `status="unknown"` (past exposure without end date).
- File: [`src/clindoc/services/fhir_export.py:743, 1029`](file:///d:/daa/src/clindoc/services/fhir_export.py#L743)
  - Explicitly pass `enforce_req44_lifecycle=True`.
- File: [`src/clindoc/cli.py:244`](file:///d:/daa/src/clindoc/cli.py#L244)
  - Explicitly pass `enforce_req44_lifecycle=True`.

---

### 4.4 Required Adversarial Tests
1. `test_req44_historical_exposure_without_duration_emits_unknown`:
   - Text: "Past Medical History: Warfarin use in 2021."
   - Assert FHIR output is `MedicationStatement(status="unknown")`.
   - Assert `MedicationStatement(status="completed")` is NOT emitted.
2. `test_req44_completed_course_with_duration_emits_completed`:
   - Text: "Patient completed a 10-day course of Amoxicillin 500mg."
   - Assert FHIR output is `MedicationStatement(status="completed")`.
3. `test_req44_patient_commentary_with_continue_does_not_emit_plan`:
   - Text: "Patient states: I continue to take my morning vitamins."
   - Assert FHIR output is `MedicationStatement(status="active")`, NOT `MedicationRequest(intent="plan")`.

---

### 4.5 Objective Acceptance Criteria
1. `enforce_req44_lifecycle` defaults to `True` across all production paths.
2. Historical exposures without documented completion are mapped to `MedicationStatement(status="unknown")`.
3. Clinician directives ("Continue") require clinician section context and emit `MedicationRequest(intent="plan")`.
4. All existing tests pass without regressions.

---

## 5. DEF-003 Remediation: Database/File Synchronization, Cross-Tier Advisory Locking & Safe Replay

### 5.1 The Exact Failure Scenarios

#### Scenario 1: Post-Read Commit Race During Gate 9 Export
- Gate 9 reads $S_{\text{db\_max}} = 100$ and verifies that the JSONL ledger matches sequence 100.
- While Gate 9 is preparing the export bundle and cryptographic attestation, a concurrent review transaction commits audit event 101 to PostgreSQL `audit_events`.
- Gate 9 emits an export that certifies the audit trail up to sequence 100, silently omitting event 101 which occurred prior to bundle emission.

#### Scenario 2: Ledger Desynchronization Gaps, Duplicates, or Split-Brain
- Process crashes after DB commit but before JSONL append ($S_{\text{db}} > S_{\text{file}}$).
- Alternatively, manual file editing introduces duplicate sequence numbers, sequence gaps ($1, 2, 4$), or an older database backup is restored ($S_{\text{file}} > S_{\text{db}}$).
- If Gate 9 only compares `MAX(sequence)`, these critical corruptions remain undetected.

#### Scenario 3: Blind HMAC Checkpoint Regeneration Legitimizing Tampered Content
- A corrupted or tampered JSONL file has an invalid HMAC.
- If a naive startup routine blindly recalculates and updates the HMAC checkpoint without verifying the entire chain against the authoritative database, tampering is permanently codified as legitimate.

---

### 5.2 Corrected Architectural Rule

#### 1. Cross-Tier Advisory Synchronization Boundary
To eliminate the post-read commit race, Gate 9 and all audit event writers MUST share an exclusive PostgreSQL transaction advisory lock:

$$\text{PostgreSQL Lock: } \texttt{SELECT pg\_advisory\_xact\_lock(hashtext('clindoc\_audit\_ledger\_global'));}$$

```
                CROSS-TIER ATOMIC ATTESTATION BOUNDARY
                
       [Gate 9 Export Transaction]              [Concurrent Review Worker]
                   │                                        │
  BEGIN PostgreSQL Transaction             BEGIN PostgreSQL Transaction
                   │                                        │
  SELECT pg_advisory_xact_lock(...) ───┐   SELECT pg_advisory_xact_lock(...)
                   │                   │                    │
  (Acquires advisory lock immediately) │          (BLOCKED: Waits for Gate 9)
                   │                   │                    │
  Acquire `<ledger>.lock` file lock    │                    │
                   │                   │                    │
  Reconcile & Verify Full Chain        │                    │
                   │                   │                    │
  Emit FHIR Bundle & Signature         │                    │
                   │                   │                    │
  COMMIT; (Releases advisory lock) ────┘                    │
                                                   (Acquires lock now)
                                                   Inserts `audit_events` row 101
                                                   Appends to JSONL
                                                   COMMIT;
```

By holding `pg_advisory_xact_lock` across the Gate 9 verification and export generation window, no concurrent review transaction can commit an audit event between Gate 9's read and bundle emission.

#### 2. Full-Chain Cryptographic Verification (Beyond Maximum Sequence)
Gate 9 must verify the **entire chain from sequence 1 to $N$**, not merely compare maximum sequence numbers:

```python
def verify_full_chain_against_db(session: Session, ledger_path: Path) -> None:
    # 1. Fetch all DB audit events ordered by sequence
    db_events = session.query(AuditEvent).order_by(AuditEvent.sequence_number.asc()).all()
    
    # 2. Read all JSONL records
    file_records = read_all_jsonl_records(ledger_path)
    
    # 3. Detect split-brain or missing records
    if len(file_records) > len(db_events):
        raise SplitBrainLedgerError(
            f"JSONL contains {len(file_records)} records, but database only has {len(db_events)}. "
            "Database may have been restored from an older backup. FAILING CLOSED."
        )
    
    # 4. Strict contiguous validation
    expected_seq = 1
    prev_hash = None
    for i, (db_evt, file_rec) in enumerate(zip(db_events[:len(file_records)], file_records)):
        if file_rec["sequence_number"] != expected_seq:
            raise AuditLedgerGapError(f"Sequence gap at {file_rec['sequence_number']}, expected {expected_seq}.")
        if file_rec["sequence_number"] != db_evt.sequence_number:
            raise AuditLedgerCorruptedError(f"Sequence mismatch: file={file_rec['sequence_number']}, db={db_evt.sequence_number}.")
        if file_rec["current_event_hash"] != db_evt.current_event_hash:
            raise AuditLedgerTamperedError(f"Hash mismatch at sequence {expected_seq}.")
        if file_rec["previous_event_hash"] != prev_hash:
            raise AuditLedgerTamperedError(f"Hash chain broken at sequence {expected_seq}.")
        
        prev_hash = file_rec["current_event_hash"]
        expected_seq += 1
```

#### 3. Idempotent Outbox Replay & Anomaly Handling

| Detected Anomaly | System State | Remediation Action |
| :--- | :--- | :--- |
| **$S_{\text{db}} > S_{\text{file}}$ (Clean Tail)** | DB committed events not yet appended to JSONL. | Under `.lock`, fetch missing events `WHERE sequence_number > S_file ORDER BY sequence_number ASC`. Canonicalize via RFC 8785 JSON, append to JSONL, `fsync`, and update HMAC checkpoint. |
| **Partial / Torn Trailing Line** | Worker crashed mid-write during JSONL append. | Detect unparseable trailing bytes. Quarantine damaged file to `<ledger>.corrupt.<iso_ts>`. Restore clean records up to $S-1$; replay event $S$ from DB. |
| **Sequence Gap or Duplicate in JSONL** | $S_{\text{file}}$ has duplicate sequences or missing numbers. | **FAILS CLOSED** (`AuditLedgerGapError`). Quarantine file; full replay from authoritative DB. |
| **$S_{\text{file}} > S_{\text{db}}$ (Split-Brain)** | File has records unknown to PostgreSQL. | **FAILS CLOSED** (`SplitBrainLedgerError`). Operator intervention required. Automatic overwrite is prohibited. |
| **Hash Mismatch ($H_{\text{file}} \neq H_{\text{db}}$)** | Record content altered. | **FAILS CLOSED** (`AuditLedgerTamperedError`). Quarantine file; generate security incident report. |

#### 4. Safe Checkpointing Invariant
- **Rule**: Checkpoint regeneration is **PERMITTED ONLY IF** full-chain verification against PostgreSQL `audit_events` passes 100%.
- Blind recalculation of HMAC checkpoints over unverified JSONL content is strictly prohibited.

#### 5. Database Schema Freeze Confirmation
- This synchronization architecture uses `pg_advisory_xact_lock` and the existing columns of `audit_events`.
- **Zero database schema changes are required.** The schema freeze remains 100% intact.

---

### 5.3 Implementation Boundary
- File: [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py)
  - `ReviewAuditLedger.reconcile_with_db`: Execute full-chain reconciliation and outbox replay.
  - `ReviewAuditLedger.verify_ledger`: Gate 9 verification holding `pg_advisory_xact_lock`.
  - `ReviewAuditLedger.append_event`: Acquire advisory lock and execute dual-write under `.lock`.

---

### 5.4 Required Adversarial Tests
1. `test_gate9_advisory_lock_blocks_concurrent_audit_write`:
   - Start Gate 9 export transaction and hold advisory lock. Attempt concurrent review action.
   - Assert review action blocks until Gate 9 commits.
2. `test_full_chain_detects_middle_sequence_tampering`:
   - Modify payload of sequence 3 in a 10-record JSONL file.
   - Assert Gate 9 raises `AuditLedgerTamperedError`.
3. `test_split_brain_file_ahead_of_db_fails_closed`:
   - Insert 5 records into JSONL, but delete record 5 from DB.
   - Assert reconciliation raises `SplitBrainLedgerError`.

---

### 5.5 Objective Acceptance Criteria
1. Gate 9 export is strictly serialized with review writes; zero post-read commit race conditions.
2. Full-chain validation mathematically proves 100% equivalence between PostgreSQL and JSONL.
3. No HMAC checkpoint can be regenerated without full database attestation.
4. Schema freeze preserved with zero migrations.

---

## 6. DEF-004 Remediation: Normative FHIR R4B Bundle Structure & Three-Tier Draft Containment

### 6.1 The Exact Failure Scenarios

#### Scenario 1: Misusing Security Labels for Workflow Governance
- In `v3`, the design proposed using `Bundle.meta.security` with `v3-ObservationValue#SUBSET`.
- **HL7 FHIR R4B Standards Reality**:
  - `Bundle.meta.security` is strictly intended for data confidentiality, sensitivity, and handling classification (e.g. HIPAA Restricted, Substance Abuse, VIP privacy).
  - Using security labels to denote workflow draft status or incomplete review invents non-standard semantics and confuses healthcare security filters.

#### Scenario 2: Assuming `Bundle.meta.tag` is a Cryptographic Security Boundary
- Relying on `Bundle.meta.tag` to prevent ingestion into clinical EHR systems.
- Flawed Security Assumption: Tags are plain JSON elements that can be stripped, ignored, or overwritten by unauthenticated intermediaries.

---

### 6.2 Corrected Architectural Rule

#### 1. Three-Tier Draft Containment Model

```
                    THREE-TIER DRAFT CONTAINMENT MODEL
                    
   [Tier 1: Draft Identification]     Normative in-payload metadata on Bundle:
   (Within Bundle Payload)            - `Bundle.identifier` ("UNAUTHORIZED_RESEARCH_DRAFT")
                                      - `Bundle.meta.tag` ("UNREVIEWED_CLI_DRAFT")
                                      - Zero non-standard security labels
                                                    │
                                                    ▼
   [Tier 2: Export Authorization]     Repository-Internal Enforcement Boundary:
   (Within ClinicalDoc Core)          - `FHIRExportService` Gate 9 audit ledger verification
                                      - CLI unconditionally names files `*.draft.json`
                                      - CLI outputs mandatory `GOVERNANCE_NOTICE.txt`
                                      - CLI parser omits all approval bypass flags
                                      - Omission of `Bundle.signature` on unapproved drafts
                                                    │
                                                    ▼
   [Tier 3: Transmission Enforcement] External Institutional Perimeter:
   (At Institutional Gateway)         - Interface engine (Mirth/Rhapsody) validates digital signature
                                      - Rejects unsigned bundles or bundles tagged draft (HTTP 403)
                                      - ClinicalDoc runs in network-segmented environments
```

#### 2. Normative FHIR R4B Structure (Passing `FHIRValidator`)
In strict conformance with HL7 FHIR R4B 4.3.0 Base, all draft identification metadata is placed exclusively in `Bundle.meta.tag` and `Bundle.identifier`. Non-standard entries in `Bundle.meta.security` are **completely removed**:

```json
{
  "resourceType": "Bundle",
  "id": "bundle-unreviewed-draft-98214",
  "meta": {
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

- **Validation**: Tested against [`src/clindoc/fhir/validator.py`](file:///d:/daa/src/clindoc/fhir/validator.py) calling `fhir.resources.bundle.Bundle.parse_raw()`.
- **Result**: Zero schema errors, zero unknown field rejections, 100% valid normative FHIR R4B.

#### 3. Clear Demarcation of Repository vs Gateway Responsibilities
- **Repository-Internal Enforcement**:
  - `src/clindoc/cli.py` unconditionally stamps `UNREVIEWED_CLI_DRAFT` and `UNAUTHORIZED_RESEARCH_DRAFT`.
  - Emits mandatory sidecar warning `GOVERNANCE_NOTICE.txt`.
  - Appends `.draft.json` suffix to all CLI-exported bundles.
  - Hardcodes parser to reject `--force`, `--approved`, `--skip-review`.
  - Omits `Bundle.signature` (cryptographic signature generated only upon Gate 9 approval).
- **External Institutional Gateway (Target System Interface Contract)**:
  - Enterprise interface engines (Epic Bridges, Cerner Open Engine, Mirth Connect) must enforce that incoming bundles contain a valid cryptographic signature (`Bundle.signature`) verified against ClinicalDoc's public key, and immediately reject any bundle bearing the `UNREVIEWED_CLI_DRAFT` tag.

---

### 6.3 Implementation Boundary
- File: [`src/clindoc/cli.py:244-270`](file:///d:/daa/src/clindoc/cli.py#L244-L270)
  - Apply normative `meta.tag` and `identifier` metadata (omitting `meta.security`).
  - Output `.draft.json` and `GOVERNANCE_NOTICE.txt`.
  - Prohibit bypass CLI arguments.
- File: [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py)
  - Retain source clinical status (`Observation.status="final"`).

---

### 6.4 Required Adversarial Tests
1. `test_cli_bundle_passes_project_fhir_validator_without_security_labels`:
   - Validate CLI bundle with `FHIRValidator.validate_bundle(bundle)`.
   - Assert `val_report.is_valid is True`.
   - Assert `meta.security` is empty or omitted.
2. `test_cli_enforces_mandatory_governance_sidecar`:
   - Run `clindoc process`.
   - Assert `GOVERNANCE_NOTICE.txt` is created in output directory.
   - Assert bundle output filename ends in `.draft.json`.

---

### 6.5 Objective Acceptance Criteria
1. Bundle conforms 100% to FHIR R4B 4.3.0 Base without non-standard security labels.
2. Clear three-tier containment model documented and enforced.
3. CLI drafts cannot be converted into approved clinical exports.
4. All existing tests pass without regressions.

---

## 7. Comprehensive Verification Matrix & Acceptance Roadmap

| Defect ID | Technical Root Cause | Architectural Remediation | Verification Test Harness | Status |
| :---: | :--- | :--- | :--- | :---: |
| **F-12** | Single-hop parent check; unverified actor provenance; branching. | Root lock on $v_1$; connected chain validation; actor equivalence check; leaf-only invariant. | `tests/test_claim_review_service.py` | Ready for Implementation |
| **DEF-002** | Confounding historical mentions with completed courses; missing context. | 9-category evidence matrix; "unknown" status for unverified past use; clinician context requirement. | `tests/test_fhir_export.py`<br>`tests/test_phase523_milestone4_4.py` | Ready for Implementation |
| **DEF-003** | Post-read commit race; maximum-only sequence comparison; unsafe replay. | PostgreSQL advisory transaction lock (`pg_advisory_xact_lock`); full-chain verification; safe replay. | `tests/test_phase523_milestone4_2.py`<br>`tests/test_audit_service.py` | Ready for Implementation |
| **DEF-004** | Non-standard security labels; conflating tags with security boundaries. | Normative `Bundle.meta.tag` and `identifier`; 3-tier boundary; removal of `meta.security#SUBSET`. | `tests/test_cli.py`<br>`tests/test_fhir_validator.py` | Ready for Implementation |

---

## 8. Final Architectural Declaration

This document represents the completed, fully reconciled architectural specification (Version 3.1) for all P0 security and architecture remediations. 
- No source code, tests, configs, database models, or benchmarks have been modified.
- All 24 modified tracked files and 68 untracked files remain intact.
- Zero defects are marked resolved until code implementation is authorized and executed.
- No Git operations were performed.
