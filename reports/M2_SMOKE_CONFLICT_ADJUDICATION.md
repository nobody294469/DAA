# M2 Smoke Benchmark Ground-Truth Conflict Adjudication Report

**Date**: 2026-10-02  
**Auditor**: BenchmarkAuditor (DeterministicVerifier Rule Engine + Cache Alignment)  
**Dataset**: `scratch/smoke_regeneration/smoke_examples.jsonl` (261 examples across 4 documents)  
**Objective**: Adjudicate all 31 suspicious records and 24 ground-truth conflicts to determine whether conflicts represent legitimate clinical discrepancy/negative control cases or actual generator/evidence/label corruption.

---

## A. Executive Summary

| Metric | Count | Percentage |
| :--- | :--- | :--- |
| **Total Smoke Examples** | 261 | 100.0% |
| **Total Clean Examples** | 230 | 88.1% |
| **Total Suspicious Examples** | 31 | 11.9% |
| **Total Definitely Invalid Examples** | 0 | **0.0%** |
| **Total Ground-Truth Conflicts** | 24 | 9.2% |
| **Legitimate Generation Cases** | **24** | **100.0% of conflicts** |
| **Actual Data Corruption** | **0** | **0.0% of conflicts** |

### Breakdown of Suspicious Examples (31 Total)
- **24 Ground-Truth Conflicts**: Classified and detailed in Section B below (all 24 are verified to be legitimate clinical cases).
- **7 Non-Conflict Suspicious Examples**:
  - Valid examples where Stage A returned `INSUFFICIENT_EVIDENCE` (e.g. subtle qualitative values or wide text windows) matching ground truth `INSUFFICIENT_EVIDENCE`, but flagged by semantic heuristics as `TRUNCATED_OR_INCOMPLETE_EVIDENCE` because the evidence span was a partial context snippet.

### Key Finding
**Zero (0) corrupted `[0, 100]` fallback spans exist.**  
**Zero (0) out-of-bounds offsets exist.**  
**Zero (0) broken table row bisections exist.**  
**Zero (0) wrong-entity or mislabeled examples exist.**

All 24 conflicts are mathematically and clinically legitimate cases where:
1. **16 cases**: Clinical laboratory tests with normal results (Template C, where normal tests leave the "Abnormal" column blank) are claimed to have `flag: HIGH`. The ground truth is correctly `DISCREPANCY`, but Stage A's heuristic rule engine cannot prove a flag is absent without Stage B/C model inference and returns `INSUFFICIENT_EVIDENCE` (`no_flag_found_in_evidence`).
2. **5 cases**: Clinical narrative document-level negative controls (`Insulin glargine` or `Troponin I` not mentioned in narrative). The ground truth is correctly `INSUFFICIENT_EVIDENCE`. Stage A detected the word "not" in the sentinel string (`"Document-level absence: Target entity 'Insulin glargine' not observed in clinical narrative."`) and flagged it as `DISCREPANCY (negation_mismatch)`.
3. **2 cases**: Clinical medication discrepancies (`Amoxicillin route: intravenously` vs oral in narrative; `Atorvastatin frequency: once daily` vs every evening on Rx pad). The ground truth is correctly `DISCREPANCY`. Stage A returned `INSUFFICIENT_EVIDENCE` due to exact string/synonym boundaries.
4. **1 case**: Degraded OCR value distortion (`Glucose 86 mg/dL` on document where OCR corrupted `86.0` to `89.0`). The generator marked it `INSUFFICIENT_EVIDENCE` (original uncorrupted reading missing), while Stage A evaluated `86.0` against the corrupted `89.0` text as `DISCREPANCY`.

---

## B. Complete 24-Example Adjudication Table

