# PROPOSED PRODUCT DECISION DRAFT: PD-022
## Adoption of Qualitative Laboratory Determinations as `lab.value` and FHIR R4B `Observation.valueString`

**Status:** PROPOSED ARCHITECTURAL DECISION (DRAFT ONLY — NOT YET MERGED)  
**Target Identifier:** `PD-022` (Succeeds `PD-021: Strict Out-of-Scope Product Boundaries`)  
**Date:** 2026-10-07  
**Authoritative Context:**  
- Extends: [`PRODUCTIZATION_SPEC.md`](PRODUCTIZATION_SPEC.md) (§4.3, §15.2)  
- Amends Decision Baseline: [`PRODUCTIZATION_DECISIONS.md`](PRODUCTIZATION_DECISIONS.md)  
- Aligns With Research Spec: [`PROJECT_SPEC.md`](PROJECT_SPEC.md) (§8.3, §15.2)  
- Prior Audit: [`reports/QUALITATIVE_LAB_CONTRACT_DECISION_AUDIT.md`](reports/QUALITATIVE_LAB_CONTRACT_DECISION_AUDIT.md)

---

## 1. Proposed Decision ID

**`PD-022`**

*(Note: Inspection of `PRODUCTIZATION_DECISIONS.md` confirms that `PD-020` governs Synthetic/Semi-Synthetic Data Governance and `PD-021` governs Strict Out-of-Scope Product Boundaries. The next available decision identifier in the authoritative log is `PD-022`.)*

---

## 2. Decision Title

**PD-022: Representation of Qualitative Laboratory Determinations as `lab.value` and Export via FHIR R4B `Observation.valueString`**

---

## 3. Problem

In the independent frozen holdout evaluation on `Original_Discharge_Summary.pdf` (CMC Vellore), the narrative extraction pipeline failed to extract legitimate qualitative diagnostic determinations:
- `"HBsAg Negative"`
- `"HIV ELISA Negative"`
- `"VDRL Non Reactive"`

Under the audited baseline, `lab.value` was narrowly constrained in extraction prompts and specifications to numeric laboratory measurements (`value_numeric`), while `lab.flag` was restricted to tabular severity indicators (`NORMAL`, `HIGH`, `LOW`, `CRITICAL`).

Because qualitative findings are neither numbers nor severity flags, they fell between the cracks of the extraction contract:
1. Extracting them as `lab.flag` causes semantic distortion, breaks Stage A deterministic token matching, misleads Stage C with tabular abnormal column instructions, empties the primary result column in the review UI, and strips `Observation.value[x]` from exported FHIR.
2. In addition, an audit of the FHIR mapper revealed that any non-numeric `lab.value` currently defaults to numeric `0.0` in `Observation.valueQuantity`—a dangerous safety defect.

A formal product decision is required to define how qualitative laboratory findings are represented, verified, persisted, and exported across the system.

---

## 4. Decision

1. **Dual Representation for `lab.value`:**
   - **Quantitative Laboratory Findings:** Continue to use `lab.value` for the formatted measurement string (e.g. `"14.2"`, `"95"`), populating `value_numeric` (e.g. `14.2`, `95.0`) and `unit_raw` / UCUM `normalized_code` where applicable.
   - **Qualitative Laboratory Findings:** Grounded qualitative determinations shall be represented as `lab.value` (e.g. `"Negative"`, `"Positive"`, `"Non-Reactive"`, `"Reactive"`, `"Detected"`, `"Not Detected"`, `"Equivocal"`). In such claims, `value_numeric`, `unit_raw`, and unit codes MUST be explicitly `null`.

2. **Controlled Vocabulary for Normalized Qualitative Values:**
   - Source evidence spans must always preserve verbatim text via existing `Evidence` grounding (§9).
   - In normalization, qualitative values shall be mapped to a canonical controlled vocabulary of qualitative findings (`NEGATIVE`, `POSITIVE`, `REACTIVE`, `NON_REACTIVE`, `DETECTED`, `NOT_DETECTED`, `EQUIVOCAL`).
   - Arbitrary unstructured free-text commentary (e.g. `"specimen slightly hemolyzed"`, `"see attachment"`) MUST NOT silently become normalized qualitative determinations.

3. **Strict Separation of `lab.flag` Semantics:**
   - `lab.flag` remains strictly reserved for clinical interpretation / abnormality assessments (`NORMAL`, `HIGH`, `LOW`, `ABNORMAL`, `CRITICAL`).
   - The system MUST NOT automatically manufacture or synthesize `lab.flag` values from qualitative findings (e.g. do not automatically synthesize `NORMAL` from `Negative`). `lab.flag` may only be emitted if explicitly and independently grounded in source text.

4. **Coexistence of `lab.value` and `lab.flag`:**
   - `lab.value` and `lab.flag` MAY coexist on the same observation entity when independently supported by the source text.
   - *Example:* For `"HBsAg: Negative (Normal)"`, the pipeline may emit `lab.test_name = "HBsAg"`, `lab.value = "Negative"`, and `lab.flag = "NORMAL"`.

