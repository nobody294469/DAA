"""Tests for Phase 8.2 Task 2: Safe Model Checkpoint Deserialization.

Authoritative Requirements:
  - PROJECT_SPEC §10, §12.2, §17.1
  - PRODUCTIZATION_SPEC §10, §13 (T-04, T-05, F-05, F-06)
  - PRODUCTIZATION_DECISIONS PD-004, PD-018
  - No torch.load(..., weights_only=False) in production
  - Strict model state key validation and fail-closed security invariants
"""
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import torch

from clindoc.config import (
    CANONICAL_CALIBRATION_TEMPERATURE,
    CANONICAL_MODERNBERT_CHECKPOINT,
    ProductionProfileError,
    RuntimeProfile,
)
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.m2.calibration import TemperatureScaler
from clindoc.m2.model import ClinicalVerifier, MODEL_CHECKPOINT
from clindoc.verify.stage_b import (
    LearnedVerifier,
    MockModelPredictor,
    load_safe_checkpoint,
)


def make_test_evidence(text: str = "Glucose 115 mg/dL") -> Evidence:
    return Evidence(
        evidence_id="e1",
        document_id="doc1",
        page_number=1,
        text=text,
        char_start=0,
        char_end=len(text),
        bbox=BBoxModel(x0=0, y0=0, x1=100, y1=20),
    )


def make_test_claim(value: str = "115") -> Claim:
    return Claim(
        claim_id="c1",
        document_id="doc1",
        field_type="lab.value",
        value=value,
        region_id="r1",
        evidence_id="e1",
        extraction_method="deterministic",
        extraction_confidence=0.95,
    )


# ─── A. VALID SAFE CHECKPOINT LOADS SUCCESSFULLY ──────────────────────────────

def test_canonical_checkpoint_loads_safely_under_production():
    """Canonical ModernBERT checkpoint loads cleanly under weights_only=True."""
    ckpt = load_safe_checkpoint(
        CANONICAL_MODERNBERT_CHECKPOINT,
        profile=RuntimeProfile.PRODUCTION,
        require_weights_only=True,
    )
    assert isinstance(ckpt, dict)
    assert "model_state" in ckpt
    assert isinstance(ckpt["model_state"], dict)
    assert len(ckpt["model_state"]) == 136

    # Verify all entries in model_state are tensors
    assert all(isinstance(v, torch.Tensor) for v in ckpt["model_state"].values())


def test_learned_verifier_loads_canonical_checkpoint_in_production():
    """LearnedVerifier safely initializes, loads checkpoint, and verifies in PRODUCTION."""
    scaler = TemperatureScaler(init_temperature=CANONICAL_CALIBRATION_TEMPERATURE)
    scaler.is_fitted = True

    verifier = LearnedVerifier(
        model_path=CANONICAL_MODERNBERT_CHECKPOINT,
        scaler=scaler,
        eager_load=True,
        profile=RuntimeProfile.PRODUCTION,
    )
    assert verifier.is_model_loaded

    claim = make_test_claim("115")
    evidence = make_test_evidence("Glucose 115 mg/dL")
    res = verifier.verify(claim, evidence)
    assert res.label in ("SUPPORTED", "DISCREPANCY", "NOT_SUPPORTED")
    assert 0.0 <= res.calibrated_confidence <= 1.0


# ─── B. MISSING CHECKPOINT FAILS CLOSED ───────────────────────────────────────

def test_missing_checkpoint_fails_closed_in_production():
    """Non-existent checkpoint file fails closed in both load_safe_checkpoint and verifier."""
    missing_path = "data/non_existent_modernbert_checkpoint.pt"

    with pytest.raises(ProductionProfileError, match="checkpoint missing or not found"):
        load_safe_checkpoint(missing_path, profile=RuntimeProfile.PRODUCTION)

    scaler = TemperatureScaler(init_temperature=CANONICAL_CALIBRATION_TEMPERATURE)
    scaler.is_fitted = True

    with pytest.raises(ProductionProfileError, match="checkpoint not found"):
        LearnedVerifier(
            model_path=missing_path,
            scaler=scaler,
            eager_load=True,
            profile=RuntimeProfile.PRODUCTION,
        )


