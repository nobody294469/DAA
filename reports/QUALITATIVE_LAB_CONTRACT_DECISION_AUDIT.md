# QUALITATIVE LABORATORY CONTRACT & FHIR DESIGN AUDIT
## Architectural and Standards Comparison: Representing Qualitative Laboratory Findings

**Status:** READ-ONLY ARCHITECTURAL DESIGN AUDIT  
**Date:** 2026-10-07  
**Context:** Post-Holdout Resolution for Root Cause B  
**Holdout Findings Triggering Audit:**
- `HBsAg Negative`
- `HIV ELISA Negative`
- `VDRL Non Reactive`

---

## 1. Problem Statement

In the frozen holdout evaluation of the Condition 4 pipeline on `Original_Discharge_Summary.pdf`, the system encountered non-numeric, qualitative laboratory determinations:
1. `"HBsAg Negative"`
2. `"HIV ELISA Negative"`
3. `"VDRL Non Reactive"`

Under the current production extraction instructions (`src/clindoc/extract/llm_extractor.py`), `lab.value` is explicitly defined as:
> `lab.value : numeric laboratory test result value (e.g. 13.5, 95)`

While `lab.flag` is restricted to:
> `lab.flag : interpretation flag (NORMAL, HIGH, LOW, CRITICAL only)`

Because the prompt instructed the extractor that `lab.value` must be numeric and `lab.flag` must be an interpretation severity keyword, Qwen omitted the qualitative determinations entirely or emitted ungrounded fragments.

To resolve Root Cause B without ad-hoc patching, an explicit, evidence-based architectural decision must be made regarding how qualitative determinations should be modeled across the entire data lifecycle:
- **Option A:** Represent qualitative results as `lab.flag` (with `lab.value` absent/null).
- **Option B:** Represent qualitative results as `lab.value` (with `lab.flag` absent/null or used for true severity).

---

## 2. Current Contract & Codebase Baseline

### 2.1 Extraction Contracts & Schemas
- **`schemas/claim.json` (§8, DEC-015, DEC-017):**
  - `field_type`: String enum of exactly 10 canonical fields (`lab.value`, `lab.unit`, `lab.flag`, `lab.reference_range`, `lab.test_name`, `med.name`, `med.dose`, `med.route`, `med.frequency`, `med.duration`).
  - `value`: String (`"Extracted raw value as string"`).
  - `value_numeric`: Optional number or null (`"Parsed numeric value if field_type is numeric (lab.value, med.dose), else null"`).
- **`src/clindoc/extract/contracts.py`:**
  - Mirrors `schemas/claim.json`: `value: str`, `value_numeric: Optional[float] = None`.
- **`PROJECT_SPEC.md` §8.3:**
  - *"Any claim with field_type in {lab.value, med.dose} MUST populate value_numeric after unit normalization is attempted (UCUM), and MUST preserve unit_raw even if normalization fails."*

### 2.2 Terminology Normalization (`src/clindoc/normalize/normalizer.py`)
- Lines 178–186 parse numeric values for `lab.value` and `med.dose`:
  ```python
  elif claim.field_type in ("lab.value", "med.dose"):
      if claim.value_numeric is None:
          m = re.search(r"[-+]?\d+(?:\.\d+)?", claim.value)
          if m:
              try:
                  claim.value_numeric = float(m.group(0))
              except ValueError:
                  pass
  ```
  If `claim.value` contains no digits, `m` is `None` and `claim.value_numeric` remains `None`.

