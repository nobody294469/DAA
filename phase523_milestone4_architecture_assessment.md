# ClinicalDoc — Phase 5.2.3: Milestone 4 Architecture & Dependency Assessment

**Execution Mode: STRICTLY READ-ONLY ASSESSMENT**  
**Author: Antigravity Agent (Pair Programming with Engineering Team)**  
**Date: October 31, 2026**  
**Status: COMPLETED — AWAITING USER REVIEW & AUTHORIZATION**

---

## Executive Summary

Phase 5.2.3 Milestones 1 through 3.1 have established a deterministic, geometry-aware, fail-closed extraction and association foundation for clinical medication and laboratory entities. Specifically:
- **Milestone 1 & 1.1–1.3**: Established deterministic sorting, permutation-invariant claim generation, and strict complete character segment containment.
- **Milestone 2 & 2.1–2.4**: Established robust clinical text segmentation (`segment_clinical_text`), decimal/abbreviation protection, OCR layout continuation across line breaks, geometry fail-closed bounding, and multi-dose ambiguity detection.
- **Milestone 3**: Delivered the Layer C Conservative Association Engine (`src/clindoc/extract/medication_association.py`) implementing Rules 1 through 6, provisional `respectively` handling requiring clinician confirmation, shared-attribute scoping, and Gate 9 export suppression in `FHIRExportService`.
- **Milestone 3.1**: Successfully executed an independent forensic safety audit across 26 adversarial test cases with 0 blockers and documented warnings W-1 (parenthetical nested clauses) and W-2 (multi-page orders).

This document provides a comprehensive technical architecture and dependency assessment for **Milestone 4**. It establishes the current repository state, reconciles original specification evidence with observed implementation facts, identifies downstream architectural bottlenecks, defines the recommended Milestone 4 scope, delineates risks, outlines a phased implementation plan, and presents the unresolved decisions requiring explicit user authorization before any production code is written.

---

## 1. Current Repository and Milestone Status

### 1.1 Git Working-Tree Inspection
A forensic inspection of the working tree (`master` branch, commit baseline aligned with `origin/master`) reveals:
- **Tracked Modifications (21 files)**:
  - Extraction & Verification: `src/clindoc/extract/deterministic.py`, `llm_extractor.py`, `router.py`, `gliner_fallback.py`, `evaluate.py`, `verify/stage_a.py`.
  - Ingestion & OCR: `src/clindoc/ingest/service.py`, `m2/context.py`, `ocr/__init__.py`, `ocr/born_digital.py`, `ocr/ingest.py`.
  - FHIR Mapping & Export: `src/clindoc/fhir/constants.py`, `mapper.py`, `validator.py`, `services/fhir_export.py`.
  - Configs & Subset Vocabularies: `configs/extraction.yaml`, `loinc_subset.json`, `ucum_subset.json`.
  - Regression Test Fixtures: `tests/test_canonical_fhir_mapping.py`, `test_extraction_subsystem.py`, `test_fhir_export.py`, `test_pd022_qualitative_labs.py`.
- **Untracked Additions (48 files)**:
  - New Core Modules: `src/clindoc/extract/medication_association.py`, `src/clindoc/extract/segmentation.py`, `src/clindoc/extract/section_detector.py`, `src/clindoc/ocr/layout.py`, `src/clindoc/ocr/table_layout.py`.
  - Comprehensive Test Suites: `tests/test_phase523_milestone1_deterministic_grounding.py`, `test_phase523_milestone13_anchor_ordering.py`, `test_phase523_milestone2_segmentation.py`, `test_phase523_milestone22_safety_remediation.py`, `test_phase523_milestone23_ocr_layout.py`, `test_phase523_milestone24_geometry_dose_safety.py`, `test_phase523_milestone3_medication_association.py`, `test_phase52_medication_coreference.py`.
  - Audit & Verification Reports: All milestone reports (Milestone 1, 1.1, 1.2, 1.3, 2, 2.1, 2.2, 2.3, 2.4, 3, 3.1) are persisted in the root directory.

### 1.2 Full Test Suite Baseline
Execution of the complete repository test suite confirms:
- **Total Tests**: 1,411
- **Passed**: 1,406
- **Failed**: 5 (Pre-existing baseline failures in Phase 2/3 legacy tests, exactly matching recorded baseline):
  1. `test_evaluation_pipeline.py::test_evaluate_end_to_end`
  2. `test_evaluation_pipeline.py::test_calculate_metrics_edge_cases`
  3. `test_gliner_real_world.py::test_gliner_entity_types`
  4. `test_gliner_real_world.py::test_gliner_thresholds`
  5. `test_gliner_real_world.py::test_gliner_pipeline_integration`
- **Regressions**: **0 new regressions across all Phase 4, Phase 5.1, Phase 5.2, and Phase 5.2.3 suites**.
- **Phase 5 Focused Suite**: 150/150 passed in 1.85s.
- **FHIR Export Service Suite**: 16/16 passed in 1.03s.

### 1.3 Milestone Status Ledger

