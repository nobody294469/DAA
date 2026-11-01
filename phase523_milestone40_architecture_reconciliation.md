# ClinicalDoc — Phase 5.2.3 Milestone 4.0: Architecture Reconciliation & Implementation Readiness

**Execution Mode: STRICTLY READ-ONLY ASSESSMENT**  
**Author: Antigravity Agent (Pair Programming with Engineering Team)**  
**Date: October 31, 2026**  
**Status: COMPLETED — AWAITING USER REVIEW & AUTHORIZATION**

---

## Executive Summary

This architecture reconciliation report addresses the outstanding technical and governance questions raised during the review of the Milestone 4 Architecture Assessment. It establishes:
1. **Full-Suite Baseline Reconciliation**: Verifies via empirical test execution that the five historic baseline test failures remain identical and stable, correcting a report transcription discrepancy.
2. **ReviewService Persistence Contract**: Specifies the formal state machine, Four-Eyes authorization protocol, version-binding rules, and durable hash-chained audit ledger, distinguishing in-memory representations from durable persistence.
3. **Split-Dose vs. Tapering Regimen Representation**: Delineates FHIR R4B `dosageInstruction` modeling for concurrent split dosing versus sequential tapering regimens, ensuring that sequential regimens cannot be misconstrued as concurrent cumulative doses.
4. **Correct Implementation Phasing**: Establishes the safe sequence (4.1 Verification Cascade $\rightarrow$ 4.2 Review Service & Durable Audit $\rightarrow$ 4.3 FHIR Mapper Regimen Hardening $\rightarrow$ 4.4 End-to-End Validation) preventing mapper consumption of multi-dose regimens prior to operational review gating.
5. **Corrected Benchmark Acceptance Criteria**: Decouples pipeline execution success from extraction accuracy, establishing separate metrics and separate denominators across all evaluation scopes.
6. **Strict Scope & File Authorization**: Limits permissible code modifications to specific lines in authorized files, confirming the frozen status of all prior milestone modules.

---

## 1. Forensic Full-Suite Baseline Reconciliation

### 1.1 Empirical Test Execution Record

To resolve the discrepancy in failure classification, the full repository test suite was executed in the active project environment:

- **Execution Timestamp**: 2026-10-31T17:04:30+05:30 (Duration: 279.69s / 04m:39s)
- **Environment**: Python 3.12.8, Windows 11 AMD64
- **Virtual Environment**: `d:\daa\.venv`
- **Execution Command**: `.\.venv\Scripts\pytest -q`
- **Test Collection**: 1,411 tests collected across 58 test modules.
- **Results Summary**: **1,406 passed, 5 failed, 11 warnings**.

### 1.2 Exact Baseline Failure Identification

The five test failures observed during the complete suite run are:

| # | Test Identifier | Error Type | Failure Reason / Assertion | Historical Baseline Status |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `tests/test_cli.py::test_cli_unsupported_extension_fails` | `AssertionError` | `assert 0 == 1` (CLI process exit code handling) | **Known Pre-existing Baseline Failure** (Recorded in M1, M2, M3 reports) |
| 2 | `tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling` | `Failed` | `DID NOT RAISE UnsupportedFileTypeError` (Ingestion error mapping) | **Known Pre-existing Baseline Failure** (Recorded in M1, M2, M3 reports) |
| 3 | `tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation` | `AssertionError` | `assert 335 == 705` (Synthetic training example parity balance) | **Known Pre-existing Baseline Failure** (Recorded in M1, M2, M3 reports) |
| 4 | `tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline` | `AssertionError` | `assert ('987654' in ['105'] or '61' in ['105'])` (Disabled mode header extraction) | **Known Pre-existing Baseline Failure** (Recorded in M2.3, M3 reports) |
| 5 | `tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification` | `AssertionError` | `Found insecure 'verify=False' in ehr_transport.py` (String check triggers on comment: `ssl_verify=False is strictly forbidden`) | **Known Pre-existing Baseline Failure** (Recorded in M1, M2, M3 reports) |

### 1.3 Forensic Resolution of the Discrepancy

- **Did the test collection change?**  
  **No.** The test collection remains exactly 1,411 items.
- **Did the environment change?**  
  **No.** `d:\daa\.venv` was used consistently.