### 2.3 Verification Cascade (`src/clindoc/verify/`)
- **Stage A (`src/clindoc/verify/stage_a.py`):**
  - Line 180 routes `"lab.value"` to `_verify_numeric_value`.
  - Line 183 routes `"lab.flag"` to `_verify_flag`.
  - In `_verify_numeric_value` (lines 220–230), the code **already anticipates non-numeric laboratory values**:
    ```python
    claim_num = self._extract_numeric_value(claim)
    if claim_num is None:
        # Non-numeric lab value (e.g. "positive", "negative")
        if field_val.strip().lower() in eval_text.lower():
            return StageAResult(label="SUPPORTED", confidence=1.0)
        return StageAResult(
            label="DISCREPANCY",
            contradiction_type="value_mismatch",
            confidence=1.0,
            details={"expected": field_val, "evidence_text": eval_text},
        )
    ```
  - In `_verify_flag` (lines 377–413), verification checks token membership against:
    - `FLAG_HIGH_TOKENS` (`{"h", "high", "hh", "crit high", "critical high", "+"}`)
    - `FLAG_LOW_TOKENS` (`{"l", "low", "ll", "crit low", "critical low", "-"}`)
    - `FLAG_NORMAL_TOKENS` (`{"normal", "norm", "wds", "wnl", "within normal limits"}`)
    - `FLAG_ABNORMAL_TOKENS` (`{"a", "abnormal", "abn", "*"}`)
    Tokens like `"negative"`, `"positive"`, `"non-reactive"` are unrecognized and return `None`, causing `_verify_flag` to return `StageAResult(label=None, confidence=0.0)` (deferral to Stage B).

### 2.4 Database Persistence (`src/clindoc/db/models.py`)
- `Claim` table:
  - `field_type`: `Mapped[str] = mapped_column(String(100))`
  - `value`: `Mapped[str] = mapped_column(Text)`
  - `payload`: `Mapped[dict[str, Any]] = mapped_column(JSONB)`
  - There is no SQL `value_numeric` column; numeric values reside solely in JSONB `payload`.

### 2.5 FHIR R4B Mapper (`src/clindoc/fhir/mapper.py`)
- Lines 825–830 map `lab.value` to FHIR `Observation.valueQuantity`:
  ```python
  val_quantity = None
  if val_claim:
      num_val = val_claim.value_numeric
      if num_val is None:
          m = re.search(r"[-+]?\d*\.?\d+", val_claim.value)
          num_val = float(m.group()) if m else 0.0
  ```
  Lines 934–938 construct `Observation`:
  ```python
  obs_res = Observation(
      id=obs_id,
      status=obs_status,
      ...
      valueQuantity=val_quantity,
      interpretation=interpretations,
      referenceRange=ref_ranges,
      ...
  )
  ```
  `Observation` currently only assigns `valueQuantity`. It has no mapping to `valueString` or `valueCodeableConcept`.

---

## 3. Option A — Qualitative Results as `lab.flag`

### 3.1 Conceptual Model
- For qualitative tests:
  - `lab.value` = absent / null
  - `lab.flag` = `"NEGATIVE"` | `"POSITIVE"` | `"REACTIVE"` | `"NON-REACTIVE"`
- Numeric tests continue as:
  - `lab.value` = `"14.2"` (with `value_numeric: 14.2`)
  - `lab.flag` = `"NORMAL"` | `"HIGH"` | `"LOW"`

### 3.2 Detailed Lifecycle Trace

#### Extraction
- **Prompt:** Redefine `lab.flag` to permit qualitative determination strings (`NEGATIVE`, `POSITIVE`, `REACTIVE`, `NON-REACTIVE`). Add rules instructing the model to suppress `lab.value` when no numeric measurement exists.
- **Schema:** Fits in `schemas/claim.json` since `field_type="lab.flag"` is valid and `value` is string.
- **Caveat:** Extractor must know not to output both `lab.value` and `lab.flag` for qualitative determinations, or suppress `lab.value` via prompt instructions.

#### Normalization
- Bypasses numeric parsing in `src/clindoc/normalize/normalizer.py` lines 178–186.
- `unit_raw` and `value_numeric` remain null.
- Terminology normalization currently has no vocabulary table for `lab.flag`, so `normalized_code` remains null.

#### Verification
- **Stage A (`stage_a.py`): BROKEN without code modifications.**
  - `_verify_flag` calls `_categorize_flag`.
  - `"negative"`, `"positive"`, `"reactive"`, `"non-reactive"` return `None`.
  - Because `claim_cat` is `None`, Stage A returns `StageAResult(label=None, confidence=0.0)`.
  - Stage A fails to deterministically verify qualitative flags, forcing 100% of them into Stage B.
  - If `"negative"` were added to `FLAG_NORMAL_TOKENS`, it conflates clinical finding with abnormality status (e.g. an antibody test where "Negative" is abnormal, or biopsy where "Negative" is normal).
