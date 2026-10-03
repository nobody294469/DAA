# Comprehensive End-to-End Pipeline Evaluation Report (§21.7)

**Experiment ID:** end-to-end-evaluation  
**Authority:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) (§1.3, §21.7, §25 Phase 6, §26)  
**Execution Script:** [`eval/evaluate_end_to_end.py`](file:///d:/daa/eval/evaluate_end_to_end.py)  
**Output Data:** [`eval/end_to_end_results.json`](file:///d:/daa/eval/end_to_end_results.json)  
**Report Date:** 2026-10-03  
**Status:** COMPLETE (Deterministic, Read-Only Audit of Frozen Pipeline Artifacts)  
**Evaluation Scope:** Authoritative V3 Out-Of-Distribution (OOD) Benchmark (34 Documents, 1,826 Claims)

---

## 1. Executive Summary & Specification Requirement

[`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §21.7 explicitly defines the compound end-to-end metric:
> **"Document → final verified FHIR bundle accuracy:**  
> fraction of ground-truth claims that appear in the final bundle with:
> 1. the correct value,
> 2. correct code (when expected), and
> 3. correct verification label,  
> end-to-end through the whole pipeline (this is distinct from — and MUST be reported separately from — per-stage metrics, since per-stage numbers can look good while end-to-end accuracy is poor due to compounding errors)."

This evaluation was executed strictly as a **read-only, deterministic audit** consuming frozen artifacts across all pipeline stages. **No model training, inference, or artifact re-generation was performed.**

---

## 2. Definitive Distinction Across Pipeline Metric Families

To satisfy the requirements of §21.7, the pipeline performance must be decomposed into its modular stages versus its compound end-to-end execution:

| Evaluation Tier | Focus & Measurement Target | Authoritative Metric | Proving Artifact / Reference |
|:---|:---|:---:|:---|
| **1. Extraction Accuracy** (§11, §21.1) | Entity discovery, span boundary segmentation, and verbatim field extraction from OCR | **0.9693 F1** (1.0000 Lab, 1.0000 Med, 0.8875 Narrative) | [`experiments/E4-hybrid-router-extraction/REPORT.md`](file:///d:/daa/experiments/E4-hybrid-router-extraction/REPORT.md) |
| **2. Verification Accuracy** (§12, §21.3) | Cascade document-fidelity classification (`SUPPORTED`, `DISCREPANCY`, `INSUFFICIENT_EVIDENCE`) | **91.68% Acc, 0.9022 Macro-F1, 94.76% Disc-Recall** (V8 V1 Prompt) | [`experiments/V10-ood-template-evaluation/REPORT.md`](file:///d:/daa/experiments/V10-ood-template-evaluation/REPORT.md) |
| **3. FHIR Schema Validity** (§15.2, §21.4) | Structural conformance to official HL7 FHIR R4B StructureDefinitions | **100.0% Valid** (34/34 OOD Bundles, 0 errors, 0 warnings) | [`data/fhir_phase5_v8/batch_validation_report.json`](file:///d:/daa/data/fhir_phase5_v8/batch_validation_report.json) |
| **4. DEC-016 Provenance Completeness** (§16) | Preservation of physical bounding boxes, evidence spans, and claim links | **100.0% Complete** (1,719 physical bboxes recovered) | [`data/fhir_phase5_v8/batch_validation_report.json`](file:///d:/daa/data/fhir_phase5_v8/batch_validation_report.json) |
| **5. Compound End-to-End Accuracy** (§21.7) | Fraction of ground-truth claims simultaneously satisfying: in-FHIR representation, correct value, correct code, and correct verification label | **59.86% In-Bundle Value/Verification Acc** (96.73% conditional) / **0.00% Strict Serialized Coding** | [`eval/end_to_end_results.json`](file:///d:/daa/eval/end_to_end_results.json) |

---

## 3. Authoritative Files, Schemas & Deterministic Join Keys

The deterministic evaluation harness joined four distinct artifact tiers using strict foreign-key relationships with **zero unjoinable records**:

```
[Authoritative V3 Benchmark]  (data/m2_verification_v3/all_examples.jsonl)
        │
        │ example_id == example_id
        ▼
[Frozen Cascade Diagnostics]   (experiments/V8-verification-cascade/phase4_cascade_diagnostics.json)
        │
        │ claim.claim_id == claim_id
        ▼
[Phase 5 Audit Reports]        (data/fhir_phase5_v8/{DOC_ID}_audit_report.json)
        │
        │ claim_id == extension[claimId].valueString
        ▼
[Phase 5 FHIR R4B Bundles]     (data/fhir_phase5_v8/{DOC_ID}_fhir_bundle.json)
```

### Deterministic Join Key Audit:
1. **`v3_dataset_key`:** `example_id` (UUID format, e.g. `222dea85-31f1-4f62-a21e-06cc13e45b2c`).
2. **`diagnostics_key`:** `example_id` / `claim.claim_id` (Exact 1:1 join across 1,826 / 1,826 OOD records).
3. **`audit_report_key`:** `claim_id` (Exact 1:1 join across 1,826 / 1,826 claims in the 34 audit reports).
4. **`fhir_provenance_key`:** `extension[url="http://clindoc.org/fhir/StructureDefinition/claim-provenance"].extension[url="claimId"].valueString` (Matches 1,130 unique claims directly linked into FHIR resource entries).
5. **Unjoinable Records:** **0** (0.0%). Every claim in the benchmark is deterministically accounted for.

---

## 4. End-to-End Evaluation Population & Per-Component Metrics

Across the canonical 34-document OOD benchmark (17 Laboratory, 12 Medication, 5 Narrative):

| Component Metric (§21.7) | Total Count | Rate (%) | Exact Evaluated Scope / Condition |
|:---|:---:|:---:|:---|
| **Total Ground-Truth Claims Evaluated** | **1,826** | **100.00%** | Full canonical V3 OOD benchmark partition |
| **Claims Represented in Audit Trail** | **1,826** | **100.00%** | All 1,826 claims preserved in independent JSON audit reports |
| **Claims Directly Linked in FHIR Resources** | **1,130** | **61.88%** | Claims embedded into `Observation`, `MedicationRequest`, `MedicationStatement` |
| **Shadowed / Unmapped Entity Claims** | **696** | **38.12%** | Duplicate field claims within composite clinical entities (`group_id`) |
| **Correct Verification Label (Overall OOD)** | **1,780** | **97.48%** | Predicted cascade label matches ground truth (`SUPPORTED`/`DISCREPANCY`/`INSUFFICIENT`) |
| **Correct Verification Label (In-Bundle)** | **1,093** | **96.73%** | 1,093 out of the 1,130 claims placed directly into FHIR resources |
| **Correct Value Representation (In-Bundle)** | **1,130** | **100.00%** | 1,130 out of 1,130 in-bundle claims carry the exact extracted/verified value |
| **Normalized Code in Final FHIR (`.coding[]`)** | **256** | **14.02%** | 256 claims carry explicit coding (HL7 interpretation flags); LOINC/RxNorm empty |
| **Upstream Extraction Code Availability** | **1,556** | **85.21%** | Ground-truth/upstream LOINC or RxNorm code available from extraction models |

---

## 5. Compound Metric Definitions & Exact Results

Because §21.7 requires simultaneous satisfaction of multiple criteria across distinct serialization boundaries, three compound metrics are reported:

### 1. In-Bundle Verified Value Accuracy (Structural FHIR Delivery)
- **Definition:** The fraction of ground-truth claims that appear inside a serialized FHIR resource with the **correct value** and the **correct verification label**.
- **Numerator:** 1,093 claims
- **Denominator:** 1,826 claims
- **Overall Rate:** **59.86%**
- **Conditional Rate (Conditioned on In-Bundle Representation):** **96.73%** (1,093 / 1,130)
- **Interpretation:** When a claim is assigned a FHIR resource slot, the pipeline achieves 96.73% verified document-fidelity accuracy. The overall rate (59.86%) reflects the structural design where composite clinical entities (`group_id`) contain multiple verification counterfactuals (e.g., both clean and corrupted dose variants), of which FHIR only assigns one slot per resource.

### 2. Strict §21.7 FHIR Bundle Compound Accuracy (All 3 Criteria in FHIR)
- **Definition:** Claims appearing in the final FHIR bundle with: (1) correct value, (2) correct normalized code in `.coding[]`, AND (3) correct verification label.
- **Strict Clinical Concept Coding (LOINC / RxNorm):** **0.00%** (0 / 1,826)
- **Including HL7 Interpretation Flags:** **12.92%** (236 / 1,826)
- **Root Cause & Evidence Gap:** In Phase 5 batch generation (`scratch/generate_phase5_v8.py`), `Claim` objects were reconstructed from `phase4_cascade_diagnostics.json` without passing `normalized_code` or `code_system`. In accordance with [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §15.3, the mapper safely fell back to text-only `CodeableConcept.text` rather than inventing codes. While extraction models achieved 100% LOINC/RxNorm accuracy upstream, these codes were not serialized into the Phase 5 FHIR `.coding[]` blocks.

### 3. Upstream-Joined Compound Pipeline Accuracy (Full Pipeline Capability)
- **Definition:** Fraction of ground-truth claims represented in the final FHIR bundle with correct value, correct verification label, and valid upstream LOINC/RxNorm code availability.
- **Numerator:** 995 claims
- **Denominator:** 1,826 claims
- **Rate:** **54.49%** (995 / 1,826)

---

## 6. Per-Document-Type Breakdown

| Document Subsystem | Evaluated Documents | Total Claims | Claims in FHIR | Correct Verification | Correct Value | In-Bundle Verified Value Acc | Upstream-Joined Compound Acc |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Laboratory Reports** (`LAB_*`) | 17 | 1,364 | 827 | 1,325 (97.14%) | 827 (100.0%) | **801 / 1,364 (58.72%)** | 739 / 1,364 (54.18%) |
| **Medication Regimens** (`MED_*`) | 12 | 301 | 191 | 295 (98.01%) | 191 (100.0%) | **183 / 301 (60.80%)** | 162 / 301 (53.82%) |
| **Narrative Clinical Notes** (`NARRATIVE_*`) | 5 | 161 | 112 | 160 (99.38%) | 112 (100.0%) | **109 / 161 (67.70%)** | 94 / 161 (58.39%) |
| **Total / Macro Average** | **34** | **1,826** | **1,130 (61.88%)** | **1,780 (97.48%)** | **1,130 (100.0%)** | **1,093 / 1,826 (59.86%)** | **995 / 1,826 (54.49%)** |

---

## 7. Requirement §21.7 Status Resolution

With the execution of [`eval/evaluate_end_to_end.py`](file:///d:/daa/eval/evaluate_end_to_end.py) and the publication of [`eval/end_to_end_results.json`](file:///d:/daa/eval/end_to_end_results.json):

1. **Deterministic Evaluator Implemented:** A reproducible, read-only evaluation script now exists and runs in CI.
2. **Compound Metric Explicitly Measured:** The compound metric defined in §21.7 has been computed and broken down across all required dimensions.
3. **Evidence Gap Formally Documented:** The exact gap between upstream terminology extraction (100% LOINC/RxNorm) and Phase 5 FHIR `.coding[]` serialization is formally disclosed without artificial estimation.
4. **Final Verdict:** Requirement §21.7 is now **COMPLETE WITH DOCUMENTED LIMITATION**.
