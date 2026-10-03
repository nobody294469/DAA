# Controlled Intervention Analysis: V8_STAGE_C_LABFLAG_PROMPT_V1

**Experiment Identifier:** `V8_STAGE_C_LABFLAG_PROMPT_V1`  
**Evaluation Target:** Out-of-Distribution (OOD) Holdout Benchmark (1,826 examples)  
**Evaluation Status:** Completed  
**Intervention Type:** Strictly controlled, single-variable Stage-C prompt modification targeting tabular `lab.flag` precedence and numerical interval reasoning.  
**Baseline Reference:** Frozen `V3-PHASE4-BASELINE-FREEZE` (`experiments/V8-verification-cascade/V3_PHASE4_BASELINE_REPORT.md`)

---

## 1. Execution & Run Validity Audit

The OOD evaluation of `V8_STAGE_C_LABFLAG_PROMPT_V1` was audited against the execution trace and per-claim diagnostics in `experiments/V8-verification-cascade/phase4_cascade_diagnostics.json`:

| Execution Dimension | Value | Audit Verdict |
| :--- | :--- | :--- |
| **Total OOD Examples Evaluated** | 1,826 / 1,826 | **Valid** (100% split coverage) |
| **Stage C Escalation Count** | 381 / 1,826 (20.87%) | **Valid** (Exact match to baseline escalation protocol) |
| **Stage C Generation Attempts** | 381 / 381 on Attempt 1 | **Valid** (Zero retry loops triggered) |
| **Transport / HTTP Errors** | 0 | **Valid** (Zero network dropouts or connection drops) |
| **Schema Validation / JSON Errors** | 0 | **Valid** (100% schema conformance to `StageCJudgeOutput`) |
| **Fallback Verdicts** | 0 | **Valid** (Zero clinical fallbacks to `INSUFFICIENT_EVIDENCE`) |
| **Active Prompt Version Logged** | `V8_STAGE_C_LABFLAG_PROMPT_V1` | **Valid** (100% verified across all 381 Stage-C records) |
| **Model Backend** | `Qwen/Qwen2.5-7B-Instruct-AWQ` | **Valid** (All 381 decisions are genuine Qwen judgments) |

**Conclusion on Run Validity:** The V1 evaluation is technically clean, fully instrumented, and provides an authoritative basis for controlled comparative analysis.

---

## 2. Global Metric Comparison: Baseline vs V1

The single-variable intervention modified *only* the Stage-C prompt for `lab.flag` claims, leaving ModernBERT weights, temperature scaling ($T = 1.146522$), Stage A rules, and cascade threshold ($0.60$) completely untouched.

| Metric | Frozen Baseline (V0) | Controlled V1 Run | Absolute Change | Relative Change |
| :--- | :---: | :---: | :---: | :---: |
| **Cascade Macro-F1** | 0.9422 | **0.9538** | **+0.0116** | **+1.23%** |
| **Cascade Discrepancy Recall** | 0.9806 | **0.9826** | **+0.0020** | **+0.20%** |
| **Cascade Accuracy** | 0.9595 | **0.9748** | **+0.0153** | **+1.59%** |
| **Stage B Resolved Count** | 1,445 / 1,826 (79.13%) | 1,445 / 1,826 (79.13%) | 0 | Identical (Unchanged) |
| **Stage B Resolved Accuracy** | 0.9931 (1,435 / 1,445) | 0.9931 (1,435 / 1,445) | 0 | Identical (Unchanged) |
| **Stage B AUROC (Macro)** | 0.9732 | 0.9732 | 0 | Identical (Unchanged) |
| **Stage B Brier Score** | 0.0858 | 0.0858 | 0 | Identical (Unchanged) |
| **Stage B ECE** | 0.0372 | 0.0372 | 0 | Identical (Unchanged) |
| **Stage C Escalated Count** | 381 / 1,826 (20.87%) | 381 / 1,826 (20.87%) | 0 | Identical (Unchanged) |
| **Stage C Accuracy** | 0.8294 (316 / 381) | **0.9055 (345 / 381)** | **+0.0761** | **+9.18%** |
| **Stage C Total Errors** | 65 | **36** | **-29** | **-44.62%** |

