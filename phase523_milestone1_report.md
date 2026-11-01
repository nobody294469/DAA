# Phase 5.2.3 — Milestone 1 Implementation Report

**Milestone:** Milestone 1 — Deterministic Extraction Ordering & Evidence Grounding  
**Status:** COMPLETED  
**Production Code Scope:** `src/clindoc/extract/llm_extractor.py` (ONLY)  
**Test Suite Created:** `tests/test_phase523_milestone1_deterministic_grounding.py`  

---

## 1. Overview & Objectives Achieved

Milestone 1 successfully eliminates the nondeterministic ordering defects and naive substring search limitations of the previous extractor implementation:

1. **Precomputation and Grounding Before Grouping:** Source evidence spans are now validated and grounded before any medication or laboratory entity grouping logic executes.
2. **Deterministic Coordinate Sorting:** Extractions are sorted deterministically by source coordinates `(local_start, local_end)` prior to sequential group ID assignment, guaranteeing that grouping partitions are mathematically invariant to the order of items in the LLM JSON response.
3. **Fail-Closed Ambiguity Quarantine:** Extractions referencing phrases that occur multiple times in the source text without explicit offsets fail closed rather than silently selecting the first occurrence.
4. **Anchor-Prioritized Tie-Breaking:** Ties at identical offsets place anchor fields (`med.name`, `lab.value`) before attribute fields (`med.dose`, `med.route`, `med.frequency`), ensuring attributes at the same offset inherit the correct entity identity.
5. **Word-Boundary Token Precision:** Occurrence matching incorporates word/token boundaries (`(?<!\w)`, `(?!\w)`), preventing false substring collisions (e.g. preventing `"5 mg"` from erroneously matching inside `"25 mg"`).

---

## 2. Files Changed

### Production File (Allowed Scope)
- **`src/clindoc/extract/llm_extractor.py`**
  - Refactored `_build_claims()` to implement a 5-step deterministic grounding and grouping architecture:
    1. *Candidate Pre-Validation*: Validates `field_type` against `PERMITTED_NARRATIVE_FIELDS` and verifies non-empty `value` and `evidence_text`.
    2. *Explicit Offset Validation*: If `char_start`/`char_end` or `local_start`/`local_end` are provided, validates bounds within `region_text` and validates verbatim or whitespace-normalized text match. Fails closed on out-of-bounds, non-integer, or text mismatch.
    3. *Missing Offset Search*: If offsets are omitted, performs word-boundary and whitespace-normalized occurrence searches. Unambiguous spans ($M=1$) ground reliably. Multiple occurrences ($M>1$) fail closed unless all $K$ candidate extractions are identical and match $M=K$ occurrences 1-to-1.
    4. *Deterministic Multi-Key Sorting*: Sorts grounded candidates by `(local_start, local_end, field_priority, field_type, value, unit_raw, -confidence)`.
    5. *Group ID Assignment & Canonical Objects*: Sequentially processes sorted extractions with `current_med_group`, `current_lab_group`, and `__no_anchor__` sentinels, emitting canonical `Evidence` and `Claim` records.

### Test Files Added
- **`tests/test_phase523_milestone1_deterministic_grounding.py`**
  - Implements all 8 mandatory tests specified in the prompt plus coordinate tie-breaking verification (9 tests total, all passing).

---

## 3. Implementation Details & Architecture

### 3.1 Five-Stage Processing Pipeline in `_build_claims()`

```
  [Candidate Extractions from LLM]
                 │
                 ▼
 ┌───────────────────────────────┐
 │ Stage 1: Candidate Validation │ ──► Reject unsupported fields, empty values
 └───────────────────────────────┘
                 │
                 ▼
 ┌───────────────────────────────┐
 │ Stage 2: Evidence Grounding   │
 │ - Explicit Offsets: Bounds &  │ ──► Reject offset mismatches / out-of-bounds
 │   text match validation       │
 │ - Missing Offsets: Token-     │ ──► Reject ambiguous multi-occurrences
 │   aware occurrence search     │
 └───────────────────────────────┘
                 │
                 ▼
 ┌───────────────────────────────┐
 │ Stage 3: Deterministic Sort   │
 │   Key: (start, end, priority, │ ──► Mathematically invariant to JSON order
 │         field, value, conf)   │
 └───────────────────────────────┘
                 │
                 ▼
 ┌───────────────────────────────┐
 │ Stage 4: State-Machine Group  │
 │ - med.name -> new UUID        │
 │ - Attributes inherit anchor   │
 │ - Leading orphan -> sentinel  │
 └───────────────────────────────┘
                 │
                 ▼
 ┌───────────────────────────────┐
 │ Stage 5: Canonical Emission   │ ──► Emits (all_evidences, all_claims)
 └───────────────────────────────┘
```

### 3.2 Coordinate Tie-Breaking Specification
When multiple candidate extractions share identical `(local_start, local_end)` coordinates, ties are broken deterministically by anchor priority:

```python
def _field_priority(ft: str) -> int:
    if ft == "med.name":
        return 0  # Medication anchor
    if ft in ("med.dose", "med.route", "med.frequency", "med.duration"):
        return 1  # Medication attributes
    if ft == "lab.value":
        return 0  # Laboratory anchor
    if ft == "lab.flag":
        return 1  # Laboratory attribute
    return 2      # Other permitted fields
```

This guarantees that an attribute beginning at the same character as a drug name (e.g. extracted from `"Metoprolol 25 mg"`) is processed after `med.name`, inheriting the freshly instantiated entity `group_id` instead of remaining unanchored.

---

## 4. Evidence Grounding & Safety Invariants

