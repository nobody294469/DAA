# ClinicalDoc — Phase 5.2.3 Sub-Milestone 4.2.0: ReviewService Contract Finalization

**Execution Mode: STRICTLY READ-ONLY ARCHITECTURAL & CONTRACT REVIEW**  
**Author: Antigravity Agent (Pair Programming with Engineering Team)**  
**Date: October 31, 2026**  
**Status: FINAL CONTRACT SPECIFICATION — AWAITING IMPLEMENTATION AUTHORIZATION**

---

## Executive Summary

This contract specification finalizes the governance, state machine, identity rules, cryptographic audit ledger, and Gate 9 integration protocol for **Sub-Milestone 4.2: Mode 0 Review Service & Durable Audit Contract**.

Under ClinicalDoc productization requirements (PRODUCTIZATION_SPEC §4.2, §7.3; PD-002, PD-004, PD-009, PD-010, PD-012, PD-015), human clinical review is the deterministic safety boundary for provisional associations, multi-dosage regimens, and slot conflicts. In-memory representations are inherently ephemeral and untrusted; therefore, **no in-memory mutation of `claim.review_state` may independently establish export eligibility**.

This document defines:
1. The **canonical review state machine**, reconciling `ACCEPTED` with Gate 9 requirements and defining the exact semantics of `EDITED`, `ESCALATED`, `REOPEN`, and `REJECTED`.
2. The **Four-Eyes identity protocol**, defining actor roles and separation of duties for high-risk clinical claims.
3. The **cryptographic audit ledger specification**, detailing RFC 8785 canonical serialization, SHA-256 hash chaining, root anchor protection, and fail-closed handling of corruption or concurrency.
4. The **version and document binding rules**, preventing stale reviews from surviving document replacements or claim edits.
5. The **dual persistence contracts** (Relational SQLAlchemy vs. Offline JSONL), guaranteeing equivalent safety enforcement across server and CLI environments.
6. The **Gate 9 export validation gate**, specifying the exact evidence verification required before any provisional or ambiguous claim is permitted to emit FHIR resources.

---

## 1. Canonical Review State Machine

### 1.1 Reconciliation of `ACCEPTED` vs. `CONFIRMED`

A terminological discrepancy exists between specification prose and repository implementations:
- In `PRODUCTIZATION_SPEC.md §7.3`, `PRODUCTIZATION_DECISIONS.md PD-010`, and `clindoc.db.enums.ClaimReviewState`, the canonical state of a reviewed and approved clinical claim is **`ACCEPTED`**.
- In informal specifications, prompt instructions, and Gate 9 error text (`"requires clinician confirmation before export"`), the word *"CONFIRMED"* is used colloquially.
- Concurrently, at the document and demographic level (`clindoc.db.enums.PatientIdentityApprovalStatus` and `IdentityVerificationStatus`), **`CONFIRMED`** is the formal status indicating that patient demographic matching has been signed off.

**Contract Reconciliation Rule**:
1. At the **claim review level**, the single canonical state for an approved claim is **`ClaimReviewState.ACCEPTED`**.
2. Gate 9 and ReviewService must strictly enforce `ACCEPTED` as the export-eligible claim state.
3. For backwards compatibility with legacy tests or external clients passing string literals, Gate 9 will accept `"ACCEPTED"` and treat `"CONFIRMED"` as an alias for `ACCEPTED` *only if* backed by a verified audit event with `action="ACCEPT"`.

### 1.2 State Transition Matrix

The lifecycle of an individual claim version is strictly governed by the following state machine:

```
                              ┌────────────────────────┐
                              │        PENDING         │ ◄──────────────────────────────┐
                              └───────────┬────────────┘                                │
                                          │                                             │
                     ┌────────────────────┼────────────────────┐                        │
                     │ (ACCEPT)           │ (REJECT)           │ (EDIT)                 │
                     ▼                    ▼                    ▼                        │
           ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐               │
           │     ACCEPTED     │ │     REJECTED     │ │      EDITED      │               │
           │ (Export Eligible)│ │(Quarantined/Omit)│ │(Superseded v+1)  │               │
           └─────────┬────────┘ └─────────┬────────┘ └─────────┬────────┘               │
                     │                    │                    │                        │
                     │ (REOPEN)           │ (REOPEN)           │ (New version created   │
                     └────────────────────┴────────────────────┴─ in PENDING state) ────┘
```

