# Decision DEC-009: Verification Confidence Calibration Method

## Status
EMPIRICALLY OPEN (Provisional development default: Multi-class temperature scaling on CV calibration split; isotonic regression fallback)

## Context
PROJECT_SPEC §10 and §17 mandate that reported verification confidence must be calibrated (reflecting true empirical probability of correctness), evaluated via ECE, Brier score, and reliability diagrams on both CV and OOD splits.

## Decision
Use multi-class temperature scaling (optimizing a single positive scalar parameter $T$ on unnormalized Stage B ModernBERT logits) fit strictly on the dedicated `calib_doc_ids` split. Isotonic regression will be evaluated as a comparison fallback only if temperature scaling fails to transfer effectively to OOD templates (trigger condition: OOD ECE > 2× CV validation ECE).

## Alternatives Considered
1. Platt scaling — rejected because Platt scaling is fundamentally binary and does not naturally model 3-class verification.
2. Isotonic regression as primary — rejected because it easily overfits on modest calibration splits and transfers poorly to OOD data.
3. Multi-class temperature scaling (Selected).

## Rationale
Temperature scaling preserves logit rank ordering and classification accuracy while optimizing softmax calibration with minimal parameter complexity ($T$).

## Consequences
- Requires the 3-way CV split established in AUDIT-05.
- Calibration scalar $T$ must be saved and versioned alongside model checkpoint weights.

## Validation / Experiment
Phase 4 Experiment `experiments/DR9-calibration-comparison/`: Retrain V6 on v2 data, fit $T$ on `calib_doc_ids`, evaluate ECE and Brier score on CV val vs. OOD test splits. Compare against isotonic regression.

## Related Specification Sections
- PROJECT_SPEC §5 Component 12, §10, §17.2, §19.5, §21.3
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-9
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-05, AUDIT-06, Section 10
