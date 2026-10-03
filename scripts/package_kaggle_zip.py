"""package_kaggle_zip.py — Clean packaging of D:\daa for Kaggle transfer.

Includes:
  - Source code (src/)
  - Configs (configs/, pyproject.toml, uv.lock)
  - Evaluation scripts (eval/)
  - Tests (tests/)
  - Project specification & documentation (PROJECT_SPEC.md, README.md, docs/, etc.)
  - Canonical M2 datasets (data/m2_verification/)
  - M2 results metadata & evaluation records (data/m2_results/*.json)
  - Experiments, benchmarks, schemas, scratch

Excludes:
  - .git
  - .venv*
  - Python caches (__pycache__, .pytest_cache, *.pyc)
  - Existing model checkpoints (data/m2_results/checkpoints/, *.pt checkpoints)
  - Legacy result and backup directories (m2_results_v1_legacy, m2_verification_*_backup)
  - Unnecessary generated artifacts (rendered images, OCR caches, degraded images)
  - Existing ZIP archives (*.zip)
"""
import os
import sys
import zipfile
from pathlib import Path

ROOT_DIR = Path("d:/daa").resolve()
OUTPUT_ZIP = Path("d:/daa_kaggle.zip").resolve()

# Root files to include
INCLUDE_ROOT_FILES = {
    "PROJECT_SPEC.md",
    "README.md",
    "PHASE0_ARCHITECTURE_RESOLUTION.md",
    "PHASE0_DECISION_RESOLUTION_PACKAGE.md",
    "app.py",
    "pyproject.toml",
    "uv.lock",
    ".gitignore",
}

# Top-level directories to walk and include (with standard code exclusions)
INCLUDE_CODE_DIRS = [
    "src",
    "configs",
    "eval",
    "tests",
    "docs",
    "schemas",
    "experiments",
    "benchmark",
    "n2c2_access",
    "scratch",
]

# Specifically included data subdirectories and files
INCLUDE_DATA_FILES = [
    # Canonical M2 verification dataset
    "data/m2_verification/all_examples.jsonl",
    "data/m2_verification/split_manifest.json",
    "data/m2_verification/metadata.json",
    "data/m2_verification/generation_manifest.json",
    "data/m2_verification/frozen_embeddings.pt",
    # Evaluation results and baseline reports (no checkpoints)
    "data/m2_results/m2_baseline_results.json",
    "data/m2_results/m2_recovered_headline_results.json",
    "data/m2_results/m2_train_results_recovered.json",
    "data/m2_results/m2_final_report.json",
    "data/m2_results/m2_error_analysis.json",
    "data/m2_results/m2_train_debug_results.json",
    "data/m2_results/.gitkeep",
]

EXCLUDE_PATTERNS = [
    "/.git/",
    "/.git",
    "/.venv",
    "/__pycache__/",
    "/.pytest_cache/",
    "/checkpoints/",
    "/m2_results_v1_legacy/",
    "/m2_verification_pre_task3_backup/",
    "/m2_verification_v1_backup/",
    "/m2_verification_v2_pre_phase1_backup/",
    "/degraded/",
    "/rendered/",
    "/ocr_cache/",
]


def should_exclude(rel_path_str: str) -> bool:
    norm = rel_path_str.replace("\\", "/")
    if not norm.startswith("/"):
        norm_with_slash = "/" + norm
    else:
        norm_with_slash = norm

    if norm.endswith(".pyc") or norm.endswith(".zip"):
        return True

    for exc in EXCLUDE_PATTERNS:
        if exc in norm_with_slash:
            return True

    return False