| # | Example ID | Doc ID | Field Type | Claim | GT Label | Semantic Category | Stage A Result | Reason for Conflict | Target Entity | True/Ref Val Present? | Claimed Val Present? | Final Classification |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `267af211-7d87-4c99-8559-b31807d0d609` | `LAB_0002` | `lab.flag` | Carbon Dioxide flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Carbon Dioxide (Present) | Yes (`24.0 mmol/L 22-29`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 2 | `2c097383-093a-453e-b495-126c485d8f2b` | `LAB_0002` | `lab.flag` | Potassium flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Potassium (Present) | Yes (`4.2 mmol/L 3.5-5.1`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 3 | `82fce65f-b079-4dc8-bd07-029b24c1f990` | `LAB_0001` | `lab.value` | Glucose: 86 mg/dL | `INSUFFICIENT_EVIDENCE` | `CONTRADICTS_CLAIM` | `DISCREPANCY` | OCR degraded 86.0 -> 89.0; Stage A saw value mismatch | Glucose (Present) | Yes (`89.0 mg/dL` corrupted) | No (`86.0` absent) | `LEGITIMATE_GENERATION_CASE` |
| 4 | `bdef50f2-1705-44e6-aee2-41b765b329b3` | `LAB_0002` | `lab.flag` | Alkaline Phosphatase flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Alkaline Phosphatase (Present) | Yes (`118.0 U/L 44-147`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 5 | `4399e043-8fe8-41d4-aafc-8ffff432ab64` | `NARRATIVE_0001` | `med.frequency` | Insulin glargine frequency: at bedtime | `INSUFFICIENT_EVIDENCE` | `CONTRADICTS_CLAIM` | `DISCREPANCY` | Unobserved negative control sentinel string contains "not" | Insulin glargine (Present in sentinel) | No (Unobserved entity) | No (`at bedtime` absent) | `LEGITIMATE_GENERATION_CASE` |
| 6 | `3bd41618-e22f-4efd-9469-978562274510` | `LAB_0002` | `lab.flag` | Red Blood Cell Count flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Red Blood Cell Count (Present) | Yes (`5.53 10*6/uL 4.2-5.8`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 7 | `6ab9d27c-b9d7-4533-a33e-1326752d865e` | `LAB_0002` | `lab.flag` | Albumin flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Albumin (Present) | Yes (`4.1 g/dL 3.5-5.0`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 8 | `a56b1251-8afc-435d-b8d2-77f33c3e7a8d` | `NARRATIVE_0001` | `med.name` | Medication: Insulin glargine | `INSUFFICIENT_EVIDENCE` | `CONTRADICTS_CLAIM` | `DISCREPANCY` | Unobserved negative control sentinel string contains "not" | Insulin glargine (Present in sentinel) | No (Unobserved entity) | Yes (Mentioned in sentinel) | `LEGITIMATE_GENERATION_CASE` |
| 9 | `c05a1bd1-00d5-4b50-bf02-9fa90a3b60cd` | `LAB_0002` | `lab.flag` | Hematocrit flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Hematocrit (Present) | Yes (`45.3 % 36.0-48.0`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 10 | `20ade0c4-6f85-47c5-a92c-c40a62297d4c` | `LAB_0002` | `lab.flag` | Calcium flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Calcium (Present) | Yes (`9.8 mg/dL 8.5-10.2`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 11 | `349f2003-ebc2-4947-ab77-0a66c6094fd0` | `LAB_0002` | `lab.flag` | Platelet Count flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Platelet Count (Present) | Yes (`264.0 10*3/uL 150-450`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 12 | `de1e6d27-190e-4b33-83af-3bc8ecfa9c2c` | `NARRATIVE_0001` | `med.route` | Insulin glargine route: subcutaneously | `INSUFFICIENT_EVIDENCE` | `CONTRADICTS_CLAIM` | `DISCREPANCY` | Unobserved negative control sentinel string contains "not" | Insulin glargine (Present in sentinel) | No (Unobserved entity) | No (`subcutaneously` absent) | `LEGITIMATE_GENERATION_CASE` |
| 13 | `6cd8576b-166d-4175-a3d7-29812d417f7a` | `NARRATIVE_0001` | `med.dose` | Insulin glargine dose: 20 units | `INSUFFICIENT_EVIDENCE` | `CONTRADICTS_CLAIM` | `DISCREPANCY` | Unobserved negative control sentinel string contains "not" | Insulin glargine (Present in sentinel) | No (Unobserved entity) | No (`20 units` absent) | `LEGITIMATE_GENERATION_CASE` |
| 14 | `78080d05-dff2-4767-9bc2-589b78b6d52e` | `LAB_0002` | `lab.flag` | BUN flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | BUN (Present) | Yes (`16.0 mg/dL 7-20`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 15 | `909df456-950a-41dd-9078-a25768a892e9` | `NARRATIVE_0001` | `med.route` | Amoxicillin route: intravenously | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | True route is `orally`; Stage A rule engine lacked match | Amoxicillin (Present) | Yes (`orally`) | No (`intravenously` absent) | `LEGITIMATE_GENERATION_CASE` |
| 16 | `7ab9b621-9baf-44bd-81aa-e6dae37d7530` | `LAB_0002` | `lab.flag` | Creatinine flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Creatinine (Present) | Yes (`0.65 mg/dL 0.60-1.30`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 17 | `cb7a6178-469d-4760-9323-92ca8ea5b729` | `LAB_0002` | `lab.flag` | Chloride flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Chloride (Present) | Yes (`105.0 mmol/L 96-106`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 18 | `b9948b3d-6265-4cc7-9e21-ec545b7a8373` | `LAB_0002` | `lab.flag` | Aspartate Aminotransferase (AST) flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Aspartate Aminotransferase (AST) (Present) | Yes (`29.0 U/L 10-40`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 19 | `602b5a4d-1552-43ab-aa08-18b785b07e45` | `LAB_0002` | `lab.flag` | White Blood Cell Count flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | White Blood Cell Count (Present) | Yes (`10.1 10*3/uL 4.5-11.0`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 20 | `64535f55-1173-4d1f-b779-6542ef97c8d6` | `LAB_0002` | `lab.flag` | Sodium flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Sodium (Present) | Yes (`140.0 mmol/L 135-145`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 21 | `61af012f-a701-4186-90bb-410e44f3a5a0` | `NARRATIVE_0001` | `lab.value` | Troponin I: 0.02 ng/mL | `INSUFFICIENT_EVIDENCE` | `CONTRADICTS_CLAIM` | `DISCREPANCY` | Unobserved negative control sentinel string contains "not" | Troponin I (Present in sentinel) | No (Unobserved entity) | No (`0.02 ng/mL` absent) | `LEGITIMATE_GENERATION_CASE` |
| 22 | `fa64a8fe-641e-4572-b2d2-365d7842cd5b` | `LAB_0002` | `lab.flag` | Total Bilirubin flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Total Bilirubin (Present) | Yes (`0.1 mg/dL 0.1-1.2`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 23 | `8e727b6d-f737-4102-b932-17fba83f4d99` | `LAB_0002` | `lab.flag` | Alanine Aminotransferase (ALT) flag: HIGH | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Stage A cannot infer normal from empty flag column | Alanine Aminotransferase (ALT) (Present) | Yes (`31.0 U/L 7-56`) | No (`HIGH` absent) | `LEGITIMATE_GENERATION_CASE` |
| 24 | `95fc5802-f372-408f-8444-26a378889fd0` | `MED_0001` | `med.frequency` | Atorvastatin frequency: once daily | `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | True frequency is `every evening`; Stage A lacked frequency mapping | Atorvastatin (Present) | Yes (`every evening`) | No (`once daily` absent) | `LEGITIMATE_GENERATION_CASE` |

---

## C. Deep Dive into the 4 Conflict Categories

### Category 1: `lab.flag` Discrepancy Claims on Template C Normal Rows (16 examples)
- **Examples**: Indices 1, 2, 4, 6, 7, 9, 10, 11, 14, 16, 17, 18, 19, 20, 22, 23 (all on `LAB_0002`).
- **Clinical Context**: In Quest-style lab reports (Template C), the tabular header is `Analyte Result Units Reference Range Abnormal`. When an analyte is normal, the `Abnormal` column is left blank; when abnormal, it shows `H` or `L`. For example:
  ```text
  Analyte Result Units Reference Range Abnormal
  Glucose 105.0 mg/dL 70 - 99 H
  BUN 16.0 mg/dL 7 - 20
  Creatinine 0.65 mg/dL 0.60 - 1.30
  Potassium 4.2 mmol/L 3.5 - 5.1
  ```
- **The Claim**: An external entity asserts `Potassium flag: HIGH`.
- **The Ground Truth**: Clinically and factually, the ground truth is **`DISCREPANCY`**. The claim asserts the potassium was flagged high, when in reality it was normal and not flagged.
- **Why Stage A Produced a Conflict**: Stage A's deterministic rule engine looks for tokens like `high`, `low`, `normal`, `abnormal` in the evidence text. When it inspects `Potassium 4.2 mmol/L 3.5 - 5.1`, it finds no flag token whatsoever. It therefore returns `INSUFFICIENT_EVIDENCE (reason: no_flag_found_in_evidence)`.
- **Why the Auditor Flagged It**: The benchmark auditor checks `if gt == "DISCREPANCY" and sem_cat != CONTRADICTS_CLAIM: is_conflict = True`. Since Stage A returned `INSUFFICIENT_EVIDENCE`, the auditor categorized it as `TRUNCATED_OR_INCOMPLETE_EVIDENCE`, creating a conflict alert.
- **Adjudication**: **`LEGITIMATE_GENERATION_CASE`**. The generated training example is 100% correct. This is the exact task M2's learned neural model (ModernBERT) is designed to solve: learning that the absence of an abnormal flag in a structured tabular column with an in-range numerical value implies normal status, thereby refuting the claim `HIGH`.

### Category 2: Narrative Negative Controls with Entity in Absence String (5 examples)
- **Examples**: Indices 5, 8, 12, 13, 21 (on `NARRATIVE_0001` for `Insulin glargine` or `Troponin I`).
- **Clinical Context**: Unobserved negative controls test whether the verification system correctly identifies claims about entities that never appear in the patient's record.
- **Evidence Window**:
  ```text
  Document-level absence: Target entity 'Insulin glargine' not observed in clinical narrative.
  ```
- **The Claim**: `Insulin glargine dose: 20 units` (or route, frequency, name).
- **The Ground Truth**: **`INSUFFICIENT_EVIDENCE`** (the document does not contain evidence about Insulin glargine).
- **Why Stage A Produced a Conflict**: Stage A contains a clinical negation detector (`_detect_negation`). When it scans the evidence window, it detects the negation trigger `not` and the entity `'Insulin glargine'`. It interprets this as an explicit negation of the entity ("patient did not receive Insulin glargine"), predicting `DISCREPANCY (negation_mismatch)`.
- **Why the Auditor Flagged It**: Because Stage A predicted `DISCREPANCY` while ground truth was `INSUFFICIENT_EVIDENCE`, the auditor flagged a conflict.
- **Adjudication**: **`LEGITIMATE_GENERATION_CASE`**. The generation logic is sound. Both `INSUFFICIENT_EVIDENCE` and `DISCREPANCY` reject the claim as ungrounded/untrue. (Note: Clean medication generation already uses unquoted sentinel strings which eliminate this noise).

### Category 3: Semantic Mismatch in Route & Frequency Discrepancies (2 examples)
- **Examples**:
  - Index 15: `Amoxicillin route: intravenously` (gt=`DISCREPANCY`).
    Evidence: `"The patient received Amoxicillin 250 mg orally every 8 hours for 7 days"`.
  - Index 24: `Atorvastatin frequency: once daily` (gt=`DISCREPANCY`).
    Evidence: `"Atorvastatin 10 mg tablet / Sig: Take 1 tablet PO every evening"`.
- **Clinical Context**: The claims state perturbed routes/frequencies (`intravenously` instead of `orally`; `once daily` instead of `every evening`).
- **The Ground Truth**: **`DISCREPANCY`**. The claim directly contradicts the prescription/narrative.
- **Why Stage A Produced a Conflict**: Stage A's rule engine has a restricted dictionary of route synonyms (`po`, `oral`, `iv`, `subcutaneous`) and frequency patterns. In Index 15, `intravenously` was not matched against `orally` by the deterministic parser. In Index 24, `once daily` was not recognized as conflicting with `every evening`. Stage A therefore returned `INSUFFICIENT_EVIDENCE`.
- **Adjudication**: **`LEGITIMATE_GENERATION_CASE`**. The generated claim and evidence pair is completely valid. It is an ideal training case for ModernBERT cross-attention to learn clinical discrepancy resolution.

### Category 4: Degraded OCR Value Mismatch Evaluated as Discrepancy (1 example)
- **Example**: Index 3 (`LAB_0001`, `Glucose: 86 mg/dL`, gt=`INSUFFICIENT_EVIDENCE`).
- **Evidence Text (Degraded OCR)**:
  ```text
  TestDescription Observed Value Status Reference Interval Units
  Glucose 89.0 NORMAL 70 - 99 mg/dL
  ```
- **Context**: In clean OCR, Glucose was `86.0`. Under degraded OCR simulation, `86.0` was perturbed/misrecognized as `89.0`.
- **The Generator's Ground Truth**: `INSUFFICIENT_EVIDENCE` (because `86.0` cannot be found in the degraded document text).
- **The Verifier's Evaluation**: Stage A parsed the number in the claim (`86.0`) and the number in the evidence (`89.0`), and returned `DISCREPANCY (claim_value: 86.0, evidence_numbers: [89.0])`.
- **Adjudication**: **`LEGITIMATE_GENERATION_CASE`**. Both labels represent valid rejections of the unsupported claim. There is no corrupt span, no header offset, and no wrong entity.

---

## D. Final Recommendation

### **`SAFE_TO_FULL_REGENERATE`**

**Justification**:
1. **Zero Generator Corruption**: All fixes from `M2_GENERATION_ROOT_CAUSE_ANALYSIS.md` are verified working.
   - Context window row-snapping cleanly preserved all 261 evidence spans without mid-word or mid-row bisections.
   - The corrupted `[0, 100]` fallback offsets were 100% eliminated (0 occurrences).
   - Character offsets match cleanly against the canonical OCR text caches.
2. **All 24 Conflicts are Semantically Valid**: Not a single one of the 24 conflicts represents data corruption, malformed JSON, misaligned character offsets, or invalid clinical statements. They are legitimate clinical training cases where deterministic Stage A heuristics have known limitations that Stage B (BioClinical-ModernBERT) is designed to resolve.

---

## E. Integrity Verification of Canonical Dataset

To ensure complete read-only adherence during this audit, cryptographic SHA256 checksums of the canonical files were calculated before and after analysis:

- `data/m2_verification/all_examples.jsonl`:
  `ce924fa3921eedf2482c29279579ed010036273e608c399f38ce291c8306a2a8` (**UNCHANGED**)
- `data/m2_verification/split_manifest.json`:
  `8a1ebd157188c4c4032dab297df4c53f9d2fc1dd58a52596a368cc7c676e4393` (**UNCHANGED**)
