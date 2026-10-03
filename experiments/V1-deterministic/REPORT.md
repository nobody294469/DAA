# Experiment V1: Deterministic Rule-Based Verifier Baseline

## 1. Experiment Overview
- **Experiment ID:** V1 (`deterministic_verifier`)
- **Specification Authority:** [`PROJECT_SPEC.md §22.2`](../../PROJECT_SPEC.md) (V1 baseline)
- **Execution Status:** COMPLETED (persisted results in repository)
- **Source Data Artifact:** [`data/m2_results/m2_baseline_results.json`](../../data/m2_results/m2_baseline_results.json)
- **Implementation Module:** [`predict_deterministic`](file:///d:/daa/src/clindoc/m2/baselines.py#L35-L78) in `src/clindoc/m2/baselines.py`

## 2. Objective
Establish the baseline performance of a non-parametric, rule-based verification procedure (Stage A heuristic alone). This experiment measures how effectively exact lexical and numerical matching between an extracted claim and source evidence text can verify clinical claims, detect discrepancies, and handle missing evidence without statistical learning, embeddings, or neural inference.

## 3. Exact Method and Model Details
The deterministic baseline operates as a rule-based heuristic wrapper:
1. **Evidence and OCR Guard:**
   - Evaluates OCR quality and evidence snippet length.
   - If `ocr_confidence < 0.50` or `len(evidence.strip()) < 3`, the example is mapped immediately to `INSUFFICIENT_EVIDENCE`.
2. **Numeric and Token Extraction:**
   - Extracts all numbers (including floating-point values) and word tokens of length >= 2 from the claim string.
   - Performs case-insensitive containment checks against the evidence text snippet.
3. **Discrepancy and Overlap Logic:**
   - **Critical Token / Value Absence:** If any numerical value or critical token present in the claim is missing from the evidence snippet, the claim is assigned `DISCREPANCY`.
   - **Lexical Overlap Ratio:** Computes token overlap ratio:
     `overlap = |claim_tokens ∩ evidence_tokens| / |claim_tokens|`
   - If `overlap >= 0.80`, the claim is classified as `SUPPORTED`; otherwise, it is assigned `DISCREPANCY`.
4. **Parameters & Model Weights:** None (deterministic rule-based algorithm; 0 trainable parameters).

## 4. Dataset, Split, and Evaluation Protocol
- **Cross-Validation Split:** 5-fold cross-validation using document-level `GroupKFold` on the canonical 86-document pool (52 train, 17 calibration, 17 validation). Total test examples evaluated across all 5 folds: **6,126 examples** (Fold 0: 984, Fold 1: 1,039, Fold 2: 1,372, Fold 3: 863, Fold 4: 1,868).
- **Out-of-Distribution (OOD) Split:** 34 held-out documents representing completely unseen visual templates (17 lab, 12 medication, 5 narrative documents). Total OOD test examples: **3,042 examples** (1,441 SUPPORTED, 1,441 DISCREPANCY, 160 INSUFFICIENT_EVIDENCE).
- **Data Isolation:** Zero document, group, or patient leakage across folds.
- **Canonical Label Ordering:** `SUPPORTED` (0), `DISCREPANCY` (1), `INSUFFICIENT_EVIDENCE` (2).

## 5. Cross-Validation Results (5-Fold CV)

### Summary Metrics
- **CV Macro F1:** **0.3721 ± 0.0145** (Sample standard deviation with ddof=1)
- **CV Discrepancy Recall:** **0.9230 ± 0.0304**

### Per-Fold Breakdown
| Fold | Test Examples ($N$) | Macro F1 | Discrepancy Recall |
|---|---|---|---|
| Fold 0 | 984 | 0.3850 | 0.9375 |
| Fold 1 | 1039 | 0.3667 | 0.9266 |
| Fold 2 | 1372 | 0.3898 | 0.9614 |
| Fold 3 | 863 | 0.3627 | 0.8807 |
| Fold 4 | 1868 | 0.3564 | 0.9087 |
| **Mean ± Std** | **6126 total** | **0.3721 ± 0.0145** | **0.9230 ± 0.0304** |

### Per-Class CV Metrics (Mean ± Std across 5 Folds)
| Class Label | Precision | Recall | F1-Score | Evaluated Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.8048 ± 0.0665 | 0.3125 ± 0.0237 | 0.4497 ± 0.0308 | 2,853 |
| **DISCREPANCY** | 0.5218 ± 0.0157 | 0.9230 ± 0.0304 | 0.6667 ± 0.0201 | 2,853 |
| **INSUFFICIENT_EVIDENCE** | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 0.0000 ± 0.0000 | 420 |

### CV Confusion Matrix (Summed over 5 Folds)
```
                          Predicted SUPPORTED   Predicted DISCREPANCY   Predicted INSUFFICIENT
True SUPPORTED                           877                     1976                     0
True DISCREPANCY                         216                     2637                     0
True INSUFFICIENT_EVIDENCE               0                       420                      0
```

## 6. Out-of-Distribution (OOD) Performance
- **OOD Macro F1:** **0.3738**
- **OOD Discrepancy Recall:** **0.9674**

### Per-Class OOD Metrics (Total N = 3,042)
| Class Label | Precision | Recall | F1-Score | Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.8965 | 0.2824 | 0.4296 | 1441 |
| **DISCREPANCY** | 0.5386 | 0.9674 | 0.6920 | 1441 |
| **INSUFFICIENT_EVIDENCE** | 0.0000 | 0.0000 | 0.0000 | 160 |

### OOD Confusion Matrix
```
                          Predicted SUPPORTED   Predicted DISCREPANCY   Predicted INSUFFICIENT
True SUPPORTED                           407                     1034                     0
True DISCREPANCY                         47                      1394                     0
True INSUFFICIENT_EVIDENCE               0                       160                      0
```

## 7. Calibration Metrics
- **AUROC (Discrepancy vs Rest):** *Unavailable*
- **Brier Score:** *Unavailable*
- **Expected Calibration Error (ECE):** *Unavailable*

*(Technical Note: V1 is a deterministic rule-based heuristic that produces hard categorical assignments rather than continuous posterior probabilities. Calibration metrics requiring confidence scores or probability distributions are unavailable by definition.)*

## 8. Subgroup and Slice Metrics

### Degradation Severity Breakdown (Macro F1)
| Severity Slice | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `clean` | 0.3882 ± 0.0218 | 0.3932 | 1475 |
| `low` | 0.4310 ± 0.0177 | 0.4118 | 540 |
| `medium` | 0.3285 ± 0.0319 | 0.3792 | 526 |
| `high` | 0.2599 ± 0.0234 | 0.2445 | 501 |

### Claim Domain Breakdown (Macro F1)
| Claim Type | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `lab` | 0.3681 ± 0.0200 | 0.3770 | 2482 |
| `med` | 0.3829 ± 0.0313 | 0.3590 | 560 |

### Field Type Breakdown (Macro F1)
| Field Type | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `lab.value` | 0.3942 ± 0.0187 | 0.4010 | 1966 |
| `lab.flag` | 0.4175 ± 0.0594 | 0.4099 | 516 |
| `med.name` | 0.5156 ± 0.0372 | 0.4240 | 101 |
| `med.dose` | 0.3403 ± 0.0510 | 0.2086 | 257 |
| `med.frequency` | 0.4057 ± 0.0738 | 0.4956 | 101 |
| `med.route` | 0.2926 ± 0.0278 | 0.3925 | 101 |

## 9. Threshold and Calibration Procedure
- **Fixed Heuristic Thresholds:**
  - Token overlap threshold: `0.80`.
  - Evidence text minimum length: `3` characters.
  - OCR confidence gate: `< 0.50` flags `INSUFFICIENT_EVIDENCE`.
- **Calibration Status:** No threshold tuning or post-hoc probability scaling was applied.

## 10. Concise Interpretation of What the Experiment Measures
1. **Aggressive Discrepancy Detection:** The deterministic rule achieves high discrepancy recall (0.9230 CV, 0.9674 OOD) because any omission or discrepancy in numeric tokens or keywords immediately triggers a discrepancy decision.
2. **False Discrepancy Bias on Paraphrases / Formatting:** The model exhibits low recall on true supported claims (0.3125 in CV; 0.2824 in OOD). When the evidence text contains incidental formatting, surrounding clinical narrative, or abbreviation changes (e.g., "po daily" vs "oral once a day"), the token overlap drops below 0.80, misclassifying valid claims as discrepancies.
3. **Inability to Recognize Missing Evidence Without OCR Failure:** The deterministic verifier achieves 0.0000 F1 on `INSUFFICIENT_EVIDENCE`. In cases where evidence text is present (>= 3 characters and OCR confidence >= 0.50) but lacks substantive evidentiary support for the claim, the model flags a missing token discrepancy rather than recognizing the complete absence of evidentiary context.
4. **Architectural Role in the Cascade:** Experiment V1 confirms that while deterministic heuristics provide an aggressive, low-latency filter for exact numerical mismatches, they cannot independently resolve linguistic variability or distinguish missing evidence from clinical contradiction.