| Milestone | Title | Status | Primary Artifacts | Key Deliverable / Invariant |
| :--- | :--- | :--- | :--- | :--- |
| **Milestone 1** | Deterministic Ordering & Grounding Invariants | **COMPLETE** | `phase523_milestone1_report.md`, `phase523_milestone11_grounding_audit.md` | Permutation-invariant sorting in `_build_claims()`, complete segment containment, source coordinate fidelity. |
| **Milestone 1.3** | Anchor Ordering Remediation | **COMPLETE** | `phase523_milestone13_anchor_ordering_fix_report.md` | Explicit anchor stabilization preventing coordinate drift across identical confidence extractions. |
| **Milestone 2** | Sentence & Clause Segmentation | **COMPLETE** | `phase523_milestone2_sentence_clause_segmentation_report.md` | Clinical boundary segmentation (`segment_clinical_text`), decimal protection, abbreviation protection. |
| **Milestone 2.4** | Geometry Fail-Closed & Dose Ambiguity Hardening | **COMPLETE** | `phase523_milestone24_geometry_and_dose_safety_report.md` | Bounding box spatial containment, multi-dose detection in single clauses, fail-closed geometry fallback. |
| **Milestone 3** | Layer C Conservative Association Engine | **COMPLETE** | `phase523_milestone3_implementation_report.md`, `phase523_milestone3_benchmark_report.md` | Rules 1–6 association engine, Gate 9 export suppression in `FHIRExportService`, provisional `respectively` tracking. |
| **Milestone 3.1** | Independent Association Safety Audit | **COMPLETE** | `phase523_milestone31_independent_association_safety_audit.md` | 26 adversarial edge cases evaluated; 0 blockers; documented warnings W-1 (parentheticals) and W-2 (multi-page). |
| **Milestone 4** | Verification Cascade & FHIR Mapper Integration | **PENDING** | *This Assessment* | Downstream mapper multi-dose support, verification cascade anchor propagation, end-to-end integration, PD-011 audit. |

---

## 2. Original Milestone 4 Specification Evidence

To establish the genuine intended scope of Milestone 4, existing specification documents and architectural designs were inspected.

### 2.1 Specification Citations

1. **`phase523_implementation_readiness.md` (§5, lines 258–262, 291–299)**:
   > *"Milestone 4: Export Safety & Mode 0 Review Gate Integration"*
   > - *Integrate Gate 9 into `FHIRExportService` and wire upstream ambiguity flags into FHIR bundle suppression.*
   > - *Enforce human review (Mode 0) before unquarantining provisional / complex associations.*
   > - *Verify zero cross-medication attribute leakage on 20-document narrative corpus.*

2. **`phase522_medication_association_design.md` (§8, lines 205–208)**:
   > *"Milestone 4: Verification Cascade & FHIR Mapper Integration"*
   > - *Wire Rule 1–6 outcomes into Stage A verification pipeline and FHIR resource builder.*
   > - *Ensure cross-attribute slot conflict resolution in mapper.*
   > - *Preserve element-level provenance (DEC-016) while minimizing PHI leakage (PD-011).*

3. **`PRODUCTIZATION_DECISIONS.md`**:
   - **PD-004**: *Mode 0 Human Review Architecture*. Mandates that high-risk, provisional, or ambiguous extractions require clinician sign-off before export eligibility.
   - **PD-007 / PD-008**: *Deterministic Fail-Closed Export*. Any ungrounded claim, contaminated group, or ambiguous association must trigger bundle suppression or resource omit with deterministic reason codes.
   - **PD-011**: *Provenance Minimization*. Provenance records must link to evidence without leaking raw unredacted PHI in export extensions.
   - **PD-016**: *Clinical Safety Architecture*. Strict containment, complete isolation between medication groups, zero phantom attributes.
   - **DEC-016**: *Element-level Provenance Extension*. FHIR resources must trace each mapped element back to its source claim ID.
   - **DEC-020**: *Permitted Canonical Narrative Fields*. Strict enforcement of allowed clinical fields (`med.name`, `med.dosage`, `med.frequency`, `med.route`, `med.status`).

---

## 3. Actual Implementation Gaps

A comparison between the original specification and the current codebase reveals that while **Gate 9 export suppression was successfully implemented in `FHIRExportService` during Milestone 3**, several critical downstream and pipeline integration components remain incomplete:

### Gap 1: Downstream FHIR Mapper Bottleneck (`src/clindoc/fhir/mapper.py`)
In Milestone 3, `src/clindoc/fhir/mapper.py` was intentionally left untouched to adhere to frozen Phase 4 contracts. However, inspection of `mapper.py` reveals significant architectural limitations:
1. **Single-Candidate Slot Bottleneck (Line 2105)**:
   ```python
   # mapper.py: select_deterministic_slot_candidate()
   # Selects ONLY ONE dose candidate per medication group!
   ```
   If a valid clinical narrative contains a split dose or tapering regimen (e.g., *"Carvedilol 6.25 mg morning and 12.5 mg evening"* or *"Prednisone 20 mg daily for 5 days then 10 mg daily"*), Layer C association properly groups both doses under their respective timing anchors. However, `mapper.py`'s `select_deterministic_slot_candidate` forces all subsequent dose claims into `EXT_COMPETING_CANDIDATE` / `shadowed`, completely dropping valid split dosing!
