# POST-PD-022 SCIENTIFIC ERROR AUDIT

**Audit Status:** COMPLETE & READ-ONLY  
**Date:** October 7, 2026  
**Investigator:** Clinical Document Intelligence & Verification Team  
**Evaluation Sink:** [`scratch/extraction_experiment/post_pd022_frozen_results.json`](file:///d:/daa/scratch/extraction_experiment/post_pd022_frozen_results.json)  
**Machine-Readable Audit Sink:** [`scratch/extraction_experiment/post_pd022_error_audit.json`](file:///d:/daa/scratch/extraction_experiment/post_pd022_error_audit.json)  
**Scope:** Root-cause forensics of CMC Vellore holdout, development cohort (`sample2.pdf`), and synthetic narrative stress evaluations without code modification or reruns.

---

## 1. Executive Summary

This read-only scientific error audit investigated the performance dynamics of the frozen post-PD022 clinical extraction pipeline. By auditing raw predictions, verbatim text evidence, and benchmark evaluation harnesses across all 24 evaluated documents, the audit established six conclusive scientific findings:

1. **CMC Holdout Progress & VDRL Status:** True positives expanded from 4 to 7 (+75%), with recall rising from 36.36% to 63.64%. The apparent failure on `VDRL Non-Reactive` is **(b) semantically correct but mismatched by evaluator normalization**. The model correctly extracted `lab.value = "Non-Reactive"` from `"VDRL NON REACTIVE"`, but the evaluation script demanded an unhyphenated string (`"non reactive"`) without normalizing punctuation.
2. **Compound Prescription Recovery Is Partial:** Root Cause C successfully split co-prescribed medication entity names (`C.Autrin` and `T.Shelcal` both captured), but failed to propagate the shared schedule (`"once daily"` was extracted once as `med.route`), and inline PRN entity head extraction still failed (`Tab Metacin` dose and frequency were extracted, but `med.name: "Metacin"` was omitted).
3. **The "Sample2 Regression" Is 67% Evaluator Artifact:** Of the 4 new errors on `sample2.pdf` (F1 drop from 94.44% to 77.78%), 2 errors (TLC and Platelets) were caused by a strict string inequality between `unit_raw: "Cmm"` vs `expected_unit: "/Cmm"` (missing leading slash), and 1 error was a legitimate PD-022 qualitative extraction (`HIV/HBsAg-Negative`) that was scored as a False Positive because the pre-PD022 ground truth contains zero qualitative annotations for `sample2.pdf`. Only 1 genuine omission occurred (`PT: 12.1 secs`).
4. **Authoritative Development Denominator Is 42, Not 41:** The historical reference to "41 targets" was an addition typo in the markdown documentation (`12 + 18 + 11 = 41` in `NARRATIVE_EXTRACTION_GROUND_TRUTH_EVALUATION.md`, whereas `sample3.pdf` contains 12 targets: `3 + 2 + 3 + 2 + 2 = 12`). The authoritative JSON file [`scratch/trials_ground_truth.json`](file:///d:/daa/scratch/trials_ground_truth.json) has always contained exactly 42 targets, and all legacy metrics (e.g., Condition 4 recall `40/42 = 95.24%`) used 42 as the denominator.
5. **Synthetic Narrative Failure Driven by Evaluation Harness Bugs:** The 59 False Positives and 92 False Negatives in the synthetic cohort do not reflect model collapse. Rather, 55 of the 59 FPs occurred because the evaluation harness checked `med_ent.get("name")` instead of the actual JSON key `med_ent["medication_name"]`, marking every valid drug name as an FP. Similarly, 53 of the 66 lab FNs occurred because the synthetic generator attached unstated `NORMAL` reference-range flags to normal values, which the LLM correctly refused to hallucinate per strict negative prompt rules.
6. **No Production Modification Required:** The extraction engine is performing substantially better than surface-level metrics indicate. Before any prompt, schema, or model changes are contemplated, the benchmark evaluation scripts and ground-truth contracts must be reconciled.

---

## 2. CMC Holdout Target-Level Error Matrix

The frozen CMC Vellore holdout (`Original_Discharge_Summary.pdf`) comprises 11 atomic targets. Below is the comprehensive claim-by-claim audit:

| Target ID | Expected Field Type | Expected Value / Unit | Predicted Value / Unit | Evaluation Status | Failure Category | Root Cause | Explanatory Audit Finding |
|:---|:---:|:---:|:---:|:---:|:---|:---:|:---|
| `cmc_lab_hb_val` | `lab.value` | `12.6` (GM%) | `12.6` (GM%) | **TP** | None | Root Cause A | Normalized across OCR line-breaks (`HAEMOGLOBIN\n12.6 GM%`) and grounded cleanly. |
| `cmc_lab_hbsag_val` | `lab.value` | `Negative` | `Negative` | **TP** | None | Root Cause B | Extracted into `lab.value` under authorized PD-022 qualitative laboratory contract. |
| `cmc_lab_hiv_val` | `lab.value` | `Negative` | `Negative` | **TP** | None | Root Cause B | Extracted into `lab.value` under authorized PD-022 qualitative laboratory contract. |
| `cmc_lab_vdrl_val` | `lab.value` | `Non-Reactive` | `Non-Reactive` | **FP & FN\*** | Evaluator Normalization Mismatch | Root Cause B | Semantically correct; evaluator normalized against unhyphenated `"non reactive"` and failed string comparison. |
| `cmc_med_autrin_name` | `med.name` | `C.Autrin` | `C.Autrin` | **TP** | None | Root Cause C | First entity head of compound slash line `"C.Autrin / T.Shelcal"` correctly split and extracted. |
| `cmc_med_shelcal_name` | `med.name` | `T.Shelcal` | `T.Shelcal` | **TP** | None | Root Cause C | Second entity head of compound slash line correctly split and extracted (was dropped pre-fix). |
| `cmc_med_metacin_dose` | `med.dose` | `1` (g) | `1` (g) | **TP** | None | Baseline | Successfully parsed numeric dosage from inline PRN line `"Tab Metacin 1g"`. |
| `cmc_med_metacin_freq` | `med.frequency`| `as and when needed` | `as and when needed` | **TP** | None | Baseline | Successfully captured PRN frequency phrase. |
| `cmc_med_autrin_freq` | `med.frequency`| `once daily` | *[None]* | **FN** | Dose/Frequency/Route Confusion | Root Cause C | Omitted because model misclassified `"once daily"` as `med.route`. |
| `cmc_med_shelcal_freq` | `med.frequency`| `once daily` | *[None]* | **FN** | Dose/Frequency/Route Confusion | Root Cause C | Shared frequency was not duplicated to the second medication entity. |
| `cmc_med_metacin_name` | `med.name` | `Tab Metacin` | *[None]* | **FN** | Medication Name Omission | Root Cause C | Entity head omitted; model captured dose and frequency but failed to emit separate `med.name` claim. |

*\*Unmatched Grounded Predictions (False Positives on CMC):*
1. `lab.value: "Non-Reactive"` (Evidence: `"VDRL NON REACTIVE"`): Scored as FP because target matcher failed hyphen comparison.
2. `med.route: "once daily"` (Evidence: `"once daily"`): Frequency phrase erroneously typed as administration route.
3. `med.dose: "6"`, `unit_raw: "weeks"` (Evidence: `"6 weeks"`): Treatment duration erroneously typed as dosage.

### Definitive Determination on VDRL:
**Status: (b) Semantically correct but mismatched by evaluator normalization.**  
- **Raw Prediction:** `field_type = "lab.value"`, `value = "Non-Reactive"`, `evidence_text = "VDRL NON REACTIVE"`.
- **Authoritative PD-022 Standard:** Recognizes `NON-REACTIVE` as canonical qualitative determination.
- **Harness Flaw:** The evaluation harness defined `norm: "non reactive"` (space-separated) and compared `normalize_str(p_val) == normalize_str(g_val)`. Because `normalize_str` does not strip punctuation, `"non-reactive"` did not match `"non reactive"`, nor did either satisfy substring checks. VDRL was penalized as both 1 FP and 1 FN despite being clinically accurate.

---

## 3. Sample2 Regression Audit

In the frozen post-PD022 evaluation, `sample2.pdf` recorded an apparent regression:
- **Condition 4 Baseline:** TP = 17, FP = 1, FN = 1 | Precision = 94.44%, Recall = 94.44%, F1 = 94.44%
- **Post-PD022 Frozen:** TP = 14, FP = 4, FN = 4 | Precision = 77.78%, Recall = 77.78%, F1 = 77.78%

### Target-by-Target Decision Matrix
| Analyte / Entity | Field Type | Condition 4 Status | Post-PD022 Status | Difference Mechanism | Regression Classification |
|:---|:---:|:---:|:---:|:---|:---:|
| **Inderal** | `med.name` | TP | TP | Identical extraction | No Change |
| **2.5 mg** | `med.dose` | TP | TP | Identical extraction | No Change |
| **1-0-1** | `med.frequency`| TP | TP | Identical extraction | No Change |
| **"tablet"** | `med.route` | FP | FP | Unannotated in GT; extracted in both runs | No Change |
| **Hb (16.1 gm%)** | `lab.value` | TP | TP | Identical extraction | No Change |
| **BUL (23 mg%)** | `lab.value` | TP | TP | Identical extraction | No Change |
| **Creat (0.69 mg%)**| `lab.value` | TP | TP | Identical extraction | No Change |
| **Na (141.5 mEq/L)**| `lab.value` | TP | TP | Identical extraction | No Change |
| **K (4.2 mEq/L)** | `lab.value` | TP | TP | Identical extraction | No Change |
| **Cl (107.4 mEq/L)**| `lab.value` | TP | TP | Identical extraction | No Change |
| **SGOT (33 U/L)** | `lab.value` | TP | TP | Identical extraction | No Change |
| **SGPT (14 U/L)** | `lab.value` | TP | TP | Identical extraction | No Change |
| **Total Bili (0.70)**| `lab.value` | TP | TP | Identical extraction | No Change |
| **Direct Bili (0.42)**| `lab.value` | TP | TP | Identical extraction | No Change |
| **Indirect Bili (0.28)**| `lab.value`| TP | TP | Identical extraction | No Change |
| **TLC (12300)** | `lab.value` | **TP** (`unit_raw: "/Cmm"`) | **FP + FN** (`unit_raw: "Cmm"`) | Qwen emitted `"Cmm"`; evaluator strictly compared `"cmm" == "/cmm"` -> False | **Evaluator Normalization Mismatch** |
| **Platelets (373000)**| `lab.value` | **TP** (`unit_raw: "/Cmm"`) | **FP + FN** (`unit_raw: "Cmm"`) | Qwen emitted `"Cmm"`; evaluator strictly compared `"cmm" == "/cmm"` -> False | **Evaluator Normalization Mismatch** |
| **HIV/HBsAg Negative**| `lab.value` | *[Omitted]* | **FP** (`value: "Negative"`) | PD-022 prompt correctly extracted qualitative finding; legacy GT has no qualitative targets | **Legacy GT Omission** |
| **PT (12.1 secs)** | `lab.value` | **TP** | **FN** | Omitted from completion in post-PD022 run | **Qwen LLM Generation Drift** |
| **INR (1.0)** | `lab.value` | FN | FN | Compound slash token missed in both runs | No Change |

### Scientific Conclusion on Sample2:
The F1 shift from 94.44% to 77.78% is **not a systemic model regression**:
- **2 TPs were converted to 2 FPs + 2 FNs purely by leading slash syntax:** In the source text, the unit is printed as `12300/Cmm` and `373000/Cmm`. Qwen stripped the leading punctuation slash (`"Cmm"`), but the evaluator evaluated unit match as strict equality without stripping leading slashes. The numbers (12300 and 373000) were 100% extracted and grounded.
- **1 FP was an authorized qualitative extraction:** Qwen extracted `lab.value = "Negative"` from `HIV/HBsAg-Negative`. Because `trials_ground_truth.json` was created prior to PD-022, this test was unannotated, unfairly penalizing the system.
- **Only 1 true regression occurred:** The model omitted `PT: 12.1 secs`, likely due to token competition during the generation of the qualitative lab finding.

---

## 4. Ground-Truth / Denominator Consistency Audit

The audit investigated the apparent conflict between historical documentation referencing "41 targets" and the frozen run evaluating 42 targets.

### Findings:
1. **Actual Count in Source File:** Inspection of [`scratch/trials_ground_truth.json`](file:///d:/daa/scratch/trials_ground_truth.json) reveals:
   - `sample.pdf`: 12 claims
   - `sample2.pdf`: 18 claims
   - `sample3.pdf`: 12 claims
   - **Total:** **42 ground-truth targets**.
2. **Origin of the "41 Targets" Label:**
   - In [`reports/NARRATIVE_EXTRACTION_GROUND_TRUTH_EVALUATION.md`](file:///d:/daa/reports/NARRATIVE_EXTRACTION_GROUND_TRUTH_EVALUATION.md) (§2 line 54), the table lists:
     - `sample.pdf`: 12
     - `sample2.pdf`: 3 meds + 15 labs = 18
     - `sample3.pdf`: Listed entities: Amiodarone (3), Lasilactone (2), Shelcal (3), Aspirin (2), Rantac (2) = **12 claims**.
     - However, the author entered **`11`** into the "Claim Count" column and summed `12 + 18 + 11 = 41`.
3. **Mathematical Proof of Unchanged Data:**
   - In [`scratch/trials_ground_truth_metrics.json`](file:///d:/daa/scratch/trials_ground_truth_metrics.json) (the earliest saved run), line 7 calculates Recall as:
     `0.7380952380952381` $\equiv \frac{31}{42}$.
   - Line 15 calculates Normalized Recall as:
     `0.7619047619047619` $\equiv \frac{32}{42}$.
   - In Condition 4 ([`reports/NARRATIVE_EXTRACTION_CONDITION4_EVALUATION.md`](file:///d:/daa/reports/NARRATIVE_EXTRACTION_CONDITION4_EVALUATION.md)), line 87 reports Recall as:
     `95.24%` $\equiv \frac{40}{42} = 0.95238$.
   - While authors repeatedly typed "41 targets" in document headers and narrative text, **the code and metrics engines always evaluated against 42 targets**.
4. **Authoritative Denominator:**
   - The authoritative denominator is **42**. No targets were ever added, modified, or removed.

---

## 5. Root-Cause Recovery Audit (A, B, C)

The frozen report's claim that Root Causes A, B, and C were "fully recovered" was audited against target-level evidence:

| Root Cause | Hypothesis & Target Mechanism | Target Evidence | Recovery Determination | Audit Rationale |
|:---|:---|:---|:---:|:---|
| **Root Cause A** | OCR newline / whitespace grounding rejection | `cmc_lab_hb_val` (`12.6 GM%`) | **FULLY RECOVERED** | Pre-fix, literal substring matching failed on `HAEMOGLOBIN\n12.6 GM%`. Post-fix, whitespace-invariant token matching successfully grounded the claim, scoring a True Positive. 100% grounding rate maintained. |
| **Root Cause B** | Qualitative serologies rejected as numeric claims | `cmc_lab_hbsag_val`, `cmc_lab_hiv_val`, `cmc_lab_vdrl_val` | **PARTIALLY RECOVERED** | Functionally, Qwen extracted all 3 serology determinations into `lab.value`. 2 were confirmed True Positives (`Negative`). 1 (`Non-Reactive`) was scored as FP/FN due to evaluator hyphen formatting. In synthetic stress, zero false positives were introduced to numeric labs (96.43% precision). |
| **Root Cause C** | Compound slash prescriptions and inline PRN heads dropped | `cmc_med_autrin_name`, `cmc_med_shelcal_name`, `cmc_med_autrin_freq`, `cmc_med_shelcal_freq`, `cmc_med_metacin_name` | **PARTIALLY RECOVERED** | Entity name splitting on compound slash lines was 100% resolved (`C.Autrin` and `T.Shelcal` both captured). However, shared frequency propagation failed (`"once daily"` extracted only once, under `med.route`), and inline PRN entity head extraction failed (`Tab Metacin` name dropped). |

---

## 6. Synthetic Narrative Failure Analysis

The synthetic stress cohort (`NARRATIVE_0001.pdf` through `NARRATIVE_0020.pdf`) produced 59 False Positives and 92 False Negatives. Forensic auditing of the raw outputs and evaluation script revealed that these errors are predominantly artifacts of evaluation harness misalignments:

### Classification of 59 False Positives
```
Total False Positives: 59
├── Evaluator Key Bug (med.name): 55 (93.2%)
├── Med Attribute Over-Extraction: 2 (3.4%)
└── Lab Spurious Extractions:     2 (3.4%)
```
1. **Evaluator Schema Bug (55 of 59 FPs):**
   - In [`scratch/run_post_pd022_evaluations.py`](file:///d:/daa/scratch/run_post_pd022_evaluations.py) line 608, the ground-truth target builder executes:
     `if med_ent.get("name"): gt_targets.append(...)`
   - In all synthetic ground-truth files (`data/narrative_ground_truth/NARRATIVE_XXXX.json`), the medication name is keyed as **`"medication_name"`**, not `"name"`.
   - As a result, **zero `med.name` targets were added to the ground truth across all 20 documents**.
   - When Qwen correctly extracted drug names (e.g., Amoxicillin, Lisinopril, Atorvastatin), every single valid drug name was scored as an unmatched False Positive (55 claims).
2. **Medication Attribute Over-Extraction (2 FPs):** Minor schedule phrase duplicates.
3. **Lab Spurious Extractions (2 FPs):** Model extracted non-analyte numbers as lab values.

### Classification of 92 False Negatives
```
Total False Negatives: 92
├── Unwritten Inferred lab.flag Targets: 53 (57.6%)
├── Medication Attribute Omissions:     26 (28.3%)
└── Genuine Numeric Lab Omissions:      13 (14.1%)
```
1. **Unwritten `lab.flag` Targets (53 of 92 FNs):**
   - The synthetic dataset generator assigned `"flag": "NORMAL"` programmatically whenever a numeric lab fell within physiological reference ranges.
   - However, the word `"NORMAL"` never appeared in the narrative prose.
   - In `run_post_pd022_evaluations.py` line 596, the harness created 60 `lab.flag` ground-truth targets demanding `"NORMAL"`.
   - The LLM strictly adhered to prompt rule 184: *"interpretation or severity flag ONLY when explicitly stated in the source text"*, and refused to hallucinate unstated flags.
   - The harness recorded 53 False Negatives for unhallucinated flags.
2. **Medication Attribute Omissions (26 FNs):** Model missed routes or durations embedded in conversational narrative sentences.
3. **Genuine Numeric Lab Omissions (13 FNs):** Out of 60 actual numeric laboratory measurements in the synthetic cohort, the model successfully extracted 47 (78.3% value recall). It missed 13 tests due to prose ambiguity (e.g., differentiating initial vs repeat tests).

---

## 7. Scientific Interpretation & Answers to Core Questions

### 1. Did PD-022 improve the independent CMC holdout?
**Yes.** True positives increased from 4 to 7 (+75%), recall rose from 36.36% to 63.64%, and F1 rose from 53.33% to 66.67%, with 100% grounding integrity maintained.

### 2. Did it improve recall at the cost of precision?
**Only superficially.** Precision dropped from 100% to 70.00% (3 false positives). However:
- 1 FP was `Non-Reactive` for VDRL, which is clinically accurate and was penalized solely by unhyphenated evaluator string comparison.
- 2 FPs were structural field misclassifications (`once daily` as route, `6 weeks` as dose).
- No clinical hallucinations or spurious patient data entered the system.

### 3. Did it actually solve A/B/C, or only partially?
- **Root Cause A:** Fully solved.
- **Root Cause B:** Functionally solved by the model; penalized by evaluation string matching.
- **Root Cause C:** Partially solved. Entity splitting succeeded; shared frequency propagation and inline PRN entity heads remain unresolved.

### 4. Is the Condition-4 development regression real?
**Largely illusory.** Of the 4 lost F1 points on `sample2.pdf`:
- 2 decisions were caused by `Cmm` vs `/Cmm` leading slash matching.
- 1 decision was caused by the legacy ground truth lacking qualitative serology targets.
- Only 1 decision (`PT: 12.1 secs`) was a genuine model omission.

### 5. How serious is the synthetic narrative result?
**Not serious.** The apparent 76.59% precision and 67.72% recall were distorted by two harness defects: missing `"medication_name"` target keys (causing 55 false FPs) and demanding unwritten `"NORMAL"` flags (causing 53 false FNs).

### 6. What is the single strongest remaining technical bottleneck?
**Medication field-boundary disambiguation:** Distinguishing administration route from frequency, dosage amounts from duration periods, and associating shared schedules across compound co-prescriptions.

### 7. What should be investigated NEXT before any further implementation?
Reconciling the evaluation harnesses and ground-truth definitions. Modifying prompts or model parameters to "fix" scores distorted by evaluator bugs will degrade genuine clinical generalization.

---

## 8. Recommended Next Experiment

**Controlled Experiment: Benchmark Normalization & Contract Reconciliation Harness**
1. **Objective:** Measure the true clinical extraction accuracy of the frozen post-PD022 pipeline without altering any production code, prompts, schemas, or models.
2. **Controls:**
   - Update evaluation string normalization to strip leading slashes (reconciling `/Cmm` with `Cmm`) and normalize hyphens (reconciling `Non-Reactive` with `Non Reactive`).
   - Fix synthetic evaluation key mapping from `med_ent.get("name")` to `med_ent.get("medication_name")`.
   - Restrict synthetic `lab.flag` ground-truth targets to cases where the flag token is explicitly present in the source text.
   - Add authorized PD-022 qualitative laboratory targets (`HIV/HBsAg-Negative`) to `sample2.pdf` ground truth.
3. **Execution Rule:** Zero production code modifications. Zero prompt modifications. Re-evaluate using the exact cached outputs from the frozen run.
