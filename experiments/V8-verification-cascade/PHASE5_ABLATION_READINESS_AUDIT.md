# Phase 5 / Baseline / Ablation Experiments Readiness Audit

**Audit Date:** 2026-10-03  
**Auditor:** Antigravity Autonomous Pair Programmer  
**Authority:** `PROJECT_SPEC.md` (§12, §15–§17, §21, §22, §25, §26)  
**Repository State:** Frozen Post-V1 Baseline (`V8_STAGE_C_LABFLAG_PROMPT_V1` frozen)  
**Strict Policy:** Audit only — no code, dataset, checkpoint, threshold, or existing artifact was modified.

---

## 1. Executive Summary

This audit evaluates the codebase to establish exactly which baseline, ablation, and Phase 5 experiments are already implemented, runnable, completed, or pending.

### High-Level Summary
- **Verification Cascade (V8):** Fully implemented, evaluated, and frozen on both Validation (883 claims) and OOD (1,826 claims) across Baseline V0 and Prompt V1.
- **Stage C Alone (V9 / Always-Stage-C):** **FULLY IMPLEMENTED & RUNNABLE NOW** via `eval/eval_phase4_cascade.py --always-stage-c --stage-c-client vllm`.
- **Stage B Alone (V6):** **FULLY IMPLEMENTED & RUNNABLE NOW** via `clindoc.m2.recovery.recover_and_evaluate_checkpoint()` or directly from existing evaluation probability outputs.
- **Selective Prediction / Risk-Coverage Analysis (V12):** **FULLY IMPLEMENTED & RUNNABLE NOW** using `clindoc.m2.metrics.compute_risk_coverage()` and existing persisted diagnostics.
- **Classical Baselines (V1–V5):** **COMPLETED & PERSISTED** in `data/m2_results/m2_baseline_results.json` and reported in `experiments/`.
- **Extraction Experiments (E1–E5):** **FULLY IMPLEMENTED** via `src/clindoc/extract/evaluate.py` and `src/clindoc/degrade/robustness.py`.
- **Phase 5 (FHIR Bundle & Audit Report Generation):** **FULLY IMPLEMENTED & TESTED** via `src/clindoc/fhir/runner.py`, `src/clindoc/audit/report.py`, and `src/clindoc/cli.py`.

---

## 2. Targeted Verification & Ablation Configurations

### 2.1 Stage B / ModernBERT Alone
- **Experiment ID:** V6 (Text-Only Fine-Tuned ModernBERT) / ModernBERT Standalone Evaluation
- **Purpose:** Establish the standalone performance, calibration, and failure modes of the fine-tuned BioClinical ModernBERT model without any deterministic Stage A pre-filtering or Stage C LLM escalation.
- **Implementation / Script:**
  - Inference & Evaluation: `clindoc.m2.recovery.recover_and_evaluate_checkpoint()` in `src/clindoc/m2/recovery.py`.
  - Batch / Pipeline: `src/clindoc/m2/train.py:evaluate_model_on_split()`.
  - Also embedded within `eval/eval_phase4_cascade.py`: `stage_b_probabilities` computes full-split AUROC, Brier, and ECE across all 1,826 claims.
- **Dataset / Split:** `data/m2_verification_v3` (Validation: 883 claims; OOD: 1,826 claims).
- **Status:** **IMPLEMENTED**
- **Safe to Run Now:** **YES**. Checkpoint `data/m2_results_v3/checkpoints/modernbert_text_only_v3_fold0_seed42.pt` is present, verified, and calibrated ($T = 1.146522$). No external network or vLLM dependency.
- **Expected Compute / Runtime:** ~30–45 seconds on GPU (T4/RTX) or ~2–3 minutes on CPU for the full 1,826 OOD claims.

---

### 2.2 Stage C / Qwen Alone (Always-Stage-C / V9)
- **Experiment ID:** V9 (`llm_verifier_baseline`) / Always-Stage-C Ablation
- **Purpose:** Evaluate `Qwen/Qwen2.5-7B-Instruct-AWQ` acting as the sole verifier across 100% of claims, measuring the accuracy, schema conformance, and financial/compute cost of an un-cascaded LLM versus the selective cascade.
- **Implementation / Script:**
  - Script: `eval/eval_phase4_cascade.py` with arguments:
    `--always-stage-c --stage-c-client vllm --split ood` (or `--split val`)
  - Cascade routing logic (`src/clindoc/verify/cascade.py:141, 181`): When `always_stage_c=True`, Stage A pre-checks are bypassed for Tier 2 fields, and `should_escalate` is forced to `True` for every claim.
- **Dataset / Split:** `data/m2_verification_v3` (`val` = 883 claims, `ood` = 1,826 claims).
- **Status:** **IMPLEMENTED**
- **Safe to Run Now:** **YES**. Local vLLM server is active and serving `Qwen/Qwen2.5-7B-Instruct-AWQ` on port 8000.
- **Expected Compute / Runtime:** ~1.2s per claim (~15–18 minutes for 883 validation claims; ~35–45 minutes for 1,826 OOD claims).