2. **Single DosageInstruction Generation (Lines 2202–2253)**:
   `mapper.py` constructs a hardcoded single `Dosage` element:
   ```python
   dosage = [Dosage(text=..., doseAndRate=[DosageDoseAndRate(...)])]
   ```
   FHIR R4B `MedicationRequest.dosageInstruction` and `MedicationStatement.dosage` are defined as `list[Dosage]`. The mapper currently cannot emit multi-entry dosage instructions even when split dosing has been confirmed by a clinician.
3. **Legacy Multi-Name Contamination Check (Line 2088)**:
   `mapper.py` contains a legacy check:
   ```python
   if len(med_name_claims) > 1:
       # Suppresses group as contaminated
   ```
   While Milestone 3 solved multi-name isolation upstream by generating distinct group UUIDs for distinct drug names, if coordinate claims (such as a shared frequency or shared route) share evidence across groups, the downstream interaction with mapper slot resolution requires formal verification.

### Gap 2: Verification Cascade Anchor Disconnect (`src/clindoc/verify/stage_a.py`)
Currently, Stage A verification evaluates each claim in isolation based on regex patterns, dictionary lookups, and character offsets. It possesses no awareness of association topology:
- If a `med.name` anchor fails verification (e.g., ungrounded, low confidence, or blacklisted), its associated coordinate claims (`med.dosage`, `med.frequency`, `med.route`) are not automatically invalidated or cascaded to fail-closed.
- Coordinate claims can theoretically arrive at Gate 9 without an active verified anchor, relying solely on Gate 9's orphan check rather than a coherent verification cascade.

### Gap 3: Mode 0 Review Transition State Rigidity
In Milestone 3, Gate 9 enforces:
```python
if (claim.is_provisional or claim.ambiguity_flags) and claim.review_state != "CONFIRMED":
    return EligibilityResult(eligible=False, suppression_reason=...)
```
While this correctly blocks export, the repository lacks:
1. A programmatic interface to transition claims from `review_state="PENDING"` to `review_state="CONFIRMED"` or `review_state="REJECTED"` with an immutable clinician audit record.
2. Verification that clinician confirmation safely clears the specific suppression reason without bypassing other invariants (e.g., grounding or geometry).

### Gap 4: End-to-End Pipeline & Provenance Compliance (PD-011 / DEC-016)
Full end-to-end integration testing from raw clinical narrative text through segmentation -> LLM/deterministic extraction -> Layer C association -> Verification Cascade -> Gate 9 eligibility -> FHIR Mapper -> Bundle validation does not exist as a single automated regression suite. Furthermore, exported bundles have not been formally audited against PD-011 provenance minimization.

---

## 4. Recommended Milestone 4 Scope

To resolve these implementation gaps safely and incrementally, Milestone 4 must be focused on **FHIR Mapper Regimen Hardening, Verification Cascade Alignment, and End-to-End Pipeline Validation**.

### 4.1 Scope Boundaries

#### What Milestone 4 IS:
1. **Downstream FHIR Mapper Regimen Hardening**: Upgrading `src/clindoc/fhir/mapper.py` to support multi-entry `dosageInstruction` arrays for confirmed split-dose and temporal regimens, replacing the crude single-candidate slot bottleneck with regimen-aware slot resolution.
2. **Verification Cascade Anchor Propagation**: Hardening `src/clindoc/verify/stage_a.py` and verification models so that attribute claims inherit the verification status of their parent medication anchor (fail-closed anchor cascade).
3. **Mode 0 Review State Transition & Audit Enforcement**: Implementing the formal review transition service that enables clinicians to confirm or reject provisional (`respectively`, split dose) associations with Four-Eyes audit logging.
4. **End-to-End Pipeline Integration & Benchmark**: Validating the unified pipeline on the 20-document narrative corpus and running the full 1,411-test regression suite.
5. **PD-011 Provenance Minimization Audit**: Verifying that exported FHIR bundles contain complete DEC-016 provenance extensions without leaking raw unredacted PHI.

#### What Milestone 4 IS NOT:
- NOT an expansion of LLM prompts or extraction ontologies (DEC-020 narrative fields remain strictly frozen).
- NOT an automated resolution of W-1 (parenthetical nested clauses) or W-2 (multi-page spanning orders) — both must remain fail-closed and quarantined.
- NOT a modification of the frozen Phase 4 FHIR resource schemas (R4B MedicationRequest, MedicationStatement, Observation).
- NOT Phase 5.3 (Production Deployment / Cloud Infrastructure).

---

## 5. Technical Architecture & Dependency Assessment

### 5.1 End-to-End Pipeline Dataflow Architecture

