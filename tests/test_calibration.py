"""Unit tests for multi-class TemperatureScaler.

Authority: PROJECT_SPEC §17, DEC-009, AUDIT-06
"""
import numpy as np
import pytest
import torch

from clindoc.m2.calibration import IsotonicConfidenceCalibrator, TemperatureScaler


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

    # High confidence with logits should be scaled down when T > 1.0
    logits = np.array([4.0, 1.0, 0.5])
    raw_conf = float(np.max(np.exp(logits) / np.sum(np.exp(logits))))
    cal_conf = scaler.calibrate_confidence(logits)
    assert cal_conf < raw_conf
    assert 0.0 <= cal_conf <= 1.0

    # Calling calibrate_confidence with scalar must raise ValueError (DEC-018)
    with pytest.raises(ValueError, match="IsotonicConfidenceCalibrator"):
        scaler.calibrate_confidence(0.99)


def test_isotonic_calibrator_fit_and_predict():
    np.random.seed(42)
    # Synthetic overconfident calibration data
    confidences = np.array([0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 0.95])
    labels = np.array([0, 0, 0, 1, 0, 1, 1, 1])

    calibrator = IsotonicConfidenceCalibrator()
    assert not calibrator.is_fitted

    calibrator.fit(confidences, labels)
    assert calibrator.is_fitted

    # Predict on array
    cal_preds = calibrator.predict(confidences)
    assert isinstance(cal_preds, np.ndarray)
    assert len(cal_preds) == len(confidences)
    assert np.all((cal_preds >= 0.0) & (cal_preds <= 1.0))

    # Verify monotonicity
    assert np.all(np.diff(cal_preds) >= -1e-6)

    # Predict on scalar
    single_cal = calibrator.predict(0.85)
    assert isinstance(single_cal, float)
    assert 0.0 <= single_cal <= 1.0


def test_isotonic_calibrator_unfitted_raises():
    calibrator = IsotonicConfidenceCalibrator()
    with pytest.raises(RuntimeError, match="must be fit before predict"):
        calibrator.predict(0.5)


def test_isotonic_calibrator_invalid_inputs():
    calibrator = IsotonicConfidenceCalibrator()

    # Empty inputs
    with pytest.raises(ValueError, match="empty"):
        calibrator.fit([], [])

    # Mismatched lengths
    with pytest.raises(ValueError, match="Length mismatch"):
        calibrator.fit([0.2, 0.8], [1])

    # Non-binary labels
    with pytest.raises(ValueError, match="Binary labels"):
        calibrator.fit([0.2, 0.8], [0, 2])

    # Insufficient class diversity (edge case: all 1s or all 0s)
    with pytest.raises(ValueError, match="Insufficient class diversity"):
        calibrator.fit([0.2, 0.8], [1, 1])

    with pytest.raises(ValueError, match="Insufficient class diversity"):
        calibrator.fit([0.2, 0.8], [0, 0])

    # Out of range confidences (<0 or >1)
    with pytest.raises(ValueError, match="in range"):
        calibrator.fit([-0.1, 0.8], [0, 1])

    with pytest.raises(ValueError, match="in range"):
        calibrator.fit([0.2, 1.2], [0, 1])

    # Non 1D inputs
    with pytest.raises(ValueError, match="1-dimensional"):
        calibrator.fit(np.zeros((2, 2)), [0, 1])

    # Predict out of range
    calibrator.fit([0.2, 0.8], [0, 1])
    with pytest.raises(ValueError, match="range"):
        calibrator.predict(-0.01)

    with pytest.raises(ValueError, match="range"):
        calibrator.predict(1.05)


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
