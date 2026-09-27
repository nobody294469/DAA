"""Unit tests for multi-class TemperatureScaler.

Authority: PROJECT_SPEC §17, DEC-009, AUDIT-06
"""
import numpy as np
import pytest
import torch

from clindoc.m2.calibration import TemperatureScaler


def test_temperature_scaler_initialization():
    scaler = TemperatureScaler(init_temperature=1.5)
    assert scaler.temperature == 1.5
    assert not scaler.is_fitted

    with pytest.raises(ValueError):
        TemperatureScaler(init_temperature=-0.5)


def test_temperature_scaler_fit_and_predict():
    # Synthetic logits for 3 classes
    np.random.seed(42)
    torch.manual_seed(42)

    n_samples = 200
    n_classes = 3
    # Overconfident uncalibrated logits
    true_labels = np.random.choice(n_classes, size=n_samples)
    logits = np.random.randn(n_samples, n_classes) * 3.0
    # Bias true class
    for i in range(n_samples):
        logits[i, true_labels[i]] += 2.0

    scaler = TemperatureScaler(init_temperature=1.0)
    scaler.fit(logits, true_labels)

    assert scaler.is_fitted
    assert scaler.temperature > 0.0

    # Predictions
    probs = scaler.predict_proba(logits)
    assert probs.shape == (n_samples, n_classes)
    # Probabilities sum to 1
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-5)
    # All probabilities in [0, 1]
    assert np.all((probs >= 0.0) & (probs <= 1.0))


def test_temperature_scaler_confidence_calibration():
    scaler = TemperatureScaler(init_temperature=2.0)
    scaler.is_fitted = True

    # High confidence should be scaled down when T > 1.0
    cal_conf = scaler.calibrate_confidence(0.99)
    assert cal_conf < 0.99
    assert cal_conf > 0.5


def test_temperature_scaler_evaluation():
    np.random.seed(42)
    n_samples = 150
    n_classes = 3
    true_labels = np.random.choice(n_classes, size=n_samples)
    logits = np.random.randn(n_samples, n_classes) * 2.5
    for i in range(n_samples):
        logits[i, true_labels[i]] += 1.5

    scaler = TemperatureScaler()
    scaler.fit(logits, true_labels)
    eval_results = scaler.evaluate(logits, true_labels, n_bins=5)

    assert "ece_before" in eval_results
    assert "ece_after" in eval_results
    assert "nll_before" in eval_results
    assert "nll_after" in eval_results
    assert eval_results["temperature"] > 0
