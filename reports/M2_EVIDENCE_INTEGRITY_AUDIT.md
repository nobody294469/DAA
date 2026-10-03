# M2 Benchmark Evidence and Provenance Integrity Audit Report

**Authority**: `PROJECT_SPEC.md` (§7.1, §7.8, §8, §9, §10, §20.1)  
**Dataset**: `data/m2_verification/all_examples.jsonl`  
**Manifest**: `data/m2_verification/split_manifest.json`  
**Audit Date**: October 2, 2026  
**Auditor Mode**: Read-Only / Non-Destructive Inspection  

---

## Executive Summary

A comprehensive, non-destructive evidence and provenance integrity audit was conducted across all **9,168 examples** in the canonical M2 verification benchmark (`data/m2_verification/all_examples.jsonl`). 

The audit revealed that **only 44.57% (4,086 examples)** of the benchmark meet rigorous clean provenance and semantic grounding standards. **25.04% (2,296 examples)** are **DEFINITELY INVALID**, and **30.39% (2,786 examples)** are **SUSPICIOUS** (with 1,500 requiring manual adjudication). 

Critically, **29.94% (2,745 examples)** suffer from ground-truth/evidence conflicts, including **782 examples** labeled `SUPPORTED` where the target entity is completely absent from the evidence snippet, and **640 examples** where character offsets point to clinic letterhead metadata (`[0, 100]`) rather than clinical observations.

---

## A. Dataset Summary

| Dimension | Count | Percentage |
| :--- | :--- | :--- |
| **Total Examples** | 9,168 | 100.0% |
| **Total Source Documents** | 120 | 100.0% |
| **Total Entity Groups** | 3,473 | — |
| **Cross-Validation (CV) Split** | 6,126 | 66.82% |
| **Out-of-Distribution (OOD) Split** | 3,042 | 33.18% |

### Counts by Ground-Truth Label
| Ground-Truth Label | Count | Percentage |
| :--- | :--- | :--- |
| `DISCREPANCY` | 4,294 | 46.84% |
| `SUPPORTED` | 4,294 | 46.84% |
| `INSUFFICIENT_EVIDENCE` | 580 | 6.33% |

### Counts by Canonical Field Type
| Field Type | Claim Domain | Tier | Count | Percentage |
| :--- | :--- | :--- | :--- | :--- |
| `lab.value` | Laboratory | Tier-1 | 5,728 | 62.48% |
| `lab.flag` | Laboratory | Tier-1 | 1,530 | 16.69% |
| `med.dose` | Medication | Tier-1 | 848 | 9.25% |
| `med.name` | Medication | Tier-1 | 354 | 3.86% |
| `med.frequency` | Medication | Tier-1 | 354 | 3.86% |
| `med.route` | Medication | Tier-1 | 354 | 3.86% |

### Counts by Split Partition
| Partition | Documents | Examples | Purpose |
| :--- | :--- | :--- | :--- |
| `train` | 52 | 3,576 | CV Folds 0–4 Training Pool |
| `calibration` | 17 | 1,117 | Temperature Scaling / Conformal Calibration |
| `validation` | 17 | 1,433 | Cross-Validation Holdout Evaluation |
| `ood` | 34 | 3,042 | Unseen Template Generalization Stress Test |
| **Total** | **120** | **9,168** | — |

---

## B. Provenance Validation

The provenance audit validated character offsets (`char_start`, `char_end`) against authoritative canonical document texts reconstructed from OCR cache lines (`data/ocr_cache/`, `data/med_ocr_cache/`, `data/narrative_ocr_cache/`).

| Provenance Status | Count | Percentage | Description |
| :--- | :--- | :--- | :--- |
| `VALID_ATOMIC` | 5,837 | 63.67% | Offsets correctly isolate the target atomic token inside `evidence` |
| `DEGRADED_OFFSET_MISMATCH` | 2,687 | 29.31% | Offsets valid only against corrupt OCR lines; mismatch canonical cache |
| `CORRUPTED_DEFAULT` | 640 | 6.98% | Offsets point to hardcoded `[0, 100]` fallback (clinic header/letterhead) |
| `OUT_OF_BOUNDS` | 4 | 0.04% | Offsets exceed total character length of the source document |
| `MISSING_DOC` | 0 | 0.00% | All 120 referenced documents were successfully resolved in cache |
| `MISSING_PAGE` | 0 | 0.00% | All referenced page numbers exist |

