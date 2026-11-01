# ClinicalDoc — Phase 5.2.3 Sub-Milestone 4.3 Implementation Report
## FHIR Medication Regimen Mapping Hardening (Split Dosing, Sequential Tapers, & Conformance)

**Execution Date:** 2026-10-31  
**Scope Authority:** ClinicalDoc Phase 5.2.3 Sub-Milestone 4.3 Specification  
**Status:** Completed & Validated  

---

### 1. Executive Summary & Scope Compliance

Sub-Milestone 4.3 hardens FHIR R4B medication regimen mapping within `clindoc`. Clinical documentation frequently expresses complex regimens that cannot be represented as a single static dose without risking severe clinical misinterpretation. This milestone delivers deterministic, standards-compliant FHIR R4B transformations for:
1. **Split Dosing Regimens**: E.g., *Carvedilol 6.25 mg every morning, 12.5 mg at bedtime*. Represented within a single `MedicationRequest` or `MedicationStatement` with multiple concurrent `dosageInstruction` elements (`sequence: 1`), preserving distinct doses, timing codes, and element-level provenance without loss of timing or dose merging.
2. **Sequential Taper Regimens**: E.g., *Prednisone 20 mg once daily for 5 days, then 10 mg once daily*. Represented with chronological `sequence` ordering (`sequence: 1`, `sequence: 2`), bound by explicit UCUM durations in `timing.repeat.boundsDuration` for bounded phases, while omitting fabricated bounds durations or speculative start dates for subsequent step-down phases.
3. **Competing Slot & Fail-Closed Safeguards**: Preserves deterministic candidate slot selection (`select_deterministic_slot_candidate`) for unslotted competing candidates, emits `EXT_COMPETING_CANDIDATE` provenance extensions on shadowed claims, suppresses invalid resources when core medication names or doses are missing, and strictly preserves W-1 (complex parentheticals) and W-2 (multi-page spanning) fail-closed boundaries.

#### Strict Scope Compliance
Production modifications were strictly confined to authorized areas in:
- [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py)
  - `select_deterministic_slot_candidate`
  - `_build_dose_and_rate`
  - `_build_timing_object`
  - `_get_claim_pos`
  - `_build_regimen_dosages`
  - `map_medication_request`
  - `map_medication_statement`
  - Medication mapping loop in `claims_to_fhir_bundle`

