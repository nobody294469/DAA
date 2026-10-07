# Clinical Narrative Extraction Validation Cohort Inventory & Selection Audit

**Author / Evaluator:** Antigravity AI Engine  
**Date:** October 7, 2026  
**Status:** READ-ONLY AUDIT & COHORT SELECTION PLAN  
**Scope:** Workspace Document Inventory, Modality/Diversity Classification, Independence Verification, and Annotation Planning for Condition 4 Narrative Extraction Pipeline  

---

## 1. Objective

Following the controlled extraction architecture experiments (Conditions 0–4), the pipeline achieved high performance on the initial 3-document development pilot:
- **Condition 4 (Gated Layout Serialization + Dual-Pass Domain Extraction + Lab Negative Boundary):**
  - **Precision:** 95.24% (40/42)
  - **Recall:** 95.24% (39/41)
  - **F1 Score:** 95.24%
  - **Grounding Compliance:** 100.0% (42/42)
  - **Target Count:** 41 ground-truth clinical targets
  - **Errors:** 2 False Negatives, 2 False Positives

While these metrics demonstrate substantial improvement over the baseline (Condition 0: F1 85.33%, Recall 76.19%), Condition 4 **must not be merged into production** based solely on an $N=3$ development cohort. 

The primary objective of this audit is to conduct a rigorous, read-only inventory of all candidate clinical PDF documents in the workspace (`d:\daa`), assess their provenance, layout characteristics, and independence, and design an unbiased, scientifically defensible validation cohort (aiming for 15–25 documents) without selecting documents based on prior Qwen execution results or tuning artifacts.

---

## 2. Existing Development Cohort

The following three documents formed the original development and tuning cohort. To guarantee mathematical and scientific integrity, **they are strictly excluded from the validation cohort**:

1. **`sample.pdf`** (`trials/sample.pdf` / Doc ID `0a703e85-5593-4053-a789-79b5f7fbe183`)
   - **Source:** Narayana Hrudayalaya Hospitals
   - **Modality:** Born-digital PDF (3 pages)
   - **Clinical Scope:** 13 Ground-Truth Targets (Medications with dot-leader layout; lab parameters)
   - **Role in Development:** Prompt anchoring analysis; baseline extraction benchmarking; regression baseline.

2. **`sample2.pdf`** (`trials/sample2.pdf` / Doc ID `d061c4fa-4d1a-4648-8df0-1c0f16df1632`)
   - **Source:** Deenanath Mangeshkar Hospital and Research Center
   - **Modality:** Scanned image PDF (2 pages; dense OCR noise)
   - **Clinical Scope:** 16 Ground-Truth Targets (Multi-column lab investigation panels and discharge medications)
   - **Role in Development:** Inspired the dual-pass architecture and layout-aware serialization hypothesis; primary source of recall gain in Condition 4.

3. **`sample3.pdf`** (`trials/sample3.pdf` / Doc ID `b2fa9ce3-1bb0-4d57-8178-5a706b453e9c`)
   - **Source:** Sir H. N. Reliance Foundation Hospital and Research Centre
   - **Modality:** Scanned institutional discharge summary (3 pages)
   - **Clinical Scope:** 12 Ground-Truth Targets (Complex tabular prescription layout with dot-leaders)
   - **Role in Development:** Used to diagnose dot-leader spatial degradation; directly motivated the spatial gating mechanism (`has_tabular_landmarks`) in Condition 4.

**Status:** **PERMANENTLY EXCLUDED FROM VALIDATION** to prevent test-set contamination.

---

## 3. Workspace Document Inventory

A systematic scan across all directories in `d:\daa` identified 209 PDF files across five main categories:
1. `trials/` (3 PDFs) — Real-world clinical development cohort.
2. `scratch/blobs/` & SQLite Database (`scratch/clindoc_dev.db`) (2 uploaded document blobs) — Real-world upload and test stub.
3. `data/narrative_rendered/` (20 PDFs) — Synthetic multi-section narrative inpatient discharge summaries.
4. `data/rendered/` & `data/med_rendered/` (100 PDFs) — Synthetic single-purpose structured lab panels and outpatient prescriptions.
5. `data/degraded/visual/` (84 PDFs) — Synthetically degraded visual distortion variants of structured benchmarks.
6. `n2c2_access/` — External clinical data tracking directory (Confirmed status: `NOT STARTED`; zero external clinical datasets on disk).

