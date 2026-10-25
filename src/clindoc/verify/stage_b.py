"""Stage B: Learned Semantic Verifier using BioClinical ModernBERT.

Authority:
  - PROJECT_SPEC §5 #8, §10, §12.2, §17.1, §21.5
  - DEC-005: Stage B escalation threshold (provisional default: 0.60)
  - DEC-009: Temperature scaling calibration for multi-class logits
  - DEC-019: Field verification tiering (Tier 1 learned verification)

Stage B processes Tier 1 clinical claims through a fine-tuned BioClinical
ModernBERT 3-way sequence classifier with temperature scaling calibration.

Checkpoints are the .pt files written by clindoc.m2.train.train_one_fold
(scripts/train_v3_experiment.py). The base encoder config + tokenizer are resolved by
resolve_base_model_id(); env CLINDOC_BASE_MODEL points at a local copy of the base model and
CLINDOC_STAGE_B_CHECKPOINT (opt-in via use_env_checkpoint=True) at a trained checkpoint.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol, Sequence, Union

import numpy as np

from clindoc.extract.contracts import Claim, Evidence
from clindoc.m2.calibration import TemperatureScaler
from clindoc.m2.model import (
    INT_TO_LABEL,
    LABEL_ORDER,
    LABEL_TO_INT,
    MAX_SEQ_LENGTH,
    MODEL_CHECKPOINT,
    format_input,
)
from clindoc.schema.verification import VerificationLabel

DEFAULT_ESCALATION_THRESHOLD: float = 0.60  # DEC-005 provisional default
INFERENCE_BATCH_SIZE: int = 16  # sequences per batched forward pass (prefetch_logits)

logger = logging.getLogger(__name__)


def resolve_base_model_id(checkpoint_model_id: Optional[str] = None) -> str:
    """Base encoder id/dir for the tokenizer + architecture of a Stage B checkpoint.

    Order: the checkpoint's own config.model_id when it is an existing local dir ->
    env CLINDOC_BASE_MODEL -> ~/.cache/clindoc/BioClinical-ModernBERT-base if present -> hub id.
    """
    if checkpoint_model_id and Path(checkpoint_model_id).is_dir():
        return checkpoint_model_id
    if os.environ.get("CLINDOC_BASE_MODEL"):
        return os.environ["CLINDOC_BASE_MODEL"]
    local = Path.home() / ".cache" / "clindoc" / "BioClinical-ModernBERT-base"
    return str(local) if local.is_dir() else MODEL_CHECKPOINT


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
        use_env_checkpoint: bool = False,
    ) -> None:
        """
        Args:
            model_path: .pt checkpoint from clindoc.m2.train (None = heuristic fallback).
            use_env_checkpoint: When model_path is None, read it from env
                CLINDOC_STAGE_B_CHECKPOINT. Off by default so tests and the Streamlit UI keep
                the heuristic unless a caller opts in.
        """
        self.escalation_threshold = float(escalation_threshold)
        self.scaler = scaler or TemperatureScaler(init_temperature=1.0)
        if not self.scaler.is_fitted:
            # If not pre-fitted, set identity temperature so it can scale logits cleanly
            self.scaler.is_fitted = True

        if model_path is None and use_env_checkpoint:
            model_path = os.environ.get("CLINDOC_STAGE_B_CHECKPOINT") or None

        self._predictor: Optional[ModelPredictor] = custom_predictor
        self._model = None
        self._tokenizer = None
        self._device = device
        self.model_path = model_path
        self._load_attempted: bool = False
        self._load_error: Optional[str] = None
        self._base_model_id: Optional[str] = None
        self._max_seq_length: int = MAX_SEQ_LENGTH
        self._logit_cache: dict[tuple[str, str, str], np.ndarray] = {}

        if eager_load and self.model_path is not None and self._predictor is None:
            self.load_model()

    @property
    def is_model_loaded(self) -> bool:
        """Check whether a neural model or custom predictor is ready for inference."""
        return self._model is not None or self._predictor is not None

    @property
    def load_error(self) -> Optional[str]:
        """Why the configured checkpoint is not serving (None when loaded or none configured)."""
        return self._load_error

    @property
    def device(self) -> Optional[str]:
        """Device of the loaded neural model, None when it is not loaded."""
        return self._device if self._model is not None else None

    @property
    def temperature(self) -> float:
        """DEC-009 temperature applied to Stage B logits."""
        return float(self.scaler.temperature)

    @property
    def base_model_id(self) -> Optional[str]:
        """Base encoder id/dir the loaded checkpoint was rebuilt from."""
        return self._base_model_id

    @property
    def max_seq_length(self) -> int:
        """Tokenizer truncation length (the checkpoint's training max_seq_length)."""
        return self._max_seq_length

    def load_model(self) -> bool:
        """Load ModernBERT model and tokenizer once into memory outside the evaluation loop.

        Guarantees single initialization: if already loaded or if a custom predictor
        is present, returns True immediately without reloading. Failures leave the
        heuristic fallback active and record the reason in `load_error`.
        """
        if self._predictor is not None or self._model is not None:
            return True
        if self._load_attempted or not self.model_path:
            return False
        self._load_attempted = True
        try:
            self._load_checkpoint(Path(self.model_path))
            self._load_error = None
            return True
        except Exception as e:
            self._model = None
            self._load_error = f"{type(e).__name__}: {e}"
            logger.warning(
                "Stage B ModernBERT not loaded from %s; heuristic fallback in use. %s",
                self.model_path, self._load_error,
            )
            return False

    def _load_checkpoint(self, path: Path) -> None:
        import torch
        from clindoc.m2.model import ClinicalVerifier, load_tokenizer

        if path.is_dir():
            raise ValueError(
                f"{path} is a directory; Stage B needs the .pt checkpoint written by "
                "scripts/train_v3_experiment.py (a bare encoder has no trained classification head)"
            )
        if not path.is_file():
            raise FileNotFoundError(f"Stage B checkpoint not found: {path}")

        # CPU first so loading never spikes GPU memory (DEC-006); weights_only: no pickled code.
        ckpt = torch.load(str(path), map_location="cpu", weights_only=True)
        if not isinstance(ckpt, dict):
            raise ValueError(f"{path} is not a ClinicalVerifier checkpoint")
        cfg = ckpt.get("config") or {}
        aux_mode = ckpt.get("aux_mode", "none")
        if aux_mode != "none":
            raise ValueError(
                f"checkpoint aux_mode={aux_mode!r}: Stage B serves text-only (aux_mode='none') "
                "checkpoints; it has no OCR/template features at inference"
            )
        base_model_id = resolve_base_model_id(cfg.get("model_id"))
        max_seq_length = int(cfg.get("max_seq_length") or MAX_SEQ_LENGTH)

        tokenizer = load_tokenizer(base_model_id)
        model = ClinicalVerifier(model_id=base_model_id, aux_mode="none", load_pretrained=False)
        model.load_state_dict(ckpt.get("model_state", ckpt), strict=True)
        # DEC-009: fitted temperature from the checkpoint
        fitted_temp = float(ckpt.get("temperature") or 0.0)
        if fitted_temp > 0:
            self.scaler = TemperatureScaler(init_temperature=fitted_temp)
            self.scaler.is_fitted = True
        del ckpt

        device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
        # Transfer to target device with graceful CPU fallback on CUDA OOM
        try:
            model = model.to(device)
        except (torch.cuda.OutOfMemoryError, RuntimeError) as oom_err:
            if device == "cpu":
                raise
            logger.warning(
                "CUDA allocation failed for Stage B ModernBERT (%s). Gracefully falling back to cpu.",
                oom_err,
            )
            device = "cpu"
            torch.cuda.empty_cache()
            model = model.to("cpu")

        model.eval()
        self._tokenizer, self._model, self._device = tokenizer, model, device
        self._base_model_id, self._max_seq_length = base_model_id, max_seq_length

    def verify(
        self,
        claim: Claim,
        evidence: Optional[Evidence] = None,
        stage_a_label: Optional[VerificationLabel] = None,
        context_text: Optional[str] = None,
        claim_text: Optional[str] = None,
    ) -> StageBResult:
        """Run Stage B inference on claim and evidence.

        Args:
            claim: Clinical claim.
            evidence: Linked evidence.
            stage_a_label: Intermediate label from Stage A (if any).
            context_text: Optional local document context window.
            claim_text: Optional descriptive claim (clindoc.verify.claim_text). The neural
                model was trained on descriptive claims + DEC-003 context windows, so when it
                is loaded it reads claim_text/context_text instead of the bare value/span.

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

        # 1. Obtain Model Logits (neural model sees the training-time input distribution)
        logits = self._compute_logits(*self._model_inputs(claim, ev_text, context_text, claim_text))

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

    def _model_inputs(
        self,
        claim: Claim,
        ev_text: str,
        context_text: Optional[str],
        claim_text: Optional[str],
    ) -> tuple[str, str, str]:
        """(field_type, claim, evidence) as the active predictor reads them.

        The neural model was trained on descriptive claims + DEC-003 context windows; custom
        predictors and the heuristic keep reading the bare value and evidence span.
        """
        if self._model is not None and self._predictor is None:
            return claim.field_type, claim_text or claim.value, (context_text or "").strip() or ev_text
        return claim.field_type, claim.value, ev_text

    def prefetch_logits(
        self,
        requests: Iterable[tuple[Claim, Optional[Evidence], Optional[str], Optional[str]]],
    ) -> None:
        """Batch the neural forward passes for upcoming verify() calls.

        `requests` are (claim, evidence, context_text, claim_text) as verify() will receive them.
        No-op, without consuming `requests`, unless a neural model is loaded. verify() reads the
        cached logits; each call replaces the cache, so it holds at most one batch of claims.
        """
        if self._model is None or self._predictor is not None:
            return
        keys = []
        for claim, evidence, context_text, claim_text in requests:
            ev_text = evidence.text.strip() if (evidence and evidence.text) else ""
            if ev_text:
                keys.append(self._model_inputs(claim, ev_text, context_text, claim_text))
        keys = list(dict.fromkeys(keys))
        self._logit_cache = dict(zip(keys, self._neural_logits(keys))) if keys else {}

    def _neural_logits(self, inputs: Sequence[tuple[str, str, str]]) -> np.ndarray:
        """(N, 3) logits for (field_type, claim, evidence) inputs, dynamically padded batches."""
        import torch

        texts = [format_input(*inp) for inp in inputs]
        # Length-sorted batches keep padding (wasted FLOPs on CPU) small; rows are scattered back.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = np.empty((len(texts), len(LABEL_ORDER)), dtype=np.float32)
        for start in range(0, len(order), INFERENCE_BATCH_SIZE):
            idx = order[start:start + INFERENCE_BATCH_SIZE]
            toks = self._tokenizer(
                [texts[i] for i in idx],
                padding=True,
                truncation=True,
                max_length=self._max_seq_length,
                return_tensors="pt",
            )
            with torch.inference_mode():
                logits = self._model(
                    input_ids=toks["input_ids"].to(self._device),
                    attention_mask=toks["attention_mask"].to(self._device),
                )
            out[idx] = logits.float().cpu().numpy()
        return out

    def _compute_logits(self, field_type: str, claim_value: str, evidence_text: str) -> np.ndarray:
        """Compute 3-class logits from predictor or neural model."""
        if self._predictor is not None:
            return self._predictor.predict_logits(field_type, claim_value, evidence_text)

        # Reuse pre-loaded ModernBERT model instance (never load/reload inside per-example loop)
        if self._model is not None:
            key = (field_type, claim_value, evidence_text)
            cached = self._logit_cache.get(key)
            return cached if cached is not None else self._neural_logits([key])[0]

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
