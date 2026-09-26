# Decision DEC-007: Terminology Retrieval Embedding Model & Scoring

## Status
EMPIRICALLY OPEN (Provisional development default: SapBERT + fixed weighted sum with BM25; UCUM = deterministic lookup)

## Context
Terminology normalization (§14) maps raw clinical mentions to LOINC (lab tests) and RxNorm (medications). The pipeline requires a hybrid retrieval architecture combining lexical search and semantic dense embeddings.

## Decision
Use SapBERT (`cambridgeltl/SapBERT-from-PubMedBERT-fulltext`) as the primary semantic embedding model, with BioLORD as empirical fallback. Scoring combines dense cosine similarity and BM25 lexical score via a fixed weighted sum, with the weight grid-searched in Phase 3. UCUM units are resolved strictly via deterministic lookup, bypassing embedding retrieval.

## Alternatives Considered
1. General-purpose sentence embedding model — rejected due to lack of biomedical entity-linking training.
2. BioLORD — retained as empirical fallback if SapBERT underperforms on specific concept classes.
3. Trained reranker (cross-encoder or logistic regression) — rejected as unnecessary complexity and overfitting risk for closed concept spaces like LOINC/RxNorm.
4. SapBERT + BM25 weighted sum (Selected).

## Rationale
SapBERT is pre-trained specifically for UMLS entity alignment, aligning surface-form synonyms to canonical concepts. A fixed weighted sum avoids the training overhead of an auxiliary reranker.

## Consequences
- Requires downloading and indexing official LOINC and RxNorm concept tables in Phase 3.
- UCUM lookup operates at zero embedding inference cost.

## Validation / Experiment
Phase 3 Experiment `experiments/DR7-terminology-retrieval-benchmark/`: Compare top-1 and top-5 retrieval accuracy for SapBERT vs. BioLORD on benchmark ground-truth codes; grid-search lexical/semantic weight.

## Related Specification Sections
- PROJECT_SPEC §5 Component 6, §14.2, §21.6
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-7
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-14
