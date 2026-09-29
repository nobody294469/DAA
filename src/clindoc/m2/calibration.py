"""Calibration utilities for M2 learned verification models.

Authority: PROJECT_SPEC §17, DEC-009, PHASE0_ARCHITECTURE_RESOLUTION AUDIT-06
"""
from __future__ import annotations

from typing import Any, Optional, Union
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

from clindoc.m2.metrics import ece, reliability_data


class TemperatureScaler:
    """Multi-class Temperature Scaling for post-hoc probability calibration.

    Fits a single learned scalar parameter T > 0 on validation/calibration logits:
        p_calibrated = softmax(logits / T)

    Conforms to DEC-009 (multi-class temperature scaling on held-out calibration split).
    """

    def __init__(self, init_temperature: float = 1.0, max_iter: int = 100, lr: float = 0.01) -> None:
        if init_temperature <= 0:
            raise ValueError(f"Initial temperature must be positive, got {init_temperature}")
        self.init_temperature = float(init_temperature)
        self.max_iter = max_iter
        self.lr = lr
        self.temperature: float = self.init_temperature
        self.is_fitted: bool = False

    def fit(
        self,
        logits: Union[np.ndarray, torch.Tensor],
        labels: Union[np.ndarray, torch.Tensor],
    ) -> "TemperatureScaler":
        """Fit optimal scalar temperature T on calibration logits and ground-truth labels."""
        if isinstance(logits, np.ndarray):
            logits_tensor = torch.tensor(logits, dtype=torch.float32)
        else:
            logits_tensor = logits.detach().clone().float()

        if isinstance(labels, np.ndarray):
            labels_tensor = torch.tensor(labels, dtype=torch.long)
        else:
            labels_tensor = labels.detach().clone().long()

        if logits_tensor.ndim != 2:
            raise ValueError(f"Logits must be 2D (N, K), got shape {logits_tensor.shape}")
        if labels_tensor.ndim != 1:
            raise ValueError(f"Labels must be 1D (N,), got shape {labels_tensor.shape}")
        if logits_tensor.shape[0] != labels_tensor.shape[0]:
            raise ValueError(
                f"Batch size mismatch: logits {logits_tensor.shape[0]} vs labels {labels_tensor.shape[0]}"
            )

        # Log temperature parameter to ensure T > 0
        log_temp = nn.Parameter(torch.tensor([np.log(self.init_temperature)], dtype=torch.float32))
        nll_criterion = nn.CrossEntropyLoss()

        optimizer = optim.LBFGS([log_temp], lr=self.lr, max_iter=self.max_iter)

        def eval_step() -> torch.Tensor:
            optimizer.zero_grad()
            temp = torch.exp(log_temp).clamp(min=1e-3, max=100.0)
            scaled_logits = logits_tensor / temp
            loss = nll_criterion(scaled_logits, labels_tensor)
            loss.backward()
            return loss

        optimizer.step(eval_step)

        self.temperature = float(torch.exp(log_temp).detach().item())
        self.is_fitted = True
        return self

    def transform(self, logits: Union[np.ndarray, torch.Tensor]) -> np.ndarray:
        """Scale logits by fitted temperature: z / T."""
        if isinstance(logits, torch.Tensor):
            logits_np = logits.detach().cpu().numpy()
        else:
            logits_np = np.asarray(logits, dtype=np.float32)

        return logits_np / self.temperature

    def predict_proba(self, logits: Union[np.ndarray, torch.Tensor]) -> np.ndarray:
        """Compute calibrated probabilities via softmax(logits / T)."""
        scaled = self.transform(logits)
        # Numerically stable softmax
        exp_scaled = np.exp(scaled - np.max(scaled, axis=-1, keepdims=True))
        return exp_scaled / np.sum(exp_scaled, axis=-1, keepdims=True)

    def calibrate_confidence(
        self,
        raw_logits: Union[np.ndarray, torch.Tensor],
    ) -> float:
        """Calibrate confidence for a multi-class logit vector via softmax(logits / T).

        Authority: DEC-009.
        Note: For Stage C LLM self-reported scalar confidences, use IsotonicConfidenceCalibrator (DEC-018).
        """
        if not self.is_fitted:
            raise RuntimeError("TemperatureScaler must be fit before calling calibrate_confidence")
        if isinstance(raw_logits, (int, float)):
            raise ValueError(
                "Multi-class TemperatureScaler operates on logits vectors. "
                "For Stage C scalar self-reported confidence calibration, use "
                "IsotonicConfidenceCalibrator per DEC-018."
            )
        probs = self.predict_proba(raw_logits)
        return float(np.max(probs))

    def evaluate(
        self,
        logits: Union[np.ndarray, torch.Tensor],
        labels: Union[np.ndarray, torch.Tensor],
        n_bins: int = 10,
    ) -> dict[str, float]:
        """Compute ECE and NLL before and after calibration."""
        if isinstance(logits, torch.Tensor):
            logits_np = logits.detach().cpu().numpy()
        else:
            logits_np = np.asarray(logits, dtype=np.float32)

        if isinstance(labels, torch.Tensor):
            labels_np = labels.detach().cpu().numpy()
        else:
            labels_np = np.asarray(labels, dtype=np.int64)

        # Uncalibrated probabilities
        uncal_exp = np.exp(logits_np - np.max(logits_np, axis=-1, keepdims=True))
        uncal_probs = uncal_exp / np.sum(uncal_exp, axis=-1, keepdims=True)
        uncal_preds = np.argmax(uncal_probs, axis=-1)
        uncal_conf = np.max(uncal_probs, axis=-1)

        # Calibrated probabilities
        cal_probs = self.predict_proba(logits_np)
        cal_preds = np.argmax(cal_probs, axis=-1)
        cal_conf = np.max(cal_probs, axis=-1)

        ece_before = ece(labels_np.tolist(), uncal_probs.tolist(), n_bins=n_bins)
        ece_after = ece(labels_np.tolist(), cal_probs.tolist(), n_bins=n_bins)

        # NLL computation
        n_samples = len(labels_np)
        eps = 1e-12
        nll_before = -float(np.mean(np.log(np.clip(uncal_probs[np.arange(n_samples), labels_np], eps, 1.0))))
        nll_after = -float(np.mean(np.log(np.clip(cal_probs[np.arange(n_samples), labels_np], eps, 1.0))))

        return {
            "temperature": self.temperature,
            "ece_before": ece_before,
            "ece_after": ece_after,
            "nll_before": nll_before,
            "nll_after": nll_after,
        }


