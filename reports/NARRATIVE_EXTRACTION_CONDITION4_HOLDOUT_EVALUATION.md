# Frozen Condition 4 Narrative Extraction Holdout Evaluation

**Document:** `Original_Discharge_Summary.pdf`  
**Institution:** Christian Medical College (CMC) Vellore (Obstetrics & Gynaecology Unit V)  
**Evaluator:** Antigravity AI Engine  
**Date:** October 7, 2026  
**Pipeline Evaluated:** Frozen Condition 4 (Gated Layout Serialization + Dual-Pass Extraction + Lab Negative Boundary)  
**Evaluation Status:** FROZEN READ-ONLY HOLDOUT EXPERIMENT (No code, prompt, gate, or config modifications)  
**Final Verdict:** **SIGNIFICANT GENERALIZATION DROP** (Precision preserved at 100.0%, but Recall collapsed from 95.24% to 36.36% due to domain shift from numeric chemistry panels to qualitative serology and compound prescription syntax)

---

## 1. Objective

Following the controlled extraction architecture experiments (Conditions 0 through 4), Condition 4 demonstrated strong performance on the 3-document development pilot:
- **Precision:** 95.24% (40/42)
- **Recall:** 95.24% (39/41)
- **F1 Score:** 95.24%
- **Grounding Compliance:** 100.0%

However, the development cohort consisted of three pediatric cardiac surgical discharge summaries (`sample.pdf`, `sample2.pdf`, `sample3.pdf`), which directly informed the prompt exemplars, negative lab boundaries, and layout gate thresholds. 

The objective of this evaluation is to execute a **strictly frozen, uncompromised holdout evaluation** of Condition 4 on `Original_Discharge_Summary.pdf`—a completely unseen, independent real-world clinical hospital document—to measure out-of-distribution generalizability without post-hoc tuning.

---

## 2. Frozen Evaluation Protocol

To prevent evaluation bias and data leakage, the following scientific protocol was enforced:

1. **Strictly Frozen State:**
   - Zero changes to `src/clindoc/*`, prompts, JSON schemas, layout gate thresholds, or negative boundary rules.
   - Zero commits, pushes, or modifications to project specifications.
2. **Two-Pass Domain Architecture:**
   - Pass A: Medication pass executed with `MEDICATION_SYSTEM_PROMPT` and `SCHEMA_MED`.
   - Pass B: Laboratory pass executed with `LAB_SYSTEM_PROMPT_SAFEGUARDED` and `SCHEMA_LAB`.
3. **Layout Gating:**
   - Evaluated using `detect_multicolumn_table(ocr_lines, page_num)`. Passthrough sequential text if no multi-column key-value table detected; layout-aware spatial reconstruction if detected.
4. **Verbatim Grounding Enforcement:**
   - Extractions filtered against source page text via exact substring check (`evidence_text in page_text`).
5. **Independent Ground Truth Formulation:**
   - Clinical ground truth established strictly from the source document text under the Tier 1 extraction contract (`med.name`, `med.dose`, `unit_raw`, `med.frequency`, `med.route`, `lab.value`, `lab.flag`).
6. **No Retries or Post-Hoc Patching:**
   - All errors, omissions, and format mismatches recorded as genuine pipeline failures.

---

## 3. Holdout Document Description

- **Filename:** `Original_Discharge_Summary.pdf`
- **Internal Storage Key:** `scratch/blobs/1f/1f87afe9fb9b53e45f6d00a9b03ddfc8d5d99fc39cb1cc087c5e7011120d0c05.enc`
- **Document UUID:** `1a58f0eb-62ba-415a-bfa8-bc0d9ba2d389`
- **OCR Cache:** `data/ocr_cache/clindoc_job_1a58f0eb_7pp2q9ld.json` (PaddleOCR, 11,474 lines)
- **Facility / Source:** Christian Medical College (CMC) Vellore, Obstetrics & Gynaecology Unit V
- **Modality:** Scanned institutional document (2 pages, 217.7 KB, 300 DPI, institutional stamps)
- **Clinical Summary:**
  - **Page 1:** Admission details, obstetric delivery history (primi gravida at 39 weeks, suction cup delivery, maternal examination vitals), and **antenatal maternal investigations** (`HBsAg Negative`, `HIV ELISA Negative`, `VDRL Non Reactive`, `Haemoglobin 12.6 GM%`, `Blood Grouping B Rh Positive`).
  - **Page 2:** Neonatal delivery outcome (birth weight 2.76 kg, APGAR 9,10), postpartum course, and **discharge recommendations** containing outpatient prescriptions (`C.Autrin / T.Shelcal once daily for 6 weeks`, `Tab Metacin 1g as and when needed`).