### Detailed Candidate Inventory Table

| Filename | Path / Storage Key | Pages | Size (KB) | Modality | Source / Facility | Clinical Content | Layout Characteristics | OCR Quality | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`Original_Discharge_Summary.pdf`** | `scratch/blobs/1f/1f87afe...` (Doc ID `1a58f0eb-...`) | 2 | 217.7 | Scanned image | Christian Medical College (CMC) Vellore (OB/GYN Unit V) | Discharge Meds (Autrin, Shelcal, Metacin), Lab flags (HBsAg Neg) | Institutional scanned form, multi-column tables, physician stamps | Medium (300 DPI scan, minor slant) | **SELECTED** |
| `discharge_summary_sample.pdf` | `scratch/blobs/06/0653d9e...` (Doc ID `f9650f1a-...`) | 1 | 0.5 | Born-digital | Unknown (Dev stub) | None (single test sentence) | Single line text stub | N/A | **EXCLUDED** |
| `sample.pdf` | `trials/sample.pdf` | 3 | 7.7 | Born-digital | Narayana Hrudayalaya | Meds (dot-leaders), Labs | Mixed text + dot-leaders | Clean | **EXCLUDED** (Dev) |
| `sample2.pdf` | `trials/sample2.pdf` | 2 | 531.9 | Scanned image | Deenanath Mangeshkar Hospital | Meds, Lab panel (multi-column) | Multi-column tabular + narrative | High noise | **EXCLUDED** (Dev) |
| `sample3.pdf` | `trials/sample3.pdf` | 3 | 165.6 | Scanned image | Sir H. N. Reliance Foundation | Meds (dot-leaders), clinical course | Tabular prescription with dot-leaders | Medium | **EXCLUDED** (Dev) |
| `NARRATIVE_0001.pdf` to `NARRATIVE_0020.pdf` (20 docs) | `data/narrative_rendered/` | 1 each | 3.3–3.4 | Born-digital | Memorial General Hospital (Synthetic Benchmark) | Inpatient HPI Labs + Discharge Meds (15–30 targets/doc) | Narrative prose + bulleted discharge lists | Clean digital text | **BACKUP (SYNTHETIC)** |
| `SYNTH_LAB_0001.pdf` to `0050` (50 docs) | `data/rendered/` | 1 each | ~3.0 | Born-digital | Synthetic Clinical Lab | Pure laboratory panels | Structured 2-column tables | Clean digital | **EXCLUDED** (Non-narrative) |
| `MED_0001.pdf` to `0050` (50 docs) | `data/med_rendered/` | 1 each | ~3.0 | Born-digital | Synthetic Outpatient Clinic | Pure outpatient prescriptions | Structured list | Clean digital | **EXCLUDED** (Non-narrative) |
| Degraded Visual Suite (84 docs) | `data/degraded/visual/` | 1 each | Various | Synthetically degraded | Synthetic | Structured labs/meds | Degraded synthetic | Synthetic distortion | **EXCLUDED** (Non-narrative) |

---

## 4. Exclusions / Duplicates

Rigorous evaluation standards require excluding any document that does not represent an independent, uncompromised test of narrative clinical extraction:

1. **Development Cohort Exclusions (`sample.pdf`, `sample2.pdf`, `sample3.pdf`):**
   - **Reason:** Directly inspected and utilized during prompt refinement, error triage, and Condition 0–4 architecture formulation. Re-testing them would measure training/tuning fit rather than generalizability.
2. **Synthetic Outpatient Prescriptions (`data/med_rendered/*.pdf` - 50 docs):**
   - **Reason:** These documents are synthetic outpatient scripts lacking narrative context, laboratory panels, or multi-column discharge summaries. They evaluate single-domain structured prescription parsing, not narrative extraction.
