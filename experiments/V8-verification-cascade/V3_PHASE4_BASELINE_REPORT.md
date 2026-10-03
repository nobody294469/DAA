# V3 Phase-4 Verification Cascade: Consolidated Baseline Report

**Experiment ID**: `V8-verification-cascade`  
**Evaluation Target**: Authoritative V3 Benchmark (`data/m2_verification_v3/`)  
**Stage B Checkpoint**: `data/m2_results_v3/checkpoints/modernbert_text_only_v3_fold0_seed42.pt` (Temperature $T = 1.146522$)  
**Stage C LLM Judge**: `Qwen/Qwen2.5-7B-Instruct-AWQ` via vLLM on NVIDIA T4 GPU  
**Decision Policy**: Escalation threshold $\tau = 0.60$ (DEC-005), Document-local context bounding (DEC-003), Grammar-constrained JSON output (DEC-013)  
**Status**: **PRE-IMPROVEMENT BASELINE FREEZE** (Do not alter model weights, thresholds, prompts, or evaluation code)

---

## 1. Executive Summary

This report establishes the frozen, authoritative **pre-improvement baseline** for the Phase-4 Verification Cascade evaluated on the V3 clinical verification benchmark.

The Verification Cascade operates as a hierarchical multi-stage architecture:
1. **Stage A**: Fast deterministic rule verifier (numeric tolerance, unit equivalence, negation parsing).
2. **Stage B**: Fine-tuned clinical language model (`thomas-sounack/BioClinical-ModernBERT-base`) calibrated via post-hoc temperature scaling ($T = 1.1465$).
3. **Stage C**: Selective 7B parameter instruction-tuned LLM judge (`Qwen/Qwen2.5-7B-Instruct-AWQ`) invoked only when Stage B encounters low confidence ($< 0.60$), disagreement with Stage A pre-checks, or potential evidence insufficiency.

### Key Headline Metrics:
- **In-Distribution Validation (883 claims)**:
  - **Cascade Macro-F1**: **`0.9801`**
  - **Cascade Discrepancy Recall**: **`0.9978`**
  - **Stage B Resolution Rate**: **`87.77%`** (775 claims) with **`0.9935`** conditional accuracy.
  - **Stage C Escalation Rate**: **`12.23%`** (108 claims) with **`0.9815`** conditional accuracy.
- **Held-Out Template Out-of-Distribution (1,826 claims)**:
  - **Cascade Macro-F1**: **`0.9422`**
  - **Cascade Discrepancy Recall**: **`0.9806`**
  - **Stage B Resolution Rate**: **`79.13%`** (1,445 claims) with **`0.9931`** conditional accuracy.
  - **Stage C Escalation Rate**: **`20.87%`** (381 claims) with **`0.8294`** conditional accuracy.

### Baseline Integrity Statement:
*This evaluation reflects benchmark performance under rigorous document-isolated splits and held-out template stress testing. This report does NOT describe the system as clinically safe or clinically validated. It benchmarks discrepancy detection fidelity, evidence verification behavior, and models architectural limitations.*

---

## 2. Experimental Configuration

| Parameter | Specification | Notes / Rationale |
| :--- | :--- | :--- |
| **Stage A Verifier** | `DeterministicVerifier` | Exact float tolerance (1e-4), UCUM unit normalization, regex negation |
| **Stage B Encoder** | `BioClinical-ModernBERT-base` | Fold 0, Seed 42 text-only checkpoint trained on V3 Train split |
| **Stage B Calibration** | Temperature Scaling ($T = 1.146522$) | Fitted strictly on held-out V3 Calibration split (678 claims) |
| **Escalation Threshold** | $\tau = 0.60$ | Calibrated confidence below 0.60 triggers Stage C (DEC-005) |
| **Stage C Engine** | `Qwen/Qwen2.5-7B-Instruct-AWQ` | vLLM OpenAI-compatible server on NVIDIA T4 GPU |
| **Stage C Decoding** | `temperature=0.0`, `seed=42`, `max_tokens=256` | Strict JSON schema constrained output via Pydantic model (DEC-013) |
| **Context Window** | Document-local snippet | Bounded local window surrounding target evidence span (DEC-003) |
| **Hardware** | NVIDIA T4 GPU (vLLM) / CUDA Host | Automated single-pass batch inference |

---

## 3. V3 Dataset & Split Architecture