- **Stage B (ModernBERT):**
  - ModernBERT receives auxiliary feature vector where `lab.flag` one-hot bit is active.
  - Model was trained primarily on tabular abnormal flags (`H`, `L`, `CRITICAL`), not qualitative assay determinations.
- **Stage C (`stage_c.py`):**
  - Line 58 `LAB_FLAG_TABULAR_INSTRUCTION` states: *"Explicit abnormal column indicators take strict precedence: a trailing 'H' or 'HIGH' explicitly denotes HIGH; a trailing 'L' or 'LOW' explicitly denotes LOW; an unflagged or blank abnormal entry denotes NORMAL."*
  - This instruction distorts Stage C evaluation of qualitative text.

#### Database Persistence
- Cleanly stores in PostgreSQL `claims.field_type = 'lab.flag'` and `claims.value = 'NEGATIVE'`. Zero migrations required.

#### Review Workflow & UI
- In the review interface, the user sees `lab.test_name = "HBsAg"`, `lab.value = ""` (empty), and `lab.flag = "NEGATIVE"`.
- Human reviewers find this confusing because the primary test result is displayed in a small severity badge rather than the primary value field.

#### FHIR R4B Export
- In `src/clindoc/fhir/mapper.py`:
  - `val_claim` is absent $\rightarrow$ `val_quantity = None`.
  - `flag_claim` is present $\rightarrow$ `Observation.interpretation` receives code `"NEGATIVE"`.
  - `Observation.value[x]` is **completely omitted**.
  - **FHIR Standards Violation:** HL7 FHIR defines `Observation.interpretation` as:
    > *"A categorical assessment of an observation value. For example, high, low, normal."*
    The qualitative determination is the **value** of the observation, not its interpretation. Furthermore, `code="NEGATIVE"` violates the HL7 `v3-ObservationInterpretation` value set (which uses `NEG`, `POS`, `NR`).
  - **Downstream EHR Ingestion:** Many hospital EHRs (Epic, Cerner) and FHIR profiles (US Core, ABDM) require `Observation.value[x]` for completed laboratory results. An Observation with no `value[x]` is often rejected, flagged as incomplete, or hidden from clinician view.

---

## 4. Option B — Qualitative Results as `lab.value`

### 4.1 Conceptual Model
- For qualitative tests:
  - `lab.value` = `"Negative"` | `"Non Reactive"` | `"Positive"` | `"Reactive"` (string value)
  - `lab.flag` = absent/null, or populated if document explicitly contains an abnormal flag (e.g. `*` or `ABNORMAL`).
- Numeric tests continue as:
  - `lab.value` = `"14.2"` (with `value_numeric: 14.2`)
  - `lab.flag` = `"NORMAL"` | `"HIGH"` | `"LOW"`

### 4.2 Detailed Lifecycle Trace

#### Extraction
- **Prompt:** Update `lab.value` description from *"numeric laboratory test result value"* to *"laboratory test result value, either quantitative numeric (e.g. 13.5, 95) or qualitative determination (e.g. Negative, Non-Reactive, Positive)"*.
- **Schema:** Fully compatible with `schemas/claim.json` and `src/clindoc/extract/contracts.py`. `Claim.value` is already a string, and `Claim.value_numeric` is optional (`Optional[float] = None`).

#### Normalization
- In `src/clindoc/normalize/normalizer.py`:
  - Enters `claim.field_type in ("lab.value", "med.dose")`.
  - `re.search(r"[-+]?\d+(?:\.\d+)?", "Negative")` finds no digits $\rightarrow$ `claim.value_numeric` remains `None`.
  - Unit regex `[-+]?\d+...` finds no match $\rightarrow$ `unit_raw` remains `None`.
  - **Subtle Gotcha:** If a qualitative value contains digits (e.g., `"HIV-1 Negative"` or `"Type 2 Non-reactive"`), regex `[-+]?\d+` would extract `1.0` or `2.0` unless guarded.