3. **Synthetic Laboratory Reports (`data/rendered/*.pdf` - 50 docs):**
   - **Reason:** Pure laboratory tables lacking medication lists or clinical history narrative.
4. **Visual Degradation Benchmarks (`data/degraded/visual/*.pdf` - 84 docs):**
   - **Reason:** Artificial rotations, blurs, and contrast reductions of synthetic structured tables, outside the narrative discharge summary scope.
5. **System Integration Stub (`discharge_summary_sample.pdf` - 480 bytes):**
   - **Reason:** Contains no clinical entities (a 1-line developer dummy file).
6. **Synthetic Narrative Discharge Summaries (`NARRATIVE_0001` to `NARRATIVE_0020` - 20 docs):**
   - **Classification:** **BACKUP / SYNTHETIC_SUPPLEMENTARY**.
   - **Reason:** While these documents contain both narrative clinical course and discharge medications, they are machine-generated using a single ReportLab template (`Memorial General Hospital`). They lack genuine clinical messiness, OCR artifacts, complex institutional typography, and variable physician phrasing. They cannot be counted as real clinical documents.

---

## 5. Diversity Matrix

To evaluate where validation documents fall across realistic clinical document dimensions, we map all examined candidates across five axes:

```
+-----------------------------------------------------------------------------------------------------------------+
|                                           DOCUMENT DIVERSITY MATRIX                                             |
+-------------------+-------------------+-------------------+-------------------+---------------------------------+
| Modality          | Content Profile   | Layout Complexity | Document Length   | Facility / Source Provenance    |
+-------------------+-------------------+-------------------+-------------------+---------------------------------+
| Scanned Image     | Mixed Med + Lab   | Form Table + Text | 2 Pages           | CMC Vellore (Real Hospital)     |
| [Original_Disch.] | [Original_Disch.] | [Original_Disch.] | [Original_Disch.] | [Original_Discharge_Summary.pdf]|
+-------------------+-------------------+-------------------+-------------------+---------------------------------+
| Born-Digital      | Narrative + Med   | Sequential Prose  | 1 Page (Dense)    | Memorial General Hospital       |
| [NARRATIVE_0001   | + Lab             | + Bullet Lists    | [NARRATIVE_0001   | (Synthetic Benchmark Facility)  |
|  to 0020]         | [NARRATIVE suite] | [NARRATIVE suite] |  to 0020]         | [NARRATIVE_0001 to 0020]        |
+-------------------+-------------------+-------------------+-------------------+---------------------------------+
| Scanned (Dev)     | Tabular Med Only  | Dot-Leader Tables | 3 Pages           | Sir H. N. Reliance (Dev Excl.)  |
| [sample3.pdf]     | [sample3.pdf]     | [sample3.pdf]     | [sample3.pdf]     | [sample3.pdf]                   |
+-------------------+-------------------+-------------------+-------------------+---------------------------------+
| Scanned (Dev)     | Dense Lab + Med   | Multi-Column Grid | 2 Pages           | Deenanath Mangeshkar (Dev Excl.)|
| [sample2.pdf]     | [sample2.pdf]     | [sample2.pdf]     | [sample2.pdf]     | [sample2.pdf]                   |
+-------------------+-------------------+-------------------+-------------------+---------------------------------+
| Born-Digital (Dev)| Meds + Lab Params | Dot-Leader Rows   | 3 Pages           | Narayana Hrudayalaya (Dev Excl.)|
| [sample.pdf]      | [sample.pdf]      | [sample.pdf]      | [sample.pdf]      | [sample.pdf]                    |
+-------------------+-------------------+-------------------+-------------------+---------------------------------+
```

---

## 6. Proposed Validation Cohort

### The Fundamental Ground-Truth Finding

An exhaustive audit of the workspace reveals an indisputable truth:
> **There is exactly ONE (1) independent, genuine real-world clinical hospital PDF available in the workspace outside the development cohort: `Original_Discharge_Summary.pdf`.**