```
[ Clinical Text (Narrative / OCR Layout) ]
                   │
                   ▼
[ Milestone 2: segment_clinical_text() ] ─── Enforces Decimal, Abbreviation & Geometry Bounds
                   │
                   ▼
[ Milestone 1: _build_claims() ] ────────── Deterministic Permutation-Invariant Claim Generation
                   │
                   ▼
[ Milestone 3: associate_medications() ] ── Rules 1–6 Layer C Engine
                   │                         ├── Unique Group UUID per Drug Anchor
                   │                         ├── Shared Coordinate Scoping
                   │                         └── Provisional 'respectively' Flagging
                   ▼
[ Milestone 4.1: Verification Cascade ] ── Anchor Verification Propagation (Fail-Closed)
                   │
                   ▼
[ Milestone 3: Gate 9 (fhir_export.py) ] ── Evaluates Eligibility & Mode 0 Review State
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
   [ REJECTED /        [ ELIGIBLE /
     QUARANTINED ]       CONFIRMED ]
         │                   │
         │                   ▼
         │         [ Milestone 4.2: mapper.py ]
         │                   ├── Regimen-Aware Slot Resolution
         │                   ├── Multi-entry dosageInstruction Array
         │                   └── DEC-016 Element-Level Provenance (PD-011 Minimized)
         │                   │
         ▼                   ▼
[ Audit Trail Log ]  [ FHIR R4B Bundle ]
```

### 5.2 Component Touchpoints and Architectural Dependencies

| Component | Path | Current Status | Milestone 4 Expected Change | Dependency on M1–M3.1 |
| :--- | :--- | :--- | :--- | :--- |
| **Association Engine** | `src/clindoc/extract/medication_association.py` | Complete (Rules 1–6) | **NO CODE CHANGES** (Frozen). Acts as upstream producer of `MedicationGroup` and `Claim.metadata`. | Full dependency (M1, M2, M3). |
| **Segmentation Engine** | `src/clindoc/extract/segmentation.py` | Complete | **NO CODE CHANGES** (Frozen). | Base dependency (M2). |
| **Verification Cascade** | `src/clindoc/verify/stage_a.py` & `cascade.py` | Standalone claim verification | **MODIFY**: Add parent anchor verification check (`verify_medication_group_integrity`). If anchor fails, children fail. | Relies on M3 group UUIDs. |
| **FHIR Export Service** | `src/clindoc/services/fhir_export.py` | Gate 9 implemented | **MODIFY**: Add Mode 0 review transition handler (`transition_review_state`) and PD-011 audit filter. | Relies on Gate 9 logic. |
| **FHIR Mapper** | `src/clindoc/fhir/mapper.py` | Frozen Phase 4 (Single dose slot bottleneck) | **MODIFY**: Upgrade `select_deterministic_slot_candidate` to support distinct timing slots; construct `list[Dosage]` for split regimens. | Critical touchpoint; requires explicit authorization. |
| **FHIR Constants & Extensions** | `src/clindoc/fhir/constants.py` | Frozen Phase 4 | **MODIFY**: Add standard extension URIs for multi-regimen linking if needed; verify DEC-016 compliance. | No breaking changes. |
| **Audit Trail & Models** | `src/clindoc/core/contracts.py` | Complete | **POSSIBLE MODIFY**: Ensure review transition actions and reason codes are strictly typed. | Preserves existing schemas. |

### 5.3 Architectural Duplication & Fragile Points Identified

1. **Duplicate Slot Conflict Resolution**:
   - Upstream Layer C (`medication_association.py`) already detects within-clause dose adjustment conflicts and applies Rule 6 (marks both doses with `is_ambiguous=True`, `conflict_type="WITHIN_CLAUSE_DOSE_ADJUSTMENT"`).
   - Downstream `mapper.py` independently runs `select_deterministic_slot_candidate`, which sorts by confidence and drops the second candidate into `EXT_COMPETING_CANDIDATE`.
   - *Fragility*: If Layer C approves a valid split dose (e.g. morning vs evening), `mapper.py`'s legacy logic still drops one of the doses! The downstream mapper must respect upstream association semantics.
2. **Review State Representation**:
   - `Claim.review_state` is currently an optional string field on `Claim`.
   - Gate 9 checks for `"CONFIRMED"`.
   - *Fragility*: Lack of an enumerated `ReviewState` (`PENDING`, `CONFIRMED`, `REJECTED`, `OVERRIDDEN`) creates the risk of subtle typo regressions (e.g. `"APPROVED"` vs `"CONFIRMED"`).

---

## 6. Preservation of Accepted Safety Invariants

Milestone 4 must strictly uphold all safety guarantees established in prior milestones:

1. **Strict Evidence Grounding & Source Offsets**: Every claim and mapped FHIR element must trace to an exact character span in the source text. Zero fabricated values.
2. **Complete Segment Containment**: Spans must be strictly contained within sentence/clause boundaries (`start >= seg.start and end <= seg.end`).
3. **Geometry Fail-Closed Behavior**: Extractions lacking valid spatial coordinates or crossing table bounding boxes must fail closed.
4. **Medication & Laboratory Grouping Isolation**: Absolute segregation between medication claims and observation/lab claims. Zero crosstalk.
5. **Conservative Medication Association**: Unassociated attributes must remain orphans. No speculative or nearest-neighbor bridging across clause boundaries.
6. **Shared-Attribute Scope Enforcement**: Shared coordinates are replicated only when triggered by explicit semantic coordination markers (`both`, `each`, `all`).
7. **Provisional Association Clinician Sign-off**: Associations derived from `respectively` or split-dose patterns must remain provisional and require human confirmation before export eligibility.
8. **Ambiguous Medication Association Quarantine**: Any unresolvable slot conflict or ambiguous association must trigger Gate 9 suppression (`SUPPRESSED_AMBIGUOUS_ASSOCIATION`).
9. **Temporal & Conditional Safeguards**: Temporal adjustments and conditional instructions must be captured without overwriting baseline regimens.
10. **Gate 9 Export Eligibility**: No unconfirmed provisional claim or ambiguous association may be emitted into a FHIR bundle.
11. **Mode 0 / Four-Eyes Principle**: Mode 0 human review must be enforced for high-risk extractions.
12. **Stable Suppression Reason Codes**: Reason codes (`SUPPRESSED_UNCONFIRMED_PROVISIONAL`, `SUPPRESSED_AMBIGUOUS_ASSOCIATION`, `SUPPRESSED_PARENT_ANCHOR_UNVERIFIED`) must remain deterministic and stable.
13. **Audit-Trail Provenance**: Complete provenance linking with zero raw PHI leakage (PD-011).

