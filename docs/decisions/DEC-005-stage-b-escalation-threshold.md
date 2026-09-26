# Decision DEC-005: Stage B Escalation Threshold

## Status
EMPIRICALLY OPEN (Provisional development default: 0.60)

## Context
In the verification cascade (§12), Stage B (ModernBERT) escalates to Stage C (LLM Judge) if its calibrated confidence falls below a threshold $\tau_{\text{esc}}$. This decision directly controls the selective prediction tradeoff and system escalation rate.

## Decision
The Stage B escalation threshold $\tau_{\text{esc}}$ cannot be locked a priori. It MUST be chosen from the "knee" of the risk-coverage curve evaluated in Experiment V12 (§21.5, §22.2) after V6 ModernBERT is retrained on v2 data and calibrated using temperature scaling (DR-9).

## Alternatives Considered
1. Locking a fixed default (e.g. 0.60) — rejected because the optimal threshold is an empirical property of the trained model's calibrated confidence distribution.
2. Fragmented per-field-type thresholds — deferred to post-V12 ablation (V11) to avoid premature fragmentation.
3. Risk-coverage knee-point selection (Selected).

## Rationale
Setting this threshold prematurely would be guessing. The risk-coverage curve (AURC) is a headline scientific contribution of the project (§21.5) designed to establish this threshold empirically.

## Consequences
- Requires completion of V6 retraining, calibration (DEC-009), and V12 risk-coverage evaluation in Phase 4.
- During Phase 1–3 development and pipeline testing, provisional default of 0.60 is used.

## Validation / Experiment
Phase 4 Experiment `experiments/V12-risk-coverage-analysis/`: Plot risk vs. coverage curve on CV validation split; verify threshold transfer on OOD split; lock the knee-point threshold.

## Related Specification Sections
- PROJECT_SPEC §5 Component 8, §10, §12, §17.1, §21.5, §22.2 (V12)
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-5
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-08, Section 8, Section 10
