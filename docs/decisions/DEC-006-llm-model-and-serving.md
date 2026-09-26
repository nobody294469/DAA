# Decision DEC-006: LLM Model Selection and Serving Stack

## Status
EMPIRICALLY OPEN (Provisional development default: 7–8B instruction-tuned model, 4-bit quantized, served via vLLM on Colab T4 16GB; llama.cpp fallback)

## Context
The LLM is strictly used for narrative extraction (§7.4), verification Stage C judging (§12), and the standalone baseline V9 (§22.2). It must execute within Colab T4 16GB VRAM and reliably follow JSON schemas.

## Decision
Select a 7–8B class instruction-tuned model (candidates: Qwen2.5-7B-Instruct or Llama-3.1-8B-Instruct), 4-bit quantized (AWQ or bitsandbytes), served via vLLM on Colab T4. Final selection between the two candidates will be decided by a benchmark comparing VRAM footprint, latency, and exact verbatim evidence-substring compliance (§7.4).

## Alternatives Considered
1. ~3B class model (e.g. Phi-3.5-mini) — rejected because 7–8B models fit comfortably in 5–6GB at 4-bit on T4 and demonstrate significantly higher instruction-following fidelity for verbatim text extraction.
2. Hosted proprietary API — rejected for primary pipeline to maintain offline reproducibility and direct logprob extraction, though permitted for small-scale baseline checks.
3. 7–8B class open model on vLLM (Selected).

## Rationale
vLLM provides high batch throughput and native support for grammar-constrained decoding (DEC-013) on the T4 GPU.

## Consequences
- All LLM evaluation runs are planned as Colab T4 batch jobs, never on the local RTX 3050 (6GB).
- Substring validity bar of ≥95% must be cleared before accepting a candidate.

## Validation / Experiment
Phase 0/1 Benchmark `experiments/DR6-llm-model-comparison/`: Benchmark Qwen2.5-7B-Instruct vs. Llama-3.1-8B-Instruct across 100 narrative extraction cases and 100 judge cases; evaluate VRAM, latency, and verbatim substring validity.

## Related Specification Sections
- PROJECT_SPEC §5 Components 5 and 9, §7.4, §12, §13.2, §27
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-6
- PHASE0_ARCHITECTURE_RESOLUTION Section 7, Section 8