### Documented Warnings (Must Remain Active)
- **W-1: Complex Parenthetical Embedded Clauses**: Nested parentheticals (e.g., *"Metoprolol (previously 25 mg daily, increased due to tachycardia) 50 mg daily"*) must continue to fail closed as ambiguous or provisional until explicit syntax parsing is authorized.
- **W-2: Multi-Page Spanning Medication Regimens**: Cross-page continuation orders lacking explicit coordinate markers must fail closed as orphans.

---

## 7. Risks and Design Alternatives

### Feature 1: Multi-Dosage Regimen Mapping in `mapper.py`

#### Problem Description
`mapper.py` currently selects only one dose candidate per medication group (`select_deterministic_slot_candidate`) and instantiates a single `Dosage` object (`dosage = [Dosage(...)]`). Valid split regimens (e.g., morning 25 mg, night 50 mg) or tapering regimens cannot be represented in the emitted FHIR resource.

#### Design Alternatives

##### Alternative A: Service-Layer Pre-Filtering & Virtual Grouping (Workaround)
- *Mechanism*: Keep `mapper.py` strictly frozen. In `FHIRExportService`, split multi-dose medication groups into separate virtual `MedicationGroup` instances (e.g., Group-1A for morning dose, Group-1B for night dose), passing them separately to `mapper.py`.
- *Pros*: Does not modify frozen `mapper.py`. Zero risk of breaking Phase 4 mapper unit tests.
- *Cons*: Highly fragile architectural hack. Generates two distinct FHIR `MedicationRequest` resources for a single medication order, distorting clinical reality and inflating resource counts. Violates FHIR best practices where a single order with multiple timing instructions belongs in a single `MedicationRequest.dosageInstruction` array.
- *Safety Rating*: Low / Undesirable.

##### Alternative B: Regimen-Aware Multi-Dosage Support in `mapper.py` (Recommended)
- *Mechanism*: Modify `src/clindoc/fhir/mapper.py` to allow multiple dose claims within a medication group *only when* each dose claim is associated with a distinct, non-overlapping `timing` or `condition` claim. Construct a compliant `list[Dosage]` containing multiple `Dosage` elements in `MedicationRequest.dosageInstruction` and `MedicationStatement.dosage`.
- *Pros*: Clinically and architecturally accurate. Full FHIR R4B standard compliance. Clean element-level provenance for each dosage instruction.
- *Cons*: Requires modifying `src/clindoc/fhir/mapper.py` (which requires explicit user authorization).
- *Safety Rating*: **High / Recommended**.

#### Failure Mode & Safeguards
If multiple doses exist without distinguishing timing or conditions, the group is a true slot conflict (Rule 6). In this scenario, `mapper.py` must fail closed and suppress the resource. Only distinct-timing or clinician-confirmed split doses are permitted to emit multi-entry dosage arrays.

---

### Feature 2: Verification Cascade Anchor Propagation

#### Problem Description
Currently, Stage A verification verifies claims independently. If a drug name claim `med.name` fails verification, its associated `med.dosage` claim could pass verification in isolation, creating floating verified attributes for an invalid medication.

#### Design Alternatives

##### Alternative A: Downstream Gate 9 Filter Only (Current State)
- *Mechanism*: Rely solely on Gate 9 in `FHIRExportService` to notice that the parent group lacks an eligible `med.name`.
- *Pros*: Requires no changes to `stage_a.py`.
- *Cons*: Inconsistent intermediate representation. Stage A reports claims as "VERIFIED" even though they belong to a dead/unverified anchor. Leaks invalid claims into downstream consumers that do not run Gate 9.
- *Safety Rating*: Medium.

##### Alternative B: Two-Pass Group-Aware Verification Cascade (Recommended)
- *Mechanism*: In `src/clindoc/verify/stage_a.py` (or a dedicated `cascade.py` step), execute Pass 1 (individual claim verification) followed by Pass 2 (group topological verification). In Pass 2, if a group's anchor `med.name` is not `VERIFIED`, all associated coordinate claims within that group are transitioned to `UNVERIFIED` with reason `PARENT_ANCHOR_UNVERIFIED`.
- *Pros*: Completely eliminates orphaned verified attributes. Enforces true topological consistency across the extraction pipeline.
- *Cons*: Small additional verification pass (< 5ms latency).
- *Safety Rating*: **High / Recommended**.