5. **FHIR R4B Export Contract:**
   - Qualitative laboratory findings shall be exported to FHIR R4B as **`Observation.valueString = claim.value`**.
   - `Observation.valueQuantity` is strictly reserved for quantitative results with valid `value_numeric`.
   - In accordance with `PD-021` (SNOMED CT exclusion in MVP), terminology mapping to `Observation.valueCodeableConcept` (e.g. SNOMED CT `260385009` "Negative") is deferred to future post-MVP terminology enhancements.

6. **Zero-Coercion Safety Invariant:**
   - Non-numeric qualitative determinations MUST NEVER be coerced into numeric zero. Converting `"Negative"`, `"Positive"`, or `"Non-Reactive"` into `valueQuantity = 0.0` is strictly prohibited and treated as an unrecoverable pipeline defect.

---

## 5. Rationale

1. **Clinical Semantic Correctness:**
   In clinical medicine, `"Negative"` is the primary diagnostic finding (the *value* of the observation). It is not an interpretation flag. Placing the finding in `lab.value` accurately mirrors clinical documentation.
2. **HL7 FHIR R4B Alignment:**
   HL7 FHIR defines `Observation.value[x]` as the choice element holding the observation result, and defines `Observation.interpretation` as an assessment of that value (e.g. High/Low/Normal). Storing qualitative results in `valueString` complies directly with base FHIR, US Core, and ABDM laboratory profiles. Omitting `value[x]` would cause hospital integration engines to flag the record as empty or incomplete.
3. **Stage A Verifier Alignment:**
   The deterministic Stage A engine in `src/clindoc/verify/stage_a.py` (lines 220–230) already has an established code path for non-numeric `lab.value` verification via exact case-insensitive evidence substring matching. Option B fits the existing verification architecture naturally.
4. **Human Review Clarity:**
   In the review workspace, the medical records reviewer sees `Test Name: HBsAg` and `Value: Negative` in the primary result column, rather than an empty value column with a displaced badge.

---

## 6. Data Model Semantics

### Quantitative vs. Qualitative Claim Attributes:

| Field | Quantitative Lab Claim | Qualitative Lab Claim |
| :--- | :--- | :--- |
| `field_type` | `lab.value` | `lab.value` |
| `value` | String representation of number (`"14.2"`) | Grounded qualitative determination (`"Negative"`) |
| `value_numeric` | `float` (`14.2`) | `None` / `null` |
| `unit_raw` | String (`"g/dL"`) | `None` / `null` |
| `normalized_code` | UCUM code (`"g/dL"`) | `None` / `null` (or qualitative concept code in Phase 9) |
| `code_system` | `"UCUM"` | `None` / `null` |
| `evidence_id` | Foreign key to `Evidence` span | Foreign key to `Evidence` span |
| `group_id` | Shared entity group ID | Shared entity group ID |

---

## 7. Extraction Contract Impact

1. **Extraction Prompt (`src/clindoc/extract/llm_extractor.py`):**
   - Update `lab.value` field description:
     *From:* `"numeric laboratory test result value (e.g. 13.5, 95)"`  
     *To:* `"laboratory test result value, either quantitative numeric (e.g. 13.5, 95) or qualitative determination (e.g. Negative, Positive, Non-Reactive, Reactive, Detected, Not Detected)"`
   - Add explicit qualitative instruction:
     *"For qualitative tests (e.g., viral serologies, antibody screens, qualitative cultures), extract the grounded qualitative determination as lab.value. Do not emit lab.flag unless an explicit interpretation (e.g., NORMAL, ABNORMAL) is independently printed in the document."*
2. **Schema Invariance:**
   - `schemas/claim.json` already defines `value: string` and `value_numeric: number | null`. No JSON Schema changes are required.

---

## 8. Verification Impact

1. **Stage A (Deterministic):**
   - The existing logic in `src/clindoc/verify/stage_a.py` (lines 220–230) verifies non-numeric `lab.value` by entity-scoped substring containment. This design is fully preserved.
2. **Stage B (ModernBERT):**
   - ModernBERT receives the active `lab.value` one-hot auxiliary feature bit and cross-attends to the verbatim qualitative pair `("Negative", evidence_text)`.
3. **Stage C (Qwen Judge):**
   - General verification prompt applies cleanly without interference from tabular severity flag rules.

---

## 9. FHIR R4B Impact

In `src/clindoc/fhir/mapper.py`:
- When mapping an observation group:
  - If `val_claim.value_numeric is not None`: construct `Observation.valueQuantity = Quantity(value=val_claim.value_numeric, unit=unit_str, system=UCUM, ...)`.
  - If `val_claim.value_numeric is None`: construct `Observation.valueString = val_claim.value`.
  - Under no circumstances shall `valueQuantity` be constructed with `0.0` when `value_numeric` is null.
- `Observation.interpretation` is populated **only** if an explicit `flag_claim` is present in the group.

---

## 10. Safety Requirements