---

## 4. Ground-Truth Definition

Ground truth was established by clinical review of the document text under the existing extraction contract:

| Target ID | Field Type | Page | Expected Value | Normalized Value | Expected Unit | Source Evidence Text |
| :--- | :--- | :---: | :--- | :--- | :---: | :--- |
| `holdout_med_autrin_name` | `med.name` | 2 | `C.Autrin` | `autrin` | — | `C.Autrin / T.Shelcal once daily for 6 weeks` |
| `holdout_med_autrin_freq` | `med.frequency`| 2 | `once daily` | `once daily` | — | `C.Autrin / T.Shelcal once daily for 6 weeks` |
| `holdout_med_shelcal_name` | `med.name` | 2 | `T.Shelcal` | `shelcal` | — | `C.Autrin / T.Shelcal once daily for 6 weeks` |
| `holdout_med_shelcal_freq` | `med.frequency`| 2 | `once daily` | `once daily` | — | `C.Autrin / T.Shelcal once daily for 6 weeks` |
| `holdout_med_metacin_name` | `med.name` | 2 | `Tab Metacin` | `metacin` | — | `Tab Metacin 1g as and when needed` |
| `holdout_med_metacin_dose` | `med.dose` | 2 | `1` | `1` | `g` | `Tab Metacin 1g as and when needed` |
| `holdout_med_metacin_freq` | `med.frequency`| 2 | `as and when needed` | `as and when needed` | — | `Tab Metacin 1g as and when needed` |
| `holdout_lab_hb_val` | `lab.value` | 1 | `12.6` | `12.6` | `GM%` | `HAEMOGLOBIN\n12.6\nGM%` |
| `holdout_lab_hbsag_flag` | `lab.flag` | 1 | `NEGATIVE` | `negative` | — | `HBSAG\nNEGATIVE` |
| `holdout_lab_hiv_flag` | `lab.flag` | 1 | `NEGATIVE` | `negative` | — | `HIV ELISA\nNEGATIVE` |
| `holdout_lab_vdrl_flag` | `lab.flag` | 1 | `NON REACTIVE` | `non reactive` | — | `VDRL\nNON REACTIVE` |

**Total Ground-Truth Targets:** **11 atomic clinical targets** (7 medications across Page 2; 4 lab determinations across Page 1).

---

## 5. Raw Prediction Summary

The frozen pipeline executed across both pages with the following raw outputs:

### Page 1 (Antenatal Course & Investigations):
- **Layout Gate:** Passthrough (Sequential OCR; `is_table=False`, no multi-column key-value grid detected).
- **Pass A (Medication):** 0 extractions (Correct: zero medications present on Page 1).
- **Pass B (Laboratory):** 1 raw extraction:
  - `field_type: "lab.value"`, `value: "12.6"`, `unit_raw: "GM%"`, `evidence_text: "HAEMOGLOBIN 12.6 GM%"`, `confidence: 0.95`.
  - **Grounding Filter Outcome:** **REJECTED.** In the PaddleOCR raw text stream, `HAEMOGLOBIN`, `12.6`, and `GM%` appeared as three vertical newline-separated lines (`HAEMOGLOBIN\n12.6\nGM%`). Because Qwen normalized the whitespace into a single space (`"HAEMOGLOBIN 12.6 GM%"`), exact string membership (`ev_text in page_text`) evaluated to `False`.

