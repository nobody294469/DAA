# Decision DEC-001: Region/Layout Classification Method

## Status
LOCKED

## Context
The Extraction Router (PROJECT_SPEC §5 #2, §11) must determine whether each document region is routed to the deterministic parser, GLiNER biomedical NER, or the LLM narrative extractor.

## Decision
The region classifier MUST use a deterministic rule-based heuristic: regex pattern matching for medication-regimen line grammar (`DRUG DOSE ROUTE FREQUENCY [DURATION]`), column alignment heuristics for lab result tables, and default to narrative for unmatched text blocks. Pretrained PP-Structure table detection MAY be used as a supplementary signal for table boundary refinement. No custom-trained layout model is used.

## Alternatives Considered
1. Fine-tuning a custom multimodal layout model (e.g. LayoutLMv3) — rejected because §3 and §28 explicitly prohibit custom-trained layout/vision-language models as a deliverable.
2. Rule-based / heuristic classifier (Selected) — synthetically generated templates have known, finite structural patterns.
3. Pure off-the-shelf layout tool (e.g. PP-Structure) as sole decider — rejected as unnecessary complexity for known template structures, retained only as optional boundary supplement.

## Rationale
Heuristic classification achieves near-100% accuracy on templated clinical documents at zero inference cost, perfectly aligning with the "deterministic first" architectural principle (§4.2).

## Consequences
- Fast, reproducible routing execution without GPU memory overhead.
- Regions are tagged with explicit `RoutingDecision` objects (§7.2).
- If template drift occurs in unseen OOD documents, PP-Structure supplementary boundary detection can be engaged without retraining.

## Validation / Experiment
Lightweight routing accuracy check across benchmark templates during Phase 2 router implementation (target: ≥90% routing accuracy per document type).

## Related Specification Sections
- PROJECT_SPEC §5 Component 2, §7.2, §11, §28
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-1
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-13
