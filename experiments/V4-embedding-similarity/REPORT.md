# Experiment V4: Embedding Similarity Baseline

## 1. Experiment Overview
- **Experiment ID:** V4 (`embedding_similarity`)
- **Specification Authority:** [`PROJECT_SPEC.md §22.2`](../../PROJECT_SPEC.md) (V4 baseline), DEC-007
- **Execution Status:** COMPLETED (persisted results in repository)
- **Source Data Artifact:** [`data/m2_results/m2_baseline_results.json`](../../data/m2_results/m2_baseline_results.json)
- **Implementation Module:** [`train_embedding_similarity`](file:///d:/daa/src/clindoc/m2/baselines.py#L168-L215), [`predict_embedding_similarity`](file:///d:/daa/src/clindoc/m2/baselines.py#L217-L250) in `src/clindoc/m2/baselines.py`

## 2. Objective
Evaluate whether domain-specific semantic similarity derived from pretrained biomedical sentence embeddings (SapBERT / BioLORD) can verify clinical claims without task-specific fine-tuning (zero training). This experiment isolates the discriminative capacity of dense bi-encoder cosine representations for factual verification.

## 3. Exact Method and Model Details
1. **Pretrained Biomedical Embedder:**
   - Embedder: `cambridgeltl/SapBERT-from-PubMedBERT-fulltext` (with deterministic fallback).
   - "Zero training": All encoder weights are completely frozen; no gradient updates or fine-tuning on M2 data.
2. **Dense Vector Encoding and Similarity:**
   - Encodes claim string `e_c = embed(c)` and evidence snippet `e_v = embed(v)` into dense d-dimensional vectors.
   - Computes cosine similarity:
     `sim(c, v) = (e_c · e_v) / (||e_c||_2 * ||e_v||_2)`
3. **Evidence and OCR Guard:**
   - If `ocr_confidence < 0.50` or `len(evidence.strip()) < 3`, maps immediately to `INSUFFICIENT_EVIDENCE`.
4. **Threshold Selection Protocol:**
   - Cosine threshold tuned on training folds over grid `[0.05, 0.10, ..., 0.95]` to maximize training Macro F1.
   - If `sim(c, v) >= threshold`, predicts `SUPPORTED`; otherwise, predicts `DISCREPANCY`.

## 4. Dataset, Split, and Evaluation Protocol
- **Cross-Validation Split:** 5-fold cross-validation using document-level `GroupKFold` on the canonical 86-document pool (52 train, 17 calibration, 17 validation). Total test examples evaluated across all 5 folds: **6,126 examples** (Fold 0: 984, Fold 1: 1,039, Fold 2: 1,372, Fold 3: 863, Fold 4: 1,868).
- **Out-of-Distribution (OOD) Split:** 34 held-out documents representing completely unseen visual templates (17 lab, 12 medication, 5 narrative documents). Total OOD test examples: **3,042 examples** (1,441 SUPPORTED, 1,441 DISCREPANCY, 160 INSUFFICIENT_EVIDENCE).
- **Data Isolation:** Zero document, group, or patient leakage across folds. Thresholds selected strictly on training partitions.
- **Canonical Label Ordering:** `SUPPORTED` (0), `DISCREPANCY` (1), `INSUFFICIENT_EVIDENCE` (2).

## 5. Cross-Validation Results (5-Fold CV)

### Summary Metrics
- **CV Macro F1:** **0.3211 ± 0.0049** (Sample standard deviation with ddof=1)
- **CV Discrepancy Recall:** **0.5038 ± 0.0208**

### Per-Fold Breakdown
| Fold | Test Examples ($N$) | Macro F1 | Discrepancy Recall |
|---|---|---|---|
| Fold 0 | 984 | 0.3181 | 0.4777 |
| Fold 1 | 1039 | 0.3249 | 0.4948 |
| Fold 2 | 1372 | 0.3277 | 0.5131 |
| Fold 3 | 863 | 0.3161 | 0.5000 |
| Fold 4 | 1868 | 0.3188 | 0.5333 |
| **Mean ± Std** | **6126 total** | **0.3211 ± 0.0049** | **0.5038 ± 0.0208** |

### Per-Class CV Metrics (Mean ± Std across 5 Folds)
| Class Label | Precision | Recall | F1-Score | Evaluated Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.4923 ± 0.0081 | 0.4972 ± 0.0297 | 0.4945 ± 0.0179 | 2,853 |
| **DISCREPANCY** | 0.4386 ± 0.0164 | 0.5038 ± 0.0208 | 0.4689 ± 0.0176 | 2,853 |
| **INSUFFICIENT_EVIDENCE** | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 420 |

### CV Confusion Matrix (Summed over 5 Folds)
```
                          Predicted SUPPORTED   Predicted DISCREPANCY   Predicted INSUFFICIENT
True SUPPORTED                           1399                    1454                     0
True DISCREPANCY                         1401                    1452                     0
True INSUFFICIENT_EVIDENCE               43                      377                      0
```

## 6. Out-of-Distribution (OOD) Performance
- **OOD Macro F1:** **0.3109**
- **OOD Discrepancy Recall:** **0.5649**

### Per-Class OOD Metrics (Total N = 3,042)
| Class Label | Precision | Recall | F1-Score | Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.4714 | 0.4004 | 0.4330 | 1441 |
| **DISCREPANCY** | 0.4477 | 0.5649 | 0.4995 | 1441 |
| **INSUFFICIENT_EVIDENCE** | 0.0000 | 0.0000 | 0.0000 | 160 |

### OOD Confusion Matrix
```
                          Predicted SUPPORTED   Predicted DISCREPANCY   Predicted INSUFFICIENT
True SUPPORTED                           577                     864                      0
True DISCREPANCY                         627                     814                      0
True INSUFFICIENT_EVIDENCE               20                      140                      0
```

## 7. Calibration Metrics
- **AUROC (Discrepancy vs Rest):** *Unavailable*
- **Brier Score:** *Unavailable*
- **Expected Calibration Error (ECE):** *Unavailable*

*(Technical Note: V4 is a cosine similarity thresholding baseline. It does not output a calibrated posterior probability distribution over classes; AUROC, Brier score, and ECE are unavailable.)*

## 8. Subgroup and Slice Metrics

### Degradation Severity Breakdown (Macro F1)
| Severity Slice | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `clean` | 0.3257 ± 0.0058 | 0.3186 | 1475 |
| `low` | 0.3148 ± 0.0115 | 0.3112 | 540 |
| `medium` | 0.3181 ± 0.0166 | 0.3037 | 526 |
| `high` | 0.2908 ± 0.0134 | 0.2820 | 501 |

### Claim Domain Breakdown (Macro F1)
| Claim Type | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `lab` | 0.3166 ± 0.0044 | 0.3123 | 2482 |
| `med` | 0.3385 ± 0.0150 | 0.3043 | 560 |

### Field Type Breakdown (Macro F1)
| Field Type | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `lab.value` | 0.3042 ± 0.0055 | 0.3029 | 1966 |
| `lab.flag` | 0.5310 ± 0.0064 | 0.5155 | 516 |
| `med.name` | 0.4075 ± 0.0572 | 0.3040 | 101 |
| `med.dose` | 0.3549 ± 0.0242 | 0.2871 | 257 |
| `med.frequency` | 0.2369 ± 0.0309 | 0.2986 | 101 |
| `med.route` | 0.2721 ± 0.0233 | 0.2456 | 101 |

## 9. Threshold and Calibration Procedure
- **Threshold Search Space:** Evaluated candidate cosine thresholds in `[0.05, 0.95]`.
- **Optimization Criterion:** Threshold was selected on each training fold to maximize training Macro F1.
- **Evidence Guard:** Examples failing OCR confidence (< 0.50) or evidence length (< 3 characters) were routed to `INSUFFICIENT_EVIDENCE`.
- **Calibration Status:** Uncalibrated cosine distance metric; no posterior probabilities.

## 10. Concise Interpretation of What the Experiment Measures
1. **Semantic Affinity vs. Factual Verification:** Pretrained biomedical embeddings capture medical domain and conceptual relatedness rather than factual truth. A claim stating "Hemoglobin: 14.2 g/dL" and an evidence snippet stating "Hemoglobin: 9.8 g/dL" yield near-identical embedding representations because both encode the clinical concept of hemoglobin measurement. Bi-encoder cosine similarity cannot detect fine-grained numeric contradictions.
2. **Chance-Level Binary Discrimination:** Because contradictory claim-evidence pairs share nearly the same semantic concepts as supported pairs, cosine similarity produces overlapping distributions for both classes. As shown in the CV confusion matrix, the model predicts supported (1399 vs 1401) and discrepancy (1454 vs 1452) at roughly equal rates regardless of actual label, yielding near 50% precision and recall.
3. **Necessity of Joint Cross-Encoding:** Experiment V4 proves that bi-encoder cosine similarity alone cannot solve clinical claim verification. Full cross-attention between claim tokens and evidence tokens (as in cross-encoder NLI and ModernBERT architectures) is structurally required to detect negation, modifier shifts, and numerical divergence.
