# Experiment V5: Zero-Shot Off-the-Shelf NLI Baseline

## 1. Experiment Overview
- **Experiment ID:** V5 (`zeroshot_nli`)
- **Specification Authority:** [`PROJECT_SPEC.md §22.2`](../../PROJECT_SPEC.md) (V5 baseline)
- **Execution Status:** COMPLETED (persisted results in repository)
- **Source Data Artifact:** [`data/m2_results/m2_baseline_results.json`](../../data/m2_results/m2_baseline_results.json)
- **Implementation Module:** [`predict_zeroshot_nli`](file:///d:/daa/src/clindoc/m2/baselines.py#L423-L468), [`predict_zeroshot_nli_proba`](file:///d:/daa/src/clindoc/m2/baselines.py#L470-L515) in `src/clindoc/m2/baselines.py`

## 2. Objective
Evaluate the zero-shot transfer capabilities of a specialized biomedical cross-encoder trained on natural language inference (`pritamdeka/PubMedBERT-MNLI-MedNLI`). This experiment tests whether standard NLI relationships (entailment, contradiction, neutral) map effectively onto clinical document verification labels (supported, discrepancy, insufficient evidence) without domain-specific fine-tuning on M2 data.

## 3. Exact Method and Model Details
1. **Pretrained Model Architecture:**
   - Model: `pritamdeka/PubMedBERT-MNLI-MedNLI` (PubMedBERT encoder + cross-encoder sequence classification head).
   - Zero-shot evaluation: Pretrained weights are completely frozen; zero task fine-tuning on M2.
2. **Cross-Encoder Pair Representation:**
   - **Premise:** Extracted source evidence snippet (`evidence.strip()`).
   - **Hypothesis:** Formatted claim string with optional field context:
     `hypothesis = format_nli_hypothesis(claim, field_type)`
3. **Label Space Mapping:**
   - The native 3-class NLI outputs are mapped directly to canonical project labels:
     - `Entailment (native 1) -> SUPPORTED (project 0)`
     - `Contradiction (native 0) -> DISCREPANCY (project 1)`
     - `Neutral (native 2) -> INSUFFICIENT_EVIDENCE (project 2)`
4. **Evidence and OCR Guard:**
   - If `ocr_confidence < 0.50` or `len(evidence.strip()) < 3`, automatically classified as `INSUFFICIENT_EVIDENCE`.
5. **Decision Rule:**
   - Logits are projected to canonical label space: `[z_0, z_1, z_2] = [z_entail, z_contra, z_neutral]`.
   - Softmax yields class probabilities: `P(y = c | X) = softmax(z)_c`.
   - Decision is selected via argmax: `y_hat = argmax_c P(y = c | X)`.

## 4. Dataset, Split, and Evaluation Protocol
- **Cross-Validation Split:** 5-fold cross-validation using document-level `GroupKFold` on the canonical 86-document pool (52 train, 17 calibration, 17 validation). Total test examples evaluated across all 5 folds: **6,126 examples** (Fold 0: 984, Fold 1: 1,039, Fold 2: 1,372, Fold 3: 863, Fold 4: 1,868).
- **Out-of-Distribution (OOD) Split:** 34 held-out documents representing completely unseen visual templates (17 lab, 12 medication, 5 narrative documents). Total OOD test examples: **3,042 examples** (1,441 SUPPORTED, 1,441 DISCREPANCY, 160 INSUFFICIENT_EVIDENCE).
- **Data Isolation:** Zero document, group, or patient leakage across folds. Completely frozen weights evaluated zero-shot across all splits.
- **Canonical Label Ordering:** `SUPPORTED` (0), `DISCREPANCY` (1), `INSUFFICIENT_EVIDENCE` (2).

## 5. Cross-Validation Results (5-Fold CV)

### Summary Metrics
- **CV Macro F1:** **0.5699 ± 0.0282** (Sample standard deviation with ddof=1)
- **CV Discrepancy Recall:** **0.5246 ± 0.0457**

### Per-Fold Breakdown
| Fold | Test Examples ($N$) | Macro F1 | Discrepancy Recall | AUROC (Discrepancy) | Brier Score | ECE |
|---|---|---|---|---|---|---|
| Fold 0 | 984 | 0.6062 | 0.5179 | 0.7572 | 0.5496 | 0.1913 |
| Fold 1 | 1039 | 0.5826 | 0.4948 | 0.7401 | 0.5747 | 0.2160 |
| Fold 2 | 1372 | 0.5503 | 0.5379 | 0.7246 | 0.6077 | 0.2205 |
| Fold 3 | 863 | 0.5762 | 0.4772 | 0.7106 | 0.6138 | 0.2404 |
| Fold 4 | 1868 | 0.5343 | 0.5953 | 0.7194 | 0.5992 | 0.2226 |
| **Mean ± Std** | **6126 total** | **0.5699 ± 0.0282** | **0.5246 ± 0.0457** | **0.7304 ± 0.0184** | **0.5890 ± 0.0266** | **0.2182 ± 0.0176** |

### Per-Class CV Metrics (Mean ± Std across 5 Folds)
| Class Label | Precision | Recall | F1-Score | Evaluated Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.7331 ± 0.0079 | 0.6753 ± 0.0480 | 0.7022 ± 0.0252 | 2,853 |
| **DISCREPANCY** | 0.7151 ± 0.0260 | 0.5246 ± 0.0457 | 0.6038 ± 0.0254 | 2,853 |
| **INSUFFICIENT_EVIDENCE** | 0.2665 ± 0.0600 | 0.8488 ± 0.0638 | 0.4038 ± 0.0759 | 420 |

### CV Confusion Matrix (Summed over 5 Folds)
```
                          Predicted SUPPORTED   Predicted DISCREPANCY   Predicted INSUFFICIENT
True SUPPORTED                           1892                    584                      377
True DISCREPANCY                         668                     1532                     653
True INSUFFICIENT_EVIDENCE               16                      49                       355
```

## 6. Out-of-Distribution (OOD) Performance
- **OOD Macro F1:** **0.5929**
- **OOD Discrepancy Recall:** **0.6384**

### Per-Class OOD Metrics (Total N = 3,042)
| Class Label | Precision | Recall | F1-Score | Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.7553 | 0.6662 | 0.7080 | 1441 |
| **DISCREPANCY** | 0.6991 | 0.6384 | 0.6674 | 1441 |
| **INSUFFICIENT_EVIDENCE** | 0.2725 | 0.7750 | 0.4033 | 160 |

### OOD Confusion Matrix
```
                          Predicted SUPPORTED   Predicted DISCREPANCY   Predicted INSUFFICIENT
True SUPPORTED                           960                     369                      112
True DISCREPANCY                         302                     920                      219
True INSUFFICIENT_EVIDENCE               9                       27                       124
```

## 7. Calibration Metrics
- **AUROC (Discrepancy vs Rest):**
  - **CV Mean ± Std:** **0.7304 ± 0.0184**
  - **OOD:** **0.7416**
- **Brier Score (Multi-Class Mean Squared Probability Error):**
  - **CV Mean ± Std:** **0.5890 ± 0.0266**
  - **OOD:** **0.5504**
- **Expected Calibration Error (ECE, 10 Bins):**
  - **CV Mean ± Std:** **0.2182 ± 0.0176**
  - **OOD:** **0.2074**

## 8. Subgroup and Slice Metrics

### Degradation Severity Breakdown (Macro F1)
| Severity Slice | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `clean` | 0.6351 ± 0.0225 | 0.6445 | 1475 |
| `low` | 0.5100 ± 0.0233 | 0.5423 | 540 |
| `medium` | 0.4572 ± 0.0313 | 0.5117 | 526 |
| `high` | 0.4532 ± 0.0323 | 0.5008 | 501 |

### Claim Domain Breakdown (Macro F1)
| Claim Type | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `lab` | 0.5360 ± 0.0231 | 0.5679 | 2482 |
| `med` | 0.5550 ± 0.0337 | 0.5661 | 560 |

### Field Type Breakdown (Macro F1)
| Field Type | CV Mean ± Std | OOD Macro F1 | OOD Sample Count ($N$) |
|---|---|---|---|
| `lab.value` | 0.5992 ± 0.0231 | 0.6464 | 1966 |
| `lab.flag` | 0.2455 ± 0.0280 | 0.1983 | 516 |
| `med.name` | 0.4919 ± 0.0178 | 0.4481 | 101 |
| `med.dose` | 0.5292 ± 0.0620 | 0.5786 | 257 |
| `med.frequency` | 0.6068 ± 0.0322 | 0.6740 | 101 |
| `med.route` | 0.6314 ± 0.0808 | 0.4966 | 101 |

## 9. Threshold and Calibration Procedure
- **Decision Rule:** Argmax selection over softmax probabilities after projecting native logits to project label ordering.
- **Evidence Guard:** Applied fixed guard threshold (< 0.50 OCR confidence or < 3 character length).
- **Post-Hoc Calibration:** Evaluated zero-shot without temperature scaling. While `calibrate_v5_temperature` is implemented in `baselines.py`, the persisted baseline results represent raw uncalibrated cross-encoder logits. Consequently, ECE remains elevated (0.2182 in CV; 0.2074 in OOD).

## 10. Concise Interpretation of What the Experiment Measures
1. **Semantic Cross-Encoding Advantage:** The zero-shot NLI cross-encoder achieves balanced precision on both `SUPPORTED` (0.7331) and `DISCREPANCY` (0.7151). Cross-attention between premise and hypothesis allows token-level comparison between claim and evidence, resolving lexical synonyms that cause deterministic and edit-distance baselines to fail.
2. **Template Robustness on OOD:** In contrast to the TF-IDF model whose Macro F1 dropped by ~10 points on OOD, the zero-shot NLI baseline improves slightly on OOD (0.5699 CV vs 0.5929 OOD). Pretrained language representations are invariant to surface layout and template variations.
3. **Tendency to Over-Predict Neutral:** The model achieves high recall on `INSUFFICIENT_EVIDENCE` (0.8488 in CV; 0.7750 in OOD), but exhibits low precision (0.2665 in CV; 0.2725 in OOD). When evidence snippets contain noisy OCR text or partial fragments, the cross-encoder defaults to neutral/insufficient evidence rather than detecting factual alignment or numerical contradiction.
4. **Severe Probability Miscalibration:** Zero-shot application produces significant overconfidence, reflected in high Brier score (0.5890) and high ECE (0.2182). This demonstrates that while pretrained NLI cross-encoders possess strong linguistic inductive biases, task-specific adaptation and temperature scaling are necessary for reliable risk-controlled clinical deployment.
