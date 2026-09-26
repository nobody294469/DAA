# Decision DEC-003: Verification Context Window Size

## Status
EMPIRICALLY OPEN (Provisional development default: paragraph-bounded window, max 500 chars, fallback to ±250 chars fixed window)

## Context
Verification components (ModernBERT Stage B and LLM Judge Stage C) require surrounding context to correctly assess negation ("patient denies"), temporal status ("discontinued 2 weeks ago"), and clinical qualifiers.

## Decision
The verification context window is paragraph-bounded with a hard cap of 500 characters, falling back to a fixed ±250 character window around `[char_start, char_end]` when no clear paragraph boundary exists (e.g. dense tabular text). This definition MUST be applied identically during dataset generation and inference.

## Alternatives Considered
1. Fixed character window (e.g. ±200 chars) — rejected because it truncates negation and temporal clauses that extend beyond the window boundary.
2. Sentence-boundary-aware window — rejected because OCR output on tabular/semi-structured layouts often lacks standard punctuation.
3. Paragraph-bounded window with character cap and tabular fallback (Selected).

## Rationale
ModernBERT-base (512 max length) and 7–8B LLMs easily handle a few hundred context characters. Context correctness takes precedence over micro-optimizing token count.

## Consequences
- Preserves crucial syntactic and clinical context for hard negatives (§18.5, §20.1).
- Context generation is deterministic and shared between training dataset generation and runtime inference.

## Validation / Experiment
Phase 1 Mini-Ablation `experiments/DR3-context-window-ablation/`: Evaluate Stage B frozen probe accuracy on the negation/temporal hard-negative slice across ±100, ±250, and paragraph-bounded windows.

## Related Specification Sections
- PROJECT_SPEC §5 Components 7–9, §7.6, §9, §12, §23.1
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-3
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-09, Section 9