| Source State | Permitted Action | Target State | Export Eligible? | Audit Action Recorded | Invariant / Side Effect |
| :--- | :--- | :--- | :---: | :--- | :--- |
| **`PENDING`** | `ACCEPT` | **`ACCEPTED`** | **YES** | `ACCEPT` | Requires valid clinician actor ID. Transition is final until `REOPEN`. |
| **`PENDING`** | `REJECT` | **`REJECTED`** | **NO** | `REJECT` | Requires mandatory `reason_code`. Permanently omitted from export. |
| **`PENDING`** | `EDIT` | **`EDITED`** | **NO** | `EDIT` | Requires mandatory `reason_code` and `new_value`. Current row becomes `EDITED` (immutable); spawns version $v+1$ in `PENDING` (PD-010). |
| **`PENDING`** | `ESCALATE` | **`ESCALATED`** | **NO** | `ESCALATE` | Requires mandatory `reason_code`. Routed to supervisory triage queue. |
| **`ACCEPTED`** | `REOPEN` | **`PENDING`** | **NO** | `REOPEN` | Requires mandatory `reason_code`. Immediately revokes export eligibility. |
| **`REJECTED`** | `REOPEN` | **`PENDING`** | **NO** | `REOPEN` | Requires mandatory `reason_code`. Restores claim to unreviewed triage. |
| **`ESCALATED`**| `ACCEPT` | **`ACCEPTED`** | **YES** | `ACCEPT` | Requires supervisor authorization. |
| **`ESCALATED`**| `REJECT` | **`REJECTED`** | **NO** | `REJECT` | Requires supervisor authorization. |
| **`ESCALATED`**| `REOPEN` | **`PENDING`** | **NO** | `REOPEN` | Returns claim to standard review queue. |
| **`EDITED`** | *None* | *None* | **NO** | *None* | **Terminal & Immutable**. Superseded versions cannot transition to any other state. |

### 1.3 Detailed Behavior of Complex States

1. **`EDITED`**:
   - When a claim $C_{v1}$ is edited, its `review_state` transitions to `EDITED`.
   - $C_{v1}$ becomes **permanently read-only and ineligible for export**.
   - A new claim record $C_{v2}$ is inserted with:
     - `claim_id = C_{v1}.claim_id`
     - `version = C_{v1}.version + 1`
     - `supersedes_claim_id = C_{v1}.id`
     - `origin = ClaimOrigin.REVIEWER`
     - `review_state = ClaimReviewState.PENDING`
   - **Crucial Invariant**: Editing a claim **invalidates any prior acceptance**. Version $C_{v2}$ cannot be exported until it is re-verified by the verification cascade and explicitly reviewed and accepted.
2. **`REOPEN`**:
   - `REOPEN` is an explicit revocation action.
   - It transitions an `ACCEPTED`, `REJECTED`, or `ESCALATED` claim back to `PENDING`.
   - Gate 9 immediately treats the claim as unreviewed and blocks export.
3. **`REJECTED`**:
   - Quarantines the claim. Gate 9 suppresses the resource with code `SUPPRESSED_CLAIM_REJECTED`.

---

## 2. Four-Eyes Identity Protocol

The Four-Eyes Principle (PD-004, PD-009) ensures that no clinical extraction or manual modification can be autonomously or self-servingly approved into the medical record by a single individual.

### 2.1 Actor Taxonomy

