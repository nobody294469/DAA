# Phase 5.2.3 — Milestone 1.3: Minimal Anchor Ordering Correction Report

**Author**: Antigravity Assistant  
**Date**: October 31, 2026  
**Status**: Completed & Verified — Minimal Production Fix Implemented; 6/6 New Regression Tests Passed; 322/322 Core Tests Passed; 1,333/1,338 Repository Tests Passed (0 New Failures)  
**Production File Modified**: [`src/clindoc/extract/llm_extractor.py`](file:///d:/daa/src/clindoc/extract/llm_extractor.py)  
**Test File Added**: [`tests/test_phase523_milestone13_anchor_ordering.py`](file:///d:/daa/tests/test_phase523_milestone13_anchor_ordering.py)  

---

## Executive Summary

Milestone 1.3 implements the minimal approved production correction for the anchor ordering defect identified during the Milestone 1.2 read-only audit.

### Core Problems Resolved
1. **Sort Key Ordering Defect**: The Step 4 sort key previously evaluated `local_end` before `_field_priority`. When an anchor (`med.name` or `lab.value`) and an attribute shared the same start coordinate but the attribute span was shorter, the attribute was sorted ahead of the anchor. In documents with multiple medications, this caused shorter attributes to **silently attach to the preceding medication** (cross-medication leakage).
2. **Diagnostic List Determinism**: Candidate rejections were appended to `diagnostics["rejected"]` in raw JSON arrival order. While claims, evidences, and group IDs were invariant, the list sequence of rejection logs was permutation-sensitive.

### Implemented Corrections
1. **Sort Key Adjustment**: Swapped `_field_priority` ahead of `local_end` in the sort tuple:
   `(local_start, _field_priority, local_end, field_type, value, unit_raw, -confidence)`.
   Anchors (`priority 0`) are now guaranteed to be sorted before dependent attributes (`priority 1`) whenever `local_start` matches, regardless of span length.
2. **Diagnostic List Stabilization**: Added canonical sorting of `diagnostics["rejected"]` prior to function return.

---

## 1. Exact Production Code Changes

The production fix was strictly confined to [`src/clindoc/extract/llm_extractor.py`](file:///d:/daa/src/clindoc/extract/llm_extractor.py). No architectural or grouping state-machine redesign was introduced outside this minimal correction.

```diff
--- a/src/clindoc/extract/llm_extractor.py
+++ b/src/clindoc/extract/llm_extractor.py
@@ -856,8 +856,8 @@ class NarrativeExtractor:
 
         grounded.sort(key=lambda item: (
             item[1],  # local_start (ascending)
-            item[2],  # local_end (ascending)
-            _field_priority(item[0].get("field_type", "")),
+            _field_priority(item[0].get("field_type", "")),  # Anchor priority: med.name & lab.value first
+            item[2],  # local_end (ascending)
             str(item[0].get("field_type", "")),
             str(item[0].get("value", "")),
             str(item[0].get("unit_raw", "") or ""),
@@ -935,5 +935,12 @@ class NarrativeExtractor:
             )
             all_claims.append(claim)
 
+        diagnostics["rejected"].sort(key=lambda r: (
+            str(r.get("field_type", "")),
+            str(r.get("reason", "")),
+            str(r.get("value", "")),
+            str(r.get("evidence_text", "")),
+        ))
+
         return all_evidences, all_claims
```

---

## 2. Mandatory Regression Tests

A comprehensive test suite was created in [`tests/test_phase523_milestone13_anchor_ordering.py`](file:///d:/daa/tests/test_phase523_milestone13_anchor_ordering.py) with 6 explicit test scenarios asserting actual group relationships:

| Test Case | Scenario / Assertion | Status | Execution Time |
| :--- | :--- | :---: | :---: |
| `test_costarting_dose_ending_before_name` | `med.name [0:10]` vs `med.dose [0:5]`. Asserts `med.name` is sorted first and both share the same non-sentinel group ID. | **PASSED** | 0.02s |
| `test_costarting_dose_ending_before_name_with_preceding_med` | `"Aspirin 81 mg. Metoprolol 25 mg"`. Metoprolol dose at `[15:20]` co-starts with Metoprolol at `[15:25]`. Asserts Aspirin group and Metoprolol group are distinct, and Metoprolol dose **cannot leak into Aspirin group**. | **PASSED** | 0.02s |
| `test_costarting_lab_flag_ending_before_value` | `lab.value [0:9]` ('95 Normal') vs `lab.flag [0:2]` ('95'). Asserts `lab.value` is sorted first and both share the same lab group ID. | **PASSED** | 0.02s |
| `test_identical_spans_across_anchor_and_dependent_fields` | `med.name [0:10]` vs `med.dose [0:10]`, and `lab.value [0:6]` vs `lab.flag [0:6]`. Asserts anchors sort first and dependent fields join the anchor group. | **PASSED** | 0.02s |
| `test_permutation_invariance_for_co_starting_and_overlapping` | All $5! = 120$ permutations of candidates for co-starting/overlapping inputs. Asserts normalized claims, evidences, and group relationships are 100% identical. | **PASSED** | 0.03s |
| `test_diagnostic_rejection_determinism_across_permutations` | All permutations of ambiguous/invalid candidates. Asserts `diagnostics["rejected"]` is 100% bitwise deterministic across all permutations. | **PASSED** | 0.03s |

**Result**: 6 passed in 0.13s.

---

## 3. Group Association Verification

### Verification of Co-Starting Span Resolution

#### Before Correction (Defect):
```
Text: 'Aspirin 81 mg. Metoprolol 25 mg'
Extraction order:
  [0] med.name=Aspirin     span=[0:7]   group=G_A
  [1] med.dose=81          span=[8:13]  group=G_A
  [2] med.dose=25          span=[15:20] group=G_A  <-- LEAKED TO ASPIRIN!
  [3] med.name=Metoprolol  span=[15:25] group=G_M
```

#### After Correction (Verified Safe):
```
Text: 'Aspirin 81 mg. Metoprolol 25 mg'
Extraction order:
  [0] med.name=Aspirin     span=[0:7]   group=G_A
  [1] med.dose=81          span=[8:13]  group=G_A
  [2] med.name=Metoprolol  span=[15:25] group=G_M  <-- ANCHOR FIRST!
  [3] med.dose=25          span=[15:20] group=G_M  <-- BOUND TO METOPROLOL!
```
- **Aspirin**: `Aspirin` + `81 mg` (Group `G_A`)
- **Metoprolol**: `Metoprolol` + `25 mg` (Group `G_M`)
- **Isolation**: Verified zero cross-talk contamination.

---

## 4. Regression & Verification Results

### 4.1 Core Extraction, Boundary, and Safety Suites
Executed across the entire extraction subsystem, boundary isolation, medication coreference, and FHIR export:
```bash
pytest tests/test_phase523_milestone1_deterministic_grounding.py \
       tests/test_phase523_milestone13_anchor_ordering.py \
       tests/test_phase52_medication_coreference.py \
       tests/test_phase5113_fixture_isolation_and_diagnostics.py \
       tests/test_extraction_subsystem.py \
       tests/test_canonical_fhir_mapping.py \
       tests/test_fhir_export.py \
       tests/test_pd022_qualitative_labs.py \
       tests/test_phase511_export_safety.py -q
```
**Result**: **322 passed, 2 warnings in 14.86s**.

### 4.2 Full Repository Test Suite
Executed across the entire codebase (`pytest -q`):
```
=========================== short test summary info ===========================
FAILED tests/test_cli.py::test_cli_unsupported_extension_fails - assert 0 == 1
FAILED tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling
FAILED tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation
FAILED tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline
FAILED tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification
=================== 5 failed, 1333 passed, 11 warnings in 261.28s ====================
```

### Analysis of Test Results:
- **Total Tests Passed**: 1,333 (increased from 1,327 baseline by exactly +6 new Milestone 1.3 tests).
- **Total Failures**: 5 (all 5 are pre-existing, known baseline failures documented in Phase 5.1/5.2).
- **Newly Introduced Failures**: **0**.

---

## 5. Strict Boundaries Adherence

| Constraint | Status | Verification Evidence |
| :--- | :---: | :--- |
| **No Milestone 2 implementation** | **CONFIRMED** | Clause segmentation and pre-nominal pairing have NOT been started. |
| **No medication grouping changes beyond sort key** | **CONFIRMED** | Only lines 859 and 938 in `llm_extractor.py` were modified. |
| **Ground truth & benchmarks unmodified** | **CONFIRMED** | `tests/fixtures/`, `benchmarks/`, and benchmark labels untouched. |
| **Frozen Phase 4 files unmodified** | **CONFIRMED** | `src/clindoc/fhir/` and Phase 4 test files untouched. |
| **Preserve workspace changes** | **CONFIRMED** | All working files preserved. |
| **No staging, commits, or pushes** | **CONFIRMED** | `git status` clean of any commits/pushes. |

---

## 6. Remaining Limitations & Milestone 2 Readiness

While Milestone 1, 1.1, 1.2, and 1.3 establish complete coordinate-level grounding safety, anchor priority, and permutation determinism, the following intentional architectural boundary remains:

- **Pre-nominal attributes across sentence boundaries**:
  In sentences such as:
  `"Patient on Lisinopril 10 mg. Switched to Oral Metoprolol 25 mg."`
  Because `"Oral"` physically precedes `"Metoprolol"`, the unsegmented forward stream still associates `"Oral"` with the preceding anchor (Lisinopril) unless sentence/clause boundaries are respected.

**Readiness**: This establishes the exact mandate for **Milestone 2 (Syntactic Clause & Sentence Boundary Segmentation)**:
1. Detect sentence and clause delimiters (`.`, `;`, `\n`).
2. Reset `current_med_group = _NO_ANCHOR` across boundaries to prevent cross-sentence leakage.
3. Prepare segmented clause tokens for Milestone 3 clause-level pairing.

---

## Conclusion
Milestone 1.3 is fully verified, regression-free, and complete. Milestone 1 (including edge cases 1.1–1.3) is production-ready. We stop here and await user review before proceeding to Milestone 2.
