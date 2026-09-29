# Experiment V5-zeroshot-nli

## Specification Reference
- Authority: `PROJECT_SPEC.md` §22.2 (Variant V5)
- Concept: Off-the-shelf NLI baseline, zero/few-shot (no fine-tuning)

## Implementation Status
- **Status:** IMPLEMENTED (canonical benchmark execution PENDING)
- **Module:** `src/clindoc/m2/baselines.py` (`predict_zeroshot_nli`, `predict_zeroshot_nli_proba`, `calibrate_v5_temperature`)
- **Evaluation Runner:** `src/clindoc/m2/evaluate.py` (`evaluate_zeroshot_nli`, `evaluate_baselines`)
- **Locked Model:** `pritamdeka/PubMedBERT-MNLI-MedNLI`
- **Testing Double:** `MockNLIPredictor` for deterministic offline test suite execution

## Verification Protocol
1. **Evidence Guards:** If `ocr_confidence < 0.5` or `len(evidence) < 3`, verdict is `INSUFFICIENT_EVIDENCE`.
2. **Input Formatting:** Premise = `evidence`, Hypothesis = `claim` (with field_type prefix).
3. **Native Label Mapping:**
   - Native `1` (`entailment`) $\rightarrow$ `SUPPORTED`
   - Native `0` (`contradiction`) $\rightarrow$ `DISCREPANCY`
   - Native `2` (`neutral`) $\rightarrow$ `INSUFFICIENT_EVIDENCE`
4. **Decision Policy:**
   - Frozen pretrained model (zero fine-tuning).
   - Primary prediction: native softmax $\operatorname{argmax}$ over mapped classes.
   - Temperature scaling (if enabled per DEC-009) fit strictly on `calibration_doc_ids`.
   - Zero tuning on validation or OOD splits.

## Benchmark Execution
Pending execution on the canonical benchmark dataset (`data/m2_verification/`). Official metrics and `REPORT.md` will be generated during the benchmark evaluation run.
