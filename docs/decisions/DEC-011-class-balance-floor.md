# Decision DEC-011: Dataset Class-Balance Floor Policy

## Status
LOCKED

## Context
The M2 v1 dataset suffered from single-class field representations (e.g. `lab.flag` having 0 SUPPORTED examples, `med.name` having 0 DISCREPANCY examples). PROJECT_SPEC §18.4 mandates automated class-balance checks to permanently prevent recurrence.

## Decision
Establish the canonical class-balance floor rule:
1. For every active learned verification field type, both `SUPPORTED` and `DISCREPANCY` must satisfy:
   $$\text{count} \ge \max(0.10 \times N_{\text{field}}, 30)$$
2. For `INSUFFICIENT_EVIDENCE`, a minimum of 15 absolute examples is required per field type where that class is semantically applicable (unobserved tests, unprescribed drugs, OCR-destroyed values).
3. The dataset generation pipeline MUST execute an automated check `assert_class_balance()` in CI; failures must halt the pipeline.

## Alternatives Considered
1. Pure percentage floor (5% or 10%) — rejected because fields with modest sample sizes (e.g. 174 examples) would yield insufficient absolute support for statistical evaluation.
2. Pure absolute floor — rejected because large fields (e.g. 5,000+ examples) would allow extreme 99:1 imbalances.
3. Combined percentage-or-absolute-whichever-is-larger rule (Selected).

## Rationale
Protects small and large field types simultaneously, guaranteeing statistical validity in Macro-F1 evaluation.

## Consequences
- Encoded in `configs/class_balance_floor.yaml`.
- Enforced on every dataset regeneration.

## Validation / Experiment
Automated CI unit test in `tests/test_m2_dataset.py` verifying the generated v2 dataset passes `assert_class_balance()`.

## Related Specification Sections
- PROJECT_SPEC §18.4, §21.3, §24, §26 Phase 1
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-11
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-08, AUDIT-15
