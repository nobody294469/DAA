# Decision DEC-004: Deterministic Extraction Confidence Re-routing Threshold

## Status
EMPIRICALLY OPEN (Provisional development default: 0.75)

## Context
Under Extraction Routing rules (§11 rule 2), if the deterministic parser's extraction confidence is below a defined threshold, the region must be re-routed: first to GLiNER as cross-check, and if uncertainty persists, escalated to the LLM narrative extractor.

## Decision
The threshold will be selected empirically via parameter sweep {0.5, 0.6, 0.7, 0.8, 0.9} on the Phase 1 benchmark ground truth, biased toward the recall floor (preferring escalation over silent acceptance of uncertain extractions), subject to a ≤30% escalation rate sanity bound per document type.

## Alternatives Considered
1. Fixed default 0.70 — rejected as arbitrary a priori assumption without empirical validation.
2. F1-maximizing threshold — rejected because under-escalation allows erroneous deterministic extractions to propagate silently.
3. Recall-biased threshold with escalation-rate cap (Selected).

## Rationale
DAA is designed for offline/batch execution on Colab T4 hardware (§27). Compute time is not scarce, whereas silent extraction error is a critical failure mode.

## Consequences
- Requires completion of DEC-001 (router) and Phase 1 corrected benchmark.
- Slightly higher escalation rate to GLiNER/LLM in exchange for minimal undetected extraction errors.

## Validation / Experiment
Phase 2 Experiment `experiments/DR4-deterministic-threshold-sweep/`: Sweep candidate thresholds against extraction ground truth; measure end-to-end extraction F1 vs. escalation rate.

## Related Specification Sections
- PROJECT_SPEC §5 Component 2, §11 rule 2, §21.1, §22.1
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-4
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-13