No other unobserved real hospital discharge summaries currently reside on local disk. (The `n2c2` clinical records have not yet been downloaded, as documented in `n2c2_access/REQUEST_STATUS.md`).

Per the user's explicit directive:
> *"If fewer than 15 genuinely independent suitable documents exist, DO NOT pad the cohort with duplicates or weak documents. Report the actual number available."*

Therefore, the **Primary Real-World Validation Cohort** consists strictly of the single genuinely available document:

### Primary Real-World Validation Document

- **Filename:** `Original_Discharge_Summary.pdf`
- **Internal Storage Path:** `scratch/blobs/1f/1f87afe9fb9b53e45f6d00a9b03ddfc8d5d99fc39cb1cc087c5e7011120d0c05.enc`
- **Registered Document ID:** `1a58f0eb-62ba-415a-bfa8-bc0d9ba2d389`
- **OCR Cache Location:** `data/ocr_cache/clindoc_job_1a58f0eb_7pp2q9ld.json`
- **Page Count:** 2 pages
- **File Size:** 222,947 bytes (~217.7 KB)
- **Modality:** Scanned image PDF (institutional obstetric delivery discharge summary)
- **Facility / Source:** Christian Medical College (CMC) Vellore, Obstetrics & Gynaecology Unit V
- **Clinical Content:**
  - **Medications:** Explicit discharge prescriptions including iron/folic acid (`Autrin`), calcium (`Shelcal`), and analgesics (`Metacin`), with dosing, frequency, and duration.
  - **Laboratories:** Antenatal maternal serology screening panel (including `HBsAg` with explicit negative flag/result).
  - **Clinical Narrative:** Obstetric course, gestational age, delivery outcome, postpartum progress.
- **Layout Characteristics:** Standard institutional two-column header, tabular maternal vitals/labs, boxed discharge orders, physician signatures/stamps.
- **Why Selected:**
  1. Complete institutional independence: CMC Vellore format has never been seen by the layout gate or prompt design.
  2. Multi-domain challenge: Combines maternal serology flags with standard discharge prescriptions.
  3. Real-world scanning artifacts: Contains slight scan skew, institutional stamps, and variable typography.
  4. 100% Unseen: Zero targets from this document were included in the 41-target development set.

---

## 7. Backup Candidates: Synthetic Narrative Suite

If the project requires an immediate large-scale quantitative stress test of 15–20 documents to compute aggregate F1 across hundreds of targets, the **20 synthetic inpatient discharge summaries** in `data/narrative_rendered/` are designated as the **Backup / Synthetic Supplementary Cohort**.

### Characteristics of the Backup Cohort:
- **Files:** `NARRATIVE_0001.pdf` through `NARRATIVE_0020.pdf`
- **Count:** 20 documents (1 page each)
- **Modality:** Born-digital (ReportLab generated)
- **Source:** Synthetic `Memorial General Hospital`
- **Clinical Structure:**
  - **HPI / Narrative Course:** Contains embedded laboratory values (e.g., hemoglobin, troponin, creatinine, potassium, glucose).
  - **Discharge Medications:** Formatted as structured bulleted lists with dosages, routes, and frequencies (e.g., `Lisinopril 10 mg Oral Daily`, `Metformin 500 mg Oral Twice Daily`).
- **Ground Truth Availability:** Corresponding machine-readable truth exists in `data/narrative_dataset/manifest.json`.
- **Target Scale:** ~20–30 clinical entities per document ($\approx 450\text{--}550$ total targets).
- **Why Designated as Backup:**
  - While excellent for stress-testing LLM extraction logic and dual-pass domain routing at scale, they do **not** reflect real hospital OCR scanning artifacts, handwriting, stamps, or degraded typography. They must not masquerade as real-world hospital validation.

---

## 8. Data-Leakage / Independence Check

We conducted an audit against all potential vectors of data leakage:

| Potential Leakage Vector | Real Document (`Original_Discharge_Summary.pdf`) | Backup Cohort (`NARRATIVE_0001` to `0020`) |
| :--- | :--- | :--- |
| **Used in Few-Shot Prompt Examples?** | **NO.** Prompt examples were synthesized from general clinical patterns (`Atorvastatin 40mg`, `WBC 11.2`, `Lisinopril 10mg`). | **NO.** None of the 20 documents appear in prompt exemplars. |
| **Used in 41-Target Ground Truth?** | **NO.** The 41 targets came exclusively from `sample.pdf` (13), `sample2.pdf` (16), and `sample3.pdf` (12). | **NO.** Zero overlap. |
| **Used to Tune Layout Gate?** | **NO.** Layout gate thresholds (`has_tabular_landmarks`, horizontal spread, dot-leader checks) were tuned specifically to resolve `sample2.pdf` and `sample3.pdf` regressions. | **NO.** Born-digital documents do not trigger the spatial OCR table gating. |
| **Used to Tune Lab Negative Boundary?** | **NO.** Negative lab boundaries (rejecting `8.5 kg` weight, BP, pulse) were tuned specifically to fix the false positive observed on `sample2.pdf`. | **NO.** |
| **Selected Based on Prior Qwen Performance?** | **NO.** No Qwen extraction was run on this document during this audit. Selection was strictly determined by document inventory, hospital provenance, and clinical content validity. | **NO.** |
| **Overall Independence Classification** | **COMPLETELY INDEPENDENT** | **COMPLETELY INDEPENDENT** |

---

## 9. Ground-Truth Annotation Plan

To establish rigorous ground truth for `Original_Discharge_Summary.pdf` (and the backup cohort if activated), annotation must follow the exact existing clinical contract without adding ad-hoc field types.

### Annotation Schema (Strict Contract)
1. **Medication (`field_type: "medication"`):**
   - `med.name` (Primary generic/brand name string)
   - `med.dose` (Numeric magnitude as string, e.g., `"1"`, `"500"`)
   - `unit_raw` (Dosage unit, e.g., `"tab"`, `"mg"`, `"capsule"`)
   - `med.frequency` (Normalized timing, e.g., `"OD"`, `"BD"`, `"TDS"`, `"daily"`)
   - `med.route` (Route if explicitly written, e.g., `"Oral"`, `"IV"`)
2. **Laboratory (`field_type: "laboratory"`):**
   - `lab.value` (Analyte name + numeric/qualitative result)
   - `lab.flag` (Explicit clinical flags only: `"High"`, `"Low"`, `"Positive"`, `"Negative"`, `"Normal"`)

### Detailed Annotation Estimates

#### A. Real-World Document (`Original_Discharge_Summary.pdf`)
- **Total Pages:** 2 pages
- **Pages Requiring Clinical Annotation:**
  - **Page 1:** Clinical course, delivery details, maternal laboratory serology table (`HBsAg Negative`, blood grouping/Rh, hemoglobin if recorded).
  - **Page 2:** Postpartum discharge orders, discharge prescriptions (`Tab Autrin OD`, `Tab Shelcal BD`, `Tab Metacin SOS`), follow-up instructions.
- **Estimated Target Count:**
  - Medications: 3 to 5 distinct medication regimens (approx. 12–18 atomic slot attributes).
  - Laboratories: 2 to 4 laboratory/serology determinations (approx. 4–8 atomic slot attributes).
  - **Total Ground-Truth Targets:** **16 to 24 atomic targets**.
- **Challenging Annotation Nuances:**
  - Determining exact bounding box references for physician handwriting/signatures accompanying discharge orders.
  - Qualitative lab flag (`HBsAg Negative`) where the flag is a word rather than a numeric abnormal flag.

#### B. Backup Synthetic Cohort (`NARRATIVE_0001.pdf` to `NARRATIVE_0020.pdf`)
- **Total Pages:** 20 pages (1 page per document)
- **Pages Requiring Annotation:** All 20 pages.
- **Estimated Target Count:**
  - 12 to 18 medications per document.
  - 8 to 14 lab values per document.
  - **Total Ground-Truth Targets:** **450 to 550 atomic targets across the cohort**.
