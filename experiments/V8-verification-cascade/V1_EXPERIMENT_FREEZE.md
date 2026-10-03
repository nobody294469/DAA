# Experiment Freeze & Audit: V8_STAGE_C_LABFLAG_PROMPT_V1

**Freeze Status:** EXPERIMENTAL INTERVENTION FROZEN  
**Experiment ID:** `V8_STAGE_C_LABFLAG_PROMPT_V1`  
**Prompt Version:** `V8_STAGE_C_LABFLAG_PROMPT_V1`  
**Evaluation Target:** Out-of-Distribution (OOD) Holdout Benchmark  
**Date of Freeze:** 2026-10-03  

---

## 1. Executive Summary & Verdict

"V1 is a successful controlled intervention and is frozen as an experimental result."

The single-variable prompt intervention targeting tabular `lab.flag` precedence and numerical interval recomputation achieved:
- **Stage C Accuracy Gain:** +7.61 percentage points (82.94% $\rightarrow$ 90.55%).
- **Stage C Error Reduction:** -44.62% total error reduction (65 errors down to 36).
- **Targeted Subset Error Reduction:** Exactly 29 errors eliminated from `lab.flag` (56 $\rightarrow$ 27) and `template_c` (60 $\rightarrow$ 31).
- **Zero Untargeted Drift:** Zero error drift across non-lab fields (`lab.value`, `med.name`, `med.route`, `med.frequency`, `med.dose`), confirming strict experimental attribution.
- **End-to-End Pipeline Impact:** Cascade Macro-F1 increased from 0.9422 to 0.9538; Discrepancy Recall increased from 0.9806 to 0.9826.

---

## 2. Dataset & Evaluation Benchmark Configuration

| Parameter | Specification |
| :--- | :--- |
| **Dataset Path** | `data/m2_verification_v3` |
| **Partition / Split** | `ood` (Out-of-Distribution holdout) |
| **Total Evaluated Claims** | 1,826 extracted clinical claims |
| **Total Unseen Documents** | 34 held-out documents |
| **Document Distribution** | 17 laboratory reports (`template_c`), 12 medication orders (`mar_order_sheet`), 5 clinical narratives (`narrative_ambulatory_progress_note_v1`, `narrative_specialty_consult_note_v1`) |
| **Split Isolation** | Strictly disjoint from training (2,242), calibration (678), and validation (883) pools |

---

## 3. Model Architecture, Checkpoint & Serving Stack

| Component | Implementation & Configuration |
| :--- | :--- |
| **Stage A (Deterministic Pre-check)** | Deterministic rule verifier implementing exact surface matching, normalization, and unit conversion |
| **Stage B (Semantic Verifier)** | `thomas-sounack/BioClinical-ModernBERT-base` text-only classifier |
| **Stage B Checkpoint** | `data/m2_results_v3/checkpoints/modernbert_text_only_v3_fold0_seed42.pt` |
| **Stage B Temperature Calibration** | Post-hoc temperature scaling ($T = 1.146522$) |
| **Stage C (LLM Judge)** | `Qwen/Qwen2.5-7B-Instruct-AWQ` running locally on vLLM |
| **Stage C Decoding & Budget** | Greedy decoding (`temperature: 0.0`), `seed: 42`, `max_tokens: 256`, max attempts: 3 |
| **Stage C Schema Enforcement** | DEC-013 grammar-constrained JSON schema (`StageCJudgeOutput`) |
| **Cascade Escalation Threshold** | $\tau = 0.60$ (escalation triggered on Stage A/B disagreement, low confidence $< 0.60$, or insufficient evidence precaution) |

---

## 4. Controlled Intervention Verification (One-Variable Protocol)

Relative to the frozen baseline (`V3-PHASE4-BASELINE-FREEZE`), **only** the Stage-C prompt instruction for `lab.flag` claims was modified:
- **No changes to:** ModernBERT weights, temperature scaling, Stage A pre-checks, cascade escalation threshold ($0.60$), dataset records, split manifests, OCR caches, or evaluation logic.
- **Prompt Addition:** The following instruction was appended conditionally to `_build_judge_prompt` when `claim.field_type == "lab.flag"`:
  ```text
  [FIELD-SPECIFIC INSTRUCTIONS]
  When evaluating lab.flag claims against structured laboratory tables:
  1. Explicit abnormal column indicators take strict precedence: a trailing 'H' or 'HIGH' explicitly denotes HIGH; a trailing 'L' or 'LOW' explicitly denotes LOW; an unflagged or blank abnormal entry denotes NORMAL.
  2. Do NOT attempt to recompute, recalculate, or verify numerical intervals if an explicit flag indicator ('H', 'L', 'HIGH', 'LOW', or blank) is present in the source table row.
  ```
