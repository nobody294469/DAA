# Decision DEC-002: Terminology Code Confidence Threshold

## Status
EMPIRICALLY OPEN (Provisional development default: 0.75 for LOINC/RxNorm; UCUM exact-match lookup)

## Context
Terminology normalization (§5 #6, §14) maps raw clinical mentions to LOINC, RxNorm, and UCUM. If `code_confidence` is below threshold, `normalized_code` and `code_system` must be set to `null` to avoid corrupting downstream FHIR resources.

## Decision
The `code_confidence` threshold will be set independently per `code_system` (LOINC vs. RxNorm), empirically calibrated via threshold sweep against benchmark ground truth to target ≥95% precision on accepted codes. UCUM uses deterministic lookup, not a threshold.

## Alternatives Considered
1. Single global threshold (e.g. 0.70 placeholder) — rejected because LOINC, RxNorm, and UCUM have vastly different synonym densities and ambiguity profiles.
2. F1-maximizing threshold — rejected because the clinical risk of an incorrectly accepted code is asymmetric compared to an auditable null fallback.
3. Independent per-code-system precision-targeted thresholds (Selected).

## Rationale
In clinical data conversion, false positive codes silently corrupt clinical records, whereas null codes cleanly fall back to `CodeableConcept.text`. Targeting ≥95% precision directly addresses safety requirements.

## Consequences
- Requires empirical sweep in Phase 3 after DR-7 retrieval model is selected.
- Lower retrieval coverage is traded for guaranteed high coding fidelity.
- During early development prior to sweep, provisional default of 0.75 is used.

## Validation / Experiment
Phase 3 Experiment `experiments/DR2-code-confidence-sweep/`: Sweep threshold from 0.50 to 0.95, plot precision-recall-coverage curve on CV ground truth, and lock the value hitting ≥95% precision.

## Related Specification Sections
- PROJECT_SPEC §5 Component 6, §7.5, §14, §15.3, §21.6
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-2
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-14