### Confusion Matrices (Row = Ground Truth, Column = Prediction)

#### Final Full Cascade Confusion Matrix (V1)
```
                          Predicted: SUPPORTED   DISCREPANCY   INSUFFICIENT_EVIDENCE   Total
True: SUPPORTED                    714              15                     0             729
True: DISCREPANCY                    8             961                     9             978
True: INSUFFICIENT_EVIDENCE          3              11                   105             119
Total                              725             987                   114            1826
```

#### Stage C Adjudicated Subset Confusion Matrix (V1, 381 Claims)
```
                          Predicted: SUPPORTED   DISCREPANCY   INSUFFICIENT_EVIDENCE   Total
True: SUPPORTED                     53              15                     0              68
True: DISCREPANCY                    6             187                     9             202
True: INSUFFICIENT_EVIDENCE          3               3                   105             111
Total                               62             205                   114             381
```

---

## 3. Granular Stage-C Performance Breakdown

### Performance by Clinical Field Type (Stage C Escalated Subset)

| Field Type | Total Claims | Correct | Accuracy | Errors | Error Share |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **lab.flag** | **247** | **220** | **0.8907** | **27** | **75.0%** |
| **lab.value** | 55 | 51 | 0.9273 | 4 | 11.1% |
| **med.route** | 22 | 20 | 0.9091 | 2 | 5.6% |
| **med.name** | 18 | 16 | 0.8889 | 2 | 5.6% |
| **med.frequency** | 21 | 20 | 0.9524 | 1 | 2.8% |
| **med.dose** | 18 | 18 | 1.0000 | 0 | 0.0% |
| **Total** | **381** | **345** | **0.9055** | **36** | **100.0%** |

### Performance by Document Family & Template (Stage C Escalated Subset)

| Document Family | Template Identifier | Total Escalated | Correct | Accuracy | Errors |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Lab Reports** | `template_c` (`LAB_*`) | 293 | 262 | 0.8942 | 31 |
| **Medication Orders** | `mar_order_sheet` (`MED_*`) | 55 | 51 | 0.9273 | 4 |
| **Clinical Narratives** | `narrative_*` (`NARRATIVE_*`) | 33 | 32 | 0.9697 | 1 |
| **Total** | | **381** | **345** | **0.9055** | **36** |

---

## 4. Controlled Comparison: Baseline vs V1 Error Dynamics

### Comparison on Targeted Subsets (`lab.flag` and `template_c`)

| Evaluation Dimension | Frozen Baseline (V0) | Controlled V1 Run | Net Difference |
| :--- | :---: | :---: | :---: |
| **`lab.flag` Stage-C Accuracy** | 77.33% (191 / 247) | **89.07% (220 / 247)** | **+11.74%** |
| **`lab.flag` Stage-C Errors** | 56 | **27** | **-29 (-51.8%)** |
| **`template_c` Stage-C Accuracy** | 79.52% (233 / 293) | **89.42% (262 / 293)** | **+9.90%** |
| **`template_c` Stage-C Errors** | 60 | **31** | **-29 (-48.3%)** |

### Error Transition Patterns (Stage C Subset)