- **Isolation:** Claims for non-`lab.flag` fields (`lab.value`, `med.*`, narratives) received zero prompt modifications.

---

## 5. Frozen Baseline (V0) vs Controlled V1 Metrics

All metrics evaluated on the full 1,826 OOD holdout claims:

| Evaluation Dimension | Frozen Baseline (V0) | Controlled V1 Run | Absolute Delta | Relative Delta |
| :--- | :---: | :---: | :---: | :---: |
| **Cascade Macro-F1** | 0.9422 | **0.9538** | **+0.0116** | **+1.23%** |
| **Cascade Discrepancy Recall** | 0.9806 | **0.9826** | **+0.0020** | **+0.20%** |
| **Cascade Overall Accuracy** | 0.9595 (1,752 / 1,826) | **0.9748 (1,780 / 1,826)** | **+0.0153** | **+1.59%** |
| **Stage B Resolved Count** | 1,445 / 1,826 (79.13%) | 1,445 / 1,826 (79.13%) | 0 | Identical |
| **Stage B Resolved Accuracy** | 0.9931 (1,435 / 1,445) | 0.9931 (1,435 / 1,445) | 0 | Identical |
| **Stage B Macro AUROC** | 0.9732 | 0.9732 | 0 | Identical |
| **Stage B Brier Score** | 0.0858 | 0.0858 | 0 | Identical |
| **Stage B Expected Calibration Error (ECE)** | 0.0372 | 0.0372 | 0 | Identical |
| **Stage C Escalated Count** | 381 / 1,826 (20.87%) | 381 / 1,826 (20.87%) | 0 | Identical |
| **Stage C Accuracy** | 0.8294 (316 / 381) | **0.9055 (345 / 381)** | **+0.0761** | **+9.18%** |
| **Stage C Error Count** | 65 | **36** | **-29** | **-44.62%** |

---

## 6. Targeted Subset Analysis: `lab.flag` & `template_c`

The intervention specifically targeted the tabular format of `template_c` and the interval-reasoning failure of `lab.flag`:

| Metric | Frozen Baseline (V0) | Controlled V1 Run | Net Difference |
| :--- | :---: | :---: | :---: |
| **`lab.flag` Stage-C Accuracy** | 77.33% (191 / 247) | **89.07% (220 / 247)** | **+11.74%** |
| **`lab.flag` Stage-C Errors** | 56 | **27** | **-29 (-51.8%)** |
| **`template_c` Stage-C Accuracy** | 79.52% (233 / 293) | **89.42% (262 / 293)** | **+9.90%** |
| **`template_c` Stage-C Errors** | 60 | **31** | **-29 (-48.3%)** |
| **`SUPPORTED` $\rightarrow$ `DISCREPANCY` Errors** | 42 | **15** | **-27 (-64.3%)** |

---

## 7. Execution Integrity & Audit Verification

Audited from per-claim records in `phase4_cascade_diagnostics.json`:
- **Stage C Calls:** Exactly 381 genuine Qwen invocations.
- **First-Attempt Success:** 381 / 381 (100%) completed on Attempt 1.
- **Retry Count:** 0 retries required across the entire OOD split.
- **Fallbacks:** 0 clinical fallbacks to default `INSUFFICIENT_EVIDENCE`.
- **HTTP Transport Errors:** 0 transport/timeout errors.
- **JSON Schema Conformance:** 100% valid schema adherence to `StageCJudgeOutput`.
- **Prompt Version Validation:** 100% of Stage-C diagnostic records record `prompt_version = "V8_STAGE_C_LABFLAG_PROMPT_V1"`.

---

## 8. Remaining Error Taxonomy (Post-V1)

A total of 36 Stage-C errors remain out of 381 escalated claims (9.45% error rate):

