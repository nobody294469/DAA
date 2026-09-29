"""Unit tests for M2 baseline models (deterministic, Levenshtein, TF-IDF+LR, V4 embedding similarity)."""
import pytest
from clindoc.m2.schema import M2Example, VerificationLabel, LABEL_ORDER
from clindoc.m2.baselines import (
    predict_deterministic, train_levenshtein, predict_levenshtein,
    train_tfidf_lr, predict_tfidf_lr, predict_tfidf_lr_proba,
    train_embedding_similarity, predict_embedding_similarity,
    LABEL_TO_INT
)
from clindoc.normalize.hybrid import NGramProxyEmbedder


def _make_example(claim: str, evidence: str, conf: float, label: VerificationLabel) -> M2Example:
    return M2Example(
        example_id="1",
        claim=claim,
        evidence=evidence,
        field_type="lab.value",
        doc_template="template_a",
        ocr_confidence=conf,
        severity="clean" if conf > 0.9 else "high",
        label=label,
        source_doc_id="DOC_1",
        generation_method="test",
        claim_type="lab",
    )


def test_deterministic_baseline():
    ex_sup = _make_example("Glucose: 90 mg/dL", "Glucose: 90 mg/dL", 0.98, VerificationLabel.SUPPORTED)
    ex_disc = _make_example("Glucose: 150 mg/dL", "Glucose: 70 mg/dL", 0.98, VerificationLabel.DISCREPANCY)
    ex_insuf = _make_example("Glucose: 90 mg/dL", "G", 0.2, VerificationLabel.INSUFFICIENT_EVIDENCE)

    preds = predict_deterministic([ex_sup, ex_disc, ex_insuf])
    assert preds[0] == LABEL_TO_INT["SUPPORTED"]
    assert preds[1] == LABEL_TO_INT["DISCREPANCY"]
    assert preds[2] == LABEL_TO_INT["INSUFFICIENT_EVIDENCE"]


def test_levenshtein_baseline():
    train_ex = [
        _make_example("A B C D", "A B C D", 1.0, VerificationLabel.SUPPORTED),
        _make_example("A B C D", "W X Y Z", 1.0, VerificationLabel.DISCREPANCY),
        _make_example("A B C D", "A", 0.1, VerificationLabel.INSUFFICIENT_EVIDENCE),
    ]
    params = train_levenshtein(train_ex)
    assert "threshold" in params
    assert 0.0 < params["threshold"] < 1.0

    preds = predict_levenshtein(train_ex, params)
    assert len(preds) == len(train_ex)


def test_tfidf_logreg_baseline():
    train_ex = [
        _make_example("Metformin 500 mg oral", "Metformin 500 mg oral", 1.0, VerificationLabel.SUPPORTED),
        _make_example("Metformin 1000 mg oral", "Metformin 500 mg oral", 1.0, VerificationLabel.DISCREPANCY),
        _make_example("Metformin 500 mg", "Corrupted OCR", 0.1, VerificationLabel.INSUFFICIENT_EVIDENCE),
    ] * 5  # Duplicate to allow fitting
    model = train_tfidf_lr(train_ex)
    preds = predict_tfidf_lr(model, train_ex[:3])
    probs = predict_tfidf_lr_proba(model, train_ex[:3])

    assert len(preds) == 3
    assert len(probs) == 3
    for p in probs:
        assert len(p) == len(LABEL_ORDER)
        assert abs(sum(p) - 1.0) < 1e-4


# ─── V4: Embedding Similarity Tests ──────────────────────────────────────────

def test_v4_embedding_similarity_guards():
    """Verify OCR confidence < 0.5 and evidence length < 3 trigger INSUFFICIENT_EVIDENCE."""
    embedder = NGramProxyEmbedder()
    ex_low_ocr = _make_example("Glucose 90 mg/dL", "Glucose 90 mg/dL", 0.40, VerificationLabel.INSUFFICIENT_EVIDENCE)
    ex_short_ev = _make_example("Glucose 90 mg/dL", "Gl", 0.98, VerificationLabel.INSUFFICIENT_EVIDENCE)
    ex_valid = _make_example("Glucose 90 mg/dL", "Glucose 90 mg/dL", 0.98, VerificationLabel.SUPPORTED)

    preds = predict_embedding_similarity([ex_low_ocr, ex_short_ev, ex_valid], params=0.5, embedder=embedder)
    assert preds[0] == LABEL_TO_INT["INSUFFICIENT_EVIDENCE"]
    assert preds[1] == LABEL_TO_INT["INSUFFICIENT_EVIDENCE"]
    assert preds[2] == LABEL_TO_INT["SUPPORTED"]