Every clinical claim interacts with up to four distinct identities across its lifecycle:
1. **Pipeline Operator (`operator_id`)**: The technical user or automated service account that initiated the document processing job.
2. **Claim Creator (`creator_id`)**: The identity that instantiated the claim. If extracted by the pipeline, `origin='pipeline'` and `creator_id=None`. If manually keyed in, `origin='reviewer'` and `creator_id=User.id`.
3. **Previous Modifier (`modifier_id`)**: The identity of any reviewer who edited or modified a prior version of this claim ($v-1$). Recorded in `supersedes_claim.created_by`.
4. **Clinical Reviewer (`reviewer_id`)**: The authenticated clinician performing the `accept_claim` or `reject_claim` action.

### 2.2 Four-Eyes Separation of Duties

For standard low-risk claims (e.g. routine vital signs or standard lab observations with clear evidence), standard clinician review is sufficient.

For **High-Risk Claims**, strict identity segregation is enforced:
- **Definition of High-Risk Claims**:
  1. Any claim with provisional association (`association_status="PARALLEL_COORDINATE_BIJECTION"` / `respectively`).
  2. Any claim with an ambiguous slot conflict or split-dose regimen (`slot_conflict=True` or `SPLIT_DOSE_REGIMEN`).
  3. Any claim with conditional hold instructions (`is_conditional=True`).
  4. Any claim where the raw value was edited or manually created (`origin='reviewer'`).
  5. Any high-potency medication category (narcotics, anticoagulants, insulin, chemotherapeutics).

- **Strict Identity Segregation Rules**:
  $$\text{reviewer\_id} \neq \text{operator\_id}$$
  $$\text{reviewer\_id} \neq \text{creator\_id}$$
  $$\text{reviewer\_id} \neq \text{modifier\_id}$$

*A clinician who edited a claim value cannot approve their own edit. The uploader of a document cannot approve high-risk claims on that document. A second, independent clinician must perform the acceptance action.*

### 2.3 Unverifiable & Anonymous Identity Handling

- **Anonymous Reviews Forbidden**: Any review request where `reviewer_id` is missing, null, empty string, or `"anonymous"` is rejected immediately with `UnauthorizedReviewerError`.
- **Role Verification**: The actor UUID must exist in the authenticated user store and hold the `CLINICAL_REVIEWER` or `CHIEF_MEDICAL_OFFICER` role in `user_roles`.
- **Fail-Closed Gate**: If the reviewer identity cannot be cryptographically verified against the active session or token, the review action is aborted and no state transition occurs.

---

## 3. Cryptographic Audit Ledger Specification

### 3.1 Mathematical Hash Chain Formulation

Audit events are chained using SHA-256 cryptographic hashes according to the existing project standard (`clindoc.api.audit.service`, models.py:824):

$$H_n = \text{SHA-256}\Big(\text{seq}_n \mathbin{\Vert} \text{ts}_n \mathbin{\Vert} \text{actor}_n \mathbin{\Vert} \text{type}_n \mathbin{\Vert} \text{ent\_type}_n \mathbin{\Vert} \text{ent\_id}_n \mathbin{\Vert} \text{act}_n \mathbin{\Vert} M_n \mathbin{\Vert} H_{n-1}\Big)$$

Where:
- $\text{seq}_n$: Strictly monotonic integer sequence ($1, 2, 3, \dots$).
- $\text{ts}_n$: UTC ISO-8601 string with microsecond precision (`YYYY-MM-DDTHH:MM:SS.ffffff+00:00`).
- $\text{actor}_n$: Verified UUID string of the reviewer.
- $\text{type}_n$: `"CLAIM_REVIEW_ACTION"`.
- $\text{ent\_type}_n$: `"claim"`.
- $\text{ent\_id}_n$: UUID string of the specific claim record (`Claim.id`).
- $\text{act}_n$: String action literal (`"ACCEPT"`, `"REJECT"`, `"EDIT"`, `"ESCALATE"`, `"REOPEN"`).
- $M_n$: RFC 8785 Canonical JSON string of metadata (sorted keys, no whitespace).
- $H_{n-1}$: Raw 64-character lowercase hexadecimal hash of the previous event (empty string `""` for genesis event $\text{seq}_1$).
- $\Vert$: Delimiter character `'|'`.