### Page 2 (Postpartum Course & Discharge Advice):
- **Layout Gate:** Passthrough (Sequential OCR; `is_table=False`).
- **Pass A (Medication):** 4 raw extractions:
  1. `med.name`: `"C.Autrin"` (evidence: `"C.Autrin / T.Shelcal once daily for 6 weeks"`) $\rightarrow$ **Grounded & Valid**
  2. `med.frequency`: `"once daily"` (evidence: `"C.Autrin / T.Shelcal once daily for 6 weeks"`) $\rightarrow$ **Grounded & Valid**
  3. `med.dose`: `"1"`, `unit_raw: "g"` (evidence: `"Tab Metacin 1g as and when needed"`) $\rightarrow$ **Grounded & Valid**
  4. `med.frequency`: `"as and when needed"` (evidence: `"Tab Metacin 1g as and when needed"`) $\rightarrow$ **Grounded & Valid**
- **Pass B (Laboratory):** 0 extractions (Correct: zero labs on Page 2). Crucially, infant birth weight (`2.76 kg`) and height (`49 cm`) were **correctly ignored** by the safeguarded lab prompt negative boundary.

---

## 6. Overall Metrics

Evaluation against the 11 authoritative ground-truth targets yields the following metrics:

### Primary Pipeline Results (Post-Grounding Filter):
- **Precision (Strict Exact):** **100.00%** (4/4)
- **Precision (Normalized):** **100.00%** (4/4)
- **Recall (Strict Exact):** **36.36%** (4/11)
- **Recall (Normalized):** **36.36%** (4/11)
- **F1 Score (Strict Exact):** **53.33%**
- **F1 Score (Normalized):** **53.33%**
- **True Positives (TP):** **4**
- **False Positives (FP):** **0**
- **False Negatives (FN):** **7**
- **Grounding Compliance Rate:** **100.00%** (4/4 grounded claims admitted to pipeline)

### Raw Model Extraction (Pre-Grounding Filter):
- **Precision:** **100.00%** (5/5)
- **Recall:** **45.45%** (5/11)
- **F1 Score:** **62.50%**
- **True Positives (TP):** **5**
- **False Positives (FP):** **0**
- **False Negatives (FN):** **6**

---

## 7. Per-Field Metrics

| Field Type | Ground Truth | Predictions (Pipeline) | True Positives | False Positives | False Negatives | Precision | Recall | F1 Score |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`med.name`** | 3 | 1 | 1 | 0 | 2 | **100.0%** | **33.3%** | **50.0%** |
| **`med.dose`** | 1 | 1 | 1 | 0 | 0 | **100.0%** | **100.0%** | **100.0%** |
| **`med.frequency`**| 3 | 2 | 2 | 0 | 1 | **100.0%** | **66.7%** | **80.0%** |
| **`med.route`** | 0 | 0 | 0 | 0 | 0 | — | — | — |
| **`lab.value`** | 1 | 0 | 0 | 0 | 1 | **0.0%** | **0.0%** | **0.0%** |
| **`lab.flag`** | 3 | 0 | 0 | 0 | 3 | **0.0%** | **0.0%** | **0.0%** |
| **Overall** | **11** | **4** | **4** | **0** | **7** | **100.0%** | **36.4%** | **53.3%** |

---

## 8. Detailed Error Analysis

The evaluation revealed **zero False Positives (FP = 0)**. The pipeline never hallucinated non-existent drugs, never invented phantom lab values, and successfully rejected infant vitals. 

However, **seven False Negatives (FN = 7)** occurred due to four distinct failure mechanisms:

### Failure Mode 1: Verbatim Substring Grounding Whitespace Mismatch (1 FN)
- **Missed Target:** `HAEMOGLOBIN 12.6 GM%` (`lab.value`: `12.6`).
- **Mechanism:** Qwen extracted the test value correctly with 95% confidence. However, Qwen generated `evidence_text: "HAEMOGLOBIN 12.6 GM%"`, replacing the raw OCR line break (`\n`) with a standard space (` `). The pipeline's naive grounding check (`if ev_text in page_text:`) failed because `"HAEMOGLOBIN 12.6 GM%"` does not match `"HAEMOGLOBIN\n12.6\nGM%"`.
- **Impact:** Converted a valid model extraction into a pipeline false negative.