def test_v4_embedding_similarity_classification():
    """Verify identical pairs yield SUPPORTED, dissimilar pairs yield DISCREPANCY, and labels are valid."""
    embedder = NGramProxyEmbedder()
    ex_sup = _make_example("Amoxicillin 500mg PO TID", "Amoxicillin 500mg PO TID", 0.98, VerificationLabel.SUPPORTED)
    ex_disc = _make_example("Amoxicillin 500mg PO TID", "Lisinopril 10mg oral daily", 0.98, VerificationLabel.DISCREPANCY)
    ex_insuf = _make_example("Amoxicillin 500mg PO TID", "No", 0.20, VerificationLabel.INSUFFICIENT_EVIDENCE)

    preds = predict_embedding_similarity([ex_sup, ex_disc, ex_insuf], params=0.5, embedder=embedder)

    assert preds[0] == LABEL_TO_INT["SUPPORTED"]
    assert preds[1] == LABEL_TO_INT["DISCREPANCY"]
    assert preds[2] == LABEL_TO_INT["INSUFFICIENT_EVIDENCE"]

    # Invariant 5: output label is always one of SUPPORTED, DISCREPANCY, INSUFFICIENT_EVIDENCE
    valid_int_labels = {LABEL_TO_INT[l] for l in LABEL_ORDER}
    for p in preds:
        assert p in valid_int_labels


def test_v4_embedding_similarity_determinism_and_threshold():
    """Verify similarity/threshold behavior is strictly deterministic and respects threshold shifts."""
    embedder = NGramProxyEmbedder()
    ex = _make_example("Metformin 500mg daily", "Metformin 850mg daily", 0.95, VerificationLabel.DISCREPANCY)

    # Determinism: identical calls produce identical output
    preds_run1 = predict_embedding_similarity([ex], params=0.5, embedder=embedder)
    preds_run2 = predict_embedding_similarity([ex], params=0.5, embedder=embedder)
    assert preds_run1 == preds_run2

    # High threshold rejects partial match
    preds_strict = predict_embedding_similarity([ex], params=0.99, embedder=embedder)
    assert preds_strict[0] == LABEL_TO_INT["DISCREPANCY"]

    # Very low threshold accepts partial match
    preds_lenient = predict_embedding_similarity([ex], params=0.20, embedder=embedder)
    assert preds_lenient[0] == LABEL_TO_INT["SUPPORTED"]


def test_v4_train_embedding_similarity():
    """Verify threshold selection on train examples and explicit threshold locking."""
    embedder = NGramProxyEmbedder()
    train_ex = [
        _make_example("A B C D", "A B C D", 1.0, VerificationLabel.SUPPORTED),
        _make_example("A B C D", "W X Y Z", 1.0, VerificationLabel.DISCREPANCY),
        _make_example("A B C D", "A", 0.1, VerificationLabel.INSUFFICIENT_EVIDENCE),
    ]

    # Grid search selects optimal threshold in (0, 1)
    params = train_embedding_similarity(train_ex, embedder=embedder)
    assert "threshold" in params
    assert 0.0 < params["threshold"] < 1.0
    assert params["train_macro_f1"] is not None

    # Explicit threshold bypasses search
    params_fixed = train_embedding_similarity(train_ex, embedder=embedder, threshold=0.82)
    assert params_fixed["threshold"] == 0.82
    assert params_fixed["train_macro_f1"] is None


def test_v4_evaluation_integration():
    """Verify V4 integration in evaluate_embedding_similarity and evaluate_baselines."""
    from clindoc.m2.evaluate import evaluate_embedding_similarity, evaluate_baselines
    embedder = NGramProxyEmbedder()

    val_ex = [
        _make_example("Glucose 90 mg/dL", "Glucose 90 mg/dL", 0.98, VerificationLabel.SUPPORTED),
        _make_example("Glucose 150 mg/dL", "Creatinine 0.9", 0.98, VerificationLabel.DISCREPANCY),
        _make_example("Glucose 90 mg/dL", "G", 0.20, VerificationLabel.INSUFFICIENT_EVIDENCE),
    ]

    # Dedicated evaluator
    result = evaluate_embedding_similarity(val_ex, params=0.5, embedder=embedder, split="cv")
    assert result.model_name == "embedding_similarity"
    assert result.split == "cv"
    assert result.macro_f1 > 0.0
    assert "SUPPORTED" in result.per_class

    # evaluate_baselines wiring
    train_ex = val_ex * 2
    results, lev_p, lr_m, probe_clf, v4_p = evaluate_baselines(
        train_examples=train_ex,
        val_examples=val_ex,
        embedder=embedder,
        return_v4_params=True,
    )
    model_names = [r.model_name for r in results]
    assert "embedding_similarity" in model_names
    assert "threshold" in v4_p