### 3.2 Canonical Metadata Schema ($M_n$)

The metadata payload must contain exact binding context while strictly complying with the PHI-free filter (`validate_metadata_phi_free`):

```json
{
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
}
```

*Forbidden Keys*: Any key containing raw patient clinical text, patient names, MRNs, or unredacted evidence is strictly rejected by `validate_metadata_phi_free`.

### 3.3 Root Anchor Protection & Anti-Tamper Guarantees

> [!CAUTION]
> **Cryptographic Realism Rule**: An unanchored hash chain file stored on an ordinary writable filesystem is **not tamper-proof**. An attacker with file write permissions can rewrite historical lines and recalculate all downstream hashes.

To achieve genuine tamper-evidence, the ledger must be anchored:

1. **Relational Mode Anchoring**:
   - The PostgreSQL database enforces row-level append-only constraints (`REVOKE UPDATE, DELETE ON audit_events`).
   - Transactional advisory locks (`SELECT pg_advisory_xact_lock(...)`) serialize concurrent writes and prevent race conditions.
2. **Offline JSONL Mode Anchoring (Manifest Digest)**:
   - When an offline processing or review session terminates, the final hash $H_{\text{latest}}$ and total count $N$ are written to a cryptographic manifest:
     `audit_ledger_anchor = HMAC-SHA256(H_latest || N, secret_key)` or written to a signed manifest file.
   - When `FHIRExportService` evaluates eligibility, it verifies:
     1. Unbroken hash chain from $\text{seq}_1$ to $\text{seq}_N$.
     2. $H_N == H_{\text{manifest}}$.
     3. Total lines $== N$.

### 3.4 Failure Mode Mitigations

| Failure Mode | Attack / Scenario | Mitigation / Defense |
| :--- | :--- | :--- |
| **Concurrent Writes** | Two reviewers act simultaneously. | Relational: PostgreSQL advisory lock. JSONL: Exclusive file lock (`fcntl`/`msvcrt`) with atomic append. |
| **Partial Writes** | Process crashes mid-write. | JSONL reader validates trailing line. If trailing line is malformed JSON or has invalid hash, fails closed immediately. |
| **Replay Attacks** | Attacker replays valid past review. | Sequence numbers are strictly monotonic and unique per document. Replay breaks sequence continuity. |
| **History Rewriting** | Attacker modifies row 2 and recalculates rows 3..N. | Fails validation against the sealed manifest root anchor. |
| **Ledger Truncation** | Attacker deletes trailing rejections. | Manifest record of total sequence count $N$ detects missing events. |
| **Missing Ledger** | Audit log file missing or empty. | Fail closed: Gate 9 assumes zero verified reviews; suppresses all provisional claims. |

---

## 4. Version and Document Binding

Review decisions cannot float unmoored from their originating clinical text.

### 4.1 Strict Binding Tuples

Every review decision is cryptographically bound to the 4-tuple:
$$(\text{document\_id}, \text{document\_sha256}, \text{claim\_id}, \text{claim\_version})$$

### 4.2 Document Replacement Invalidation

- If a document is re-scanned, re-OCR'd, or replaced, its content hash changes:
  $$\text{document\_sha256}_{\text{new}} \neq \text{document\_sha256}_{\text{audit}}$$
- When Gate 9 evaluates export eligibility:
  - It compares the current raw document hash against the `document_sha256` recorded in the review audit event.
  - If a mismatch is detected, the review decision is classified as **`STALE_DOCUMENT_VERSION`**.
  - All claims revert to unreviewed status (`PENDING`) and fail closed.

### 4.3 Optimistic Locking on Concurrent Reviews

- When a clinician initiates a review, the client submits `expected_version`.
- If `claim.version != expected_version`:
  - Raises `OptimisticLockError(f"Claim version mismatch: expected {expected_version}, current {claim.version}")`.
  - Mutation is aborted. No review action is recorded.

---

## 5. Dual Persistence Modes