- **Did the historical failures disappear or get fixed?**  
  **No.** All five historical failures continue to fail with their exact recorded assertions.
- **Did new evaluation and GLiNER failures occur in pytest?**  
  **No.** The five evaluation and GLiNER failures mentioned in the preliminary assessment report were a **transcription error** caused by conflating offline scratch scripts (`scratch/audit_phase3.py` and GLiNER threshold evaluation scripts) with the pytest suite. Pytest does not fail on any GLiNER or evaluation pipeline tests.
- **Conclusion**: The baseline is completely stable. There are **zero new regressions** introduced by Milestones 1, 1.3, 2, 2.4, 3, or 3.1.

---

## 2. ReviewService Persistence Contract & Lifecycle Specification

Human clinical review under Mode 0 (PD-002, PD-004) is the central safety invariant governing provisional associations and slot conflict resolution. The following contract defines the review lifecycle, identity bindings, and durable audit persistence.

### 2.1 State Machine Lifecycle

The formal lifecycle of a claim within the review system is governed by `ClaimReviewState`:

```
                       ┌──────────────────────┐
                       │       PENDING        │ ◄───────────────────────────────┐
                       └──────────┬───────────┘                                 │
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

#### State Definitions:
1. **`PENDING`**: Initial state of any pipeline-extracted claim or new version created via edit. Gate 9 blocks export of provisional or ambiguous claims in this state.
2. **`ACCEPTED`**: Clinician has reviewed and formally confirmed the clinical validity of the claim and its associations (e.g., confirmed split dose or confirmed parallel bijection). Claim is export-eligible.
3. **`REJECTED`**: Clinician has determined the extraction is inaccurate, spurious, or clinically unsafe. Claim is quarantined and omitted from FHIR resource generation.
4. **`EDITED`**: Prior version of a claim that was modified by a clinician. The prior record is frozen immutably with state `EDITED`; a new version ($v+1$) is spawned with origin `reviewer` in state `PENDING` (PD-010).
5. **`ESCALATED`**: Reviewer flagged claim for specialist or supervisory adjudication. Remains non-exportable until resolved.

### 2.2 Reviewer Identity & Authorization

- **Actor Identity**: Must be a verified UUID representing an authenticated user (`clindoc.db.models.User`). Anonymous or unauthenticated reviews are rejected with `UnauthorizedReviewerError`.
- **Role Verification**: The actor must hold the `CLINICAL_REVIEWER` or `CHIEF_MEDICAL_OFFICER` role in `user_roles`.
- **Four-Eyes Enforcement (PD-004)**:
  - Demographic & Patient Identity matching requires an independent operator sign-off (`PatientIdentityApprovalStatus.CONFIRMED`).
  - High-risk claims (e.g., narcotic dosing, chemotherapy, or multi-dose split regimens) require that the reviewer UUID is distinct from the pipeline operator or any prior modifier (`actor_id != claim.created_by`).

### 2.3 Cryptographic Binding to Claim ID & Document Version

To prevent review decisions from detaching from their underlying clinical context:
- Every review action strictly binds:
  1. `claim_id` (Stable UUID of the clinical entity).
  2. `expected_version` (Optimistic locking: integer version of the claim). If the claim was concurrently edited or re-extracted, `OptimisticLockError` is raised.
  3. `document_id` (Source document UUID).
  4. `document_sha256` (Cryptographic hash of the raw document file). If document contents change, all prior reviews are invalidated.

### 2.4 Review Rationale & Timestamp

- **Mandatory Reason Codes**: For `REJECT`, `EDIT`, and `ESCALATE` actions, a non-empty `reason_code` string is mandatory (raises `MissingReasonCodeError` if omitted). Permitted reason codes include:
  - `CLINICAL_DISCREPANCY`
  - `AMBIGUOUS_DOSAGE_RESOLVED`
  - `INCORRECT_MEDICATION_ASSOCIATION`
  - `UNANCHORED_ORPHAN_REJECTED`
  - `SPLIT_REGIMEN_CONFIRMED`
  - `CHRONIC_DOSE_CONFIRMED`
- **Timestamp**: Recorded as UTC ISO-8601 with microsecond precision (`datetime.now(timezone.utc)`). Client-supplied timestamps are forbidden.

### 2.5 Duplicate & Repeated Confirmation Behavior

- If `accept_claim()` is invoked on a claim that is already `ACCEPTED`:
  - If `expected_version` matches and actor is identical: The operation is idempotent and returns the existing accepted claim without appending a duplicate audit event.
  - If a different reviewer attempts to confirm an already accepted claim: Raises `InvalidStateTransitionError("Cannot ACCEPT claim from 'ACCEPTED'. Allowed origin state is PENDING.")`. To change decisions, the claim must explicitly transition through `REOPEN`.

### 2.6 Rejection & Revocation Behavior

- **`REJECT`**: Immutably marks the claim as `REJECTED`. Gate 9 permanently suppresses the claim from export.
- **Revocation (`REOPEN`)**: If a clinical reviewer or supervisor determines a prior acceptance or rejection was erroneous, they must invoke `reopen_claim(claim_id, reason_code, expected_version)`.
  - Reopening transitions the claim back to `PENDING`.
  - Reopening appends a `ReviewActionType.REOPEN` record to the audit trail.
  - While in `PENDING`, Gate 9 immediately blocks export until a fresh acceptance is recorded.

### 2.7 Distinguishing In-Memory State from Durable Audit Persistence

| Feature | In-Memory Representation (`clindoc.extract.contracts`) | Relational Store (`clindoc.db.models`) | Offline / File-Based Export |
| :--- | :--- | :--- | :--- |
| **Object Type** | Pydantic / Dataclass `Claim` | SQLAlchemy `Claim` + `ReviewAction` | Serialized JSON Manifest (`.claims.json`) |
| **Review State Field** | `claim.review_state` (string / enum) | `Claim.review_state` (Column with enum) | JSON attribute `review_state` |
| **Durability** | **Ephemeral** (lost when Python process exits) | **Durable** (PostgreSQL / SQLite ACID) | **Durable** (Disk file with SHA-256) |
| **Audit Ledger** | Python list in `AuditTrail` | Cryptographic hash-chained `AuditEvent` table | Append-only `.audit.jsonl` with hash chaining |
| **Tamper Evidence** | None (in-memory mutation possible) | Cryptographic SHA-256 hash chaining | Cryptographic SHA-256 hash chaining |

#### Evaluating Non-Database Durable Review Persistence:
- **Architectural Fact**: The repository already possesses a full relational schema (`clindoc.db.models.AuditEvent`, `Claim`, `ReviewAction`) and `clindoc.review.service.ClaimReviewService`.
- **Limitation**: When running CLI batch processing (e.g. offline narrative evaluation on E3), the database is frequently not provisioned, relying purely on in-memory intermediate representations.
- **Durable Solution for Offline / CLI Mode**:
  - Rather than allowing unvalidated in-memory mutation (`claim.review_state = "ACCEPTED"`), offline workflows must write review decisions to an append-only JSONL audit ledger (`<doc_id>.review_audit.jsonl`).
  - Each line contains `compute_audit_event_hash(...)` linking to the previous review hash.
  - When `FHIRExportService` runs, it hydrates review decisions from this audit ledger, verifying the hash chain before setting `rev_state`. Any manual tampering breaks the hash chain, causing Gate 9 to fail closed.

---

## 3. Split-Dose versus Tapering Regimen Representation

Clinical narratives frequently express complex dosing regimens that fall into two distinct pharmacological classes: **Concurrent Split Dosing** and **Sequential Tapering Regimens**. Modeling these requires distinct FHIR R4B semantics.

### 3.1 Case A: Split Dosing

**Example Narrative**:  
`Carvedilol 6.25 mg in morning and 12.5 mg at bedtime.`

#### Clinical & Pharmacological Reality:
Both doses are active concurrently on the same days. The patient takes 6.25 mg at morning time AND 12.5 mg at night time (total daily dose = 18.75 mg).

#### FHIR R4B Representation:
- **Number of FHIR Resources**: **1** `MedicationRequest` (or `MedicationStatement`).  
  *Rationale*: A single prescription covers the drug entity. Creating two resources would represent two duplicate prescriptions for Carvedilol, inflating active medication counts in EHR reconciliations.
- **Number of `Dosage` Entries**: **2** distinct `Dosage` elements in `dosageInstruction`:
  ```json
  "dosageInstruction": [
    {
      "sequence": 1,
      "text": "6.25 mg in morning",
      "timing": {
        "repeat": {
          "frequency": 1,
          "period": 1,
          "periodUnit": "d",
          "when": ["MORN"]
        },
        "code": { "text": "morning" }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 6.25,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg",
            "extension": [ { "url": ".../element-provenance", "valueString": "claim-dose-1" } ]
          }
        }
      ]
    },
    {
      "sequence": 2,
      "text": "12.5 mg at bedtime",
      "timing": {
        "repeat": {
          "frequency": 1,
          "period": 1,
          "periodUnit": "d",
          "when": ["NIGHT"]
        },
        "code": { "text": "bedtime" }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 12.5,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg",
            "extension": [ { "url": ".../element-provenance", "valueString": "claim-dose-2" } ]
          }
        }
      ]
    }
  ]
  ```
- **Concurrency Semantics**: In FHIR R4B, multiple `dosageInstruction` entries without `timing.repeat.boundsDuration` or sequencing dependencies are interpreted by EHR scheduling engines as **concurrent instructions** taking place at their respective `when` times.
- **Provenance**: Dosage 1 links to the 6.25 mg dose claim; Dosage 2 links to the 12.5 mg dose claim.
- **Review Requirements**: Because Layer C identifies distinct non-overlapping timing anchors (`morning` vs `bedtime`), Layer C can associate both without slot conflict, but flags the regimen as `SPLIT_DOSE_REGIMEN`. Gate 9 requires clinician acceptance (`review_state == "ACCEPTED"`) before export.

---

### 3.2 Case B: Sequential Taper

**Example Narrative**:  
`Prednisone 20 mg daily for 5 days then 10 mg daily.`

#### Clinical & Pharmacological Reality:
The doses are **mutually exclusive in time**. Step 1 (20 mg) lasts 5 days. Step 2 (10 mg) commences on Day 6. The patient does NOT take 30 mg on Day 1.

#### FHIR R4B Representation:
- **Number of FHIR Resources**: **1** `MedicationRequest` (or `MedicationStatement`).
- **Number of `Dosage` Entries**: **2** distinct `Dosage` elements in `dosageInstruction`:
  ```json
  "dosageInstruction": [
    {
      "sequence": 1,
      "text": "20 mg daily for 5 days",
      "timing": {
        "repeat": {
          "frequency": 1,
          "period": 1,
          "periodUnit": "d",
          "boundsDuration": {
            "value": 5,
            "unit": "d",
            "system": "http://unitsofmeasure.org",
            "code": "d"
          }
        },
        "code": { "text": "daily" }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 20.0,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg",
            "extension": [ { "url": ".../element-provenance", "valueString": "claim-dose-taper-1" } ]
          }
        }
      ]
    },
    {
      "sequence": 2,
      "text": "then 10 mg daily",
      "patientInstruction": "Commence after completion of 20 mg step",
      "timing": {
        "repeat": {
          "frequency": 1,
          "period": 1,
          "periodUnit": "d"
        },
        "code": { "text": "daily" }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 10.0,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg",
            "extension": [ { "url": ".../element-provenance", "valueString": "claim-dose-taper-2" } ]
          }
        }
      ]
    }
  ]
  ```

#### Critical Concurrency Guard: Preventing Cumulative Misinterpretation
In FHIR R4B, if two dosage instructions are provided without duration boundaries, an automated dispensing machine or clinical decision support system could evaluate them as concurrent ($20 + 10 = 30\text{ mg}$ overdose risk). To guarantee that sequential tapering cannot be interpreted as concurrent:
1. **Mandatory Duration on Prior Steps**: Step 1 MUST contain `timing.repeat.boundsDuration` (e.g. 5 days).
2. **Sequential Semantic Marker**: Step 2 MUST contain sequential text qualification (`patientInstruction` or `text` starting with "then", "followed by", "subsequently").
3. **Ambiguity Suppression Gate**: If a clinical narrative specifies a taper step without a duration (e.g. *"Prednisone 20 mg then 10 mg"* without saying how many days 20 mg should be taken), this is **clinically indeterminate**. It CANNOT be safely emitted as a FHIR taper. The system must raise `AMBIGUOUS_SLOT_CONFLICT` and Gate 9 must strictly quarantine the order until a human clinician specifies the duration or active dose.

---

### 3.3 Regimen Comparison Matrix

| Property | Case A: Split Dosing (`Carvedilol`) | Case B: Sequential Taper (`Prednisone`) | Conflicting Doses (`Lisinopril 10 mg inc to 20 mg`) |
| :--- | :--- | :--- | :--- |
| **Clinical Concurrency** | **Concurrent** (both active daily) | **Sequential** (mutually exclusive phases) | **Mutually Exclusive** (prior vs current) |
| **FHIR Resources** | 1 `MedicationRequest` | 1 `MedicationRequest` | 1 `MedicationRequest` (after human review) |
| **`dosageInstruction` Entries** | 2 entries | 2 entries | 1 entry (active dose only) |
| **Sequence Field** | `sequence: 1` and `sequence: 2` (presentation order) | `sequence: 1` and `sequence: 2` (temporal execution order) | `sequence: 1` |
| **Timing Distinction** | Distinct `when` (`MORN` vs `NIGHT`) | Mandatory `boundsDuration` on Step 1 | Prior dose moved to audit/shadowed |
| **Review Gate (Mode 0)** | Requires `ACCEPTED` review state | Requires `ACCEPTED` review state | Suppressed until clinician picks active |
| **Unresolvable State** | Missing timing $\rightarrow$ Quarantine | Missing duration $\rightarrow$ Quarantine | Unreviewed $\rightarrow$ Quarantine |

---

## 4. Correct Implementation Order & Phasing

To guarantee that the FHIR mapper cannot accidentally consume provisional or ambiguous regimens before the review gating and audit logging mechanisms are operational, the implementation sequence is strictly phased as follows:

```
┌────────────────────────────────────────────────────────┐
│  Phase 4.1: Verification Cascade Anchor Propagation   │
│  (Topological integrity: anchor fail -> children fail) │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│  Phase 4.2: Mode 0 Review Service & Durable Audit      │
│  (State machine, hash-chained ledger, Gate 9 binding)  │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│  Phase 4.3: FHIR Mapper Regimen Hardening              │
│  (Multi-dosage serialization, slot candidate upgrades) │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│  Phase 4.4: End-to-End Pipeline & Benchmark Validation │
│  (20-doc E3, OOD-34, Adversarial suite, 1,411 tests)   │
└────────────────────────────────────────────────────────┘
```

### 4.1 Sub-Milestone 4.1: Verification Cascade Anchor Propagation
- **Objective**: Ensure that coordinate claims (`dosage`, `route`, `frequency`) inherit the verification failure of their parent anchor `med.name`.
- **Files to Modify**: `src/clindoc/verify/stage_a.py`.
- **Dependencies**: Milestones 1–3.
- **Stop Condition**: Stage A unit tests confirm that if a drug name claim is unverified, its coordinates receive `VerificationStatus.REJECTED` with reason `PARENT_ANCHOR_UNVERIFIED`. Full test suite passes.

### 4.2 Sub-Milestone 4.2: Mode 0 Review Service & Durable Audit Contract
- **Objective**: Implement the durable review state machine, hash-chained audit ledger, and wire review validation directly into Gate 9.
- **Files to Modify**: `src/clindoc/review/service.py`, `src/clindoc/services/fhir_export.py`.
- **Dependencies**: Sub-Milestone 4.1.
- **Stop Condition**: ReviewService tests confirm that PENDING provisional claims fail Gate 9, ACCEPTED claims pass Gate 9, and tamper attempts break the audit hash chain.

### 4.3 Sub-Milestone 4.3: FHIR Mapper Regimen Hardening
- **Objective**: Upgrade `select_deterministic_slot_candidate` and `mapper.py` (lines 2070–2320 only) to support multi-entry `dosageInstruction` arrays for confirmed split-dose regimens and tapers with distinct timing/duration.
- **Files to Modify**: `src/clindoc/fhir/mapper.py` (strictly lines 2070–2320).
- **Dependencies**: Sub-Milestones 4.1 and 4.2.
- **Stop Condition**: Mapper emits valid multi-entry `dosageInstruction` for confirmed split doses, emits sequential tapers with `boundsDuration`, and continues to pass all Phase 4 mapping regression tests.

### 4.4 Sub-Milestone 4.4: End-to-End Pipeline & Benchmark Validation
- **Objective**: Execute the complete integrated pipeline on the 20-document narrative corpus (E3) and adversarial test suite.
- **Files to Create**: `tests/test_phase523_milestone4_end_to_end.py`.
- **Dependencies**: Sub-Milestones 4.1–4.3.
- **Stop Condition**: All separate acceptance criteria met across all four evaluation scopes; full 1,411-test suite passes with exactly the 5 known historic failures (zero regressions).

---

## 5. Corrected Benchmark Acceptance Criteria & Separate Denominators

To maintain scientific integrity, **pipeline execution success is decoupled from clinical extraction accuracy**. Metrics are tracked with separate denominators across four distinct evaluation scopes.

### 5.1 Distinct Evaluation Scopes & Denominators

```
┌───────────────────────────────────────────────────────────────────────────┐
│                      BENCHMARK EVALUATION SCOPES                         │
├───────────────────────────────────────────────────────────────────────────┤
│ Scope 1: 20-Document Complex Narrative Corpus (E3 Scope)                 │
│   - Denominator: 20 unsegmented clinical narratives                       │
│                                                                           │
│ Scope 2: 120-Document GLiNER Evaluation Corpus                            │
│   - Denominator: 120 standard clinical encounter notes                    │
│                                                                           │
│ Scope 3: 34-Document Out-of-Distribution (OOD) Scope                      │
│   - Denominator: 34 unseen discharge summaries / novel layouts            │
│                                                                           │
│ Scope 4: Dedicated Adversarial Test Suite (Milestone 3/4 Scope)           │
│   - Denominator: 36 adversarial fixtures (26 M3 + 10 M4 cases)           │
└───────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Decoupled Acceptance Criteria Table

