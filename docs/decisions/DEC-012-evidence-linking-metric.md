# Decision DEC-012: Evidence-Linking Evaluation Metric

## Status
LOCKED

## Context
PROJECT_SPEC §21.2 mandates measuring the accuracy with which extracted claims are grounded to source document evidence spans.

## Decision
Designate **character-offset overlap F1** (precision, recall, and F1 over character indices within the page text, extractive-QA / SQuAD style) as the **primary headline metric** for evidence linking. Bounding-box intersection-over-union ($\text{IoU} \ge 0.5$) will be reported strictly as a **secondary diagnostic metric** to isolate geometric OCR mapping errors.

## Alternatives Considered
1. Exact character-offset match — rejected as overly strict for minor whitespace/token boundary differences.
2. Bounding-box IoU as primary — rejected because verification consumes text strings, not bounding boxes; geometric errors should not obscure textual grounding accuracy.
3. Dual-metric reporting with character-offset F1 as headline (Selected).

## Rationale
Character offsets represent the true semantic grounding key used by the verification cascade. Bounding boxes are derived layout metadata.

## Consequences
- Evaluation harness evaluates textual grounding independently of OCR geometric bounding box inaccuracies.
- Diagnostic logs clearly distinguish textual extraction misses from bbox alignment misses.

## Validation / Experiment
Evaluation in Phase 2 extraction experiments (E1–E4) and Phase 6 end-to-end evaluation.

## Related Specification Sections
- PROJECT_SPEC §5 Component 13, §7.6, §9, §21.2
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-12
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-17