To support both cloud/microservice deployments (PostgreSQL) and offline CLI batch execution (E3 narrative benchmarks), Clindoc must support two equivalent persistence modes.

### 5.1 Relational Mode (Database)

- **Storage**: `claims`, `review_actions`, and `audit_events` tables.
- **Workflow**:
  1. `ReviewService.accept_claim()` runs inside a database transaction.
  2. Acquires PostgreSQL advisory lock.
  3. Appends row to `audit_events`.
  4. Updates `claim.review_state = 'ACCEPTED'`.
  5. Commits transaction.
- **Export Verification**: `FHIRExportService` queries `audit_events` to verify the hash chain and confirm the latest action is `ACCEPT`.

### 5.2 Offline JSONL Mode (File-Based / CLI)

- **Storage**: Stored alongside the document artifacts:
  - `<doc_id>.review_audit.jsonl`: Append-only JSONL ledger.
- **Record Structure**: Each line is an exact JSON serialization of an audit event:
  ```json
  {"sequence_number":1,"timestamp":"2026-10-31T12:00:00.000000Z","actor_id":"...","event_type":"CLAIM_REVIEW_ACTION","entity_type":"claim","entity_id":"...","action":"ACCEPT","metadata_json":{...},"previous_event_hash":"","current_event_hash":"..."}
  ```
- **Export Verification**:
  1. `FHIRExportService` reads `<doc_id>.review_audit.jsonl`.
  2. Verifies the entire cryptographic hash chain from line 1 to $N$.
  3. Validates that `metadata_json.document_sha256` matches the active document.
  4. Hydrates in-memory claims with their verified review state.
  5. Any tampering, sequence gap, or hash mismatch aborts export with `AuditLedgerVerificationError`.

### 5.3 Prohibition of Naked In-Memory Overrides

In-memory claims passed to `FHIRExportService.export_intermediate_representation()` cannot declare themselves eligible merely via `claim.review_state = "ACCEPTED"`.
- If `enforce_mode_0=True` (default in production):
  - Gate 9 checks whether an `audit_ledger` (or session) is provided.
  - If a claim asserts `review_state="ACCEPTED"` but lacks a backing verifiable audit event, Gate 9 classifies the claim as **`UNVERIFIED_REVIEW_STATE`** and strictly suppresses it from export.

---

## 6. Gate 9 Integration Contract

Gate 9 in `src/clindoc/services/fhir_export.py` is the final export boundary.

### 6.1 Verification Precedence Hierarchy

Gate 9 must evaluate claim eligibility in the following strict order:

```
[ Step 1: Negation Gate ] ───────────── negated=True -> FAIL (Cannot export positive finding)
             │
             ▼
[ Step 2: Verification Discrepancy ] ── label not in (SUPPORTED, VERIFIED) -> FAIL
             │
             ▼
[ Step 3: Anchor Integrity (M4.1) ] ── reason == PARENT_ANCHOR_UNVERIFIED -> FAIL
             │
             ▼
[ Step 4: Grounding & Value ] ──────── missing evidence or empty value -> FAIL
             │
             ▼
[ Step 5: High-Risk / Provisional ] ── requires explicit review acceptance
             │                          ├── Provisional 'respectively'
             │                          ├── Ambiguous slot conflict / Split dose
             │                          ├── Unanchored orphan attribute
             │                          └── Conditional hold instructions
             ▼
[ Step 6: Audit Ledger Verification ] ─ Must have verified audit event (action="ACCEPT")
             │                          ├── Valid hash chain
             │                          ├── Unbroken document SHA-256 match
             │                          └── Four-Eyes compliance
             ▼
[ PASS: Export Eligible ]
```

### 6.2 Deterministic Suppression Reason Codes

When Gate 9 suppresses a claim, it must emit stable, machine-readable reason codes:

| Suppression Reason Code | Cause | Review Behavior |
| :--- | :--- | :--- |
| `SUPPRESSED_PARENT_ANCHOR_UNVERIFIED` | Parent drug name failed verification (M4.1). | Requires resolving drug name anchor. |
| `SUPPRESSED_UNCONFIRMED_PROVISIONAL` | Provisional parallel bijection (`respectively`) in `PENDING` state. | Requires clinician acceptance (`action="ACCEPT"`). |
| `SUPPRESSED_AMBIGUOUS_SLOT_CONFLICT` | Conflicting doses / split regimen in `PENDING` state. | Requires clinician resolution of regimen. |
| `SUPPRESSED_UNANCHORED_ORPHAN` | Attribute has no drug anchor in `PENDING` state. | Requires clinician entity association. |
| `SUPPRESSED_CONDITIONAL_ORDER` | Conditional hold order in `PENDING` state. | Requires clinician review. |
| `SUPPRESSED_CLAIM_REJECTED` | Clinician explicitly rejected the claim (`REJECTED`). | Permanently omitted. |
| `SUPPRESSED_SUPERSEDED_VERSION` | Claim version was edited (`EDITED`). | Only superseding version can be reviewed. |
| `SUPPRESSED_UNVERIFIED_AUDIT_LEDGER`| Claim asserts `ACCEPTED` but lacks valid audit ledger evidence. | Tamper-evidence tripwire fail-closed. |
| `SUPPRESSED_FOUR_EYES_VIOLATION` | High-risk claim approved by same actor who created/edited it. | Requires independent clinician review. |

---

## 7. Exact Proposed Files for Sub-Milestone 4.2 Implementation

### 7.1 Permitted Production Files
1. `src/clindoc/review/service.py`:
   - Add offline JSONL audit ledger persistence and verification methods.
   - Enforce Four-Eyes separation rules on `accept_claim()`.
   - Implement optimistic locking and document SHA-256 binding.
2. `src/clindoc/services/fhir_export.py`:
   - Wire audit ledger hydration into `evaluate_claim_eligibility()`.
   - Add verification tripwire: reject in-memory `ACCEPTED` claims that lack backing audit records.

### 7.2 Permitted Test Files
1. `tests/test_phase523_milestone4_2.py`:
   - Comprehensive test suite for state machine transitions, Four-Eyes enforcement, hash chain verification, tampering detection, and Gate 9 integration.

### 7.3 Strictly Frozen Modules (ZERO Changes Permitted)
- `src/clindoc/extract/medication_association.py` (FROZEN)
- `src/clindoc/extract/segmentation.py` (FROZEN)
- `src/clindoc/extract/deterministic.py` (FROZEN)
- `src/clindoc/verify/stage_a.py` (FROZEN)
- `src/clindoc/fhir/mapper.py` (FROZEN — will be modified only in M4.3)
- `src/clindoc/fhir/constants.py` (FROZEN)
- All configs and vocabularies (`extraction.yaml`, LOINC, UCUM) (FROZEN)

---

## 8. Open Questions, Limitations & Unresolved Risks

1. **HMAC Key Provisioning for Offline JSONL Anchors**:
   - In production cloud environments, HMAC keys or KMS signatures anchor audit logs. In standalone local offline CLI execution (e.g., developer workstation without environment variables), how should the ledger root anchor be signed?
   - *Recommendation*: In offline development mode, use a standard SHA-256 ledger manifest (`<doc_id>.review_audit.sha256`) containing the final hash, file size, and timestamp. If `CLINDOC_AUDIT_SECRET` is present in the environment, generate an HMAC-SHA256 signature.
2. **Reviewing Unanchored Orphans**:
   - If a clinician reviews an unanchored orphan attribute (e.g. bare "50 mg"), can they accept it as an orphan?
   - *Recommendation*: No. An orphan dose cannot become an active `MedicationRequest` without a drug entity. To make an orphan export-eligible, the clinician must use `EDIT` to associate it with an active medication anchor or provide the drug name.

---

## 9. Stop Condition

In compliance with the **STRICTLY READ-ONLY** mandate, no production code, tests, or configurations have been modified.

**Execution is halted. Awaiting explicit user review and authorization of this contract specification before proceeding to Sub-Milestone 4.2 implementation.**
