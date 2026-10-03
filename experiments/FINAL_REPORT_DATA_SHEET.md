# Final Project Report & Presentation Authoritative Data Sheet

**Authority Reference:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md)  
**Parent Reconciliation Audit:** [`experiments/FINAL_PROJECT_RECONCILIATION_AUDIT.md`](file:///d:/daa/experiments/FINAL_PROJECT_RECONCILIATION_AUDIT.md)  
**Date:** 2026-10-03  
**Governance Directive:** This data sheet is the **SOLE AUTHORITATIVE NUMERICAL REFERENCE** for the final project report, research paper, and presentation slides. All cited metrics, tables, and figures MUST cite exact values from this document.

---

## 1. Dataset & Benchmark Architecture (Certified V3 Final Release)

| Metric Description | Exact Value | Numerator / Denominator | Split / Dataset | Canonical Source Artifact |
|:---|:---:|:---:|:---|:---|
| **Total Certified Claims** | **5,629** | 5,629 / 5,629 | Full Benchmark V3 | `data/m2_verification_v3/all_examples.jsonl` |
| **Total Clinical Documents** | **120** | 50 Lab + 50 Med + 20 Narrative | Full Benchmark V3 | `data/m2_verification_v3/split_manifest.json` |
| **Unique Finding Groups (DEC-015)** | **2,078** | 2,078 groups | Full Benchmark V3 | `data/m2_verification_v3/all_examples.jsonl` |
| **Clean Extractions & Controls** | **3,878** | 3,878 / 5,629 (68.9%) | Full Benchmark V3 | `reports/M2_V3_FULL_REGENERATION_REPORT.md` |
| **Multi-Level Degraded Pairs** | **1,751** | 1,751 / 5,629 (31.1%) | Full Benchmark V3 | `reports/M2_V3_FULL_REGENERATION_REPORT.md` |
| **Train Partition Claims** | **2,242** | 2,242 / 5,629 (39.8%) | Train Split (52 docs) | `data/m2_verification_v3/split_manifest.json` |
| **Calibration Partition Claims** | **678** | 678 / 5,629 (12.0%) | Calibration Split (17 docs)| `data/m2_verification_v3/split_manifest.json` |
| **Validation Partition Claims (In-Dist)**| **883** | 883 / 5,629 (15.7%) | Validation Split (17 docs) | `data/m2_verification_v3/split_manifest.json` |
| **OOD Template Holdout Claims** | **1,826** | 1,826 / 5,629 (32.4%) | OOD Split (34 docs) | `data/m2_verification_v3/split_manifest.json` |
| **Total Benchmark SUPPORTED Claims** | **2,270** | 2,270 / 5,629 (40.3%) | Full Benchmark V3 | `data/m2_verification_v3/all_examples.jsonl` |
| **Total Benchmark DISCREPANCY Claims** | **2,925** | 2,925 / 5,629 (52.0%) | Full Benchmark V3 | `data/m2_verification_v3/all_examples.jsonl` |
| **Total Benchmark INSUFFICIENT Claims**| **434** | 434 / 5,629 (7.7%) | Full Benchmark V3 | `data/m2_verification_v3/all_examples.jsonl` |
| **OOD SUPPORTED Claims** | **729** | 729 / 1,826 (39.9%) | OOD Template Holdout | `data/m2_verification_v3/split_manifest.json` |
| **OOD DISCREPANCY Claims** | **978** | 978 / 1,826 (53.6%) | OOD Template Holdout | `data/m2_verification_v3/split_manifest.json` |
| **OOD INSUFFICIENT_EVIDENCE Claims** | **119** | 119 / 1,826 (6.5%) | OOD Template Holdout | `data/m2_verification_v3/split_manifest.json` |
| **Document Split Leakage** | **0** | 0 documents overlapping | Cross-Split Intersection | Split Integrity Audit |
| **Composite Group Split Leakage** | **0** | 0 groups overlapping | Cross-Split Intersection | Group Integrity Audit |
| **all_examples.jsonl Hash** | `40278c01...` | SHA256 (64 hex chars) | Full Benchmark V3 | `reports/M2_V3_FULL_REGENERATION_REPORT.md` |
| **split_manifest.json Hash** | `cf41cc1d...` | SHA256 (64 hex chars) | Full Benchmark V3 | `reports/M2_V3_FULL_REGENERATION_REPORT.md` |

---

## 2. Ingestion & Extraction Performance (E1–E5)

| Metric Description | Exact Value | Numerator / Denominator | Split / Dataset | Canonical Source Artifact |
|:---|:---:|:---:|:---|:---|
| **Lab Observation Detection Precision** | **1.0000** | 705 / 705 | 50 Lab Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Lab Observation Detection Recall** | **1.0000** | 705 / 705 | 50 Lab Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Lab Observation Detection F1** | **1.0000** | 705 observations | 50 Lab Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Lab Per-Field Accuracy (`lab.test_name`)**| **1.0000** | 705 / 705 | 50 Lab Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Lab Per-Field Accuracy (`lab.value`)** | **1.0000** | 705 / 705 | 50 Lab Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Lab Per-Field Accuracy (`lab.unit`)** | **1.0000** | 705 / 705 | 50 Lab Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Lab Per-Field Accuracy (`lab.reference_range`)**| **1.0000**| 705 / 705 | 50 Lab Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Lab Per-Field Accuracy (`lab.flag`)** | **1.0000** | 705 / 705 | 50 Lab Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Medication Regimen Detection Precision**| **1.0000** | 87 / 87 | 50 Med Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Medication Regimen Detection Recall** | **1.0000** | 87 / 87 | 50 Med Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Medication Regimen Detection F1** | **1.0000** | 87 regimens | 50 Med Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Med Per-Field Accuracy (`med.name`)** | **1.0000** | 87 / 87 | 50 Med Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Med Per-Field Accuracy (`med.dose`)** | **1.0000** | 87 / 87 | 50 Med Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Med Per-Field Accuracy (`med.route`)** | **1.0000** | 87 / 87 | 50 Med Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Med Per-Field Accuracy (`med.frequency`)**| **1.0000**| 87 / 87 | 50 Med Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Med Per-Field Accuracy (`med.duration`)** | **1.0000** | 87 / 87 | 50 Med Documents (E1) | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Header Metadata Extraction Rate** | **100.0%** | 6 fields across 100 docs | 100 Structured Docs | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **Evidence Offset Preservation Rate** | **1.0000** | 100% verbatim grounding | 100 Structured Docs | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **E2 Standalone GLiNER Micro-P / R / F1**| **0.4390 / 0.1737 / 0.2489** | 774 matched / 1,763 pred (4,456 GT)| 120 Documents (E2) | `experiments/E2-gliner-extraction/REPORT.md` |
| **E2 Standalone GLiNER Macro-F1** | **0.3455** | Across 10 canonical fields | 120 Documents (E2) | `experiments/E2-gliner-extraction/REPORT.md` |
| **E2 GLiNER `med.name` P / R / F1** | **0.9257 / 0.9648 / 0.9448** | 137 matched / 148 pred (142 GT) | 120 Documents (E2) | `experiments/E2-gliner-extraction/REPORT.md` |
| **E2 GLiNER `med.dose` P / R / F1** | **0.9085 / 0.9789 / 0.9424** | 139 matched / 153 pred (142 GT) | 120 Documents (E2) | `experiments/E2-gliner-extraction/REPORT.md` |
| **E3 Standalone Narrative Micro-P/R/F1** | **0.6783 / 0.1444 / 0.2382** | 78 matched / 115 pred (540 GT) | 20 Narrative Docs (E3)| `experiments/E3-narrative-extraction/REPORT.md` |
| **E3 Standalone Narrative Macro-F1**| **0.2047** | Across 10 canonical fields | 20 Narrative Docs (E3)| `experiments/E3-narrative-extraction/REPORT.md` |
| **E3 Narrative Execution Mode** | **Offline MockLLMProvider**| DEC-006 / configs/extraction.yaml (Qwen Colab documented) | 20 Narrative Docs (E3)| `experiments/E3-narrative-extraction/REPORT.md` |

---

## 3. Verification Models Comparative Performance (All on V3 OOD, 1,826 Claims)

| Model Configuration | Overall Accuracy | Macro-F1 | Discrepancy Recall | Misclassifications (Errors) | Runtime | Authoritative Source Artifact |
|:---|:---:|:---:|:---:|:---:|:---:|:---|
| **V1: Deterministic Matcher** | **0.7015** (70.15%) | **0.4609** | **0.9765** (955 / 978) | 545 / 1,826 | 0.03s | `v1_v5_v3_ood_results.json` |
| **V2: Levenshtein Distance** | **0.4858** (48.58%) | **0.3134** | **0.6779** (663 / 978) | 939 / 1,826 | 0.52s | `v1_v5_v3_ood_results.json` |
| **V3: TF-IDF + Logistic Reg** | **0.6347** (63.47%) | **0.7180** | **0.6810** (666 / 978) | 667 / 1,826 | 1.09s | `v1_v5_v3_ood_results.json` |
| **V4: SapBERT Embedding Sim** | **0.4524** (45.24%) | **0.3105** | **0.4765** (466 / 978) | 1,000 / 1,826 | 193.33s | `v1_v5_v3_ood_results.json` |
| **V5: PubMedBERT Zero-Shot** | **0.7760** (77.60%) | **0.7303** | **0.6319** (618 / 978) | 409 / 1,826 | 38.82s | `v1_v5_v3_ood_results.json` |
| **V6: ModernBERT Standalone** | **0.9551** (95.51%) | **0.9519** | **0.9877** (966 / 978) | 82 / 1,826 | ~45.0s | `V10 REPORT.md`, `V12 ANALYSIS.md` |
| **V8: Cascade Baseline (V0)**| **0.9595** (95.95%) | **0.9422** | **0.9806** (959 / 978) | 74 / 1,826 | ~1,500s | `V3_PHASE4_BASELINE_REPORT.md` |
| **V8: Cascade Controlled (V1)**| **0.9748** (97.48%)| **0.9538** | **0.9826** (961 / 978) | **46 / 1,826** | 1,598.59s | `phase4_cascade_eval_report.json` |
| **V9: Standalone Qwen-7B** | **0.9365** (93.65%) | **0.8543** | **0.9755** (954 / 978) | 116 / 1,826 | 6,203.75s | `V9_QWEN_ONLY_OOD_ANALYSIS.md` |

---

## 4. Phase 4 Verification Cascade Execution Details (Validation vs OOD)

| Metric Description | Exact Value | Numerator / Denominator | Split Partition | Canonical Source Artifact |
|:---|:---:|:---:|:---|:---|
| **Validation Cascade Accuracy** | **0.9921** (99.21%) | 876 / 883 correct | V3 Validation Split | `V3_PHASE4_BASELINE_REPORT.md` |
| **Validation Cascade Macro-F1** | **0.9801** | Across 3 classes | V3 Validation Split | `V3_PHASE4_BASELINE_REPORT.md` |
| **Validation Discrepancy Recall** | **0.9978** (99.78%) | 458 / 459 caught | V3 Validation Split | `V3_PHASE4_BASELINE_REPORT.md` |
| **Validation Stage B Resolved Count**| **775** (87.77%) | 775 / 883 claims | V3 Validation Split | `V3_PHASE4_BASELINE_REPORT.md` |
| **Validation Stage B Subset Accuracy**| **0.9935** (99.35%)| 770 / 775 correct | V3 Validation Split | `V3_PHASE4_BASELINE_REPORT.md` |
| **Validation Stage C Escalation Count**| **108** (12.23%) | 108 / 883 claims | V3 Validation Split | `V3_PHASE4_BASELINE_REPORT.md` |
| **Validation Stage C Subset Accuracy**| **0.9815** (98.15%)| 106 / 108 correct | V3 Validation Split | `V3_PHASE4_BASELINE_REPORT.md` |
| **OOD Cascade Accuracy (V1 Prompt)** | **0.9748** (97.48%) | 1,780 / 1,826 correct | V3 OOD Split | `phase4_cascade_eval_report.json` |
| **OOD Cascade Macro-F1 (V1 Prompt)** | **0.9538** | Across 3 classes | V3 OOD Split | `phase4_cascade_eval_report.json` |
| **OOD Discrepancy Recall (V1 Prompt)**| **0.9826** (98.26%)| 961 / 978 caught | V3 OOD Split | `phase4_cascade_eval_report.json` |
| **OOD Stage B Resolved Count** | **1,445** (79.13%)| 1,445 / 1,826 claims | V3 OOD Split | `phase4_cascade_eval_report.json` |
| **OOD Stage B Subset Accuracy** | **0.9931** (99.31%)| 1,435 / 1,445 correct | V3 OOD Split | `phase4_cascade_eval_report.json` |
| **OOD Stage C Escalation Count** | **381** (20.87%) | 381 / 1,826 claims | V3 OOD Split | `phase4_cascade_eval_report.json` |
| **OOD Stage C Subset Accuracy (V0 Prompt)**| **0.8294** (82.94%)| 316 / 381 correct | V3 OOD Split | `V3_PHASE4_BASELINE_REPORT.md` |
| **OOD Stage C Subset Accuracy (V1 Prompt)**| **0.9055** (90.55%)| 345 / 381 correct | V3 OOD Split | `V1_LABFLAG_OOD_ANALYSIS.md` |
| **Stage C Genuine Invocation Rate** | **100.0%** | 381 / 381 invocations | V3 OOD Split | `phase4_cascade_diagnostics.json` |
| **Stage C Transport / HTTP Errors** | **0** | 0 errors | V3 OOD Split | `phase4_cascade_diagnostics.json` |
| **Stage C Schema / JSON Parse Errors**| **0** | 0 errors | V3 OOD Split | `phase4_cascade_diagnostics.json` |
| **Stage C Fallback Verdicts** | **0** | 0 fallbacks | V3 OOD Split | `phase4_cascade_diagnostics.json` |

---

## 5. Selective Prediction & Risk-Coverage Analysis (V12)

| Metric Description | Exact Value | Standalone Stage B Reference | Performance Delta / Improvement | Canonical Source Artifact |
|:---|:---:|:---:|:---|:---|
| **Autonomous Tier-1 Coverage** | **79.13%** (1,445 / 1,826) | 100.0% (No Abstention) | Operates at zero LLM inference cost | `V12_RISK_COVERAGE_ANALYSIS.md` |
| **Autonomous Tier-1 Accuracy** | **99.31%** (1,435 / 1,445) | 95.51% (1,744 / 1,826) | +3.80% accuracy on resolved subset | `V12_RISK_COVERAGE_ANALYSIS.md` |
| **Autonomous Tier-1 Empirical Risk**| **0.0069** (0.69%) | 0.0449 (4.49%) | -3.80% risk reduction | `V12_RISK_COVERAGE_ANALYSIS.md` |
| **Stage C Escalation Rate** | **20.87%** (381 / 1,826) | 0.00% (No Escalation) | Selective triage to 7B LLM judge | `V12_RISK_COVERAGE_ANALYSIS.md` |
| **Stage C Escalated Accuracy** | **90.55%** (345 / 381) | *81.10% (Stage B alone)*| **+9.45% accuracy (+36 errors salvaged)** | `V12_RISK_COVERAGE_ANALYSIS.md` |
| **Full Cascade Overall Accuracy** | **97.48%** (1,780 / 1,826) | 95.51% (1,744 / 1,826) | **+1.97% overall accuracy** | `V12_RISK_COVERAGE_ANALYSIS.md` |
| **Full Cascade Empirical Risk** | **0.0252** (2.52%) | 0.0449 (4.49%) | **-1.97% overall risk** | `V12_RISK_COVERAGE_ANALYSIS.md` |
| **Total System Misclassifications**| **46 errors** | 82 errors | **-36 errors (-43.90% relative error)** | `V12_RISK_COVERAGE_ANALYSIS.md` |
| **Dual-Gate vs Confidence Risk (79.1% Cov)**| **0.0069 vs 0.0422** | 10 vs 61 errors | **6.1$\times$ risk reduction** via Stage A | `V12_RISK_COVERAGE_ANALYSIS.md` |
| **Stage B Alone Calibrated AURC** | **0.034600** | N/A | Fitted on V3 Calibration ($T=1.1465$) | `v12_risk_coverage_analysis.json` |
| **Stage B Alone Raw Softmax AURC** | **0.034700** | N/A | Uncalibrated Softmax baseline | `v12_risk_coverage_analysis.json` |
| **Full Cascade Naive Joint AURC** | **0.054300** | N/A | Elevated due to uncalibrated Stage C `1.0`| `v12_risk_coverage_analysis.json` |

---

## 6. Phase 5 FHIR R4B Conformance & Terminology Serialization

| Metric Description | Historical Frozen Baseline | Remediated Post-Audit Tier | Delta / Interpretation | Canonical Source Artifact |
|:---|:---:|:---:|:---|:---|
| **Artifact Directory Path** | `data/fhir_phase5_v8/` | `data/fhir_phase5_v8_remediated/` | Separately partitioned | Repository File System |
| **Total Serialized Documents** | **34 documents** | **34 documents** | 100% document parity | Batch Summary JSON |
| **Total Serialized Claims** | **1,826 claims** | **1,826 claims** | 100% claim parity | Batch Summary JSON |
| **FHIR R4B Schema Validity** | **100%** (0 errors / 34) | **100%** (0 errors / 34) | 0 errors, 0 warnings | `batch_validation_report.json` |
| **DEC-016 Provenance Completeness** | **100%** (1,826 claims) | **100%** (1,826 claims) | 100% resource & element | `batch_validation_report.json` |
| **Physical BBoxes Preserved** | **1,719 bboxes** | **1,719 bboxes** | 100% identical coordinates | Batch Summary JSON |
| **Absence BBoxes (0,0,0,0)** | **107 bboxes** | **107 bboxes** | 100% unmapped controls | Batch Summary JSON |
| **Observation LOINC Codings** | **0 / 603 (0.00%)** | **603 / 603 (100.00%)** | **+603 codings (+100.0%)** | `PHASE5_REMEDIATION_REPORT.md` |
| **Medication RxNorm Codings** | **0 / 94 (0.00%)** | **94 / 94 (100.00%)** | **+94 codings (+100.0%)** | `PHASE5_REMEDIATION_REPORT.md` |
| **Quantity UCUM System & Code** | **0 / 697 (0.00%)** | **697 / 697 (100.00%)** | **+697 units (+100.0%)** | `PHASE5_REMEDIATION_REPORT.md` |
| **HL7 Abnormal Flag Codings** | **256 / 256 (100.0%)** | **256 / 256 (100.0%)** | 0 (Unchanged) | `PHASE5_REMEDIATION_REPORT.md` |
| **In-Bundle Coded Claims** | **256 / 1,130 (22.65%)**| **1,130 / 1,130 (100.0%)**| **+874 claims (+77.35%)** | `PHASE5_REMEDIATION_REPORT.md` |
| **Verification Label Invariance**| Identical to V8 | **1,826 / 1,826 (100.0%)** | **Bit-for-bit identical** | `verify_remediation_invariance.py`|

---

## 7. PROJECT_SPEC.md §21.7 End-to-End Pipeline Evaluation

| Compound Evaluation Metric | Historical Frozen Baseline (`data/fhir_phase5_v8/`) | Remediated Post-Audit Tier (`data/fhir_phase5_v8_remediated/`) | Delta / Interpretation | Canonical Source Artifact |
|:---|:---:|:---:|:---|:---|
| **Total Ground-Truth Claims** | **1,826** | **1,826** | Canonical V3 OOD Split | `eval/end_to_end_results*.json` |
| **Claims in Audit Reports** | **1,826 / 1,826 (100.0%)** | **1,826 / 1,826 (100.0%)** | 100% accountability | `eval/end_to_end_results*.json` |
| **Claims in FHIR Provenance** | **1,130 / 1,826 (61.88%)** | **1,130 / 1,826 (61.88%)** | In-bundle representation | `eval/end_to_end_results*.json` |
| **Shadowed Group Claims** | **696 / 1,826 (38.12%)** | **696 / 1,826 (38.12%)** | Grounding absence controls| `eval/end_to_end_results*.json` |
| **Correct Verification (Overall)** | **1,780 / 1,826 (97.48%)** | **1,780 / 1,826 (97.48%)** | **0.00% (Model Invariant)** | `eval/end_to_end_results*.json` |
| **Correct Verification (In-FHIR)** | **1,093 / 1,130 (96.73%)** | **1,093 / 1,130 (96.73%)** | **0.00% (Model Invariant)** | `eval/end_to_end_results*.json` |
| **Correct Value Representation** | **1,130 / 1,130 (100.0%)** | **1,130 / 1,130 (100.0%)** | 100% faithful values | `eval/end_to_end_results*.json` |
| **Normalized Codes in FHIR Bundle**| **256 / 1,130 (22.65%)** | **1,130 / 1,130 (100.0%)** | **+77.35% (Coded concepts)**| `eval/end_to_end_results*.json` |
| **Strict §21.7 Compound Accuracy** | **12.92%** (236 / 1,826) | **59.86%** (1,093 / 1,826) | **+46.94% (Serialization)** | `eval/end_to_end_results*.json` |
| *— Strict Concept Coding Rate* | *0.00% (LOINC/RxNorm missing)*| *100.00% (1,130 / 1,130)* | **Resolved parameter** | `PHASE5_REMEDIATION_REPORT.md` |
| **In-Bundle Verified Value Accuracy**| **59.86%** (1,093 / 1,826) | **59.86%** (1,093 / 1,826) | **0.00% (True fidelity)** | `eval/end_to_end_results*.json` |
| **Conditional In-Bundle Accuracy** | **96.73%** (1,093 / 1,130) | **96.73%** (1,093 / 1,130) | **0.00% (In-bundle quality)**| `eval/end_to_end_results*.json` |
| **Upstream-Joined Compound Accuracy**| **54.49%** (995 / 1,826) | **54.49%** (995 / 1,826) | **0.00% (Reference baseline)**| `eval/end_to_end_results*.json` |

---

## 8. Compute Environment & Runtime Profile

| Subsystem / Benchmark Component | Hardware Engine | Runtime (Wall-Clock) | Throughput / Latency | Canonical Source Artifact |
|:---|:---|:---:|:---:|:---|
| **V1: Deterministic Verifier** | Local Host (CPU) | **0.03 seconds** | ~60,866 claims/sec | `v1_v5_v3_ood_results.json` |
| **V2: Levenshtein Distance** | Local Host (CPU) | **0.52 seconds** | ~3,511 claims/sec | `v1_v5_v3_ood_results.json` |
| **V3: TF-IDF + Logistic Reg** | Local Host (CPU) | **1.09 seconds** | ~1,675 claims/sec | `v1_v5_v3_ood_results.json` |
| **V4: SapBERT Dense Embeddings** | Local Host (RTX 3050) | **193.33 seconds** | ~9.4 claims/sec | `v1_v5_v3_ood_results.json` |
| **V5: PubMedBERT Zero-Shot NLI** | Local Host (RTX 3050) | **38.82 seconds** | ~47.0 claims/sec | `v1_v5_v3_ood_results.json` |
| **V6: ModernBERT Standalone** | Local Host (RTX 3050) | **~45.0 seconds** | ~40.5 claims/sec | `V10 REPORT.md` |
| **V8: Full Cascade (V1 Prompt)** | Hybrid (RTX 3050 + T4 vLLM)| **1,598.59 seconds (~26.6m)**| **0.87 sec/claim** | `phase4_cascade_eval_report.json` |
| **V9: Standalone Qwen-7B** | NVIDIA Tesla T4 (vLLM) | **6,203.75 seconds (~103.4m)**| **3.40 sec/claim** | `V9_QWEN_ONLY_OOD_ANALYSIS.md` |
| **Phase 5 Remediated Generation**| Local Host (Python 3.11.9) | **~18.0 seconds** | 34 bundles / 1,826 claims | `batch_summary.json` |
| **§21.7 Deterministic Evaluation**| Local Host (Python 3.11.9) | **~4.0 seconds** | 1,826 claims evaluated | `eval/evaluate_end_to_end.py` |
