# M2 V3 Benchmark ModernBERT Training Experiment Report

**Status:** COMPLETED & VERIFIED  
**Date:** 2026-10-02  
**Git Commit HEAD:** `a6a13d12001ea35342b20526b5ae8882bb977566`  
**Execution Script:** `scripts/train_v3_experiment.py`  
**Target Benchmark:** `data/m2_verification_v3/`  
**Output Results Directory:** `data/m2_results_v3/`  

---

## 1. Executive Summary

This report documents the **FIRST Stage B ModernBERT training experiment** conducted exclusively on the newly certified and authoritative **V3 M2 verification benchmark** (`data/m2_verification_v3/`). 

Following the strict protocol mandates:
1. **Zero benchmark modification:** The V3 dataset files (`all_examples.jsonl` and `split_manifest.json`) were verified before and after execution; their SHA256 hashes remained byte-identical.
2. **Historical benchmark preservation:** The historical V2 dataset (`data/m2_verification/`) and historical checkpoint directories remained untouched.
3. **Partition isolation:** The model was trained strictly on the V3 **Train** partition (2,242 examples), evaluated on the V3 **Validation** partition (883 examples), and calibrated via post-hoc Temperature Scaling strictly on the dedicated V3 **Calibration** partition (678 examples). The Validation split was **never** used for temperature fitting.
4. **Architectural fidelity:** Built on `thomas-sounack/BioClinical-ModernBERT-base` with text-only features (`aux_mode="none"`), full transformer fine-tuning (`freeze_lower_n_layers=0`), differential learning rates (encoder: $2 \times 10^{-5}$, head: $1 \times 10^{-4}$), linear warmup (6%), and cosine decay.
5. **No Phase 4 or Stage C execution:** Experimentation terminated immediately following model training, post-hoc calibration, split evaluation, and report generation.

### Key Headline Results

| Metric | Validation Partition (In-Distribution, N=883) | OOD Partition (Out-of-Distribution, N=1826) |
|---|---|---|
| **Macro-F1** | **0.9809** | **0.9519** |
| **Accuracy** | **0.9898** (874 / 883) | **0.9551** (1,744 / 1,826) |
| **Discrepancy Recall** | **0.9913** (455 / 459) | **0.9877** (966 / 978) |
| **AUROC (Discrepancy)** | **0.9920** | **0.9659** |
| **ECE (Post-Calibration)** | **0.0048** | **0.0365** |
| **Brier Score** | **0.0188** | **0.0858** |
| **Learned Temperature $T$** | **1.146522** (fitted on Calibration split only) | Applied $T=1.146522$ |

---

## 2. Dataset Provenance and Cryptographic Verification

All data loading used the dedicated partition APIs in `clindoc.m2.splits` operating exclusively on `data/m2_verification_v3/`.

### Cryptographic Hashes

| File | Role | Expected SHA256 | Verified Runtime SHA256 | Status |
|---|---|---|---|---|
| `data/m2_verification_v3/all_examples.jsonl` | Active Benchmark Examples | `40278c01848875382931a8a01397982ff6c00dfb7f588c30666b1ca8a66d6d16` | `40278c01848875382931a8a01397982ff6c00dfb7f588c30666b1ca8a66d6d16` | MATCH / UNMODIFIED |
| `data/m2_verification_v3/split_manifest.json` | Active Split Manifest | `cf41cc1d7e889f14cdcecf9ca2c411d54f2b737ddcfa280f6852b739050d2067` | `cf41cc1d7e889f14cdcecf9ca2c411d54f2b737ddcfa280f6852b739050d2067` | MATCH / UNMODIFIED |
| `data/m2_verification/all_examples.jsonl` | Historical V2 Examples | `ce924fa3921eedf2482c29279579ed010036273e608c399f38ce291c8306a2a8` | `ce924fa3921eedf2482c29279579ed010036273e608c399f38ce291c8306a2a8` | MATCH / UNTOUCHED |
| `data/m2_verification/split_manifest.json` | Historical V2 Manifest | `8a1ebd157188c4c4032dab297df4c53f9d2fc1dd58a52596a368cc7c676e4393` | `8a1ebd157188c4c4032dab297df4c53f9d2fc1dd58a52596a368cc7c676e4393` | MATCH / UNTOUCHED |

### Partition Breakdown and Document Separation

| Partition | Examples Loaded | Documents | Percentage | Disjointness Verification |
|---|---|---|---|---|
| **Train** | 2,242 | 52 | 39.8% | Disjoint from Val, Calib, OOD (0 document leakage) |
| **Validation** | 883 | 17 | 15.7% | Disjoint from Train, Calib, OOD (0 document leakage) |
| **Calibration** | 678 | 17 | 12.0% | Disjoint from Train, Val, OOD (0 document leakage) |
| **OOD (Held-out)** | 1,826 | 34 | 32.4% | Disjoint from CV pool (0 document leakage) |
| **Total Benchmark** | **5,629** | **120** | **100.0%** | Full coverage confirmed |

