# ClinicalDoc Phase 5.2.3 — Sub-Milestone 4.4: REQ-44 Implementation Readiness Assessment

**Document Authority:** Phase 5.2.3 Sub-Milestone 4.4 Technical Governance  
**Assessment Date:** 2026-10-31  
**Operating Mode:** STRICTLY READ-ONLY ASSESSMENT (Zero modifications to production code, tests, schemas, constants, configurations, or Git state)  
**Deliverable Target:** `REQ44_IMPLEMENTATION_READINESS_ASSESSMENT.md`  

---

## 1. Executive Verdict & Readiness Summary

### **Executive Verdict: READY WITH CONDITIONS**

The REQ-44 Remediation Design Review (v3.1) represents a mathematically rigorous, clinically sound, and fail-closed architectural specification for multi-phase medication regimens, semantic duration ownership, and FHIR R4B lifecycle mapping. 

The repository architecture provides clear, well-isolated extension points in [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py) (`_build_regimen_dosages`, `claims_to_fhir_bundle`, `map_medication_request`, `map_medication_statement`) that permit implementation without breaking existing schema contracts.

However, implementation **must not proceed unconditionally**. The actual codebase currently exhibits:
1. **Critical Behavioral Conflicts**: Current production code directly contradicts v3.1 by mapping historical medication requests to `status="completed"`, automatically falling back from prescription cancellations to `MedicationStatement(status="stopped")`, and using forward-only windowing that misattributes leading durations.
2. **Substantial Implementation Gaps**: Zero semantic role classification exists in the extraction or mapping pipelines; transition-based clause partitioning is completely absent; 11 out of the 13 required v3.1 safety test cases have no test coverage.
3. **Architectural Pipeline Boundary Decision**: The pipeline boundary where semantic role filtering executes (mapper-level evidence span inspection vs. extractor-level claim classification) must be formalized before touching production code.
4. **Governance Export Blocker**: The multi-hop Four-Eyes ancestry vulnerability in frozen [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py) remains an active clinical export blocker under Gate 9.

---

## 2. Requirement-by-Requirement Implementation Status