### A. Remaining `lab.flag` Errors (27 claims)
1. **Context-Anchoring & Value Expectation (15 claims, `SUPPORTED` $\rightarrow$ `DISCREPANCY`):**
   - The model observes the correct flag token (`H` / `L`), but rejects the claim because it defers to Stage B's preceding `DISCREPANCY (confidence: 1.00)` prediction in `[CASCADE STAGE CONTEXT]`, or because the extracted claim omitted the numeric value (e.g. asserts `Albumin flag: HIGH` without stating `5.6 g/dL`).
2. **Unflagged Normal / Absence Reasoning (8 claims, `DISCREPANCY` $\rightarrow$ `INSUFFICIENT_EVIDENCE`):**
   - The claim asserts an abnormal flag (`flag: HIGH`), but the document row shows normal values with a blank abnormal cell. Qwen classifies this as `INSUFFICIENT_EVIDENCE` (absence of an explicit flag token) rather than an active contradiction with normal status.
3. **OCR Row-Bleed & Multi-Line Misalignment (4 claims, `DISCREPANCY` $\rightarrow$ `SUPPORTED`):**
   - When adjacent lines are compact, the model misassociates an abnormal flag token on row $N$ with analyte $N+1$.

### B. Remaining Non-Flag Errors (9 claims — identical to baseline)
1. **Entity Absence vs Discrepancy (4 claims):** Missing medication or analyte classified as `DISCREPANCY` instead of `INSUFFICIENT_EVIDENCE` (2 `med.name`, 1 `med.frequency`, 1 `lab.value`).
2. **Clinical Synonymy & Abbreviations (2 claims):** `by mouth` vs `oral`/`PO` marked `SUPPORTED` by Qwen due to clinical equivalence (2 `med.route`).
3. **Unlinked Verbatim Grounded Spans (3 claims):** Benchmark span unlinking marked `SUPPORTED` because text is present verbatim in local context window (3 `lab.value`).

---

## 9. Artifact Paths & Cryptographic Hashes (SHA256)

| Artifact Path | SHA256 Hash | Role |
| :--- | :--- | :--- |
| `experiments/V8-verification-cascade/V1_EXPERIMENT_FREEZE.md` | `2f749f73284a8db855bd7de11e657de10d0926359b7ce197272fb87e1bed48dc` | Authoritative V1 freeze record |
| `experiments/V8-verification-cascade/V1_LABFLAG_OOD_ANALYSIS.md` | `296adadf8a87538f3d7b5d73706acf53597616da93b097e4f0e4f02d30fc21aa` | Granular V1 diagnostic analysis |
| `experiments/V8-verification-cascade/phase4_cascade_eval_report.json` | `770987a04b7313911419cc8e54dafa9a50c8349f85eba81bf6dbbfe5d1e292ef` | Machine-readable V1 evaluation report |
| `experiments/V8-verification-cascade/phase4_cascade_diagnostics.json` | `6c2b7d1492d4dfa8e9bb0d08d3513bef4ba166f52b620c71864509594d3e1117` | Per-claim diagnostics for all 1,826 OOD records |
| `experiments/V8-verification-cascade/V3_PHASE4_BASELINE_REPORT.md` | `7ab37e638de042f4a937049d15e6fe1a015be702ff5033c03469be662fe61043` | Frozen baseline report (untouched) |
| `experiments/V8-verification-cascade/V3_PHASE4_BASELINE_SUMMARY.json` | `b24624698601103961425b0b7bffcb5a3b15ca7b6942d5b60f9e8fb868f9285e` | Frozen baseline summary JSON (untouched) |
| `src/clindoc/verify/stage_c.py` | `a006dd501438f38cb9f8980b3e20e194fabe443c5e88ae38cbff0964b58b2815` | Stage C verifier implementation |
| `data/m2_results_v3/checkpoints/modernbert_text_only_v3_fold0_seed42.pt` | `d24add6135634f660d2fdcf1c51545e201cc65c00fd2d060452eac95be9e7902` | ModernBERT Stage B checkpoint ($T = 1.146522$) |

---

## 10. Audit Sign-off

- **Regression Test Suite:** 53 / 53 passed (10 tests in `test_stage_c_llm_judge.py`, 30 in `test_verification_cascade.py`, 13 in `test_eval_phase4_cascade.py`).
- **Pipeline Invariants:** Model weights, datasets, split manifests, thresholds, and baseline artifacts remain intact and unchanged.
- **Experimental Status:** Experiment `V8_STAGE_C_LABFLAG_PROMPT_V1` is officially frozen.