| Scenario | Old Behavior | Milestone 1 Deterministic Behavior | Safety Status |
| :--- | :--- | :--- | :--- |
| **Random LLM Output Order** | Grouping depended on order of keys in JSON response | Pre-sort by `(char_start, char_end)` ensures 100% permutation invariance | **SAFE** |
| **Multi-Occurrence Phrase** | Silently took first occurrence via `find()` | Fails closed with diagnostic `ambiguous evidence span` if un-offset | **SAFE** |
| **Sub-token Collision** | `"5 mg"` matched inside `"25 mg"` | Word boundary `(?<!\w)` prevents sub-digit matching | **SAFE** |
| **Invalid Offset Bounds** | Ignored; substring searched from start | Validates bounds and text; fails closed with rejection diagnostic | **SAFE** |
| **Overlapping Valid Spans** | Undefined tie order | Both spans preserved; distinct `Evidence` and `Claim` generated | **SAFE** |
| **Leading Orphan Attribute** | Attached to preceding group or undefined | Receives `__no_anchor__` sentinel; quarantined fail-closed | **SAFE** |

---

## 5. Mandatory Tests & Validation Results

### 5.1 Milestone 1 Test Suite (`tests/test_phase523_milestone1_deterministic_grounding.py`)

All 9 dedicated test cases passed in 0.12s:

1. `test_1_random_permutations_produce_identical_claims_and_grouping`: Tested across 30 random shuffles with distinct random seeds. Verified byte-for-byte identical claim types, values, evidence spans, and grouping partitions.
2. `test_2_duplicate_evidence_spans`: Verified duplicate extractions targeting identical character offsets are preserved and correctly grounded.
3. `test_3_repeated_identical_evidence_phrases_at_different_positions`: Verified that multiple occurrences of the same drug name at different document positions with explicit offsets ground to their exact occurrences.
4. `test_4_partially_overlapping_spans`: Verified that overlapping spans (e.g. `"Tab Metacin"` vs `"Metacin"`) are preserved.
5. `test_5_missing_or_invalid_source_offsets`: Verified fail-closed rejection for out-of-bounds offsets, text mismatches, and ambiguous un-offset multi-occurrences.
6. `test_6_unmatched_evidence_text`: Verified fail-closed diagnostic rejection when evidence text does not exist in source text.
7. `test_7_multiple_fields_referencing_same_source_phrase`: Verified multiple distinct fields referencing the same span are preserved and grouped.
8. `test_8_existing_unambiguous_grouping_behavior_unchanged`: Verified full clinical note with multi-medication and multi-lab blocks maintains correct distinct grouping.
9. `test_9_deterministic_tie_breaking_at_identical_coordinates`: Verified `med.name` prioritizes before `med.dose` when both share identical character start.

### 5.2 Regression & Benchmark Test Results

```bash
# 1. Milestone 1 test suite
.venv\Scripts\pytest tests/test_phase523_milestone1_deterministic_grounding.py
# Result: 9 passed in 0.12s

# 2. Phase 5.2 Co-reference & Boundary suite
.venv\Scripts\pytest tests/test_phase52_medication_coreference.py tests/test_medication_boundaries.py
# Result: 38 passed in 0.32s

# 3. Narrative Benchmark suite (20 documents)
.venv\Scripts\pytest tests/test_narrative_benchmark.py
# Result: 18 passed in 1.38s

# 4. Core extraction & whitespace grounding suite
.venv\Scripts\pytest tests/test_extraction_subsystem.py tests/test_compound_medications.py tests/test_whitespace_grounding.py tests/test_phase5113_fixture_isolation_and_diagnostics.py
# Result: 244 passed in 1.95s

# 5. Full combined milestone regression run (309 items)
.venv\Scripts\pytest tests/test_phase52_medication_coreference.py tests/test_narrative_benchmark.py tests/test_phase523_milestone1_deterministic_grounding.py tests/test_extraction_subsystem.py tests/test_compound_medications.py tests/test_whitespace_grounding.py tests/test_medication_boundaries.py tests/test_phase5113_fixture_isolation_and_diagnostics.py
# Result: 309 passed in 2.19s

# 6. Full repository test suite
.venv\Scripts\pytest -q
# Result: 5 failed (known pre-existing baseline failures), 1,327 passed in 269.67s
```

---

## 6. Remaining Limitations & Boundaries

Milestone 1 addresses **deterministic extraction ordering, source coordinate grounding, and evidence validation**. The following capabilities belong to subsequent milestones and were **not** started:

1. **Milestone 2 (Pending Approval):** Sentence and clause segmentation with negative-lookahead protection for 85+ Latin clinical sigs (`b.i.d.`, `p.o.`, `q.d.`).
2. **Milestone 3 (Pending Approval):** Layered Association Rules (Rules 1–6) for coordinate constructions, distributive phrases (`"each at their prescribed dose"`), and bijective pairing with quarantine for `"respectively"`.
3. **Milestone 4 (Pending Approval):** Export safety gate integration in `FHIRExportService` and `claims_to_fhir_bundle()`, requiring Mode 0 clinician confirmation for coordinate associations.

---

## 7. Confirmation of Constraints

- [x] Implemented ONLY Milestone 1.
- [x] Production code change strictly limited to `src/clindoc/extract/llm_extractor.py`.
- [x] Zero changes to `FHIRExportService`, FHIR mappers, ground truth, or frozen Phase 4 tests.
- [x] No clause segmentation or coordinate association logic started.
- [x] No files staged, committed, or pushed.
- [x] Workspace preserved intact.

*Awaiting user review and approval before proceeding to Milestone 2.*