| Evaluation Scope | Metric Category | Specific Measurable Metric | Acceptance Threshold | Measurement Methodology |
| :--- | :--- | :--- | :--- | :--- |
| **Scope 1: E3 (20 docs)** | Pipeline Execution | **Pipeline Execution Success** | **100% (20/20 docs)** | Document ingests, segments, extracts, and evaluates Gate 9 with 0 unhandled exceptions. |
| **Scope 1: E3 (20 docs)** | FHIR Conformance | **FHIR R4B Schema Validity** | **100% of emitted bundles** | Emitted bundles pass official FHIR R4B validator schema. |
| **Scope 1: E3 (20 docs)** | Safety Isolation | **Cross-Medication Attribute Leakage** | **0.0% (0 occurrences)** | Verbatim evidence offset tracing confirms zero coordinates assigned to wrong drug. |
| **Scope 1: E3 (20 docs)** | Extraction Accuracy | **Token/Span Precision & Recall** | **Reported as-is vs GT** | Computed against frozen `e3_results.json` without modifying ground-truth labels. |
| **Scope 2: GLiNER (120 docs)** | Extraction Performance | **Entity-Level Precision, Recall, F1** | **Preserve Phase 5.1 baseline ($\pm 0.5\%$)** | Evaluated on frozen 120-document corpus. |
| **Scope 3: OOD (34 docs)** | Fail-Closed Safety | **Fail-Closed Quarantine Rate** | **100% of ungrounded or ambiguous claims** | Zero hallucinated or speculative coordinate attachments on novel document formats. |
| **Scope 4: Adversarial (36 cases)** | Safety Enforcement | **Ambiguous Association Quarantine** | **100% (36/36 tests pass)** | Every slot conflict, unanchored orphan, and unconfirmed provisional claim is quarantined. |
| **Scope 4: Adversarial (36 cases)** | Review Enforcement | **Mode 0 Gate 9 Rejection** | **100% of unreviewed provisional claims** | Zero provisional claims emitted without `review_state == "ACCEPTED"`. |
| **All Scopes** | Governance | **Provenance Minimization (PD-011)** | **100% compliant** | Verified zero unredacted PHI text in FHIR `Provenance` or extensions. |

