# Phase 5.2.3 — Milestone 2.4: Geometry Fail-Closed & Medication Dose Ambiguity Hardening Report

**Document Authority**: Phase 5.2.3 Milestone 2.4 Implementation & Audit Report  
**Target Milestone**: Phase 5.2.3 — Milestone 2.4 (Geometry Fail-Closed & Medication Dose Ambiguity Hardening)  
**Implementation Date**: October 31, 2026  
**Status**: **IMPLEMENTATION COMPLETE — ALL 12 ADVERSARIAL TESTS PASSED — ZERO REGRESSIONS**

---

## 1. Executive Summary

Milestone 2.4 was authorized following the Milestone 2.3 cross-domain anchor audit to resolve two critical safety concerns:
1. **Geometry Fail-Closed Enforcement**:
   Eliminate syntax-only fallthrough in vertical laboratory continuation (`is_vertical_lab_component_continuation`). Require that BOTH bounding boxes be present, valid, finite, and aligned before layout-aware continuation is permitted. Any missing, malformed, non-finite (NaN, Inf), inverted, or non-aligned geometry strictly fails closed, preserving the original newline boundary.
2. **Medication Dose Ambiguity Hardening**:
   Ensure that clinically incomplete or ambiguous medication doses (specifically bare numeric quantities like `Paracetamol\n500` lacking a unit of measure) associate with the parent medication entity, preserve source evidence verbatim without guessing or inventing units, and cannot be exported into clinical FHIR bundles without explicit human clinical review acceptance (Mode 0).

Both remediation objectives have been successfully implemented and verified with 12 new targeted adversarial tests (`tests/test_phase523_milestone24_geometry_dose_safety.py`). All 137 tests across Milestones 1 through 2.4 pass with zero regressions, and the full repository test suite executed cleanly with 1,380 passing tests (12 new passes, exactly matching the 5 historical baseline failures).

---

## 2. Problem Analysis & Implementation Details

### 2.1 Problem 1: Missing or Malformed Geometry

#### Root Cause
In Milestone 2.3, `is_vertical_lab_component_continuation()` executed spatial layout checks only if both bounding boxes were provided:
```python
# Milestone 2.3 Vulnerability (table_layout.py:162-177):
if prev_bbox is not None and curr_bbox is not None:
    ...
return True  # Syntax-only continuation fallthrough when bboxes were None!
```
When `prev_bbox` or `curr_bbox` was `None` (for instance, in plain-text processing with `page=None` or when an OCR token lacked geometric grounding), the function fell through and returned `True` based solely on regex heuristics (`_NUMERIC_VAL_RE`, `COMMON_CLINICAL_UNITS`, `_FLAG_RE`).

#### Implemented Correction
1. **Dedicated Bounding Box Validator (`_validate_and_extract_bbox`)**:
   Created `_validate_and_extract_bbox(bbox)` in `src/clindoc/ocr/table_layout.py`:
   - Validates all coordinate representations (`list`, `tuple`, `dict`, `BBox`, `BBoxDict`, `BBoxModel`).
   - Rejects non-finite values (`math.isnan`, `math.isinf`).
   - Rejects non-numeric types and booleans (`isinstance(x, bool)`).
   - Enforces geometric invariants: non-negative coordinates ($x_0 \ge 0, y_0 \ge 0$), non-inverted axes ($x_1 > x_0, y_1 > y_0$), positive area.
   - Returns sanitized `(x0, y0, x1, y1)` floats or `None`.
2. **Strict Fail-Closed Enforcement in `is_vertical_lab_component_continuation`**:
   ```python
   p_box = _validate_and_extract_bbox(prev_bbox)
   c_box = _validate_and_extract_bbox(curr_bbox)
   if p_box is None or c_box is None:
       return False  # Strict fail-closed: preserves newline cut
   ```
3. **Resolution-Adaptive Geometric Tolerances**:
   To support documents at different scan resolutions (72 DPI born-digital, 150 DPI standard scan, 300 DPI high-res scan) without hardcoding fixed pixel thresholds, tolerances scale adaptively with the text height of the preceding line ($prev\_h = py_1 - py_0$):
   ```python
   effective_x_tol = max(x_tolerance, 1.0 * prev_h)
   effective_max_y = max(max_y_gap, 2.5 * prev_h)
   ```