#### Verification
- **Stage A (`stage_a.py`): ALREADY FULLY IMPLEMENTED!**
  - Lines 220–230:
    ```python
    claim_num = self._extract_numeric_value(claim)
    if claim_num is None:
        # Non-numeric lab value (e.g. "positive", "negative")
        if field_val.strip().lower() in eval_text.lower():
            return StageAResult(label="SUPPORTED", confidence=1.0)
        return StageAResult(
            label="DISCREPANCY",
            contradiction_type="value_mismatch",
            confidence=1.0,
            details={"expected": field_val, "evidence_text": eval_text},
        )
    ```
  - Stage A deterministic verifier already has an explicit, tested branch that executes verbatim string-in-evidence matching when `claim_num` is None.
  - Option B works in Stage A **out of the box with zero code changes**.
- **Stage B (ModernBERT):**
  - ModernBERT receives auxiliary feature vector where `lab.value` one-hot bit is active.
  - Text input is `("Negative", evidence_text)`. Cross-attention verifies textual alignment cleanly.
- **Stage C (`stage_c.py`):**
  - Uses standard judge prompt. Free of tabular flag instructions.

#### Database Persistence
- Cleanly stores in PostgreSQL `claims.field_type = 'lab.value'` and `claims.value = 'Negative'`. Zero migrations required.

#### Review Workflow & UI
- In the review interface, the user sees `lab.test_name = "HBsAg"`, `lab.value = "Negative"`.
- Clean, natural, and intuitive clinical presentation.

#### FHIR R4B Export
- **Current Mapper Behavior (HAZARD IDENTIFIED):**
  - In `src/clindoc/fhir/mapper.py` line 829:
    ```python
    num_val = val_claim.value_numeric
    if num_val is None:
        m = re.search(r"[-+]?\d*\.?\d+", val_claim.value)
        num_val = float(m.group()) if m else 0.0
    ```
  - For `"Negative"`, `m` is `None`, so `num_val` defaults to **`0.0`**!
  - `Observation.valueQuantity` becomes `Quantity(value=0.0)`!
  - **CRITICAL SAFETY DEFECT:** Under the current mapper implementation, `"Negative"` becomes numeric `0.0`.
- **Target FHIR R4B Implementation Required:**
  - Branch on `val_claim.value_numeric is not None`:
    - Numeric $\rightarrow$ `Observation.valueQuantity = Quantity(...)`
    - Non-numeric $\rightarrow$ `Observation.valueString = val_claim.value`
  - Fully complies with HL7 FHIR Observation specification for qualitative findings (`Observation.value[x]` choice).

---

## 5. End-to-End Data Flow Comparison

| Lifecycle Stage | Option A (`lab.flag`) | Option B (`lab.value`) |
| :--- | :--- | :--- |
| **Extraction Prompt** | Requires redefining `lab.flag` semantics and adding suppression rules for `lab.value`. | Clean relaxation of `lab.value` constraint from strictly numeric to numeric or qualitative. |
| **Claim Schema** | Valid schema, but semantic distortion of "flag". | 100% compliant with existing schema (`value: str`, `value_numeric: null`). |
| **Normalization** | Bypasses normalizer. | Normalizer leaves `value_numeric: null` (needs regex guard against strings with digits). |
| **Stage A Verifier** | **Broken:** `_verify_flag` token sets do not recognize qualitative words; defers 100% to Stage B. | **Working:** `_verify_numeric_value` lines 220–230 already implement exact string matching. |
| **Stage B Verifier** | ModernBERT evaluated with `lab.flag` auxiliary bit. | ModernBERT evaluated with `lab.value` auxiliary bit. |
| **Stage C Verifier** | Distorted by `LAB_FLAG_TABULAR_INSTRUCTION` (expects H, L, NORMAL). | Clean standard verification prompt. |
| **PostgreSQL DB** | Stores in `claims` (`value="NEGATIVE"`). No migration. | Stores in `claims` (`value="Negative"`). No migration. |
| **Review UI** | Primary value column is empty; result displayed in flag badge. | Primary value column displays `"Negative"`; natural UX. |
| **FHIR Mapper** | Emits `Observation.interpretation = "NEGATIVE"`, `value[x]` is null. | **Current mapper emits `valueQuantity = 0.0` (Hazard).** Requires adding `Observation.valueString`. |
| **EHR Transport** | High risk of rejection due to missing `value[x]`. | High interoperability via standard `Observation.valueString`. |

