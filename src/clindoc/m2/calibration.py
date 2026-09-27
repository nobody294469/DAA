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
        predicted_class_prob: float,
        raw_logits: Optional[Union[np.ndarray, torch.Tensor]] = None,
    ) -> float:
        """Calibrate a single scalar confidence score given raw logits or probability."""
        if raw_logits is not None:
            probs = self.predict_proba(raw_logits)
            return float(np.max(probs))
        # Fallback power scaling approximation if raw logits not provided
        if not self.is_fitted or self.temperature == 1.0:
            return float(predicted_class_prob)
        # Power scaling mapping: p^(1/T) / (p^(1/T) + (1-p)^(1/T))
        p = np.clip(predicted_class_prob, 1e-6, 1.0 - 1e-6)
        inv_t = 1.0 / self.temperature
        p_pow = p ** inv_t
        q_pow = (1.0 - p) ** inv_t
        calibrated = p_pow / (p_pow + q_pow)
        return float(np.clip(calibrated, 0.0, 1.0))

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


__all__ = ["TemperatureScaler", "ece", "reliability_data"]