---

## 6. Scope and File Authorization Matrix

To guarantee that Milestone 4 implementation remains tightly bounded and does not introduce regressions into established components, file modifications are restricted to the following exact boundaries:

### 6.1 Permitted File Changes by Sub-Milestone

| Sub-Milestone | File Path | Permitted Scope of Modification | Prohibited Modifications |
| :--- | :--- | :--- | :--- |
| **4.1** | `src/clindoc/verify/stage_a.py` | Add Pass 2 group topological integrity check (`verify_medication_group_integrity`). Cascade unverified drug name status to associated coordinate claims. | Do NOT modify regex patterns, UCUM dictionaries, or numeric parser logic. |
| **4.2** | `src/clindoc/review/service.py` | Ensure `ClaimReviewService` integrates cleanly with offline audit logging and Gate 9 review state validation. | Do NOT alter existing database schema or rollback transaction semantics. |
| **4.2** | `src/clindoc/services/fhir_export.py` | Wire audit ledger hydration into `evaluate_claim_eligibility` and enforce strict Mode 0 Gate 9 review state checking. | Do NOT alter existing DocumentReference or Patient export paths. |
| **4.3** | `src/clindoc/fhir/mapper.py` | **Strictly bounded to lines 2070–2320**: Upgrade `select_deterministic_slot_candidate` to recognize distinct timing slots; construct `list[Dosage]` with multiple entries for confirmed split doses and sequential tapers. | **DO NOT MODIFY** any code outside lines 2070–2320. Do NOT modify lab observation mapping, diagnostic report grouping, or patient demographic serialization. |
| **4.4** | `tests/test_phase523_milestone4_end_to_end.py` | **New File**: Add end-to-end integration and adversarial regression test suite (ADV-M4-01 through ADV-M4-10). | N/A (New test file). |

