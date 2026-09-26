# Decision DEC-013: LLM Structured-Output Enforcement Mechanism

## Status
EMPIRICALLY OPEN (Provisional development default: Grammar-constrained decoding via vLLM native support; prompt+parse+retry fallback)

## Context
PROJECT_SPEC §7.4 and §13.3 mandate that LLM extraction and verification judging MUST enforce structured output (schema constraints). Free-form prompting with best-effort parsing is prohibited.

## Decision
Use grammar-constrained / JSON-schema-constrained decoding at generation time via vLLM's native structured-output engine (compatible with Outlines/XGrammar). Use prompt + schema parsing + bounded retry (maximum 2 retries) strictly as a fallback if grammar decoding proves unavailable in a specific deployment runtime. In all cases, post-hoc verbatim evidence-substring verification (§7.4) remains mandatory.

## Alternatives Considered
1. Unconstrained prompting with regex JSON parsing — rejected by §13.3.
2. Prompt + parse + unbounded retry — rejected due to latency unpredictability and potential infinite loops.
3. Grammar-constrained generation at decoding time (Selected).

## Rationale
Grammar-constrained decoding mathematically prevents syntactic JSON errors at generation time, reducing parser failure rates to 0%.

## Consequences
- Requires using serving engines that support grammar constraints (vLLM or llama.cpp).
- Output is guaranteed to conform to target Pydantic schemas.

## Validation / Experiment
Phase 0/1 Benchmark `experiments/DR13-structured-output-check/`: Test 100 narrative extraction calls with grammar-constrained decoding; verify 100% syntactic schema conformance and absence of semantic degradation.

## Related Specification Sections
- PROJECT_SPEC §5 Component 5, §7.4, §13.3, §24
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-13
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-07, Section 7