This section provides an itemized comparison between the v3.1 specification ([`phase523_milestone44_req44_remediation_design_review_v3_1.md`](file:///d:/daa/phase523_milestone44_req44_remediation_design_review_v3_1.md)) and the current codebase.

### Status Matrix

| v3.1 Requirement & Section | Current Repository Status | Primary Code Reference | Severity of Gap / Conflict |
| :--- | :---: | :--- | :---: |
| **REQ-44-A1: Semantic Role Grounding** (v3.1 §2.1, §2.2) | **MISSING** | [`src/clindoc/fhir/mapper.py:1845-1855`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1845-L1855)<br>[`src/clindoc/extract/deterministic.py:844-898`](file:///d:/daa/src/clindoc/extract/deterministic.py#L844-L898) | **HIGH** |
| **REQ-44-A2: Follow-up & Monitoring Exclusion** (v3.1 §2.2) | **MISSING** | [`src/clindoc/fhir/mapper.py:1845-1855`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1845-L1855) | **HIGH** (Clinical Hazard) |
| **REQ-44-A3: Non-Med History Duration Separation** (v3.1 §2.2, §2.5, §4) | **MISSING** | [`src/clindoc/fhir/mapper.py:2731-2740`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2731-L2740)<br>[`src/clindoc/extract/medication_association.py:116-120`](file:///d:/daa/src/clindoc/extract/medication_association.py#L116-L120) | **MEDIUM** |
| **REQ-44-A4: OCR Paragraph & Line Wrap Handling** (v3.1 §2.3) | **MISSING** | [`src/clindoc/fhir/mapper.py:1802-1819`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1802-L1819) | **MEDIUM** |
| **REQ-44-A5: Transition-Grounded Clause Partitioning** (v3.1 §2.4) | **MISSING** (Defect in Prod) | [`src/clindoc/fhir/mapper.py:1821-1855`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1821-L1855) | **HIGH** (Causes Dropped/Shifted Durs) |
| **REQ-44-A6: Multi-Duration Fail-Closed (Case B)** (v3.1 §2.5) | **MISSING** (Defect in Prod) | [`src/clindoc/fhir/mapper.py:1851`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1851) (`cand_durs[0]`) | **HIGH** (Picks arbitrarily) |
| **REQ-44-B1: Decoupling Cancellation from Cessation** (v3.1 §3.1, §3.4) | **CONFLICTING** | [`src/clindoc/fhir/mapper.py:2081, 2139`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2081)<br>[`src/clindoc/extract/medication_association.py:106`](file:///d:/daa/src/clindoc/extract/medication_association.py#L106) | **CRITICAL** (Semantic Violation) |
| **REQ-44-B2: Completed Course Syntactic Grounding** (v3.1 §3.1, §3.4) | **CONFLICTING / MISSING** | [`src/clindoc/fhir/mapper.py:2083`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2083) | **CRITICAL** (Semantic Violation) |
| **REQ-44-B3: Historical Mention Mapping** (v3.1 §3.1, §3.4) | **CONFLICTING in MedReq**<br>**IMPLEMENTED in MedStat** | [`src/clindoc/fhir/mapper.py:2083`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2083) (`MedReq.status="completed"`)<br>[`src/clindoc/fhir/mapper.py:2141`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2141) (`MedStat.status="unknown"`) | **HIGH** |
| **REQ-44-C1: Split Dosing Preservation** (v3.1 §2.6) | **IMPLEMENTED** | [`src/clindoc/fhir/mapper.py:1884-1999`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1884-L1999) | **VERIFIED** |
| **REQ-44-C2: Competing Candidates Preservation** (v3.1 §2.6) | **IMPLEMENTED** | [`src/clindoc/fhir/mapper.py:2001-2049`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2001-L2049) | **VERIFIED** |
| **REQ-44-C3: Unrelated Medication Separation** (v3.1 §2.6) | **IMPLEMENTED** | [`src/clindoc/fhir/mapper.py:2697-2715`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2697-L2715) | **VERIFIED** |
| **REQ-44-C4: Four-Eyes Blocker Preservation** (v3.1 §6.1) | **IMPLEMENTED** | [`src/clindoc/services/fhir_export.py:488-530`](file:///d:/daa/src/clindoc/services/fhir_export.py#L488-L530) | **VERIFIED** |

---

### Deep-Dive Analysis of Gaps and Conflicts

#### 1. REQ-44-A1 & A2: Duration Extraction and Semantic Role Classification
- **Current Behavior in Code**:
  - [`src/clindoc/extract/deterministic.py:844-898`](file:///d:/daa/src/clindoc/extract/deterministic.py#L844-L898) extracts durations via regexes (`_DURATION_EXPLICIT_DIGIT`, `_DURATION_EXPLICIT_WRITTEN`).
  - Extracted claims receive `field_type = "med.duration"` regardless of whether the governing verb is `"take"`, `"review"`, `"check"`, or `"history"`.
  - In [`src/clindoc/fhir/mapper.py:1845-1855`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1845-L1855), `_build_regimen_dosages` blindly pairs any duration claim located in the forward window `[d_pos, next_d_pos)` or falls back to index parity `len(sorted_durs) == len(sorted_doses)`.
- **Conflict with v3.1**:
  - When given `"Take 40 mg daily for 2 weeks, then increase to 60 mg daily. Review after 1 week."`, the current code extracts `"1 week"` as `med.duration` and assigns `boundsDuration = 1.0 wk` to Phase 2 (60 mg), cutting off chronic therapy.
  - v3.1 mandates that `"Review after 1 week"` be classified as `ROLE_FOLLOWUP_REVIEW` and excluded from `Dosage.timing.repeat.boundsDuration`.

#### 2. REQ-44-A5 & A6: Clause Partitioning & Leading Duration Failure
- **Current Behavior in Code**:
  - Phase windowing in [`src/clindoc/fhir/mapper.py:1848-1850`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1848-L1850) is strictly `d_pos <= dur_pos < next_d_pos`.
  - If a duration precedes the dose (e.g. `"For 2 weeks take 40 mg daily..."`), `dur_pos < d_pos`, causing the duration to be ignored for Phase 1.
  - In mixed placement (`"40 mg daily for 2 weeks, then for 2 weeks take 20 mg daily"`), both durations fall into Phase 1's window `[pos(40mg), pos(20mg))`. Line 1851 binds `cand_durs[0]` to Phase 1, leaving Phase 2 empty.
  - Line 1853 then checks `elif len(sorted_durs) == len(sorted_doses): phase_dur = sorted_durs[idx]`, masking some failures by index parity, but index parity fails whenever duration counts and dose counts differ.
- **Conflict with v3.1**:
  - v3.1 requires partitioning text into intervals `[L_k, R_k)` defined by transition tokens (`"then"`, `"followed by"`).
  - v3.1 mandates that if `len(cand_durs) > 1` within a phase clause, the phase must **fail closed** (`boundsDuration = None`) with `AMBIGUOUS_MULTI_DURATION_CONFLICT`. Currently, line 1851 silently takes the first candidate.

#### 3. REQ-44-B1: Decoupling Prescription Cancellation from Patient Cessation
- **Current Behavior in Code**:
  - [`src/clindoc/extract/medication_association.py:60-63`](file:///d:/daa/src/clindoc/extract/medication_association.py#L60-L63) groups `"discontinue"`, `"stopped"`, `"ceased"`, `"d/c"` into a single regex `RE_DISCONTINUED`.
  - Any match sets `temporal_status = "discontinued"`.
  - In [`src/clindoc/fhir/mapper.py:2080-2081`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2080-L2081) (`map_medication_request`):
    ```python
    if temporal_status == "discontinued":
        req_status = "stopped"
    ```
  - In [`src/clindoc/fhir/mapper.py:2138-2139`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2138-L2139) (`map_medication_statement`):
    ```python
    if temporal_status == "discontinued":
        stat_status = "stopped"
    ```
- **Conflict with v3.1**:
  - If a progress note narrative contains `"Discontinue Prednisone 50 mg"`, mapper generates `MedicationStatement(status="stopped")`.
  - v3.1 §3.1 & §3.4 explicitly forbid this: prescriber cancellation intent does NOT constitute evidence of patient cessation. A stopped statement requires explicit evidence that the patient stopped taking the drug (`"patient stopped taking"`). Unreferenced cancellation directives must fail closed (`UNREFERENCED_ORDER_CANCELLATION_FAIL_CLOSED`), emitting no statement.
  - Furthermore, in `map_medication_request`, setting `status = "stopped"` fails to verify whether a prior order reference exists to authorize `status = "cancelled"`.

#### 4. REQ-44-B2 & B3: Historical Mentions and Completed Course Mapping
- **Current Behavior in Code**:
  - In [`src/clindoc/fhir/mapper.py:2082-2083`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2082-L2083):
    ```python
    elif temporal_status == "historical":
        req_status = "completed"
    ```
  - In [`src/clindoc/fhir/mapper.py:2140-2141`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2140-L2141):
    ```python
    elif temporal_status == "historical":
        stat_status = "unknown"
    ```
- **Conflict with v3.1**:
  - Mapping `historical` to `MedicationRequest(status="completed")` directly violates v3.1 Section 3.1: *"Historical mention must never be mapped to status='completed'. In medical documentation, historical use does not verify that a prescribed therapy course was completed."*
  - In `MedicationStatement`, there is currently zero logic to detect completed courses or emit `stat_status = "completed"`. It only maps `discontinued` -> `stopped`, `historical` -> `unknown`, `planned` -> `intended`, and default -> `active`.

---

## 3. Existing Test Coverage vs. Missing Regression Tests

### Coverage Evaluation of v3.1 Mandated Test Cases

v3.1 Section 5 specifies 13 mandatory regression test cases. Below is the verification of test implementation in [`tests/test_phase523_milestone4_4.py`](file:///d:/daa/tests/test_phase523_milestone4_4.py) and across the repository:

| Test Case Specified in v3.1 Section 5 | Implemented in Repository? | Actual Test Function in Repo | Evaluation & Evidence |
| :--- | :---: | :--- | :--- |
| `test_req44_duration_vs_followup_review_interval` | **NO** | None | **Missing**. No test checks `"Review after 1 week"` vs regimen duration. |
| `test_req44_duration_vs_monitoring_reassessment` | **NO** | None | **Missing**. No test checks lab monitoring duration exclusion. |
| `test_req44_paragraph_break_without_transition_fails_closed` | **NO** | None | **Missing**. No test checks `\n\n` without transition tokens. |
| `test_req44_ocr_line_wrap_with_explicit_transition` | **NO** | None | **Missing**. No test checks soft `\n` normalization within clauses. |
| `test_req44_taxonomy_case_a_orphan_medication_duration` | **PARTIAL** | `test_w2_multipage_medication_split_fail_closed` | Partially covered for cross-page `__no_anchor__`, but missing intra-group orphan duration suppression. |
| `test_req44_taxonomy_case_b_multiple_regimen_durations` | **NO** | None | **Missing**. Current mapper selects `cand_durs[0]` instead of failing closed. |
| `test_req44_taxonomy_case_c_non_regimen_activity` | **NO** | None | **Missing**. No test asserts exclusion of follow-up from `Dosage.timing`. |
| `test_req44_taxonomy_case_d_symptom_history_duration` | **NO** | None | **Missing**. No test verifies Scenario 16 non-medication history preservation. |
| `test_req44_lifecycle_unreferenced_cancellation_fails_closed` | **NO** | None | **Missing**. Current mapper does the opposite (emits `status="stopped"`). |
| `test_req44_lifecycle_patient_cessation_emits_stopped` | **NO** | None | **Missing**. No test evaluates grounded patient cessation language. |
| `test_req44_lifecycle_historical_maps_to_unknown_not_completed` | **NO** | None | **Missing**. Current `MedReq` emits `completed`, violating this rule. |
| `test_req44_lifecycle_completed_requires_syntactic_linkage` | **NO** | None | **Missing**. No test evaluates detached completion keyword isolation. |
| `test_req44_split_dosing_preserved_concurrent` | **YES** | `test_split_dosing_e2e_bundle_and_fhir_validator` | **Verified**. Verifies concurrent `sequence: 1` instructions. |

### Current Test Suite in `tests/test_phase523_milestone4_4.py`:
The existing milestone 4.4 test file contains exactly 7 tests:
1. `test_w2_multipage_medication_split_fail_closed`: Passes (covers W-2 orphan suppression).
2. `test_req_44_03_shared_evidence_duplicate_duration_matching`: Passes (covers trailing repeated durations).
3. `test_req_44_04_narrative_sig_transition_context_sufficiency`: Passes (covers SIG `"Then "` prefix).
4. `test_split_dosing_e2e_bundle_and_fhir_validator`: Passes (covers split dosing `sequence: 1`).
5. `test_sequential_taper_medication_statement_e2e_validator`: Passes (covers simple 2-phase taper).
6. `test_competing_candidate_shadowing_provenance_emitted`: Passes (covers `EXT_COMPETING_CANDIDATE`).
7. `test_four_eyes_ancestry_blocker_remains_active`: Passes (covers Gate 9 export service blocking).

**Summary**: 11 of the 13 required v3.1 safety test scenarios are currently **completely untested** in the test suite.

---

## 4. Remaining Design Ambiguities & Technical Contradictions

During inspection of the repository against v3.1, three specific technical risks and ambiguities were identified:

### Risk 1: Execution Layer Boundary for Semantic Role Classification
- **Ambiguity**: v3.1 defines semantic roles (`ROLE_REGIMEN_DURATION`, `ROLE_FOLLOWUP_REVIEW`, `ROLE_MONITORING_REASSESSMENT`, `ROLE_NON_MED_CLINICAL_HISTORY`). However, it does not specify whether classification happens:
  1. In `clindoc.extract` (e.g. modifying `extract_med_duration` or `medication_association.py` to tag claims with `semantic_role`), OR
  2. Inside `_build_regimen_dosages` in `clindoc.fhir.mapper` by inspecting evidence text spans associated with each duration claim.
- **Impact**:
  - If handled in `clindoc.extract`, `Claim` contract schema or `metadata` must support role tagging, and downstream consumers may be affected.
  - If handled in `mapper.py`, `_build_regimen_dosages` must perform local regex/syntax checks against `evidence_map[dur_claim.evidence_id].text`. This keeps changes localized to the mapper and leaves `clindoc.extract` contracts intact.
- **Resolution Recommendation**: Execute semantic role filtering **within `mapper.py`** during phase candidate selection. The mapper already receives `evidence_map` and performs string offset lookups. This prevents churn across extraction contracts.

### Risk 2: FHIR R4B `MedicationRequest.status` Permitted Value Set
- **Technical Contradiction**:
  - In `mapper.py:2081`, `temporal_status == "discontinued"` sets `req_status = "stopped"`.
  - In v3.1 §3.1, cancellation directives for prescriptions map to `MedicationRequest(status="cancelled")` when an order reference exists, and fail closed when unreferenced.
  - FHIR R4B `MedicationRequest.status` allows `cancelled` and `stopped`. However, in clinical practice:
    * `cancelled` = order was voided or cancelled before completion/dispensing.
    * `stopped` = therapy was actively halted by prescriber order during treatment.
  - Current code never emits `status="cancelled"`. It only emits `"stopped"`.
- **Resolution Recommendation**: Implement the v3.1 rule in `map_medication_request`: if explicit order cancellation with order ID exists $\implies$ `status="cancelled"`. If unreferenced prescriber discontinuation $\implies$ fail closed (suppress with audit ledger note). Never emit `MedicationStatement(status="stopped")` as a fallback.

### Risk 3: Verbatim SIG Narrative Assembly
- **Ambiguity**: v3.1 requires preserving verbatim narrative `Dosage.text` from the clause.
- **Current Code**: `_build_regimen_dosages` lines 1858-1871 synthesizes text:
  ```python
  parts = []
  if idx == 0 and name_claim:
      parts.append(name_claim.value)
  elif idx > 0:
      parts.append("Then")
  parts.append(f"{d_claim.value} {d_claim.unit_raw or ''}".strip())
  ...
  p_text = " ".join(parts).strip()
  ```
- **Impact**: Synthetic generation (`"Then 20 mg daily for 2 weeks"`) loses original clinical phrasing, qualifiers (e.g. `"with food"`, `"by mouth"`), and follow-up sentences.
- **Resolution Recommendation**: Slice verbatim clause text `full_grp_text[L_k:R_k].strip()` for `Dosage.text`, prefixing `"Then "` on phases $>0$ only if missing from the source span.

---

## 5. Preservation Verification of Existing Regimen & Safety Features

Inspection of the codebase confirms that implementing v3.1 will NOT adversely impact existing core capabilities:

1. **Split Dosing Preservation**:
   - `_build_regimen_dosages` lines 1884–1999 cleanly branches on `has_split_cues` (e.g. `MORNING_REGEX` and `BEDTIME_REGEX`).
   - Split dosing emits concurrent `sequence: 1` instructions with element-level provenance.
   - Preserved intact because `has_taper_cues` and `has_split_cues` are mutually gated, and taper transition logic only triggers when sequential markers exist.
2. **Competing Candidate Shadowing (`EXT_COMPETING_CANDIDATE`)**:
   - `select_deterministic_slot_candidate` (lines 1761–1766 and 2001–2010) deterministically ranks multiple claims competing for the same slot.
   - Non-winning candidates are exported via `EXT_COMPETING_CANDIDATE` extensions.
   - Preserved intact.
3. **Cross-Medication and Cross-Analyte Contamination Gates**:
   - Lines 2697–2715 suppress medication groups containing conflicting drug names (`distinct_names > 1`).
   - Lines 2334–2352 suppress lab groups containing conflicting test names.
   - Preserved intact.
4. **Four-Eyes Security Export Blocker**:
   - [`src/clindoc/services/fhir_export.py:488-530`](file:///d:/daa/src/clindoc/services/fhir_export.py#L488-L530) strictly enforces Gate 9 audit ledger verification.
   - Neither `fhir_export.py` nor `review/service.py` require any edits for REQ-44.
   - The multi-hop ancestry vulnerability in `review/service.py:838-851` remains unchanged and continues to block unverified export.

---

## 6. Implementation Dependencies and Staged Execution Order

When authorized, implementation must follow this strict, staged dependency sequence:

```
[Phase 1: Mapper Regex & Role Classifiers]
  ├── Define ROLE_REGIMEN, ROLE_FOLLOWUP, ROLE_MONITORING, ROLE_NON_MED_HISTORY patterns
  └── Update TAPER_TRANSITION_REGEX in src/clindoc/fhir/constants.py or mapper.py
                                │
                                ▼
[Phase 2: Transition-Grounded Clause Partitioning in mapper.py]
  ├── Implement [L_k, R_k) boundary detection on transition tokens
  ├── Implement Rule 2: paragraph breaks (\n\n) without transitions fail closed
  ├── Implement Phase candidate filtering with semantic role exclusion
  └── Implement Case B fail-closed: len(cand_durs) > 1 -> boundsDuration = None
                                │
                                ▼
[Phase 3: Lifecycle Mapping Grounding in mapper.py]
  ├── Fix map_medication_request: historical -> status="unknown" (never "completed")
  ├── Fix map_medication_request: cancellation requires order ref; else fail closed
  └── Fix map_medication_statement: remove auto-fallback from cancellation to "stopped"
                                │
                                ▼
[Phase 4: Expanded Test Suite in tests/test_phase523_milestone4_4.py]
  ├── Implement all 11 missing test cases from v3.1 Section 5
  └── Re-verify existing 7 tests continue to pass
                                │
                                ▼
[Phase 5: Regression & Benchmark Invariance Verification]
  ├── Full test suite execution: confirm 1,454+ passed, exactly 5 pre-existing failures
  └── Benchmark manifests: evaluate 12-doc and 60-doc cascade baselines (0 drift)
```

---

## 7. Explicit Blockers Requiring Resolution Before Implementation

Before implementation commences, the following two preconditions must be explicitly recorded:

1. **Authorization Precondition**:
   - Implementation is currently prohibited under strict pair programming guidelines. Explicit user instruction stating *"Proceed with implementation of v3.1"* is required.
2. **Review & Export Service Freeze Precondition**:
   - [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py) and [`src/clindoc/services/fhir_export.py`](file:///d:/daa/src/clindoc/services/fhir_export.py) must remain completely frozen. Four-Eyes ancestry remediation is out of scope for REQ-44 and must remain an active blocker.

---

## 8. Recommended Next Milestone

- **Next Milestone**: **Sub-Milestone 4.4.1 — REQ-44 Remediation Implementation & Verification**.
- **Scope**:
  1. Apply minimal, surgical edits to [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py) implementing transition-grounded clause partitioning, semantic role filtering, and lifecycle decoupling.
  2. Implement the 11 missing test cases in [`tests/test_phase523_milestone4_4.py`](file:///d:/daa/tests/test_phase523_milestone4_4.py).
  3. Execute repository baseline and benchmark verification to guarantee zero denominator drift.