---

### 2.3 Selective Stage B $\rightarrow$ Stage C Cascade (without Stage A)
- **Experiment ID:** Stage A Ablation (Cascade B $\rightarrow$ C)
- **Purpose:** Isolate the contribution of Stage A (deterministic rule pre-check) to cascade accuracy, latency, and escalation rates.
- **Implementation / Script:**
  - Current Status: In `src/clindoc/verify/cascade.py:169`, Stage A is hardcoded to run before Stage B, and Stage B receives `stage_a_res.label` to evaluate Rule A escalation (`stage_a_disagreement`).
  - While `always_stage_c` exists, there is currently no `--no-stage-a` or `--disable-stage-a` argument in `eval/eval_phase4_cascade.py`.
- **Dataset / Split:** `data/m2_verification_v3` (`val`, `ood`).
- **Status:** **PARTIALLY IMPLEMENTED**
- **Safe to Run Now:** **NO** (Requires exposing a configuration parameter or flag in `cascade.py` to bypass Stage A without modifying Stage B / C).

---

### 2.4 Multi-Stage Cascade Variants (Threshold Sweeps & Backends)
- **Experiment ID:** V8 Sensitivity Sweeps (DEC-005 / DR-5)
- **Purpose:** Characterize the trade-off curve between Stage C escalation volume and cascade accuracy as the confidence threshold $\tau$ varies.
- **Implementation / Script:**
  - Script: `eval/eval_phase4_cascade.py --escalation-threshold <tau> --stage-c-client <mock|vllm> --split <val|ood>`.
- **Dataset / Split:** `data/m2_verification_v3` (`val`, `ood`).
- **Status:** **IMPLEMENTED**
- **Safe to Run Now:** **YES**. Can run immediately with arbitrary thresholds (e.g. $\tau \in \{0.40, 0.50, 0.70, 0.80\}$).
- **Expected Compute / Runtime:** ~5–15 minutes on OOD depending on escalation rate.

---

## 3. Comprehensive Audit of Canonical Experiments (PROJECT_SPEC §22)

### 3.1 Extraction Experiments (E1–E5)

| ID | Description | Primary Module / Script | Target Dataset / Splits | Current Status | Safe to Run Now? | Expected Runtime |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **E1** | Deterministic-Only Extraction | `src/clindoc/extract/evaluate.py` (`run_extraction_evaluation("deterministic")`) | CV & OOD Documents (120 docs) | **IMPLEMENTED** | **YES** | ~15–30 seconds |
| **E2** | GLiNER-BioMed-Only Extraction | `src/clindoc/extract/evaluate.py` (`run_extraction_evaluation("gliner")`) | CV & OOD Documents | **IMPLEMENTED** | **YES** (w/ mock or local weights) | ~1–2 minutes |
| **E3** | LLM-Only Narrative Extraction | `src/clindoc/extract/evaluate.py` (`run_extraction_evaluation("llm_narrative")`) | Narrative Documents (15 docs) | **IMPLEMENTED** | **YES** (via active vLLM) | ~3–5 minutes |
| **E4** | Hybrid Extraction Router (Production) | `src/clindoc/extract/evaluate.py` (`run_extraction_evaluation("hybrid")`) | All Document Types (120 docs) | **IMPLEMENTED** | **YES** | ~4–6 minutes |
| **E5** | Robustness Degradation Matrix | `src/clindoc/degrade/robustness.py` (`run_e5_robustness_benchmark()`) | Degraded Scans (Tracks A & B) | **IMPLEMENTED** | **YES** | ~5–10 minutes |

---

### 3.2 Verification Experiments (V1–V13)

