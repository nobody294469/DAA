# Phase 5.2.3 — Milestone 2: Sentence & Clause Boundary Segmentation Report

**Author**: Antigravity Assistant  
**Date**: October 31, 2026  
**Status**: Completed & Verified — Deterministic Segmentation Engine Implemented; 13/13 New Adversarial Tests Passed; 335/335 Core Extraction & Safety Tests Passed; 1,346/1,351 Repository Tests Passed (0 New Failures)  
**Production Files Modified / Added**:
- [`src/clindoc/extract/segmentation.py`](file:///d:/daa/src/clindoc/extract/segmentation.py) *(New)*
- [`src/clindoc/extract/llm_extractor.py`](file:///d:/daa/src/clindoc/extract/llm_extractor.py) *(Updated)*
**Test File Added**: [`tests/test_phase523_milestone2_segmentation.py`](file:///d:/daa/tests/test_phase523_milestone2_segmentation.py) *(New)*

---

## Executive Summary

Milestone 2 implements deterministic sentence and clause boundary segmentation for narrative clinical text extraction. It establishes rigorous structural boundaries to ensure that medication and laboratory groupings cannot leak across unrelated sentences, independent clauses, or list items.

### Primary Accomplishments
1. **Deterministic Segmentation Engine (`segmentation.py`)**:
   - Implemented `segment_clinical_text()` returning ordered, non-overlapping `ClinicalSegment` objects with exact `[local_start, local_end]` character coordinates in `region_text`.
   - Recognizes sentence terminators (`.`, `!`, `?`), clause delimiters (`;`), line breaks (`\n`, `\r\n`), list item markers (`1. `, `2) `, `- `), and uppercase heading boundaries.
   - Strictly preserves decimal numbers (`2.5 mg`), clinical abbreviations (`Tab.`, `p.o.`, `b.i.d.`, `mg.`), and collapses empty/punctuation-only segments.
2. **Segment Boundary Enforcement in LLM Extractor (`llm_extractor.py`)**:
   - Integrated `find_segment_idx()` into Step 5 of `_build_claims()`.
   - Whenever an extraction crosses into a new sentence or clause segment, `current_med_group` and `current_lab_group` are **reset to `__no_anchor__`**.
   - **Cross-Sentence Contamination Eliminated**: In `"Patient on Lisinopril 10 mg. Switched to Oral Metoprolol 25 mg."`, `"Oral"` no longer leaks into Lisinopril's group; it is safely quarantined as `__no_anchor__`.
3. **Adversarial & Regression Verification**:
   - All 12 mandated adversarial scenarios passed (13/13 tests in 0.14s).
   - Core extraction, boundary isolation, and safety suites: **335/335 passed** (15.45s).
   - Full repository test suite: **1,346 passed, 5 known pre-existing failures** (265.49s). **Zero new failures introduced**.

---

## 1. Audit Findings Prior to Implementation

Before implementation, an architectural audit of the narrative extraction pipeline identified:

| Dimension | Audit Finding | Architectural Decision |
| :--- | :--- | :--- |
| **`region_text` Construction** | Reconstructed in `router.py` by splitting page text on double newlines (`\n\n`) into blocks $r_0, r_1, \dots$. | Operates on `region_text` directly within `NarrativeExtractor`, segmenting narrative prose blocks into fine-grained clauses. |
| **Coordinate System** | `local_start` and `local_end` are region-local indices ($0 \le ls < le \le \text{len}(region\_text)$). `char_offset` translates them to page-level coordinates for bbox extraction. | `ClinicalSegment` uses `local_start` and `local_end` to align directly with grounded extractions. |
| **Punctuation & Decimals** | Periods in decimal numbers (`2.5 mg`, `0.5 ml`) were at risk of being treated as sentence breaks. | Added lookaround guards: periods flanked by digits (`(?<=\d)\.(?=\d)`) are strictly protected from splitting. |
| **Clinical Abbreviations** | Common abbreviations with periods (`Tab.`, `p.o.`, `b.i.d.`, `q.d.`, `i.v.`, `mg.`, `dr.`) could trigger false splits. | Implemented `CLINICAL_ABBREVIATIONS` dictionary and contextual lookahead: abbreviation periods followed by lowercase, digits, or inline medication names do not split. |
| **Lists & Delimiters** | Semicolons (`;`) and line breaks (`\n`) frequently delineate distinct medication orders in clinical notes. | Both `;` and `\n` are registered as clause cut points. Repeated punctuation (e.g. `...\n\n;;`) is safely collapsed to avoid empty segments. |
| **Section Interaction** | `MedSpaCySectionDetector` identifies macro-level sections (e.g. `MEDICATIONS:`). | Heading labels ending with colons (`^[A-Z\s]{3,35}:`) are segmented as standalone boundaries, preventing header text from bleeding into the first clinical statement. |

---

## 2. Segmentation Architecture & Rules

### 2.1 Segmentation Module (`src/clindoc/extract/segmentation.py`)

```python
@dataclass(frozen=True)
class ClinicalSegment:
    segment_idx: int
    text: str
    local_start: int
    local_end: int
    delimiter_type: str
```

### 2.2 Boundary Decision Rules
1. **Line Breaks (`\r?\n+`)**: Every newline sequence marks a clause/record boundary.
2. **Semicolons (`;+`)**: Every semicolon sequence marks an independent clause boundary.
3. **Sentence Terminators (`[.!?]+`)**:
   - Allowed when followed by whitespace, uppercase letter, or end of text.
   - **Exception A (Decimals)**: Protected if preceded by a digit and followed by a digit.
   - **Exception B (Abbreviations)**: Protected if the preceding token is in `CLINICAL_ABBREVIATIONS` and followed by lowercase text, numbers, or inline title/dosage forms (`Tab.`, `Cap.`, etc.).
   - **Exception C (List numbers)**: Protected if preceded by line start and digits (e.g., `^1. `).
4. **Numbered / Bullet Lists**: Cut points inserted before item prefixes (e.g., ` 1. `, ` 2) `, ` - `).
5. **Uppercase Heading Colons**: Cut points inserted immediately after heading colons (e.g., `MEDICATIONS:`).
6. **Empty Span Suppression**: Segments lacking alphanumeric characters (`re.search(r"\w", seg_text) == None`) are filtered out.

---

## 3. Integration with LLM Extractor Grouping State Machine

In [`src/clindoc/extract/llm_extractor.py`](file:///d:/daa/src/clindoc/extract/llm_extractor.py), Step 4.5 segments `region_text`, and Step 5 enforces boundary isolation:

```python
        # Step 4.5: Segment region_text into sentence and clause spans (Milestone 2)
        segments = segment_clinical_text(region_text)

        # Step 5: State-machine Group ID assignment and Claim/Evidence generation
        _NO_ANCHOR = "__no_anchor__"
        current_med_group: str = _NO_ANCHOR
        current_lab_group: str = _NO_ANCHOR
        current_segment_idx: int = -1

        for ext, local_start, local_end, source_span in grounded:
            seg_idx = find_segment_idx(local_start, local_end, segments)

            # SEGMENT BOUNDARY ENFORCEMENT (Milestone 2):
            # A new sentence or clause cannot inherit the preceding segment's active group
            if seg_idx != current_segment_idx:
                current_segment_idx = seg_idx
                current_med_group = _NO_ANCHOR
                current_lab_group = _NO_ANCHOR

            field_type = ext.get("field_type", "")
            ...
```

---

## 4. Adversarial Verification Results

All 12 required adversarial scenarios were implemented and executed in [`tests/test_phase523_milestone2_segmentation.py`](file:///d:/daa/tests/test_phase523_milestone2_segmentation.py):

| Scenario | Input Text & Test Focus | Segment Count | Group Association Result | Status |
| :--- | :--- | :---: | :--- | :---: |
| **1. Consecutive Sentences** | `"Metoprolol 25 mg daily. Lisinopril 10 mg daily."` | 2 | Metoprolol (`G_0`), Lisinopril (`G_1`). Complete group separation across sentences. | **PASSED** |
| **2. Attribute at Beginning of New Sentence** | `"Patient was taking Lisinopril 10 mg. Oral Metoprolol 25 mg was added."` | 2 | Lisinopril (`G_0`). `"Oral"` in sentence 2 **does not leak** into `G_0`; quarantined as `__no_anchor__`. Metoprolol + 25 mg (`G_1`). | **PASSED** |
| **3. Semicolon-Separated Medications** | `"Metoprolol 25 mg; Lisinopril 10 mg; Aspirin 81 mg"` | 3 | Semicolons isolate clauses. Metoprolol (`G_0`), Lisinopril (`G_1`), Aspirin (`G_2`). | **PASSED** |
| **4. Decimal Doses** | `"Patient prescribed Metoprolol 2.5 mg daily and Amlodipine 0.5 mg."` | 1 | Decimals `2.5` and `0.5` do NOT trigger sentence splits. Numeric value preserved as `2.5`. | **PASSED** |
| **5. Clinical Abbreviations** | `"Take Tab. Metoprolol 25 mg p.o. b.i.d. daily."` | 1 | Abbreviations `Tab.`, `p.o.`, `b.i.d.` do NOT split sentence. All 4 claims share single group. | **PASSED** |
| **6. Numbered Medication Lists** | `"1. Metoprolol 50 mg\n2. Lisinopril 20 mg\n3. Aspirin 81 mg"` | 3 | Each numbered list item forms its own isolated segment. Zero inter-item contamination. | **PASSED** |
| **7. Multiline Tables / OCR Text** | `"Metoprolol 25 mg oral\nLisinopril 10 mg oral\nAspirin 81 mg oral"` | 3 | Line breaks isolate rows. Each medication and its attributes remain on their own row group. | **PASSED** |
| **8. Empty Segments & Repeated Delimiters** | `"Metoprolol 25 mg...\n\n;;  \nLisinopril 10 mg."` | 2 | Multiple dots, newlines, and semicolons collapsed safely. Zero empty segments emitted. | **PASSED** |
| **9. Spans Immediately Adjacent to Boundaries** | `"Metoprolol 25 mg.Lisinopril 10 mg"` | 2 | Period immediately followed by uppercase letter (`.L`) splits cleanly into 2 segments. | **PASSED** |
| **10. Mixed Med and Lab Claims** | `"Glucose was 95 mg/dL. Metoprolol 25 mg prescribed."` | 2 | Lab value in segment 0 (`L_0`); Metoprolol in segment 1 (`M_1`). Zero cross-domain leakage. | **PASSED** |
| **11. Multiple Clauses Within a Sentence** | `"Patient took Metoprolol 25 mg; however, Lisinopril 10 mg was stopped."` | 2 | Semicolon divides sentence into 2 clauses. Metoprolol and Lisinopril strictly separated. | **PASSED** |
| **12. Heading Followed by Separate Statement** | `"CURRENT MEDICATIONS:\nMetoprolol 25 mg daily\nDISCHARGE:\nLisinopril 10 mg"` | 4 | Heading lines isolated from medication statements. | **PASSED** |
| **Permutation Invariance** | Multi-sentence permutations ($4! = 24$ candidate orderings) | 2 | Invariant normalized claims, evidences, and group relationships across all permutations. | **PASSED** |

**Summary**: 13 passed in 0.14s.

---

## 5. Full Regression & Repository Verification

### 5.1 Extraction, Boundary, and FHIR Safety Test Suites
```bash
pytest tests/test_phase523_milestone1_deterministic_grounding.py \
       tests/test_phase523_milestone13_anchor_ordering.py \
       tests/test_phase523_milestone2_segmentation.py \
       tests/test_phase52_medication_coreference.py \
       tests/test_phase5113_fixture_isolation_and_diagnostics.py \
       tests/test_extraction_subsystem.py \
       tests/test_canonical_fhir_mapping.py \
       tests/test_fhir_export.py \
       tests/test_pd022_qualitative_labs.py \
       tests/test_phase511_export_safety.py -q
```
**Result**: **335 passed, 2 warnings in 15.45s**.

### 5.2 Full Repository Test Suite (`pytest -q`)
```
=========================== short test summary info ===========================
FAILED tests/test_cli.py::test_cli_unsupported_extension_fails - assert 0 == 1
FAILED tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling
FAILED tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation - Assertion...
FAILED tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline
FAILED tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification
=================== 5 failed, 1346 passed, 11 warnings in 265.49s ====================
```
- **Passed Tests**: **1,346** (increased from 1,333 baseline by exactly +13 new Milestone 2 tests).
- **Known Baseline Failures**: **5** (100% pre-existing, zero newly introduced failures).
- **Regression Count**: **0**.

---

## 6. Strict Architectural Boundaries

- **No Milestone 3 syntactic pairing implemented**: Pre-nominal attributes (e.g. `"Oral"` before `"Metoprolol"`) remain safely unanchored as `__no_anchor__` when starting a segment. No premature forward or backward guessing was added.
- **No fuzzy matching or LLM association introduced**: Grounding remains 100% deterministic and coordinate-based.
- **Ground truth and benchmark labels untouched**: Zero edits to `tests/fixtures/`, `benchmarks/`, or metrics.
- **Frozen Phase 4 files untouched**: FHIR export models and safety gates remain pristine.
- **No staging, commits, or pushes performed**.

---

## 7. Remaining Limitations & Milestone 3 Readiness

### Remaining Limitation
Within an isolated segment:
`"Oral Metoprolol 25 mg daily"`
`"Oral"` is in the same segment as `"Metoprolol"`, but because `"Oral"` is at index 0 and `"Metoprolol"` is at index 5, the strictly forward-streaming state machine gives `"Oral"` `group_id = "__no_anchor__"`.

### Milestone 3 Roadmap (Clause-Level Syntactic Pairing)
With Milestone 2's sentence and clause boundaries now firmly established:
1. Each segment forms an isolated syntactic container.
2. Inside that container, Milestone 3 will implement deterministic syntactic pairing rules:
   - Pre-nominal routes, forms, and dosages bind forward to the head medication anchor within the same clause.
   - Coordinate medications (e.g., `"Metoprolol and Lisinopril were both taken orally"`) bind shared attributes according to coordinate coordination rules.
   - Ambiguous attributes remain quarantined with review flags.

---

## Conclusion
Milestone 2 is complete, fully verified, and regression-free. We stop here and await user review before proceeding to Milestone 3.