---

### Feature 3: Mode 0 Review Transition Interface

#### Problem Description
Gate 9 suppresses provisional and ambiguous claims unless `claim.review_state == "CONFIRMED"`. Currently, there is no standardized API or service method to perform this state transition, validate credentials, and record the clinician's review in the immutable audit trail.

#### Design Alternatives

##### Alternative A: Ad-hoc Direct Mutation of Claim Objects
- *Mechanism*: Allow client code or test scripts to set `claim.review_state = "CONFIRMED"` directly.
- *Pros*: Trivial to write.
- *Cons*: Severe safety risk. Bypasses audit trail, lacks clinician ID recording, does not verify Four-Eyes protocol, open to silent bypass of safety gates.
- *Safety Rating*: **Unacceptable**.

##### Alternative B: Dedicated Review Transition Service (`ReviewService`) (Recommended)
- *Mechanism*: Implement `ReviewService.confirm_provisional_association(claim_id, reviewer_id, rationale, audit_context)`. Validates that the claim is eligible for review, verifies the reviewer's authorization, transitions `review_state` to `CONFIRMED`, and records an immutable event in `AuditTrail`.
- *Pros*: Enforces PD-004 Mode 0 requirements, provides complete Four-Eyes compliance, guarantees tamper-evident audit logs.
- *Cons*: Requires formal service implementation and tests.
- *Safety Rating*: **High / Recommended**.

---

## 8. Proposed Milestone 4 Implementation Sequence

Milestone 4 is structured into four independent, sequentially verifiable sub-milestones:

```
[ Milestone 4.1: Cascade Anchor Propagation ]
                   │
                   ▼
[ Milestone 4.2: FHIR Mapper Regimen Hardening ]
                   │
                   ▼
[ Milestone 4.3: Mode 0 Review Service & Audit ]
                   │
                   ▼
[ Milestone 4.4: End-to-End Pipeline & Benchmarks ]
```

### Sub-Milestone 4.1: Verification Cascade Anchor Propagation
- **Objective**: Ensure that coordinate claims inherit the verification status of their parent anchor claim, preventing floating verified attributes for unverified drugs.
- **Files to Modify**: `src/clindoc/verify/stage_a.py`, `src/clindoc/verify/cascade.py`.
- **Dependencies**: Milestones 1–3.
- **Acceptance Criteria**:
  - If `med.name` fails verification, all coordinate claims (`dosage`, `route`, `frequency`) in its group receive `VerificationStatus.REJECTED` with reason `PARENT_ANCHOR_UNVERIFIED`.
  - Zero floating verified attributes in the Intermediate Representation.
- **Regression Suite**: `tests/test_extraction_subsystem.py`, all Phase 5.1/5.2 verification tests.

### Sub-Milestone 4.2: FHIR Mapper Regimen Hardening & Multi-Dosage Support
- **Objective**: Enable `src/clindoc/fhir/mapper.py` to construct multi-entry `dosageInstruction` arrays for confirmed split-dose regimens with distinct timing, while strictly preserving fail-closed suppression on unresolvable slot conflicts.
- **Files to Modify**: `src/clindoc/fhir/mapper.py`. *(Requires explicit user authorization)*.
- **Dependencies**: Sub-Milestone 4.1, Milestone 3.
- **Acceptance Criteria**:
  - Split dosing (e.g. morning 25 mg, night 50 mg) generates a single `MedicationRequest` with `len(dosageInstruction) == 2`.
  - Conflicting doses within the same timing slot without clinician confirmation trigger `EXT_COMPETING_CANDIDATE` and resource suppression.
  - All existing Phase 4 mapping tests (16/16) continue to pass without regression.
- **Regression Suite**: `tests/test_canonical_fhir_mapping.py`, `tests/test_fhir_export.py`.

### Sub-Milestone 4.3: Mode 0 Review Transition Service & Audit Provenance (PD-011)
- **Objective**: Implement the formal Mode 0 clinician review transition API and verify that exported bundles comply with PD-011 provenance minimization.
- **Files to Modify**: `src/clindoc/services/fhir_export.py`, `src/clindoc/core/contracts.py`.
- **Dependencies**: Sub-Milestones 4.1, 4.2.
- **Acceptance Criteria**:
  - `ReviewService.confirm_provisional_association()` securely transitions claims and records reviewer ID + timestamp in the audit trail.
  - Confirmed claims pass Gate 9; unconfirmed provisional claims remain strictly suppressed.
  - FHIR provenance extensions do not contain unredacted raw text (PD-011 compliance).
- **Regression Suite**: `tests/test_phase511_export_safety.py`, `tests/test_phase43_provenance_traceability.py`.

### Sub-Milestone 4.4: End-to-End Pipeline Integration & Benchmark Validation
- **Objective**: Validate the entire integrated pipeline from unsegmented clinical narratives to validated FHIR R4B bundles across the benchmark suites.
- **Files to Create/Modify**: `tests/test_phase523_milestone4_end_to_end.py`.
- **Dependencies**: Sub-Milestones 4.1–4.3.
- **Acceptance Criteria**:
  - 100% pass on 20-document narrative E3 pipeline test.
  - Zero cross-medication attribute leakage.
  - All 1,411 baseline tests pass (0 new regressions).
