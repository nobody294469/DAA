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
from clindoc.config import ProductionProfileError, RuntimeProfile, get_active_profile

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


def load_safe_checkpoint(
    checkpoint_path: Union[str, Path],
    profile: Union[RuntimeProfile, str] = RuntimeProfile.DEV,
    require_weights_only: Optional[bool] = None,
) -> dict[str, Any]:
    """Safely deserialize and validate a PyTorch model checkpoint.

    Enforces PD-004, PD-018, and Phase 8.2 Task 2 security invariants:
      - In PRODUCTION profile, strictly requires weights_only=True (never False).
      - In DEV profile, defaults to weights_only=True unless explicitly overridden.
      - Verifies that the checkpoint file exists and is a regular file.
      - Deserializes with torch.load(..., weights_only=weights_only).
      - Validates that the deserialized object is a dictionary.
      - Validates that either 'model_state' or a tensor state_dict is present.
      - Validates that all tensor weights in the state_dict are torch.Tensor instances.
      - Fails closed with ProductionProfileError upon missing, malformed, or invalid checkpoints.

    Args:
        checkpoint_path: Path to the .pt / .pth checkpoint file.
        profile: Active runtime profile (DEV or PRODUCTION).
        require_weights_only: Explicit flag for weights_only. If False in PRODUCTION, raises immediately.

    Returns:
        The deserialized checkpoint dictionary.

    Raises:
        FileNotFoundError: If checkpoint file does not exist (under DEV profile).
        ProductionProfileError: If any security invariant or structural validation fails (under PRODUCTION profile).
        ValueError: If checkpoint structure is invalid (under DEV profile).
    """
    import torch

    profile_obj = RuntimeProfile.from_string(profile) if isinstance(profile, str) else profile
    path = Path(checkpoint_path)

    # 1. Existence and file type validation
    if not path.exists() or not path.is_file():
        msg = f"ModernBERT checkpoint missing or not found at: {path}"
        if profile_obj == RuntimeProfile.PRODUCTION:
            raise ProductionProfileError(msg)
        raise FileNotFoundError(msg)

    # 2. Strict enforcement of weights_only policy
    if profile_obj == RuntimeProfile.PRODUCTION:
        if require_weights_only is False:
            raise ProductionProfileError(
                "torch.load(..., weights_only=False) is strictly forbidden in PRODUCTION profile."
            )
        weights_only = True
    else:
        weights_only = True if require_weights_only is None else bool(require_weights_only)

    # 3. Deserialization attempt
    try:
        ckpt = torch.load(str(path), map_location="cpu", weights_only=weights_only)
    except Exception as err:
        if profile_obj == RuntimeProfile.PRODUCTION:
            raise ProductionProfileError(
                f"Safe checkpoint deserialization failed for '{path}' (weights_only={weights_only}): {err}"
            ) from err
        raise

    # 4. Root structure validation
    if not isinstance(ckpt, dict):
        msg = f"Invalid checkpoint structure in '{path}': expected dictionary, got {type(ckpt).__name__}"
        if profile_obj == RuntimeProfile.PRODUCTION:
            raise ProductionProfileError(msg)
        raise ValueError(msg)

    # 5. Extract and validate state_dict
    if "model_state" in ckpt:
        state_dict = ckpt["model_state"]
        if not isinstance(state_dict, dict):
            msg = f"Invalid checkpoint structure in '{path}': 'model_state' must be a dictionary, got {type(state_dict).__name__}"
            if profile_obj == RuntimeProfile.PRODUCTION:
                raise ProductionProfileError(msg)
            raise ValueError(msg)
    else:
        state_dict = ckpt

    if not state_dict:
        msg = f"Invalid checkpoint structure in '{path}': empty model weights state dictionary."
        if profile_obj == RuntimeProfile.PRODUCTION:
            raise ProductionProfileError(msg)
        raise ValueError(msg)

    for k, v in state_dict.items():
        if not isinstance(k, str):
            msg = f"Invalid state_dict key type in '{path}': expected str, got {type(k).__name__}"
            if profile_obj == RuntimeProfile.PRODUCTION:
                raise ProductionProfileError(msg)
            raise ValueError(msg)
        if not isinstance(v, torch.Tensor):
            msg = f"Invalid state_dict tensor type for key '{k}' in '{path}': expected torch.Tensor, got {type(v).__name__}"
            if profile_obj == RuntimeProfile.PRODUCTION:
                raise ProductionProfileError(msg)
            raise ValueError(msg)

    return ckpt


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
        profile: Optional[Union[RuntimeProfile, str]] = None,
        allow_heuristic_fallback: Optional[bool] = None,
    ) -> None:
        self.profile = RuntimeProfile.from_string(profile) if profile else get_active_profile()
        if allow_heuristic_fallback is not None:
            self.allow_heuristic_fallback = allow_heuristic_fallback
        else:
            self.allow_heuristic_fallback = (self.profile == RuntimeProfile.DEV)

        if self.profile == RuntimeProfile.PRODUCTION:
            if isinstance(custom_predictor, MockModelPredictor):
                raise ProductionProfileError(
                    "MockModelPredictor is forbidden in PRODUCTION profile."
                )
            if self.allow_heuristic_fallback:
                raise ProductionProfileError(
                    "Heuristic fallback is forbidden in PRODUCTION profile."
                )
            if model_path is None and custom_predictor is None:
                raise ProductionProfileError(
                    "ModernBERT model_path is required in PRODUCTION profile."
                )
            if model_path is not None and not Path(model_path).exists():
                raise ProductionProfileError(
                    f"ModernBERT checkpoint not found at: {model_path}"
                )

        self.escalation_threshold = float(escalation_threshold)
        self.scaler = scaler or TemperatureScaler(init_temperature=1.0)
        if self.profile == RuntimeProfile.PRODUCTION:
            if scaler is not None and not scaler.is_fitted:
                raise ProductionProfileError(
                    "Stage B temperature scaler must be fitted in PRODUCTION profile."
                )
        else:
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
            if self.profile == RuntimeProfile.PRODUCTION:
                raise ProductionProfileError(
                    f"ModernBERT checkpoint missing or not found at: {self.model_path}"
                )
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

            local_files_only = (self.profile == RuntimeProfile.PRODUCTION)
            if model_path_obj.is_dir():
                self._tokenizer = AutoTokenizer.from_pretrained(str(model_path_obj), local_files_only=local_files_only)
                model = ClinicalVerifier(model_id=str(model_path_obj), aux_mode="none", local_files_only=local_files_only)
            else:
                self._tokenizer = AutoTokenizer.from_pretrained(MODEL_CHECKPOINT, local_files_only=local_files_only)
                # Load checkpoint to CPU using safe deserialization (weights_only=True)
                ckpt = load_safe_checkpoint(model_path_obj, profile=self.profile, require_weights_only=True)
                aux_mode = ckpt.get("aux_mode", "none") if isinstance(ckpt, dict) else "none"
                model = ClinicalVerifier(model_id=MODEL_CHECKPOINT, aux_mode=aux_mode, local_files_only=local_files_only)
                
                state_dict = ckpt["model_state"] if "model_state" in ckpt else ckpt
                missing_keys, unexpected_keys = model.load_state_dict(state_dict, strict=False)
                if self.profile == RuntimeProfile.PRODUCTION:
                    if missing_keys:
                        raise ProductionProfileError(
                            f"Missing required model weights in checkpoint '{model_path_obj}': {missing_keys[:5]} "
                            f"(total missing: {len(missing_keys)})"
                        )
                    if unexpected_keys:
                        raise ProductionProfileError(
                            f"Unexpected incompatible keys in checkpoint '{model_path_obj}': {unexpected_keys[:5]} "
                            f"(total unexpected: {len(unexpected_keys)})"
                        )
                elif missing_keys or unexpected_keys:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Stage B checkpoint '%s' loaded with discrepancies: missing=%s, unexpected=%s",
                        model_path_obj, missing_keys[:3], unexpected_keys[:3],
                    )

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
            if self.profile == RuntimeProfile.PRODUCTION:
                if isinstance(e, ProductionProfileError):
                    raise e
                raise ProductionProfileError(
                    f"Failed to load Stage B ModernBERT model from {self.model_path}: {e}"
                ) from e
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
        if self.profile == RuntimeProfile.PRODUCTION:
            if not self.scaler.is_fitted or getattr(self.scaler, "temperature", 1.0) <= 0.0:
                raise ProductionProfileError(
                    "Stage B temperature scaler must be fitted with a valid positive temperature in PRODUCTION profile."
                )

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
        if not self.allow_heuristic_fallback or self.profile == RuntimeProfile.PRODUCTION:
            raise ProductionProfileError(
                "Stage B neural model is not loaded; heuristic fallback is forbidden in PRODUCTION profile."
            )
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