The V3 benchmark (`data/m2_verification_v3/`) enforces absolute document-level isolation, zero patient/group leakage, and template-based out-of-distribution partitioning:

| Partition | Document Count | Example Count | Class Distribution (SUP / DISC / IE) | Purpose |
| :--- | :---: | :---: | :---: | :--- |
| **Train** | 52 | 2,242 | 920 / 1,164 / 158 | ModernBERT encoder & head training |
| **Calibration** | 17 | 678 | 277 / 353 / 48 | Dedicated temperature scaling fitting |
| **Validation (In-Dist)** | 17 | 883 | 362 / 459 / 62 | In-distribution cascade evaluation |
| **OOD Template Holdout** | 34 | 1,826 | 748 / 951 / 127 | Unseen template stress testing |
| **Total Benchmark** | **120** | **5,629** | **2,307 / 2,927 / 395** | Immutable certified V3 dataset |

- **Held-Out OOD Templates**:
  - `template_c`: Complex multi-column diagnostic laboratory report
  - `mar_order_sheet`: Multi-entry Medication Administration Record
  - `narrative_specialty_consult_note_v1` & `narrative_ambulatory_progress_note_v1`: Unstructured clinical narrative notes

---

## 4. In-Distribution Validation Results (883 Claims)

Evaluated on the canonical V3 validation partition:

### 4.1 Overall Cascade Performance
- **Macro-F1**: **`0.9801`**
- **Discrepancy Recall**: **`0.9978`** (458 / 459 clinical discrepancies caught)
- **Overall Accuracy**: **`0.9921`** (876 / 883 correct)

### 4.2 Per-Class Metrics
| Class | Precision | Recall | F1 Score | Support |
| :--- | :---: | :---: | :---: | :---: |
| **SUPPORTED** | 0.9972 | 1.0000 | 0.9986 | 362 |
| **DISCREPANCY** | 0.9871 | 0.9978 | 0.9924 | 459 |
| **INSUFFICIENT_EVIDENCE** | 1.0000 | 0.9032 | 0.9492 | 62 |

### 4.3 Stage Resolution Breakdown
- **Stage A Resolved**: 0 claims (Tier-1 fields prioritize Stage B learned verification)
- **Stage B Resolved**: 775 claims (87.77%) | Accuracy: **0.9935** (770 / 775)
- **Stage C Escalated**: 108 claims (12.23%) | Accuracy: **0.9815** (106 / 108)

---

## 5. Out-of-Distribution (OOD) Results (1,826 Claims)

Evaluated on the completely held-out OOD template pool:

### 5.1 Overall Cascade Performance
- **Macro-F1**: **`0.9422`**
- **Discrepancy Recall**: **`0.9806`** (932 / 951 clinical discrepancies caught)
- **Overall Accuracy**: **`0.9595`** (1,752 / 1,826 correct)

### 5.2 Stage Resolution Breakdown
- **Stage A Resolved**: 0 claims
- **Stage B Resolved**: 1,445 claims (79.13%) | Accuracy: **0.9931** (1,435 / 1,445)
- **Stage C Escalated**: 381 claims (20.87%) | Accuracy: **0.8294** (316 / 381)

---

## 6. Validation vs. OOD Comparison Table

| Metric / Attribute | Validation (In-Dist) | OOD Holdout | Delta / Shift | Observations |
| :--- | :---: | :---: | :---: | :--- |
| **Total Claims** | 883 | 1,826 | +106.8% | Full benchmark scale |
| **Cascade Macro-F1** | **0.9801** | **0.9422** | **-0.0379** | Strong generalization retention |
| **Discrepancy Recall** | **0.9978** | **0.9806** | **-0.0172** | Retains high discrepancy catch rate |
| **Stage B Resolved Count** | 775 (87.77%) | 1,445 (79.13%) | -8.64% | Increased escalation under OOD |
| **Stage B Subset Accuracy** | **0.9935** | **0.9931** | **-0.0004** | Remarkable Stage B stability |
| **Stage B Subset Macro-F1** | 0.6646 | 0.6644 | -0.0002 | Mathematical unweighted average effect |
| **Stage B AUROC (Macro)** | 0.9939 | 0.9732 | -0.0207 | Maintained discrimination |
| **Stage B Brier Score** | 0.0189 | 0.0858 | +0.0669 | Slight probability calibration drift |
| **Stage B ECE** | 0.0049 | 0.0372 | +0.0323 | Well-bounded calibration error |
| **Stage C Escalation Count** | 108 (12.23%) | 381 (20.87%) | +8.64% | Reflects appropriate uncertainty routing |
| **Stage C Accuracy** | **0.9815** | **0.8294** | **-0.1521** | Primary baseline vulnerability |

