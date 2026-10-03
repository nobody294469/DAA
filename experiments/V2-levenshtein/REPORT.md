# Experiment V2: Levenshtein Distance Threshold Baseline

## 1. Experiment Overview
- **Experiment ID:** V2 (`levenshtein_threshold`)
- **Specification Authority:** [`PROJECT_SPEC.md §22.2`](../../PROJECT_SPEC.md) (V2 baseline)
- **Execution Status:** COMPLETED (persisted results in repository)
- **Source Data Artifact:** [`data/m2_results/m2_baseline_results.json`](../../data/m2_results/m2_baseline_results.json)
- **Implementation Module:** [`train_levenshtein`](file:///d:/daa/src/clindoc/m2/baselines.py#L80-L102), [`predict_levenshtein`](file:///d:/daa/src/clindoc/m2/baselines.py#L104-L117) in `src/clindoc/m2/baselines.py`

## 2. Objective
Evaluate character-level edit-distance similarity (Levenshtein ratio) as a lightweight verification mechanism. This experiment tests whether string edit distance between extracted claim strings and source evidence snippets can discriminate supported facts from contradictions and insufficient evidence when the decision threshold is tuned on training data.

## 3. Exact Method and Model Details
1. **String Similarity Metric:**
   - Computes normalized character Levenshtein ratio:
     `sim(claim, evidence) = ratio(claim.lower(), evidence.lower())`
   - Implementation uses the C-optimized `Levenshtein.ratio` function.
2. **Evidence and OCR Guard:**
   - If `ocr_confidence < 0.50` or `len(evidence.strip()) < 3`, predicts `INSUFFICIENT_EVIDENCE`.
3. **Threshold Decision Rule:**
   - If `sim >= threshold`, predicts `SUPPORTED`.
   - Otherwise, predicts `DISCREPANCY`.
4. **Threshold Selection Protocol:**
   - On each training split, evaluates candidate thresholds from `0.05` to `0.95` with step size `0.05`.
   - Selects the threshold that maximizes Macro F1 on the training partition.
   - Evaluates the selected threshold on the unseen test fold and OOD split.

## 4. Dataset, Split, and Evaluation Protocol
- **Cross-Validation Split:** 5-fold cross-validation using document-level `GroupKFold` on the canonical 86-document pool (52 train, 17 calibration, 17 validation). Total test examples evaluated across all 5 folds: **6,126 examples** (Fold 0: 984, Fold 1: 1,039, Fold 2: 1,372, Fold 3: 863, Fold 4: 1,868).
- **Out-of-Distribution (OOD) Split:** 34 held-out documents representing completely unseen visual templates (17 lab, 12 medication, 5 narrative documents). Total OOD test examples: **3,042 examples** (1,441 SUPPORTED, 1,441 DISCREPANCY, 160 INSUFFICIENT_EVIDENCE).
- **Data Isolation:** Zero document, group, or patient leakage across folds. Thresholds selected strictly on training partitions.
- **Canonical Label Ordering:** `SUPPORTED` (0), `DISCREPANCY` (1), `INSUFFICIENT_EVIDENCE` (2).

## 5. Cross-Validation Results (5-Fold CV)

### Summary Metrics
- **CV Macro F1:** **0.3066 ± 0.0051** (Sample standard deviation with ddof=1)
- **CV Discrepancy Recall:** **0.6382 ± 0.0161**

### Per-Fold Breakdown
| Fold | Test Examples ($N$) | Macro F1 | Discrepancy Recall |
|---|---|---|---|
| Fold 0 | 984 | 0.3067 | 0.6317 |
| Fold 1 | 1039 | 0.3056 | 0.6184 |
| Fold 2 | 1372 | 0.3129 | 0.6584 |
| Fold 3 | 863 | 0.2989 | 0.6320 |
| Fold 4 | 1868 | 0.3090 | 0.6505 |
| **Mean ± Std** | **6126 total** | **0.3066 ± 0.0051** | **0.6382 ± 0.0161** |

### Per-Class CV Metrics (Mean ± Std across 5 Folds)
| Class Label | Precision | Recall | F1-Score | Evaluated Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.4214 ± 0.0248 | 0.3405 ± 0.0132 | 0.3762 ± 0.0106 | 2,853 |
| **DISCREPANCY** | 0.4735 ± 0.0043 | 0.6382 ± 0.0161 | 0.5436 ± 0.0085 | 2,853 |
| **INSUFFICIENT_EVIDENCE** | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 420 |

### CV Confusion Matrix (Summed over 5 Folds)
```
                          Predicted SUPPORTED   Predicted DISCREPANCY   Predicted INSUFFICIENT
True SUPPORTED                           966                     1887                     0
True DISCREPANCY                         1023                    1830                     0
True INSUFFICIENT_EVIDENCE               278                     142                      0
```

## 6. Out-of-Distribution (OOD) Performance
- **OOD Macro F1:** **0.3189**
- **OOD Discrepancy Recall:** **0.6537**

### Per-Class OOD Metrics (Total N = 3,042)
| Class Label | Precision | Recall | F1-Score | Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.4614 | 0.3525 | 0.3997 | 1441 |
| **DISCREPANCY** | 0.4853 | 0.6537 | 0.5571 | 1441 |
| **INSUFFICIENT_EVIDENCE** | 0.0000 | 0.0000 | 0.0000 | 160 |

### OOD Confusion Matrix
```
                          Predicted SUPPORTED   Predicted DISCREPANCY   Predicted INSUFFICIENT
True SUPPORTED                           508                     933                      0
True DISCREPANCY                         499                     942                      0
True INSUFFICIENT_EVIDENCE               94                      66                       0
```

## 7. Calibration Metrics
- **AUROC (Discrepancy vs Rest):** *Unavailable*
- **Brier Score:** *Unavailable*
- **Expected Calibration Error (ECE):** *Unavailable*

*(Technical Note: V2 is a thresholded edit-distance baseline producing hard categorical decisions. Because it does not estimate class probabilities, AUROC, Brier score, and ECE are unavailable.)*

## 8. Subgroup and Slice Metrics

### Degradation Severity Breakdown (Macro F1)
| Severity Slice | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `clean` | 0.2969 ± 0.0105 | 0.3177 | 1475 |
| `low` | 0.3190 ± 0.0065 | 0.3174 | 540 |
| `medium` | 0.3169 ± 0.0068 | 0.3241 | 526 |
| `high` | 0.3132 ± 0.0052 | 0.3161 | 501 |

### Claim Domain Breakdown (Macro F1)
| Claim Type | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `lab` | 0.3178 ± 0.0024 | 0.3230 | 2482 |
| `med` | 0.2713 ± 0.0079 | 0.3007 | 560 |

### Field Type Breakdown (Macro F1)
| Field Type | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `lab.value` | 0.3154 ± 0.0036 | 0.3180 | 1966 |
| `lab.flag` | 0.4895 ± 0.0096 | 0.5123 | 516 |
| `med.name` | 0.2653 ± 0.0291 | 0.2965 | 101 |
| `med.dose` | 0.3057 ± 0.0071 | 0.3087 | 257 |
| `med.frequency` | 0.1523 ± 0.0173 | 0.1679 | 101 |
| `med.route` | 0.1849 ± 0.0208 | 0.2807 | 101 |

## 9. Threshold and Calibration Procedure
- **Threshold Search Space:** Evaluated 19 uniform threshold values: `[0.05, 0.10, ..., 0.95]`.
- **Optimization Criterion:** Maximized Macro F1 on each training split.
- **Evidence Guard:** Examples failing OCR confidence (< 0.50) or evidence length (< 3 characters) were routed to `INSUFFICIENT_EVIDENCE`.
- **Calibration Status:** Uncalibrated decision threshold metric; no posterior probabilities.

## 10. Concise Interpretation of What the Experiment Measures
1. **Insensitivity to Semantic and Numerical Nuance:** Character edit-distance correlates poorly with factual verification. In clinical records, a single character edit (e.g. changing "5 mg" to "50 mg", or altering a decimal point in "0.9" vs "9.0") represents a critical clinical discrepancy, yet produces a high Levenshtein similarity ratio (>= 0.85). Conversely, valid paraphrasing or including surrounding bounding-box tokens drastically reduces character ratio while remaining factually supported.
2. **Degenerate Threshold Selection:** Because Macro F1 across 3 classes penalizes poor performance uniformly, and Levenshtein distance cannot separate `INSUFFICIENT_EVIDENCE` from text-based pairs without low OCR confidence, the training sweep selects low thresholds that fail to separate `SUPPORTED` and `DISCREPANCY` (resulting in CV precision of only 0.4214 on supported claims and 0.4735 on discrepancies).
3. **Severe Deficit on Insufficient Evidence:** Across all 6,126 CV evaluations and 3,042 OOD evaluations, the Levenshtein baseline failed to identify a single `INSUFFICIENT_EVIDENCE` case correctly (F1 = 0.0000), misclassifying them as supported or discrepancies based entirely on character overlap with spurious context.
