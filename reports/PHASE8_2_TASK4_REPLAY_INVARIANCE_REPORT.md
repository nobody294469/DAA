# Phase 8.2 Task 4: Stage-B Qualified Configuration Replay Invariance Audit Report

- **Date:** 2026-10-04
- **Auditor:** Phase 8.2 Model Serving & Invariance Auditor
- **Status:** PASS
- **Target Specification:** [PRODUCTIZATION_SPEC.md](file:///d:/daa/PRODUCTIZATION_SPEC.md) §3.6, §3.8, §8.2 (Task 4)
- **Architectural Decisions:** [PRODUCTIZATION_DECISIONS.md](file:///d:/daa/PRODUCTIZATION_DECISIONS.md) PD-004, PD-005, PD-018, PD-019
- **Authoritative Technical Spec:** [PROJECT_SPEC.md](file:///d:/daa/PROJECT_SPEC.md) §3, §10, §12, §13, §17.1
- **Machine-Readable Replay Results:** [reports/phase4_stage_b_replay_results.json](file:///d:/daa/reports/phase4_stage_b_replay_results.json)
- **Replay Invariance Test Suite:** [tests/test_replay_invariance.py](file:///d:/daa/tests/test_replay_invariance.py)

---

## 1. Executive Summary

Phase 8.2 Task 4 specifies the implementation of a deterministic **Qualified Configuration Replay Harness** for Stage-B neural verification. This harness mathematically verifies that the qualified clinical document verification pipeline produces invariant decisions and probabilities against the frozen canonical benchmark dataset without numerical drift.

### Replay Summary Across Full OOD Benchmark Split (1,826 Claims)

| Metric | Replay Result | Target / Criterion | Status |
| :--- | :--- | :--- | :--- |
| **Total Frozen Benchmark Records** | 1,826 claims | 1,826 claims | COMPLETE |
| **Replayable Records** | 1,826 claims (100.0%) | 1,826 claims (100.0%) | PASS |
| **Non-Replayable Records** | 0 claims (0.0%) | 0 claims | PASS |
| **Exact Decision Matches** | **1,826 / 1,826 (100.00%)** | 100.0% label match | **PASS** |
| **Matches Within Tolerance** | **1,826 / 1,826 (100.00%)** | 100.0% within $\epsilon = 10^{-4}$ | **PASS** |
| **Label Mismatches** | 0 | 0 | PASS |
| **Escalation Mismatches** | 0 | 0 | PASS |
| **Numerical Drift Count** | 0 | 0 | PASS |
| **Maximum Confidence Drift ($\Delta p$)** | **0.0000000000** | $< 0.0001$ | **ZERO DRIFT** |
| **Mean Confidence Drift** | **0.0000000000** | $< 0.0001$ | **ZERO DRIFT** |
| **Overall Replay Verification Status** | **PASS** | PASS | **PASS** |

---

## 2. Artifact Integrity and Cryptographic Provenance

Before executing replay evaluation, the harness cryptographically validates all pinned artifacts against authoritative SHA-256 hashes recorded in [experiments/FINAL_REPORT_DATA_SHEET.md](file:///d:/daa/experiments/FINAL_REPORT_DATA_SHEET.md) and [PROJECT_SPEC.md](file:///d:/daa/PROJECT_SPEC.md):

1. **Frozen Diagnostics Dataset (`phase4_cascade_diagnostics.json`):**
   - Expected SHA-256: `6c2b7d1492d4dfa8e9bb0d08d3513bef4ba166f52b620c71864509594d3e1117`
   - Verified SHA-256: `6c2b7d1492d4dfa8e9bb0d08d3513bef4ba166f52b620c71864509594d3e1117`
   - Status: **VERIFIED**

2. **Stage B Neural Checkpoint (`modernbert_text_only_v3_fold0_seed42.pt`):**
   - Expected SHA-256: `d24add6135634f660d2fdcf1c51545e201cc65c00fd2d060452eac95be9e7902`
   - Verified SHA-256: `d24add6135634f660d2fdcf1c51545e201cc65c00fd2d060452eac95be9e7902`
   - Loading Mechanism: `clindoc.verify.stage_b.safe_load_checkpoint` (`weights_only=True` via PyTorch safe unpickler)
   - Status: **VERIFIED**

3. **Qualified Manifest (`QualifiedManifest.create_canonical_v1()`):**
   - Manifest Version: `1.0.0`
   - Manifest Digest: `7a5cb9f4d471b91704aafd00bd391b7ec14564cb99c2ffe52d54e87ea6d15b53`
   - Temperature Parameter $T$: `1.146522`
   - Escalation Threshold: `0.60`
   - Status: **VERIFIED**

---

## 3. Explicit Stage-C Historical Limitation

Per **PD-019** and [PRODUCTIZATION_SPEC.md](file:///d:/daa/PRODUCTIZATION_SPEC.md) §3.6 / §3.8:

> **Formal Limitation Notice:**
> The audited Phase 4 verification cascade evaluations utilized an external vLLM instance serving `Qwen/Qwen2.5-7B-Instruct-AWQ` on Google Colab. The upstream HuggingFace Git revision hash and exact GPU kernel scheduling were not captured in `phase4_cascade_diagnostics.json`.
>
> Therefore, Stage C LLM generation is explicitly documented as **NOT QUALIFIED for bitwise historical reproduction**. Stage B neural verification remains 100% locally deterministic, cryptographically pinned, and empirically verifiable.

Both the replay CLI and machine-readable output record this limitation under `stage_c_historical_limitation`.

---

## 4. Test Suite and Verification

The accompanying automated test suite [tests/test_replay_invariance.py](file:///d:/daa/tests/test_replay_invariance.py) verifies:
1. `test_frozen_diagnostics_artifact_integrity`: Asserts canonical diagnostics file exists and matches pinned SHA-256.
2. `test_replay_harness_rejects_corrupted_diagnostics`: Asserts fail-closed behavior (`ProductionProfileError`) when fed tampered diagnostic records.
3. `test_replay_harness_executes_on_subset_and_produces_valid_structure`: Asserts end-to-end replay on subset with strict JSON schema compliance.
4. `test_non_replayable_handling`: Asserts malformed records are safely captured as `NON_REPLAYABLE` without crashing the pipeline.

```text
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0 -- D:\daa\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\daa
configfile: pyproject.toml
plugins: anyio-4.15.1
collected 4 items

tests/test_replay_invariance.py::test_frozen_diagnostics_artifact_integrity PASSED [ 25%]
tests/test_replay_invariance.py::test_replay_harness_rejects_corrupted_diagnostics PASSED [ 50%]
tests/test_replay_invariance.py::test_replay_harness_executes_on_subset_and_produces_valid_structure PASSED [ 75%]
tests/test_replay_invariance.py::test_non_replayable_handling PASSED     [100%]

============================= 4 passed in 23.35s ==============================
```

---

## 5. Artifacts and Source Links

- Replay Harness Source: [eval/replay_qualified_stage_b.py](file:///d:/daa/eval/replay_qualified_stage_b.py)
- Replay Invariance Test Suite: [tests/test_replay_invariance.py](file:///d:/daa/tests/test_replay_invariance.py)
- Machine-Readable JSON Replay Report: [reports/phase4_stage_b_replay_results.json](file:///d:/daa/reports/phase4_stage_b_replay_results.json)