# ─── C. MALFORMED CHECKPOINT FAILS CLOSED ─────────────────────────────────────

def test_malformed_checkpoint_fails_closed_in_production(tmp_path):
    """Corrupted binary data fails safe deserialization without arbitrary code execution."""
    malformed_file = tmp_path / "corrupted_checkpoint.pt"
    malformed_file.write_bytes(b"\x80\x03cbad_module\nbad_func\nq\x00)Rq\x01.")

    with pytest.raises(ProductionProfileError, match="Safe checkpoint deserialization failed"):
        load_safe_checkpoint(malformed_file, profile=RuntimeProfile.PRODUCTION)

    scaler = TemperatureScaler(init_temperature=CANONICAL_CALIBRATION_TEMPERATURE)
    scaler.is_fitted = True

    with pytest.raises(ProductionProfileError, match="Safe checkpoint deserialization failed"):
        LearnedVerifier(
            model_path=malformed_file,
            scaler=scaler,
            eager_load=True,
            profile=RuntimeProfile.PRODUCTION,
        )


# ─── D. INVALID CHECKPOINT STRUCTURE FAILS CLOSED ────────────────────────────

def test_non_dict_root_structure_fails_closed(tmp_path):
    """Checkpoints containing non-dict root objects fail closed."""
    list_file = tmp_path / "list_root.pt"
    torch.save([1, 2, 3, 4], list_file)

    with pytest.raises(ProductionProfileError, match="expected dictionary, got list"):
        load_safe_checkpoint(list_file, profile=RuntimeProfile.PRODUCTION)


def test_non_dict_model_state_fails_closed(tmp_path):
    """Checkpoints where model_state is not a dictionary fail closed."""
    bad_state_file = tmp_path / "bad_state.pt"
    torch.save({"model_state": "invalid_string_state"}, bad_state_file)

    with pytest.raises(ProductionProfileError, match="'model_state' must be a dictionary"):
        load_safe_checkpoint(bad_state_file, profile=RuntimeProfile.PRODUCTION)


def test_empty_model_state_fails_closed(tmp_path):
    """Checkpoints with empty model_state fail closed."""
    empty_file = tmp_path / "empty_state.pt"
    torch.save({"model_state": {}}, empty_file)

    with pytest.raises(ProductionProfileError, match="empty model weights state dictionary"):
        load_safe_checkpoint(empty_file, profile=RuntimeProfile.PRODUCTION)


def test_non_tensor_weights_fail_closed(tmp_path):
    """Checkpoints with non-Tensor values inside state_dict fail closed."""
    bad_tensor_file = tmp_path / "bad_tensor.pt"
    torch.save({"model_state": {"layer.weight": [1.0, 2.0, 3.0]}}, bad_tensor_file)

    with pytest.raises(ProductionProfileError, match="expected torch.Tensor"):
        load_safe_checkpoint(bad_tensor_file, profile=RuntimeProfile.PRODUCTION)


# ─── E. MISSING REQUIRED MODEL KEYS FAILS CLOSED ──────────────────────────────

def test_missing_required_model_keys_fails_closed(tmp_path):
    """Checkpoints missing required ModernBERT weights fail closed during load."""
    partial_file = tmp_path / "partial_weights.pt"
    # Save state_dict with only classifier weight, missing all 134 encoder layers
    partial_ckpt = {
        "model_state": {
            "classifier.weight": torch.randn(3, 768),
            "classifier.bias": torch.randn(3),
        },
        "aux_mode": "none",
    }
    torch.save(partial_ckpt, partial_file)

    scaler = TemperatureScaler(init_temperature=CANONICAL_CALIBRATION_TEMPERATURE)
    scaler.is_fitted = True

    with pytest.raises(ProductionProfileError, match="Missing required model weights"):
        LearnedVerifier(
            model_path=partial_file,
            scaler=scaler,
            eager_load=True,
            profile=RuntimeProfile.PRODUCTION,
        )


