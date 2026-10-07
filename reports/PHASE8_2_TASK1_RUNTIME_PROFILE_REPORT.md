# Phase 8.2 Task 1: Runtime Profile Containment Audit Report

**Status:** COMPLETE (Task 1 only; Phase 8.2 remains ongoing)  
**Execution Date:** 2026-10-04  
**Governing Architecture Documents:**  
- `PROJECT_SPEC.md` (§3, §10, §12, §13)  
- `PRODUCTIZATION_SPEC.md` (§3.6, §3.8, §10, §13: F-05, F-06, F-07)  
- `PRODUCTIZATION_DECISIONS.md` (PD-003, PD-004, PD-005, PD-018, PD-019)  

---

## 1. Executive Summary

This task implements **Phase 8.2 Task 1: Runtime Execution Profiles and Mock Containment** for the Clindoc2FHIR clinical verification pipeline.

Prior to this task, the repository exhibited a critical operational vulnerability: development defaults (specifically regex-based `MockLLMProvider`, heuristic keyword-based `DefaultMockLLMJudge`, and untracked heuristic logit fallbacks in `LearnedVerifier`) were wired as silent defaults across `VerificationCascade`, `ExtractionRouter`, and `clindoc process` CLI workflows. Under silent initialization, pipeline execution appeared operational but evaluated zero genuine neural models.

With the completion of Phase 8.2 Task 1:
1. Two explicit runtime profiles are formalized: `RuntimeProfile.DEV` and `RuntimeProfile.PRODUCTION`.
2. The runtime profile is selected via `CLINDOC_PROFILE` environment variable or explicit constructor/CLI arguments, defaulting safely to `dev` for offline research/testing.
3. In `PRODUCTION` profile, the pipeline enforces strict fail-closed invariants: any instantiation or invocation of mock extractors, mock judges, heuristic Stage B fallbacks, missing or uncalibrated temperature scalers, missing ModernBERT checkpoints, or unconfigured vLLM endpoints immediately raises a structured `ProductionProfileError`.
4. Dedicated factory builders (`build_production_cascade` and `build_production_router`) pin canonical artifacts and parameters (`modernbert_text_only_v3_fold0_seed42.pt`, $T = 1.146522$, $\tau = 0.60$).
5. A comprehensive suite of 18 new unit tests validates all containment boundaries, and all 65 regression tests across the CLI, verification cascade, and LLM judge pass without errors.

> [!IMPORTANT]
> **Scope Notice:** This report certifies completion of **Phase 8.2 Task 1 only**. Phase 8.2 (Pipeline Hardening) contains additional subsequent tasks (safe checkpoint loading without arbitrary code execution, provenance and runtime version stamping, and qualified-configuration replay invariance tests). Phase 8.2 is **not** complete.

---

## 2. Invariants Enforced by Profile

| Subsystem Component | `RuntimeProfile.DEV` Behavior | `RuntimeProfile.PRODUCTION` Behavior | Governing Decision |
|---|---|---|---|
| **Extraction Subsystem (`ExtractionRouter`)** | Allows `MockLLMProvider` and `MockGLiNERProvider` for offline unit testing without GPU. | **Strictly rejects** `MockLLMProvider` and `MockGLiNERProvider`. Validates that `VLLMProvider` is configured with a valid HTTP/HTTPS endpoint and non-empty model identifier. | PD-003 |
| **Stage B Verification (`LearnedVerifier`)** | Allows `custom_predictor=MockModelPredictor` and permits silent fallback to heuristic logit generation (`_heuristic_logits`) when neural checkpoint is absent. | **Strictly rejects** `MockModelPredictor`. Forbids `allow_heuristic_fallback=True`. Enforces existence of validated checkpoint file (`.pt`). Inference fails closed if neural model fails to load. | PD-004 |
| **Stage B Calibration (`TemperatureScaler`)** | Pre-fits an identity temperature grid if scaler is unfitted to allow immediate offline scaling. | Enforces that `scaler` is explicitly fitted (`is_fitted=True`) with a calibrated, positive temperature ($T > 0$). Rejects uncalibrated scalers at both initialization and inference. | PD-018 |
| **Stage C LLM Judge (`StageCJudge`)** | Defaults to `DefaultMockLLMJudge` if no client is supplied. | **Strictly rejects** `DefaultMockLLMJudge`. Requires explicit injection of a non-mock client (`VLLMJudgeClient`) with a validated HTTP/HTTPS endpoint. | PD-003, PD-019 |
| **Verification Cascade (`VerificationCascade`)** | Defaults `stage_b` and `stage_c` to lightweight dev instances. | Requires explicit injection of hardened, non-heuristic Stage B and non-mock Stage C instances initialized with `profile=PRODUCTION`. | PD-003, PD-004 |
| **CLI / Orchestrator (`clindoc process` / `batch`)** | Assembles lightweight dev pipeline if components are omitted. | Fails closed with exit code 1 if unconfigured default components or mock-derived configurations are executed under `CLINDOC_PROFILE=production`. | PD-003, PD-005 |

