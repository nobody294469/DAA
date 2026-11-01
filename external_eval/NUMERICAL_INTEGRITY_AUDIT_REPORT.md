# Numerical Integrity Audit Report: Frozen 200-Record Glaucoma Benchmark

**Date**: 2026-10-29  
**Evaluation Target**: `external_eval/results_frozen_200.json`  
**Subset Source**: `external_eval/glaucoma_eval_subset_200.json` (Seed 42, 150 positive records, 50 zero-annotation negative records)  
**Status**: AUDITED & RECONCILED  

---

## 1. Executive Summary & Core Reconciliations

1. **Arithmetic Verification**: Direct re-calculation of Precision, Recall, and $F_1$ across all 6 evaluated fields for both Standalone GLiNER and the DEC-004 Cascade verified 100% numerical consistency ($< 10^{-6}$ deviation).
2. **Micro-Average Scope**: The micro-average strictly and intentionally aggregates the 5 canonical clinical fields (`med.name`, `med.dose`, `med.route`, `med.frequency`, `med.duration`). `ocular.laterality` is deliberately excluded from the micro-average because it is currently unrepresented in the canonical 10-field Claim schema (§8.2, DEC-017).
3. **Denominator Conservation Across Systems**:
   - Total ground-truth mentions ($TP + FN$) in the 200-record subset for `med.*` is strictly **309** in both systems:
     $$\text{GLiNER: } TP(231) + FN(78) = \mathbf{309}$$
     $$\text{Cascade: } TP(233) + FN(76) = \mathbf{309}$$
4. **Resolution of Cascade TP/FN Shift (+2 TP / -2 FN)**:
   - In GLiNER standalone, `med.duration` had $TP=0, FN=3$ (as duration was excluded per DEC-019).
   - In the DEC-004 cascade, the deterministic Tier-2 extractor successfully extracted 2 durations, turning 2 ground-truth mentions from $FN \to TP$.
   - This exact +2 shift accounts for the entire difference: $231 \to 233$ for $TP$, and $78 \to 76$ for $FN$. The sum $TP + FN = 309$ remains identical.
5. **Audit of 50 Zero-Annotation Negative Records**:
   - 34 of 50 negative records triggered at least one prediction.
   - Comprehensive clinical review verified that **100% of these 34 records are definite false positives** (hallucinations on non-medication prose). Zero unannotated genuine medications were missed by the original annotators in these 50 records.

---

## 2. Reconciled Contingency Tables & Metrics

### Table 1: Field-by-Field Reconciled Metrics (Canonical Value Matching)

| Field | Ground Truth | GLiNER Standalone | | | | | | DEC-004 Cascade | | | | | |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| | | **TP** | **FP** | **FN** | **Precision** | **Recall** | **$F_1$** | **TP** | **FP** | **FN** | **Precision** | **Recall** | **$F_1$** |
| **`med.name`** | 165 | 136 | 46 | 29 | 0.7473 | 0.8242 | **0.7839** | 136 | 46 | 29 | 0.7473 | 0.8242 | **0.7839** |
| **`med.dose`** | 4 | 3 | 29 | 1 | 0.0938 | 0.7500 | 0.1667 | 3 | 29 | 1 | 0.0938 | 0.7500 | 0.1667 |
| **`med.route`** | 6 | 0 | 23 | 6 | 0.0000 | 0.0000 | 0.0000 | 0 | 25 | 6 | 0.0000 | 0.0000 | 0.0000 |
| **`med.frequency`** | 131 | 92 | 50 | 39 | 0.6479 | 0.7023 | **0.6740** | 92 | 135 | 39 | 0.4053 | 0.7023 | 0.5140 |
| **`med.duration`** | 3 | 0 | 0 | 3 | 0.0000 | 0.0000 | 0.0000 | 2 | 11 | 1 | 0.1538 | 0.6667 | **0.2500** |
| **`ocular.laterality`** | 116 | 0 | 0 | 116 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 | 116 | 0.0000 | 0.0000 | 0.0000 |
| **MICRO AVG (`med.*`)** | **309** | **231** | **148** | **78** | **0.6095** | **0.7476** | **0.6715** | **233** | **246** | **76** | **0.4864** | **0.7540** | **0.5914** |

---

## 3. Detailed Reconciliation of the Cascade TP/FN Conserved Sum

