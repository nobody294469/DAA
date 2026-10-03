# Experiment V3: TF-IDF + Logistic Regression Baseline

## 1. Experiment Overview
- **Experiment ID:** V3 (`tfidf_logreg`)
- **Specification Authority:** [`PROJECT_SPEC.md §22.2`](../../PROJECT_SPEC.md) (V3 baseline)
- **Execution Status:** COMPLETED (persisted results in repository)
- **Source Data Artifact:** [`data/m2_results/m2_baseline_results.json`](../../data/m2_results/m2_baseline_results.json)
- **Implementation Module:** [`train_tfidf_lr`](file:///d:/daa/src/clindoc/m2/baselines.py#L122-L133), [`predict_tfidf_lr`](file:///d:/daa/src/clindoc/m2/baselines.py#L136-L139), [`predict_tfidf_lr_proba`](file:///d:/daa/src/clindoc/m2/baselines.py#L141-L145) in `src/clindoc/m2/baselines.py`

## 2. Objective
Establish the performance benchmark of a classical supervised text classification model. This experiment evaluates how effectively linear decision boundaries over bag-of-words and character/word n-gram representations can classify claim-evidence pairs into supported claims, discrepancies, and insufficient evidence, and assesses the calibration of linear model probabilities.

## 3. Exact Method and Model Details
1. **Input Pair Representation:**
   - Concatenates the claim string and evidence snippet with a separation delimiter:
     `X = claim + " [SEP] " + evidence`
2. **Feature Extraction Pipeline:**
   - `TfidfVectorizer(ngram_range=(1, 2), max_features=10000)`: Extracts unigram and bigram token counts weighted by inverse document frequency.
3. **Classification Model:**
   - `LogisticRegression(max_iter=1000, C=1.0, random_state=seed, solver="lbfgs")`: Fits a multinomial logistic regression model with L2 regularization over the 10,000 TF-IDF features.
4. **Output Distribution:**
   - Generates calibrated multi-class posterior probabilities via softmax:
     `P(y = c | X) = exp(w_c^T X + b_c) / sum_j exp(w_j^T X + b_j)`
   - Predicted class is selected via argmax: `y_hat = argmax_c P(y = c | X)`.

## 4. Dataset, Split, and Evaluation Protocol
- **Cross-Validation Split:** 5-fold cross-validation using document-level `GroupKFold` on the canonical 86-document pool (52 train, 17 calibration, 17 validation). Total test examples evaluated across all 5 folds: **6,126 examples** (Fold 0: 984, Fold 1: 1,039, Fold 2: 1,372, Fold 3: 863, Fold 4: 1,868).
- **Out-of-Distribution (OOD) Split:** 34 held-out documents representing completely unseen visual templates (17 lab, 12 medication, 5 narrative documents). Total OOD test examples: **3,042 examples** (1,441 SUPPORTED, 1,441 DISCREPANCY, 160 INSUFFICIENT_EVIDENCE).
- **Data Isolation:** Zero document, group, or patient leakage across folds. Fitted strictly on training folds.
- **Canonical Label Ordering:** `SUPPORTED` (0), `DISCREPANCY` (1), `INSUFFICIENT_EVIDENCE` (2).

## 5. Cross-Validation Results (5-Fold CV)

### Summary Metrics
- **CV Macro F1:** **0.7091 ± 0.0241** (Sample standard deviation with ddof=1)
- **CV Discrepancy Recall:** **0.7886 ± 0.0376**

### Per-Fold Breakdown
| Fold | Test Examples ($N$) | Macro F1 | Discrepancy Recall | AUROC (Discrepancy) | Brier Score | ECE |
|---|---|---|---|---|---|---|
| Fold 0 | 984 | 0.6884 | 0.8192 | 0.7442 | 0.4466 | 0.0608 |
| Fold 1 | 1039 | 0.7167 | 0.7547 | 0.7565 | 0.4352 | 0.0843 |
| Fold 2 | 1372 | 0.7014 | 0.8176 | 0.7541 | 0.4355 | 0.0734 |
| Fold 3 | 863 | 0.7474 | 0.7411 | 0.7603 | 0.4223 | 0.0930 |
| Fold 4 | 1868 | 0.6916 | 0.8106 | 0.7466 | 0.4401 | 0.0731 |
| **Mean ± Std** | **6126 total** | **0.7091 ± 0.0241** | **0.7886 ± 0.0376** | **0.7523 ± 0.0068** | **0.4359 ± 0.0089** | **0.0769 ± 0.0122** |

### Per-Class CV Metrics (Mean ± Std across 5 Folds)
| Class Label | Precision | Recall | F1-Score | Evaluated Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.6941 ± 0.0140 | 0.5285 ± 0.0595 | 0.5982 ± 0.0344 | 2,853 |
| **DISCREPANCY** | 0.6184 ± 0.0210 | 0.7886 ± 0.0376 | 0.6924 ± 0.0047 | 2,853 |
| **INSUFFICIENT_EVIDENCE** | 0.9876 ± 0.0170 | 0.7276 ± 0.0664 | 0.8367 ± 0.0453 | 420 |

### CV Confusion Matrix (Summed over 5 Folds)
```
                          Predicted SUPPORTED   Predicted DISCREPANCY   Predicted INSUFFICIENT
True SUPPORTED                           1485                    1366                     2
True DISCREPANCY                         584                     2267                     2
True INSUFFICIENT_EVIDENCE               65                      51                       304
```

## 6. Out-of-Distribution (OOD) Performance
- **OOD Macro F1:** **0.6115**
- **OOD Discrepancy Recall:** **0.5781**

### Per-Class OOD Metrics (Total N = 3,042)
| Class Label | Precision | Recall | F1-Score | Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.5862 | 0.6489 | 0.6159 | 1441 |
| **DISCREPANCY** | 0.6063 | 0.5781 | 0.5918 | 1441 |
| **INSUFFICIENT_EVIDENCE** | 1.0000 | 0.4562 | 0.6266 | 160 |

### OOD Confusion Matrix
```
                          Predicted SUPPORTED   Predicted DISCREPANCY   Predicted INSUFFICIENT
True SUPPORTED                           935                     506                      0
True DISCREPANCY                         608                     833                      0
True INSUFFICIENT_EVIDENCE               52                      35                       73
```

## 7. Calibration Metrics
- **AUROC (Discrepancy vs Rest):**
  - **CV Mean ± Std:** **0.7523 ± 0.0068**
  - **OOD:** **0.6955**
- **Brier Score (Multi-Class Mean Squared Probability Error):**
  - **CV Mean ± Std:** **0.4359 ± 0.0089**
  - **OOD:** **0.4663**
- **Expected Calibration Error (ECE, 10 Bins):**
  - **CV Mean ± Std:** **0.0769 ± 0.0122**
  - **OOD:** **0.0269**

## 8. Subgroup and Slice Metrics

### Degradation Severity Breakdown (Macro F1)
| Severity Slice | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `clean` | 0.8068 ± 0.0195 | 0.7010 | 1475 |
| `low` | 0.3871 ± 0.0065 | 0.3806 | 540 |
| `medium` | 0.3775 ± 0.0254 | 0.3686 | 526 |
| `high` | 0.4408 ± 0.0700 | 0.3573 | 501 |

### Claim Domain Breakdown (Macro F1)
| Claim Type | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `lab` | 0.6422 ± 0.0330 | 0.4358 | 2482 |
| `med` | 0.7688 ± 0.0208 | 0.7389 | 560 |

### Field Type Breakdown (Macro F1)
| Field Type | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `lab.value` | 0.5974 ± 0.0342 | 0.3984 | 1966 |
| `lab.flag` | 0.8419 ± 0.0216 | 0.8078 | 516 |
| `med.name` | 0.8144 ± 0.0414 | 0.8009 | 101 |
| `med.dose` | 0.6797 ± 0.0333 | 0.6613 | 257 |
| `med.frequency` | 0.8665 ± 0.0435 | 0.8130 | 101 |
| `med.route` | 0.7937 ± 0.0475 | 0.7455 | 101 |

## 9. Threshold and Calibration Procedure
- **Decision Rule:** Multi-class maximum a posteriori (MAP) prediction over multinomial logistic regression probabilities.
- **Probabilities:** Computed directly via softmax transformation over fitted linear logits (w^T X + b).
- **Post-Hoc Calibration:** No post-hoc temperature scaling or Platt scaling was applied in the baseline run. Raw logistic regression probabilities were evaluated directly.

## 10. Concise Interpretation of What the Experiment Measures
1. **Strong Lexical Association on CV:** TF-IDF with Logistic Regression achieves the highest cross-validation Macro F1 (0.7091) among the lexical baselines. By fitting n-gram co-occurrence weights directly between claim and evidence, the model learns strong patterns for specific terms and numbers.
2. **Effective Identification of Missing Evidence:** In contrast to the heuristic and edit-distance baselines, the supervised linear classifier reliably detects `INSUFFICIENT_EVIDENCE` (0.8367 F1 in CV, 304 of 420 correctly identified). Missing evidentiary n-grams reliably guide the logistic regression weights toward the insufficient class.
3. **Severe Degradation on OOD Templates:** When evaluated on unseen visual templates (OOD), Macro F1 drops sharply from 0.7091 to 0.6115, and Discrepancy Recall collapses from 0.7886 to 0.5781. This indicates that the linear model overfits specific token formatting and document vocabulary present in the 86 CV training templates.
4. **Vulnerability to OCR Noise:** While clean text achieves high performance (0.8068 Macro F1), degradation conditions reduce Macro F1 by more than half (0.3871 on low degradation; 0.3775 on medium). Character substitutions and OCR errors disrupt exact n-gram matching.