---

## 3. Implementation Details & File Manifest

### A. New File: `src/clindoc/config.py`
Establishes the central configuration and runtime profile contracts:
- `RuntimeProfile(str, Enum)`: Enumeration with `DEV = "dev"` and `PRODUCTION = "production"`, featuring case-insensitive string parsing.
- `ProductionProfileError(RuntimeError)`: Dedicated exception class raised whenever a production invariant is violated.
- `get_active_profile()`: Reads the active profile from the `CLINDOC_PROFILE` environment variable (default: `RuntimeProfile.DEV`).
- `set_active_profile(profile)`: Programmatic profile override for test harnesses and deployment initialization.
- Canonical constants pinned from the authoritative experimental data sheet:
  - `CANONICAL_MODERNBERT_CHECKPOINT`: `data/m2_results_v3/checkpoints/modernbert_text_only_v3_fold0_seed42.pt`
  - `CANONICAL_CALIBRATION_TEMPERATURE`: $1.146522$
  - `CANONICAL_STAGE_B_ESCALATION_THRESHOLD`: $0.60$
  - `CANONICAL_VLLM_MODEL_NAME`: `"Qwen/Qwen2.5-7B-Instruct"`
- `ProductionPipelineConfig`: Pydantic dataclass validating that checkpoint files exist, temperatures are positive, escalation thresholds are in $(0, 1)$, and vLLM endpoints use valid HTTP/HTTPS schemes.

### B. Modified File: `src/clindoc/verify/stage_b.py`
- Accepts `profile` and `allow_heuristic_fallback` in `LearnedVerifier.__init__`.
- Under `profile=PRODUCTION`:
  - Disallows `MockModelPredictor`.
  - Disallows `allow_heuristic_fallback=True`.
  - Requires non-null `model_path` pointing to an existing file.
  - Rejects unfitted temperature scalers.
  - In `verify()`, raises `ProductionProfileError` if scaler is not fitted or has non-positive temperature.
  - In `_compute_logits()`, raises `ProductionProfileError` if the neural model is not loaded, completely blocking heuristic fallback.

### C. Modified File: `src/clindoc/verify/stage_c.py`
- Accepts `profile` and `allow_mock_client` in `StageCJudge.__init__`.
- Under `profile=PRODUCTION`:
  - Disallows `client=None` (which would default to `DefaultMockLLMJudge`).
  - Disallows `DefaultMockLLMJudge`.
  - Validates `VLLMJudgeClient` parameters (valid HTTP/HTTPS scheme and non-empty `model_name`).

### D. Modified File: `src/clindoc/extract/router.py`
- Accepts `profile` in `ExtractionRouter.__init__`.
- Under `profile=PRODUCTION`:
  - Disallows `MockGLiNERProvider`.
  - Disallows `provider="mock"` in YAML or `MockLLMProvider` injection.
  - Validates `VLLMProvider` endpoint scheme and model name.

