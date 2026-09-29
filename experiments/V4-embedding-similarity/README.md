# Experiment V4-embedding-similarity

## Specification Reference
- Authority: `PROJECT_SPEC.md` §22.2 (Variant V4)
- Concept: Embedding-similarity baseline (SapBERT/BioLORD cosine, zero training)

## Implementation Status
- **Status:** IMPLEMENTED (canonical benchmark execution PENDING)
- **Module:** `src/clindoc/m2/baselines.py` (`predict_embedding_similarity`, `train_embedding_similarity`)
- **Evaluation Runner:** `src/clindoc/m2/evaluate.py` (`evaluate_embedding_similarity`, `evaluate_baselines`)
- **Embedder:** `SapBERTEmbedder` (`cambridgeltl/SapBERT-from-PubMedBERT-fulltext` per DEC-007) with `NGramProxyEmbedder` fallback for offline testing

## Verification Protocol
1. **Guards:** If `ocr_confidence < 0.5` or `len(evidence) < 3`, verdict is `INSUFFICIENT_EVIDENCE`.
2. **Similarity Scoring:** Cosine similarity computed between claim and evidence representations.
3. **Classification:** If `similarity >= threshold`, verdict is `SUPPORTED`; otherwise `DISCREPANCY`.
4. **Threshold Selection:** Zero training implies frozen embedding weights; decision threshold $\tau$ is selected on the CV training split (grid search over $\tau \in [0.05, 0.95]$ optimizing macro-F1) or preset, and evaluated out-of-sample on CV validation and OOD holdout splits without test-split leakage.

## Benchmark Execution
Pending execution on the canonical benchmark dataset (`data/m2_verification/`). Official metrics and `REPORT.md` will be generated during the benchmark evaluation run.
