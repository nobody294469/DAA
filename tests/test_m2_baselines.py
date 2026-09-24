"""Unit tests for M2 baseline models (deterministic, Levenshtein, TF-IDF+LR)."""
import pytest
from clindoc.m2.schema import M2Example, VerificationLabel, LABEL_ORDER
from clindoc.m2.baselines import (
    predict_deterministic, train_levenshtein, predict_levenshtein,
    train_tfidf_lr, predict_tfidf_lr, predict_tfidf_lr_proba,
    LABEL_TO_INT
)


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