### Structural Failures Observed
1. **Out-of-Bounds Offsets (4 examples)**:
   - `LAB_0028` (clean length 598 chars): Examples `66f8b880-14e7-4bdc-bd96-bd5ae6fa8b35` and `3831d8a4-24d9-4c13-ab82-8184c605c3d8` have `char_start=598, char_end=603`.
   - `LAB_0020` (clean length 520 chars): Examples `38f93953-f159-4eb3-bf90-1fac50b8d923` and `12e612de-3e4a-48c7-ba20-1295303d18ea` have `char_start=518, char_end=522`.
2. **Corrupted Default Fallback `[0, 100]` (640 examples)**:
   - When OCR perturbation prevented substring search during synthetic example generation, `generation.py` defaulted to `char_start = 0, char_end = 100`.
   - This bound clinic letterheads (e.g. `"Metro Health Diagnostic Center..."`) as clinical evidence for laboratory analytes.

---

## C. Semantic Evidence Audit

Each example's evidence text was audited against the claim value, target analyte/medication entity, and numeric values using deterministic verification contracts.

| Semantic Category | Count | Percentage | Description |
| :--- | :--- | :--- | :--- |
| `CONTRADICTS_CLAIM` | 3,131 | 34.15% | Evidence mentions the entity with a conflicting value, flag, dose, or route |
| `SUPPORTS_CLAIM` | 2,520 | 27.49% | Evidence contains the target entity and unambiguously verifies the claim |
| `WRONG_ENTITY` | 1,706 | 18.61% | Evidence discusses an unrelated test, analyte, or medication |
| `TRUNCATED_OR_INCOMPLETE_EVIDENCE` | 772 | 8.42% | Evidence context window clipped the analyte name or numeric value |
| `INVALID_PROVENANCE` | 639 | 6.97% | Evidence consists of clinic headers, out-of-bounds text, or corrupt tokens |
| `INSUFFICIENT_EVIDENCE` | 400 | 4.36% | Genuine absence of evidence for negative controls |
| `UNRELATED_EVIDENCE` | 0 | 0.00% | Grouped under `WRONG_ENTITY` and `INVALID_PROVENANCE` |
| `OTHER` | 0 | 0.00% | No uncategorized exceptions |

---

## D. Ground-Truth / Evidence Conflicts

A major finding of this audit is severe ground-truth label pollution. **2,745 examples (29.94%)** have stored ground-truth labels that conflict with what the evidence span actually proves:

| Ground-Truth Label | Semantic Reality | Conflict Count | Root Cause |
| :--- | :--- | :--- | :--- |
| `SUPPORTED` | `WRONG_ENTITY` | **782** | Evidence window centered on wrong table row or demographic block |
| `SUPPORTED` | `CONTRADICTS_CLAIM` | **494** | Claim value was perturbed but labeled as `SUPPORTED`, or evidence captures old row |
| `DISCREPANCY` | `TRUNCATED_OR_INCOMPLETE` | **392** | Evidence window truncated before observation; unobserved negative mislabeled as discrepancy |
| `SUPPORTED` | `TRUNCATED_OR_INCOMPLETE` | **377** | Evidence window truncated the analyte header row |
| `DISCREPANCY` | `INVALID_PROVENANCE` | **256** | Fallback header `[0, 100]` assigned to discrepancy claim |
| `SUPPORTED` | `INVALID_PROVENANCE` | **256** | Fallback header `[0, 100]` assigned to supported claim |
| `DISCREPANCY` | `SUPPORTS_CLAIM` | **135** | Claim actually matches evidence text, but was labeled `DISCREPANCY` |
| `INSUFFICIENT_EVIDENCE`| `CONTRADICTS_CLAIM` | **50** | Evidence contains contradiction, but labeled `INSUFFICIENT_EVIDENCE` |
| `INSUFFICIENT_EVIDENCE`| `TRUNCATED_OR_INCOMPLETE` | **3** | Incomplete text snippet assigned to negative control |
| **Total Conflicts** | — | **2,745** | **29.94% of dataset** |

---

## E. High-Priority Suspicious Examples

The table below documents specific high-priority cases, including the previously observed **Ex9**, **Ex12**, out-of-bounds cases, and header fallback anomalies:

| Example ID | Doc ID | Field | Claim | Ground Truth | Offsets | Source Page | Detected Problem | Reason for Flagging |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `bad63dac-be2f-46e9-b7d3-22a8278315bf` (Ex9) | `MED_0010` | `med.frequency` | `Insulin glargine frequency: at bedtime` | `INSUFFICIENT_EVIDENCE` | `[0, 217]` | Page 1 | Corrupted Header Fallback | Evidence text is discharge letterhead. Insulin glargine not present. |
| `02f9d689-ba1b-4a7e-adfb-0ec2dc30faa7` (Ex12)| `LAB_0037` | `lab.value` | `Creatinine: 0.16 mg/dL` | `DISCREPANCY` | `[440, 480]` | Page 1 | Demographics in Evidence | Evidence text is `"Date of Birth: 1954-08-15 Ordering Clinician..."`. No Creatinine in window. |
| `6de66e4f-b20c-422c-90dd-ec1dc38a586d` (Ex10)| `LAB_0031` | `lab.value` | `BUN: 14 mg/dL` | `SUPPORTED` | `[406, 434]` | Page 1 | Shifted Evidence Window | Offsets point to atomic `"BUN 14.0"`, but evidence window started at char 197 capturing demographics. |
| `66f8b880-14e7-4bdc-bd96-bd5ae6fa8b35` | `LAB_0028` | `lab.value` | `Red Blood Cell Count: 0.61 10*6/uL` | `DISCREPANCY` | `[598, 603]` | Page 1 | Out-of-Bounds Offsets | Offsets `[598, 603]` exceed total document character length (598 chars). |
| `3831d8a4-24d9-4c13-ab82-8184c605c3d8` | `LAB_0028` | `lab.value` | `Red Blood Cell Count: 6.12 10*6/uL` | `SUPPORTED` | `[598, 603]` | Page 1 | Out-of-Bounds Offsets | Offsets `[598, 603]` exceed total document character length (598 chars). |
| `38f93953-f159-4eb3-bf90-1fac50b8d923` | `LAB_0020` | `lab.value` | `Red Blood Cell Count: 0.59 10*6/uL` | `DISCREPANCY` | `[518, 522]` | Page 1 | Out-of-Bounds Offsets | Offsets `[518, 522]` exceed document character length (520 chars). |
| `12e612de-3e4a-48c7-ba20-1295303d18ea` | `LAB_0020` | `lab.value` | `Red Blood Cell Count: 5.93 10*6/uL` | `SUPPORTED` | `[518, 522]` | Page 1 | Out-of-Bounds Offsets | Offsets `[518, 522]` exceed document character length (520 chars). |
| `9e66fd9d-9ab5-4068-a6aa-2e01dca53ae0` | `LAB_0025` | `lab.value` | `Potassium: 0.51 mmol/L` | `DISCREPANCY` | `[0, 100]` | Page 1 | Clinic Header Fallback | Evidence text is `"Metro Health Diagnostic Center Outpatient Pathology..."`. |
| `68f576fe-21d5-4f2c-8ef8-8223d9b26eb5` | `LAB_0045` | `lab.value` | `Sodium: 137 mmol/L` | `INSUFFICIENT_EVIDENCE` | `[0, 100]` | Page 1 | Clinic Header Fallback | Evidence text is hospital address header. |
| `8eaec441-7c9b-443b-b85f-8828715ecef5` | `LAB_0043` | `lab.value` | `Chloride: 108 mmol/L` | `SUPPORTED` | `[0, 100]` | Page 1 | Clinic Header Fallback | Labeled SUPPORTED, but evidence text is clinic address header. |
| `20695533-08a9-4eef-bd28-1e066302f102` | `LAB_0017` | `lab.value` | `Potassium: 4.1 mmol/L` | `SUPPORTED` | `[0, 100]` | Page 1 | Clinic Header Fallback | Labeled SUPPORTED, but evidence is laboratory letterhead. |
| `1c804ce0-9836-4d31-8af1-c3b176b3c0b0` | `LAB_0026` | `lab.value` | `Carbon Dioxide: 24 mmol/L` | `SUPPORTED` | `[803, 807]` | Page 1 | Wrong Entity Contamination | Claim is Carbon Dioxide; evidence text contains Glucose and BUN. |
| `2d1e20f7-4851-423e-a07f-5488a78ac838` | `LAB_0019` | `lab.value` | `Alkaline Phosphatase: 85 U/L`| `SUPPORTED` | `[633, 642]` | Page 1 | Wrong Entity Contamination | Claim is Alkaline Phosphatase; evidence text contains BUN and Creatinine. |

---