---

## 6. FHIR R4B Comparison

In HL7 FHIR R4B (and R4), `Observation` defines:
1. **`Observation.value[x]`** (0..1):
   - `valueQuantity`
   - `valueCodeableConcept`
   - `valueString`
   - `valueBoolean`
   - `valueRange`, `valueRatio`, `valueSampledData`, `valueTime`, `valueDateTime`, `valuePeriod`
2. **`Observation.interpretation`** (0..*):
   - Description: *"A categorical assessment of an observation value. For example, high, low, normal."*
   - Binding: `http://hl7.org/fhir/ValueSet/observation-interpretation`

### Option A Mapping:
- `Observation.value[x]`: `None`
- `Observation.interpretation`:
  ```json
  "interpretation": [
    {
      "coding": [
        {
          "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
          "code": "NEGATIVE",
          "display": "Negative"
        }
      ],
      "text": "Negative"
    }
  ]
  ```
- **Deficiencies:**
  1. `NEGATIVE` is not a valid code in HL7 v3 `ObservationInterpretation` (the valid code is `NEG`).
  2. The finding itself is absent from `value[x]`.
  3. Violates FHIR best practices: interpretation is meant to qualify the value against clinical reference, not store the primary result.

### Option B Mapping (Target):
- `Observation.valueString`: `"Negative"`
- `Observation.interpretation`: `None` (or `NORMAL` if explicit reference interpretation exists)
  ```json
  "valueString": "Negative"
  ```
- **Advantages:**
  1. Conforms directly to HL7 FHIR R4B specification.
  2. Compatible with downstream EHRs that parse qualitative laboratory results from `valueString`.
  3. Clean upgrade path to `valueCodeableConcept` (e.g. SNOMED CT `260385009` "Negative") in future terminology phases.

---

## 7. Safety Analysis

### 7.1 Danger of "Negative" Accidentally Becoming Numeric 0.0

A primary concern in clinical software is numeric coercion of textual concepts.

#### Investigation of Codebase Paths:
1. **`src/clindoc/normalize/normalizer.py` lines 180–186:**
   ```python
   m = re.search(r"[-+]?\d+(?:\.\d+)?", claim.value)
   if m:
       claim.value_numeric = float(m.group(0))
   ```
   For pure text `"Negative"`, `m` is `None`. `claim.value_numeric` remains `None`.  
   *However*, if the text contains numbers (e.g. `"HIV-1 Negative"` or `"COVID-19 Negative"`), `m.group(0)` extracts `1` or `19`.
2. **`src/clindoc/fhir/mapper.py` lines 828–830:**
   ```python
   num_val = val_claim.value_numeric
   if num_val is None:
       m = re.search(r"[-+]?\d*\.?\d+", val_claim.value)
       num_val = float(m.group()) if m else 0.0
   ```
   **CRITICAL VULNERABILITY FOUND:** If `val_claim.value` is `"Negative"` and `val_claim.value_numeric` is `None`, `m` is `None`, and the expression `float(m.group()) if m else 0.0` evaluates to **`0.0`**!  
   Under Option B, if the FHIR mapper is not modified, `"HBsAg Negative"` would be mapped to `Observation.valueQuantity = 0.0`!  
   This would be a severe clinical safety hazard (a negative viral marker represented as a quantitative count of 0.0).

#### Safety Mitigation for Option B:
The FHIR mapper MUST explicitly check whether a numeric value is present. If `value_numeric` is `None` and no valid numeric token exists, it must **never** default to `0.0`. It must emit `Observation.valueString`.

