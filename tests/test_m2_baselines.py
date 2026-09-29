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
