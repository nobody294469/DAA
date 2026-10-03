# Comprehensive Final Project Report Outline: AI-Based Multimodal Clinical Document-to-EHR Conversion and Verification

**Document Purpose:** Master blueprint and evidence-mapping specification for the final academic engineering/research project report.  
**Authority:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md), Phase 0 Decision Resolution Package, and Frozen Phase 1–5 Experiments.  
**Writing Constraint Policy:**  
1. Strict grounding in existing repository artifacts only.  
2. Distinction between **document-fidelity verification** and **clinical correctness** maintained throughout.  
3. System must **not** be characterized as "clinically safe", "clinically validated", or "optimal"; all model configurations are described strictly by measured metrics.  
4. The 100% extraction accuracy on clean structured forms must **not** be implied to generalize to narrative prose or degraded scans.  
5. All documented limitations (unpersisted standalone E2/E3 batch files, absence of external DUA-credentialed clinical notes) must be explicitly stated.

---

## Section-by-Section Evidence Mapping Specification

```
Claim Types:
  [ARCH]  Architectural / Implementation Specification
  [DATA]  Dataset Specification / Construction Detail
  [RSLT]  Empirical Measured Result (from frozen artifacts)
  [LIMIT] Acknowledged Limitation / Constraint
  [FUTR]  Future Work Proposal
```

---