- **Regression Suite**: Full repository test suite (`pytest -q`).

---

## 9. Acceptance Criteria and Adversarial Test Catalog

For Milestone 4 validation, the following test matrix must be implemented:

| Test ID | Test Scenario | Input Narrative | Expected Behavior | Safety Invariant Tested |
| :--- | :--- | :--- | :--- | :--- |
| **ADV-M4-01** | Split-dose regimen mapping | *"Carvedilol 6.25 mg in morning and 12.5 mg at bedtime."* | Single `MedicationRequest` with 2 `dosageInstruction` entries; each links to exact timing offset. | Multi-dosage mapping fidelity. |
| **ADV-M4-02** | Unconfirmed `respectively` export | *"Metoprolol and Lisinopril were prescribed 25 mg and 5 mg respectively."* | Gate 9 suppresses both resources (`SUPPRESSED_UNCONFIRMED_PROVISIONAL`). | Mode 0 human review requirement. |
| **ADV-M4-03** | Confirmed `respectively` export | Same as above + Mode 0 Clinician Confirmation. | Emits 2 `MedicationRequest` resources; Metoprolol has 25 mg, Lisinopril has 5 mg. | Review state transition validity. |
| **ADV-M4-04** | Unverified drug name anchor | Drug name fails vocabulary/regex check; dosage is valid. | Cascade rejects dosage with `PARENT_ANCHOR_UNVERIFIED`. Gate 9 suppresses entire group. | Anchor propagation safety. |
| **ADV-M4-05** | Within-clause dose adjustment conflict | *"Lisinopril 10 mg increased to 20 mg daily."* (Unconfirmed). | Rule 6 ambiguity flags both doses; Gate 9 suppresses group. | Slot conflict fail-closed behavior. |
| **ADV-M4-06** | Confirmed dose adjustment export | Same as above + Clinician resolves to active dose (20 mg). | Single `MedicationRequest` emitted with 20 mg active; prior dose recorded in audit log. | Clinician conflict resolution. |
| **ADV-M4-07** | Complex nested parenthetical (W-1) | *"Metoprolol (previously 25 mg, stopped) 50 mg daily."* | Ambiguity flag set; fail closed; Gate 9 suppresses. | Warning W-1 enforcement. |
| **ADV-M4-08** | Multi-page order spanning (W-2) | Drug name on Page 1, dosage on Page 2 without link. | Orphan dosage; Gate 9 suppresses. | Warning W-2 enforcement. |
| **ADV-M4-09** | PD-011 Provenance Minimization | Mapped resource with DEC-016 provenance. | Bundled `Provenance` contains claim IDs and targets; contains ZERO unredacted PHI text. | PD-011 minimization compliance. |
| **ADV-M4-10** | Contaminated lab crosstalk guard | *"Glucose 110 mg/dL, Metformin 500 mg daily."* | Lab Observation emitted; MedicationRequest emitted; zero crosstalk between units/values. | Domain isolation. |

---

## 10. Benchmark and Validation Plan

### 10.1 Distinct Benchmark Scopes & Denominators

To ensure scientific rigor and avoid cross-corpus contamination, validation metrics must maintain strictly separate denominators:

```
┌───────────────────────────────────────────────────────────────────────────┐
│                      BENCHMARK EVALUATION SCOPES                         │
├───────────────────────────────────────────────────────────────────────────┤
│ 1. 20-Document Narrative Corpus (E3 Scope):                               │
│    - Denominator: 20 unsegmented complex narrative documents             │
│    - Primary Metrics: End-to-end pipeline completion, zero unhandled      │
│      exceptions, zero cross-medication attribute leakage.                 │
│                                                                           │
│ 2. 120-Document GLiNER Evaluation Scope:                                  │
│    - Denominator: 120 standard clinical notes                             │
│    - Primary Metrics: Token-level and entity-level Precision, Recall, F1. │
│                                                                           │
│ 3. 34-Document Out-of-Distribution (OOD) Scope:                           │
│    - Denominator: 34 unseen clinical discharge summaries                  │
│    - Primary Metrics: Fail-closed rate on novel formatting, zero          │
│      hallucinated associations.                                           │
│                                                                           │
│ 4. Dedicated Adversarial Test Suite (Milestone 3/4 Scope):                 │
│    - Denominator: 36 edge cases (26 M3 + 10 M4 adversarial fixtures)     │
│    - Target: 100% pass rate (36/36). Zero false unquarantines.            │
└───────────────────────────────────────────────────────────────────────────┘
```

### 10.2 Genuinely Measurable Metrics vs. Unmeasurable Metrics