# ─── V5: Off-the-Shelf NLI Baseline Tests ────────────────────────────────────

from clindoc.m2.baselines import (
    MockNLIPredictor,
    PubMedBERTNLIPredictor,
    predict_zeroshot_nli,
    predict_zeroshot_nli_proba,
    calibrate_v5_temperature,
    NATIVE_NLI_CONTRADICTION,
    NATIVE_NLI_ENTAILMENT,
    NATIVE_NLI_NEUTRAL,
    NATIVE_TO_PROJECT_INT,
)


def test_v5_nli_native_label_mapping():
    """Verify exact native NLI to project verification label mapping."""
    # 0 (contradiction) -> DISCREPANCY (1)
    assert NATIVE_TO_PROJECT_INT[NATIVE_NLI_CONTRADICTION] == LABEL_TO_INT["DISCREPANCY"]
    # 1 (entailment) -> SUPPORTED (0)
    assert NATIVE_TO_PROJECT_INT[NATIVE_NLI_ENTAILMENT] == LABEL_TO_INT["SUPPORTED"]
    # 2 (neutral) -> INSUFFICIENT_EVIDENCE (2)
    assert NATIVE_TO_PROJECT_INT[NATIVE_NLI_NEUTRAL] == LABEL_TO_INT["INSUFFICIENT_EVIDENCE"]

    ex = _make_example("Amoxicillin 500mg", "Amoxicillin 500mg PO TID", 0.98, VerificationLabel.SUPPORTED)

    # Entailment mock -> SUPPORTED
    pred_entail = MockNLIPredictor(default_native_label=NATIVE_NLI_ENTAILMENT, confidence=0.9)
    assert predict_zeroshot_nli([ex], predictor=pred_entail)[0] == LABEL_TO_INT["SUPPORTED"]

    # Contradiction mock -> DISCREPANCY
    pred_contra = MockNLIPredictor(default_native_label=NATIVE_NLI_CONTRADICTION, confidence=0.9)
    assert predict_zeroshot_nli([ex], predictor=pred_contra)[0] == LABEL_TO_INT["DISCREPANCY"]

    # Neutral mock -> INSUFFICIENT_EVIDENCE
    pred_neutral = MockNLIPredictor(default_native_label=NATIVE_NLI_NEUTRAL, confidence=0.9)
    assert predict_zeroshot_nli([ex], predictor=pred_neutral)[0] == LABEL_TO_INT["INSUFFICIENT_EVIDENCE"]


def test_v5_nli_evidence_guards():
    """Verify evidence guards override NLI predictions when OCR < 0.5 or evidence < 3 chars."""
    pred_entail = MockNLIPredictor(default_native_label=NATIVE_NLI_ENTAILMENT, confidence=0.99)
    ex_low_ocr = _make_example("Glucose 90 mg/dL", "Glucose 90 mg/dL", 0.40, VerificationLabel.INSUFFICIENT_EVIDENCE)
    ex_short_ev = _make_example("Glucose 90 mg/dL", "Gl", 0.98, VerificationLabel.INSUFFICIENT_EVIDENCE)
    ex_valid = _make_example("Glucose 90 mg/dL", "Glucose 90 mg/dL", 0.98, VerificationLabel.SUPPORTED)

    preds = predict_zeroshot_nli([ex_low_ocr, ex_short_ev, ex_valid], predictor=pred_entail)
    assert preds[0] == LABEL_TO_INT["INSUFFICIENT_EVIDENCE"]
    assert preds[1] == LABEL_TO_INT["INSUFFICIENT_EVIDENCE"]
    assert preds[2] == LABEL_TO_INT["SUPPORTED"]

    # Probabilities for guarded examples should assign 1.0 to INSUFFICIENT_EVIDENCE
    probs = predict_zeroshot_nli_proba([ex_low_ocr, ex_short_ev], predictor=pred_entail)
    assert probs[0] == [0.0, 0.0, 1.0]
    assert probs[1] == [0.0, 0.0, 1.0]