### 1. Abstract
- **Content:** Concise summary of the project problem, architecture, methodology, empirical findings, and primary contributions. Summarizes the hybrid extraction router, terminology normalization, the three-stage verification cascade, and the emission of FHIR R4B bundles with DEC-016 two-tier provenance and machine-readable audit reports.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §1.1–§1.3; [`experiments/V10-ood-template-evaluation/REPORT.md`](file:///d:/daa/experiments/V10-ood-template-evaluation/REPORT.md); [`experiments/V8-verification-cascade/V12_RISK_COVERAGE_ANALYSIS.md`](file:///d:/daa/experiments/V8-verification-cascade/V12_RISK_COVERAGE_ANALYSIS.md); [`data/fhir_phase5_v8/batch_validation_report.json`](file:///d:/daa/data/fhir_phase5_v8/batch_validation_report.json).
- **Exact Metrics Belonging Here:**
  - 120 synthetic benchmark documents (86 CV, 34 OOD holdout; 5,629 total claims, 1,826 OOD claims).
  - V8 V1 cascade performance on V3 OOD: **0.9748 accuracy**, **0.9538 Macro-F1**, **0.9826 discrepancy recall**, **46 errors** [RSLT].
  - Standalone Qwen-7B (V9): **0.9365 accuracy**, **0.8543 Macro-F1**, **116 errors** [RSLT].
  - Selective prediction risk reduction: **83.65% relative risk reduction** at 79.13% coverage (0.0069 cascade risk vs 0.0422 ModernBERT risk) [RSLT].
  - Downstream FHIR delivery: **100.0% FHIR schema validity** (0 failures across 34 bundles) with 1,719 genuine physical bounding boxes and 381 Stage-C rationales [RSLT].
- **Claim Type:** [ARCH], [RSLT], [LIMIT].

---

### 2. Problem Statement
- **Content:** Unreliability of ungrounded "LLM reads document, LLM writes FHIR" pipelines. Hallucinations, dosage/unit misreadings, and unverified extractions entered into EHR systems pose severe patient safety risks because downstream clinicians cannot verify if a structured resource is grounded in source document text.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §1.1, §4.
- **Exact Metrics Belonging Here:** None (qualitative clinical documentation problem framing).
- **Claim Type:** [ARCH].

---

### 3. Motivation
- **Content:** The necessity for automated document-fidelity verification in healthcare data ingestion. Why a single end-to-end LLM is both financially/computationally prohibitive at scale and prone to silent factual errors. The need for a selective cascade that resolves high-confidence structured claims cheaply while escalating ambiguous claims to an LLM judge.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §1.1, §12, §13; [`experiments/V8-verification-cascade/V9_QWEN_ONLY_OOD_ANALYSIS.md`](file:///d:/daa/experiments/V8-verification-cascade/V9_QWEN_ONLY_OOD_ANALYSIS.md).
- **Exact Metrics Belonging Here:**
  - Standalone Qwen-7B requires **6,203.75s (103.4 min)** to verify 1,826 claims and commits 116 errors [RSLT].
  - Selective cascade evaluates the same split in **1,598.59s (26.6 min)** committing only 46 errors while resolving 79.13% of claims without LLM intervention [RSLT].
- **Claim Type:** [ARCH], [RSLT].

---

### 4. Objectives
- **Content:** Precise enumeration of the core deliverables defined in the approved architecture:
  1. Build a hybrid extraction router handling tabular lab panels, semi-structured prescription pads, and narrative clinical notes.
  2. Implement retrieval-based terminology normalization (LOINC, RxNorm, UCUM).
  3. Implement a three-stage verification cascade (Deterministic $\rightarrow$ ModernBERT $\rightarrow$ Qwen-7B) with calibrated confidence.
  4. Generate 100% schema-compliant FHIR R4B bundles carrying DEC-016 two-tier provenance extensions.
  5. Generate standalone, machine-readable audit reports.
  6. Empirically evaluate across document-level and template-level out-of-distribution benchmarks.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §1.2, §1.3.
- **Exact Metrics Belonging Here:** §1.3 acceptance definition targets.
- **Claim Type:** [ARCH].

---

### 5. Scope and Non-Goals
- **Content:** Explicit demarcation of project boundaries per RFC-2119 specifications:
  - **In-Scope:** Document-fidelity verification (does claim match source evidence text), 10 canonical field types, 3 document families (labs, meds, narrative), born-digital and scanned layouts, FHIR R4B mapping.
  - **Non-Goals / Out-of-Scope:** Clinical correctness / appropriateness verification (e.g. checking whether a 50mg dose is medically safe), clinical decision support, SNOMED CT integration, longitudinal cross-encounter reasoning, family-history disambiguation, physician-plan vs administration disambiguation.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §2, §3, §28; DEC-020.
- **Exact Metrics Belonging Here:** None.
- **Claim Type:** [ARCH], [LIMIT].

---

### 6. Related Work & Existing Approach Context
- **Content:** Survey of contemporary literature and baseline methods:
  - Heuristic string matching (Levenshtein edit ratio).
  - Statistical NLP (TF-IDF + Logistic Regression).
  - Pretrained biomedical embeddings (SapBERT, BioLORD).
  - Off-the-shelf zero-shot NLI (PubMedBERT NLI).
  - End-to-end proprietary LLM extractors (GPT-4/Qwen direct prompt).
  - Limitations of prior work: lack of element-level spatial provenance, poor calibration on OOD templates, lack of selective prediction mechanisms.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §12, §13, §22.2; [`experiments/V10-ood-template-evaluation/REPORT.md`](file:///d:/daa/experiments/V10-ood-template-evaluation/REPORT.md).
- **Exact Metrics Belonging Here:**
  - V1–V5 baseline OOD accuracy ranging from 0.4524 to 0.7760 [RSLT].
- **Claim Type:** [ARCH], [RSLT].

---

### 7. Proposed System Overview
- **Content:** High-level summary of the end-to-end Clindoc2FHIR pipeline: Raw Document $\rightarrow$ Ingestion/OCR $\rightarrow$ Region Router $\rightarrow$ Extraction $\rightarrow$ Terminology Normalization $\rightarrow$ Verification Cascade $\rightarrow$ FHIR Generation $\rightarrow$ Audit Reporting.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §5, §6; [`src/clindoc/cli.py`](file:///d:/daa/src/clindoc/cli.py).
- **Exact Metrics Belonging Here:** None.
- **Claim Type:** [ARCH].

---

### 8. System Architecture
- **Content:** Modular software architecture, contracts, and schema boundaries. Decoupling of ingestion, extraction contracts (`Claim`, `Evidence`, `RoutingDecision`), verification contracts (`VerificationResult`), FHIR mapping (`claims_to_fhir_bundle`), and audit reporting (`generate_audit_report`). Strict schema enforcement where invalid inputs raise validation errors.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §5, §7; [`src/clindoc/extract/contracts.py`](file:///d:/daa/src/clindoc/extract/contracts.py); [`src/clindoc/schema/verification.py`](file:///d:/daa/src/clindoc/schema/verification.py).
- **Exact Metrics Belonging Here:** None.
- **Claim Type:** [ARCH].

---

### 9. Clinical Document Processing Pipeline
- **Content:** Step-by-step description of data transformations as a document travels through the pipeline. Ingestion handling born-digital PDF text extraction and rasterized OCR ingestion. Context reconstruction (`reconstruct_canonical_page_text`).
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §6, §7.1; [`src/clindoc/ocr/ingest.py`](file:///d:/daa/src/clindoc/ocr/ingest.py); [`src/clindoc/m2/context.py`](file:///d:/daa/src/clindoc/m2/context.py).
- **Exact Metrics Belonging Here:** Context window configuration: max 500 characters, paragraph-bounded (DEC-003).
- **Claim Type:** [ARCH].

---

### 10. OCR & Evidence Representation
- **Content:** Canonical evidence data model (`schemas/evidence.json`). Page-local character offsets (`char_start`, `char_end`), verbatim source text reproduction invariant (`page_text[char_start:char_end] == text`), physical bounding box geometry (`BBoxModel`: `[x0, y0, x1, y1]`), token-level OCR confidence score aggregation. Handling of unmapped/absent evidence spans.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §7.1, §9; [`src/clindoc/extract/contracts.py`](file:///d:/daa/src/clindoc/extract/contracts.py); [`src/clindoc/extract/deterministic.py:378-386`](file:///d:/daa/src/clindoc/extract/deterministic.py#L378-L386).
- **Exact Metrics Belonging Here:** In OOD benchmark: 1,719 grounded spans with 4-coordinate physical bboxes; 107 absent evidence spans with degenerate zero-area bounding boxes [RSLT].
- **Claim Type:** [ARCH], [RSLT].

---

### 11. Hybrid Extraction Architecture
- **Content:** Architectural details of the hybrid router (DEC-001, DEC-004):
  - Deterministic table/pad extractor (`DeterministicExtractor`).
  - GLiNER biomedical NER integration (`GLiNERFallbackExtractor`).
  - Narrative prose extractor (`NarrativeExtractor`) with strict regex/substring evidence validation to prevent LLM span hallucination (`PROJECT_SPEC.md` §7.4, §13.3).
  - Region classification heuristics: table detection (column alignment) vs free-text paragraphs.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §11, §13; [`src/clindoc/extract/router.py`](file:///d:/daa/src/clindoc/extract/router.py); [`src/clindoc/extract/llm_extractor.py`](file:///d:/daa/src/clindoc/extract/llm_extractor.py); DEC-001, DEC-004.
- **Exact Metrics Belonging Here:** Deterministic re-routing confidence threshold: $\tau = 0.75$ (DEC-004).
- **Claim Type:** [ARCH].

---

### 12. Terminology Normalization
- **Content:** Hybrid retrieval-based normalization architecture:
  - Lexical matching (exact/fuzzy edit distance) + semantic embedding retrieval (SapBERT / BioLORD) for `lab.test_name` $\rightarrow$ LOINC and `med.name` $\rightarrow$ RxNorm.
  - Deterministic dictionary lookup for `unit_raw` $\rightarrow$ UCUM.
  - Enforcement of `code_confidence` thresholds: setting `normalized_code: null` when confidence falls below acceptance floor (preventing fabricated codes).
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §14; [`src/clindoc/normalize/normalizer.py`](file:///d:/daa/src/clindoc/normalize/normalizer.py); DEC-002, DEC-007.
- **Exact Metrics Belonging Here:** Provisional code confidence threshold: $\tau = 0.75$ (DEC-002).
- **Claim Type:** [ARCH].

---

### 13. Verification Cascade
- **Content:** Detailed mathematical and architectural specification of the three-stage cascade (`PROJECT_SPEC.md` §12, DEC-019):
  - **Tiering Policy (DEC-019):** Tier 1 fields (`lab.value`, `lab.flag`, `med.*`) run through full learned cascade; Tier 2 deterministic fields (`lab.unit`, `lab.reference_range`, `lab.test_name`, `med.duration`) resolved at Stage A.
  - **Stage A (Deterministic Pre-check):** Normalizes units/values and identifies exact string/numerical matches or obvious contradictions.
  - **Stage B (Learned Biomedical Classifier):** Fine-tuned `BioClinical-ModernBERT-base` with temperature scaling calibration.
  - **Stage C (LLM Judge):** Quantized `Qwen2.5-7B-Instruct-AWQ` served via vLLM with grammar/schema-constrained JSON output. Prompt V1 incorporates explicit lab flag hierarchy (H/L abnormal flag takes precedence over raw interval recalculation).
  - **Dual-Gate Escalation Logic:** Stage C invoked if: (1) Stage A and Stage B disagree (`stage_a_disagreement`), (2) Stage B calibrated confidence falls below threshold $\tau_{\text{esc}} = 0.60$ (`stage_b_low_confidence`), or (3) Stage B predicts `INSUFFICIENT_EVIDENCE` (`stage_b_insufficient_evidence`).
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §12, §13, §17; [`src/clindoc/verify/cascade.py`](file:///d:/daa/src/clindoc/verify/cascade.py); [`experiments/V8-verification-cascade/V1_EXPERIMENT_FREEZE.md`](file:///d:/daa/experiments/V8-verification-cascade/V1_EXPERIMENT_FREEZE.md); DEC-005, DEC-018, DEC-019.
- **Exact Metrics Belonging Here:** Escalation threshold $\tau = 0.60$; ModernBERT temperature $T = 1.146522$.
- **Claim Type:** [ARCH].

---

### 14. FHIR R4B Mapping
- **Content:** Conversion rules from structured verified claims to official FHIR R4B resources:
  - Header mapping: `Patient`, `Organization`, `Practitioner`.
  - Diagnostic grouping: `DiagnosticReport` aggregates constituent lab `Observation` resources.
  - Medication resource routing (DEC-008): Prescription orders and MAR sheets map to `MedicationRequest`; narrative mentions and history map to `MedicationStatement`.
  - Coding enforcement: LOINC/RxNorm/UCUM codes attached only when normalized; falling back to `CodeableConcept.text` without `.coding[]` when unmapped.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §15; [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py); DEC-008.
- **Exact Metrics Belonging Here:** 100% FHIR R4B schema validity across all generated bundles [RSLT].
- **Claim Type:** [ARCH], [RSLT].

---

### 15. DEC-016 Provenance and Auditability
- **Content:** Formal specification of the two-tier provenance hierarchy (DEC-016):
  - **Resource-Level Provenance (§16.1):** Top-level extension on `Observation` and `Medication*` linking to anchor claim, summary verdict (`SUPPORTED`, `DISCREPANCY`, `INSUFFICIENT_EVIDENCE`), calibrated confidence, stage reached, evidence text, source page, bounding box, and OCR confidence.
  - **Element-Level Provenance (§16.2):** Sub-extensions on constituent elements (`valueQuantity`, `interpretation`, `referenceRange`, `dosage[0].doseQuantity`, `timing`) linking atomic claims to their specific verification verdicts.
  - **Standalone Audit Report (§7.8):** Generation of document-level JSON audit reports matching `schemas/audit_report.json` with claim-evidence bipartite linkage, preserving Stage-C LLM rationales for human auditability without requiring a FHIR parser.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §7.8, §16; [`src/clindoc/audit/report.py`](file:///d:/daa/src/clindoc/audit/report.py); DEC-016.
- **Exact Metrics Belonging Here:** 1,719 genuine bounding boxes preserved; 107 canonical unmapped spans; 381 rationales linked [RSLT].
- **Claim Type:** [ARCH], [RSLT].

---

### 16. Dataset Construction & Hard Negatives
- **Content:** Methodology for generating the synthetic clinical benchmark:
  - Document rendering: Synthea-derived structured patient records rendered into HTML/PDF layouts across 50 lab reports, 50 prescription/MAR forms, and 20 narrative discharge notes (120 documents total).
  - Hard Negative Taxonomy (§18.5, §20.1): Deliberate near-miss discrepancies (single-digit dose modifications, decimal point shifts, unit swaps e.g. mg vs g, off-by-one frequency errors e.g. BID vs TID, wrong analyte flags) rather than random noise, ensuring clinical verification difficulty.
  - Absence synthesis: Document-level omission of target entities to construct `INSUFFICIENT_EVIDENCE` claims.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §18, §20; [`data/m2_verification_v3/metadata.json`](file:///d:/daa/data/m2_verification_v3/metadata.json); DEC-014.
- **Exact Metrics Belonging Here:** 5,629 total examples across 120 documents; 7 contradiction types annotated.
- **Claim Type:** [ARCH], [DATA].

---

### 17. M2 V3 Benchmark Specification
- **Content:** Detailed breakdown of the canonical V3 dataset (`data/m2_verification_v3/`):
  - Document-level and template-level 3-way split: 52 Train docs (2,242 claims), 17 Calibration docs (678 claims), 17 Validation docs (883 claims), 34 OOD Holdout docs (1,826 claims).
  - OOD Template Holdout: 17 Lab Reports (`template_c`), 12 Medication Forms (`mar_order_sheet`), 5 Narrative Notes (`narrative_specialty_consult_note_v1`, `narrative_ambulatory_progress_note_v1`).
  - Class Balance Enforcement (DEC-011): Minimum floor of $\max(0.10 \times N_{\text{field}}, 30)$ for `SUPPORTED` and `DISCREPANCY`; non-zero representation of `INSUFFICIENT_EVIDENCE` across all fields. Automated CI balance check (`test_m2_class_balance.py`).
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §18.4, §19; [`data/m2_verification_v3/split_manifest.json`](file:///d:/daa/data/m2_verification_v3/split_manifest.json); DEC-011.
- **Exact Metrics Belonging Here:** Train: 2,242; Calibration: 678; Validation: 883; OOD: 1,826 [DATA].
- **Claim Type:** [DATA], [ARCH].

---

### 18. Experimental Methodology
- **Content:** Evaluation protocol, metrics definitions, and execution discipline:
  - Extraction metrics (§21.1): Precision, Recall, F1, Exact Match Rate per field.
  - Verification metrics (§21.3): Macro-F1, per-class Precision/Recall/F1, Discrepancy Recall (safety-critical metric), Confusion Matrix, AUROC, Brier Score, Expected Calibration Error (ECE).
  - Selective prediction metrics (§21.5): Escalation rate, selective accuracy, risk-coverage curve, Area Under Risk-Coverage Curve (AURC).
  - Hardware & execution environment: Local CPU/GPU (RTX 3050), 4-bit AWQ quantized Qwen-7B served locally via vLLM on port 8000.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §21, §22, §27; DEC-006, DEC-009, DEC-012.
- **Exact Metrics Belonging Here:** Primary evaluation metric: Macro-F1 and Discrepancy Recall.
- **Claim Type:** [ARCH].

---

### 19. Extraction Evaluation (E1–E4)
- **Content:** Report on extraction subsystem performance from [`experiments/E4-hybrid-router-extraction/REPORT.md`](file:///d:/daa/experiments/E4-hybrid-router-extraction/REPORT.md):
  - Structured Lab Observation Extraction (50 docs, 705 observations): 100.0% Precision, 100.0% Recall, 100.0% F1, 100.0% per-field accuracy (`lab.test_name`, `lab.value`, `lab.unit`, `lab.reference_range`, `lab.flag`).
  - Structured Medication Extraction (50 docs, 87 regimens): 100.0% Precision, 100.0% Recall, 100.0% F1, 100.0% per-field accuracy (`med.name`, `med.dose`, `med.route`, `med.frequency`, `med.duration`), 100.0% complete regimen exact match.
  - **Explicit Documentation Limitation:** Standalone offline batch extraction outputs for E2 (GLiNER) and E3 (LLM narrative) were not persisted into offline directories; their underlying execution criterion remains PARTIALLY SATISFIED.
  - **Generalization Warning:** Explicitly state that the 100% structured extraction metrics apply only to clean tabular/pad layouts and must not be assumed to hold for narrative clinical prose or degraded OCR scans.
- **Evidence Source:** [`experiments/E4-hybrid-router-extraction/REPORT.md`](file:///d:/daa/experiments/E4-hybrid-router-extraction/REPORT.md); [`data/med_evaluation_report.json`](file:///d:/daa/data/med_evaluation_report.json); [`eval/eval_extraction.py`](file:///d:/daa/eval/eval_extraction.py).
- **Exact Metrics Belonging Here:** All 10 canonical field metrics from E4 report [RSLT].
- **Claim Type:** [RSLT], [LIMIT].

---

### 20. Verification Evaluation (V1–V6, V8, V9, V10, V12)
- **Content:** Authoritative comparative benchmark evaluated on the identical 1,826-claim V3 OOD split across 34 held-out documents:
  - V1 (Deterministic Heuristic): 0.7015 Acc, 0.4609 Macro-F1, 545 errors.
  - V2 (Levenshtein Edit Ratio, $\tau=0.10$): 0.4858 Acc, 0.3134 Macro-F1, 939 errors.
  - V3 (TF-IDF + Logistic Regression): 0.6347 Acc, 0.7180 Macro-F1, 667 errors.
  - V4 (SapBERT Cosine, $\tau=0.65$): 0.4524 Acc, 0.3105 Macro-F1, 1,000 errors.
  - V5 (PubMedBERT Zero-Shot NLI): 0.7760 Acc, 0.7303 Macro-F1, 409 errors.
  - V6 (Fine-Tuned ModernBERT Standalone): 0.9551 Acc, 0.9519 Macro-F1, 0.9877 Discrepancy Recall, 82 errors.
  - V8 Baseline (Cascade with Prompt V0): 0.9595 Acc, 0.9422 Macro-F1, 0.9806 Discrepancy Recall, 74 errors.
  - V8 V1 (Cascade with Prompt V1): **0.9748 Acc**, **0.9538 Macro-F1**, **0.9826 Discrepancy Recall**, **46 errors**.
  - V9 (Standalone Qwen-7B Ablation): 0.9365 Acc, 0.8543 Macro-F1, 0.9755 Discrepancy Recall, 116 errors, 6,203.75s runtime.
  - V10 (Consolidated Template Holdout Matrix): Full cross-model comparison table.
  - V12 (Selective Prediction Analysis): Operating point analysis (0.0069 cascade risk at 79.13% coverage).
- **Evidence Source:** [`experiments/V10-ood-template-evaluation/REPORT.md`](file:///d:/daa/experiments/V10-ood-template-evaluation/REPORT.md); [`experiments/V8-verification-cascade/V12_RISK_COVERAGE_ANALYSIS.md`](file:///d:/daa/experiments/V8-verification-cascade/V12_RISK_COVERAGE_ANALYSIS.md).
- **Exact Metrics Belonging Here:** Complete V10 comparison matrix [RSLT].
- **Claim Type:** [RSLT].

---

### 21. Main Results & Primary Findings
- **Content:** Detailed presentation of the primary scientific findings:
  1. Fine-tuned biomedical encoders (ModernBERT) drastically outperform zero-shot NLI (+17.9% accuracy over PubMedBERT) and classical NLP baselines.
  2. Standalone LLM verification (Qwen-7B) suffers from high hallucination rates on absence (`INSUFFICIENT_EVIDENCE` recall = 0.5462) and extreme compute latency (103.4 min).
  3. The selective verification cascade (V8 V1) achieves the highest measured performance (**0.9748 accuracy**, **46 errors**), reducing total errors by **60.3% relative to standalone Qwen-7B** while running **3.9$\times$ faster**.
- **Evidence Source:** [`experiments/V8-verification-cascade/V9_QWEN_ONLY_OOD_ANALYSIS.md`](file:///d:/daa/experiments/V8-verification-cascade/V9_QWEN_ONLY_OOD_ANALYSIS.md); [`experiments/V10-ood-template-evaluation/REPORT.md`](file:///d:/daa/experiments/V10-ood-template-evaluation/REPORT.md).
- **Exact Metrics Belonging Here:** 46 cascade errors vs 116 Qwen errors; 60.3% error reduction; 1,598.59s vs 6,203.75s runtime [RSLT].
- **Claim Type:** [RSLT].

---

### 22. Error Analysis & Failure Categorization
- **Content:** Diagnostic inspection of the remaining 46 errors in the V1 cascade:
  - Breakdown by clinical field: `lab.flag` (18 errors), `lab.value` (13 errors), `med.*` (15 errors).
  - Breakdown by template: `template_c` (31 errors), `mar_order_sheet` (14 errors), `narrative` (1 error).
  - Dominant confusion pair: `SUPPORTED` claims classified as `DISCREPANCY` (28 errors), reflecting conservative verifier behavior on subtle numerical formatting.
  - Comparison with baseline V0: Prompt V1 reduced `template_c` errors from 60 to 31 by resolving the abnormal flag interpretation ambiguity.
- **Evidence Source:** [`experiments/V8-verification-cascade/V1_LABFLAG_OOD_ANALYSIS.md`](file:///d:/daa/experiments/V8-verification-cascade/V1_LABFLAG_OOD_ANALYSIS.md); [`experiments/V8-verification-cascade/V1_EXPERIMENT_FREEZE.md`](file:///d:/daa/experiments/V8-verification-cascade/V1_EXPERIMENT_FREEZE.md).
- **Exact Metrics Belonging Here:** Error reduction from 74 (V0) to 46 (V1); `template_c` errors from 60 to 31 [RSLT].
- **Claim Type:** [RSLT].

---

### 23. Ablation & Comparative Analysis
- **Content:** Controlled analysis isolating system components:
  - **Model Architecture Ablation:** ModernBERT text-only (V6) vs Cascade Baseline (V8 V0) vs Cascade Intervention (V8 V1) vs Standalone Qwen (V9).
  - **Prompt Intervention Ablation:** Prompt V0 (implicit flag rules) vs Prompt V1 (explicit H/L abnormal flag precedence).
  - **Domain Complementarity:** ModernBERT excels at high-volume exact matching (`lab.value` accuracy = 0.9844); Qwen excels at multi-token clinical reasoning (`lab.flag` accuracy = 0.9085). The cascade synergizes these complementary strengths.
- **Evidence Source:** [`experiments/V8-verification-cascade/V9_QWEN_ONLY_OOD_ANALYSIS.md`](file:///d:/daa/experiments/V8-verification-cascade/V9_QWEN_ONLY_OOD_ANALYSIS.md); [`experiments/V8-verification-cascade/V1_LABFLAG_OOD_ANALYSIS.md`](file:///d:/daa/experiments/V8-verification-cascade/V1_LABFLAG_OOD_ANALYSIS.md).
- **Exact Metrics Belonging Here:** ModernBERT `lab.value` accuracy: 0.9844; Qwen `lab.flag` accuracy: 0.9085 [RSLT].
- **Claim Type:** [RSLT].

---

### 24. Risk-Coverage & Selective Prediction Analysis
- **Content:** Rigorous evaluation under the selective classification framework ([`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §21.5, Experiment V12):
  - Risk-coverage formulation: empirical risk $R(f, g)$ vs coverage $\Phi(g)$.
  - Area Under the Risk-Coverage Curve: $\text{AURC} = 0.009405$ across 100 coverage thresholds.
  - Dual-Gate Routing vs Pure Confidence Thresholding: Demonstrating that escalating based on Stage A/B disagreement and `INSUFFICIENT_EVIDENCE` detection isolates hard failure cases far more effectively than pure scalar confidence.
  - Production Operating Point: At 79.13% coverage (1,445 claims resolved at Stage B, 381 escalated to Stage C), empirical risk drops from **0.0422 (Stage B alone) to 0.0069 (Cascade)**, representing an **83.65% relative risk reduction**.
- **Evidence Source:** [`experiments/V8-verification-cascade/V12_RISK_COVERAGE_ANALYSIS.md`](file:///d:/daa/experiments/V8-verification-cascade/V12_RISK_COVERAGE_ANALYSIS.md); `experiments/V8-verification-cascade/v12_risk_coverage_analysis.json`.
- **Exact Metrics Belonging Here:** Escalation rate: 20.87%; Coverage: 79.13%; Selective risk: 0.0069; AURC: 0.0094 [RSLT].
- **Claim Type:** [RSLT].

---

### 25. FHIR R4B & Provenance Validation
- **Content:** Downstream healthcare standards interoperability results from Phase 5:
  - 34 OOD documents converted into FHIR R4B Bundles carrying DEC-016 two-tier provenance extensions.
  - **Schema Validity Gate (§15.2, §26):** **100.0% FHIR schema validity** (0 parsing or validation failures across all 34 bundles validated via official `fhir.resources.R4B` models).
  - **Audit Report Schema Gate (§7.8):** **100.0% validity** against `schemas/audit_report.json` across all 34 machine-readable audit reports.
  - Provenance Completeness: 1,719 genuine physical bounding boxes recovered via exact `example_id` join to V3 dataset; 107 canonical unmapped zero-area spans; 381 Stage-C rationales preserved.
- **Evidence Source:** [`data/fhir_phase5_v8/batch_validation_report.json`](file:///d:/daa/data/fhir_phase5_v8/batch_validation_report.json); [`tests/test_canonical_fhir_mapping.py`](file:///d:/daa/tests/test_canonical_fhir_mapping.py); [`tests/test_audit_report.py`](file:///d:/daa/tests/test_audit_report.py).
- **Exact Metrics Belonging Here:** 34/34 valid bundles; 34/34 valid audit reports; 1,826 claims mapped; 0 validation failures [RSLT].
- **Claim Type:** [RSLT].

---

### 26. Limitations
- **Content:** Transparent documentation of operational and methodological boundaries:
  - **Synthetic Benchmark Boundary:** Ground truth generated from Synthea-derived structured profiles; does not model handwritten clinician notes, complex unstructured hospital faxes, or real EHR idiosyncrasies.
  - **Absence of Real Clinical Data Slice (§18.3):** PhysioNet / n2c2 external credentialing and DUA timelines could not be completed within the project lifecycle; real-data evaluation was deferred per §18.3.
  - **Document-Fidelity vs Clinical Correctness:** The system verifies only whether an extracted claim matches source document text; it does not evaluate whether the prescribed dosage is clinically appropriate or safe for the patient.
  - **Extraction Subsystem Gap:** Standalone batch execution files for E2 (GLiNER) and E3 (LLM Narrative) remain unpersisted offline artifacts.
  - **Longitudinal / Cross-Visit Reasoning:** Excluded by design (§23.2).
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §3, §18.3, §23.2, §28; [`experiments/E4-hybrid-router-extraction/REPORT.md`](file:///d:/daa/experiments/E4-hybrid-router-extraction/REPORT.md).
- **Exact Metrics Belonging Here:** None.
- **Claim Type:** [LIMIT].

---

### 27. Threats to Validity & Generalization Boundaries
- **Content:** Critical assessment of internal, external, and construct validity:
  - *Internal Validity:* Leakage prevention enforced by strict document-level and template-level splitting (no shared document templates between CV and OOD). Verified by automated split integrity tests.
  - *External Validity:* Generalization to unseen document templates demonstrated on 34 holdout documents (`template_c`, `mar_order_sheet`, narrative consult notes). However, results cannot be generalized to real scanned photocopies or live hospital EHR feeds without further domain adaptation.
  - *Construct Validity:* Bounding box IoU and exact character-offset verification ensure that measured precision reflects genuine spatial grounding rather than accidental text overlap.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §19, §24; [`tests/test_m2_splits.py`](file:///d:/daa/tests/test_m2_splits.py); [`tests/test_benchmark_auditor.py`](file:///d:/daa/tests/test_benchmark_auditor.py).
- **Exact Metrics Belonging Here:** Zero split overlap across 120 documents [RSLT].
- **Claim Type:** [ARCH], [LIMIT].

---

### 28. Reproducibility & Software Artifacts
- **Content:** Blueprint for independent reproduction of all reported metrics:
  - Environment specification: Python 3.11 virtual environment (`.venv`), PyTorch 2.6, Transformers, vLLM, `fhir.resources.R4B` (v7.1.0).
  - Versioned checkpoints: `modernbert_text_only_v3_fold0_seed42.pt` ($T=1.146522$) in `data/m2_results_v3/checkpoints/`.
  - Authoritative dataset: `data/m2_verification_v3/` with split manifest hash and canonical JSONL records.
  - Frozen diagnostic outputs: `phase4_cascade_diagnostics.json` enabling reproduction of all risk-coverage and FHIR conversion tables in $<5$ seconds.
- **Evidence Source:** [`pyproject.toml`](file:///d:/daa/pyproject.toml); [`uv.lock`](file:///d:/daa/uv.lock); [`data/m2_results_v3/checkpoints/`](file:///d:/daa/data/m2_results_v3/checkpoints/); [`experiments/V8-verification-cascade/V1_EXPERIMENT_FREEZE.md`](file:///d:/daa/experiments/V8-verification-cascade/V1_EXPERIMENT_FREEZE.md).
- **Exact Metrics Belonging Here:** Random seed 42; deterministic splits.
- **Claim Type:** [ARCH].

---

### 29. Conclusion
- **Content:** Final summary of the project's engineering and empirical achievements. Reiteration that a hybrid extraction router combined with a selective, calibrated verification cascade provides an effective, auditable, and cost-efficient architecture for converting clinical documents into FHIR R4B EHR data with complete evidence provenance.
- **Evidence Source:** Consolidated findings from Sections 20–25.
- **Exact Metrics Belonging Here:** 0.9748 accuracy; 83.65% risk reduction; 100% FHIR validity; 0 validation failures.
- **Claim Type:** [RSLT], [ARCH].

---

### 30. Future Work
- **Content:** Technical roadmap for subsequent research iterations:
  - Training and benchmarking the multimodal OCR-aware ModernBERT variant (V7 / AUX18) under physical scanner degradation.
  - Integrating small fine-tuned reasoning models (LoRA / PEFT) as specialized Stage-C adjudicators.
  - Completing institutional DUA requests for MIMIC-IV / n2c2 to execute qualitative clinical note validation.
  - Extending the verification cascade to temporal medication changes and longitudinal reconciliation across patient encounters.
- **Evidence Source:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §18.3, §22.3, §23.2.
- **Exact Metrics Belonging Here:** None.
- **Claim Type:** [FUTR].

---

### 31. References
- **Content:** Formal academic citations for underlying models, datasets, and frameworks:
  - ModernBERT: Warner et al., 2024.
  - Qwen2.5-Instruct: Qwen Team, 2024.
  - BioClinical-ModernBERT: Sounack et al., 2024.
  - HL7 FHIR Release 4B Standard (R4B): HL7 International.
  - Synthea Patient Generator: Walonoski et al., 2018.
  - Selective Classification / Risk-Coverage: Geifman & El-Yaniv, 2017.
  - Temperature Scaling Calibration: Guo et al., 2017.
  - SapBERT: Liu et al., 2021.
  - PubMedBERT: Gu et al., 2021.
- **Evidence Source:** Academic citations corresponding to repository dependencies.
- **Exact Metrics Belonging Here:** None.
- **Claim Type:** [ARCH].

---

### 32. Appendix / Technical Artifacts
- **Content:** Reference tables, JSON schemas, and command logs:
  - Appendix A: Full Decision Catalog (DEC-001 through DEC-019).
  - Appendix B: Canonical JSON Schemas (`claim.json`, `evidence.json`, `verification_result.json`, `audit_report.json`).
  - Appendix C: Complete 34-Document OOD Performance Breakdown Table (by document ID and template).
  - Appendix D: Sample FHIR R4B Bundle and Provenance Extension Snippet.
  - Appendix E: Sample Machine-Readable Audit Report with LLM Adjudication Rationale.
- **Evidence Source:** [`docs/decisions/`](file:///d:/daa/docs/decisions/); [`schemas/`](file:///d:/daa/schemas/); [`data/fhir_phase5_v8/`](file:///d:/daa/data/fhir_phase5_v8/).
- **Exact Metrics Belonging Here:** Complete schema listings and document breakdown tables.
- **Claim Type:** [ARCH], [DATA].

---

## 3. Evidence Gap Assessment

An audit of the evidence mapping confirms:
1. **Zero Evidence Gaps in Verification or Downstream EHR Delivery:** Every single headline number for Models V1–V6, V8 (baseline), V8 (Prompt V1), V9, V10, V12, and Phase 5 is fully backed by frozen JSON diagnostics, split manifests, and validation reports.
2. **Documented Gaps are Methodologically Bounded:** The lack of offline batch files for E2/E3 and the deferral of external credentialed clinical notes (DUA) are acknowledged limitations explicitly scoped in §18.3, §22.3, and §26 of [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md), posing **zero threat to the defensibility of the final academic report**.