### 7.2 False Positives & Free-Text Contamination
- **Option A:** Risk of extracting non-test clinical adjectives (e.g. `"Negative history of smoking"`, `"Abdomen: soft, non-tender"`) as `lab.flag`.
- **Option B:** Risk of extracting clinical impression sentences as `lab.value`. Controlled by layout-aware section gating and entity-aware bounding in Stage A.

### 7.3 Downstream EHR Data Corruption
- **Option A:** If `value[x]` is missing, EHR clinical decision support (CDS) rules evaluating lab results may fail to trigger or evaluate the test as "result pending".
- **Option B (with current mapper):** Catastrophic corruption (quantitative `0.0` in EHR).
- **Option B (with `valueString` fix):** Zero corruption. Standard, robust clinical documentation.

---

## 8. Backward Compatibility

### Impact on Numeric Laboratory Claims
- For standard numeric tests (`Hemoglobin: 14.2 g/dL`, `Glucose: 95 mg/dL`):
  - Under Option A: Numeric claims remain `lab.value="14.2"`, `lab.flag="NORMAL"`. 100% backward compatible.
  - Under Option B: Numeric claims remain `lab.value="14.2"`, `lab.flag="NORMAL"`. 100% backward compatible.
- All existing numeric test suites and benchmarks remain completely unaffected under both options.

---

## 9. Required Tests

### Tests Required for Option A:
1. `tests/test_stage_a_qualitative_flags.py`: Add tests for `_categorize_flag` handling qualitative tokens.
2. `tests/test_stage_c_qualitative_flags.py`: Verify Stage C prompt handling of non-H/L flags.
3. `tests/test_fhir_mapping.py`: Verify `Observation.interpretation` mapped from qualitative flags.

### Tests Required for Option B:
1. `tests/test_stage_a_qualitative_values.py`: Verify that `_verify_numeric_value` handles non-numeric strings (already passes in existing test harness, but needs explicit test coverage).
2. `tests/test_normalizer_qualitative_values.py`: Verify that `normalizer.py` does not corrupt qualitative strings containing numbers (e.g. `"HIV-1 Negative"`).
3. `tests/test_fhir_qualitative_observation.py`: **Critical test** asserting that qualitative `lab.value` produces `Observation.valueString` and **NEVER** `Observation.valueQuantity = 0.0`.

---

## 10. Required Production Files

| Subsystem | Option A Files | Option B Files |
| :--- | :--- | :--- |
| **Extraction Prompts** | `src/clindoc/extract/llm_extractor.py` | `src/clindoc/extract/llm_extractor.py` |
| **Normalizer** | None | `src/clindoc/normalize/normalizer.py` (add regex guard) |
| **Stage A Verifier** | `src/clindoc/verify/stage_a.py` (modify token sets & categories) | None (already implemented in lines 220–230) |
| **Stage C Verifier** | `src/clindoc/verify/stage_c.py` (modify tabular flag instruction) | None |
| **FHIR Mapper** | `src/clindoc/fhir/mapper.py` (map flag to interpretation) | `src/clindoc/fhir/mapper.py` (add `valueString` branch) |

---

## 11. Specification / Decision Impact

### If Option A is Selected:
- `PRODUCTIZATION_DECISIONS.md`: Needs new decision `PD-021` defining `lab.flag` as a dual-purpose container (severity flags + qualitative determinations).
- `PROJECT_SPEC.md` §8.2: Expand `lab.flag` definition beyond abnormal flags.
- `schemas/claim.json`: Update documentation of `lab.flag`.

### If Option B is Selected:
- `PRODUCTIZATION_DECISIONS.md`: Needs new decision `PD-021: Qualitative Laboratory Findings as lab.value and FHIR Observation.valueString`.
- `PRODUCTIZATION_SPEC.md` §8.3: Clarify that `value_numeric` is populated *only when a numeric value can be parsed*, and is explicitly `null` for qualitative determinations.
- `src/clindoc/fhir/mapper.py`: Document that `Observation.valueString` is the authoritative FHIR representation for non-numeric laboratory claims.

