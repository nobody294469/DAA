"""Stage B: Learned Semantic Verifier using BioClinical ModernBERT.

Authority:
  - PROJECT_SPEC §5 #8, §10, §12.2, §17.1, §21.5
  - DEC-005: Stage B escalation threshold (provisional default: 0.60)
  - DEC-009: Temperature scaling calibration for multi-class logits
  - DEC-019: Field verification tiering (Tier 1 learned verification)

Stage B processes Tier 1 clinical claims through a fine-tuned BioClinical
ModernBERT 3-way sequence classifier with temperature scaling calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Union

import numpy as np

from clindoc.extract.contracts import Claim, Evidence
from clindoc.m2.calibration import TemperatureScaler
from clindoc.m2.model import (
    INT_TO_LABEL,
    LABEL_ORDER,
    LABEL_TO_INT,
    MODEL_CHECKPOINT,
    format_input,
)
from clindoc.schema.verification import VerificationLabel

DEFAULT_ESCALATION_THRESHOLD: float = 0.60  # DEC-005 provisional default


@dataclass(frozen=True)
class StageBResult:
    """Output from Stage B learned semantic verification."""

    label: VerificationLabel
    raw_confidence: float
    calibrated_confidence: float
    logits: np.ndarray
    escalated: bool
    escalation_reason: Optional[str] = None


class ModelPredictor(Protocol):
    """Protocol for Stage B model prediction."""

    def predict_logits(self, field_type: str, claim_value: str, evidence_text: str) -> np.ndarray:
        """Return 1D numpy array of 3 logits corresponding to LABEL_ORDER."""
        ...


class MockModelPredictor:
    """Deterministic mock predictor for unit tests and offline execution."""

    def __init__(self, default_label: str = "SUPPORTED", confidence: float = 0.95) -> None:
        self.default_label = default_label
        self.confidence = confidence

    def predict_logits(self, field_type: str, claim_value: str, evidence_text: str) -> np.ndarray:
        target_idx = LABEL_TO_INT.get(self.default_label, 0)
        # Construct synthetic logits
        logits = np.zeros(len(LABEL_ORDER), dtype=np.float32)
        logits[target_idx] = float(np.log(self.confidence / (1.0 - self.confidence + 1e-6)))
        return logits


class LearnedVerifier:
    """Stage B learned semantic verifier backed by BioClinical ModernBERT.

    Evaluates sequence pairs [FIELD] {field} [CLAIM] {claim} [EVIDENCE] {evidence}
    and scales logits via TemperatureScaler (DEC-009).
    """

    def __init__(
        self,
        model_path: Optional[Union[str, Path]] = None,
        scaler: Optional[TemperatureScaler] = None,
        escalation_threshold: float = DEFAULT_ESCALATION_THRESHOLD,
        custom_predictor: Optional[ModelPredictor] = None,
        device: Optional[str] = None,
        eager_load: bool = True,
    ) -> None:
        self.escalation_threshold = float(escalation_threshold)
        self.scaler = scaler or TemperatureScaler(init_temperature=1.0)
        if not self.scaler.is_fitted:
            # If not pre-fitted, set identity temperature so it can scale logits cleanly
            self.scaler.is_fitted = True

        self._predictor: Optional[ModelPredictor] = custom_predictor
        self._model = None
        self._tokenizer = None
        self._device = device
        self.model_path = model_path
        self._load_attempted: bool = False

        if eager_load and self.model_path is not None and self._predictor is None:
            self.load_model()

    @property
    def is_model_loaded(self) -> bool:
        """Check whether a neural model or custom predictor is ready for inference."""
        return self._model is not None or self._predictor is not None

    def load_model(self) -> bool:
        """Load ModernBERT model and tokenizer once into memory outside the evaluation loop.

        Guarantees single initialization: if already loaded or if a custom predictor
        is present, returns True immediately without reloading.
        """
        if self._predictor is not None:
            return True
        if self._model is not None:
            return True
        if self._load_attempted:
            return self._model is not None
        if not self.model_path or not Path(self.model_path).exists():
            self._load_attempted = True
            return False

        self._load_attempted = True
        try:
            import logging
            import torch
            from transformers import AutoTokenizer
            from clindoc.m2.model import ClinicalVerifier, MODEL_CHECKPOINT

            if self._device is None:
                self._device = "cuda" if torch.cuda.is_available() else "cpu"
            model_path_obj = Path(self.model_path)

            if model_path_obj.is_dir():
                self._tokenizer = AutoTokenizer.from_pretrained(str(model_path_obj))
                model = ClinicalVerifier(model_id=str(model_path_obj), aux_mode="none")
            else:
                self._tokenizer = AutoTokenizer.from_pretrained(MODEL_CHECKPOINT)
                # Load checkpoint to CPU first to prevent GPU memory spikes (DEC-006 / Colab T4 stability)
                ckpt = torch.load(str(model_path_obj), map_location="cpu", weights_only=False)
                aux_mode = ckpt.get("aux_mode", "none") if isinstance(ckpt, dict) else "none"
                model = ClinicalVerifier(model_id=MODEL_CHECKPOINT, aux_mode=aux_mode)
                if isinstance(ckpt, dict) and "model_state" in ckpt:
                    model.load_state_dict(ckpt["model_state"])
                elif isinstance(ckpt, dict):
                    model.load_state_dict(ckpt)
                # DEC-009: load fitted temperature from checkpoint if present
                if isinstance(ckpt, dict) and "temperature" in ckpt:
                    fitted_temp = float(ckpt["temperature"])
                    if fitted_temp > 0:
                        self.scaler = TemperatureScaler(init_temperature=fitted_temp)
                        self.scaler.is_fitted = True
                del ckpt
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

            # Transfer to target device with graceful CPU fallback on CUDA OOM
            try:
                self._model = model.to(self._device)
            except (torch.cuda.OutOfMemoryError, RuntimeError) as oom_err:
                if self._device != "cpu":
                    import logging
                    logging.getLogger(__name__).warning(
                        "CUDA allocation failed for Stage B ModernBERT (%s). Gracefully falling back to cpu.",
                        oom_err,
                    )
                    self._device = "cpu"
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    self._model = model.to("cpu")
                else:
                    raise

            self._model.eval()
            return True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to load Stage B ModernBERT model from %s: %s", self.model_path, e
            )
            self._model = None
            return False

    def _lazy_load_model(self) -> bool:
        """Deprecated alias for load_model() retained for backward compatibility."""
        return self.load_model()

    def verify(
        self,
        claim: Claim,
        evidence: Optional[Evidence] = None,
        stage_a_label: Optional[VerificationLabel] = None,
        context_text: Optional[str] = None,
    ) -> StageBResult:
        """Run Stage B inference on claim and evidence.

        Args:
            claim: Clinical claim.
            evidence: Linked evidence.
            stage_a_label: Intermediate label from Stage A (if any).
            context_text: Optional local document context window.

        Returns:
            StageBResult with predicted label, calibrated confidence, logits, and escalation state.
        """
        ev_text = evidence.text.strip() if (evidence and evidence.text) else ""
        if not ev_text:
            # Missing evidence: definitive INSUFFICIENT_EVIDENCE
            logits = np.array([-4.0, -4.0, 4.0], dtype=np.float32)
            raw_conf = 0.98
            cal_conf = self.scaler.calibrate_confidence(logits)
            return StageBResult(
                label="INSUFFICIENT_EVIDENCE",
                raw_confidence=raw_conf,
                calibrated_confidence=cal_conf,
                logits=logits,
                escalated=True,  # INSUFFICIENT_EVIDENCE always escalates per §12.2
                escalation_reason="insufficient_evidence_precaution",
            )

        # 1. Obtain Model Logits
        logits = self._compute_logits(claim.field_type, claim.value, ev_text)

        # 2. Raw Softmax Confidence
        exp_logits = np.exp(logits - np.max(logits))
        probs = exp_logits / np.sum(exp_logits)
        pred_idx = int(np.argmax(probs))
        predicted_label: VerificationLabel = INT_TO_LABEL[pred_idx]
        raw_confidence = float(probs[pred_idx])

        # 3. Calibrated Confidence (DEC-009)
        calibrated_confidence = float(self.scaler.calibrate_confidence(logits))

        # 4. Escalation Evaluation per PROJECT_SPEC §12.2 & DEC-005
        escalate = False
        escalation_reason = None

        # Rule A: Stage A disagreement
        if stage_a_label is not None and stage_a_label != predicted_label:
            escalate = True
            escalation_reason = f"stage_a_disagreement (A={stage_a_label}, B={predicted_label})"

        # Rule B: Stage B confidence below escalation threshold
        elif calibrated_confidence < self.escalation_threshold:
            escalate = True
            escalation_reason = (
                f"low_confidence ({calibrated_confidence:.4f} < {self.escalation_threshold:.2f})"
            )

        # Rule C: INSUFFICIENT_EVIDENCE prediction (conservative safety check)
        elif predicted_label == "INSUFFICIENT_EVIDENCE":
            escalate = True
            escalation_reason = "insufficient_evidence_precaution"

        return StageBResult(
            label=predicted_label,
            raw_confidence=raw_confidence,
            calibrated_confidence=calibrated_confidence,
            logits=logits,
            escalated=escalate,
            escalation_reason=escalation_reason,
        )

    def get_calibrated_probabilities(
        self, result_or_logits: Union[StageBResult, np.ndarray]
    ) -> np.ndarray:
        """Compute the 3-class calibrated probability distribution (DEC-009)."""
        logits = result_or_logits.logits if isinstance(result_or_logits, StageBResult) else result_or_logits
        return self.scaler.predict_proba(logits)

    def _compute_logits(self, field_type: str, claim_value: str, evidence_text: str) -> np.ndarray:
        """Compute 3-class logits from predictor or neural model."""
        if self._predictor is not None:
            return self._predictor.predict_logits(field_type, claim_value, evidence_text)

        # Reuse pre-loaded ModernBERT model instance (never load/reload inside per-example loop)
        if self._model is not None:
            import torch
            formatted = format_input(field_type, claim_value, evidence_text)
            toks = self._tokenizer(
                formatted,
                padding=True,
                truncation=True,
                max_length=256,
                return_tensors="pt",
            )
            if hasattr(toks, "to"):
                toks = toks.to(self._device)
            else:
                toks = {k: v.to(self._device) if hasattr(v, "to") else v for k, v in toks.items()}
            with torch.no_grad():
                logits_tensor = self._model(
                    input_ids=toks["input_ids"],
                    attention_mask=toks["attention_mask"],
                )
            return logits_tensor.squeeze(0).cpu().numpy()

        # Fallback heuristic predictor for offline execution without downloaded neural weights
        return self._heuristic_logits(field_type, claim_value, evidence_text)

    def _heuristic_logits(self, field_type: str, claim_value: str, evidence_text: str) -> np.ndarray:
        """Lightweight heuristic logit generator when neural weights are offline."""
        c_val = claim_value.strip().lower()
        e_text = evidence_text.strip().lower()

        if c_val in e_text:
            # Clear support
            return np.array([3.5, -2.0, -3.0], dtype=np.float32)
        elif not e_text:
            # Insufficient evidence
            return np.array([-3.0, -3.0, 3.5], dtype=np.float32)
        else:
            # Apparent discrepancy
            return np.array([-2.0, 3.0, -2.5], dtype=np.float32)