4. **Whitespace-Robust Token Bounding Box Resolution in Segmentation**:
   In `src/clindoc/extract/segmentation.py`, added `_resolve_line_bbox()`. When text contains irregular whitespace (tabs, multiple newlines) that cause character offsets to drift from `page.get_page_text()`, the resolver maps line words directly against genuine `page.tokens` to retrieve exact, verified OCR token bounding boxes. If `page` is `None` or tokens cannot be matched, it returns `None`, guaranteeing that non-OCR text strictly preserves all newline cuts.

---

### 2.2 Problem 2: Medication Dose Ambiguity Hardening

#### Analysis of `Paracetamol\n500`
1. **Entity Association**:
   When `Paracetamol\n500` occurs within a single segment, `Paracetamol` is extracted as `med.name`, setting `current_med_group`. `500` is extracted as `med.dose` and inherits `current_med_group`. The association between `500` and `Paracetamol` is entity-correct.
2. **Clinical Completeness**:
   In clinical pharmacology, a bare number `"500"` without a unit is incomplete and ambiguous (could represent 500 mg, 500 mcg, 500 mL, 500 units). The source text contains only `"500"`.
3. **Evidence Integrity & Anti-Hallucination**:
   The system must **never invent or guess a unit** (such as assuming "mg" based on prescribing conventions). The evidence text is preserved verbatim as `"500"`, and `unit_raw` remains `None`.

#### Implemented Export Gate (Medication Dose Completeness Gate)
In `src/clindoc/services/fhir_export.py` (`evaluate_claim_eligibility`):
- Added an explicit check for `med.dose` claims lacking a unit of measure (`unit_raw` is None/empty and no unit embedded in `value`).
- Under Mode 0 (100% human review policy), un-unitted medication doses are ambiguous and strictly ineligible for export without explicit human clinical review acceptance (`rev_state in (ACCEPTED, EDITED)`).
- When unreviewed or pending, `evaluate_claim_eligibility` returns `is_eligible = False` with the explicit reason:
  `"Medication dose lacks required unit of measure; ambiguous dosage requires human review before export"`.
- This ensures an ambiguous dose can **never become an accepted/exportable medication dosage without appropriate human review**.

---

## 3. Exact Files Modified

1. **`src/clindoc/ocr/table_layout.py`**:
   - Added `_validate_and_extract_bbox()` helper.
   - Enforced fail-closed geometry in `is_vertical_lab_component_continuation()`.
   - Added resolution-adaptive tolerances based on previous line height.
2. **`src/clindoc/extract/segmentation.py`**:
   - Added `_resolve_line_bbox()` helper.
   - Replaced fragile character-span bounding box lookups with whitespace-robust token-level resolution.
3. **`src/clindoc/services/fhir_export.py`**:
   - Added `import re`.
   - Integrated Medication Dose Completeness Gate in `evaluate_claim_eligibility()` for `med.dose` claims lacking units.
4. **`tests/test_phase523_milestone24_geometry_dose_safety.py`**:
   - Created full 12-case adversarial test suite.

---

## 4. Adversarial Test Suite Results

Test file: `tests/test_phase523_milestone24_geometry_dose_safety.py`

| Test Case | Scenario Description | Expected Behavior | Result |
| :--- | :--- | :--- | :--- |
| **Test 1** | Valid vertically aligned lab result (`HAEMOGLOBIN\n12.6\nGM%`) with complete geometry | 1 segment; 1 lab claim with exact verbatim span and enclosing bbox | **PASS** |
| **Test 2** | Missing previous bounding box (`prev_bbox=None`) | Returns `False`; newline cut preserved | **PASS** |
| **Test 3** | Missing current bounding box (`curr_bbox=None`) | Returns `False`; newline cut preserved | **PASS** |
| **Test 4** | Both bounding boxes missing (`page=None`) | Returns `False`; plain-text cuts all newlines into separate segments | **PASS** |
| **Test 5** | Inverted / malformed bounding boxes ($x_0 > x_1$, $y_0 > y_1$, len != 4, negative, zero-width) | Rejected fail-closed; returns `False` | **PASS** |
| **Test 6** | NaN and infinite coordinates (`float('nan')`, `float('inf')`, `-inf`) | Rejected fail-closed; returns `False` | **PASS** |
| **Test 7** | Adaptive resolution support (72 DPI, 150 DPI, 300 DPI) | Valid vertical rows continue across all supported DPI levels | **PASS** |
| **Test 8** | `Paracetamol\n500` with no unit | Binds dose to drug; preserves evidence; blocked from unreviewed export; exportable only with human acceptance | **PASS** |
| **Test 9** | `Paracetamol 500 mg` with explicit unit | Explicit unit passes completeness check; exportable once accepted | **PASS** |
| **Test 10** | Two adjacent medication orders | Lines split into independent segments; distinct group IDs; zero migration | **PASS** |
| **Test 11** | Laboratory result immediately followed by medication | Lab and med in distinct segments; disjoint group IDs (`UUID_LAB != UUID_MED`) | **PASS** |
| **Test 12** | Evidence crossing independent logical rows | Rejected fail-closed with `"crosses segment boundary"` | **PASS** |

