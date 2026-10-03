# Verification Baseline Experiments (V1–V5)

This directory contains the formal experiment reports for the verification baselines evaluated under the canonical cross-validation and out-of-distribution (OOD) protocols defined in [`PROJECT_SPEC.md §22.2`](../PROJECT_SPEC.md).

All performance metrics, standard deviations, confusion matrices, and subgroup slices originate strictly from persisted results recorded in [`data/m2_results/m2_baseline_results.json`](../data/m2_results/m2_baseline_results.json). In compliance with the experimental audit constraints, no experiments were rerun, no persisted results were modified, and no non-persisted metrics have been invented.

## Baseline Summary Matrix

| Experiment ID | Benchmark Name | Model / Method Description | Primary Authority | Report Link |
|---|---|---|---|---|
| **V1** | Deterministic Verifier | Exact token & numerical matching heuristic (Stage A wrapper) | `PROJECT_SPEC.md §22.2` (V1) | [V1 REPORT.md](file:///d:/daa/experiments/V1-deterministic/REPORT.md) |
| **V2** | Levenshtein Threshold | Normalized edit-distance ratio with training-selected threshold | `PROJECT_SPEC.md §22.2` (V2) | [V2 REPORT.md](file:///d:/daa/experiments/V2-levenshtein/REPORT.md) |
| **V3** | TF-IDF + Logistic Regression | Word/character n-grams (1, 2) with multinomial L2 logistic regression | `PROJECT_SPEC.md §22.2` (V3) | [V3 REPORT.md](file:///d:/daa/experiments/V3-tfidf-logreg/REPORT.md) |
| **V4** | Embedding Similarity | Pretrained biomedical embeddings (SapBERT/BioLORD) cosine similarity | `PROJECT_SPEC.md §22.2` (V4) | [V4 REPORT.md](file:///d:/daa/experiments/V4-embedding-similarity/REPORT.md) |
| **V5** | Zero-Shot NLI | Pretrained cross-encoder `pritamdeka/PubMedBERT-MNLI-MedNLI` | `PROJECT_SPEC.md §22.2` (V5) | [V5 REPORT.md](file:///d:/daa/experiments/V5-nli/REPORT.md) |

## Overall Performance Overview (Persisted Benchmark Data)

| Metric | V1 (Deterministic) | V2 (Levenshtein) | V3 (TF-IDF + LR) | V4 (Embedding Sim) | V5 (Zero-Shot NLI) |
|---|---|---|---|---|---|
| **CV Macro F1 (mean ± std)** | 0.3721 ± 0.0145 | 0.3066 ± 0.0051 | 0.7091 ± 0.0241 | 0.3211 ± 0.0049 | 0.5699 ± 0.0282 |
| **CV Discrepancy Recall** | 0.9230 ± 0.0304 | 0.6382 ± 0.0161 | 0.7886 ± 0.0376 | 0.5038 ± 0.0208 | 0.5246 ± 0.0457 |
| **OOD Macro F1** | 0.3738 | 0.3189 | 0.6115 | 0.3109 | 0.5929 |
| **OOD Discrepancy Recall** | 0.9674 | 0.6537 | 0.5781 | 0.5649 | 0.6384 |
| **CV AUROC (Discrepancy)** | *Unavailable* | *Unavailable* | 0.7523 ± 0.0068 | *Unavailable* | 0.7304 ± 0.0184 |
| **CV Brier Score** | *Unavailable* | *Unavailable* | 0.4359 ± 0.0089 | *Unavailable* | 0.5890 ± 0.0266 |
| **CV ECE (10-bin)** | *Unavailable* | *Unavailable* | 0.0769 ± 0.0122 | *Unavailable* | 0.2182 ± 0.0176 |
| **OOD AUROC** | *Unavailable* | *Unavailable* | 0.6955 | *Unavailable* | 0.7416 |
| **OOD Brier Score** | *Unavailable* | *Unavailable* | 0.4663 | *Unavailable* | 0.5504 |
| **OOD ECE** | *Unavailable* | *Unavailable* | 0.0269 | *Unavailable* | 0.2074 |

*(Notes: All standard deviations are computed across the 5 cross-validation folds using sample standard deviation `ddof=1`. Baselines V1, V2, and V4 produce hard categorical labels without posterior probabilities, meaning probabilistic calibration metrics AUROC, Brier score, and ECE are unavailable by definition.)*

## Canonical Evaluation Protocol

1. **Document-Level GroupKFold CV:** The 86-document pool is partitioned into 5 folds with zero document or patient leakage across partitions. Each fold contains approximately 17-18 held-out documents. A total of 6,126 examples were evaluated across the 5 validation folds (Fold 0: 984, Fold 1: 1,039, Fold 2: 1,372, Fold 3: 863, Fold 4: 1,868).
2. **Held-Out OOD Pool:** 34 documents (17 lab, 12 medication, 5 narrative) representing entirely unseen layout templates (3,042 examples total: 1,441 SUPPORTED, 1,441 DISCREPANCY, 160 INSUFFICIENT_EVIDENCE).
3. **Canonical Label Space:**
   - Index 0: `SUPPORTED`
   - Index 1: `DISCREPANCY`
   - Index 2: `INSUFFICIENT_EVIDENCE`