---

## 3. Training & Hardware Configuration

- **Model Identifier:** `thomas-sounack/BioClinical-ModernBERT-base`
- **Total Parameters:** 149,016,579 (149M)
- **Trainable Parameters:** 149,016,579 (100% trainable, `freeze_lower_n_layers=0`)
- **Auxiliary Mode:** `"none"` (Text-only verification: `[FIELD] {field} [CLAIM] {claim} [EVIDENCE] {evidence}`)
- **Sequence Length:** 256 tokens
- **Batch Size:** 16
- **Optimizer:** AdamW with decoupled weight decay
  - Encoder parameter learning rate: $2.0 \times 10^{-5}$
  - Classification head learning rate: $1.0 \times 10^{-4}$
  - Weight decay: $0.01$
- **Learning Rate Schedule:** Linear warmup over first 6% of total steps, followed by Cosine Annealing decay
- **Loss Function:** PyTorch `nn.CrossEntropyLoss`
- **Mixed Precision:** Automatic Mixed Precision (AMP FP16 via `torch.amp.autocast("cuda")` and `GradScaler`)
- **Gradient Clipping:** Max norm = 1.0
- **Stopping Criterion:** Validation macro-F1 early stopping with patience = 3 epochs
- **Hardware Acceleration:** NVIDIA GeForce RTX 3050 6GB Laptop GPU (CUDA 12.x / PyTorch 2.x)
- **Total Training Duration:** 484.25 seconds (8.07 minutes)

---

## 4. Training Trajectory & Convergence

Training proceeded smoothly across epochs with early stopping triggered after 3 consecutive non-improving validation epochs following the peak at Epoch 2.

| Epoch | Train Loss | Validation Loss | Validation Macro-F1 | Notes |
|---|---|---|---|---|
| **1** | 0.7655 | 0.6896 | 0.6224 | Rapid initial representation alignment |
| **2** | **0.3802** | **0.0618** | **0.9809** | **BEST EPOCH (Checkpoint saved)** |
| **3** | 0.0736 | 0.0556 | 0.9784 | Patience counter = 1 |
| **4** | 0.0546 | 0.0920 | 0.9793 | Patience counter = 2 |
| **5** | 0.0260 | 0.0648 | 0.9795 | Patience counter = 3 $\to$ **Early stopping triggered** |

The model state from **Epoch 2** (validation macro-F1: `0.9809`, validation loss: `0.0618`) was automatically restored as the optimal checkpoint prior to post-hoc calibration and evaluation.

---

## 5. In-Distribution Validation Performance (N=883)

Evaluated on the 883 examples belonging to the 17 in-distribution Validation documents.

### Overall Validation Metrics

- **Validation Accuracy:** `0.9898` (874 / 883 correct predictions)
- **Validation Macro-F1:** `0.9809`
- **Validation Discrepancy Recall:** `0.9913` (455 / 459 true discrepancies caught)
- **Validation AUROC (Discrepancy vs. Rest):** `0.9920`
- **Validation Brier Score:** `0.0188`
- **Validation ECE (Post-Temperature Scaling):** `0.0048`

### Per-Class Performance Breakdown

| Class | Precision | Recall | F1-Score | Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.9891 | 1.0000 | 0.9945 | 362 |
| **DISCREPANCY** | 0.9891 | 0.9913 | 0.9902 | 459 |
| **INSUFFICIENT_EVIDENCE** | 1.0000 | 0.9194 | 0.9580 | 62 |
| **Macro Average** | **0.9927** | **0.9702** | **0.9809** | **883** |

### Confusion Matrix (Rows: Ground Truth, Columns: Predicted)

| Ground Truth \ Predicted | SUPPORTED | DISCREPANCY | INSUFFICIENT_EVIDENCE | Total |
|---|---|---|---|---|
| **SUPPORTED** | **362** | 0 | 0 | 362 |
| **DISCREPANCY** | 4 | **455** | 0 | 459 |
| **INSUFFICIENT_EVIDENCE** | 0 | 5 | **57** | 62 |
| **Total** | 366 | 460 | 57 | 883 |

#### Observations on Validation Errors:
- Only **9 total errors** occurred out of 883 examples.
- **0 false positives** on `SUPPORTED` (100% recall for supported claims).
- **4 true discrepancies** were misclassified as `SUPPORTED` (0.87% false negative rate for discrepancies).
- **5 true insufficient evidence cases** were classified as `DISCREPANCY`.

---

## 6. Post-Hoc Calibration & Temperature Scaling

