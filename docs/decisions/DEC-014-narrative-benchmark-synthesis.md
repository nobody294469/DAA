# Decision DEC-014: Narrative Benchmark Synthesis Scope

## Status
LOCKED

## Context
AUDIT-01 revealed that the existing 100-document benchmark consists entirely of tabular lab reports and prescription forms. The `discharge_summary` template is rendered as a tabular grid. There are zero free-text narrative clinical notes to evaluate E3 (LLM narrative extraction) and E4 (hybrid extraction router).

## Decision
Generate a focused suite of **20 synthetic narrative discharge summaries** (15 CV, 5 OOD) in Phase 1, bringing the total benchmark to **120 documents**. These documents will feature realistic clinical prose paragraphs (History of Present Illness, Hospital Course, Discharge Instructions & Plan) generated from structured Synthea profiles, with embedded medication orders and laboratory findings. Ground-truth character offsets for all mentions will be preserved.

## Alternatives Considered
1. Expanding scope to build an open-ended clinical NLP benchmark with hundreds of notes — rejected as severe scope creep for the project timeline.
2. Omitting narrative documents and evaluating E3/E4 on tabular forms — rejected because it invalidates the core architectural premise of a hybrid router.
3. Targeted 20-document narrative benchmark suite (Selected).

## Rationale
20 multi-section narrative documents provide sufficient textual diversity to validate the extraction router and LLM narrative extractor without overbuilding synthetic data pipelines.

## Consequences
- Core benchmark expands from 100 to 120 documents.
- OOD split expands from 29 to 34 documents (5 narrative OOD documents added).
- E3 and E4 experiments have authentic narrative prose to evaluate.

## Validation / Experiment
Phase 1 dataset generation tests verifying character offset alignment of ground-truth mentions within generated prose paragraphs.

## Related Specification Sections
- PROJECT_SPEC §1.2, §2.1, §7.4, §11, §18.1, §22.1 (E3, E4)
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-01, Section 5, Section 12