### 6.2 Strictly Frozen Modules (ZERO Code Modifications Permitted)

The following modules remain strictly frozen and must NOT be edited during Milestone 4:
1. `src/clindoc/extract/medication_association.py` (Layer C Rules 1–6 Engine complete & verified).
2. `src/clindoc/extract/segmentation.py` (Sentence/clause segmenter verified).
3. `src/clindoc/extract/deterministic.py` (Grounding invariants verified).
4. `src/clindoc/ocr/born_digital.py` (Ingestion geometry verified).
5. `src/clindoc/ocr/layout.py` (Layout engine verified).
6. `configs/extraction.yaml` (Permitted fields DEC-020 frozen).
7. `configs/loinc_subset.json` & `configs/ucum_subset.json` (Subset vocabularies frozen).
8. `src/clindoc/fhir/constants.py` (Extension URIs and constants frozen).
9. `experiments/E3-narrative-extraction/` (Benchmark data & results strictly frozen).
10. `tests/test_phase523_milestone*.py` (All prior milestone test suites strictly frozen).

---

## 7. Summary of Unresolved Decisions Requiring Explicit User Approval

The following four specific decisions are submitted for final user authorization before implementation begins:

| Decision # | Core Architectural Question | Recommended Choice | Rationale |
| :---: | :--- | :--- | :--- |
| **DEC-1** | **Mapper Authorization**: Authorize modifying lines 2070–2320 of `src/clindoc/fhir/mapper.py` for multi-dosage serialization? | **YES (Approve Option 1A)** | Essential for clinically valid split dosing and tapering representation in FHIR R4B. |
| **DEC-2** | **Implementation Phasing**: Adopt the revised sequence (4.1 Cascade $\rightarrow$ 4.2 Review Service $\rightarrow$ 4.3 Mapper $\rightarrow$ 4.4 End-to-End)? | **YES (Adopt Revised Order)** | Guarantees mapper cannot consume multi-dose regimens without review gate enforcement. |
| **DEC-3** | **Durable Audit for Offline Mode**: Adopt hash-chained append-only JSONL audit ledger for offline CLI review persistence? | **YES (Approve JSONL Ledger)** | Provides tamper-evident durable audit records in non-database environments without adding heavy dependencies. |
| **DEC-4** | **Warnings W-1 & W-2**: Keep complex parentheticals and multi-page orders strictly fail-closed? | **YES (Preserve Fail-Closed)** | Prevents speculative heuristic contamination; preserves established safety invariants. |

---

## 8. Stop Condition

In strict accordance with the **READ-ONLY** mandate, no production code, tests, or configuration files have been modified.

**Execution is halted. Implementation of Milestone 4 will commence only upon explicit user authorization of the four decisions listed in Section 7.**