**Adversarial Suite Total**: **12 passed in 0.27s**.

---

## 5. Regression Testing & Full Repository Suite Results

### 5.1 Milestone Regression Suites
Re-ran all test suites covering Milestones 1 through 2.4:
- `tests/test_phase523_milestone1_deterministic_grounding.py`
- `tests/test_phase523_milestone13_anchor_ordering.py`
- `tests/test_phase523_milestone2_segmentation.py`
- `tests/test_phase523_milestone22_safety_remediation.py`
- `tests/test_phase523_milestone23_ocr_layout.py`
- `tests/test_phase523_milestone24_geometry_dose_safety.py`
- `tests/test_whitespace_grounding.py`
- `tests/test_phase52_medication_coreference.py`
- `tests/test_phase511_export_safety.py`
- `tests/test_phase5111_isolation_audit.py`
- `tests/test_phase51_layout_ingest.py`

**Milestone Suite Result**: **137 passed, 0 failed, 3 warnings in 4.54s**.

### 5.2 Full Repository Test Suite
Command: `.venv\Scripts\pytest -q`
- **Total Tests Passed**: 1,380 (an increase of +12 new passing tests from baseline 1,368).
- **Total Tests Failed**: 5 (0 new failures; 100% match with historical baseline).
- **Warnings**: 11.
- **Duration**: 428.58s (7m 8s).

#### Complete Reconciliation of the 5 Known Baseline Failures:
1. `tests/test_cli.py::test_cli_unsupported_extension_fails`: Pre-existing CLI exit-code assertion disparity.
2. `tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling`: Pre-existing text file ingestion mock discrepancy.
3. `tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation`: Pre-existing imbalance assertion in synthetic generator fixture.
4. `tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline`: Pre-existing baseline test expectation difference.
5. `tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification`: Pre-existing test searching for `"verify=False"` string across comments/docstrings in `ehr_transport.py`.

**Conclusion**: Zero regressions introduced. All 12 Milestone 2.4 adversarial tests passed cleanly.

---

## 6. Constraints Adherence & Remaining Limitations

### 6.1 Constraints Verification
- **Read-Only on Frozen Phase 4 Contracts**:
  - `src/clindoc/fhir/mapper.py`: UNMODIFIED.
  - `src/clindoc/fhir/constants.py`: UNMODIFIED.
  - `src/clindoc/fhir/validator.py`: UNMODIFIED.
- **Strict Evidence Containment**: Preserved verbatim character offsets and strict boundary rejection.
- **Ground Truth Integrity**: Zero ground truth modifications made.
- **No Milestone 3 Implementation**: Scope remained strictly within Milestone 2.4 hardening.
- **Git State**: No staging, commits, or pushes executed.

### 6.2 Remaining Limitations & Future Considerations
- In non-OCR plain text (`page=None`), vertical laboratory rows with newlines (e.g. `HAEMOGLOBIN\n12.6\nGM%`) will be split into separate single-line segments because no bounding boxes exist to confirm layout continuity. For born-digital text with vertical tables, an OCR or layout ingestion step (`DocumentPage`) is required to reconstruct multi-line findings.
- When an extracted medication dose has no unit in the source text (e.g. `"Paracetamol 500"`), human review is required before clinical export. If automated unit resolution is required in future phases, a supervised clinical ontology expansion step (with audit logging) should be formally specified.