### Failure Mode 2: Qualitative Serology vs Numeric Lab Schema Mismatch (3 FN)
- **Missed Targets:** `HBSAG NEGATIVE`, `HIV ELISA NEGATIVE`, `VDRL NON REACTIVE`.
- **Mechanism:** The prompt specification for Pass B strictly dictates:
  > *"You MUST extract ONLY these field types: lab.value : numeric laboratory test result value (e.g. 13.5, 95, 16.1)... lab.flag : interpretation flag (NORMAL, HIGH, LOW, CRITICAL only)"*
- **Root Cause:** In obstetric discharge summaries, maternal infection screens report qualitative findings (`NEGATIVE`, `NON REACTIVE`). Because the prompt strictly restricted `lab.value` to numeric quantities and `lab.flag` to standard severity flags (`NORMAL/HIGH/LOW/CRITICAL`), Qwen obediently suppressed these qualitative serology determinations.

### Failure Mode 3: Compound Slash-Delimited Prescription Truncation (1 FN)
- **Missed Target:** `T.Shelcal` (`med.name`: `Shelcal`, `med.frequency`: `once daily`).
- **Mechanism:** The input prescription read: `"C.Autrin / T.Shelcal once daily for 6 weeks"`. Qwen parsed `C.Autrin` and attached the `once daily` frequency, but failed to bifurcate the slash-separated entity to create an independent claim for `T.Shelcal`.
- **Root Cause:** Compound drug combinations sharing a single frequency clause are common in manual hospital charts but were absent from the single-drug-per-line development cohort.

### Failure Mode 4: Entity Disconnection in Inline Prescriptions (1 FN)
- **Missed Target:** `Metacin` (`med.name`: `Metacin`).
- **Mechanism:** From the line `"Tab Metacin 1g as and when needed"`, Qwen extracted `med.dose: "1 g"` and `med.frequency: "as and when needed"`, but omitted the `med.name` claim. Qwen extracted the dose and frequency attributes while dropping the parent entity name.

---

## 9. Latency and Execution Profile

| Metric | Measured Value |
| :--- | :--- |
| **Total Pages Processed** | 2 pages |
| **Total Qwen Inference Calls** | 4 calls (2 passes $\times$ 2 pages) |
| **Total Execution Latency** | **15.08 seconds** |
| **Average Latency per Call** | **3.77 seconds** |
| **Page 1 Latency** | 5.51s (Med: 2.08s, Lab: 3.43s) |
| **Page 2 Latency** | 9.56s (Med: 7.81s, Lab: 1.75s) |
| **Retries / Timeouts** | 0 retries |
| **Schema Validation Errors** | 0 errors (100% JSON schema compliance) |
| **Layout Gate Activations** | 0 / 2 pages (Correct passthrough) |

---

## 10. Development vs Holdout Comparison

| Dimension | Condition 4 Development Pilot ($N=3$) | Condition 4 Holdout Evaluation ($N=1$) | Absolute Delta |
| :--- | :---: | :---: | :---: |
| **Source Diversity** | 3 Hospitals (Narayana, DMH, Reliance) | 1 Hospital (CMC Vellore) | Completely Unseen |
| **Clinical Specialty** | Pediatric Cardiac Surgery | Obstetrics & Gynaecology | Major Domain Shift |
| **Total Targets** | 41 targets | 11 targets | — |
| **Precision (Normalized)** | **95.24%** (40/42) | **100.00%** (4/4) | **+4.76%** |
| **Recall (Normalized)** | **95.24%** (39/41) | **36.36%** (4/11) | **-58.88%** |
| **F1 Score (Normalized)** | **95.24%** | **53.33%** | **-41.91%** |
| **Grounding Rate** | **100.00%** | **100.00%** | **0.00%** |
| **False Positives (FP)** | 2 | 0 | **0 FP (Zero Hallucination)** |
| **False Negatives (FN)** | 2 | 7 | **+5 FN** |
| **Layout Gate Accuracy** | 100% (Activated on DMH/Reliance) | 100% (Passed through CMC Vellore) | Consistent |
| **Vital Sign Rejection** | 100% (Replaced 8.5 kg FP) | 100% (Rejected 2.76 kg baby weight) | Stable |