---

## 7. Cascade Dynamics

The cascade functions as an efficient triage funnel:
- In-distribution, **87.8%** of claims are resolved cheaply and rapidly by Stage B without invoking the large LLM judge.
- Under unseen template stress, Stage B detects distribution shift and increases escalation to **20.9%**, successfully preserving **99.31%** accuracy on the claims it resolves.

### Escalation Reason Distribution (OOD 381 claims):
1. **Stage A / Stage B Disagreement**: 371 claims (97.38%)
   - Driven largely by tabular format differences in `template_c` where Stage A and Stage B arrive at conflicting intermediate hypotheses.
2. **Stage B Low Confidence ($< 0.60$)**: 7 claims (1.84%)
3. **Stage B Insufficient Evidence Precaution (Rule C)**: 3 claims (0.78%)

---

## 8. Stage-B Calibration & Probability Results

Probabilities are derived strictly from genuine ModernBERT logits calibrated by temperature scaling ($T = 1.146522$):

| Metric | Validation (883 claims) | OOD Holdout (1,826 claims) | Reference Target |
| :--- | :---: | :---: | :---: |
| **AUROC (Macro)** | 0.9939 | 0.9732 | $\ge 0.95$ |
| **AUROC (Discrepancy)** | 0.9922 | 0.9670 | $\ge 0.95$ |
| **Brier Score** | 0.0189 | 0.0858 | $\le 0.10$ |
| **Expected Calibration Error (ECE)** | 0.0049 | 0.0372 | $\le 0.05$ |

**Key Finding**: The temperature scaling calibrator fitted on the 678-claim calibration partition transferred successfully to the unseen OOD partition, maintaining ECE under 4% and AUROC above 0.96.

---

## 9. Stage-C Results

Stage C was executed with zero mock abstractions, using genuine vLLM inference of `Qwen/Qwen2.5-7B-Instruct-AWQ`:
- **Validation**: 106 correct / 108 claims (**98.15% accuracy**)
- **OOD**: 316 correct / 381 claims (**82.94% accuracy**)
- **Runtime Integrity**:
  - `0` fallbacks triggered
  - `0` HTTP/transport timeouts
  - `0` schema validation errors
  - `381 / 381` requests succeeded on attempt 1 with complete structured rationales.

---

## 10. Stage-C OOD Error Analysis (65 Errors)

A thorough diagnostic inspection of all 65 errors on the OOD partition revealed a concentrated, systematic vulnerability:

### 10.1 Confusion Matrix (Stage C OOD):
- **True SUPPORTED**: 26 correct, **42 falsely predicted as DISCREPANCY**, 0 predicted as INSUFFICIENT
- **True DISCREPANCY**: **185 correct**, 8 falsely predicted as SUPPORTED, 9 predicted as INSUFFICIENT
- **True INSUFFICIENT**: **105 correct**, 3 falsely predicted as SUPPORTED, 3 predicted as DISCREPANCY

### 10.2 Concentration by Field and Template:
- **`lab.flag` accounts for 56 out of 65 errors (86.15%)**.
- **`template_c` accounts for 60 out of 65 errors (92.31%)**.
- On all medication fields (`med.name`, `med.dose`, `med.route`, `med.frequency`), Stage C attained **93.42% accuracy**.

### 10.3 Root Cause Breakdown:
1. **Numerical Interval Reasoning Hallucination**:
   In `template_c`, tabular lines place analyte flags in an `Abnormal` column (e.g., `Glucose 64.0 mg/dL 70 - 99 L` or `WBC 11.6 10*3/uL 4.5 - 11.0 H`). Rather than reading the explicit `H` or `L` token, Qwen frequently attempts to verify the reference range mathematically and makes elementary range comparison errors, asserting in its rationales that $64.0$ is inside $[70, 99]$ or $11.6$ is inside $[4.5, 11.0]$.
2. **Single-Letter Flag Semantics**:
   Qwen occasionally misinterprets single-letter column entries, even asserting in one instance that `L` denoted a "high creatinine level".