## F. Aggregate Integrity Assessment

The 9,168 examples are classified into four mutual integrity tiers:

| Integrity Classification | Count | Percentage | Definition |
| :--- | :--- | :--- | :--- |
| **CLEAN** | **4,086** | **44.57%** | Character offsets are valid atomic/verbatim spans; semantic relationship matches ground truth |
| **SUSPICIOUS** | **2,786** | **30.39%** | Degraded OCR offset shifts, borderline window clipping, or soft semantic ambiguity |
| **DEFINITELY INVALID** | **2,296** | **25.04%** | Out-of-bounds offsets, clinic letterhead fallbacks `[0, 100]`, or direct entity contradictions in `SUPPORTED` claims |
| **Requires Manual Review** | **1,500** | **16.36%** | Subset of suspicious examples where automatic alignment is ambiguous |

### Breakdown by Severity
- **Clean Subsets (`severity == 'clean'`)**:
  - CLEAN: 3,340 (72.67%)
  - SUSPICIOUS: 643 (13.99%)
  - DEFINITELY INVALID: 613 (13.34%)
- **Degraded Subsets (`severity in ('low', 'medium', 'high')`)**:
  - CLEAN: 746 (16.32%)
  - SUSPICIOUS: 2,143 (46.87%)
  - DEFINITELY INVALID: 1,683 (36.81%)

---

## G. Recommended Remediation Plan

*(READ-ONLY AUDIT: None of the following remediations have been performed.)*

| Problem Category | Recommended Action | Affected Count | Justification |
| :--- | :--- | :--- | :--- |
| **Degraded OCR Offset Mismatch** | **Safe to Automatically Repair** | **1,286 examples** | The target entity and value exist intact in the canonical document. An automated bounded fuzzy alignment can re-anchor `char_start` and `char_end` to canonical coordinates without modifying claims or ground-truth labels. |
| **Semantic Boundary Ambiguity / Context Clipping** | **Requires Manual Review** | **1,500 examples** | Evidence windows where the analyte name is partially clipped or where multi-word units create ambiguity must be inspected by a clinician/annotator before re-labeling. |
| **Fatal Entity Mismatches & Out-of-Bounds** | **Should Be Excluded** | **1,656 examples** | Claims where the entity does not exist anywhere in the document (yet were labeled `SUPPORTED`), or where offsets exceed document bounds, represent fatal label corruption and must be pruned. |
| **Corrupted Default Fallback Headers `[0, 100]`** | **Requires Regeneration** | **640 examples** | The synthetic generation script failed to locate the analyte in degraded text and defaulted to the header. These examples must be regenerated with OCR-aware substring matching or dropped. |

---

## H. Final Benchmark Suitability Conclusion

Based strictly on empirical audit counts, the current canonical dataset (`data/m2_verification/all_examples.jsonl`) is assessed as follows:

| Target Use Case | Current Suitability | Justification |
| :--- | :--- | :--- |
| **1. Model Training** | **NOT SUITABLE** | Over **25% of examples are definitely invalid** and contain severe label noise (e.g. 782 `SUPPORTED` claims without the target entity). Training ModernBERT or other NLI models on this data injects toxic supervision signals and ruins calibration. |
| **2. Validation** | **NOT SUITABLE** | With **29.94% ground-truth/evidence conflicts**, standard evaluation metrics (AUROC, Macro-F1, Brier score, ECE) are mathematically distorted. A verifier that correctly catches a missing entity is penalized as false-positive discrepancy. |
| **3. OOD Evaluation** | **NOT SUITABLE** | The degraded and OOD slices suffer disproportionately from the `[0, 100]` clinic header fallback (640 examples) and corrupt OCR offset mismatches (2,687 examples), rendering OOD robustness measurements invalid. |
| **4. Publication-Quality Results** | **NOT SUITABLE** | Peer review audit of evidence-grounded extraction would immediately flag that hundreds of examples point to clinic letterheads and demographic blocks instead of clinical observations. |

### Path to Certification
Before executing Experiment V8 (Phase 4 cascade evaluation), Experiment V10 (OOD template holdout), or any further model fine-tuning:
1. Prune the **1,656 fatal invalid examples** from the benchmark.
2. Regenerate or re-extract the **640 header fallback examples** (`[0, 100]`).
3. Re-anchor the **1,286 repairable degraded offsets** to canonical text coordinates.
4. Update `data/m2_verification/split_manifest.json` with the certified, audited benchmark version.
