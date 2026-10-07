# Condition 4 Holdout Evaluation: Root-Cause & Change-Impact Audit

**Evaluator:** Antigravity AI Engine  
**Date:** October 7, 2026  
**Document Evaluated:** `Original_Discharge_Summary.pdf` (CMC Vellore, OB/GYN Unit V)  
**Status:** READ-ONLY IMPACT AUDIT (No production code, prompts, schemas, configs, or specs modified)  
**Baseline Evaluation Reference:** [`reports/NARRATIVE_EXTRACTION_CONDITION4_HOLDOUT_EVALUATION.md`](file:///d:/daa/reports/NARRATIVE_EXTRACTION_CONDITION4_HOLDOUT_EVALUATION.md)  

---

## 1. Holdout Findings

The frozen holdout evaluation of Condition 4 (Gated Layout-Aware Serialization + Dual-Pass Extraction + Lab Negative Boundary) on the unobserved real-world clinical hospital document `Original_Discharge_Summary.pdf` revealed an acute contrast between precision and generalizability:

- **Precision (Exact & Normalized):** **100.00%** (4/4 on pipeline output; 5/5 on raw model output)
- **Recall (Exact & Normalized):** **36.36%** (4/11 on pipeline output; 45.45% on raw model output)
- **F1 Score:** **53.33%** (Pipeline) / **62.50%** (Raw Qwen)
- **Grounding Compliance:** **100.00%** (Zero hallucinated or ungrounded claims admitted)
- **True Positives (TP):** 4
- **False Positives (FP):** **0** (Zero hallucinated entities, zero vital signs misextracted)
- **False Negatives (FN):** **7** (Omissions across both pages)

### Diagnosis:
The pipeline's negative boundaries and prompt safeguards completely protected against hallucinations and vital sign confusion. However, **Recall collapsed by -58.88 percentage points** relative to the development pilot (95.24% $\rightarrow$ 36.36%). 

This collapse was driven by three root causes:
1. **Root Cause A (1 FN):** Literal substring grounding filter rejected a valid hemoglobin extraction because OCR text had a newline (`\n`) where Qwen emitted a space (` `).
2. **Root Cause B (3 FN):** Antenatal infectious serology screens (`HBsAg Negative`, `HIV ELISA Negative`, `VDRL Non Reactive`) were suppressed because the prompt and extraction contract strictly required numeric lab values and severity flags.
3. **Root Cause C (3 FN):** Compound slash-delimited prescriptions (`C.Autrin / T.Shelcal once daily`) and inline narrative prescriptions caused entity truncation, dropping `T.Shelcal` and omitting `med.name: Metacin`.

---

## 2. Root Cause A — Whitespace-Invariant Grounding

### Current Implementation & Mechanics
The grounding verification is implemented in `src/clindoc/extract/llm_extractor.py` (lines 549–565):
```python
# REJECT: evidence_text is not a verbatim substring (§7.4)
if not evidence_text or evidence_text not in region_text:
    diagnostics["rejected"].append({
        "field_type": field_type,
        "value": value,
        "evidence_text": evidence_text,
        "reason": "evidence_text is not a verbatim substring of region_text",
    })
    continue

local_pos = region_text.find(evidence_text)
if local_pos >= 0:
    char_start = char_offset + local_pos
    char_end = char_start + len(evidence_text)
```
And in scratch Condition 4 runners:
```python
if ev_text and ev_text in page_text:
    valid_exts.append(ex)
```

### All Callers
1. `clindoc.extract.llm_extractor.NarrativeExtractor.extract()`
2. `clindoc.extract.router.ExtractionRouter._extract_narrative()`
3. Background processing worker: `clindoc.jobs.worker.py` (via ExtractionRouter)
4. Scratch runners: `scratch/run_condition_4.py`, `scratch/run_condition4_holdout.py`, `scratch/run_extraction_experiment.py`
5. Test suites: `tests/test_extraction_subsystem.py`, `tests/test_e3_evaluation.py`

### Safety & Security Analysis
- **Why it failed:** Real-world scanned OCR engines (such as PaddleOCR) serialize text with line breaks (`\n`) between adjacent layout blocks:
  ```
  HAEMOGLOBIN
  12.6
  GM%
  ```
  When Qwen extracts this finding, it produces `evidence_text: "HAEMOGLOBIN 12.6 GM%"`. Because python's `in` operator evaluates strict byte/character equality, `"HAEMOGLOBIN 12.6 GM%"` is not a substring of `"HAEMOGLOBIN\n12.6\nGM%"`.
- **Can normalization be introduced safely?** **YES.**
  If normalization strictly treats runs of whitespace (`[ \t\r\n]+`) as interchangeable while requiring **exact, case-sensitive character matches for all non-whitespace tokens in identical order**, provenance and security are completely preserved.
- **Does it weaken anti-hallucination security?** **NO.**
  It does not introduce fuzzy matching, Levenshtein distance, token reordering, or character substitutions. It merely tolerates layout-induced whitespace token delimiters.
- **Narrowest Safe Normalization Rule:**
  ```python
  def find_whitespace_invariant_span(pattern_text: str, source_text: str) -> Optional[tuple[int, int]]:
      tokens = pattern_text.split()
      if not tokens:
          return None
      regex_pattern = r"\s+".join(re.escape(tok) for tok in tokens)
      match = re.search(regex_pattern, source_text)
      if match:
          return match.start(), match.end()
      return None
  ```
  When matched, the true verbatim text is sliced directly from `source_text[start:end]` (preserving exact underlying OCR characters and newlines), and `char_start`, `char_end`, and bounding boxes are computed from the underlying text with 100% geometric fidelity.
- **Classification:** **A. Safe Isolated Bug Fix.** Independent of Condition 4, this is an immediate defect correction for production `NarrativeExtractor`.

---

## 3. Root Cause B — Qualitative Laboratory Results

### The Holdout Defect
The holdout document contained three antenatal serology screening results on Page 1:
- `HBSAG NEGATIVE`
- `HIV ELISA NEGATIVE`
- `VDRL NON REACTIVE`

Qwen extracted zero claims for these tests. In obstetric, infectious disease, and surgical pre-op charts, serology screens are standard.

### Complete Downstream Pipeline Audit

| Pipeline Layer | Component / File | Behavior on Qualitative Laboratory Data | Severity / Constraint Level |
| :--- | :--- | :--- | :--- |
| **1. Controlled Vocabulary** | `src/clindoc/extract/contracts.py` | `FieldType = Literal["lab.value", "lab.flag", ...]` includes both. | **Permitted.** No type violation. |
| **2. Pydantic Models** | `src/clindoc/extract/contracts.py` | `Claim.value: str`, `value_numeric: Optional[float] = None`. | **Permitted.** `value` is already a string; `value_numeric` defaults to `None`. |
| **3. Canonical JSON Schema** | `schemas/claim.json` | `value: {"type": "string"}`, `value_numeric: {"type": ["number", "null"]}`. | **Permitted.** Conforms to schema. |
| **4. Database Schema** | `src/clindoc/db/models.py` | `claims.value: Mapped[str] = mapped_column(Text)`. | **Permitted.** No database numeric constraint. |
| **5. Extraction Prompt** | `src/clindoc/extract/llm_extractor.py` | Strictly instructs: `lab.value: numeric laboratory test result value... lab.flag: (NORMAL, HIGH, LOW, CRITICAL only)`. | **BLOCKING CONSTRAINT.** Forbids qualitative values and non-severity flags. |
| **6. Normalization Engine** | `src/clindoc/normalize/normalizer.py` | Regex extracts numbers for `value_numeric`. If none, leaves `value_numeric = None`. | **Safe Passthrough.** Does not crash or reject non-numeric values. |
| **7. Stage A Verification** | `src/clindoc/verify/stage_a.py` | `_verify_numeric_value()` explicitly handles `claim_num is None` via string containment. However, `_categorize_flag()` only recognizes `FLAG_HIGH`, `FLAG_LOW`, `FLAG_NORMAL`, `FLAG_ABNORMAL`. | **Partial Block.** `lab.value` passes; `lab.flag` defers to Stage B because "negative" is not categorized. |
| **8. Stage B Verification** | `src/clindoc/verify/stage_b.py` | DeBERTa neural verifier and heuristic (`c_val in e_text`) both accept arbitrary strings. | **Safe Passthrough.** Yields `SUPPORTED`. |
| **9. Stage C Judge** | `src/clindoc/verify/stage_c.py` | Judge prompt formats `claim.value` as string and `numeric_str` as `"None"`. | **Safe Passthrough.** Evaluates qualitative claims naturally. |
| **10. FHIR R4B Mapper** | `src/clindoc/fhir/mapper.py` (lines 826–860) | **CRITICAL FLAW:** If mapped as `lab.value`, lines 828–829 default unparseable values to `num_val = 0.0` and emit `Quantity(value=Decimal("0.0"))`. | **CATASTROPHIC DOWNSTREAM CORRUPTION.** Exports "Negative" as numeric `0.0`! |
| **11. FHIR Flag Mapping** | `src/clindoc/fhir/mapper.py` (lines 863–892) | If mapped as `lab.flag`, `flag_claim.value` maps into `Observation.interpretation`. | **Safe Path.** Preserves qualitative interpretation. |
| **12. API & Review UI** | `src/clindoc/api/routes/review.py` | All claim endpoints serialize `value: str`. | **Safe Passthrough.** Displays cleanly in review queue. |

### Architectural Resolution & Decision Impact
Attempting to force qualitative results into `lab.value` causes a severe downstream regression in FHIR export (converting qualitative tests to numeric `0.0`). 

There are two potential architectural resolutions:
- **Design Path 1 (Recommended): Represent Qualitative Determinations as `lab.flag`**
  - Clinical rationale: In laboratory medicine, "Negative" and "Non-Reactive" are clinical interpretations (qualitative flags), exactly like "Normal" or "Abnormal".
  - Expand prompt for `lab.flag`: `(NORMAL, HIGH, LOW, CRITICAL, POSITIVE, NEGATIVE, REACTIVE, NON-REACTIVE)`.
  - Update `src/clindoc/verify/stage_a.py`: Add `FLAG_NORMAL_TOKENS |= {"negative", "neg", "non-reactive", "non reactive"}` and `FLAG_ABNORMAL_TOKENS |= {"positive", "pos", "reactive"}`.
  - Update `src/clindoc/fhir/mapper.py`: Map `"NEGATIVE"` to HL7 code `NEG`, `"POSITIVE"` to `POS`, `"NON REACTIVE"` to `NR`.
- **Design Path 2: Support `valueCodeableConcept` / `valueString` in FHIR for `lab.value`**
  - Clinical rationale: Allows the primary result of qualitative tests (e.g., blood type `B Rh Positive`) to be stored in `lab.value`.
  - Requires branching in `fhir/mapper.py` to emit `valueString` when `value_numeric is None`.

### Specification & Decision Impact
- **Classification:** **E. Authoritative Product-Spec Decision Required.**
  Both `PRODUCTIZATION_SPEC.md` §8 (FHIR export mapping) and `PRODUCTIZATION_DECISIONS.md` (PD-005 qualified manifest) govern the permissible extraction contract. Changing the semantic boundary of laboratory fields requires an explicit productization decision before any production code change.

---

## 4. Root Cause C — Compound / Slash-Delimited Medication Lines

### Current Mechanics & Cause of Omission
Holdout line:
`"C.Autrin / T.Shelcal once daily for 6 weeks"`
- Qwen extracted:
  - `med.name: "C.Autrin"`
  - `med.frequency: "once daily"`
- Qwen omitted:
  - `med.name: "T.Shelcal"` (and its associated frequency)

Inline PRN line:
`"Tab Metacin 1g as and when needed"`
- Qwen extracted:
  - `med.dose: "1"`, `unit_raw: "g"`
  - `med.frequency: "as and when needed"`
- Qwen omitted:
  - `med.name: "Metacin"`

### Analysis of the Defect
1. **Prompt Training Bias:** The few-shot examples in `MEDICATION_SYSTEM_PROMPT` consist exclusively of single-medication rows (one medication per line: `Furoped`, `Aldactone`, `Inderal`). The model learned that each input line corresponds to exactly one medication entity.
2. **Compound Prescription Omission:** In clinical practice, physicians routinely co-prescribe oral supplements or analgesics separated by slashes or commas (e.g., iron/calcium, aspirin/statin) while sharing a single frequency clause (`once daily`).
3. **Entity Disconnection:** On `"Tab Metacin 1g as and when needed"`, Qwen successfully extracted the attributes (dose and frequency) but dropped the entity head (`Metacin`), violating clinical completeness.

### Narrowest General Rule
Add an explicit extraction rule and a single general few-shot example to `MEDICATION_SYSTEM_PROMPT`:
- **Rule:** *"COMPOUND & MULTI-DRUG PRESCRIPTIONS: When multiple medications are listed on a single line separated by slashes, commas, or conjunctions sharing a frequency, route, or duration (e.g. 'Drug A / Drug B once daily'): extract EACH drug as an independent med.name, and extract the shared frequency/duration for each drug. Do NOT drop co-prescribed medications."*
- **General Exemplar:**
  ```
  Input: "C.Ferrous / T.Calcium once daily for 30 days"
  Output:
  {
    "extractions": [
      {"field_type": "med.name", "value": "Ferrous", "evidence_text": "C.Ferrous", "unit_raw": null, "confidence": 0.95},
      {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily", "unit_raw": null, "confidence": 0.95},
      {"field_type": "med.name", "value": "Calcium", "evidence_text": "T.Calcium", "unit_raw": null, "confidence": 0.95},
      {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily", "unit_raw": null, "confidence": 0.95}
    ]
  }
  ```
- **Classification:** **B. Prompt-Only Refinement.**

---

## 5. Exact Files and Components Affected

```
+------------------------------------+---------------------------------------------------------------+-----------------------------------+
| Component                          | File Path                                                     | Root Cause & Nature of Change     |
+------------------------------------+---------------------------------------------------------------+-----------------------------------+
| Narrative Extractor                | src/clindoc/extract/llm_extractor.py                          | Root Cause A (Whitespace Regex)   |
|                                    |                                                               | Root Cause B (Prompt Flag Scope)  |
|                                    |                                                               | Root Cause C (Prompt Compound Ex) |
+------------------------------------+---------------------------------------------------------------+-----------------------------------+
| Stage A Deterministic Verifier     | src/clindoc/verify/stage_a.py                                 | Root Cause B (FLAG token sets)    |
+------------------------------------+---------------------------------------------------------------+-----------------------------------+
| FHIR R4B Observation Mapper        | src/clindoc/fhir/mapper.py                                    | Root Cause B (HL7 interpretation  |
|                                    |                                                               | codes & valueString handling)     |
+------------------------------------+---------------------------------------------------------------+-----------------------------------+
| Extractor Test Suite               | tests/test_extraction_subsystem.py                            | Tests for Root Causes A, B, C     |
+------------------------------------+---------------------------------------------------------------+-----------------------------------+
| FHIR Export Test Suite             | tests/test_fhir_export.py                                     | Tests for Root Cause B mapping    |
+------------------------------------+---------------------------------------------------------------+-----------------------------------+
| Authoritative Spec & Decisions     | PRODUCTIZATION_SPEC.md                                        | Root Cause B (Qualitative lab     |
|                                    | PRODUCTIZATION_DECISIONS.md                                   | architecture decision)            |
+------------------------------------+---------------------------------------------------------------+-----------------------------------+
```

---

## 6. Safety Impact

1. **Root Cause A (Whitespace Grounding):**
   - **Safety Benefit:** **High.** Prevents valid extracted claims from being discarded due to incidental line-wrapping in institutional OCR.
   - **Safety Risk:** **Zero.** Retains exact token ordering and character containment; does not permit fuzzy alterations.
2. **Root Cause B (Qualitative Labs):**
   - **Safety Benefit:** **Critical.** Missing infectious disease screening results (`HBsAg Negative`, `HIV ELISA Negative`) is a severe clinical omission. In obstetric and surgical contexts, maternal infection status dictates isolation, prophylaxis, and delivery management.
   - **Safety Risk:** **Moderate if misimplemented.** If qualitative results are mapped as numeric `0.0` in FHIR, EHR ingestion will be corrupted. Must strictly map to `Observation.interpretation` or `valueString`.
3. **Root Cause C (Compound Prescriptions):**
   - **Safety Benefit:** **High.** Missing co-prescribed medications (e.g., omitting calcium while extracting iron, or omitting cardioprotective/anti-ulcer agents) creates dangerous gaps in the electronic medication record.
   - **Safety Risk:** **Low.** Must ensure combination drugs (e.g., `Amoxicillin-clavulanate`) or combined metrics (`PT/INR`) are not erroneously split into non-existent entities.

---

## 7. Regression Risk Analysis

| Test Suite / Benchmark | Root Cause A (Grounding) | Root Cause B (Qualitative Labs) | Root Cause C (Compound Meds) | Mitigating Design Guard |
| :--- | :---: | :---: | :---: | :--- |
| **Development Cohort ($N=3$)** | No regression (Safe) | No regression (Dev labs are all numeric) | Risk: Could over-split combination entities | Test against `Inderal` and `Aldactone` rows |
| **Condition 4 Baseline** | Expected gain (+1 TP) | Expected gain (+3 TP) | Expected gain (+2 TP) | Rerun scratch holdout cleanly |
| **E3 Extraction Suite** | No regression | No regression | No regression | Fixed synthetic test strings |
| **Worker / Fallback Tests** | No regression | No regression | No regression | Mock providers unaffected |
| **F-05 Negation Tests** | No regression | Must preserve negative tokens | No regression | Negation detector unaltered |
| **F-06 / F-07 Confusion** | No regression | No regression | Must not swap doses between compound drugs | Strict per-entity extraction pairing |
| **FHIR R4B Export Tests** | No regression | High Risk if `fhir/mapper.py` breaks schema | No regression | Validate with official FHIR validator |
| **Adversarial / Security** | Must test whitespace injection | No regression | No regression | Verify no delimiter injection attacks |

---

## 8. Specification & Decision Impact

1. **`PROJECT_SPEC.md`:**
   - **Impact: NONE.** §7.4 mandates verbatim grounding and §8.2 defines Tier 1 fields. Both `lab.flag` and `med.name` are already Tier 1 fields.
2. **`PRODUCTIZATION_SPEC.md`:**
   - **Impact: MINOR AMENDMENT.** §8 (FHIR Mapping) currently specifies `Quantity` mapping for laboratory observations. An amendment must document that qualitative determinations are mapped to `Observation.interpretation` using HL7 interpretation codes (`NEG`, `POS`, `NR`) or to `Observation.valueString`.
3. **`PRODUCTIZATION_DECISIONS.md`:**
   - **Impact: FORMAL DECISION REQUIRED (PD-017).** A new decision (e.g., `PD-017: Handling Qualitative Laboratory Screening Determinations`) must formally establish:
     - That qualitative infectious disease and serology screenings are extracted as `field_type: "lab.flag"`.
     - That allowed flag tokens include `{NEGATIVE, POSITIVE, NON_REACTIVE, REACTIVE}` alongside `{NORMAL, HIGH, LOW, CRITICAL}`.
     - That the FHIR mapper exports these as HL7 v3 `Observation.interpretation` codes.

---

## 9. Minimal Proposed Changes (When Authorized)

### Change 1: Regex-Based Whitespace Invariant Grounding (Root Cause A)
*File:* `src/clindoc/extract/llm_extractor.py` (lines 549–565)
```python
# Whitespace-invariant verbatim token span matching
tokens = evidence_text.split()
matched_span = None
if tokens:
    pattern = r"\s+".join(re.escape(tok) for tok in tokens)
    match = re.search(pattern, region_text)
    if match:
        matched_span = (match.start(), match.end())

if not evidence_text or matched_span is None:
    diagnostics["rejected"].append({
        "field_type": field_type,
        "value": value,
        "evidence_text": evidence_text,
        "reason": "evidence_text tokens not found verbatim in region_text",
    })
    continue

local_start, local_end = matched_span
char_start = char_offset + local_start
char_end = char_offset + local_end
verbatim_text = region_text[local_start:local_end]
```

### Change 2: Compound Prescription Extraction Exemplar (Root Cause C)
*File:* `src/clindoc/extract/llm_extractor.py` (System Prompt)
* Add general instruction: *"When multiple medications appear on a single line separated by slashes or commas sharing a schedule, extract each medication as an independent med.name claim."*
* Add generic exemplar (e.g., `C.Iron / T.Zinc once daily`).

### Change 3: Qualitative Laboratory Flag Support (Root Cause B)
*Files:* `src/clindoc/extract/llm_extractor.py`, `src/clindoc/verify/stage_a.py`, `src/clindoc/fhir/mapper.py`
* Expand `lab.flag` prompt enum: `NORMAL, HIGH, LOW, CRITICAL, POSITIVE, NEGATIVE, REACTIVE, NON-REACTIVE`.
* In `stage_a.py`: Map `"negative"` and `"non reactive"` to `FLAG_NORMAL_TOKENS`; map `"positive"` and `"reactive"` to `FLAG_ABNORMAL_TOKENS`.
* In `fhir/mapper.py`: Map `"NEGATIVE"` to `NEG`, `"POSITIVE"` to `POS`, `"NON REACTIVE"` to `NR`.

---

## 10. Recommended Implementation Order

1. **Step 1: Implement Whitespace-Invariant Grounding (Root Cause A)**
   - *Rationale:* Safe isolated bug fix. Zero specification impact. Immediately resolves multiline OCR line-wrap rejections across all documents.
2. **Step 2: Add Compound Prescription Prompt Rule (Root Cause C)**
   - *Rationale:* Prompt-only refinement within existing Tier 1 medication contract. Resolves multi-drug prescription omission without modifying schemas or downstream mappers.
3. **Step 3: Formalize Product Decision & Implement Qualitative Labs (Root Cause B)**
   - *Rationale:* Requires architectural alignment on FHIR representation before modifying code. Prevents corruption of the FHIR export pipeline.
4. **Step 4: Re-Run Holdout & Benchmark Regression Suite**
   - *Rationale:* Verify that holdout Recall improves from 36.36% to $\ge 90\%$ while confirming zero regression on the 3-document development suite.

---

## 11. What MUST NOT Be Changed

To protect clinical safety and architectural stability, the following constraints are non-negotiable:
1. **DO NOT WEAKEN PROVENANCE:** Never introduce fuzzy edit-distance or Levenshtein matching in the grounding filter. All non-whitespace characters must match verbatim.
2. **DO NOT REMOVE THE LAB NEGATIVE BOUNDARY:** The negative boundary in Condition 4 successfully rejected infant body weight (`2.76 kg`), maternal pulse, and blood pressure. It must remain strictly active.
3. **DO NOT HARDCODE HOLDOUT ENTITIES:** Never mention `Autrin`, `Shelcal`, `Metacin`, or `HBsAg` in prompt exemplars or unit tests.
4. **DO NOT EXPORT QUALITATIVE RESULTS AS ZERO QUANTITIES:** Never emit `Quantity(value=0.0)` for qualitative laboratory determinations.
5. **DO NOT MODIFY STAGE B CHECKPOINTS OR STAGE C THRESHOLDS:** The verification cascade weights and thresholds must remain locked.

---

## 12. Final Recommendation

- **Holdout Audit Conclusion:**
  The Condition 4 holdout evaluation was not an architectural failure, but an illuminating demonstration of how real-world clinical domain shifts (chemistry $\rightarrow$ serology) and layout variations (newlines in OCR tables) expose boundary assumptions.
- **Immediate Next Step:**
  Keep the codebase strictly in read-only mode until the team signs off on:
  1. The whitespace-invariant grounding bug fix (Step 1).
  2. The compound prescription prompt rule (Step 2).
  3. The formal product decision on qualitative serology mapping (Step 3).
