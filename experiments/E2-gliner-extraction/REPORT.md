# Experiment E2: Standalone GLiNER-BioMed Extraction Evaluation

**Experiment ID:** `E2-gliner-extraction`  
**Authority:** [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) (§1.3, §11, §21.1, §22.1, §25 Phase 2, §26)  
**Architectural Decisions:** DEC-001 (Layout Routing), DEC-004 (Deterministic Confidence Threshold), DEC-017 (Canonical 10-Field Vocabulary), DEC-019 (Tier 1 vs. Tier 2 Field Scoping)  
**Report Date:** 2026-10-03  
**Status:** COMPLETE  
**Execution Environment:** Isolated `.venv_ner` environment (GLiNER 0.2.29, PyTorch 2.6.0+cu124) via `SubprocessGLiNERProvider`  
**Git Commit:** `a6a13d12001ea35342b20526b5ae8882bb977566`  

---

## 1. Executive Summary & Objective

The objective of **Experiment E2** is to evaluate the standalone performance of **GLiNER-BioMed** (`Ihor/gliner-biomed-base-v1.0`), a zero-shot bidirectional encoder-based biomedical named entity recognition (NER) model, across the canonical 120-document clinical benchmark.

In the production pipeline architecture ([`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §11, DEC-001, DEC-004), GLiNER-BioMed functions as a **Tier 1 fallback extractor** when deterministic column/regex confidence falls below threshold. Prior to this evaluation, GLiNER was reported only indirectly as an integrated component of the Hybrid Router (Experiment E4). This report establishes an **independent, standalone offline batch evaluation artifact** for E2, directly executing GLiNER across all 120 benchmark documents without reliance on deterministic parsers or LLMs.

### Key Headline Findings
1. **Strong Named Medication Entity Extraction:** GLiNER achieves high performance on unstructured and semi-structured medication entities across all document types:
   - `med.name`: **Precision 0.9257 | Recall 0.9648 | F1 0.9448** (85/87 matches on medication forms; 52/55 on narrative discharge notes).
   - `med.dose`: **Precision 0.9085 | Recall 0.9789 | F1 0.9424** (85/87 matches on medication forms; 54/55 on narrative notes; 100% on OOD holdout).
2. **Empirical Justification for Hybrid Routing (DEC-001 / DEC-019):**
   - GLiNER alone achieves poor performance on structured tabular laboratory panels (`lab.value` F1 = 0.2741 on lab panels; `lab.flag` F1 = 0.0112). Without spatial grid awareness, numeric values in tables are frequently misassociated or flooded with spurious candidates (922 predicted values for 705 true observations).
   - By architectural design (DEC-019), Tier 2 fields (`lab.test_name`, `lab.unit`, `lab.reference_range`, `med.duration`) require deterministic parsers and are strictly excluded from GLiNER emission, yielding F1 = 0.0 across all 4 Tier 2 fields.
3. **Aggregate Benchmark Metrics:** Across all 120 documents (4,456 total ground truth claims):
   - **Micro Precision: 0.4390 | Micro Recall: 0.1737 | Micro F1: 0.2489 | Macro F1: 0.3455**
   - High precision and recall on targeted Tier 1 medication fields (F1 0.942–0.945) contrasted with 0.0 on non-targeted Tier 2 fields.

---

## 2. Dataset Scope and Partitioning

The evaluation was executed across all 120 canonical clinical documents with zero sample omissions:

| Document Family | Document Count | Total GT Claims | Primary Structural Layout | Ground Truth Source | OCR Cache Source |
| :--- | :---: | :---: | :--- | :--- | :--- |
| **Laboratory Reports** | 50 | 3,525 | Structured tabular panels (`template_a`, `template_b`, `template_c`) | `data/ground_truth/LAB_*.json` | `data/ocr_cache/LAB_*.json` |
| **Medication Forms** | 50 | 391 | Prescription pads, MAR sheets, pharmacy labels | `data/med_ground_truth/MED_*.json` | `data/med_ocr_cache/MED_*.json` |
| **Narrative Notes** | 20 | 540 | Free-text HPI, clinical course, discharge summaries | `data/narrative_ground_truth/NARRATIVE_*.json` | `data/narrative_ocr_cache/NARRATIVE_*.json` |
| **Total Benchmark** | **120** | **4,456** | Heterogeneous clinical corpora | Canonical Ground Truth Store | Canonical OCR Cache |

### Split Partitioning (§19)
The benchmark is partitioned according to [`data/m2_verification_v3/split_manifest.json`](file:///d:/daa/data/m2_verification_v3/split_manifest.json):
- **Cross-Validation Split (CV):** 86 documents (33 Lab, 38 Med, 15 Narrative) — 2,973 GT claims.
- **Out-of-Distribution Split (OOD):** 34 documents (17 Lab `template_c`, 12 Med `mar_order_sheet`, 5 Narrative specialty/ambulatory notes) — 1,483 GT claims.

---

## 3. Evaluation Protocol & Methodology

1. **Standalone Model Execution:**
   - Ingestion: OCR text pages loaded via `clindoc.ocr.document_text.DocumentText`.
   - Execution: Ingested page texts processed through `clindoc.extract.gliner_fallback.SubprocessGLiNERProvider` into the isolated `.venv_ner` environment running `Ihor/gliner-biomed-base-v1.0`.
   - Batching: Inference executed in 2 chunks of 60 pages each to avoid memory leaks while minimizing subprocess startup overhead. Total inference duration: **115.74s** (**0.965s/page** on CPU).
2. **Canonical Claim & Evidence Construction:**
   - Predicted entity spans mapped to canonical fields via `GLiNERFallbackExtractor._build_claims`.
   - Evidence character spans grounded verbatim against page text.
   - Bounding boxes retrieved from OCR geometry.
   - Extraction method set to `ExtractionMethod.GLINER`.
3. **Scoring Protocol:**
   - Evaluated using canonical bipartite matching engine [`src/clindoc/extract/evaluate.py`](file:///d:/daa/src/clindoc/extract/evaluate.py).
   - Strict numeric equivalence for `lab.value` and `med.dose` ($|v_{\text{pred}} - v_{\text{gt}}| < 10^{-4}$).
   - Normalization applied to flags (`_FLAG_MAP`) and routes (`_ROUTE_VOCAB`).
   - True Positives (TP), False Positives (FP), and False Negatives (FN) computed independently per canonical field.

---

## 4. Empirical Evaluation Results

### 4.1 Overall 10-Field Extraction Performance (120 Documents)

Per DEC-017, all 10 canonical field types are reported without silent omission:

```
+-----------------------------------------------------------------------------------------------------------------------+
|                                    EXPERIMENT E2: STANDALONE GLiNER-BioMed EXTRACTION                                 |
+----------------------+------------+------------+-----------+--------+-------------+-------+-------+-------+-----------+
| Field Type           | Tier / Scp | Precision  | Recall    | F1     | Exact Match | GT    | Pred  | TP    | Status    |
+----------------------+------------+------------+-----------+--------+-------------+-------+-------+-------+-----------+
| med.name             | Tier 1 NER |   0.9257   |  0.9648   | 0.9448 |   0.9648    |   142 |   148 |   137 | Evaluated |
| med.dose             | Tier 1 NER |   0.9085   |  0.9789   | 0.9424 |   0.9789    |   142 |   153 |   139 | Evaluated |
| med.frequency        | Tier 1 NER |   0.5324   |  0.8099   | 0.6425 |   0.8099    |   142 |   216 |   115 | Evaluated |
| med.route            | Tier 1 NER |   0.5238   |  0.6972   | 0.5982 |   0.6972    |   142 |   189 |    99 | Evaluated |
| lab.value            | Tier 1 NER |   0.2672   |  0.3608   | 0.3070 |   0.3608    |   765 |  1033 |   276 | Evaluated |
| lab.flag             | Tier 1 NER |   0.3333   |  0.0105   | 0.0203 |   0.0105    |   765 |    24 |     8 | Evaluated |
| med.duration         | Tier 2 Det |   0.0000   |  0.0000   | 0.0000 |   0.0000    |    63 |     0 |     0 | DEC-019 Ex|
| lab.test_name        | Tier 2 Det |   0.0000   |  0.0000   | 0.0000 |   0.0000    |   765 |     0 |     0 | DEC-019 Ex|
| lab.unit             | Tier 2 Det |   0.0000   |  0.0000   | 0.0000 |   0.0000    |   765 |     0 |     0 | DEC-019 Ex|
| lab.reference_range  | Tier 2 Det |   0.0000   |  0.0000   | 0.0000 |   0.0000    |   765 |     0 |     0 | DEC-019 Ex|
+----------------------+------------+------------+-----------+--------+-------------+-------+-------+-------+-----------+
| OVERALL BENCHMARK    | Aggregate  |   0.4390   |  0.1737   | 0.2489 |   0.1737    |  4456 |  1763 |   774 | COMPLETE  |
| MACRO AVERAGE (10)   | Mean-Field |     —      |     —     | 0.3455 |      —      |   —   |   —   |   —   | COMPLETE  |
+----------------------+------------+------------+-----------+--------+-------------+-------+-------+-------+-----------+
```

---

### 4.2 Subsystem Performance Breakdown

#### A. Structured Medication Forms (50 Documents, 391 GT Claims)
GLiNER excels on semi-structured prescription documents:
- **Total Predicted Claims:** 527
- **Micro Metrics:** Precision = **0.6091**, Recall = **0.8210**, F1 = **0.6993**
- **Macro F1:** **0.4487**
- `med.name`: **P = 0.9659 | R = 0.9770 | F1 = 0.9714** (85/87 matched)
- `med.dose`: **P = 0.8947 | R = 0.9770 | F1 = 0.9341** (85/87 matched)
- `med.frequency`: **P = 0.4557 | R = 0.8276 | F1 = 0.5878** (72/87 matched)
- `med.route`: **P = 0.5032 | R = 0.9080 | F1 = 0.6475** (79/87 matched)
- `med.duration`: P = 0.0000 | R = 0.0000 | F1 = 0.0000 (0/43 matched; Tier 2 deterministic)
- *Spurious detections:* 12 `lab.flag`, 17 `lab.value` false positives caused by numeric sig codes.

#### B. Free-Text Narrative Discharge Summaries (20 Documents, 540 GT Claims)
On unstructured clinical prose, GLiNER successfully identifies inline medication regimens and narrative lab values:
- **Total Predicted Claims:** 303
- **Micro Metrics:** Precision = **0.7459**, Recall = **0.4185**, F1 = **0.5362**
- **Macro F1:** **0.3919**
- `med.name`: **P = 0.9123 | R = 0.9455 | F1 = 0.9286** (52/55 matched)
- `med.dose`: **P = 0.9310 | R = 0.9818 | F1 = 0.9558** (54/55 matched)
- `med.frequency`: **P = 0.7414 | R = 0.7818 | F1 = 0.7611** (43/55 matched)
- `med.route`: **P = 0.6250 | R = 0.3636 | F1 = 0.4598** (20/55 matched)
- `lab.value`: **P = 0.5638 | R = 0.8833 | F1 = 0.6883** (53/60 matched)
- `lab.flag`: **P = 1.0000 | R = 0.0667 | F1 = 0.1250** (4/60 matched)
- Tier 2 fields: 0 matches (excluded by design)

#### C. Structured Laboratory Reports (50 Documents, 3,525 GT Claims)
On dense multi-column tabular lab panels, standalone GLiNER fails to resolve column-aligned values:
- **Total Predicted Claims:** 933
- **Micro Metrics:** Precision = **0.2433**, Recall = **0.0644**, F1 = **0.1018**
- **Macro F1:** **0.0476**
- `lab.value`: **P = 0.2419 | R = 0.3163 | F1 = 0.2741** (223/705 matched; 699 false positives from reference ranges and dates misidentified as values)
- `lab.flag`: **P = 0.5000 | R = 0.0057 | F1 = 0.0112** (4/705 matched)
- `lab.test_name`, `lab.unit`, `lab.reference_range`: **F1 = 0.0000** (0/705 matched)

---

### 4.3 Partition Breakdown: Cross-Validation vs. Out-of-Distribution

```
+------------------------------------+--------------------------+--------------------------+
| Metric                             | Cross-Validation (86)    | Out-of-Distribution (34) |
+------------------------------------+--------------------------+--------------------------+
| Total Ground Truth Claims          | 2,973                    | 1,483                    |
| Total Predicted Claims             | 1,210                    | 553                      |
| Micro Precision                    | 0.4628                   | 0.3870                   |
| Micro Recall                       | 0.1884                   | 0.1443                   |
| Micro F1                           | 0.2678                   | 0.2102                   |
| Macro F1                           | 0.3468                   | 0.3440                   |
| Overall Exact Match Rate           | 0.1884                   | 0.1443                   |
+------------------------------------+--------------------------+--------------------------+
| med.name F1                        | 0.9453 (TP=95/100)       | 0.9438 (TP=42/42, R=1.0) |
| med.dose F1                        | 0.9194 (TP=97/100)       | 1.0000 (TP=42/42, P/R=1) |
| med.frequency F1                   | 0.6393 (TP=78/100)       | 0.6491 (TP=37/42)        |
| med.route F1                       | 0.5789 (TP=66/100)       | 0.6408 (TP=33/42)        |
| lab.value F1                       | 0.3550 (TP=216/507)      | 0.2065 (TP=60/258)       |
| lab.flag F1                        | 0.0306 (TP=8/507)        | 0.0000 (TP=0/258)        |
+------------------------------------+--------------------------+--------------------------+
```

---

## 5. Architectural Diagnostics & Error Analysis

### 5.1 Why Medication Entities Excel
`Ihor/gliner-biomed-base-v1.0` was pre-trained on diverse biomedical corpora (PubMed, BioCreative, NCBI-Disease) where medication names and dosages appear in linear syntactic phrases (e.g., *"Metformin 500 mg orally twice daily"*). Consequently, zero-shot entity recognition on medication names and dosages achieves near-perfect recall (96.5% and 97.9%) and precision (>90%).

### 5.2 Why Tabular Laboratory Extraction Fails Under Standalone NER
In dense tabular lab panels, tokens are arranged in two-dimensional spatial columns (`Test Name | Result | Units | Flag | Reference Range`). Linear language models lack 2D positional encodings. Without coordinate-guided column segmenters (such as those in `DeterministicExtractor`):
1. **Header numbers and reference range bounds** are falsely predicted as `lab.value` (yielding 757 false positives).
2. **Single-letter flags** (`H`, `L`) separated from results by whitespace are missed by tokenizer chunking.
3. This empirical failure validates **DEC-001** and **DEC-004**, establishing why deterministic table parsers (E1) are necessary for laboratory panels.

### 5.3 Diagnostic Label Counts
- **Unsupported labels filtered:** 738 occurrences (e.g. non-Tier-1 biomedical mentions filtered to maintain DEC-017 boundary compliance).
- **Rejected labels encountered:** 0 occurrences of explicitly forbidden clinical labels.

---

## 6. Reproducibility & Verification Command

To independently reproduce this evaluation:

```bash
# Execute standalone E2 evaluation across all 120 benchmark documents
.venv\Scripts\python.exe experiments/E2-gliner-extraction/run_e2_evaluation.py --batch-size 60

# Execute unit and regression test suite
.venv\Scripts\pytest.exe tests/test_e2_evaluation.py
```

### Generated Artifacts
1. **Evaluation Entry Point:** [`experiments/E2-gliner-extraction/run_e2_evaluation.py`](file:///d:/daa/experiments/E2-gliner-extraction/run_e2_evaluation.py)
2. **Machine-Readable Results:** [`experiments/E2-gliner-extraction/e2_results.json`](file:///d:/daa/experiments/E2-gliner-extraction/e2_results.json)
3. **Formal Experiment Report:** [`experiments/E2-gliner-extraction/REPORT.md`](file:///d:/daa/experiments/E2-gliner-extraction/REPORT.md)
4. **Test Suite:** [`tests/test_e2_evaluation.py`](file:///d:/daa/tests/test_e2_evaluation.py)

---

## 7. Status & Project Spec Compliance Verdict

Per [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) §22.1 and [`PROJECT_SPEC_REQUIREMENTS_MATRIX.md`](file:///d:/daa/PROJECT_SPEC_REQUIREMENTS_MATRIX.md) Requirement 83:

- **Previous Status:** `PARTIALLY COMPLETE` (Evaluated only as a fallback sub-component in E4; standalone offline batch per-field metrics across 120 documents unpersisted).
- **Current Status:** **`COMPLETE`**
  - Standalone evaluation script implemented and executed independently.
  - Full per-field precision, recall, and F1 computed across all 10 canonical fields.
  - Zero fabricated numbers; all metrics derived from real `Ihor/gliner-biomed-base-v1.0` inference over all 120 benchmark documents.
  - Full machine-readable results persisted to `experiments/E2-gliner-extraction/e2_results.json`.
  - Comprehensive documentation of architectural boundaries (Tier 1 vs. Tier 2 per DEC-019).
