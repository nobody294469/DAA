# Legacy M2 v1 Experiment Artifacts

**STATUS: LEGACY / SUPERSEDED / NOT FOR FINAL RESULTS**

## Notice
These artifacts were generated during the preliminary M2 experiment using the initial (flawed) v1 dataset generation logic, where certain field types (e.g. lab.flag, med.name) had missing or single-class representation.

Per **PHASE0_ARCHITECTURE_RESOLUTION.md (Section 13)** and **PROJECT_SPEC.md (§22.2, §26)**:
- These artifacts are permanently superseded and isolated to prevent data contamination.
- They MUST NOT be used for headline comparisons or final project reporting.
- They are preserved strictly for audit, historical provenance, and dataset debugging purposes.
- Official Phase 4 experiments (V6, V7, etc.) will write to fresh directories under data/m2_results/ and experiments/ using the corrected, class-balanced dataset (DR-11).
