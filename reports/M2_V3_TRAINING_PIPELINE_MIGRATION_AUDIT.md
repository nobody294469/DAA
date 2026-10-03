# M2 V3 Training Pipeline Migration Audit Report

**Date**: 2026-10-02  
**Target Authoritative Dataset**: `data/m2_verification_v3/`  
**Historical Benchmark**: `data/m2_verification/` (Preserved byte-for-byte, read-only)  
**Objective**: Audit the entire Stage B training and evaluation pipeline, update all references to use `data/m2_verification_v3/`, eliminate hardcoded assumptions of the legacy 9,168-example count, and verify partition counts in the active data loaders.

---

## A. Executive Summary

1. **Authoritative Benchmark Migration**:
   - `data/m2_verification_v3/` is now the sole default benchmark path across the entire M2 training and evaluation runtime (`src/clindoc/m2/run.py` and `src/clindoc/m2/recovery.py`).
   - A configurable CLI parameter `--data-dir` was added to both `run.py` and `recovery.py` with default pointing explicitly to `data/m2_verification_v3/`.
2. **Historical Dataset Preservation**:
   - The historical canonical dataset at `data/m2_verification/` was NOT modified or deleted in any way.
   - Its cryptographic SHA256 checksums remain identical to the pre-audit state.
3. **V3 Dataset Preservation**:
   - The certified V3 benchmark files in `data/m2_verification_v3/` were opened in strictly read-only mode and remain bit-for-bit unchanged.
4. **Data Loader Verification**:
   - The Stage B partition loader reads exact split counts from `data/m2_verification_v3/`:
     - **Train**: 2,242 examples (Expected: 2,242)
     - **Calibration**: 678 examples (Expected: 678)
     - **Validation**: 883 examples (Expected: 883)
     - **OOD Holdout**: 1,826 examples (Expected: 1,826)
     - **Total**: 5,629 examples (Expected: 5,629)
5. **No Model Training Executed**:
   - Zero neural model weights were trained or evaluated.
   - Zero checkpoints were written.
   - Phase 4 cascade evaluation was NOT executed.

---

## B. Audit of Old Dataset References Across the Codebase

A complete audit of `src/` and `tests/` identified the following references to `data/m2_verification` and legacy assumptions:

| File | Line | Previous Reference / Assumption | Migration Action Taken |
| :--- | :--- | :--- | :--- |
| `src/clindoc/m2/run.py` | 37 | `DATA_DIR = Path("data/m2_verification")` | Updated default to `Path("data/m2_verification_v3")` |
| `src/clindoc/m2/run.py` | 640–720 | Main dispatch used hardcoded `DATA_DIR` without `--data-dir` CLI option | Added `--data-dir` argument defaulting to `data/m2_verification_v3` and passed to all step functions |
| `src/clindoc/m2/recovery.py` | 33 | `DEFAULT_DATA_DIR = Path("data/m2_verification")` | Updated default to `Path("data/m2_verification_v3")` |
| `src/clindoc/m2/recovery.py` | 625–654 | Recovery CLI utility lacked `--data-dir` argument | Added `--data-dir` argument defaulting to `data/m2_verification_v3` |
| `src/clindoc/m2/splits.py` | 489 | `assert total_examples == 9168` in `validate_3way_split` | Updated to check against `manifest.get("example_counts", {}).get("total", total_examples)` |
| `src/clindoc/m2/generation.py`| 1572 | `integrate_narrative_benchmark` default parameter | Retained for historical provenance (function only called when combining old Task 2 + Task 3) |
| `tests/test_m2_3way_splits.py`| 42 | `CANONICAL_DATA_DIR = Path("data/m2_verification")` | Retained as regression test on historical dataset preservation (all 21 tests pass) |

---

## C. Files Modified

Only three files in the active training/evaluation path were modified to update defaults and make dataset paths fully configurable:

### 1. `src/clindoc/m2/run.py`
- Changed default `DATA_DIR` from `Path("data/m2_verification")` to `Path("data/m2_verification_v3")`.
- Added `--data-dir` option to `argparse.ArgumentParser` in `main()`.
- Threaded `data_dir` into:
  - `step_generate_dataset(data_dir=data_dir)`
  - `step_run_baselines(data_dir=data_dir)`
  - `recover_checkpoints(data_dir=data_dir)`
  - `step_train(data_dir=data_dir, ...)`
  - `step_evaluate(data_dir=data_dir)`
  - `get_training_plan` loading `data_dir / "split_manifest.json"`

### 2. `src/clindoc/m2/recovery.py`
- Changed default `DEFAULT_DATA_DIR` from `Path("data/m2_verification")` to `Path("data/m2_verification_v3")`.
- Added `--data-dir` option to `argparse.ArgumentParser` in `main()`.
- Threaded `data_dir` into `recover_checkpoints(data_dir=data_dir, ...)` in `main()`.

