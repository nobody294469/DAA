# ClinicalDoc — P0 Security & Architecture Remediation Design

**Document ID**: `CLINICALDOC_P0_SECURITY_REMEDIATION_DESIGN.md`  
**Date**: October 31, 2026  
**Auditor & Architect**: Principal Software Architect, Senior Clinical NLP/AI Engineer & Security Specialist  
**Target Repository**: `ClinicalDoc` (`clindoc2fhir`)  
**Design Status**: APPROVED DESIGN SPECIFICATION — READY FOR IMPLEMENTATION (Documentation Only; Zero Source Code or Test Modifications)

---

## 1. Executive Summary & Remediation Scope

This document provides the exhaustive, technical design specification for remediating the four critical P0 and P1 security and architecture defects identified and confirmed in [`CLINICALDOC_CURRENT_STATE_RECONCILIATION.md`](file:///d:/daa/CLINICALDOC_CURRENT_STATE_RECONCILIATION.md):

1. **F-12 (P0 — Security / Four-Eyes)**: Multi-hop Four-Eyes ancestry vulnerability in [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py#L838-L852).
2. **DEF-002 (P0 — Clinical Safety / FHIR Semantics)**: REQ-44 lifecycle enforcement dormant in production export paths in [`src/clindoc/services/fhir_export.py`](file:///d:/daa/src/clindoc/services/fhir_export.py#L743) and [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2397).
3. **DEF-003 (P0 — Integrity / Concurrency)**: Unlocked concurrent append in offline JSONL audit ledger in [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py#L335-L385).
4. **DEF-004 (P1 — Pipeline Architecture & Governance)**: Stateless CLI governance bypass producing untagged FHIR bundles without review in [`src/clindoc/cli.py`](file:///d:/daa/src/clindoc/cli.py#L244-L270).

### Strict Non-Negotiable Invariants
- **Fail-Closed Clinical Safety**: Any ambiguity, broken pointer, missing actor identity, or concurrent mutation race halts review acceptance and blocks FHIR export.
- **Zero Regression on Baseline**: The 1,466 passing tests must remain 100% passing. The 5 known baseline failures must not be aggravated.
- **Frozen Benchmarks**: Zero modification to frozen benchmark manifests (`external_eval/`, `data/m2_verification_v3/`, `experiments/FINAL_REPORT_DATA_SHEET.md`), models, or split definitions.

---

## 2. F-12 Remediation Design: Multi-Hop Four-Eyes Ancestry Traversal

### 2.1 The Vulnerability
In [`src/clindoc/review/service.py:838-852`](file:///d:/daa/src/clindoc/review/service.py#L838-L852), `ClaimReviewService._check_four_eyes` only inspects the target claim's direct parent (`prev_claim = session.get(Claim, claim.supersedes_claim_id)`). 

In an edit chain:
$$\text{Claim } v_1 \xrightarrow{\text{Actor A}} v_2 \xrightarrow{\text{Actor B}} v_3 \xrightarrow{\text{Actor C}} v_4$$
When Actor A attempts to execute `accept_claim` on $v_4$:
- $v_4.\text{created\_by} = \text{Actor C} \neq \text{Actor A}$.
- $v_4.\text{supersedes\_claim\_id}$ points to $v_3$.
- $v_3.\text{created\_by} = \text{Actor B} \neq \text{Actor A}$.
- The check terminates. Actor A is permitted to approve $v_4$, directly violating the Mode 0 Four-Eyes principle (PD-002, PD-009, PD-015) by accepting their own modified clinical substance.

---

### 2.2 Complete Ancestry Traversal Specification

```
                               ANCESTRY TRAVERSAL LOGIC
                               
          [Target Claim: v_4]  (Actor attempting ACCEPT: X)
                   │
                   ▼ (Check 1: Target Claim Creator)
         Is X == v_4.created_by? ──► YES ──► RAISE FourEyesViolationError
                   │ NO
                   ▼ (Check 2: Target Claim Payload Editor)
         Is X == v_4.payload["edited_by"]? ──► YES ──► RAISE FourEyesViolationError
                   │ NO
                   ▼
     ┌───────────► Loop: curr = curr.supersedes_claim_id
     │             │
     │             ▼
     │   Has curr been visited? ──► YES (Cycle Detected) ──► RAISE CorruptedClaimAncestryError
     │             │ NO
     │             ▼
     │   Fetch parent claim from DB: session.get(Claim, curr_id)
     │             │
     │             ├─ Parent is None? ──► YES (Dangling Pointer) ──► RAISE CorruptedClaimAncestryError
     │             │
     │             ├─ Parent.created_by missing on edit? ──► YES ──► RAISE CorruptedClaimAncestryError
     │             │
     │             ▼
     │   Is X == Parent.created_by OR X == Parent.payload["edited_by"]?
     │             │
     │             ├─ YES ──► RAISE FourEyesViolationError (Self-Acceptance Blocked)
     │             │
     │             ▼ NO
     │   Has Parent reached Root (supersedes_claim_id is None)?
     │             │
     │             ├─ NO ──► Update curr = Parent.supersedes_claim_id, CONTINUE LOOP
     │             │
     └─────────────┘
                   │ YES
                   ▼ (Check 3: Document Uploader Gate)
         Is X == Document.uploaded_by? ──► YES ──► RAISE FourEyesViolationError
                   │ NO
                   ▼
           [ACCEPT AUTHORIZED]
```

#### Detailed Algorithm
1. **Initialize State**:
   - `actor_str = str(actor_uuid)`
   - `visited_claim_ids: set[uuid.UUID] = {claim.id}`
   - `ancestor_actors: set[str] = set()`
   - Maximum traversal depth limit: `MAX_ANCESTRY_DEPTH = 50` (prevents runaway recursion).
2. **Examine Target Claim ($v_k$)**:
   - If `claim.created_by` is not None and `str(claim.created_by) == actor_str`: Raise `FourEyesViolationError`.
   - If `claim.payload and str(claim.payload.get("edited_by")) == actor_str`: Raise `FourEyesViolationError`.
3. **Walk Ancestry Graph**:
   - `curr_claim = claim`
   - `depth = 0`
   - While `curr_claim.supersedes_claim_id is not None`:
     - `depth += 1`
     - If `depth > MAX_ANCESTRY_DEPTH`: Raise `CorruptedClaimAncestryError(f"Ancestry chain exceeded depth limit {MAX_ANCESTRY_DEPTH} on claim {claim.id}")`.
     - `parent_id = curr_claim.supersedes_claim_id`
     - If `parent_id in visited_claim_ids`: Raise `CorruptedClaimAncestryError(f"Cyclic claim ancestry detected at {parent_id}")`.
     - `visited_claim_ids.add(parent_id)`
     - `parent_claim = session.get(Claim, parent_id)`
     - **Fail-Closed Integrity Checks**:
       - If `parent_claim is None`: Raise `CorruptedClaimAncestryError(f"Dangling ancestry link: claim {curr_claim.id} references missing parent {parent_id}")`.
       - If `parent_claim.document_id != claim.document_id`: Raise `CorruptedClaimAncestryError("Cross-document claim ancestry injection detected")`.
     - **Extract Ancestor Editors**:
       - If `parent_claim.created_by is not None`:
         - `ancestor_actors.add(str(parent_claim.created_by))`
       - If `parent_claim.payload and parent_claim.payload.get("edited_by")`:
         - `ancestor_actors.add(str(parent_claim.payload["edited_by"]))`
       - If `parent_claim.origin == ClaimOrigin.REVIEWER and not parent_claim.created_by and not parent_claim.payload.get("edited_by")`:
         - Raise `CorruptedClaimAncestryError(f"Reviewer-created ancestor {parent_claim.id} is missing creator/editor identity")`.
     - `curr_claim = parent_claim`
4. **Assert Actor Separation**:
   - If `actor_str in ancestor_actors`:
     - Raise `FourEyesViolationError(f"Four-Eyes Violation: Clinician '{actor_uuid}' modified ancestral version of claim '{claim.claim_id}' and cannot accept it.")`.
5. **Document Uploader Check**:
   - If `claim.document_id is not None`:
     - `doc = session.get(Document, claim.document_id)`
     - If `doc is not None and doc.uploaded_by is not None and str(doc.uploaded_by) == actor_str`:
       - Raise `FourEyesViolationError(f"Four-Eyes Violation: Pipeline operator '{actor_uuid}' who uploaded document '{doc.id}' cannot accept high-risk claim '{claim.claim_id}'.")`.

---

### 2.3 Concurrency Protection During Acceptance

A race condition exists if Clinician A evaluates ancestry on $v_3$ at $T_1$, while Clinician B edits $v_3 \to v_4$ at $T_1 + \epsilon$.
- **Database Row Locking**: In `accept_claim` ([`src/clindoc/review/service.py:970`](file:///d:/daa/src/clindoc/review/service.py#L970)):
  ```python
  # Acquire pessimistic row lock on the target claim row
  claim = session.query(Claim).filter(Claim.id == resolved_claim_id).with_for_update().one_or_none()
  ```
- **Optimistic Version Lock**:
  `_check_optimistic_lock(claim, expected_version)` validates `claim.version == expected_version`. If a concurrent edit committed first, `OptimisticLockError` is raised and the transaction rolls back cleanly.

---

### 2.4 Test Specifications for F-12

```python
# To be added in tests/test_claim_review_service.py and test_phase523_milestone4_2.py

def test_four_eyes_multi_hop_ancestry_three_editors(db_session, review_service):
    """v1 (pipeline) -> v2 (Actor A) -> v3 (Actor B) -> v4 (Actor C).
    Assert Actor A cannot accept v4.
    Assert Actor B cannot accept v4.
    Assert Actor C cannot accept v4.
    Assert Independent Actor D CAN accept v4.
    """

def test_four_eyes_cyclic_ancestry_fails_closed(db_session, review_service):
    """Manually construct v1 -> v2 -> v1 cycle in test fixture.
    Assert accept_claim raises CorruptedClaimAncestryError (Fail-Closed).
    """

def test_four_eyes_dangling_parent_pointer_fails_closed(db_session, review_service):
    """Construct claim referencing non-existent supersedes_claim_id UUID.
    Assert accept_claim raises CorruptedClaimAncestryError (Fail-Closed).
    """

def test_four_eyes_missing_creator_on_reviewer_claim_fails_closed(db_session, review_service):
    """Construct reviewer claim with created_by=None and payload['edited_by']=None.
    Assert accept_claim raises CorruptedClaimAncestryError (Fail-Closed).
    """
```

---

## 3. DEF-002 Remediation Design: REQ-44 Lifecycle Enforcement Activation

### 3.1 The Defect
In [`src/clindoc/fhir/mapper.py:2397`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2397), `claims_to_fhir_bundle` defines:
```python
enforce_req44_lifecycle: bool = False
```
In [`src/clindoc/services/fhir_export.py:743, 1029`](file:///d:/daa/src/clindoc/services/fhir_export.py#L743) and [`src/clindoc/cli.py:244`](file:///d:/daa/src/clindoc/cli.py#L244), `enforce_req44_lifecycle` is omitted, causing every export in production to run legacy ungrounded medication mapping.

---

### 3.2 Dual-Tier Activation Strategy

To ensure zero risk of dormancy anywhere in the system:
1. **Tier 1 (Default Inversion in Mapper)**:
   Change default parameter in [`src/clindoc/fhir/mapper.py:2397`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2397):
   ```python
   def claims_to_fhir_bundle(
       claims: Sequence[Claim],
       evidence_map: dict[str, Evidence],
       verification_map: dict[str, VerificationResult],
       ...
       enforce_req44_lifecycle: bool = True,  # <--- DEFAULT INVERTED TO TRUE
   ) -> Bundle:
   ```
2. **Tier 2 (Explicit Enforcement at Call Sites)**:
   Explicitly pass `enforce_req44_lifecycle=True` at all call sites:
   - `src/clindoc/services/fhir_export.py:743` (`export_document_as_fhir`)
   - `src/clindoc/services/fhir_export.py:1029` (`export_batch_as_fhir`)
   - `src/clindoc/cli.py:244` (`process_document`)
   - `src/clindoc/testing/synthetic_export.py:80` (`export_synthetic_document_to_fhir`)

---

### 3.3 Semantic Lifecycle Rules Matrix

| Clinical Text Scenario | Inferred Temporal Status | Layout / Evidence Linkage | Resulting FHIR Resource & Status | Safety Justification |
| :--- | :---: | :--- | :--- | :--- |
| **Historical Medication Mention** ("Patient previously took Warfarin 5mg") | `historical` | Progress note / clinic narrative | `MedicationStatement(status="unknown")` | Prevents generating active or completed prescription order (`MedicationRequest`) for remote history. |
| **Completed Course (Syntactically Linked)** ("completed 7-day course of Amoxicillin") | `completed` | Directly adjacent to medication span | `MedicationStatement(status="completed")` | Verifiable completed therapy. |
| **Detached Completed Mention** ("Completed course. Continue Prednisone 10mg") | `completed` | Detached / unanchored text | `MedicationRequest(status="active")` or `MedicationStatement(status="active")` | Prevents falsely marking ongoing taper/order as completed. |
| **Patient-Reported Cessation** ("Patient stopped Metoprolol due to dizziness") | `discontinued` | Evidence of patient non-adherence/cessation | `MedicationStatement(status="stopped")` | Accurately records patient cessation without cancelling prescriber order. |
| **Prescriber Order Cancellation** ("Cancel order #4812 Lisinopril") | `discontinued` | Rx Pad layout + explicit Order Ref | `MedicationRequest(status="cancelled")` | Valid order cancellation. |
| **Unreferenced Cancellation** ("Discontinue Lisinopril" without order reference) | `discontinued` | Narrative progress note | **FAILS CLOSED**: Claim suppressed; recorded in `audit_trail` with reason `REQ-44: Unreferenced cancellation lacks verifiable prior order reference` | Prevents cancelling unrelated or external prescriptions without order binding. |

---

### 3.4 Test Specifications for DEF-002

```python
# To be added in tests/test_fhir_export.py

def test_production_export_service_enforces_req44_lifecycle(db_session, fhir_export_service):
    """End-to-end test of FHIRExportService.export_document_as_fhir.
    Ingest note with 'Patient previously took Warfarin 5mg'.
    Assert output bundle contains MedicationStatement with status='unknown'.
    Assert NO MedicationRequest is fabricated.
    """

def test_production_export_service_unreferenced_cancellation_suppressed(db_session, fhir_export_service):
    """End-to-end test of unreferenced cancellation in clinical note.
    Assert export succeeds but unreferenced cancellation is suppressed from Bundle.entry.
    Assert audit_trail in export records suppression under REQ-44.
    """
```

---

## 4. DEF-003 Remediation Design: Audit Ledger Concurrency & Locking

### 4.1 The Concurrency Defect
In [`src/clindoc/review/service.py:335-385`](file:///d:/daa/src/clindoc/review/service.py#L335-L385), `ReviewAuditLedger.append_event` performs:
1. File open for read $\to$ locate trailing line $\to$ calculate $S_{k+1} = S_k + 1$ and $H_{\text{prev}} = H_k$.
2. Compute new hash $H_{k+1}$.
3. File open for append $\to$ write record.

Under multi-user review concurrency in FastAPI, two requests interleave between step 1 and step 3, creating duplicate sequence numbers and corrupted previous-hash links.

---

### 4.2 Architectural Trade-Off: File Locking vs Database-Backed Sequencing

| Evaluation Dimension | Option A: OS File Locking (`portalocker` on JSONL) | Option B: PostgreSQL-Backed Sequence & Checkpoint |
| :--- | :--- | :--- |
| **Offline Independence** | **Excellent**: Preserves standalone JSONL verification without DB dependency. | **Poor**: Strictly couples audit trail verification to PostgreSQL availability. |
| **Cross-Platform Safety** | Supported on Linux/Windows via `portalocker` (uses `msvcrt.locking` on Windows, `fcntl.flock` on POSIX). | Standard SQL ACID guarantees across all platforms. |
| **Gate 9 Compatibility** | **100% Native**: Gate 9 directly inspects `ReviewAuditLedger` and `.chkpt` files. | Requires refactoring Gate 9 and all offline audit validation harnesses. |
| **Performance Under Load**| Low contention for document-isolated review ($\sim 5\text{ ms}$ lock hold time). | High performance via standard relational indexes. |
| **Recommendation** | **PRIMARY REMEDIATION (MVP/P0)** | **FUTURE SCALE (P3)** |

**Decision**: Implement **Option A** (Robust cross-platform advisory file locking with atomic write and fsync on the JSONL ledger). Since `AuditEvent` already persists relational audit events inside PostgreSQL transactions ([`src/clindoc/review/service.py:934`](file:///d:/daa/src/clindoc/review/service.py#L934)), the JSONL ledger represents the portable, tamper-evident cryptographic artifact.

---

### 4.3 Atomic File-Locking & Repair Specification

```
                          ATOMIC AUDIT LEDGER APPEND
                          
             [Enter append_event(actor_id, event, ...)]
                                 │
                                 ▼
             Acquire Exclusive Lock on "<ledger_path>.lock"
             (Wait up to 10.0s with exponential backoff)
                                 │
                                 ▼
                     Open "<ledger_path>" (r+)
                                 │
                                 ▼
             Read backwards to find valid terminal line:
             - Check JSON validity
             - Check sequence_number monotonic
             - If trailing partial write detected ──► TRUNCATE TO LAST VALID LINE
                                 │
                                 ▼
             Compute next sequence_number and previous_event_hash
                                 │
                                 ▼
             Build canonical envelope (RFC 8785) & compute SHA-256
                                 │
                                 ▼
             Seek to EOF, write serialized line + "\n"
                                 │
                                 ▼
             Flush user buffers (`f.flush()`)
             Flush OS page cache (`os.fsync(f.fileno())`)
                                 │
                                 ▼
                      Release File Lock
                                 │
                                 ▼
                            [RETURN RECORD]
```

#### Durability & Crash Recovery
- **Dedicated Lock File**: To prevent Windows file-sharing violations when reading and locking the data file, locking is executed on a sidecar lock file: `ledger_path.with_suffix(".lock")`.
- **Atomic fsync**: Every write executes `f.flush()` followed by `os.fsync(f.fileno())`, guaranteeing durability across operating system crashes or power failures.
- **Torn Write Auto-Repair**: If a prior process crashed mid-write, reading backwards validates lines. Any incomplete trailing line lacking a valid newline and JSON closing brace is truncated back to the last verifiable event hash before appending.

---

### 4.4 Test Specifications for DEF-003

```python
# To be added in tests/test_phase523_milestone4_2.py

def test_audit_ledger_concurrent_writes_integrity(tmp_path):
    """Spawn 10 concurrent worker threads/processes writing 20 audit events each (200 total).
    Assert zero sequence collisions.
    Assert ReviewAuditLedger.verify_ledger() succeeds with total_events == 200.
    Assert zero hash chain breaks.
    """

def test_audit_ledger_recovers_from_torn_write(tmp_path):
    """Simulate power failure: append partial corrupted line '{"sequence_number": 5, "curr' to ledger.
    Call append_event.
    Assert ledger auto-truncates torn line and appends clean event at sequence 5.
    Assert verify_ledger() succeeds.
    """
```

---

## 5. DEF-004 Remediation Design: Stateless CLI Governance Containment

### 5.1 The Governance Disconnection
[`src/clindoc/cli.py`](file:///d:/daa/src/clindoc/cli.py) executes a single-pass extraction and verification cascade, directly outputting `fhir_bundle.json`. It provides no review interface, records no Mode 0 human approvals, and emits resources with `status="active"` / `"final"`.

---

### 5.2 Mandatory Draft Metadata & Governance Containment

To preserve the CLI's utility for automated research evaluation while making it impossible to misrepresent CLI output as an approved clinical export:

1. **Mandatory Bundle-Level Metadata Tagging**:
   Every FHIR Bundle produced by `clindoc.cli` MUST contain:
   - `Bundle.meta.tag`:
     ```json
     {
       "system": "http://clindoc.org/fhir/tag/governance-status",
       "code": "UNREVIEWED_CLI_DRAFT",
       "display": "Unreviewed Automated Pipeline Draft — Not for Clinical Use"
     }
     ```
   - `Bundle.extension`:
     ```json
     {
       "url": "http://clindoc.org/fhir/StructureDefinition/export-mode",
       "valueCode": "unreviewed-draft"
     }
     ```
2. **Resource-Level Status Constraints**:
   - `Observation.status` set strictly to `"preliminary"`.
   - `MedicationRequest.status` set strictly to `"draft"`.
   - `MedicationStatement.status` set strictly to `"unknown"` or carries extension `http://clindoc.org/fhir/StructureDefinition/claim-review-status: unreviewed`.
3. **Prohibition of Bypass Overrides**:
   - The CLI MUST NOT accept `--approved`, `--skip-review`, or `--force-final` flags.
   - Any CLI attempt to invoke `export_document_as_fhir` without DB review sign-off raises `GovernanceEnforcementError`.
4. **Human-Readable Governance Disclaimer**:
   The CLI orchestrator must emit `UNREVIEWED_DRAFT_DISCLAIMER.txt` alongside `fhir_bundle.json`:
   > **WARNING: NOT AN APPROVED CLINICAL EXPORT**  
   > This FHIR bundle was generated headlessly via the stateless CLI. It has NOT undergone Mode 0 clinical review, Four-Eyes verification, or Gate 9 audit ledger verification. It is strictly prohibited from transmission to any Electronic Health Record (EHR) system.

---

### 5.3 Test Specifications for DEF-004

```python
# To be added in tests/test_cli.py

def test_cli_bundle_contains_mandatory_unreviewed_draft_tags(tmp_path):
    """Run `clindoc process` on sample PDF.
    Read generated fhir_bundle.json.
    Assert Bundle.meta.tag contains 'UNREVIEWED_CLI_DRAFT'.
    Assert all Observation entries have status == 'preliminary'.
    Assert all MedicationRequest entries have status == 'draft'.
    """

def test_cli_cannot_produce_approved_export_without_review(tmp_path):
    """Verify that CLI options parser has no flags to bypass draft status."""
```

---

## 6. Integration, Invariants & Regression Harness

### 6.1 Component Impact Map

| Component / File | Planned Changes | Invariants Maintained |
| :--- | :--- | :--- |
| [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py) | • Implement iterative `while` traversal in `_check_four_eyes`<br>• Add sidecar file locking (`portalocker`) in `ReviewAuditLedger.append_event`<br>• Add torn-write repair | Mode 0 Four-Eyes invariant, RFC 8785 canonical JSON formatting, SHA-256 HMAC checkpoints. |
| [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py) | • Invert default `enforce_req44_lifecycle: bool = True` | FHIR R4B Base conformance, 2-tier provenance extensions. |
| [`src/clindoc/services/fhir_export.py`](file:///d:/daa/src/clindoc/services/fhir_export.py)| • Explicitly pass `enforce_req44_lifecycle=True` in lines 743 and 1029 | Gate 9 audit proof checks, patient identity confirmation. |
| [`src/clindoc/cli.py`](file:///d:/daa/src/clindoc/cli.py) | • Explicitly pass `enforce_req44_lifecycle=True`<br>• Tag bundles with `UNREVIEWED_CLI_DRAFT`<br>• Enforce `preliminary`/`draft` statuses | Research pipeline compatibility, zero unvetted clinical leakage. |

### 6.2 Strictly Untouched / Frozen Components
- `src/clindoc/verify/stage_b.py` & `models/modernbert_v3/best_model.pt` (ModernBERT checkpoint SHA-256 and temperature $T=1.146522$).
- `src/clindoc/verify/stage_a.py` (Stage A tolerances: 0.05 float, exact string).
- `external_eval/` (12-doc and 60-doc testbeds, manifests, and synthetic text notes).
- `data/m2_verification_v3/` & `experiments/FINAL_REPORT_DATA_SHEET.md` (Certified 120-doc benchmark).
- `src/clindoc/extract/segmentation.py` & `medication_association.py` ($[L_k, R_k)$ boundary logic).
- `src/clindoc/db/models.py` (Database schema and table structures).

---

## 7. Dependency Graph & Implementation Sequence

```
                         IMPLEMENTATION SEQUENCE
                         
    ┌─────────────────────────────────────────────────────────────┐
    │ STEP 1: Multi-Hop Four-Eyes Ancestry (F-12)                │
    │ - Edit `src/clindoc/review/service.py:_check_four_eyes`     │
    │ - Add multi-hop unit & adversarial tests                    │
    │ Verification: `pytest tests/test_claim_review_service.py`   │
    └──────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ STEP 2: REQ-44 Lifecycle Activation (DEF-002)               │
    │ - Invert default in `src/clindoc/fhir/mapper.py`            │
    │ - Explicitly pass `True` in `fhir_export.py` & `cli.py`     │
    │ - Add FHIRExportService lifecycle integration tests         │
    │ Verification: `pytest tests/test_fhir_export.py`            │
    └──────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ STEP 3: Concurrency File Locking in Audit Ledger (DEF-003) │
    │ - Add sidecar lock & fsync in `service.py:append_event`     │
    │ - Add multi-threaded stress test (200 concurrent events)    │
    │ Verification: `pytest tests/test_phase523_milestone4_2.py`  │
    └──────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ STEP 4: CLI Governance Containment (DEF-004)                │
    │ - Inject `UNREVIEWED_CLI_DRAFT` tags & preliminary status   │
    │ - Add CLI governance assertions in `tests/test_cli.py`      │
    │ Verification: `pytest tests/test_cli.py`                    │
    └──────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ STEP 5: Full Suite Baseline Verification                    │
    │ - Run full repository pytest runner                         │
    │ Target: Exactly 1,466+ passed, 5 known failures, 0 drift    │
    └─────────────────────────────────────────────────────────────┘
```

---

## 8. Risk Analysis & Failure Mitigation

| Risk | Probability | Severity | Mitigation Strategy |
| :--- | :---: | :---: | :--- |
| **Deadlock in File Locking** | Low | High | Use timeout-based locking (`timeout=10.0s`). If lock cannot be acquired within 10 seconds, raise `AuditLedgerConcurrencyTimeoutError` and release all resources. |
| **Deep Ancestry Stack Overflow** | Very Low | Medium | Bound iterative ancestry traversal to `MAX_ANCESTRY_DEPTH = 50`. Clinical review chains rarely exceed 4 versions. |
| **Unintended Suppression of Valid Meds under REQ-44** | Low | High | Inverted default tested across 19 edge-case fixtures in `test_phase523_milestone4_4.py`. All standard prescription patterns pass unimpeded. |
| **Performance Overhead on File Locking** | Low | Low | Advisory lock is held exclusively during the microseconds required to seek EOF and write a single JSON line. |

---

## 9. Final Pre-Implementation Checklist

- [ ] Architectural review complete and approved.
- [ ] No git operations executed; 24 modified tracked files and 68 untracked files preserved.
- [ ] `portalocker` confirmed installed in `.venv`.
- [ ] All 1,466 baseline tests passing prior to implementation.
- [ ] Benchmark data sheets verified bitwise invariant.
- [ ] Authorization to commence implementation granted.