1. **Mandatory Prohibition of Numeric Fallback to 0.0:**
   - In `src/clindoc/fhir/mapper.py` line 829, the legacy fallback `num_val = float(m.group()) if m else 0.0` must be replaced with strict branching.
   - Text values like `"Negative"`, `"Positive"`, and `"Non-Reactive"` MUST NOT be converted to `0.0`.
2. **Regex Parsing Guard in Normalizer:**
   - In `src/clindoc/normalize/normalizer.py` lines 181–186, the regex search for numeric values (`[-+]?\d+`) must be guarded so that strings containing digits alongside qualitative words (e.g. `"HIV-1 Negative"`, `"COVID-19 Negative"`) do not inadvertently extract `"1.0"` or `"19.0"` into `value_numeric`.
3. **Fail-Closed Evidence Grounding:**
   - Any qualitative determination that cannot be grounded in the verbatim OCR page tokens must be rejected at extraction time by the evidence-grounding validator.

---

## 11. Backward Compatibility

1. **Existing Numeric Laboratory Claims:**
   - All existing quantitative laboratory claims (`Hemoglobin 14.2 g/dL`, `Glucose 95 mg/dL`) retain identical extraction, normalization, verification, and FHIR export behavior.
2. **Stored Claims in PostgreSQL:**
   - The `claims` table stores `value` as `Text` and `payload` as `JSONB`.
   - Existing records are 100% backward compatible. No database migrations, DDL adjustments, or table rewrites are required.

---

## 12. Required Implementation Changes (When Authorized)

When formally approved for implementation, only the following files will be modified:

1. **`src/clindoc/fhir/mapper.py`:**
   - Replace line 829 fallback `0.0` with a branch emitting `Observation.valueString = val_claim.value`.
2. **`src/clindoc/extract/llm_extractor.py`:**
   - Update prompt description of `lab.value` to allow qualitative determinations and add few-shot exemplars.
3. **`src/clindoc/normalize/normalizer.py`:**
   - Add a check preventing digit extraction when the claim represents an established qualitative finding.
4. **`scratch/run_extraction_experiment.py`:**
   - Synchronize the offline experimental runner's prompt for consistent offline re-evaluation.

---

## 13. Required Tests

Before merging, the following test suites must be created or augmented:
1. `tests/test_fhir_qualitative_observation.py`:
   - Assert that qualitative `lab.value` generates `Observation.valueString`.
   - Assert that `Observation.valueQuantity` is `None` (and strictly not `0.0`).
2. `tests/test_normalizer_qualitative_values.py`:
   - Assert that `"Negative"` and `"HIV-1 Negative"` do not set `value_numeric = 1.0`.
3. `tests/test_stage_a_qualitative_values.py`:
   - Verify deterministic `SUPPORTED` and `DISCREPANCY` verdicts on qualitative values against source text snippets.
4. `tests/test_extraction_subsystem.py`:
   - Verify contract compliance across qualitative and quantitative lab claims.

---

## 14. Required Spec Amendments

The following sections will require textual amendment upon adoption:

1. **`PROJECT_SPEC.md`:**
   - **§8.3 Numeric claims:** Clarify that `value_numeric` MUST be populated when `field_type` is `lab.value` **and** the value represents a parseable numeric quantity, but MUST remain `null` for qualitative determinations.
   - **§15.2 Lab Observation mapping:** Specify that qualitative `lab.value` maps to `Observation.valueString` instead of `Observation.valueQuantity`.
2. **`PRODUCTIZATION_SPEC.md`:**
   - **§4.3 Claim Schema & Data Model:** Document the dual quantitative/qualitative capability of `lab.value`.
   - **§15.2 FHIR Resource Generation Rules:** Explicitly document the choice element rule (`valueQuantity` vs `valueString`).

---

## 15. Required Decision-Log Amendment

Upon adoption, append `PD-022` to [`PRODUCTIZATION_DECISIONS.md`](PRODUCTIZATION_DECISIONS.md) immediately after `PD-021`, and add `PD-022` to the Section 3 Summary Table.

---

## 16. Open Questions Requiring Future Resolution

1. **Controlled Qualitative Lexicon:**
   Should the system restrict qualitative `lab.value` to an approved set (`NEGATIVE`, `POSITIVE`, `REACTIVE`, `NON_REACTIVE`, `DETECTED`, `NOT_DETECTED`, `EQUIVOCAL`), or allow any short clinical finding grounded in the lab table row?
2. **Post-MVP SNOMED CT Mapping:**
   In Phase 9 (Advanced Terminology), will qualitative determinations be mapped to SNOMED CT finding concepts via `Observation.valueCodeableConcept`?

---

## 17. Explicit Non-Goals

1. **NO SNOMED CT Terminology Service in MVP:**
   Per `PD-021`, SNOMED CT integration is strictly out of scope for MVP. Qualitative values shall be exported as `valueString`, not `valueCodeableConcept`.
2. **NO Heuristic Severity Flag Generation:**
   The system will not attempt to deduce clinical normality (e.g. deducing that "Non-Reactive" means "Normal") without explicit source text evidence.
3. **NO Database Migration:**
   Existing PostgreSQL schemas already support this model; no database DDL migration scripts shall be authored.