#### Frozen Modules Compliance
The following critical components remained completely untouched and frozen:
- [`src/clindoc/review/service.py`](file:///d:/daa/src/clindoc/review/service.py) (FROZEN — Verified untouched)
- [`src/clindoc/services/fhir_export.py`](file:///d:/daa/src/clindoc/services/fhir_export.py) (FROZEN — Verified untouched)
- [`src/clindoc/extract/medication_association.py`](file:///d:/daa/src/clindoc/extract/medication_association.py) (FROZEN — Verified untouched)
- [`src/clindoc/extract/segmentation.py`](file:///d:/daa/src/clindoc/extract/segmentation.py) (FROZEN — Verified untouched)
- [`src/clindoc/extract/deterministic.py`](file:///d:/daa/src/clindoc/extract/deterministic.py) (FROZEN — Verified untouched)
- [`src/clindoc/verify/stage_a.py`](file:///d:/daa/src/clindoc/verify/stage_a.py) (FROZEN — Verified untouched)
- [`src/clindoc/ocr/layout.py`](file:///d:/daa/src/clindoc/ocr/layout.py) (FROZEN — Verified untouched)
- [`src/clindoc/fhir/constants.py`](file:///d:/daa/src/clindoc/fhir/constants.py) (FROZEN — Verified untouched)
- Extraction configurations (`configs/extraction.yaml`, vocabularies, schemas) (FROZEN — Verified untouched)

#### Unresolved Clinical-Export Security Blocker Notice
As established during the Sub-Milestone 4.2.1 Security Verification Audit:
> [!IMPORTANT]
> **Multi-Hop Four-Eyes Ancestry Limitation**: The current Four-Eyes separation of duties implementation tracks immediate predecessor version authorship (`created_by`, `operator_id`, `last_modifier_id`), but does not traverse deep ancestor chains across multi-hop successive edits (e.g., Reviewer A creates v1, Reviewer B modifies to v2, Reviewer A accepts v3). This limitation remains explicitly tracked as an **unresolved clinical-export security blocker** pending root-level provenance DAG traversal authorization.

---

### 2. Production Code Modifications

All changes were implemented directly in [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py):

#### A. Imports & FHIR R4B Domain Models
Imported `Duration` from `fhir.resources.R4B.duration` and `TimingRepeat` from `fhir.resources.R4B.timing` to construct fully typed, schema-valid FHIR R4B timing bounds conforming to HL7 standards.

#### B. Slot Determination & Regular Expressions
Added clinical regimen transition and slot-targeting regexes:
- `TAPER_TRANSITION_REGEX = re.compile(r"\b(?:then|taper(?:ing)?\s+to|step\s*down\s*to|followed\s+by|afterwards)\b", re.IGNORECASE)`
- `MORNING_REGEX = re.compile(r"\b(?:morning|qam|in\s+the\s+morning|am)\b", re.IGNORECASE)`
- `BEDTIME_REGEX = re.compile(r"\b(?:bedtime|qhs|at\s+bedtime|night|pm|evening)\b", re.IGNORECASE)`
- `SPLIT_REGEX = re.compile(r"\b(?:morning|qam|bedtime|qhs|evening|night)\b", re.IGNORECASE)`

#### C. `_build_dose_and_rate` Helper
Constructs FHIR R4B `DosageDoseAndRate` with:
- Standard UCUM quantity mapping (`Quantity(value=..., unit=..., system=UCUM_SYSTEM, code=...)`).
- Element-level provenance extension (`EXT_ELEMENT_PROVENANCE`) linking the dose quantity directly to the source claim's character offsets and evidence text.
- Eliminates zero-dose fallbacks: unparsed or empty dose amounts fail closed by returning `None` instead of inventing default values.

#### D. `_build_timing_object` Helper
Constructs FHIR R4B `Timing` objects with:
- Structured timing frequency / period code (`CodeableConcept`).
- Explicit duration bounds (`TimingRepeat(boundsDuration=Duration(value=..., unit=..., system=UCUM_SYSTEM, code=...))`).
- Supported UCUM duration code mapping: `d` (days), `wk` (weeks), `mo` (months), `h` (hours).
- Element-level provenance extension linking the timing instruction directly to the source claim.

#### E. `_get_claim_pos` Helper
Robustly resolves claim token offsets within document evidence text.
- To prevent digit collision between numerical doses and duration intervals (e.g. searching for `"2"` in a text containing `"2 mg"` and `"for 2 weeks"`), the helper performs **unit-aware regex matching** (e.g. `\b2\s*mg\b`) prior to bare token fallback.
- Guarantees accurate partition boundary computation for multi-dose sentences.

#### F. `_build_regimen_dosages` Core Engine
Analyzes medication claim groupings and structures `dosageInstruction` elements:
1. **Sequential Taper Detection**:
   - Detects step-down transition markers (`then`, `taper to`, `step down to`) and partitions doses and durations into chronological phases.
   - Assigns ascending sequence integers: `sequence: 1`, `sequence: 2`, etc.
   - Extracts duration claims corresponding to phase windows and sets `boundsDuration`.
   - Leaves subsequent phases unbounded when no duration is specified in source text (avoids fabricating speculative dates/durations).
2. **Split Dosing Detection**:
   - Identifies distinct timing anchors (e.g., morning vs bedtime) associated with separate doses.
   - Emits concurrent dosage instructions with identical sequence: `sequence: 1` for both morning and evening components.
   - Slices localized text windows around each dose to prevent regex keyword contamination across clauses.
3. **Competing Slot Selection Fallback**:
   - Where multiple dose candidates compete for the same slot (or unslotted default), calls `select_deterministic_slot_candidate` to choose the highest-confidence deterministic candidate.
   - Marks shadowed competing claims with `EXT_COMPETING_CANDIDATE` provenance extensions.

#### G. Public Function Contracts
Implemented standalone, callable public mapping functions:
- `map_medication_request(med_claims, doc_id, context) -> Optional[MedicationRequest]`
- `map_medication_statement(med_claims, doc_id, context) -> Optional[MedicationStatement]`

Both functions delegate to `_build_regimen_dosages`, establish bundle-level and element-level provenance, set standard resource metadata (`status`, `intent`, `subject`), and enforce strict fail-closed suppression if medication name or valid dose is absent.

#### H. Bundle Integration
Updated the medication mapping section in `claims_to_fhir_bundle` to utilize `_build_regimen_dosages`, `map_medication_request`, and `map_medication_statement`, standardizing resource creation across batch bundle processing and single-resource mapping.

---

### 3. Split Dosing & Sequential Taper Semantics

#### HL7 FHIR R4B Dosage Concurrency Standard
In HL7 FHIR R4B, the `Dosage.sequence` integer defines the order in which dosage instructions should be applied:
- **Concurrent Instructions**: When dosage instructions have identical sequence integers (e.g. `sequence: 1`), consuming EHR systems interpret them as active concurrently. Split dosing (e.g. morning and evening doses) must therefore assign `sequence: 1` to both instructions.
- **Sequential Instructions**: When dosage instructions have ascending sequence integers (e.g. `sequence: 1` followed by `sequence: 2`), consuming systems interpret them as phased steps in a taper or titration regimen.

#### Concrete Mapping Examples

##### 1. Split Dosing: Carvedilol 6.25 mg morning, 12.5 mg bedtime
```json
{
  "resourceType": "MedicationRequest",
  "id": "medrx-carvedilol-1",
  "status": "active",
  "intent": "order",
  "medicationCodeableConcept": {
    "coding": [
      {
        "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
        "code": "203158",
        "display": "Carvedilol"
      }
    ]
  },
  "dosageInstruction": [
    {
      "sequence": 1,
      "text": "6.25 mg every morning",
      "timing": {
        "code": {
          "coding": [{ "system": "http://terminology.hl7.org/CodeSystem/v3-GTSAbbreviation", "code": "QAM" }]
        }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 6.25,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg"
          }
        }
      ]
    },
    {
      "sequence": 1,
      "text": "12.5 mg at bedtime",
      "timing": {
        "code": {
          "coding": [{ "system": "http://terminology.hl7.org/CodeSystem/v3-GTSAbbreviation", "code": "QHS" }]
        }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 12.5,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg"
          }
        }
      ]
    }
  ]
}
```
*Guarantees*: Emits 1 `MedicationRequest`, 2 `dosageInstruction` elements, `sequence: 1` on both, preserving independent doses and timing codes without dose merging or loss of timing.

##### 2. Sequential Taper: Prednisone 20 mg daily for 5 days, then 10 mg daily
```json
{
  "resourceType": "MedicationRequest",
  "id": "medrx-prednisone-1",
  "status": "active",
  "intent": "order",
  "medicationCodeableConcept": {
    "coding": [
      {
        "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
        "code": "8640",
        "display": "Prednisone"
      }
    ]
  },
  "dosageInstruction": [
    {
      "sequence": 1,
      "text": "20 mg daily for 5 days",
      "timing": {
        "repeat": {
          "boundsDuration": {
            "value": 5,
            "unit": "days",
            "system": "http://unitsofmeasure.org",
            "code": "d"
          }
        },
        "code": {
          "coding": [{ "system": "http://terminology.hl7.org/CodeSystem/v3-GTSAbbreviation", "code": "QD" }]
        }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 20,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg"
          }
        }
      ]
    },
    {
      "sequence": 2,
      "text": "10 mg daily",
      "timing": {
        "code": {
          "coding": [{ "system": "http://terminology.hl7.org/CodeSystem/v3-GTSAbbreviation", "code": "QD" }]
        }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 10,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg"
          }
        }
      ]
    }
  ]
}
```
*Guarantees*: Sequential phases ordered chronologically (`sequence: 1`, `sequence: 2`). Phase 1 preserves duration in `boundsDuration`. Phase 2 omits fabricated duration bounds or dates. Zero dose summation (does NOT imply 30 mg daily).

---

### 4. Verification Test Results Matrix

#### A. Focused Sub-Milestone 4.3 Suite (`tests/test_phase523_milestone4_3.py`)
14 focused tests verifying every requirement under Sub-Milestone 4.3:

| Test Case | Description | Result |
| :--- | :--- | :--- |
| `test_split_dosing_single_medication_request` | Verifies Carvedilol 6.25mg QAM and 12.5mg QHS maps to 1 MedicationRequest with 2 concurrent `sequence=1` instructions | **PASSED** |
| `test_split_dosing_single_medication_statement` | Verifies split dosing on MedicationStatement yields 2 concurrent `sequence=1` instructions | **PASSED** |
| `test_split_dosing_competing_candidates_in_single_slot` | Verifies deterministic slot selection chooses the higher confidence candidate when competing for a slot | **PASSED** |
| `test_sequential_taper_two_phases_with_duration` | Verifies Prednisone 20mg daily for 5d then 10mg daily produces `sequence=1` (with `boundsDuration`) and `sequence=2` | **PASSED** |
| `test_sequential_taper_multi_phase_with_weeks_duration` | Verifies multi-phase taper (40mg x 2wk, 20mg x 2wk, 10mg) with weeks UCUM duration mapping (`code="wk"`) | **PASSED** |
| `test_sequential_taper_missing_duration_does_not_fabricate_bounds` | Verifies sequential taper with no stated duration leaves `boundsDuration=None` on all phases | **PASSED** |
| `test_unslotted_competing_candidates_deterministic_resolution` | Verifies unslotted dose candidates resolve via `select_deterministic_slot_candidate` and emit shadowed provenance | **PASSED** |
| `test_sliding_scale_or_as_needed_without_zero_fallback` | Verifies PRN / sliding scale regimens preserve instructions without falling back to zero doses | **PASSED** |
| `test_explicit_zero_dose_preserved` | Verifies explicit zero doses (e.g., "0 units") are preserved when verified rather than rejected | **PASSED** |
| `test_missing_medication_name_or_dose_suppression` | Verifies claims without medication name or valid dose fail closed and emit no invalid FHIR resources | **PASSED** |
| `test_cross_medication_contamination_suppression` | Verifies distinct medication claim groups never leak doses across entities | **PASSED** |
| `test_parenthetical_w1_fail_closed_preservation` | Verifies complex parentheticals (W-1 fail-closed) do not fabricate erroneous split doses | **PASSED** |
| `test_single_dose_standard_regimen_regression_parity` | Verifies single-dose standard regimens retain identical output structure to baseline mapper | **PASSED** |
| `test_direct_mapper_functions_contract` | Verifies public function contracts for `map_medication_request` and `map_medication_statement` | **PASSED** |

**Sub-Milestone 4.3 Focused Suite Total: 14 passed in 0.20s (100% pass rate).**

#### B. Related FHIR Mapping & Export Regression Suites
- `tests/test_canonical_fhir_mapping.py`: **11 passed**
- `tests/test_phase523_milestone4_1.py`: **7 passed**
- `tests/test_phase523_milestone4_2.py`: **20 passed**
- `tests/test_fhir_export.py`: **16 passed**
- `tests/test_phase44_diagnostic_report_grouping.py`: **13 passed**
- `tests/test_phase441_conformance_audit.py`: **10 passed**
- `tests/test_phase442_export_integrity.py`: **16 passed**
- **Combined FHIR Mapping & Export Total: 107 passed in 2.53s (100% pass rate).**

#### C. Full Repository Test Suite Regression
- Total Tests: **1,452 collected**
- Passing: **1,447 passed** (including +14 newly implemented Sub-Milestone 4.3 tests)
- Exact Failures: **5 failed** (100% matched to reconciled historic baseline):
  1. `tests/test_cli.py::test_cli_unsupported_extension_fails` (Known pre-existing failure)
  2. `tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling` (Known pre-existing failure)
  3. `tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation` (Known pre-existing failure)
  4. `tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline` (Known pre-existing failure)
  5. `tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification` (Known pre-existing failure)
- **New Regressions: EXACTLY ZERO (0).**

---

### 5. Benchmark Impact Assessment

- **Benchmark Denominator Drift**: **EXACTLY ZERO (0)**.
- Regimen mapping occurs downstream during FHIR resource generation from grouped claims. Upstream claim extraction, token-level character spans, bounding boxes, segmentation boundaries, and ground-truth evaluation metrics are unaffected.
- Standard single-dose medication claims produce identical FHIR resources, preserving regression parity across all existing clinical test documents.

---

### 6. Architectural Boundary & Git Hygiene

- **Production Modifications**: Restricted strictly to [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py).
- **Test File Created**: [`tests/test_phase523_milestone4_3.py`](file:///d:/daa/tests/test_phase523_milestone4_3.py).
- **Git State**: Zero git commits, stages, pushes, or resets performed. All pre-existing modified and untracked files preserved.
- **Sub-Milestone 4.4 Status**: **NOT AUTHORIZED / NOT STARTED**. Halting execution pending user review.