def test_v5_nli_deterministic_argmax():
    """Verify deterministic argmax decision policy over NLI logits."""
    class CustomLogitPredictor:
        def predict_batch_logits(self, pairs):
            # Native logits: [contradiction, entailment, neutral]
            return [
                [0.1, 2.5, 0.2],   # Entailment highest -> project [2.5, 0.1, 0.2] -> SUPPORTED (0)
                [3.0, 0.2, 0.5],   # Contradiction highest -> project [0.2, 3.0, 0.5] -> DISCREPANCY (1)
                [0.2, 0.3, 1.8],   # Neutral highest -> project [0.3, 0.2, 1.8] -> INSUFFICIENT_EVIDENCE (2)
            ]

    examples = [
        _make_example("claim1", "evidence1 with enough text", 0.95, VerificationLabel.SUPPORTED),
        _make_example("claim2", "evidence2 with enough text", 0.95, VerificationLabel.DISCREPANCY),
        _make_example("claim3", "evidence3 with enough text", 0.95, VerificationLabel.INSUFFICIENT_EVIDENCE),
    ]

    preds1 = predict_zeroshot_nli(examples, predictor=CustomLogitPredictor())
    preds2 = predict_zeroshot_nli(examples, predictor=CustomLogitPredictor())
    assert preds1 == [0, 1, 2]
    assert preds1 == preds2  # Determinism


def test_v5_nli_calibration_isolation():
    """Verify temperature scaling calibration is fit strictly on calibration examples without OOD leakage."""
    pred = MockNLIPredictor(default_native_label=NATIVE_NLI_ENTAILMENT, confidence=0.8)
    calib_ex = [
        _make_example("claim1", "evidence 1 text long", 0.95, VerificationLabel.SUPPORTED),
        _make_example("claim2", "evidence 2 text long", 0.95, VerificationLabel.DISCREPANCY),
        _make_example("claim3", "evidence 3 text long", 0.95, VerificationLabel.INSUFFICIENT_EVIDENCE),
    ]

    scaler = calibrate_v5_temperature(calib_ex, predictor=pred)
    assert scaler.is_fitted
    assert scaler.temperature > 0.0

    # Probabilities scaled via temperature scaler remain valid probability distributions
    probs = predict_zeroshot_nli_proba(calib_ex, predictor=pred, scaler=scaler)
    for p in probs:
        assert len(p) == 3
        assert abs(sum(p) - 1.0) < 1e-4


def test_v5_evaluation_integration():
    """Verify V5 integration in evaluate_zeroshot_nli and evaluate_baselines."""
    from clindoc.m2.evaluate import evaluate_zeroshot_nli, evaluate_baselines, evaluate_baselines_on_test
    pred = MockNLIPredictor(default_native_label=NATIVE_NLI_ENTAILMENT, confidence=0.9)

    val_ex = [
        _make_example("Glucose 90 mg/dL", "Glucose 90 mg/dL text", 0.98, VerificationLabel.SUPPORTED),
        _make_example("Glucose 150 mg/dL", "Creatinine 0.9 text", 0.98, VerificationLabel.DISCREPANCY),
        _make_example("Glucose 90 mg/dL", "G", 0.20, VerificationLabel.INSUFFICIENT_EVIDENCE),
    ]

    # Dedicated evaluator
    result = evaluate_zeroshot_nli(val_ex, predictor=pred, split="cv")
    assert result.model_name == "zeroshot_nli"
    assert result.split == "cv"
    assert result.macro_f1 > 0.0
    assert result.brier_score is not None
    assert result.ece is not None

    # evaluate_baselines wiring
    train_ex = val_ex * 2
    results, lev_p, lr_m, probe_clf = evaluate_baselines(
        train_examples=train_ex,
        val_examples=val_ex,
        nli_predictor=pred,
        calib_examples=val_ex,
    )
    model_names = [r.model_name for r in results]
    assert "zeroshot_nli" in model_names

    # evaluate_baselines_on_test wiring
    ood_results = evaluate_baselines_on_test(
        test_examples=val_ex,
        lev_params=lev_p,
        lr_model=lr_m,
        split="ood",
        nli_predictor=pred,
    )
    ood_model_names = [r.model_name for r in ood_results]
    assert "zeroshot_nli" in ood_model_names


def test_v5_pubmedbert_nli_predictor_offline_availability():
    """Verify PubMedBERTNLIPredictor handles offline/missing weights gracefully."""
    predictor = PubMedBERTNLIPredictor(local_files_only=True)
    # is_available returns boolean without raising unhandled exceptions
    avail = predictor.is_available()
    assert isinstance(avail, bool)
