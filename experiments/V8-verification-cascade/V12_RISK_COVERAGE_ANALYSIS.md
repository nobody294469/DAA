# Experiment V12: Selective Prediction & Risk-Coverage Analysis

**Experiment ID:** `V12` (`V12-selective-prediction-risk-coverage`)  
**Specification Authority:** `PROJECT_SPEC.md` (§12, §21.5, §22.2)  
**Evaluation Date:** 2026-10-03  
**Evaluator:** Antigravity Autonomous Pair Programmer  
**Dataset & Split:** `data/m2_verification_v3` — Out-Of-Distribution (OOD) Benchmark  
**Total Claims Evaluated:** 1,826 claims across 34 unseen clinical documents  
**Diagnostic Source:** `experiments/V8-verification-cascade/phase4_cascade_diagnostics.json` (Frozen Post-V1 Run)  
**Machine-Readable Summary:** [`experiments/V8-verification-cascade/v12_risk_coverage_analysis.json`](file:///d:/daa/experiments/V8-verification-cascade/v12_risk_coverage_analysis.json)  
**Implementation Source:** [`src/clindoc/m2/metrics.py:compute_risk_coverage()`](file:///d:/daa/src/clindoc/m2/metrics.py#L140) & [`compute_aurc()`](file:///d:/daa/src/clindoc/m2/metrics.py#L210)  

---

## 1. Executive Summary

This study executes **Experiment V12** as formally specified in `PROJECT_SPEC.md` §21.5: evaluating risk-coverage dynamics, Area Under the Risk-Coverage Curve (AURC), and selective prediction tradeoffs comparing **Stage B (ModernBERT text-only)** standalone against the **V8 Selective Verification Cascade (Stage A $\rightarrow$ Stage B $\rightarrow$ Stage C)** on the authoritative 1,826-claim OOD benchmark.

### Core Scientific Findings:
1. **Cascade Halves Overall System Errors:**
   - Standalone Stage B commits **82 errors** across the 1,826 OOD claims (**95.51% accuracy**, 4.49% risk).
   - The V8 Selective Cascade commits only **46 errors** across the full benchmark (**97.48% accuracy**, 2.52% risk).
   - This represents an absolute reduction of **36 errors** and a **43.90% relative error reduction**.
2. **Autonomous High-Confidence Operation (Tier 1):**
   - At the operating point ($\tau = 0.60$ + Stage A agreement), Stage B autonomously resolves **1,445 of 1,826 claims (79.13% coverage)**.
   - On this resolved subset, Stage B achieves **99.31% accuracy** (only 10 errors, **risk = 0.0069**), running at zero LLM inference cost.
3. **Targeted Escalation Salvages High-Risk Claims (Tier 2):**
   - The cascade rejects and escalates **381 claims (20.87% escalation rate)** to Stage C (Qwen-7B).
   - On this escalated subset, standalone Stage B would have collapsed to **81.10% accuracy (72 errors)**.
   - Stage C achieves **90.55% accuracy (345/381)**, cutting errors on the hard subset exactly in half (from 72 down to 36).
4. **Architectural Routing Outperforms Pure Confidence Thresholding:**
   - Filtering Stage B claims purely by calibrated confidence to achieve 79.13% coverage yields **61 errors (risk = 0.0422)**.
   - In contrast, the cascade's dual-gate routing (Stage A agreement + Stage B $\ge 0.60$) achieves the exact same 79.13% coverage with **only 10 errors (risk = 0.0069)** — a **6.1$\times$ risk reduction**.
   - This empirically confirms that Stage A symbolic prechecks act as an orthogonal, high-precision safety net that catches overconfident ModernBERT misclassifications.

---

## 2. Production Operating Point Analysis

The authoritative production operating point is parameterized by escalation threshold $\tau = 0.60$ combined with Stage A agreement prechecking.

| Metric | Standalone Stage B (No Escalation) | Stage B Resolved (Autonomous Tier 1) | Stage C Escalated (LLM Tier 2) | Full V8 Cascade (Production Operating Point) |
| :--- | :---: | :---: | :---: | :---: |
| **Claim Count** | 1,826 | 1,445 | 381 | 1,826 |
| **Coverage Rate** | 100.00% | **79.13%** | 20.87% | **100.00%** |
| **Rejection / Escalation Rate** | 0.00% | 20.87% | 0.00% | **20.87%** |
| **Correct Predictions** | 1,744 | 1,435 | 345 | **1,780** |
| **Misclassifications (Errors)** | 82 | 10 | 36 | **46** |
| **Accuracy** | 95.51% | **99.31%** | **90.55%** | **97.48%** |
| **Empirical Risk (Error Rate)** | 4.49% | **0.69%** | **9.45%** | **2.52%** |
| **Counterfactual Stage B Accuracy** | 95.51% | 99.31% | *81.10% (72 errors)* | N/A |
| **Net Error Reduction vs. Stage B** | Baseline | 0 | **+36 errors avoided** | **-43.90% relative error** |

### Escalation Trigger Breakdown (381 Total Escalations):
- `stage_a_disagreement (A=INSUFFICIENT_EVIDENCE, B=DISCREPANCY)`: **192** (50.39%)
- `stage_a_disagreement (A=DISCREPANCY, B=INSUFFICIENT_EVIDENCE)`: **107** (28.08%)
- `stage_a_disagreement (A=SUPPORTED, B=DISCREPANCY)`: **58** (15.22%)
- `stage_a_disagreement (A=DISCREPANCY, B=SUPPORTED)`: **20** (5.25%)
- `stage_a_disagreement (A=INSUFFICIENT_EVIDENCE, B=SUPPORTED)`: **1** (0.26%)
- `low_confidence (confidence < 0.60)`: **3** (0.79%)

> **Critical Architectural Insight:** 378 out of 381 escalations (99.21%) were triggered by symbolic-neural disagreement between Stage A and Stage B, rather than raw confidence falling below $\tau = 0.60$. Stage B alone was overconfident on 72 errors in this subset, but Stage A's symbolic parser refused to agree, safely diverting them to Stage C.

---

## 3. Representative Risk-Coverage Points

Using the exact implementation in [`src/clindoc/m2/metrics.py:compute_risk_coverage()`](file:///d:/daa/src/clindoc/m2/metrics.py#L140), predictions were sorted in descending order of confidence to generate empirical risk-coverage curves across representative operating thresholds.

### 3.1 Standalone Stage B (ModernBERT Calibrated Confidence, $T = 1.146522$)

In pure selective prediction with abstention, Stage B makes predictions only when its temperature-calibrated softmax confidence exceeds the given threshold:

| Target Coverage | Actual Coverage | Rejection Rate | Empirical Risk | Selective Accuracy | Confidence Threshold |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **10.0%** | 10.30% (188/1826) | 89.70% | **0.0053** | 99.47% | 0.998386 |
| **20.0%** | 20.04% (366/1826) | 79.96% | **0.0137** | 98.63% | 0.998154 |
| **30.0%** | 30.01% (548/1826) | 69.99% | **0.0255** | 97.45% | 0.997899 |
| **40.0%** | 40.03% (731/1826) | 59.97% | **0.0410** | 95.90% | 0.997637 |
| **50.0%** | 50.00% (913/1826) | 50.00% | **0.0515** | 94.85% | 0.997342 |
| **60.0%** | 60.02% (1096/1826) | 39.98% | **0.0502** | 94.98% | 0.996885 |
| **70.0%** | 70.04% (1279/1826) | 29.96% | **0.0453** | 95.47% | 0.996229 |
| **79.13%** *(Equiv. Cov)* | 79.13% (1445/1826) | 20.87% | **0.0422** | **95.78%** | 0.995020 |
| **80.0%** | 80.01% (1461/1826) | 19.99% | **0.0418** | 95.82% | 0.994858 |
| **85.0%** | 85.05% (1553/1826) | 14.95% | **0.0412** | 95.88% | 0.993322 |
| **90.0%** | 90.03% (1644/1826) | 9.97% | **0.0401** | 95.99% | 0.988237 |
| **95.0%** | 95.02% (1735/1826) | 4.98% | **0.0392** | 96.08% | 0.979505 |
| **98.0%** | 98.03% (1790/1826) | 1.97% | **0.0391** | 96.09% | 0.947714 |
| **100.0%** | 100.00% (1826/1826) | 0.00% | **0.0449** | **95.51%** | 0.498600 |

### 3.2 Full Cascade (Naive Joint Confidence Ranking)

When sorting the entire 1,826 predictions by `final_confidence`:

| Target Coverage | Actual Coverage | Rejection Rate | Empirical Risk | Selective Accuracy | Confidence Threshold |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **10.0%** | 20.87% (381/1826) | 79.13% | **0.0945** | 90.55% | 1.000000 |
| **20.0%** | 20.87% (381/1826) | 79.13% | **0.0945** | 90.55% | 1.000000 |
| **30.0%** | 30.01% (548/1826) | 69.99% | **0.0675** | 93.25% | 0.998312 |
| **40.0%** | 40.09% (732/1826) | 59.91% | **0.0546** | 94.54% | 0.998029 |
| **50.0%** | 50.00% (913/1826) | 50.00% | **0.0460** | 95.40% | 0.997647 |
| **60.0%** | 60.02% (1096/1826) | 39.98% | **0.0383** | 96.17% | 0.997130 |
| **70.0%** | 70.04% (1279/1826) | 29.96% | **0.0328** | 96.72% | 0.996492 |
| **79.13%** | 79.13% (1445/1826) | 20.87% | **0.0291** | **97.09%** | 0.995489 |
| **80.0%** | 80.01% (1461/1826) | 19.99% | **0.0287** | 97.13% | 0.995285 |
| **85.0%** | 85.05% (1553/1826) | 14.95% | **0.0277** | 97.23% | 0.994052 |
| **90.0%** | 90.03% (1644/1826) | 9.97% | **0.0262** | 97.38% | 0.990214 |
| **95.0%** | 95.02% (1735/1826) | 4.98% | **0.0254** | 97.46% | 0.982901 |
| **98.0%** | 98.03% (1790/1826) | 1.97% | **0.0246** | 97.54% | 0.962299 |
| **100.0%** | 100.00% (1826/1826) | 0.00% | **0.0252** | **97.48%** | 0.617685 |

---

## 4. AURC Analysis & Scientific Interpretation

The Area Under the Risk-Coverage Curve was calculated using trapezoidal numerical integration via [`compute_aurc()`](file:///d:/daa/src/clindoc/m2/metrics.py#L210):

| Model / Configuration | Confidence Variable | AURC |
| :--- | :--- | :---: |
| **Stage B Alone (Calibrated)** | $P_{calibrated}$ ($T = 1.146522$) | **0.034600** |
| **Stage B Alone (Raw Softmax)** | $P_{raw}$ | **0.034700** |
| **Full Cascade (Naive Joint Confidence)** | $P_{cascade}$ | **0.054300** |

### Why Naive Joint Ranking Yields Higher AURC than Stage B Standalone
At first glance, AURC for the full cascade (0.0543) is higher than for Stage B standalone (0.0346), despite the cascade having **substantially higher overall accuracy (97.48% vs 95.51%)** and **substantially lower full-system risk (0.0252 vs 0.0449)**.

The mathematical cause is a standard phenomenon in hybrid LLM-classifier evaluation:
1. **Uncalibrated Confidence in Stage C:** All 381 Stage-C escalated claims received a nominal confidence of `1.0` from the LLM judge.
2. **Confidence Inversion at Low Coverage:** In naive joint ranking, these 381 escalated claims are placed at the very top of the ranking list (occupying the [0.0, 0.2087] coverage interval). Because Stage C has a 9.45% error rate (36 errors / 381 claims), the risk curve begins at `0.0945` rather than near `0.0053`.
3. In contrast, Stage B's calibrated probabilities cleanly segregate its very highest confidence predictions (accuracy 99.47% at 10% coverage).

### The Decisive Comparison: Architectural Routing vs. Confidence Thresholding
The proper operational comparison is between:
1. **Unassisted Stage B Abstention:** Selecting the top 1,445 claims (79.13% coverage) using Stage B's confidence scores.
   - Result: **61 errors, accuracy = 95.78%, risk = 0.0422**.
2. **Cascade Dual-Gate Routing:** Selecting the 1,445 claims resolved by the Stage A agreement + Stage B $\ge 0.60$ filter.
   - Result: **10 errors, accuracy = 99.31%, risk = 0.0069**.

```
Confidence Filtering (79.1% Coverage):  [========== 61 Errors ==========]  Risk: 0.0422
Cascade Gate Routing (79.1% Coverage):  [= 10 Errors =]                   Risk: 0.0069  (6.1x Lower Risk!)
```

This proves that **the cascade design earns its complexity**: Stage A + Stage B disagreement identifies 87.8% of Stage B's errors that confidence thresholding alone fails to catch.

---

## 5. Decision Resolution: Does the Cascade Provide a Useful Tradeoff?

**Conclusion: YES, decisively.**

1. **Computational & Cost Efficiency:**
   - 79.13% of claims are processed entirely locally by ModernBERT in milliseconds without GPU LLM overhead.
   - Only 20.87% of difficult edge cases are sent to Qwen-7B.
2. **Error Salvage on Hard Cases:**
   - On the 381 escalated claims, Stage B standalone collapses to 81.10% accuracy.
   - Stage C lifts this subset to 90.55% accuracy, recovering 36 claims that would otherwise have been misclassified.
3. **End-to-End System Quality:**
   - The cascade reduces benchmark errors from **82 down to 46**, achieving **97.48% accuracy** and a Discrepancy Recall of **0.9806**.

---

## 6. Audit Trail & Provenance

- **Diagnostic Source:** `experiments/V8-verification-cascade/phase4_cascade_diagnostics.json`
- **Output Artifacts:**
  - Markdown Report: [`experiments/V8-verification-cascade/V12_RISK_COVERAGE_ANALYSIS.md`](file:///d:/daa/experiments/V8-verification-cascade/V12_RISK_COVERAGE_ANALYSIS.md)
  - Machine-Readable JSON: [`experiments/V8-verification-cascade/v12_risk_coverage_analysis.json`](file:///d:/daa/experiments/V8-verification-cascade/v12_risk_coverage_analysis.json)
- **Codebase Modifications:** None (zero modifications to existing source code, models, prompts, thresholds, or benchmarks).