| Error Transition | Baseline Errors | V1 Errors | Delta | Status |
| :--- | :---: | :---: | :---: | :--- |
| **SUPPORTED $\rightarrow$ DISCREPANCY** | **42** | **15** | **-27** | **64.3% reduction in false discrepancies** |
| **DISCREPANCY $\rightarrow$ INSUFFICIENT_EVIDENCE** | 9 | 9 | 0 | Stable |
| **DISCREPANCY $\rightarrow$ SUPPORTED** | 8 | 6 | -2 | Minor reduction |
| **INSUFFICIENT_EVIDENCE $\rightarrow$ DISCREPANCY** | 3 | 3 | 0 | Stable |
| **INSUFFICIENT_EVIDENCE $\rightarrow$ SUPPORTED** | 3 | 3 | 0 | Stable |
| **SUPPORTED $\rightarrow$ INSUFFICIENT_EVIDENCE** | 0 | 0 | 0 | Zero regressions |
| **Total Stage C Errors** | **65** | **36** | **-29** | **44.6% overall error reduction** |

---

## 5. Specificity & Attribution of the Improvement

The experimental evidence establishes that the V1 performance gain is **100% specifically attributable** to the targeted `lab.flag` / `template_c` failure mode:

1. **Exact Error Reduction Matching:**
   - Total Stage C errors dropped by exactly **29** (from 65 to 36).
   - `lab.flag` errors dropped by exactly **29** (from 56 to 27).
   - `template_c` errors dropped by exactly **29** (from 60 to 31).
   - Non-lab errors remained **100% invariant**:
     - `lab.value`: 4 in baseline $\rightarrow$ 4 in V1
     - `med.name`: 2 in baseline $\rightarrow$ 2 in V1
     - `med.route`: 2 in baseline $\rightarrow$ 2 in V1
     - `med.frequency`: 1 in baseline $\rightarrow$ 1 in V1
     - `med.dose`: 0 in baseline $\rightarrow$ 0 in V1
     - `MED_*` documents: 4 in baseline $\rightarrow$ 4 in V1
     - `NARRATIVE_*` documents: 1 in baseline $\rightarrow$ 1 in V1

2. **Mechanism of Action Verification:**
   - In the baseline, Qwen attempted numerical interval arithmetic on rows like `Albumin 5.6 g/dL 3.5 - 5.0 H`, erroneously asserting that $5.6 \in [3.5, 5.0]$, and classifying `Albumin flag: HIGH` as a discrepancy.
   - Under the V1 prompt, Qwen respected the explicit abnormal flag `'H'`, recovering 27 previously misclassified `SUPPORTED` claims.
   - Zero non-flag claims received the instruction, ensuring complete experimental isolation.

---

## 6. Comprehensive Breakdown of Remaining 36 Stage-C Errors

The remaining 36 Stage-C errors fall into clear, distinct semantic failure modes:

### A. Remaining `lab.flag` Errors (27 errors)

#### 1. Context-Anchoring & Value Expectation (15 errors: `SUPPORTED` $\rightarrow$ `DISCREPANCY`)
- **Phenomenon:** The model correctly identifies the flag in the document (e.g. `Alkaline Phosphatase 29.0 U/L 44 - 147 L` or `White Blood Cell Count 3.5 10*3/uL 4.5 - 11.0 L`), but rejects the claim because:
  - **Prior Cascade Bias:** The model notes in its rationale that the `[CASCADE STAGE CONTEXT]` predicted `DISCREPANCY (confidence: 1.00)` from Stage B and defers to it.
  - **Missing Numeric Value Expectation:** The model asserts that because the extracted claim only states `Albumin flag: HIGH` without stating the numeric value `5.6 g/dL`, the claim is incomplete and therefore a `DISCREPANCY`.

#### 2. Unflagged Normal / Absence Reasoning (8 errors: `DISCREPANCY` $\rightarrow$ `INSUFFICIENT_EVIDENCE`)
- **Phenomenon:** The claim asserts an abnormal flag (e.g. `Total Bilirubin flag: HIGH`), but the document row shows `Total Bilirubin 0.1 mg/dL 0.1 - 1.2` with an empty abnormal column.
- **Root Cause:** Although rule 1 states that an unflagged cell denotes NORMAL, Qwen outputs: *"The document does not contain an explicit flag indicator for Total Bilirubin... therefore there is insufficient evidence to support or refute the claim."* The model treats the absence of a token as a lack of evidence rather than an explicit assertion of normality.