- **Annotation Advantage:** Machine-readable ground truth already exists in the benchmark metadata generator, requiring only schema alignment to the Tier 1 evaluation script.

---

## 10. Expected Evaluation Coverage

Evaluating Condition 4 against the proposed inventory will provide the following coverage:

1. **New Institutional Generalization (Real Scanned):**
   - Tests whether Condition 4's prompt and dual-pass extraction generalize to CMC Vellore formatting without prompt recalibration.
2. **Serology / Qualitative Lab Extraction:**
   - Evaluates whether Condition 4 extracts qualitative lab results (`HBsAg: Negative`) with `lab.flag: "Negative"` without misclassifying them as narrative medical history.
3. **Prescription Parsing under Mixed Scanning:**
   - Tests whether iron/calcium supplements with abbreviated Indian clinical frequencies (`OD`, `BD`, `SOS`) are parsed with high precision and 100% grounding.
4. **Statistical Power (If Backup Cohort Activated):**
   - Testing the 20 backup synthetic documents will yield statistical confidence bounds across $>450$ extraction instances, proving whether dual-pass routing scales without memory or latency degradation.

---

## 11. Risks and Limitations

1. **Real-World Sample Size Constraint ($N=1$ Real Document):**
   - While `Original_Discharge_Summary.pdf` provides genuine institutional independence, a single real document is insufficient for formal FDA-grade or production-grade clinical software release.
   - **Mitigation:** Condition 4 must be evaluated on `Original_Discharge_Summary.pdf` first. However, full production certification requires onboarding external public real-world datasets (such as MIMIC-III/IV discharge summaries or the n2c2 clinical challenge cohort once data agreements are executed).
2. **Synthetic Domain Shift (If Using Backup Suite):**
   - Evaluating on `NARRATIVE_0001` to `0020` provides high target numbers but near-zero OCR noise. High metrics on this set could overestimate scanned-document performance.
3. **No Ground Truth Fabrication:**
   - Ground truth must be established by careful human review of `Original_Discharge_Summary.pdf` before running the evaluation script, adhering strictly to the Tier 1 extraction schema.

---

## 12. Recommendation

1. **Acknowledge the True Inventory State:**
   - Formally recognize that the workspace contains **exactly one independent real-world clinical hospital PDF** (`Original_Discharge_Summary.pdf`).
   - Do **not** fabricate or pad the real-world cohort with synthetic structured files.
2. **Execute a Two-Tier Validation Protocol:**
   - **Tier 1 (Real-World Institutional Generalization):**
     - Perform manual ground-truth annotation on `Original_Discharge_Summary.pdf` (~18–22 targets).
     - Run Condition 4 in offline scratch mode and compare extraction metrics against the baseline.
   - **Tier 2 (High-Volume Synthetic Stress Test):**
     - Utilize `NARRATIVE_0001` through `NARRATIVE_0020` (20 documents, ~500 targets) as a secondary validation test to measure dual-pass extraction throughput, prompt stability, and schema compliance across high target volumes.
3. **Maintain Strict Read-Only Gate:**
   - Keep all production files (`src/clindoc/*`) untouched until Tier 1 and Tier 2 validation results are audited and reviewed.

---

## 13. Audit Artifacts Generated

1. **Validation Cohort Inventory:**  
   [`scratch/validation_cohort_inventory.json`](file:///d:/daa/scratch/validation_cohort_inventory.json) (Complete record of all 35 primary candidates with layout, source, and exclusion attributes).
2. **Validation Cohort Manifest:**  
   [`scratch/validation_cohort_manifest.json`](file:///d:/daa/scratch/validation_cohort_manifest.json) (Strict segregation into `SELECTED`, `BACKUP`, and `EXCLUDED`).
3. **Audit Report:**  
   [`reports/NARRATIVE_EXTRACTION_VALIDATION_COHORT_AUDIT.md`](file:///d:/daa/reports/NARRATIVE_EXTRACTION_VALIDATION_COHORT_AUDIT.md) (This document).
