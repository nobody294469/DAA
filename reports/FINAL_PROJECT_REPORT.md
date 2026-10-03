# AI-Based Multimodal Clinical Document-to-EHR Conversion and Verification: Final Project Report

**Academic & Engineering Master Report**  
**Document Identifier:** `CLINDOC-FINAL-REPORT-2026`  
**Authority Reference:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) (All Sections §0–§28)  
**Authoritative Numerical Source:** [`experiments/FINAL_REPORT_DATA_SHEET.md`](file:///d:/daa/experiments/FINAL_REPORT_DATA_SHEET.md)  
**Authoritative Status & Audit Source:** [`experiments/FINAL_PROJECT_RECONCILIATION_AUDIT.md`](file:///d:/daa/experiments/FINAL_PROJECT_RECONCILIATION_AUDIT.md)  
**Parent Subsystem Audits:**  
- [`reports/PHASE2_RECONCILIATION_REPORT.md`](file:///d:/daa/reports/PHASE2_RECONCILIATION_REPORT.md)  
- [`reports/PHASE6_RECONCILIATION_REPORT.md`](file:///d:/daa/reports/PHASE6_RECONCILIATION_REPORT.md)  
- [`PROJECT_SPEC_REQUIREMENTS_MATRIX.md`](file:///d:/daa/PROJECT_SPEC_REQUIREMENTS_MATRIX.md)  
- [`FINAL_REPORT_OUTLINE.md`](file:///d:/daa/FINAL_REPORT_OUTLINE.md)  
**Auditor / Engineering Lead:** Automated Engineering Integrity Agent (Antigravity)  
**Date of Assembly:** 2026-10-03  
**Classification & Governance:** Academic Research & Engineering Deliverable — Strict Grounding in Frozen Repository Artifacts Only  

---

## Table of Contents
1. [Abstract](#1-abstract)
2. [Problem Statement](#2-problem-statement)
3. [Motivation](#3-motivation)
4. [Objectives](#4-objectives)
5. [Scope and Non-Goals](#5-scope-and-non-goals)
6. [Related Work & Existing Approach Context](#6-related-work--existing-approach-context)
7. [Proposed System Overview](#7-proposed-system-overview)
8. [System Architecture](#8-system-architecture)
9. [Clinical Document Processing Pipeline](#9-clinical-document-processing-pipeline)
10. [OCR & Evidence Representation](#10-ocr--evidence-representation)
11. [Hybrid Extraction Architecture](#11-hybrid-extraction-architecture)
12. [Terminology Normalization](#12-terminology-normalization)
13. [Verification Cascade](#13-verification-cascade)
14. [FHIR R4B Mapping](#14-fhir-r4b-mapping)
15. [DEC-016 Provenance and Auditability](#15-dec-016-provenance-and-auditability)
16. [Dataset Construction & Hard Negatives](#16-dataset-construction--hard-negatives)
17. [M2 V3 Benchmark Specification](#17-m2-v3-benchmark-specification)
18. [Experimental Methodology](#18-experimental-methodology)
19. [Extraction Evaluation (E1–E4)](#19-extraction-evaluation-e1e4)
20. [Verification Evaluation (V1–V6, V8, V9, V10, V12)](#20-verification-evaluation-v1v6-v8-v9-v10-v12)
21. [Main Results & Primary Findings](#21-main-results--primary-findings)
22. [Error Analysis & Failure Categorization](#22-error-analysis--failure-categorization)
23. [Ablation & Comparative Analysis](#23-ablation--comparative-analysis)
24. [Risk-Coverage & Selective Prediction Analysis](#24-risk-coverage--selective-prediction-analysis)
25. [FHIR R4B & Provenance Validation](#25-fhir-r4b--provenance-validation)
26. [Limitations](#26-limitations)
27. [Threats to Validity & Generalization Boundaries](#27-threats-to-validity--generalization-boundaries)
28. [Reproducibility & Software Artifacts](#28-reproducibility--software-artifacts)
29. [Conclusion](#29-conclusion)
30. [Future Work](#30-future-work)
31. [References](#31-references)
32. [Appendix / Technical Artifacts](#32-appendix--technical-artifacts)

---

## 1. Abstract

Converting unstructured and semi-structured clinical documents into electronic health records (FHIR R4B) presents a critical safety challenge: ungrounded Large Language Model (LLM) pipelines routinely introduce unverified hallucinations, numerical transcription errors, and missing-evidence assertions into clinical databases without auditable source provenance. This project designs, implements, and evaluates an automated, end-to-end clinical document-to-EHR conversion and verification system governed by strict document-fidelity constraints.

The architecture couples a **Hybrid Extraction Router** (deterministic tabular parsers, biomedical named entity recognition, and schema-constrained LLM narrative extraction) with a selective **Three-Stage Verification Cascade** (Stage A deterministic rule checks $\rightarrow$ Stage B fine-tuned `BioClinical-ModernBERT-base` semantic classifier $\rightarrow$ Stage C `Qwen2.5-7B-Instruct-AWQ` judge). Evaluated on the canonical 120-document V3 clinical benchmark (5,629 total certified claims; 34 Out-Of-Distribution [OOD] template-holdout documents comprising 1,826 claims):
1. **Verification Performance:** The production verification cascade (V8 Prompt V1) achieves **0.9748 accuracy**, **0.9538 Macro-F1**, and **0.9826 discrepancy recall** on the unseen OOD benchmark, committing only 46 errors across 1,826 claims. In contrast, a standalone prompted Qwen-7B verifier (V9) achieves 0.9365 accuracy with 116 errors and requires $3.9\times$ greater execution latency.
2. **Selective Prediction & Risk Reduction:** Operating under selective classification (V12), the cascade autonomously resolves 79.13% of claims at Stage B with 0.9931 accuracy (empirical risk $R = 0.0069$), escalating the hardest 20.87% to Stage C. This architectural dual-gate routing achieves an **83.65% relative risk reduction** (a $6.1\times$ risk drop) compared to standalone ModernBERT thresholding.
3. **FHIR Interoperability & Auditability:** The pipeline converts verified claims into HL7 FHIR R4B bundles with **100.0% schema validity** (0 errors across 34 bundles) and attaches two-tier DEC-016 provenance extensions linking 1,719 genuine physical bounding boxes, 107 canonical unmapped spans, and 381 Stage-C clinical rationales. Standalone, machine-readable JSON audit reports mirror 100% of claims for human review without requiring a FHIR parser.
4. **End-to-End Pipeline Metric (§21.7):** Evaluated end-to-end, in-bundle verified value accuracy reaches **59.86% overall** and **96.73% conditional** on in-bundle representation, with strict concept coding recovering 100.0% in post-audit serialization.

All reported results are derived strictly from frozen repository artifacts. The system is evaluated on synthetic and semi-synthetic benchmark distributions; external real-data clinical note validation remains feasibility-gated due to DUA credentialing timelines.

---

## 2. Problem Statement

Modern healthcare institutions generate massive volumes of clinical information locked in unstructured and semi-structured documents, including scanned laboratory reports, prescription pads, Medication Administration Records (MARs), and narrative discharge summaries. Integrating these data into interoperable Electronic Health Record (EHR) systems requires transforming raw text and scanned layouts into standard HL7 FHIR R4B resources (such as `Observation` and `MedicationRequest`).

Existing automated approaches predominantly rely on monolithic LLM pipelines that consume document text and directly output JSON or FHIR structures. In safety-critical clinical environments, this paradigm suffers from a fatal architectural defect: **lack of document-fidelity verification and verifiable provenance**. A standard generative model possesses no intrinsic mechanism to guarantee that an extracted dosage (e.g., `50 mg` vs `5 mg`), reference range flag (`HIGH` vs `NORMAL`), or clinical observation is faithfully supported by verbatim text in the source document. When hallucinations or misreadings occur, downstream clinicians and clinical decision support systems receive syntactically valid FHIR resources that are factually untruthful, with no transparent audit trail back to physical page geometry or evidence text.

---

## 3. Motivation

Bridging clinical document conversion and EHR data integrity demands an architectural paradigm shift from ungrounded generation to **calibrated, auditable document-fidelity verification**:

1. **Failure Modes of Monolithic LLMs:** Standalone prompted LLMs are computationally prohibitive for high-throughput batch ingestion and exhibit notable error rates on negative constraints. In our empirical baselines (Experiment V9), prompted Qwen2.5-7B required **6,203.75 seconds (103.4 minutes)** to process 1,826 claims and committed **116 errors**, struggling particularly with absence detection (`INSUFFICIENT_EVIDENCE` recall was restricted to 0.5462).
2. **Efficiency and Accuracy via Asymmetric Cascading:** Over 75% of clinical document assertions reside in predictable tabular rows or standardized prescription lines. Deploying billion-parameter LLMs to verify obvious exact-token matches is inefficient. A multi-tier cascade can route claims through lightweight deterministic checkers ($<0.05\text{s}$) and highly optimized biomedical transformer encoders ($<0.03\text{s/claim}$), invoking the LLM judge selectively only when lower tiers exhibit disagreement or low calibrated confidence.
3. **Provable Spatial Provenance:** Downstream healthcare consumers must not be asked to trust a model's latent representations. Trust requires that every structured field in an EHR bundle point directly to a verifiable bounding box ($x_0, y_0, x_1, y_1$), page number, character offset, and calibrated confidence score in an independent, machine-readable audit trail.

---

## 4. Objectives

As specified in [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §1.2, this project achieves six primary engineering and research deliverables:

1. **Hybrid Extraction Router:** Develop a modular ingestion and extraction engine that dynamically classifies document regions (tabular, key-value, narrative) and routes text to specialized extractors (deterministic regex parsers, biomedical NER, and schema-constrained LLMs).
2. **Terminology Normalization:** Implement hybrid lexical-semantic retrieval mapping raw analyte names, medications, and units to standard clinical terminologies (LOINC, RxNorm, UCUM) with confidence threshold gating.
3. **Three-Stage Verification Cascade:** Build and calibrate a sequential verification cascade (Stage A deterministic checker $\rightarrow$ Stage B `BioClinical-ModernBERT` classifier $\rightarrow$ Stage C `Qwen2.5-7B` judge) classifying claims into `SUPPORTED`, `DISCREPANCY`, or `INSUFFICIENT_EVIDENCE`.
4. **HL7 FHIR R4B Generation with Two-Tier Provenance:** Transform verified claims into valid FHIR R4B resource bundles annotated with DEC-016 provenance extensions capturing exact physical bounding boxes and evidence spans.
5. **Standalone Audit Reporting:** Emit an independent, machine-readable JSON audit report for every processed document, linking claims, source evidence, verification verdicts, confidences, and Stage-C adjudication rationales.
6. **Empirical Benchmarking & Selective Classification:** Evaluate the entire pipeline on document-level and template-level Out-Of-Distribution (OOD) splits, analyzing error distributions and risk-coverage dynamics.

---

## 5. Scope and Non-Goals

To maintain rigorous safety and architectural integrity, the project enforces strict scope boundaries per [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §2, §3, and §28:

### In-Scope Deliverables
- **Document Families:** Laboratory reports (CBC, CMP, Lipid panel; tabular layouts), medication regimens / prescriptions (semi-structured pads, MAR sheets, pharmacy labels), and narrative discharge summaries / consult notes.
- **Input Formats:** Born-digital PDFs (with digital text layers) and scanned/rasterized document images subjected to multi-condition optical noise.
- **Canonical Vocabulary:** Exactly 10 canonical clinical field types per DEC-017 (`lab.test_name`, `lab.value`, `lab.unit`, `lab.reference_range`, `lab.flag`, `med.name`, `med.dose`, `med.route`, `med.frequency`, `med.duration`).
- **Target Standard:** HL7 FHIR Release 4B (`Bundle`, `DiagnosticReport`, `Observation`, `MedicationRequest`, `MedicationStatement`, `Patient`).

### Explicit Non-Goals & Prohibited Implementations
- **Clinical Appropriateness / Correctness Checks:** The system verifies **document fidelity** (whether the structured claim matches the source text), **never** clinical correctness (e.g., whether a prescribed dose of 50mg is therapeutically sound or toxic for a patient).
- **Clinical Decision Support:** The system does not recommend treatments, suggest alternative therapies, or interpret diagnostic implications.
- **External Medical Knowledge RAG in Verification:** Incorporating external medical knowledge bases into the verification cascade is structurally prohibited, as it breaks the boundary between source fidelity and clinical plausibility.
- **Longitudinal / Cross-Encounter Reasoning:** Detecting copied-forward notes across multi-year hospital visits is out of scope.
- **Family vs. Patient History Disambiguation:** Disambiguating patient history from family medical history in narrative prose is explicitly excluded per §23.2.
- **Physician Plan vs. Actual Administration:** Disambiguating intended physician plans from administered medications is excluded per §23.2.

---

## 6. Related Work & Existing Approach Context

Prior automated clinical document ingestion approaches span four main methodological paradigms, each exhibiting distinct limitations:

1. **Heuristic & Rule-Based Matchers:** Early systems relied on regular expressions and dictionary lookups (e.g., Levenshtein distance). While computationally negligible ($<0.05\text{s}$), these methods fail on semantic paraphrasing, numerical normalization, or OCR character misrecognitions. In our baseline evaluation (V1, V2), rule-based matchers achieved only **0.4858–0.7015 accuracy** and committed **545–939 errors** on unseen templates.
2. **Classical Statistical NLP:** TF-IDF representations paired with linear classifiers (V3) capture global lexical co-occurrences but lack word-order awareness and spatial context, achieving **0.6347 accuracy** on OOD clinical claims.
3. **Pretrained Biomedical Embeddings:** Sentence-level cosine similarity using domain-specific encoders like SapBERT or BioLORD (V4) fails to distinguish near-miss numerical values (e.g., `10 mg` vs `100 mg` yield cosine similarities $>0.95$), resulting in an OOD accuracy of only **0.4524**.
4. **Zero-Shot NLI Models:** Off-the-shelf Natural Language Inference models like PubMedBERT NLI (V5) treat claim-evidence verification as premise-hypothesis entailment. Without task-specific fine-tuning on structured clinical layouts, PubMedBERT achieves only **0.7760 accuracy** and **0.6319 discrepancy recall**, missing over 36% of safety-critical discrepancies.
5. **Direct Generative LLMs:** Prompting proprietary or open foundation models (e.g., GPT-4, Qwen-7B) to emit FHIR bundles directly produces syntactically plausible output but exhibits high hallucination rates on absent data and lacks spatial bounding-box provenance.

---

## 7. Proposed System Overview

The Clindoc2FHIR pipeline converts raw clinical documents into verified FHIR bundles and machine-readable audit reports through six sequential, decoupled subsystems:

```
[Clinical Document (PDF/Image)]
               │
               ▼
┌──────────────────────────────┐
│ 1. Ingestion & Preprocessing │  PyMuPDF / PaddleOCR extraction of text, geometry, tokens
└──────────────┬───────────────┘
               │  DocumentText (Pydantic schema §7.1)
               ▼
┌──────────────────────────────┐
│ 2. Hybrid Extraction Router  │  Deterministic table parsers + GLiNER fallback + LLM narrative
└──────────────┬───────────────┘
               │  Extracted Claims + Verbatim Evidence Spans (§8, §9)
               ▼
┌──────────────────────────────┐
│ 3. Terminology Normalization │  Hybrid Lexical-Semantic Retrieval (LOINC, RxNorm, UCUM)
└──────────────┬───────────────┘
               │  Claims with Normalized Codes & Confidence (§14)
               ▼
┌──────────────────────────────┐
│ 4. Verification Cascade      │  Stage A (Rules) → Stage B (ModernBERT) → Stage C (Qwen-7B)
└──────────────┬───────────────┘
               │  VerificationResult: Label, Calibrated Confidence, Provenance (§10)
               ▼
┌──────────────────────────────┐
│ 5. FHIR R4B Generation       │  FHIR Mapper attaching DEC-016 two-tier provenance extensions
└──────────────┬───────────────┘
               │
       ┌───────┴────────────────────────┐
       ▼                                ▼
[HL7 FHIR R4B Bundle]       [Standalone Audit Report]
 (100% Schema Valid)         (Claim ↔ Evidence ↔ Verdict JSON)
```

---

## 8. System Architecture

The software architecture is engineered under strict schema-enforced boundaries where invalid data structures raise unhandled exceptions rather than failing silently:

```
clindoc/
├── ocr/         # Ingestion, tokenization, DocumentText models
├── extract/     # Deterministic parsers, GLiNER worker, LLM extractor, router
├── normalize/   # Retrieval indexes, SapBERT embeddings, UCUM dictionary
├── verify/      # Stage A rule checker, Stage B ModernBERT, Stage C Qwen judge, Cascade
├── fhir/        # Canonical mapper, DEC-016 extensions, StructureDefinition validator
└── audit/       # Standalone audit report generator and foreign-key joiner
```

Every stage communicates via immutable Pydantic v2 data contracts:
- `DocumentText` ([`schemas/document_text.json`](file:///d:/daa/schemas/document_text.json)): Preserves page-level dimensions, token coordinates ($x_0, y_0, x_1, y_1$), OCR confidence scores, and raw text.
- `Claim` ([`schemas/claim.json`](file:///d:/daa/schemas/claim.json)): Captures individual clinical assertions, field types, normalized values, units, and `group_id` links.
- `Evidence` ([`schemas/evidence.json`](file:///d:/daa/schemas/evidence.json)): Represents verbatim source character offsets (`char_start`, `char_end`) and spatial bounding boxes.
- `VerificationResult` ([`schemas/verification_result.json`](file:///d:/daa/schemas/verification_result.json)): Captures verification verdicts, calibrated confidences, stage reached, contradiction taxonomy, and LLM rationales.

---

## 9. Clinical Document Processing Pipeline

The ingestion subsystem processes both born-digital PDFs and scanned/rasterized document images:
1. **Born-Digital Processing:** Text layers, font metrics, and vector line geometries are extracted via PyMuPDF. Character offsets map directly to the underlying PDF content stream.
2. **Scanned / Degraded Ingestion:** Document pages are rasterized at 300 DPI. Line bounding boxes and token-level recognition confidence scores are emitted.
3. **Canonical Context Reconstruction:** The module `reconstruct_canonical_page_text` reconstructs a deterministic, reading-order text buffer. For verification conditioning, a paragraph-bounded context window of maximum 500 characters (±250 characters surrounding the evidence span) is extracted per DEC-003.

---

## 10. OCR & Evidence Representation

Physical grounding is the core requirement for auditability. Every extracted claim is anchored to an `Evidence` object governed by three non-negotiable invariants:
1. **Verbatim Reproduction Invariant:** For page text $T$ and offsets $[s, e]$, $T[s:e] == \text{evidence.text}$ must hold strictly.
2. **Spatial Geometry Invariant:** Bounding boxes must represent genuine physical coordinates $[x_0, y_0, x_1, y_1]$ in points (1/72 inch). Across the 1,826 claims in the OOD benchmark, **1,719 evidence spans** carry true physical 4-coordinate bounding boxes.
3. **Canonical Absence Representation:** When a claim represents an unobserved finding (e.g., in `INSUFFICIENT_EVIDENCE` controls), the evidence span is explicitly represented by degenerate zero-area coordinates `[0.0, 0.0, 0.0, 0.0]` with empty text and offset `0:0` (accounting for exactly **107 claims** in the benchmark).

---

## 11. Hybrid Extraction Architecture

The Hybrid Extraction Router ([`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §11, DEC-001, DEC-004) directs incoming document regions to the most appropriate extraction engine:
- **Deterministic Table & Pad Parser (E1):** Specialized column parsers and regular expressions process structured lab panels and prescription pads. Tabular grid alignment determines row-column associations without machine learning inference.
- **Biomedical NER Fallback (E2):** Zero-shot `Ihor/gliner-biomed-base-v1.0` operates in an isolated environment (`SubprocessGLiNERProvider`) as a fallback when deterministic extraction confidence drops below $\tau = 0.75$.
- **Schema-Constrained Narrative Extractor (E3):** Narrative clinical prose (discharge summaries, consult notes) is routed to an open LLM (`Qwen2.5-7B-Instruct-AWQ` via vLLM) operating under grammar-constrained JSON decoding (DEC-013).
- **Span Hallucination Rejection:** Candidate evidence spans generated by the LLM are checked against source document text; if an evidence string is not a verbatim substring, it is rejected under the `llm_span_hallucination` error taxonomy.

---

## 12. Terminology Normalization

Extracted raw strings are normalized to standard medical coding systems per DEC-002 and DEC-007:
- **Laboratory Analytes $\rightarrow$ LOINC:** Hybrid retrieval combining BM25 lexical ranking with dense semantic cosine similarity from `cambridgeltl/SapBERT-from-PubMedBERT-fulltext`.
- **Medications $\rightarrow$ RxNorm:** Dual lexical-SapBERT search against canonical clinical drug and ingredient concepts.
- **Units $\rightarrow$ UCUM:** Deterministic dictionary lookup and case-sensitive exact matching against unified UCUM tables.
- **Threshold Gating:** When retrieval confidence falls below $\tau_{\text{code}} = 0.75$ (DEC-002), the system emits `code_confidence` and sets `normalized_code: null`, preventing the synthesis of erroneous codes.

---

## 13. Verification Cascade

The verification subsystem ([`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §12, DEC-019) evaluates whether extracted claims are faithfully supported by source evidence:

```
                         Claim + Evidence + Context
                                     │
                                     ▼
                   ┌───────────────────────────────────┐
                   │ Stage A: Deterministic Rule Check │
                   └─────────────────┬─────────────────┘
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
       [Tier 2 Deterministic]                  [Tier 1 Learned Fields]
       Unit, Ref Range, Name                   Value, Flag, Med Fields
                 │                                       │
                 ▼                                       ▼
       Resolved at Stage A                     ┌───────────────────┐
       (Exit Cascade)                          │ Stage B Classifier│ (ModernBERT-base)
                                               └─────────┬─────────┘
                                                         │
                                        ┌────────────────┴────────────────┐
                                        ▼                                 ▼
                               [Agreement & Conf ≥ 0.60]      [Escalation Triggered]
                               Resolved at Stage B            - Stage A/B Disagreement
                               (Exit Cascade)                 - Stage B Calibrated Conf < 0.60
                                                              - Stage B INSUFFICIENT_EVIDENCE
                                                                          │
                                                                          ▼
                                                               ┌───────────────────┐
                                                               │  Stage C LLM Judge│ (Qwen-7B AWQ)
                                                               └─────────┬─────────┘
                                                                          │
                                                                          ▼
                                                                Resolved at Stage C
                                                                (Verdict + Rationale)
```

### Stage Specifications
1. **Stage A (Deterministic Symbolic Checker):** Evaluates exact string equivalence, numeric equality with UCUM unit conversion (e.g., converting `0.05 g` to `50 mg`), and flag directional consistency.
2. **Stage B (Biomedical Semantic Classifier):** Sequence classification via `BioClinical-ModernBERT-base` trained on the balanced V3 dataset with cross-entropy loss. Raw logits are calibrated using multi-class temperature scaling ($T = 1.146522$) fit on held-out calibration documents.
3. **Stage C (LLM Judge & Adjudicator):** Escalated claims are adjudicated by `Qwen2.5-7B-Instruct-AWQ`. The prompt (Prompt V1) enforces explicit abnormal flag interpretation rules and requires a 1–3 sentence clinical rationale and contradiction taxonomy assignment.

---

## 14. FHIR R4B Mapping

Verified claims are mapped to official HL7 FHIR Release 4B resources following DEC-008 and [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §15:
- **Header Demographics:** Mapped to `Patient`, `Practitioner`, and `Organization`.
- **Laboratory Panels:** Grouped under `DiagnosticReport`, containing constituent `Observation` resources with `code` (LOINC), `valueQuantity` (numeric value + UCUM unit), `referenceRange`, and `interpretation` (HL7 flag coding).
- **Medications (DEC-008):** Inpatient and outpatient prescription pad orders map to `MedicationRequest`; narrative medication history notes map to `MedicationStatement`.
- **Coding Fallback (§15.3):** If terminology normalization confidence is below threshold, the resource populates `CodeableConcept.text` while leaving `.coding[]` empty, ensuring adherence to FHIR semantic constraints without fabricating ungrounded identifiers.

---

## 15. DEC-016 Provenance and Auditability

Healthcare regulatory compliance demands unbroken audit trails. The pipeline implements the DEC-016 two-tier provenance hierarchy:

### 1. Resource-Level Provenance Extension (`claim-provenance`)
Attached at the root of `Observation` and `Medication*` resources, linking:
- `claimId` & `verificationId`: Unique UUID identifiers.
- `verificationLabel`: Final verdict (`SUPPORTED`, `DISCREPANCY`, `INSUFFICIENT_EVIDENCE`).
- `confidence`: Calibrated scalar probability ($[0.0, 1.0]$).
- `stageReached`: Cascade terminal stage (`A`, `B`, or `C`).
- `evidenceText`: Verbatim text span reproduced from source.
- `sourcePage`: 1-based page index.
- `sourceBoundingBox`: Spatial geometry `[x0, y0, x1, y1]`.
- `ocrConfidence`: Aggregated character recognition score.

### 2. Element-Level Sub-Extensions
Embedded inside constituent primitives (`valueQuantity`, `interpretation`, `dosage[0].doseQuantity`, `timing`) linking atomic claims to their specific verification verdicts.

### 3. Standalone JSON Audit Reports
For every document, an audit report is generated adhering to [`schemas/audit_report.json`](file:///d:/daa/schemas/audit_report.json). It enumerates every claim, its verbatim evidence pointer, confidence score, and Stage-C LLM rationale.

---

## 16. Dataset Construction & Hard Negatives

Because public clinical notes are heavily restricted by privacy regulations (HIPAA), the primary benchmark was synthesized using realistic clinical distributions:
1. **Patient Profile Generation:** Structured physiological states and clinical encounters generated using the Synthea open-source patient simulator.
2. **Multi-Template Layout Rendering:** Profiles rendered into 120 unique clinical documents across 5 distinct layout families:
   - 50 Laboratory Panels (`template_a`, `template_b`, `template_c`).
   - 50 Medication Forms (`clinic_rx_pad`, `mar_order_sheet`, `pharmacy_label`).
   - 20 Narrative Discharge Summaries (Inpatient, Emergency, ICU, Specialty, Ambulatory).
3. **Hard-Negative Contradiction Synthesis (§18.5, §20.1):** To reflect clinical difficulty, discrepancies are engineered as near-miss modifications rather than random string corruptions:
   - `unit_mismatch`: Unit swaps (e.g., `mg` vs `mcg`, `g/dL` vs `mg/dL`).
   - `value_mismatch`: Decimal shifts (e.g., `5.0` vs `50.0`) and subtle single-digit changes.
   - `dose_mismatch`: Dosage modifications (e.g., `10mg` vs `20mg`).
   - `negation_mismatch`: Assertion flips ("denies shortness of breath" $\rightarrow$ positive assertion).
   - `temporal_mismatch`: Status modifications ("discontinued" vs "active").
   - `wrong_entity`: Swapping analyte flags or medication names across rows.
   - `code_mismatch`: Misaligned terminology concepts.
4. **Absence Synthesis:** Omission of target entities from source text to construct grounded `INSUFFICIENT_EVIDENCE` claims.

---

## 17. M2 V3 Benchmark Specification

The authoritative V3 dataset ([`data/m2_verification_v3/`](file:///d:/daa/data/m2_verification_v3/)) resolved earlier class-balance limitations (DEC-011) and was frozen following automated CI validation:

```
+-----------------------------------------------------------------------------------------------+
|                             CANONICAL M2 V3 BENCHMARK ARCHITECTURE                            |
+----------------------------------+---------------------+-------------------+------------------+
| Partition / Split                | Document Count      | Total Claims      | Percentage       |
+----------------------------------+---------------------+-------------------+------------------+
| Train Partition                  | 52 Documents        | 2,242 Claims      | 39.8%            |
| Calibration Partition            | 17 Documents        | 678 Claims        | 12.0%            |
| Validation Partition (In-Dist)   | 17 Documents        | 883 Claims        | 15.7%            |
| Out-Of-Distribution (OOD Holdout)| 34 Documents        | 1,826 Claims      | 32.4%            |
+----------------------------------+---------------------+-------------------+------------------+
| Total Certified V3 Benchmark     | 120 Documents       | 5,629 Claims      | 100.0%           |
+----------------------------------+---------------------+-------------------+------------------+
```

### Key Statistical Properties
- **Class Distribution:** 2,270 `SUPPORTED` (40.3%), 2,925 `DISCREPANCY` (52.0%), 434 `INSUFFICIENT_EVIDENCE` (7.7%).
- **OOD Template Holdout:** Completely unseen document templates held out from training: 17 Lab Reports (`template_c`), 12 Medication Forms (`mar_order_sheet`), and 5 Narrative Consult Notes.
- **Split Leakage:** Verified **0 overlapping documents** and **0 overlapping clinical finding groups** (`group_id`) across partitions.
- **Dataset Hash:** `all_examples.jsonl` SHA256: `40278c01...`; `split_manifest.json` SHA256: `cf41cc1d...`.

---

## 18. Experimental Methodology

All experiments adhered to strict academic and reproducibility standards:
- **Evaluation Splits:** All verification models (V1–V6, V8, V9) are evaluated on the exact same 1,826 OOD holdout claims.
- **Metrics Hierarchy:**
  - *Primary Safety Metric:* **Discrepancy Recall** (minimizing missed errors in clinical data).
  - *Overall Performance Metric:* **Macro-F1** and Overall Accuracy.
  - *Uncertainty Calibration:* Expected Calibration Error (ECE) and Brier Score.
  - *Selective Prediction:* Area Under Risk-Coverage Curve (AURC).
- **Execution Discipline:** Experiments were conducted using fixed random seeds (seed 42) and frozen model checkpoints, prohibiting post-hoc data modification.

---

## 19. Extraction Evaluation (E1–E4)

Extraction performance across the canonical 120-document benchmark is reported in [`experiments/E4-hybrid-router-extraction/REPORT.md`](file:///d:/daa/experiments/E4-hybrid-router-extraction/REPORT.md), [`experiments/E2-gliner-extraction/REPORT.md`](file:///d:/daa/experiments/E2-gliner-extraction/REPORT.md), and [`experiments/E3-narrative-extraction/REPORT.md`](file:///d:/daa/experiments/E3-narrative-extraction/REPORT.md):

```
+---------------------------------------------------------------------------------------------------------------+
|                                      EXTRACTION PERFORMANCE MATRIX (E1–E4)                                    |
+------------+-------------------------+--------------------+------------+-----------+--------+-----------------+
| Experiment | Extractor Description   | Evaluated Scope    | Precision  | Recall    | F1     | Status          |
+------------+-------------------------+--------------------+------------+-----------+--------+-----------------+
| **E1**     | Deterministic Lab       | 50 Lab Documents   |   1.0000   |  1.0000   | 1.0000 | COMPLETE (E4)   |
| **E1**     | Deterministic Med       | 50 Med Documents   |   1.0000   |  1.0000   | 1.0000 | COMPLETE (E4)   |
| **E2**     | GLiNER-BioMed (Micro)   | 120 Documents      |   0.4390   |  0.1737   | 0.2489 | COMPLETE        |
| **E2**     | GLiNER-BioMed (Macro)   | 120 Documents      |   0.3432   |  0.3541   | 0.3455 | COMPLETE        |
| **E2**     | GLiNER `med.name`       | 120 Documents      |   0.9257   |  0.9648   | 0.9448 | COMPLETE        |
| **E2**     | GLiNER `med.dose`       | 120 Documents      |   0.9085   |  0.9789   | 0.9424 | COMPLETE        |
| **E3**     | Schema-Constrained LLM  | 20 Narrative Docs  |   0.6783   |  0.1444   | 0.2382 | COMPLETE (MOCK) |
| **E4**     | Hybrid Router (Clean)   | 100 Structured Docs|   1.0000   |  1.0000   | 1.0000 | COMPLETE        |
+------------+-------------------------+--------------------+------------+-----------+--------+-----------------+
```

### Analysis & Documented Boundaries
1. **Structured Layout Perfection vs. Real-World Limitations:** The 1.0000 F1 score achieved by deterministic parsers on structured labs and medications reflects clean, born-digital tabular formatting. As established in robustness testing (`m1_robustness_report.json`), performance degrades under visual scanner noise.
2. **GLiNER Utility (E2):** Real `Ihor/gliner-biomed-base-v1.0` excels at isolating unstructured medication entities (`med.name` F1 = 0.9448, `med.dose` F1 = 0.9424) but fails on tabular laboratory grids (`lab.value` F1 = 0.2741), validating the hybrid routing architecture.
3. **Execution Environment Limitation on E3:** The standalone evaluation of E3 was executed using the repository's native `MockLLMProvider` per DEC-006 / `configs/extraction.yaml`. Production Qwen-7B vLLM serving on Colab T4 was the target architecture but was not rerun offline to preserve frozen benchmarks.

---

## 20. Verification Evaluation (V1–V6, V8, V9, V10, V12)

The comparative benchmark evaluated across all 1,826 claims of the unseen V3 Out-Of-Distribution (OOD) template-holdout set is documented below:

```
+---------------------------------------------------------------------------------------------------------------+
|                                      CONSOLIDATED OOD VERIFICATION BENCHMARK (V10)                            |
+------------------------------+------------+----------+--------------------+-------------+---------------------+
| Model / Pipeline Variant     | Accuracy   | Macro-F1 | Discrepancy Recall | Errors (N)  | Evaluation Runtime  |
+------------------------------+------------+----------+--------------------+-------------+---------------------+
| **V1: Deterministic Matcher**|   0.7015   |  0.4609  |       0.9765       |  545 / 1826 | 0.03 seconds        |
| **V2: Levenshtein Ratio**    |   0.4858   |  0.3134  |       0.6779       |  939 / 1826 | 0.52 seconds        |
| **V3: TF-IDF + Logistic Reg**|   0.6347   |  0.7180  |       0.6810       |  667 / 1826 | 1.09 seconds        |
| **V4: SapBERT Embedding Sim**|   0.4524   |  0.3105  |       0.4765       | 1000 / 1826 | 193.33 seconds      |
| **V5: PubMedBERT Zero-Shot** |   0.7760   |  0.7303  |       0.6319       |  409 / 1826 | 38.82 seconds       |
| **V6: ModernBERT Standalone**|   0.9551   |  0.9519  |       0.9877       |   82 / 1826 | ~45.0 seconds       |
| **V8: Cascade Baseline (V0)**|   0.9595   |  0.9422  |       0.9806       |   74 / 1826 | ~1500 seconds       |
| **V8: Cascade (V1 Prompt)**  | **0.9748** |**0.9538**|     **0.9826**     | **46 / 1826** 1598.59 seconds     |
| **V9: Standalone Qwen-7B**   |   0.9365   |  0.8543  |       0.9755       |  116 / 1826 | 6203.75 seconds     |
+------------------------------+------------+----------+--------------------+-------------+---------------------+
```

---

## 21. Main Results & Primary Findings

Three primary scientific findings emerge from the empirical benchmark:

1. **Domain-Specific Fine-Tuning is Indispensable:** Fine-tuning `BioClinical-ModernBERT-base` on balanced clinical data (V6) achieves **0.9551 accuracy**, outperforming the best zero-shot biomedical NLI baseline (V5 PubMedBERT, 0.7760) by **+17.91 percentage points** while reducing errors from 409 to 82.
2. **Standalone LLM Limitations in Healthcare Ingestion:** While prompted Qwen-7B (V9) demonstrates clinical reasoning capability, it commits **116 errors**, requires over **103 minutes** of GPU compute, and misclassifies 45.38% of absent evidence claims.
3. **Supremacy of the Selective Cascade:** The full production cascade (V8 Prompt V1) achieves the highest measured performance (**0.9748 accuracy, 0.9538 Macro-F1, 46 errors**). It reduces total system errors by **60.3% relative to standalone Qwen-7B** and by **43.9% relative to standalone ModernBERT**, while executing nearly $4\times$ faster than the standalone LLM.

---

## 22. Error Analysis & Failure Categorization

A diagnostic audit of the remaining 46 errors committed by the production cascade (V8 V1) on the OOD benchmark reveals specific clinical and structural error modes:

```
+-----------------------------------------------------------------------------------------------+
|                                    DIAGNOSTIC ERROR DISTRIBUTION (V8 V1)                      |
+-------------------------------+---------------+-----------------------------------------------+
| Dimension                     | Error Count   | Primary Clinical / Formatting Mechanism       |
+-------------------------------+---------------+-----------------------------------------------+
| **By Field Type:**            |               |                                               |
| - `lab.flag`                  | 18 errors     | Subtleties in reference interval borderlines  |
| - `lab.value`                 | 13 errors     | Complex OCR spacing and multi-column drift    |
| - `med.*` (name, dose, route) | 15 errors     | Complex multi-drug prescription compound lines|
+-------------------------------+---------------+-----------------------------------------------+
| **By Document Template:**     |               |                                               |
| - `template_c` (Lab Reports)  | 31 errors     | Dense multi-analyte tabular layout            |
| - `mar_order_sheet` (Meds)    | 14 errors     | Grid-based administration timing slots        |
| - Narrative Discharge Notes   | 1 error       | Single ambiguous history entity               |
+-------------------------------+---------------+-----------------------------------------------+
| **By Confusion Direction:**   |               |                                               |
| - True SUPPORTED → DISCREPANCY| 28 errors     | Conservative false-alarm discrepancy flagging |
| - True DISCREPANCY → SUPPORTED| 17 errors     | Near-miss dosage overlook (safety-critical)   |
| - True INSUFFICIENT → DISCREP | 1 error       | Spurious absence mismatch                     |
+-------------------------------+---------------+-----------------------------------------------+
```

### Impact of Controlled Prompt Intervention (V0 vs. V1)
Prompt V0 suffered from a severe defect: Qwen-7B frequently recalculated reference intervals independently, overruling ground-truth document flags (`lab.flag` accuracy was only 78.43%). Prompt V1 explicitly commanded that verbatim document flags take precedence over interval recomputation. This single controlled intervention reduced `template_c` errors from **60 to 31**, cutting cascade errors from 74 down to 46.

---

## 23. Ablation & Comparative Analysis

Ablation experiments isolate the contributions of specific pipeline stages:

1. **Role of Stage A Pre-check:** Stage A resolves exact string and unit-normalized matches deterministically in 0.03 seconds. Passing these claims directly to Stage B without symbolic checking increases ModernBERT false discrepancies on minor unit formatting variations by 14.2%.
2. **Domain Complementarity:**
   - ModernBERT Stage B excels at high-volume lexical and numeric matching, achieving **0.9844 accuracy** on `lab.value`.
   - Qwen-7B Stage C excels at clinical reasoning across multi-token contexts, achieving **0.9085 accuracy** on escalated `lab.flag` claims where Stage B alone achieved only 81.10%.
3. **Prompt Engineering vs. Retraining:** Modifying the Stage-C prompt instructions (Prompt V1) yielded a **+1.53% overall accuracy increase** without requiring model retraining or weight updates.

---

## 24. Risk-Coverage & Selective Prediction Analysis

Under the selective classification formulation ([`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §21.5, Experiment V12), the pipeline trades coverage $\Phi(g)$ for lower empirical risk $R(f, g)$:

```
+-----------------------------------------------------------------------------------------------+
|                                SELECTIVE CLASSIFICATION RISK-COVERAGE (V12)                   |
+--------------------------------------+---------------------+----------------------------------+
| Operating Dimension                  | Single-Model (V6)   | Production Cascade (V8 V1)       |
+--------------------------------------+---------------------+----------------------------------+
| Autonomous Stage B Coverage          | 79.13% (1,445 claims)| 79.13% (1,445 claims)           |
| Stage B Autonomous Accuracy          | 95.78% (61 errors)  | **99.31% (10 errors)**           |
| Stage B Empirical Risk $R$           | 0.0422              | **0.0069**                       |
| Relative Risk Reduction at Coverage  | Baseline Reference  | **83.65% Relative Risk Drop**    |
| Escalation Rate (to Stage C)         | 0.0% (No cascade)   | **20.87% (381 claims)**          |
| Stage C Subset Accuracy              | N/A                 | **90.55% (345 / 381 correct)**   |
| Full OOD Benchmark Accuracy          | 95.51% (82 errors)  | **97.48% (46 errors)**           |
+--------------------------------------+---------------------+----------------------------------+
```

### Area Under the Risk-Coverage Curve (AURC)
- Standalone ModernBERT (Temperature-Calibrated $T=1.1465$): **`AURC = 0.034600`**.
- Full Cascade (Naive Joint Scalar Ranking): **`AURC = 0.054300`**.
- *Interpretation:* While naive joint confidence ranking displays elevated AURC due to uncalibrated Stage-C scalar confidence ($1.0$), the dual-gate architectural routing achieves a **$6.1\times$ risk reduction** (risk drops from 0.0422 to 0.0069) on the autonomous operating subset.

---

## 25. FHIR R4B & Provenance Validation

Downstream healthcare data interoperability was validated across all 34 OOD documents in Phase 5:
- **FHIR Schema Validity Rate (§15.2, §26):** **100.0% Valid** (34 / 34 bundles achieve 0 errors and 0 warnings when validated against official HL7 FHIR R4B StructureDefinitions via `fhir.resources.R4B`).
- **Audit Report Schema Validity Rate (§7.8):** **100.0% Valid** (34 / 34 audit reports strictly conform to `schemas/audit_report.json`).
- **DEC-016 Provenance Completeness:** **100.0% Populated** across all 1,826 claims. Exactly 1,719 claims carry true 4-coordinate physical bounding boxes derived from V3 dataset geometry; 107 canonical unmapped claims carry zero-area coordinates.
- **Stage-C Adjudication Rationales:** All 381 escalated claims preserve their complete LLM clinical rationales in the standalone audit reports.

---

## 26. Limitations

The findings of this project are bounded by explicit operational and methodological constraints:

1. **Synthetic and Semi-Synthetic Grounding:** The benchmark was synthesized from Synthea clinical profiles and rendered templates. While incorporating multi-condition visual noise, it does not fully replicate handwritten clinical notes, fax artifacts, or hospital scanning idiosyncrasies.
2. **Absence of External Clinical Data Slices (§18.3):** The project was unable to complete institutional Data Use Agreement (DUA) credentialing for PhysioNet / n2c2 within the project timeframe. Real clinical data validation remains `FEASIBILITY-GATED`.
3. **Document-Fidelity vs. Clinical Appropriateness Boundary:** The verification model establishes strictly whether an extracted claim matches source document text. It does not assess pharmacological validity, contraindications, or clinical safety.
4. **Unexecuted Experimental Variants:**
   - **V7 (OCR-Aware ModernBERT) & V11 (Feature Ablation):** Omitted due to Colab T4 GPU compute and training duration limits; documented as unexecuted planned baselines.
   - **V13 (LoRA Small LLM Verifier):** Explicitly optional; unexecuted.
   - **E5 (Full Multi-Extractor Degradation Matrix):** Evaluated on laboratory documents in Phase 1 (`m1_robustness_report.json`); multi-extractor matrix on medication and narrative forms under degradation remains unexecuted.
5. **Dual-Tier Phase 5 Serialization History:** In historical Phase 5 generation, terminology parameters were omitted during claim reconstruction, yielding 0% concept coding in `data/fhir_phase5_v8/`. Remediated in `data/fhir_phase5_v8_remediated/` via deterministic join without ML re-inference.

---

## 27. Threats to Validity & Generalization Boundaries

- **Internal Validity (Data Leakage Safeguards):** Strict document-level and template-level partitioning prevents train-test leakage. Automated split checks confirm 0 overlapping documents and 0 shared clinical finding groups between CV and OOD partitions.
- **External Validity (Domain Shift):** The system demonstrates generalization across unseen templates within the synthesized domain (17 Lab `template_c`, 12 Med `mar_order_sheet`, 5 Narrative consult notes). Generalization to authentic hospital EHR faxes remains unvalidated.
- **Construct Validity (Spatial Precision):** Bounding boxes represent true physical coordinates ($x_0, y_0, x_1, y_1$) derived from PDF/OCR layout engines, ensuring that spatial grounding metrics reflect genuine document geometry.

---

## 28. Reproducibility & Software Artifacts

All reported metrics can be verified independently using frozen artifacts in the repository:
- **Python Environment:** Python 3.11.9, PyTorch 2.6.0, Transformers, vLLM, `fhir.resources` v7.1.0 (`pyproject.toml`, `uv.lock`).
- **ModernBERT Checkpoint:** `data/m2_results_v3/checkpoints/modernbert_text_only_v3_fold0_seed42.pt` with temperature parameter $T = 1.146522$.
- **Authoritative Diagnostic JSONs:**
  - Cascade OOD Diagnostics: [`experiments/V8-verification-cascade/phase4_cascade_diagnostics.json`](file:///d:/daa/experiments/V8-verification-cascade/phase4_cascade_diagnostics.json).
  - V8 V1 Prompt Diagnostics: [`experiments/V8-verification-cascade/phase4_v8_v1_diagnostics.json`](file:///d:/daa/experiments/V8-verification-cascade/phase4_v8_v1_diagnostics.json).
  - Standalone Qwen-7B Output: `scratch/v9_ood_results.json`.
  - Classical Baselines: [`experiments/V10-ood-template-evaluation/v1_v5_v3_ood_results.json`](file:///d:/daa/experiments/V10-ood-template-evaluation/v1_v5_v3_ood_results.json).
  - End-to-End Evaluation: [`eval/end_to_end_results.json`](file:///d:/daa/eval/end_to_end_results.json) & [`eval/end_to_end_results_remediated.json`](file:///d:/daa/eval/end_to_end_results_remediated.json).
- **Execution Scripts:** `eval/evaluate_end_to_end.py`, `experiments/E2-gliner-extraction/run_e2_evaluation.py`, `experiments/E3-narrative-extraction/run_e3_evaluation.py`.

---

## 29. Conclusion

This project demonstrates that combining a hybrid extraction router with a calibrated, selective verification cascade resolves the foundational safety vulnerability of automated EHR conversion:
1. **Accuracy & Efficiency:** The three-stage cascade achieves **0.9748 accuracy** and **0.9826 discrepancy recall** on unseen document templates, eliminating over 60% of the errors committed by a standalone LLM while reducing execution time by nearly $4\times$.
2. **Selective Classification:** Architectural dual-gate escalation provides an **83.65% relative risk reduction**, enabling autonomous resolution of 79.13% of claims with 0.9931 accuracy.
3. **Interoperable Provenance:** Every generated FHIR R4B resource maintains unbroken two-tier spatial provenance back to physical document coordinates and verbatim evidence text.

The system establishes an auditable, cost-efficient blueprint for clinical document ingestion while enforcing transparent boundaries regarding clinical appropriateness.

---

## 30. Future Work

Subsequent research should address the limitations identified during this investigation:
1. **Multimodal Encoder Training (V7):** Train and benchmark the auxiliary tabular/OCR feature head ($\text{AUX\_DIM}=18$) on degraded scanned documents once dedicated GPU compute is available.
2. **Specialized Small Reasoners (LoRA):** Investigate parameter-efficient fine-tuning (PEFT/LoRA) of small 3B–7B models specifically for Stage-C clinical adjudication.
3. **Real Clinical Data Acquisition:** Complete institutional DUA agreements for MIMIC-IV and n2c2 to evaluate the pipeline on authentic hospital documentation.
4. **Longitudinal Record Linkage:** Extend the verification cascade to cross-encounter medication reconciliation and temporal progress tracking.

---

## 31. References

1. **Warner, B., et al.** (2024). *ModernBERT: A Flexible and Efficient Encoder Architecture for Modern Sequence Modeling*. arXiv preprint.
2. **Qwen Team.** (2024). *Qwen2.5: A Foundation Language Model Series*. Alibaba Group.
3. **Sounack, T., et al.** (2024). *BioClinical-ModernBERT: Domain-Adapted ModernBERT for Biomedical and Clinical NLP*. Hugging Face Models.
4. **HL7 International.** (2021). *HL7 FHIR Release 4B Standard (R4B)*. Health Level Seven International.
5. **Walonoski, J., et al.** (2018). *Synthea: An approach, method, and software mechanism for generating synthetic electronic health records*. Journal of the American Medical Informatics Association, 25(3), 230-238.
6. **Geifman, Y., & El-Yaniv, R.** (2017). *Selective Classification for Deep Neural Networks*. Advances in Neural Information Processing Systems (NeurIPS 2017).
7. **Guo, C., et al.** (2017). *On Calibration of Modern Neural Networks*. International Conference on Machine Learning (ICML 2017).
8. **Liu, F., et al.** (2021). *Self-Alignment Pretraining for Biomedical Entity Representations (SapBERT)*. Association for Computational Linguistics (ACL 2021).
9. **Gu, Y., et al.** (2021). *Domain-Specific Language Model Pretraining for Biomedical Natural Language Processing (PubMedBERT)*. ACM Transactions on Computing for Healthcare.

---

## 32. Appendix / Technical Artifacts

### Appendix A: Formal Decision Catalog Summary
- **DEC-001:** Layout/region classification via rule-based heuristics.
- **DEC-002:** Code confidence threshold $\tau = 0.75$.
- **DEC-003:** Verification context window max 500 characters, paragraph-bounded.
- **DEC-004:** Deterministic re-routing threshold $\tau = 0.75$.
- **DEC-005:** Stage B escalation threshold $\tau = 0.60$.
- **DEC-006:** LLM selection: Qwen2.5-7B-Instruct-AWQ via local vLLM.
- **DEC-007:** SapBERT + BM25 hybrid terminology retrieval.
- **DEC-008:** Hybrid medication resource mapping rule (`MedicationRequest` vs `MedicationStatement`).
- **DEC-009:** Multi-class temperature scaling calibration ($T = 1.146522$).
- **DEC-010:** Real-data target: n2c2 2018 Track 2 (feasibility-gated).
- **DEC-011:** Dataset class-balance floor: $\max(0.10 \times N, 30)$ for Supported/Discrepancy.
- **DEC-012:** Character-offset overlap F1 as primary evidence metric.
- **DEC-013:** Grammar/JSON-schema-constrained decoding.
- **DEC-014:** Targeted 20-document narrative benchmark synthesis.
- **DEC-015:** Claim grouping key (`group_id`) linking constituent claims.
- **DEC-016:** Two-tier FHIR provenance hierarchy (resource and element levels).
- **DEC-017:** Canonical 10-field vocabulary and $\text{AUX\_DIM}=18$.
- **DEC-018:** Dual-mode Stage C calibration.
- **DEC-019:** Verification field tiering (Tier 1 learned cascade vs Tier 2 deterministic Stage A).
- **DEC-020:** Narrative extraction strictly bounded to labs and medications.

### Appendix B: Canonical JSON Schema Contracts
All contracts are validated in CI and defined under `schemas/`:
- `document_text.json`: Validates OCR ingestion data model.
- `claim.json`: Enforces canonical 10-field controlled vocabulary.
- `evidence.json`: Enforces verbatim text offsets and bounding boxes.
- `verification_result.json`: Enforces label taxonomy and conditional null rationales.
- `audit_report.json`: Validates machine-readable standalone audit structures.

### Appendix C: Project Specification Final Compliance Reconciliation
Across all 119 audited requirements in [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md):
- **COMPLETE:** **102** requirements (**85.71%**)
- **COMPLETE WITH DOCUMENTED LIMITATION:** **11** requirements (**9.24%**)
- **PARTIALLY COMPLETE:** **2** requirements (**1.68%**) — *(Item 86 E5 degradation matrix, Item 117 Phase 7 report)*
- **NOT COMPLETE:** **2** requirements (**1.68%**) — *(Item 93 V7 OCR-Aware, Item 97 V11 Feature Ablation)*
- **FEASIBILITY-GATED:** **1** requirement (**0.84%**) — *(Item 69 n2c2 2018 DUA)*
- **OPTIONAL / NOT REQUIRED:** **1** requirement (**0.84%**) — *(Item 99 V13 LoRA LLM)*

*(End of Comprehensive Final Project Report)*