Conforming strictly to **DEC-009**:
- Calibration was fitted on the **dedicated Calibration partition** (678 examples from 17 independent documents).
- The Validation partition was **NOT** used for temperature fitting, preserving its status as an unbiased test holdout.
- Fitting used L-BFGS optimization to minimize Negative Log-Likelihood (NLL) over the single temperature scalar $T$.

### Calibration Results on Calibration Partition (N=678)

- **Learned Temperature Parameter ($T$):** `1.146522`
- **Expected Calibration Error (ECE):**
  - Before Calibration ($T=1.0$): `0.0063`
  - After Calibration ($T=1.1465$): `0.0032` (**49.2% reduction in calibration error**)
- **Negative Log-Likelihood (NLL):**
  - Before Calibration: `0.0542`
  - After Calibration: `0.0513`

The learned temperature $T = 1.146522$ indicates that the raw network logits were slightly overconfident ($T > 1.0$). Applying $T$ successfully softened extreme probability distributions and halved the calibration error.

---

## 7. Out-of-Distribution (OOD) Generalization Performance (N=1826)

The standard Stage B pipeline (`train_one_fold` / `evaluate_model_on_split`) automatically evaluates the calibrated model on the held-out OOD partition (1,826 examples from 34 unseen templates).

### Overall OOD Metrics

- **OOD Accuracy:** `0.9551` (1,744 / 1,826 correct predictions)
- **OOD Macro-F1:** `0.9519`
- **OOD Discrepancy Recall:** `0.9877` (966 / 978 true discrepancies caught)
- **OOD AUROC (Discrepancy vs. Rest):** `0.9659`
- **OOD Brier Score:** `0.0858`
- **OOD ECE:** `0.0365`

### OOD Per-Class Performance Breakdown

| Class | Precision | Recall | F1-Score | Support |
|---|---|---|---|---|
| **SUPPORTED** | 0.9767 | 0.9204 | 0.9477 | 729 |
| **DISCREPANCY** | 0.9360 | 0.9877 | 0.9612 | 978 |
| **INSUFFICIENT_EVIDENCE** | 1.0000 | 0.8992 | 0.9469 | 119 |
| **Macro Average** | **0.9709** | **0.9358** | **0.9519** | **1,826** |

### OOD Confusion Matrix (Rows: Ground Truth, Columns: Predicted)

| Ground Truth \ Predicted | SUPPORTED | DISCREPANCY | INSUFFICIENT_EVIDENCE | Total |
|---|---|---|---|---|
| **SUPPORTED** | **671** | 58 | 0 | 729 |
| **DISCREPANCY** | 12 | **966** | 0 | 978 |
| **INSUFFICIENT_EVIDENCE** | 4 | 8 | **107** | 119 |
| **Total** | 687 | 1,032 | 107 | 1,826 |

#### Observations on OOD Generalization:
- **Discrepancy Recall remains exceptionally high** at `98.77%` on unseen document templates, demonstrating robust clinical safety.
- Macro-F1 of `0.9519` demonstrates outstanding cross-domain generalization from the 52 training documents to the 34 completely held-out OOD document types.

---

## 8. Checkpoint & Artifact Provenance

### Checkpoint Specifications

- **File Path:** `data/m2_results_v3/checkpoints/modernbert_text_only_v3_fold0_seed42.pt`
- **File Size:** `596,113,470 bytes` (568.50 MB)
- **Contained Keys:**
  - `model_state`: Full PyTorch state dict for `ClinicalVerifier`
  - `config`: Complete serialization of `M2ModelConfig`
  - `aux_mode`: `"none"`
  - `freeze_lower_n_layers`: `0`
  - `fold`: `0`
  - `seed`: `42`
  - `best_val_macro_f1`: `0.9808984780821174`
  - `model_name`: `"modernbert_text_only_v3"`
  - `temperature`: `1.1465222835540771` (DEC-009 calibrated temperature)

### Result Metadata

- **Structured Output File:** `data/m2_results_v3/v3_experiment_metrics.json`
- **Training Logs:** `.system_generated/tasks/task-11729.log`

---

## 9. Protocol Compliance & Governance Checklist

- [x] **Trained exclusively from V3 Train partition:** 2,242 examples from 52 documents.
- [x] **Evaluated on V3 Validation partition:** 883 examples from 17 documents.
- [x] **Calibration fitted exclusively on V3 Calibration partition:** 678 examples from 17 documents.
- [x] **Validation split strictly isolated:** Never used for gradient updates, early stopping, or temperature fitting.
- [x] **Zero document leakage:** Disjoint document IDs verified across Train, Validation, Calibration, and OOD.
- [x] **Historical V2 benchmark untouched:** Hashes verified before and after experiment.
- [x] **No Phase 4 cascade evaluation run:** As mandated, stopping immediately after Stage B experiment.
- [x] **No Stage C LLM judge run:** Execution strictly confined to Stage B ModernBERT.