---

## 11. Generalization Interpretation

This frozen holdout evaluation provides crucial scientific clarity:

1. **Precision Generalization is Robust (100%):**
   - The negative boundary rules designed during Condition 4 successfully protected the system. In this obstetric document, infant weight (`2.76 kg`), maternal weight, maternal pulse (`72/min`), and blood pressure (`100/60 mmHg`) were completely ignored by Pass B. The pipeline did not emit a single false positive.
2. **Recall Suffered Severe Domain Shift (-58.88%):**
   - The development cohort tuned Condition 4 exclusively on **numeric chemistry/hematology panels**. CMC Vellore introduced **qualitative maternal serology screens** (`HBsAg Negative`, `VDRL Non Reactive`), which were filtered out by the model because the prompt strictly demanded numeric results.
3. **Grounding Brittleness Exposed:**
   - The rigid substring grounding check (`ev_text in page_text`) broke when Qwen normalized OCR line breaks into whitespace, rejecting a valid hemoglobin extraction.
4. **Prescription Parsing Generalization Gap:**
   - Slash-delimited compound prescriptions (`C.Autrin / T.Shelcal once daily`) and inline narrative prescriptions caused entity truncation that did not occur in tabular dot-leader prescriptions.

---

## 12. Limitations

- **Holdout Sample Size ($N=1$):**
  - While CMC Vellore represents a genuinely independent hospital and clinical specialty, an $N=1$ evaluation cannot establish formal statistical confidence intervals.
- **Single Specialty:**
  - Evaluates obstetric discharge summaries; does not represent oncology, neurology, or intensive care discharge documentation.
- **Frozen Protocol Constraints:**
  - By protocol, no fixes were applied during this run.

---

## 13. Conclusion and Final Verdict

### Final Verdict: **SIGNIFICANT GENERALIZATION DROP**

- **Justification:**
  While Precision remained flawless at **100.00%** with zero false positives or hallucinations, the normalized Recall plummeted from **95.24%** in development down to **36.36%** on the holdout, driving overall F1 from **95.24%** down to **53.33%** (-41.91 percentage points).
- **Core Cause:**
  The drop was not caused by random model instability or hallucination, but by identifiable **clinical domain shifts** (qualitative serological tests vs numeric chemistry panels) and **syntactic formatting variations** (slash-compound drug lines and newline OCR breaks).
- **Engineering Recommendation:**
  Condition 4 should **not** be deployed to production in its current form. Prior to production release, the extraction architecture requires:
  1. A whitespace/newline-resilient grounding validation filter.
  2. Support for qualitative laboratory results (e.g., serology screens, `lab.flag: NEGATIVE / NON REACTIVE`).
  3. Compound prescription bifurcation logic in Pass A.
  4. Expanded multi-specialty validation across at least 15–25 diverse documents.

---

## 14. Artifacts Saved

- Machine-readable raw predictions: [`scratch/extraction_experiment/condition_4_holdout_raw.json`](file:///d:/daa/scratch/extraction_experiment/condition_4_holdout_raw.json)
- Machine-readable evaluation results: [`scratch/extraction_experiment/condition_4_holdout_results.json`](file:///d:/daa/scratch/extraction_experiment/condition_4_holdout_results.json)
- Authoritative evaluation report: [`reports/NARRATIVE_EXTRACTION_CONDITION4_HOLDOUT_EVALUATION.md`](file:///d:/daa/reports/NARRATIVE_EXTRACTION_CONDITION4_HOLDOUT_EVALUATION.md)