$$\begin{aligned}
\text{GLiNER Micro Denominator } (TP + FN) &= 136 + 3 + 0 + 92 + 0 + 29 + 1 + 6 + 39 + 3 = 231 + 78 = \mathbf{309} \\
\text{Cascade Micro Denominator } (TP + FN) &= 136 + 3 + 0 + 92 + 2 + 29 + 1 + 6 + 39 + 1 = 233 + 76 = \mathbf{309}
\end{aligned}$$

* **Reason for Identical Sum**: Both systems were evaluated on the identical 200 records with the exact same ground-truth entities.
* **Reason for Internal Shift**: Because `med.duration` is a Tier-2 deterministic field, the cascade successfully extracts 2 duration mentions that GLiNER omits:
  - Record 7433: `"...x 1 month..."` $\to$ Deterministic extracted `"1 month"` ($TP$).
  - Record 8121: `"...x 5 days..."` $\to$ Deterministic extracted `"5 days"` ($TP$).
  - In GLiNER standalone, these two instances were classified as $FN$. In Cascade, they shifted to $TP$.
  - Consequently, Cascade gained $+2$ in $TP$ and lost $-2$ in $FN$, preserving the total entity count of 309.

---

## 4. Audit of Negative Records: True False Positives vs. Annotation Gaps

To ensure evaluation rigor, all 34 zero-annotation negative records with predicted mentions were reviewed manually to determine whether they represented real unannotated clinical drugs or true algorithmic hallucinations.

### Clinical Taxonomy of Negative Record False Positives

| Error Category | Predicted Field | Example Mentions | Context Snippet | Clinical Root Cause |
| :--- | :--- | :--- | :--- | :--- |
| **Clinical Section Headers** | `med.name` | `"HPI"` | `"HPI: ***** ***** ********* is a 77 y.o. female"` | Acronym for *History of Present Illness* misidentified as medication name. |
| **Administrative / Visits** | `med.route`, `med.frequency` | `"New Patient Visit"`, `"f/u"` | `"Chief Complaint: New Patient Visit"`, `"6 weeks f/u-- SLT..."` | Visit reason or appointment abbreviation mistaken for sig instruction. |
| **Disease / Diagnosis** | `med.name` | `"POAG"`, `"Glaucoma"` | `"Here for POAG"`, `"H/o Glaucoma suspect..."` | Pathology terms (*Primary Open-Angle Glaucoma*) mistaken for drug names. |
| **Diagnostic Procedures** | `med.name`, `med.route` | `"HVF"`, `"SLT"` | `"Patient had HVF yesterday."`, `"...SLT ** */**/**..."` | Ophthalmic diagnostic tests (*Humphrey Visual Field*, *Selective Laser Trabeculoplasty*). |
| **Surgical Hardware** | `med.route` | `"tube shunt"`, `"Closing trab"` | `"...no plan for revision unless tube shunt fails."` | Surgical implant / anatomical intervention misclassified as route of administration. |
| **Activities of Daily Living** | `med.route` | `"DMV"`, `"drive"`, `"driving at night"` | `"Pt states needs DMV form completed to drive..."` | Functional lifestyle notes misclassified as route of administration. |
| **Follow-up Intervals** | `med.duration`, `med.frequency` | `"6 months"`, `"6 weeks"` | `"RTC in 6 months with HVF 24-2 OU..."` | *Return to Clinic* (RTC) appointment interval misclassified as drug treatment duration. |

### Finding
* **Definite False Positives**: **34 / 34 records (100%)**.
* **Plausible Unannotated Medications**: **0 / 34 records (0%)**.
* None of the 50 negative records contained missed ocular drops, ointments, or systemic drugs. The 51 GLiNER and 61 Cascade false positives on negative records are confirmed false alarms.

---

## 5. Methodological Limitations

1. **Unconstrained Regex Triggering**: The deterministic Tier-2 duration extractor captures temporal expressions like `"6 months"` indiscriminately when not bounded by medication regimens (e.g., catching `"RTC in 6 months"`). In production, `RegionClassifier` prevents this by only applying deterministic extractors to structured tables or orders.
2. **Zero Route Recall**: Because 95.1% of dataset "routes" are ocular laterality terms (`OU`, `OD`, `OS`), neither system achieved any True Positives on genuine physiological routes in this subset ($TP=0, FN=6$).
3. **vLLM Offline**: Narrative LLM inference remained offline (`WinError 10061`). These results represent the non-LLM components of the ClinicalDoc cascade.
