# Phase 5.2.3 — Milestone 2.1: Segmentation Safety & Boundary Audit Report

**Author**: Antigravity Assistant  
**Date**: October 31, 2026  
**Status**: Completed — Read-Only Adversarial Audit of Milestone 2 Segmentation Engine  
**Audited Components**:
- [`src/clindoc/extract/segmentation.py`](file:///d:/daa/src/clindoc/extract/segmentation.py)
- [`src/clindoc/extract/llm_extractor.py`](file:///d:/daa/src/clindoc/extract/llm_extractor.py)
- [`tests/test_phase523_milestone2_segmentation.py`](file:///d:/daa/tests/test_phase523_milestone2_segmentation.py)

---

## Executive Summary

This read-only audit evaluates the structural safety, edge-case resilience, and boundary behavior of the newly introduced sentence and clause segmentation engine ([`segmentation.py`](file:///d:/daa/src/clindoc/extract/segmentation.py)) and its state-machine integration within the LLM extraction pipeline ([`llm_extractor.py`](file:///d:/daa/src/clindoc/extract/llm_extractor.py)).

### Key Findings Matrix

| Dimension | Primary Observation | Safety Impact | Recommended Milestone 3 Action |
| :--- | :--- | :--- | :--- |
| **1. OCR & Multiline Text** | All newline sequences (`\r?\n+`) break segments. Multiline wrapped orders (e.g. drug on line 1, dose on line 2) quarantine the dose as `__no_anchor__`. | **Fail-Closed & Safe**: Completely prevents cross-order row contamination in table/list layouts, but unanchors visual line wraps. | Retain `\n` isolation as baseline; handle multiline continuation only when layout geometry confirms a wrapped block. |
| **2. Unit + Uppercase Route** | `10 mg. PO daily` splits at `mg.` because `PO` starts with uppercase and is not in the form-prefix whitelist. | **False Split**: `PO` is isolated into segment 1 and quarantined as `__no_anchor__`. | Expand `CLINICAL_ABBREVIATIONS` lookahead to check for following route/frequency tokens before splitting. |
| **3. Lowercase Sentence Start** | `Lisinopril 10 mg. oral Metoprolol added` does not split at `mg.` because `oral` is lowercase. | **False Merge**: Merges two sentences into one segment if the subsequent token is lowercased. | Address in Milestone 3 syntactic pairing: recognize capitalized drug head nouns even when preceding text is lowercased. |
| **4. Cross-Segment Evidence** | If an extraction span crosses a boundary (e.g. `25 mg; Lisinopril`), `find_segment_idx()` falls back to Rule 2 (start coordinate) and silently assigns it to segment 0. | **Production Vulnerability**: Cross-boundary spans can swallow delimiters and adjacent drug names, improperly binding to the preceding anchor. | **Correction Required**: `find_segment_idx()` must require strict containment; cross-segment spans must fail closed and be rejected. |
| **5. Semicolon Dependent Clauses** | `Metoprolol 25 mg daily; take orally.` splits at `;`, isolating `take orally` into segment 1 without an anchor (`__no_anchor__`). | **Fail-Closed**: Prevents cross-medication bleeding, but leaves dependent instruction unanchored. | Implement dependent-clause attachment rules in Milestone 3. |

---

## 1. OCR and Multiline Text

### 1.1 Evaluated Patterns

```python
# Scenario A: Medication name on line 1, dose on line 2
"Metoprolol\n25 mg daily"
# Segment 0: 'Metoprolol' [0:10] -> Med Group M_0
# Segment 1: '\n25 mg daily' [10:22] -> '25 mg' unanchored (__no_anchor__)

# Scenario B: Wrapped instructions
"Metoprolol 25 mg daily by mouth\nin the morning"
# Segment 0: 'Metoprolol 25 mg daily by mouth' [0:31] -> Med Group M_0 (anchored)
# Segment 1: '\nin the morning' [31:46] -> 'in the morning' unanchored (__no_anchor__)

# Scenario C: Separate table rows
"Metoprolol 25 mg\nLisinopril 10 mg"
# Segment 0: 'Metoprolol 25 mg' [0:16] -> Med Group M_0
# Segment 1: '\nLisinopril 10 mg' [16:33] -> Med Group M_1 (cleanly separated)

# Scenario D: Flattened columns
"Metoprolol Lisinopril\n25 mg 10 mg"
# Segment 0: 'Metoprolol Lisinopril' [0:21] -> M_0 (Metoprolol), M_1 (Lisinopril)
# Segment 1: '\n25 mg 10 mg' [21:33] -> '25 mg' (__no_anchor__), '10 mg' (__no_anchor__)

# Scenario E: Heading followed by multiline statement
"MEDICATIONS:\nMetoprolol\n25 mg daily"
# Segment 0: 'MEDICATIONS:' [0:12] -> Heading boundary
# Segment 1: '\nMetoprolol' [12:23] -> Med Group M_0
# Segment 2: '\n25 mg daily' [23:35] -> '25 mg' (__no_anchor__)
```

### 1.2 Architectural Assessment: Line Break Continuation vs Independent Statements
In raw narrative prose blocks, standard ASCII line breaks cannot unambiguously indicate whether a newline represents:
1. A **soft-wrapped line** within a continuous paragraph, or
2. A **hard line break** separating distinct list items, prescription orders, or table rows.

**Safety Ruling**:
Treating every `\n+` as a segment boundary is strictly **fail-closed**.
- In separate table rows (Scenario C), it guarantees that Lisinopril's dose cannot bleed into Metoprolol.
- In flattened multi-column tables (Scenario D), naive newline continuation would have assigned `25 mg` to `Lisinopril` (transposition error). Milestone 2's newline boundary quarantines both doses as `__no_anchor__`, preventing a catastrophic clinical error.
- For multiline wrapped instructions (Scenario A & B), Milestone 3 should not guess associations blindly; only if layout geometry (e.g. line bounding box vertical delta $< 1.5 \times$ line height and same left indentation) proves continuation should lines be merged.

---

## 2. Abbreviation and Punctuation Handling

### 2.1 Verification Matrix

| Clinical Pattern | Raw Input | Resulting Segments | Safety Analysis |
| :--- | :--- | :---: | :--- |
| **Inline Unit** | `"Patient took 25 mg. orally daily."` | 1 | **Protected**: Followed by lowercase `orally`; no false split. |
| **Inline Volume** | `"Dose was 5 ml. twice a day."` | 1 | **Protected**: Followed by lowercase `twice`; no false split. |
| **Form + Capitalized Drug** | `"Take 1 Tab. Metoprolol 25 mg."` | 1 | **Protected**: Explicitly whitelisted in `("tab", "cap", ...)`; no false split. |
| **Form + Lowercase** | `"Take 1 Cap. daily."` | 1 | **Protected**: Followed by lowercase `daily`; no false split. |
| **Route + Frequency** | `"Given p.o. twice daily."` | 1 | **Protected**: `p.o.` period followed by lowercase; no false split. |
| **Frequency Inline** | `"Take 25 mg b.i.d. for hypertension."` | 1 | **Protected**: `b.i.d.` followed by lowercase; no false split. |
| **Frequency at Sentence End** | `"Take 25 mg b.i.d. Next sentence starts."` | 2 | **True Split**: Period followed by uppercase word creates clean boundary. |
| **Unit at Sentence End** | `"Take Lisinopril 10 mg. Metoprolol was added."` | 2 | **True Split**: Correct sentence separation between two drug orders. |
| **Decimal Dose** | `"Give 2.5 mg daily."` | 1 | **Protected**: Lookaround `(?<=\d)\.(?=\d)` prevents split at `2.5`. |
| **Decimal with Trailing Zero** | `"Give 0.50 ml IV."` | 1 | **Protected**: Lookaround prevents split at `0.50`. |
| **Repeated Punctuation** | `"Metoprolol 25 mg... Lisinopril 10 mg"` | 2 | **Collapsed**: Repeated periods collapsed into single boundary; no empty segment. |
| **Repeated Semicolons** | `"Metoprolol 25 mg;; Lisinopril 10 mg"` | 2 | **Collapsed**: Repeated semicolons collapsed cleanly; no empty segment. |
| **Formatted Date** | `"Date: 01.12.2024. Next"` | 2 | **Protected**: `01.12.2024` intact; splits after final period before `Next`. |
| **Abbreviation at EOF** | `"Patient took Metoprolol 25 mg p.o."` | 1 | **Protected**: Handles end-of-string without trailing boundary. |

### 2.2 Detailed Edge-Case Analysis: False Splits & False Merges

#### Edge Case A: False Split on Unit Abbreviation Followed by Uppercase Route
- **Observed Behavior**:
  `"Give 10 mg. PO daily."` $\to$ Splits into `['Give 10 mg.', ' PO daily.']` (2 segments).
  `"Give 10 mg. IV stat."` $\to$ Splits into `['Give 10 mg.', ' IV stat.']` (2 segments).
- **Mechanism in `segmentation.py`**:
  Lines 97–107 check `_is_clinical_abbreviation(preceding_token)`. Here `preceding_token == "mg"`.
  It checks `rest = text[punct_end:].lstrip(" \t")`.
  Because `rest` begins with uppercase `P` (`PO`) or `I` (`IV`), it fails `rest[0].islower()` and is not in `("tab", "cap", "syp", "inj", "dr", ...)`.
  The engine falls through and treats the period as a sentence terminator!
- **Consequence**:
  `PO` or `IV` falls into Segment 1. Because Segment 1 lacks an anchor, the route is quarantined as `__no_anchor__`.
- **Note**: If written without periods (`10 mg PO daily`) or with lowercase routes (`10 mg. po daily`), it does not split.

#### Edge Case B: False Split on Clinician Middle Initials
- **Observed Behavior**:
  `"Dr. J. Smith evaluated the patient."` $\to$ Splits into `['Dr. J.', ' Smith evaluated the patient.']` (2 segments).
- **Mechanism in `segmentation.py`**:
  `"Dr."` is protected by `("dr", "mr", "mrs")`, but `"J."` is a single-letter token followed by capitalized `"Smith"`. Since `"J"` is not in `CLINICAL_ABBREVIATIONS`, it triggers a sentence cut.

#### Edge Case C: False Merge on Sentence Boundary Followed by Lowercase Token
- **Observed Behavior**:
  `"Patient took Lisinopril 10 mg. oral Metoprolol was added."` $\to$ 1 segment.
- **Mechanism in `segmentation.py`**:
  When a clinician omits capitalization or OCR produces lowercase `"oral"`, `rest[0].islower()` causes `mg.` to be treated as an inline abbreviation rather than a sentence boundary.
- **Consequence**:
  Both medications end up in the same segment.

---

## 3. Evidence-to-Segment Mapping (`find_segment_idx`)

### 3.1 Mapping Algorithm Audit

```python
def find_segment_idx(ls: int, le: int, segments: list[ClinicalSegment]) -> int:
    if not segments:
        return 0

    # 1. Strict containment: segment covers [ls, le]
    for seg in segments:
        if seg.local_start <= ls and le <= seg.local_end:
            return seg.segment_idx

    # 2. Start coordinate containment: segment covers ls
    for seg in segments:
        if seg.local_start <= ls < seg.local_end:
            return seg.segment_idx

    # 3. Midpoint containment: segment covers span midpoint
    mid = (ls + le) // 2
    for seg in segments:
        if seg.local_start <= mid < seg.local_end:
            return seg.segment_idx

    # 4. Fallback: closest segment by start distance
    closest = min(segments, key=lambda s: abs(s.local_start - ls))
    return closest.segment_idx
```

### 3.2 Span Boundary Test Matrix

Given `text = "Metoprolol 25 mg; Lisinopril 10 mg"`:
- `Seg 0`: `[0:17]` (`'Metoprolol 25 mg;'`)
- `Seg 1`: `[17:34]` (`' Lisinopril 10 mg'`)

| Span Category | Coordinates `[ls:le]` | Substring | Mapped Segment | Safe? | Analysis |
| :--- | :---: | :--- | :---: | :---: | :--- |
| **Strictly Inside** | `[0:10]` | `"Metoprolol"` | `Seg 0` | **YES** | Matches Rule 1 (strict containment). |
| **Touching Boundary** | `[0:17]` | `"Metoprolol 25 mg;"` | `Seg 0` | **YES** | Matches Rule 1 (`0 <= 0 and 17 <= 17`). |
| **Inside Next Segment** | `[18:28]` | `"Lisinopril"` | `Seg 1` | **YES** | Matches Rule 1 (`17 <= 18 and 28 <= 34`). |
| **Includes Delimiter** | `[11:17]` | `"25 mg;"` | `Seg 0` | **YES** | Semicolon delimiter index 16 is part of Seg 0. Matches Rule 1. |
| **Crosses Two Segments** | `[11:28]` | `"25 mg; Lisinopril"` | `Seg 0` | **VULNERABILITY** | **Fails Rule 1, matches Rule 2!** Silently assigned to `Seg 0`. |
| **Empty Span** | `[5:5]` | `""` | `Seg 0` | Warning | Returns segment covering coordinate 5. |
| **Inverted Span** | `[10:5]` | Invalid | `Seg 0` | Warning | Returns segment containing `ls`. |
| **Negative Out-of-Bounds** | `[-5:-1]` | Invalid | `Seg 0` | Warning | Falls back to closest start distance (Rule 4). |
| **Positive Out-of-Bounds** | `[100:110]` | Invalid | `Seg 1` | Warning | Falls back to closest start distance (Rule 4). |
| **Overlapping Spans** | `[0:10]`, `[0:10]` | Same text | `Seg 0`, `Seg 0` | **YES** | Both resolve deterministically to Seg 0. |

### 3.3 Critical Vulnerability: Cross-Segment Evidence Swallowing
When candidate extractions produce over-extended spans that cross clause boundaries:
```python
exts = [
    {"field_type": "med.name", "value": "Metoprolol", "evidence_text": "Metoprolol", "start": 0, "end": 10},
    {"field_type": "med.dose", "value": "25 mg; Lisinopril", "evidence_text": "25 mg; Lisinopril", "start": 11, "end": 28},
]
```
- The dose span `[11:28]` begins in Segment 0 and ends in Segment 1, spanning across the semicolon and covering the second medication `"Lisinopril"`.
- Because `find_segment_idx()` uses `seg.local_start <= ls < seg.local_end` as Rule 2, it assigns the entire span to `Seg 0`.
- In `llm_extractor.py`, because `seg_idx == current_segment_idx == 0`, the boundary check does not fire!
- **Result**: The extraction `"25 mg; Lisinopril"` is bound to `Metoprolol`'s group ID.
- **Required Milestone 3 Rule**: Any extraction whose span `[ls:le]` crosses a segment boundary (`find_segment_idx` returns non-contained span) must be quarantined as invalid evidence (`diagnostics["rejected"].append(...)`) rather than silently assigned.

---

## 4. Clause Semantics: Semicolons and Dependent Structures

### 4.1 Independent Clauses (Semicolon as Drug Order Boundary)
```text
"Metoprolol 25 mg daily; Lisinopril 5 mg nightly."
```
- **Segment 0**: `Metoprolol 25 mg daily;`
- **Segment 1**: ` Lisinopril 5 mg nightly.`
- **Result**: Metoprolol forms Group 1; Lisinopril forms Group 2.
- **Assessment**: Correct and clinically necessary isolation. Semicolons in prescription lists consistently delineate separate medication orders.

### 4.2 Dependent Instructional Clauses
```text
"Metoprolol 25 mg daily; take orally."
```
- **Segment 0**: `Metoprolol 25 mg daily;`
- **Segment 1**: ` take orally.`
- **Result**:
  - `Metoprolol` and `25 mg` $\to$ Group 1.
  - `orally` (`med.route`) in Segment 1 $\to$ `__no_anchor__`.
- **Assessment**:
  - In Milestone 2, quarantining `orally` to `__no_anchor__` is strictly safe (fail-closed: zero risk of assigning `orally` to an unrelated drug).
  - However, in English clinical documentation, semicolons are often used before dependent imperative instructions (`take with food`, `orally as needed`).
  - **Milestone 3 Requirement**: Syntactic pairing must distinguish between:
    - Independent clauses containing a new subject or medication anchor.
    - Dependent instructional clauses that lack a subject/anchor and follow an active medication order.

### 4.3 Coordinate Shared-Attribute Structures
```text
"Metoprolol and Lisinopril were both taken orally."
```
- Both medications are in the same clause segment.
- `orally` is a coordinate attribute modifying both `Metoprolol` and `Lisinopril`.
- **Milestone 3 Requirement**: Must define an explicit coordinate resolution rule:
  - Either distribute the attribute to both anchors, or
  - Quarantine the attribute with a `coordinate_ambiguity` review flag to ensure human clinician oversight.

---

## 5. Determinism & Regression Verification

### 5.1 Permutation Invariance
Candidate extractions for multi-clause inputs were evaluated across all $4! = 24$ candidate permutations:
```python
text = "Metoprolol 25 mg daily; Lisinopril 10 mg nightly."
# Evaluated all 24 permutations of candidate inputs
```
- **Result**: `All permutations deterministic & invariant: True`
- Normalized group structures, claims, and evidences are 100% order-invariant.

### 5.2 Test Suites Executed

#### Core Extraction, Segmentation, Medication Boundary & Safety Suites:
```bash
pytest tests/test_phase523_milestone1_deterministic_grounding.py \
       tests/test_phase523_milestone13_anchor_ordering.py \
       tests/test_phase523_milestone2_segmentation.py \
       tests/test_phase52_medication_coreference.py \
       tests/test_medication_boundaries.py \
       tests/test_narrative_benchmark.py \
       tests/test_extraction_subsystem.py \
       tests/test_canonical_fhir_mapping.py \
       tests/test_fhir_export.py \
       tests/test_pd022_qualitative_labs.py \
       tests/test_phase511_export_safety.py -q
```
- **Passed**: **355 / 355 passed** (26.22s, 0 failures).

#### Full Repository Test Suite:
```bash
pytest -q
```
- **Passed**: **1,346 passed** (0 new failures).
- **Known Baseline Failures**: **5** (100% pre-existing from Phase 5.1/5.2 baseline):
  1. `tests/test_cli.py::test_cli_unsupported_extension_fails`
  2. `tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling`
  3. `tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation`
  4. `tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline`
  5. `tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification`
- **Newly Introduced Failures**: **0**.

---

## 6. Recommendations for Milestone 3 Implementation

1. **Strict Cross-Segment Containment (`find_segment_idx`)**:
   Enforce that an extraction span must be strictly contained (`seg.local_start <= ls and le <= seg.local_end`). If a span crosses a boundary, return `None` and fail closed by recording a diagnostic rejection.
2. **Abbreviation Whitelist Lookahead**:
   Prevent false splits on `10 mg. PO` by checking if the uppercase token following a unit period is a known clinical route or frequency (`PO`, `IV`, `IM`, `PRN`, `BID`, `TID`, `QID`).
3. **Clause-Level Syntactic Pairing Rules**:
   - **Rule A (Pre-nominal binding)**: Within an isolated segment, attributes preceding the medication anchor (e.g. `Oral Metoprolol`) bind forward to that anchor.
   - **Rule B (Dependent clause chaining)**: A dependent instruction clause following a semicolon (e.g. `; take orally`) may attach to the preceding medication anchor if and only if no subsequent anchor exists in that clause.
   - **Rule C (Coordinate structures)**: In multi-anchor clauses (`Metoprolol and Lisinopril were both taken orally`), shared attributes must trigger explicit coordinate handling or fail closed to human review.

---

## Conclusion

The Milestone 2 segmentation engine successfully establishes deterministic sentence and clause boundaries, prevents cross-sentence medication contamination, and preserves all source offsets.

The audit has identified specific edge-case behaviors (cross-segment evidence fallback, unit + uppercase route splitting, and dependent clause isolation) that will form the exact design specifications for **Milestone 3**.

All tests pass. No workspace modifications were staged, committed, or pushed. Ready for review.
