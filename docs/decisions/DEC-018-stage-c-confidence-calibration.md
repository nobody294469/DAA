# Decision DEC-018: Stage C LLM Confidence Calibration Mechanism

## Status
LOCKED

## Context
AUDIT-07 identified that PROJECT_SPEC §12 stated Stage C confidence is passed through the same calibration pipeline as Stage B (temperature scaling). However, temperature scaling requires a multi-class logit vector, whereas an LLM generating JSON outputs a scalar confidence float.

## Decision
Establish a formal dual-mode calibration interface for Stage C:
1. **Primary Mode (Local vLLM Engine):** Under grammar-constrained decoding (DR-13), unnormalized token logits for the three label tokens (`SUPPORTED`, `DISCREPANCY`, `INSUFFICIENT_EVIDENCE`) are extracted directly from the engine. These 3 logits are calibrated using multi-class temperature scaling with parameter $T_{\text{LLM}}$ fit on calibration split examples.
2. **Fallback Mode (Hosted API / Non-Logit Serving):** If token logprobs cannot be extracted, the model self-reports confidence $c \in [0, 1]$, which is calibrated using 1D Isotonic Regression or Beta Calibration fit on calibration split examples.

## Alternatives Considered
1. Pretending a scalar float can be passed into multi-class softmax temperature scaling — rejected as mathematically impossible.
2. Relying on raw uncalibrated self-reported LLM confidence — rejected by §13.3 ("The LLM's self-reported confidence MUST NOT be used directly... MUST pass through calibration").
3. Dual-mode calibration with logit scaling as primary (Selected).

## Rationale
Extracting token logprobs provides authentic multi-class logits for temperature scaling, while the 1D fallback guarantees calibration even when using black-box APIs.

## Consequences
- `src/clindoc/m2/calibration.py` implements both multi-class and 1D calibrators.
- Eliminates implementation ambiguity for the verification judge.

## Validation / Experiment
Calibration validation in Phase 0/1 LLM benchmark (`experiments/DR6-llm-model-comparison/`).

## Related Specification Sections
- PROJECT_SPEC §10, §12, §13.3, §17.1
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-9
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-07, Section 10