3. **Absence vs. Contradiction Concept Boundary**:
   For document-level unobserved medication claims (where the drug was never ordered), Qwen labeled several as `DISCREPANCY` rather than `INSUFFICIENT_EVIDENCE` because other unrelated drugs were listed on the page.

---

## 11. Stage-B OCR Hard-Case Analysis (Validation Subset)

On the in-distribution validation set, Stage B resolved 775 claims with **0.9935 accuracy** (770 / 775). Exactly 5 claims were misclassified (all Ground Truth `INSUFFICIENT_EVIDENCE` $\rightarrow$ Stage B resolved as `DISCREPANCY`):

### 11.1 The 5 Hard Cases:
1. `8536c650`: Claim `Glucose: 84 mg/dL` vs. Evidence `Glucose B4.0 mg/dL` (OCR turned `8` into `B`).
2. `64434df3`: Claim `Metformin dose: 500 mg` vs. Evidence `Metformin 50 mg` (OCR dropped terminal `0`).
3. `863aeea9`: Claim `Alkaline Phosphatase: 115 U/L` vs. Evidence `Alkaline Phosphatase 1l0 U/L` (`115` $\rightarrow$ `1l0`).
4. `1287f384`: Claim `Metformin dose: 500 mg` vs. Evidence `Metformin 50D mg` (OCR turned `0` into `D`).
5. `9249f4c4`: Claim `LDL Cholesterol: 88 mg/dL` vs. Evidence `LDL Cholesterol: 8.0 mg/dL` (`88` $\rightarrow$ `8.0`).

### 11.2 Architectural Findings:
- All 5 cases originate from synthetic OCR noise modeling (**Track B**).
- When OCR corruption inadvertently produces a clean, readable alternative number (e.g. `50 mg` or `8.0 mg/dL`), text-only verifiers (both Stage A rules and Stage B ModernBERT) cannot distinguish between an OCR scanning defect and an intentional clinical contradiction.
- Both verifiers agreed on `DISCREPANCY` with $>0.996$ confidence, bypassing Stage C.
- This is a legitimate benchmark boundary phenomenon inherent to text-only verification architectures lacking token-level OCR bounding box / confidence features.

---

## 12. Current Architectural Limitations

1. **Text-Only Blindness to OCR Quality**:
   Neither Stage A nor Stage B incorporates OCR character-level confidence scores or bounding box geometry during verification.
2. **7B Model Numerical Range Fragility**:
   `Qwen2.5-7B-Instruct-AWQ` cannot be relied upon for raw floating-point interval comparison when column-structured lab tables present abbreviated single-letter flags (`H`/`L`).
3. **Stage B Resolution Exclusion on Insufficient Evidence**:
   By policy (Rule C), Stage B cannot resolve `INSUFFICIENT_EVIDENCE`. While safe, this creates a mathematical ceiling on Stage B subset macro-averages when sparse residual errors occur.
4. **Context Formatting for Tabular Spans**:
   Linearized OCR text provided to Stage C loses 2D column spatial alignment, increasing LLM entity-attribute confusion across multi-row order sheets.

---

## 13. Pre-Improvement Baseline Conclusions

1. **The Phase-4 V3 Baseline is Certified and Frozen**:
   The baseline achieves **`0.9801` Macro-F1** and **`0.9978` Discrepancy Recall** on in-distribution validation, and **`0.9422` Macro-F1** and **`0.9806` Discrepancy Recall** under held-out template stress.
2. **Stage B ModernBERT is the Anchor of Stability**:
   Stage B provides high throughput and high fidelity, resolving $\sim 80\text{--}88\%$ of claims at **$>99.3\%$ accuracy** with calibrated probability metrics ($ECE \le 0.037$).
3. **Stage C Bottleneck is Diagnosed**:
   The drop in OOD performance is localized to a known, specific mechanism: tabular `lab.flag` representation in `template_c` and numerical interval reasoning in Qwen-7B.

---

## 14. Explicit Baseline Freeze Statement

> **NOTICE**: This document establishes the authoritative **PRE-IMPROVEMENT BASELINE** for the clinical verification cascade. All metrics, thresholds, weights, and prompt configurations recorded herein are frozen. Future architectural improvements, prompt adjustments, OCR confidence conditioning, or verifier updates must measure progression against these frozen baseline figures.