#### 3. Row-Bleed / Multi-Line Flag Misalignment (4 errors: `DISCREPANCY` $\rightarrow$ `SUPPORTED`)
- **Phenomenon:** When tests are tightly stacked in OCR text (e.g. `Glucose 102.0 mg/dL 70 - 99 H\nBUN 12.0 mg/dL 7 - 20`), Qwen misassociates the trailing `'H'` from the preceding row with the subsequent row, asserting that BUN is flagged HIGH.

---

### B. Remaining Non-Flag Errors (9 errors)

#### 1. Entity Discrepancy vs Missing Entity (4 errors)
- **`med.name` (2 errors, `INSUFFICIENT_EVIDENCE` $\rightarrow$ `DISCREPANCY`):**
  - Document lists `Losartan 100 mg` or `Metoprolol 100 mg`. Claim asserts `Medication: Insulin glargine`.
  - Qwen rules this a `DISCREPANCY` (*"direct conflict: Insulin glargine is not ordered"*), whereas benchmark ground truth treats the absence of the medication as `INSUFFICIENT_EVIDENCE`.
- **`med.frequency` (1 error, `DISCREPANCY` $\rightarrow$ `INSUFFICIENT_EVIDENCE`):**
  - Claim asserts `frequency: four times daily`. Document states `initiated at 2.5 mg PO every morning`.
  - Qwen classifies as `INSUFFICIENT_EVIDENCE` because "four times daily is not mentioned", rather than identifying the conflict with "every morning".
- **`lab.value` (1 error, `INSUFFICIENT_EVIDENCE` $\rightarrow$ `DISCREPANCY`):**
  - Document shows `Hematocrit: 45.7 %`. Claim shows `Hematocrit: 45.7 %`. Qwen hallucinates a patient ID discrepancy between document and claim metadata.

#### 2. Clinical Abbreviation & Synonyms (2 errors: `DISCREPANCY` $\rightarrow$ `SUPPORTED`)
- **`med.route` (2 errors):**
  - Claim asserts route `oral` or `PO`. Document specifies `by mouth`. Qwen recognizes the clinical synonymy and predicts `SUPPORTED`, conflicting with benchmark ground truth expecting exact surface-form alignment.

#### 3. Unlinked Grounded Values (3 errors: `INSUFFICIENT_EVIDENCE` $\rightarrow$ `SUPPORTED`)
- **`lab.value` (3 errors):**
  - Claims for `Calcium: 9.3 mg/dL`, `Total Protein: 6.8 g/dL`, and `Sodium: 137 mmol/L`.
  - The text snippet contains the exact values. Ground truth is labeled `INSUFFICIENT_EVIDENCE` due to benchmark span unlinking, but Qwen sees the text verbatim in the context window and marks it `SUPPORTED`.

---

## 7. Controlled Experiment Conclusion & Verdict

### Evaluator Verdict: **SUPPORTED BY THE CONTROLLED EVIDENCE**

**Justification:**
1. **Hypothesis Confirmed:** The hypothesis that Stage-C OOD degradation was driven by tabular flag misinterpretation and numerical interval arithmetic in `template_c` is empirically proven.
2. **Dramatic Error Reduction:** Stage C accuracy increased from **82.94% to 90.55%** (+7.61 percentage points), cutting overall Stage C errors by **44.6%** (65 down to 36) and `lab.flag` errors by **51.8%** (56 down to 27).
3. **Pure Specificity:** The error reduction was strictly confined to `lab.flag` and `template_c` (exactly 29 errors eliminated in both dimensions). Non-lab fields exhibited **zero drift and zero regressions**.
4. **End-to-End Impact:** Full cascade Macro-F1 improved from **0.9422 to 0.9538**, and discrepancy recall improved from **0.9806 to 0.9826**.

The intervention is validated as an unequivocal success under strictly controlled experimental conditions.