### E. Modified File: `src/clindoc/verify/cascade.py`
- Accepts `profile` in `VerificationCascade.__init__`.
- Under `profile=PRODUCTION`:
  - Disallows default `stage_b=None` or `stage_c=None`.
  - Validates that `stage_b` has `allow_heuristic_fallback=False` and `stage_c` has `allow_mock_client=False`.

### F. Modified File: `src/clindoc/cli.py`
- Implemented production builders:
  - `build_production_cascade(config)`: Assembles `DeterministicVerifier`, `LearnedVerifier` with canonical checkpoint and $T = 1.146522$, and `StageCJudge` with `VLLMJudgeClient`.
  - `build_production_router(config)`: Assembles `ExtractionRouter` with non-mock `VLLMProvider`.
- Updated `process_document` and `batch_process` to accept `profile` and enforce that `router` and `cascade` are explicitly configured production instances when `profile=PRODUCTION`.
- Added `--profile {dev,production}` CLI argument flag to `clindoc`.
- Handled `ProductionProfileError` in the CLI top-level exception handler to emit clear diagnostics with code 1.

### G. New File: `tests/test_runtime_profiles.py`
Authoritative test suite containing 18 unit tests validating all boundary conditions.

---

## 4. Test Verification Results

### A. New Profile Containment Test Suite (`tests/test_runtime_profiles.py`)
Executed with command:
```bash
.venv\Scripts\python.exe -m pytest tests/test_runtime_profiles.py -v
```

**Results:** `18 passed in 25.69s` (100% pass rate)

1. `test_dev_profile_allows_mock_llm`: PASSED
2. `test_dev_profile_allows_mock_stage_b_and_heuristics`: PASSED
3. `test_dev_profile_allows_default_cascade`: PASSED
4. `test_production_profile_rejects_mock_llm_extractor`: PASSED
5. `test_production_profile_rejects_mock_llm_judge`: PASSED
6. `test_production_profile_rejects_mock_stage_b_predictor`: PASSED
7. `test_production_profile_rejects_heuristic_fallback_flag`: PASSED
8. `test_production_profile_rejects_heuristic_fallback_at_inference`: PASSED
9. `test_production_profile_rejects_missing_modernbert_checkpoint`: PASSED
10. `test_production_config_rejects_missing_checkpoint`: PASSED
11. `test_production_profile_rejects_invalid_calibration_in_config`: PASSED
12. `test_production_profile_rejects_uncalibrated_scaler_in_verify`: PASSED
13. `test_production_profile_rejects_invalid_vllm_endpoint_in_stage_c`: PASSED
14. `test_production_profile_rejects_invalid_vllm_endpoint_in_router`: PASSED
15. `test_production_config_rejects_invalid_vllm_endpoint`: PASSED
16. `test_production_profile_accepts_valid_configuration`: PASSED
17. `test_cli_rejects_unconfigured_pipeline_in_production`: PASSED
18. `test_cli_environment_variable_enforces_production`: PASSED

### B. Core Subsystem Regression Test Suite
Executed with command:
```bash
.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_verification_cascade.py tests/test_stage_c_llm_judge.py tests/test_runtime_profiles.py -v
```

**Results:** `65 passed in 38.51s` (100% pass rate, 0 regressions)

- `tests/test_cli.py`: 7 passed
- `tests/test_verification_cascade.py`: 30 passed
- `tests/test_stage_c_llm_judge.py`: 10 passed
- `tests/test_runtime_profiles.py`: 18 passed

---

## 5. Next Steps for Phase 8.2

Phase 8.2 (Pipeline Hardening) continues with the remaining planned engineering tasks:
1. **Task 2: Safe Checkpoint Loading** (mitigating supply-chain / RCE risk by replacing `torch.load(weights_only=False)` with safe deserialization).
2. **Task 3: Version and Provenance Stamping** (embedding qualified configuration hashes, prompt versions, and model identifiers into execution logs and audit reports).
3. **Task 4: Qualified Replay Invariance Harness** (read-only verification against frozen diagnostic artifacts).
