# Phase 5.2.3 — Milestone 2.3: OCR Layout Compatibility & Regression Repair Report

**Author**: Antigravity Assistant  
**Date**: October 31, 2026  
**Status**: Completed — Regression Resolved & Verified  
**Audited Components**:
- [`tests/test_whitespace_grounding.py`](file:///d:/daa/tests/test_whitespace_grounding.py)
- [`src/clindoc/ocr/table_layout.py`](file:///d:/daa/src/clindoc/ocr/table_layout.py)
- [`src/clindoc/extract/segmentation.py`](file:///d:/daa/src/clindoc/extract/segmentation.py)
- [`src/clindoc/extract/llm_extractor.py`](file:///d:/daa/src/clindoc/extract/llm_extractor.py)
- [`tests/test_phase523_milestone23_ocr_layout.py`](file:///d:/daa/tests/test_phase523_milestone23_ocr_layout.py)

---

## Executive Summary

Milestone 2.2 corrected critical boundary-assignment vulnerabilities by enforcing strict evidence containment: an extraction span must be wholly contained within exactly one valid segment, eliminating all start-coordinate, midpoint, and nearest-neighbor fallbacks. However, the subsequent full repository run introduced three failures in `tests/test_whitespace_grounding.py`:
1. `test_3_ocr_newlines_vs_model_spaces_passes`
2. `test_4_multiple_whitespace_characters_pass`
3. `test_9_bounding_box_derivation_correct`

This report provides the root-cause investigation, layout-aware architectural resolution, exact production modifications, adversarial test suite, and full regression verification for Milestone 2.3.

### Key Results Matrix
| Test Suite / Metric | Pre-Fix Status (M2.2) | Post-Fix Status (M2.3) | Change Impact |
| :--- | :---: | :---: | :--- |
| `tests/test_whitespace_grounding.py` | 6 passed, 3 failed | **9 passed, 0 failed** | **100% Repaired** |
| `tests/test_phase523_milestone1_deterministic_grounding.py` | 9 passed | **9 passed** | Baseline Preserved |
| `tests/test_phase523_milestone13_anchor_ordering.py` | 6 passed | **6 passed** | Baseline Preserved |
| `tests/test_phase523_milestone2_segmentation.py` | 13 passed | **13 passed** | Baseline Preserved |
| `tests/test_phase523_milestone22_safety_remediation.py` | 12 passed | **12 passed** | Strict Containment Preserved |
| `tests/test_phase523_milestone23_ocr_layout.py` | *N/A (New)* | **10 passed, 0 failed** | Comprehensive Adversarial Coverage |
| Milestone 1 through 2.3 Integrated Suite | 56 passed, 3 failed | **59 passed, 0 failed** | Zero Regressions |
| Core Domain Regressions (Med, Lab, OCR, Export Safety) | 341 passed | **341 passed** | Zero Regressions |
| Full Repository Suite (`pytest -q`) | 1,355 passed, 8 failed (3 regressions + 5 baseline) | **1,368 passed, 5 failed** | **Returned to exact 5 baseline failures (0 new failures)** |

---

## 1. Root-Cause Analysis of the Three Regressions

### 1.1 `test_3_ocr_newlines_vs_model_spaces_passes`
- **Input Text**: `page_text = "HAEMOGLOBIN\n12.6\nGM%"`
- **Model Output**: `{"field_type": "lab.value", "value": "12.6", "evidence_text": "HAEMOGLOBIN 12.6 GM%", "unit_raw": "GM%"}`
- **Previous Milestone 2.0/2.1 Behavior**:
  Milestone 2 introduced `segment_clinical_text()` with an unconditional cut on all newline sequences (`\r?\n+`). This cut split the text into three separate segments:
  - Seg 0: `HAEMOGLOBIN` `[0:11]`
  - Seg 1: `12.6` `[11:16]`
  - Seg 2: `GM%` `[16:21]`
  In Milestone 2.0/2.1, `find_segment_idx(0, 21, segments)` failed strict containment, but fell back to Rule 2 (start coordinate: `if seg.local_start <= ls < seg.local_end`), silently assigning the cross-segment span `[0:21]` to Seg 0.
- **Milestone 2.2 Change & Failure**:
  Milestone 2.2 eliminated the start-coordinate fallback to enforce strict containment. Because `[0:21]` spanned across Segments 0, 1, and 2, `find_segment_idx()` returned `None`, and `NarrativeExtractor` rejected the extraction with diagnostic reason: `"invalid source offset: [0:21] uncontained or crosses segment boundary"`. Consequently, zero claims were generated, causing `assert len(claims) == 1` to fail.
- **Architectural Diagnosis**:
  `HAEMOGLOBIN\n12.6\nGM%` does not represent three independent narrative statements. It is a single logical laboratory determination printed vertically in a columnar OCR table format (the CMC Vellore defect from Phase 5.1). Treating line breaks between constituent components of a single vertical laboratory finding as sentence/clause cuts was a false segmentation split.

### 1.2 `test_4_multiple_whitespace_characters_pass`
- **Input Text**: `page_text = "POTASSIUM  \t  4.2  \n\n  mEq/L"`
- **Model Output**: `{"field_type": "lab.value", "value": "4.2", "evidence_text": "POTASSIUM 4.2 mEq/L", "unit_raw": "mEq/L"}`
- **Failure Mechanism**:
  The underlying source span `[0:29]` spanned across the double-newline sequence `\n\n`. Unconditional newline cutting split the unit `mEq/L` into Seg 1 `[21:29]`, isolating it from `POTASSIUM  \t  4.2` in Seg 0 `[0:21]`. Strict containment rejected the span `[0:29]`, resulting in zero claims.
- **Architectural Diagnosis**:
  A line consisting solely of a clinical laboratory unit (`mEq/L`) is an incomplete fragment that cannot stand alone as a sentence. In OCR laboratory tables with blank line padding, it represents a wrapped unit continuation of the preceding analyte result.

### 1.3 `test_9_bounding_box_derivation_correct`
- **Input Text**: `page_text = "HAEMOGLOBIN\n12.6\nGM%"`
- **Failure Mechanism**:
  Identical root cause to Test 3: because the underlying extraction was rejected during segmentation containment, zero Evidence objects were created (`assert len(evidences) == 1` failed), preventing verification of the multi-line bounding box coordinates ($y_0 \le 20.0$, $y_1 \ge 60.0$).

---

## 2. Architectural Decision: Layout-Aware Logical Row Reconstruction

Rather than introducing generic newline exceptions (which would weaken sentence and medication isolation across the board), Milestone 2.3 establishes **layout-aware logical laboratory row reconstruction** housed within the OCR table layout subsystem ([`table_layout.py`](file:///d:/daa/src/clindoc/ocr/table_layout.py)).

### 2.1 Principles of Layout-Aware Grouping
1. **Preserve Strict Evidence Containment**:
   - The invariant remains immutable: an extraction span must be wholly contained within exactly one valid segment.
   - No fallbacks (start-coordinate, midpoint, or nearest-segment) are restored.
2. **Evidence of a Shared Logical Cell / Row**:
   Consecutive physical lines are grouped into a single `ClinicalSegment` if and only if both spatial layout and structural complementarity confirm a single logical finding:
   - **Spatial Alignment** (when `DocumentPage` bounding boxes are provided):
     - Left column alignment: $|x_{0,\text{curr}} - x_{0,\text{prev}}| \le 15.0\,\text{px}$ or significant horizontal interval overlap.
     - Contiguous vertical spacing: $0 \le (y_{0,\text{curr}} - y_{1,\text{prev}}) \le 35.0\,\text{px}$.
   - **Structural & Semantic Complementarity**:
     - `prev_text` is an analyte name or analyte+value, and is **not** terminated by sentence punctuation (`.`, `!`, `?`, `;`, `:`).
     - `curr_text` is an incomplete constituent fragment:
       - Pure numeric result (e.g. `12.6`, `4.2`, `<0.01`, `Negative`, `Non-Reactive`).
       - Pure clinical laboratory unit (e.g. `GM%`, `g/dL`, `mg/dL`, `mEq/L`, `mmol/L`, `U/L`, `%`, `k/uL`).
       - Pure reference interval (e.g. `12.0 - 16.0`, `(ref 12-16)`).
     - Neither `prev_text` nor `curr_text` contains medication keywords (`tab`, `cap`, `oral`, `po`, `iv`, `bid`, `tid`, `daily`, `prn`, `mg`, `mcg`, `take`, `dispense`).
3. **Strict Isolation of Independent Statements**:
   - **Independent Laboratory Rows**: A newline followed by a new analyte name (e.g. `\nPOTASSIUM`) is strictly recognized as a row boundary and cut.
   - **Independent Medication Orders**: Medication orders on separate lines (e.g. `Metoprolol 25 mg oral\nLisinopril 10 mg oral`) are never merged because they contain medication names, dosages, and routes.
   - **Multi-Column Medication Tables**: Flattened columnar tables remain cut at line breaks, quarantining orphaned attributes as `__no_anchor__` to prevent cross-medication transposition.

---

## 3. Exact Production Modifications

### 3.1 [`src/clindoc/ocr/table_layout.py`](file:///d:/daa/src/clindoc/ocr/table_layout.py)
1. **Expanded `COMMON_CLINICAL_UNITS`**:
   Added authoritative Indian hospital and laboratory units observed in real-world holdouts (CMC Vellore): `"gm%"`, `"g%"`, `"cells/cumm"`, `"cumm"`, `"/cumm"`, `"mg/l"`, `"ug/dl"`, `"g/l"`, `"iu/ml"`, `"miu/ml"`, `"ng/dl"`, `"meq/24hr"`.
2. **Added `_MED_INDICATOR_TOKENS`**:
   Defined a strict set of medication dosage forms and administration routes that immediately disqualify laboratory row continuation.
3. **Implemented `is_vertical_lab_component_continuation()`**:
   Evaluates spatial bounding boxes and structural constituent criteria to determine whether consecutive lines belong to a shared logical laboratory entry.

### 3.2 [`src/clindoc/extract/segmentation.py`](file:///d:/daa/src/clindoc/extract/segmentation.py)
1. **Updated `segment_clinical_text()` Signature**:
   Added optional `page: Any | None = None` and `char_offset: int = 0` parameters, maintaining 100% backward compatibility with text-only callers.
2. **Layout-Aware Newline Evaluation**:
   When evaluating candidate newline cuts (`\r?\n+`), queries `is_vertical_lab_component_continuation()`. If layout and structural criteria confirm a shared laboratory finding, the newline is bypassed (not cut), keeping the vertical laboratory finding within a single cohesive segment.

### 3.3 [`src/clindoc/extract/llm_extractor.py`](file:///d:/daa/src/clindoc/extract/llm_extractor.py)
1. **Updated Segmentation Invocation**:
   In `_extract_narrative()`, updated Step 4.5:
   ```python
   segments = segment_clinical_text(region_text, page=page, char_offset=char_offset)
   ```
   Provides exact page token geometry to the segmentation engine.
2. **Strict Containment Preserved**:
   Step 5 enforcement remains completely untouched: extractions with uncontained or cross-segment spans are rejected fail-closed without fallback.

---

## 4. Adversarial Test Suite

Implemented in [`tests/test_phase523_milestone23_ocr_layout.py`](file:///d:/daa/tests/test_phase523_milestone23_ocr_layout.py) (10 tests, 100% passing):

1. `test_vertically_formatted_laboratory_result`:
   Verifies `HAEMOGLOBIN\n12.6\nGM%` forms 1 segment, extracts claim `12.6 GM%`, and preserves exact source span.
2. `test_two_independent_laboratory_rows`:
   Verifies `HAEMOGLOBIN\n12.6\nGM%\nPOTASSIUM\n4.2\nmEq/L` separates into 2 distinct segments with distinct `group_id`s (zero cross-lab association).
3. `test_two_medication_orders_on_separate_lines`:
   Verifies `Metoprolol 25 mg oral daily\nLisinopril 10 mg oral daily` isolates into 2 distinct segments with distinct `group_id`s (zero cross-medication association).
4. `test_flattened_multicolumn_medication_table`:
   Verifies `Metoprolol    Lisinopril\n25 mg oral    10 mg oral` cuts at `\n`; doses are quarantined as `__no_anchor__` without false transposition.
5. `test_multiline_evidence_crossing_independent_medication_rows`:
   Verifies candidate evidence spanning `daily\nLisinopril 10 mg` is rejected fail-closed with `"crosses segment boundary"`.
6. `test_multiline_evidence_crossing_independent_lab_rows`:
   Verifies candidate evidence spanning `GM%\nPOTASSIUM` is rejected fail-closed with `"crosses segment boundary"`.
7. `test_whitespace_normalized_evidence_exact_offsets`:
   Verifies model space-separated output grounds to exact source text `POTASSIUM  \t  4.2  \n\n  mEq/L` at offsets `[0:29]`.
8. `test_bounding_box_preservation_multiline`:
   Verifies bounding box enclosing all 3 lines ($y_0 \le 20.0$, $y_1 \ge 75.0$, $x_1 > x_0$).
9. `test_horizontal_lab_rows_isolated`:
   Verifies `GLUCOSE 95 mg/dL\nCREATININE 1.0 mg/dL` separates into 2 segments with distinct `group_id`s.
10. `test_permutation_invariance_ocr_layout`:
    Verifies that all 6 permutations of valid and invalid candidates under multiline OCR layout produce identical claim signatures, group IDs, and rejection diagnostics.

---

## 5. Full Regression Verification & Baseline Parity

### 5.1 Verification Commands and Results
1. **Whitespace Grounding Regression Suite**:
   ```powershell
   .venv\Scripts\pytest -v tests/test_whitespace_grounding.py
   # Result: 9 passed, 0 failed in 0.09s
   ```
2. **Integrated Milestones 1 through 2.3 Suite**:
   ```powershell
   .venv\Scripts\pytest -v tests/test_phase523_milestone1_deterministic_grounding.py tests/test_phase523_milestone13_anchor_ordering.py tests/test_phase523_milestone2_segmentation.py tests/test_phase523_milestone22_safety_remediation.py tests/test_phase523_milestone23_ocr_layout.py tests/test_whitespace_grounding.py
   # Result: 59 passed, 0 failed in 0.48s
   ```
3. **Core Domain Regressions**:
   ```powershell
   .venv\Scripts\pytest -q tests/test_medication_boundaries.py tests/test_compound_medications.py tests/test_phase52_medication_coreference.py tests/test_narrative_benchmark.py tests/test_ocr_pipeline.py tests/test_phase51_layout_ingest.py tests/test_extraction_subsystem.py tests/test_canonical_fhir_mapping.py tests/test_fhir_export.py tests/test_phase511_export_safety.py
   # Result: 341 passed in 3.96s
   ```

### 5.2 Full Repository Test Suite Status
The full test suite execution returned to the exact baseline of five known pre-existing failures:
- `tests/test_cli.py::test_cli_unsupported_extension_fails` (Exit code assertion)
- `tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling`
- `tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation` (AssertionError: 335 != 705)
- `tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline`
- `tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification`

**Summary:** **1,368 passed, 5 failed**, 11 warnings in 299.60s (0:04:59) — exactly 5 known baseline failures, 0 new failures (100% of regressions resolved).

---

## 6. Milestone Conclusion & Readiness

- Milestone 2.3 is **complete**.
- All three OCR layout regressions in `tests/test_whitespace_grounding.py` are resolved.
- Strict evidence containment remains 100% intact with zero fallback.
- No cross-medication or cross-laboratory association is introduced.
- In accordance with instructions, work has stopped after Milestone 2.3 to await user review before Milestone 3.