# ─── F. UNEXPECTED INCOMPATIBLE KEYS DETECTED ────────────────────────────────

def test_unexpected_incompatible_keys_detected_and_rejected(tmp_path):
    """Checkpoints containing unexpected/incompatible weight keys are rejected in PRODUCTION."""
    # Load canonical checkpoint, inject unexpected key, and save
    canonical_ckpt = torch.load(CANONICAL_MODERNBERT_CHECKPOINT, map_location="cpu", weights_only=True)
    tampered_state = dict(canonical_ckpt["model_state"])
    tampered_state["unexpected_backdoor_layer.weight"] = torch.randn(10, 10)

    tampered_file = tmp_path / "tampered_checkpoint.pt"
    torch.save(
        {
            "model_state": tampered_state,
            "aux_mode": "none",
            "temperature": CANONICAL_CALIBRATION_TEMPERATURE,
        },
        tampered_file,
    )

    scaler = TemperatureScaler(init_temperature=CANONICAL_CALIBRATION_TEMPERATURE)
    scaler.is_fitted = True

    with pytest.raises(ProductionProfileError, match="Unexpected incompatible keys"):
        LearnedVerifier(
            model_path=tampered_file,
            scaler=scaler,
            eager_load=True,
            profile=RuntimeProfile.PRODUCTION,
        )


# ─── G. PRODUCTION NEVER INVOKES UNSAFE TORCH.LOAD ───────────────────────────

def test_production_enforces_weights_only_true():
    """load_safe_checkpoint strictly forbids require_weights_only=False in PRODUCTION."""
    with pytest.raises(ProductionProfileError, match=r"weights_only=False\) is strictly forbidden"):
        load_safe_checkpoint(
            CANONICAL_MODERNBERT_CHECKPOINT,
            profile=RuntimeProfile.PRODUCTION,
            require_weights_only=False,
        )


def test_production_model_loading_invokes_torch_load_with_weights_only():
    """Verify that torch.load is called with weights_only=True during production model loading."""
    scaler = TemperatureScaler(init_temperature=CANONICAL_CALIBRATION_TEMPERATURE)
    scaler.is_fitted = True

    with patch("torch.load", wraps=torch.load) as mock_torch_load:
        verifier = LearnedVerifier(
            model_path=CANONICAL_MODERNBERT_CHECKPOINT,
            scaler=scaler,
            eager_load=True,
            profile=RuntimeProfile.PRODUCTION,
        )
        assert verifier.is_model_loaded
        assert mock_torch_load.called
        # Check all calls made to torch.load have weights_only=True
        for call_args in mock_torch_load.call_args_list:
            _, kwargs = call_args
            assert kwargs.get("weights_only") is True, f"torch.load invoked without weights_only=True: {kwargs}"


# ─── H. DEV BEHAVIOR REMAINS INTACT ──────────────────────────────────────────

def test_dev_profile_behavior_intact():
    """DEV profile retains permissive mock and heuristic fallback capabilities."""
    # Mock predictor in DEV
    verifier_mock = LearnedVerifier(
        custom_predictor=MockModelPredictor(default_label="SUPPORTED", confidence=0.90),
        profile=RuntimeProfile.DEV,
    )
    claim = make_test_claim("115")
    ev = make_test_evidence()
    res = verifier_mock.verify(claim, ev)
    assert res.label == "SUPPORTED"

    # Missing file in load_safe_checkpoint raises FileNotFoundError in DEV
    with pytest.raises(FileNotFoundError):
        load_safe_checkpoint("non_existent_file.pt", profile=RuntimeProfile.DEV)

    # Heuristic fallback allowed in DEV
    verifier_heuristic = LearnedVerifier(
        model_path=None,
        allow_heuristic_fallback=True,
        profile=RuntimeProfile.DEV,
    )
    res_heuristic = verifier_heuristic.verify(claim, ev)
    assert res_heuristic.label == "SUPPORTED"