class IsotonicConfidenceCalibrator:
    """1D post-hoc confidence calibrator for Stage C LLM self-reported scalar confidence.

    Authority: DEC-018, PROJECT_SPEC §12.2 (Stage C), §17.1.
    Maps uncalibrated scalar confidence c in [0, 1] to empirical probability
    of correctness using a non-decreasing isotonic regression model fit on
    the held-out calibration split (DEC-009, DEC-018).
    """

    def __init__(self, y_min: float = 0.0, y_max: float = 1.0, out_of_bounds: str = "clip") -> None:
        self.y_min = float(y_min)
        self.y_max = float(y_max)
        self.out_of_bounds = out_of_bounds
        self.is_fitted: bool = False
        from sklearn.isotonic import IsotonicRegression
        self._regressor = IsotonicRegression(
            y_min=self.y_min,
            y_max=self.y_max,
            increasing=True,
            out_of_bounds=self.out_of_bounds,
        )

    def fit(
        self,
        confidences: Union[Sequence[float], np.ndarray],
        binary_labels: Union[Sequence[int], np.ndarray],
    ) -> "IsotonicConfidenceCalibrator":
        """Fit isotonic calibration mapping on held-out calibration split (DEC-018).

        Args:
            confidences: 1D array of predicted scalar confidences in [0, 1].
            binary_labels: 1D array of binary ground-truth correctness indicators (1 = correct, 0 = incorrect).

        Returns:
            self
        """
        x = np.asarray(confidences, dtype=np.float64)
        y = np.asarray(binary_labels, dtype=np.float64)

        if x.ndim != 1:
            raise ValueError(f"Confidences must be 1-dimensional, got shape {x.shape}")
        if y.ndim != 1:
            raise ValueError(f"Binary labels must be 1-dimensional, got shape {y.shape}")
        if len(x) == 0:
            raise ValueError("Cannot fit calibrator on empty dataset")
        if len(x) != len(y):
            raise ValueError(f"Length mismatch: {len(x)} confidences vs {len(y)} labels")
        if np.any((x < 0.0) | (x > 1.0)):
            raise ValueError("All confidences must be in range [0.0, 1.0]")
        if not np.all(np.isin(y, [0, 1])):
            raise ValueError("Binary labels must only contain 0 or 1")
        if len(np.unique(y)) < 2:
            raise ValueError(
                "Insufficient class diversity: binary_labels must contain both positive (1) "
                "and negative (0) examples to fit a calibration curve"
            )

        self._regressor.fit(x, y)
        self.is_fitted = True
        return self

    def predict(
        self,
        confidences: Union[float, Sequence[float], np.ndarray],
    ) -> Union[float, np.ndarray]:
        """Calibrate raw confidence score(s).

        Args:
            confidences: scalar float or 1D array of confidences in [0, 1].

        Returns:
            Calibrated confidence float or 1D numpy array in [0, 1].
        """
        if not self.is_fitted:
            raise RuntimeError("IsotonicConfidenceCalibrator must be fit before predict()")

        is_scalar = isinstance(confidences, (int, float, np.floating))
        x = np.atleast_1d(np.asarray(confidences, dtype=np.float64))

        if np.any((x < 0.0) | (x > 1.0)):
            raise ValueError("All input confidences must be in range [0.0, 1.0]")

        calibrated = self._regressor.predict(x)
        calibrated = np.clip(calibrated, self.y_min, self.y_max)

        if is_scalar:
            return float(calibrated[0])
        return calibrated


__all__ = ["TemperatureScaler", "IsotonicConfidenceCalibrator", "ece", "reliability_data"]