### 3. `src/clindoc/m2/splits.py`
- In `validate_3way_split`, replaced the hardcoded assertion `assert total_examples == 9168` with dynamic manifest-aware assertion:
  ```python
  manifest_total = manifest.get("example_counts", {}).get("total", total_examples)
  assert total_examples == manifest_total, f"Expected {manifest_total} examples from manifest, got {total_examples}"
  ```
  This cleanly validates both the legacy 9,168 benchmark and the new 5,629 V3 benchmark.

---

## D. Verification of Data Loader Partition Counts

The Stage B loader functions (`load_train_examples`, `load_calibration_examples`, `load_validation_examples`, `load_ood_examples`, and `get_fold_examples`) were executed directly against `data/m2_verification_v3/`:

```text
Testing Stage B loader against data/m2_verification_v3...
Train partition:       2242 (Expected: 2242)
Calibration partition: 678 (Expected: 678)
Validation partition:  883 (Expected: 883)
OOD partition:         1826 (Expected: 1826)
Total:                 5629 (Expected: 5629)

Fold 0: Train=3177, Val=626
Fold 1: Train=3148, Val=655
Fold 2: Train=2931, Val=872
Fold 3: Train=3269, Val=534
Fold 4: Train=2687, Val=1116
ALL STAGE B LOADER VERIFICATIONS PASSED!
```

### Invariant Checks on V3 Partitions
- **OOD Isolation**: Zero OOD examples or documents exist in any training or validation fold (`ood_contamination = 0`).
- **Group Isolation (DEC-015)**: Zero composite finding groups cross train/calibration/validation/OOD boundaries (`group_leakage = 0`).
- **Document Disjointness**: Zero document overlap across train, calibration, validation, and OOD partitions (`document_overlap = 0`).
- **21-Point Validation**: `validate_3way_split(manifest, examples=examples)` returned `{'status': 'VALID', 'total_documents': 120, 'cv_documents': 86, 'ood_documents': 34, 'train_documents': 52, 'calibration_documents': 17, 'validation_documents': 17}`.

---

## E. Confirmation of Zero Fallback to V2

1. **Default Path Check**:
   Running `python -m clindoc.m2.run --plan` without arguments reads directly from `data/m2_verification_v3/split_manifest.json` and loads all 120 documents and 5,629 examples.
2. **Explicit Path Check**:
   Supplying `--data-dir data/m2_verification_v3` correctly points to the V3 dataset.
3. **No Dynamic Fallback**:
   No code path checks for `data/m2_verification_v3` and silently falls back to `data/m2_verification`. If a path does not exist, a standard `FileNotFoundError` is raised loudly.

---

## F. Test Suite Execution & Results

All regression and unit test suites were executed to verify that the migration did not break existing behavior:

| Test Suite | Tests Run | Tests Passed | Status |
| :--- | :--- | :--- | :--- |
| `tests/test_m2_generator_fixes.py` | 7 | 7 | **PASSED** |
| `tests/test_benchmark_auditor.py` | 5 | 5 | **PASSED** |
| `tests/test_m2_metrics.py` | 12 | 12 | **PASSED** |
| `tests/test_m2_3way_splits.py` | 21 | 21 | **PASSED** |
| **Total** | **45** | **45** | **100% PASSED** |

---

## G. Cryptographic Checksums (Immutability Confirmation)

Cryptographic SHA256 hashes confirm that neither the historical V2 dataset nor the new V3 dataset was modified in any way:

### Historical V2 Benchmark (Pre-Fix Dataset — Unchanged)
- `data/m2_verification/all_examples.jsonl`:  
  `ce924fa3921eedf2482c29279579ed010036273e608c399f38ce291c8306a2a8` (**CONFIRMED IDENTICAL**)
- `data/m2_verification/split_manifest.json`:  
  `8a1ebd157188c4c4032dab297df4c53f9d2fc1dd58a52596a368cc7c676e4393` (**CONFIRMED IDENTICAL**)

### Authoritative V3 Benchmark (`data/m2_verification_v3/` — Unchanged)
- `data/m2_verification_v3/all_examples.jsonl`:  
  `40278c01848875382931a8a01397982ff6c00dfb7f588c30666b1ca8a66d6d16` (**CONFIRMED IDENTICAL**)
- `data/m2_verification_v3/split_manifest.json`:  
  `cf41cc1d7e889f14cdcecf9ca2c411d54f2b737ddcfa280f6852b739050d2067` (**CONFIRMED IDENTICAL**)
- `data/m2_verification_v3/generation_manifest.json`:  
  `8119a30b0e40571745a8d368faa3866a984a78a602489446066e6b4569a3eceb` (**CONFIRMED IDENTICAL**)

---

## H. Migration Conclusion

The training and evaluation pipeline migration to `data/m2_verification_v3` is complete and verified. The environment is now fully prepared for Stage B ModernBERT retraining. No model training or evaluation has been performed.