| ID | Description | Implementation Script | Dataset / Split | Status | Safe Now? | Expected Runtime |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **V1** | Deterministic Verifier Alone | `src/clindoc/m2/baselines.py` (`predict_deterministic`) | CV (5 folds) + OOD | **COMPLETED** | **YES** (Persisted) | Already completed |
| **V2** | Levenshtein Threshold | `src/clindoc/m2/baselines.py` (`predict_levenshtein`) | CV (5 folds) + OOD | **COMPLETED** | **YES** (Persisted) | Already completed |
| **V3** | TF-IDF + Logistic Regression | `src/clindoc/m2/baselines.py` (`predict_tfidf_lr`) | CV (5 folds) + OOD | **COMPLETED** | **YES** (Persisted) | Already completed |
| **V4** | Embedding Cosine Similarity | `src/clindoc/m2/baselines.py` (`predict_embedding_similarity`) | CV (5 folds) + OOD | **COMPLETED** | **YES** (Persisted) | Already completed |
| **V5** | Zero-Shot PubMedBERT NLI | `src/clindoc/m2/baselines.py` (`predict_zeroshot_nli`) | CV (5 folds) + OOD | **COMPLETED** | **YES** (Persisted) | Already completed |
| **V6** | ModernBERT Fine-Tuned (Text-Only) | `src/clindoc/m2/recovery.py` (`recover_and_evaluate_checkpoint`) | Validation + OOD | **IMPLEMENTED & TRAINED** | **YES** | ~45 seconds |
| **V7** | ModernBERT OCR-Aware (AUX18) | `src/clindoc/m2/train.py` (`aux_mode="full"`) | CV (5 folds) + OOD | **PARTIALLY IMPLEMENTED** | **NO** (Training needed) | ~25–35 min training |
| **V8** | Full Production Cascade (A $\rightarrow$ B $\rightarrow$ C) | `eval/eval_phase4_cascade.py` | Validation + OOD | **COMPLETED & FROZEN** | **YES** (Baseline & V1 frozen) | Completed |
| **V9** | LLM-Only Verifier (Stage C Alone) | `eval/eval_phase4_cascade.py --always-stage-c` | Validation + OOD | **IMPLEMENTED** | **YES** | ~35–45 min on OOD |
| **V10** | Template Holdout Across V1–V9 | Consolidated OOD evaluation scripts | OOD (1,826 claims) | **PARTIALLY COMPLETED** | **YES** (V1–V5 & V8 done; V6 & V9 ready) | Variable |
| **V11** | Auxiliary Feature Ablation on V7 | `src/clindoc/m2/train.py` (`aux_mode` variants) | CV (5 folds) | **IMPLEMENTED CODE** | **NO** (Blocked on V7) | ~1.5 hours training |
| **V12** | Selective Prediction / Risk-Coverage | `src/clindoc/m2/metrics.py` (`compute_risk_coverage`) | OOD (V6 vs V8) | **IMPLEMENTED** | **YES** (Instant from diag) | ~2–5 seconds |
| **V13** | LoRA-Fine-Tuned LLM Verifier | None (Marked optional in §22.3) | N/A | **NOT IMPLEMENTED** | **NO** | N/A |

---

## 4. Phase 5 Implementation Readiness (FHIR + Provenance + Audit Report)

Per `PROJECT_SPEC.md §25` (Phase 5: FHIR Generation + Provenance + Audit Report) and acceptance criteria in §26, the components are already built and verified in the repository:

### 4.1 Implemented Phase 5 Modules
1. **FHIR R4B Bundle Generator & Mapper:**  
   - [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py): Maps extracted lab and medication claims to canonical FHIR R4B resources (`Observation`, `MedicationStatement`, `DiagnosticReport`, `Patient`, `Practitioner`).
   - Implements two-tier FHIR provenance extensions conforming strictly to DEC-016 and §16.
2. **FHIR R4B Validator:**  
   - [`src/clindoc/fhir/validator.py`](file:///d:/daa/src/clindoc/fhir/validator.py): 100% deterministic schema validation against the official `fhir.resources.R4B` package.
3. **Audit Report Generator (§7.8):**  
   - [`src/clindoc/audit/report.py`](file:///d:/daa/src/clindoc/audit/report.py): Generates standalone JSON audit reports conforming strictly to `schemas/audit_report.json`.
4. **End-to-End Orchestrator & CLI:**  
   - [`src/clindoc/cli.py`](file:///d:/daa/src/clindoc/cli.py): CLI interface supporting single-document (`clindoc process`) and batch processing (`clindoc batch`), producing both `fhir_bundle.json` and `audit_report.json`.

### 4.2 Phase 5 Readiness Verdict
- **Status:** **FULLY IMPLEMENTED & TESTED**
- **Test Coverage:** All 18 unit tests in `tests/test_audit_report.py` and `tests/test_canonical_fhir_mapping.py` are passing.
- **Runnable Command:**  
  `python -m clindoc.fhir.runner` executes batch FHIR conversion and validation across extracted documents.

---

## 5. Recommended Immediate Next Step Options

Based on this audit, three distinct experimental paths are completely implemented, verified, and safe to execute immediately without modifying any code:

1. **Option 1: Selective Prediction & Risk-Coverage Analysis (V12)**
   - *Rationale:* Essential milestone under `PROJECT_SPEC.md §21.5` and §22.2.
   - *Feasibility:* 100% ready now. Both V6 (ModernBERT alone) and V8 (Cascade) probability/confidence outputs already exist in `phase4_cascade_diagnostics.json`.
   - *Compute/Time:* Near instantaneous (<10 seconds).

2. **Option 2: Standalone LLM Judge Baseline (V9 / Always-Stage-C)**
   - *Rationale:* Resolves the remaining core baseline comparison in §22.2: How does the selective cascade compare against an unconstrained, un-cascaded LLM verifier on the identical OOD split?
   - *Feasibility:* 100% ready now via `eval/eval_phase4_cascade.py --always-stage-c --stage-c-client vllm --split ood`.
   - *Compute/Time:* ~35–45 minutes on local vLLM.

3. **Option 3: Advance to Phase 5 Pipeline Validation**
   - *Rationale:* Transition from Phase 4 verification to Phase 5 FHIR generation, testing whether verified claims and provenance extensions map cleanly to FHIR R4B bundles and audit reports across benchmark documents.
   - *Feasibility:* 100% ready now via existing CLI and FHIR runner.