def collect_files_to_pack() -> list[tuple[Path, str]]:
    files_to_pack: list[tuple[Path, str]] = []

    # 1. Root files
    for fname in INCLUDE_ROOT_FILES:
        p = ROOT_DIR / fname
        if p.exists() and p.is_file():
            files_to_pack.append((p, fname))

    # 2. Code directories
    for dname in INCLUDE_CODE_DIRS:
        dpath = ROOT_DIR / dname
        if not dpath.exists():
            continue
        for root, dirs, files in os.walk(dpath):
            # Prune excluded directories in-place
            dirs[:] = [
                d for d in dirs
                if not should_exclude(os.path.relpath(os.path.join(root, d), ROOT_DIR))
            ]
            for f in files:
                abs_f = Path(root) / f
                rel_f = abs_f.relative_to(ROOT_DIR).as_posix()
                if not should_exclude(rel_f):
                    files_to_pack.append((abs_f, rel_f))

    # 3. Explicit canonical data files
    for data_rel in INCLUDE_DATA_FILES:
        abs_p = ROOT_DIR / data_rel
        if abs_p.exists() and abs_p.is_file():
            files_to_pack.append((abs_p, data_rel))
        else:
            print(f"Warning: Expected data file missing: {data_rel}")

    return files_to_pack


def create_and_verify_zip():
    print(f"Collecting files to package into {OUTPUT_ZIP}...")
    files = collect_files_to_pack()
    print(f"Total files selected: {len(files)}")

    # Sort files for deterministic archive creation
    files.sort(key=lambda x: x[1])

    total_uncompressed_bytes = sum(abs_p.stat().st_size for abs_p, _ in files)
    print(f"Total uncompressed size: {total_uncompressed_bytes / (1024*1024):.2f} MB")

    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()

    print(f"Writing {OUTPUT_ZIP}...")
    with zipfile.ZipFile(OUTPUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
        for abs_p, rel_p in files:
            zipf.write(abs_p, arcname=rel_p)

    zip_size_bytes = OUTPUT_ZIP.stat().st_size
    print(f"ZIP created successfully!")
    print(f"  Archive path: {OUTPUT_ZIP}")
    print(f"  Compressed size: {zip_size_bytes / (1024*1024):.2f} MB")

    # Verification phase
    print("\n" + "=" * 60)
    print("VERIFYING ZIP ARCHIVE CONTENTS")
    print("=" * 60)
    with zipfile.ZipFile(OUTPUT_ZIP, "r") as z:
        names = z.namelist()
        print(f"Total archive entries: {len(names)}")

        # Check for forbidden entries
        forbidden_hits = []
        for name in names:
            norm = "/" + name.replace("\\", "/")
            if any(f in norm for f in [".git/", ".venv", "__pycache__", ".pytest_cache", ".pyc", "checkpoints/"]):
                forbidden_hits.append(name)
        assert len(forbidden_hits) == 0, f"Found forbidden entries in ZIP: {forbidden_hits[:10]}"
        print("[CHECK] No .git, .venv, pycache, or checkpoints: PASSED")

        # Check mandatory files
        mandatory_files = [
            "PROJECT_SPEC.md",
            "README.md",
            "pyproject.toml",
            "uv.lock",
            "configs/m2_train_config.json",
            "configs/split_config.yaml",
            "configs/class_balance_floor.yaml",
            "src/clindoc/m2/model.py",
            "src/clindoc/m2/train.py",
            "src/clindoc/m2/evaluate.py",
            "src/clindoc/m2/recovery.py",
            "src/clindoc/m2/schema.py",
            "src/clindoc/m2/run.py",
            "eval/smoke_test_modernbert_full.py",
            "data/m2_verification/all_examples.jsonl",
            "data/m2_verification/split_manifest.json",
            "data/m2_verification/metadata.json",
            "tests/test_smoke_test_guard.py",
            "tests/test_m2_recovery_fidelity.py",
            "tests/test_m2_calibration_persistence.py",
        ]
        missing = [mf for mf in mandatory_files if mf not in names]
        assert len(missing) == 0, f"Missing mandatory files in ZIP: {missing}"
        print(f"[CHECK] All {len(mandatory_files)} mandatory files present: PASSED")

        # Breakdown by top-level category
        categories = {}
        for name in names:
            top = name.split("/")[0]
            categories[top] = categories.get(top, 0) + 1

        print("\nBreakdown by top-level folder:")
        for top, cnt in sorted(categories.items()):
            print(f"  - {top + '/':<20} {cnt:4d} files")

    print("\n" + "=" * 60)
    print("ZIP VERIFICATION COMPLETE AND VALID")
    print("=" * 60)


if __name__ == "__main__":
    create_and_verify_zip()