---

## 12. Side-by-Side Decision Matrix

| Criterion | Weight | Option A (`lab.flag`) | Option B (`lab.value`) |
| :--- | :---: | :---: | :---: |
| **Semantic Correctness** | High | ❌ Poor (misuses interpretation for finding) | ✅ Excellent (finding is the value) |
| **Clinical Expressiveness** | High | ❌ Moderate (cannot represent Positive + Abnormal) | ✅ High (can represent both finding and flag) |
| **Compatibility with Existing Stage A** | High | ❌ Broken (unrecognized tokens return None) | ✅ Native (lines 220–230 already handle it) |
| **Implementation Complexity** | Medium | ⚠️ Medium (changes in Stage A, C, FHIR) | ⚠️ Low-Medium (prompt update + FHIR mapper fix) |
| **FHIR R4B Standard Alignment** | Critical | ❌ Non-compliant (omits value[x]) | ✅ Fully compliant (`valueString`) |
| **EHR Interoperability** | Critical | ❌ Poor (EHRs expect `value[x]`) | ✅ High (standard EHR ingestion) |
| **Backward Compatibility** | High | ✅ 100% on numeric labs | ✅ 100% on numeric labs |
| **Safety / Fail-Closed** | Critical | ⚠️ Bypasses Stage A rules | ❌ **Hazard in current mapper (0.0 fallback)**; ✅ Safe once `valueString` is added |
| **Future Extensibility (SNOMED CT)** | Medium | ❌ Low | ✅ High (`valueCodeableConcept`) |

---

## 13. Neutral Recommendation Based on Existing Architecture

From an architectural, clinical standards, and software maintainability perspective:

1. **Option B (`lab.value`) is the technically superior, standard-compliant model:**
   - In clinical medicine, the test result for HBsAg is `"Negative"`. That is the observation's *value*.
   - In HL7 FHIR R4B, qualitative findings belong in `Observation.value[x]` (`valueString` or `valueCodeableConcept`).
   - In our verification cascade, `src/clindoc/verify/stage_a.py` **already contains the exact code to verify qualitative `lab.value` claims** (lines 220–230).
   - In the database and UI, `lab.value` naturally populates the primary result column.

2. **Crucial Prerequisite Before Option B Can Be Activated:**
   - **`src/clindoc/fhir/mapper.py` line 829 MUST NOT default non-numeric `lab.value` to `0.0`.**
   - The mapper must be updated to emit `Observation.valueString = val_claim.value` whenever `val_claim.value_numeric` is `None`.

3. **Why Option A is Not Recommended:**
   - Storing findings in `lab.flag` conflates the *result* with the *interpretation* (normality).
   - Breaks Stage A deterministic verification without modifying core flag token dictionaries.
   - Emits FHIR Observations with null `value[x]`, which will cause validation or ingestion failures in standard EHR integration engines.

---

## 14. Open Questions Requiring Product Decision

Before implementing Root Cause B, the following product questions must be resolved:

1. **Representation Choice:** Does the product team formally approve Option B (Qualitative Results as `lab.value` with FHIR `valueString`)?
2. **FHIR Typing:** Should qualitative results in FHIR R4B be exported as plain `Observation.valueString`, or should the terminology service attempt to map common qualitative results to SNOMED CT concepts (`Observation.valueCodeableConcept`, e.g. SNOMED CT `260385009` for "Negative")?
3. **Hybrid Presence:** If a source document states `"HBsAg Negative (Normal)"`, should the pipeline emit:
   - Claim 1: `lab.test_name = "HBsAg"`
   - Claim 2: `lab.value = "Negative"`
   - Claim 3: `lab.flag = "NORMAL"`
   (This is cleanly supported under Option B, but impossible under Option A).
4. **Boundary Definition:** What is the controlled list of allowable qualitative terms (e.g. `Negative`, `Positive`, `Non-Reactive`, `Reactive`, `Detected`, `Not Detected`, `Equivocal`), or should arbitrary free-text qualitative findings be permitted if grounded in the source table row?