| Metric | Genuinely Measurable? | Methodology / Ground Truth |
| :--- | :--- | :--- |
| **Cross-Medication Attribute Leakage Rate** | **YES** | Exact character offset provenance comparison against ground-truth group boundaries. Target: **0.0%**. |
| **Gate 9 Suppression Accuracy** | **YES** | Deterministic verification that 100% of unconfirmed provisional / ambiguous claims are suppressed. Target: **100.0%**. |
| **FHIR R4B Conformance** | **YES** | Official HL7 FHIR validator schema validation of emitted bundles. Target: **100.0%**. |
| **End-to-End Pipeline Latency** | **YES** | P50, P95, P99 execution time per document across E3 corpus. |
| **Clinical Diagnostic Efficacy** | **NO** | Cannot be measured without prospective clinical trials. No synthetic or retrospective metric can claim clinical diagnostic efficacy. |
| **Unseen Out-of-Vocabulary Coverage** | **NO** | Cannot claim universal clinical coverage; bounded strictly by the evaluated dictionaries and test corpora. |

---

## 11. Unresolved Questions Requiring User Authorization

Before any production code for Milestone 4 is written, the following four architectural decisions require explicit user authorization:

### Decision Question 1: Authorization to Modify `src/clindoc/fhir/mapper.py`
- **Context**: `src/clindoc/fhir/mapper.py` was frozen in Phase 4. Currently, line 2105 enforces a single-candidate slot bottleneck that drops valid split-dose regimens into `EXT_COMPETING_CANDIDATE`.
- **Options**:
  - **Option 1A (Recommended)**: Authorize modifying `mapper.py` to support multi-entry `dosageInstruction` arrays for confirmed split-dose regimens with distinct timing, while maintaining 100% backwards compatibility with Phase 4 unit tests.
  - **Option 1B**: Keep `mapper.py` strictly frozen. Force `FHIRExportService` to generate separate virtual medication groups for split regimens (architecturally inferior and splits orders into multiple resources).
- **Recommendation**: **Approve Option 1A**.

### Decision Question 2: Scope of Verification Cascade (`stage_a.py`)
- **Context**: Should anchor verification failure propagation be embedded directly into `src/clindoc/verify/stage_a.py` or placed in a new post-verification filter module `src/clindoc/verify/cascade.py`?
- **Options**:
  - **Option 2A (Recommended)**: Implement `verify_medication_group_integrity` as Pass 2 in `src/clindoc/verify/stage_a.py` so all Stage A consumers receive topologically consistent claims.
  - **Option 2B**: Implement as an isolated filter in `src/clindoc/services/fhir_export.py` immediately prior to Gate 9.
- **Recommendation**: **Approve Option 2A**.

### Decision Question 3: Mode 0 Review Confirmation Storage & Persistence
- **Context**: Where should clinician review decisions (`review_state="CONFIRMED"`, clinician ID, timestamp, rationale) be persisted?
- **Options**:
  - **Option 3A (Recommended)**: Persist in the `AuditTrail` log and update the in-memory `Claim.review_state` during pipeline execution, keeping the file system strictly read-only for input documents.
  - **Option 3B**: Introduce an external database or sidecar persistence file (e.g. SQLite / JSON store) for review records.
- **Recommendation**: **Approve Option 3A** (fits current stateless microservice architecture without introducing database dependencies).

### Decision Question 4: Handling of Warnings W-1 and W-2 in Milestone 4
- **Context**: Milestone 3.1 documented Warning W-1 (complex parentheticals) and Warning W-2 (multi-page orders).
- **Options**:
  - **Option 4A (Recommended)**: Keep both W-1 and W-2 strictly fail-closed in Milestone 4. Do NOT attempt heuristic auto-resolution. Any occurrence triggers Gate 9 quarantine.
  - **Option 4B**: Attempt experimental regex heuristic resolution for parentheticals.
- **Recommendation**: **Approve Option 4A**. Do not compromise safety with heuristic shortcuts.

---

## 12. Explicit List of Files That Must Not Be Modified

To protect established invariants, the following files are designated as **STRICTLY FROZEN** during Milestone 4 implementation:

```
=============================================================================
                  FROZEN MODULES — DO NOT MODIFY IN MILESTONE 4
=============================================================================
1.  src/clindoc/extract/medication_association.py  (Layer C Engine is complete & verified)
2.  src/clindoc/extract/segmentation.py            (Sentence/clause segmenter is verified)
3.  src/clindoc/extract/deterministic.py           (Grounding invariants are verified)
4.  src/clindoc/ocr/born_digital.py               (Ingestion geometry is verified)
5.  src/clindoc/ocr/layout.py                     (Layout engine is verified)
6.  configs/extraction.yaml                       (Permitted fields DEC-020 frozen)
7.  configs/loinc_subset.json                     (Vocabulary frozen)
8.  configs/ucum_subset.json                      (Vocabulary frozen)
9.  experiments/E3-narrative-extraction/           (Benchmark data & results frozen)
10. tests/test_phase523_milestone*.py             (All prior milestone tests frozen)
=============================================================================
```

---

## 13. Assessment Conclusion & Stop Condition

This technical assessment establishes that Milestone 4 is necessary, well-bounded, and architecturally feasible. By addressing the downstream FHIR mapper bottleneck, aligning the verification cascade, and formalizing the Mode 0 review interface, Milestone 4 will complete the Phase 5.2.3 roadmap while preserving all safety invariants.

**Execution is halted in compliance with the STRICTLY READ-ONLY mandate. No production code, tests, or configuration files have been modified. Implementation of Milestone 4 will begin only upon explicit user authorization and selection of the proposed decision options.**
