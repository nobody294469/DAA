"""Unit tests for runtime profiles and production configuration containment.

Authority:
  - PROJECT_SPEC.md §3, §10, §12, §13
  - PRODUCTIZATION_SPEC.md §3.6, §3.8, §10, §13 (F-05, F-06, F-07)
  - PRODUCTIZATION_DECISIONS.md PD-003, PD-004, PD-005, PD-018, PD-019
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from clindoc.config import (
    CANONICAL_CALIBRATION_TEMPERATURE,
    CANONICAL_MODERNBERT_CHECKPOINT,
    CANONICAL_STAGE_B_ESCALATION_THRESHOLD,
    ProductionPipelineConfig,
    ProductionProfileError,
    RuntimeProfile,
    get_active_profile,
)
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.extract.llm_extractor import MockLLMProvider, VLLMProvider
from clindoc.extract.router import ExtractionRouter
from clindoc.m2.calibration import TemperatureScaler
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.stage_a import DeterministicVerifier
from clindoc.verify.stage_b import LearnedVerifier, MockModelPredictor
from clindoc.verify.stage_c import DefaultMockLLMJudge, StageCJudge, VLLMJudgeClient
from clindoc.cli import build_production_cascade, build_production_router, process_document


# ─── 1. DEV PROFILE TESTS ───────────────────────────────────────────────────

def test_dev_profile_allows_mock_llm():
    """DEV profile allows MockLLMProvider and DefaultMockLLMJudge without raising."""
    # Extraction router with mock LLM
    router = ExtractionRouter(llm_provider=MockLLMProvider(), profile=RuntimeProfile.DEV)
    assert isinstance(router.narrative._provider, MockLLMProvider)

    # Stage C judge with default mock judge
    judge = StageCJudge(client=DefaultMockLLMJudge(), profile=RuntimeProfile.DEV)
    assert isinstance(judge.client, DefaultMockLLMJudge)


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


def test_dev_profile_allows_mock_stage_b_and_heuristics():
    """DEV profile allows MockModelPredictor and heuristic fallback in Stage B."""
    # Mock model predictor
    verifier_mock = LearnedVerifier(
        custom_predictor=MockModelPredictor(default_label="SUPPORTED", confidence=0.90),
        profile=RuntimeProfile.DEV,
    )
    claim = Claim(
        claim_id="c1",
        document_id="doc1",
        field_type="lab.value",
        value="115",
        region_id="r1",
        evidence_id="e1",
        extraction_method="deterministic",
        extraction_confidence=0.95,
    )
    ev = make_test_evidence()
    res = verifier_mock.verify(claim, ev)
    assert res.label == "SUPPORTED"

    # Default verifier with heuristic fallback (no model loaded)
    verifier_heuristic = LearnedVerifier(profile=RuntimeProfile.DEV)
    assert verifier_heuristic.allow_heuristic_fallback is True
    res_heur = verifier_heuristic.verify(claim, ev)
    assert res_heur.label == "SUPPORTED"


def test_dev_profile_allows_default_cascade():
    """DEV profile allows default cascade construction for offline tests and research."""
    cascade = VerificationCascade(profile=RuntimeProfile.DEV)
    assert cascade.profile == RuntimeProfile.DEV
    assert cascade.stage_b.allow_heuristic_fallback is True
    assert cascade.stage_c.allow_mock_client is True


# ─── 2. PRODUCTION PROFILE: MOCK LLM REJECTION ──────────────────────────────

def test_production_profile_rejects_mock_llm_extractor():
    """PRODUCTION profile strictly forbids MockLLMProvider in ExtractionRouter."""
    # Explicit MockLLMProvider passed
    with pytest.raises(ProductionProfileError, match="MockLLMProvider is forbidden in PRODUCTION profile"):
        ExtractionRouter(llm_provider=MockLLMProvider(), profile=RuntimeProfile.PRODUCTION)

    # Default extraction config with provider="mock"
    with pytest.raises(ProductionProfileError, match="MockLLMProvider is forbidden in PRODUCTION profile"):
        ExtractionRouter(profile=RuntimeProfile.PRODUCTION)


def test_production_profile_rejects_mock_llm_judge():
    """PRODUCTION profile strictly forbids DefaultMockLLMJudge in StageCJudge."""
    # Explicit DefaultMockLLMJudge passed
    with pytest.raises(ProductionProfileError, match="DefaultMockLLMJudge is forbidden in PRODUCTION profile"):
        StageCJudge(client=DefaultMockLLMJudge(), profile=RuntimeProfile.PRODUCTION)

    # None client passed (which would otherwise default to mock)
    with pytest.raises(ProductionProfileError, match="Stage C client must be explicitly configured"):
        StageCJudge(client=None, profile=RuntimeProfile.PRODUCTION)


# ─── 3. PRODUCTION PROFILE: HEURISTIC STAGE B REJECTION ──────────────────────

def test_production_profile_rejects_mock_stage_b_predictor():
    """PRODUCTION profile strictly forbids MockModelPredictor in Stage B."""
    with pytest.raises(ProductionProfileError, match="MockModelPredictor is forbidden in PRODUCTION profile"):
        LearnedVerifier(
            custom_predictor=MockModelPredictor(),
            profile=RuntimeProfile.PRODUCTION,
        )


def test_production_profile_rejects_heuristic_fallback_flag():
    """PRODUCTION profile strictly forbids allow_heuristic_fallback=True."""
    with pytest.raises(ProductionProfileError, match="Heuristic fallback is forbidden in PRODUCTION profile"):
        LearnedVerifier(
            model_path=CANONICAL_MODERNBERT_CHECKPOINT,
            allow_heuristic_fallback=True,
            profile=RuntimeProfile.PRODUCTION,
        )


def test_production_profile_rejects_heuristic_fallback_at_inference():
    """If neural model is not loaded in production, inference must fail closed rather than guessing."""
    scaler = TemperatureScaler(init_temperature=CANONICAL_CALIBRATION_TEMPERATURE)
    scaler.is_fitted = True
    verifier = LearnedVerifier(
        model_path=CANONICAL_MODERNBERT_CHECKPOINT,
        scaler=scaler,
        eager_load=False,
        profile=RuntimeProfile.PRODUCTION,
    )
    # Ensure model is not loaded
    verifier._model = None
    verifier._predictor = None

    claim = Claim(
        claim_id="c1",
        document_id="doc1",
        field_type="lab.value",
        value="115",
        region_id="r1",
        evidence_id="e1",
        extraction_method="deterministic",
        extraction_confidence=0.95,
    )
    ev = make_test_evidence()
    with pytest.raises(ProductionProfileError, match="heuristic fallback is forbidden in PRODUCTION profile"):
        verifier.verify(claim, ev)


# ─── 4. PRODUCTION PROFILE: MISSING CHECKPOINT REJECTION ─────────────────────

def test_production_profile_rejects_missing_modernbert_checkpoint():
    """PRODUCTION profile rejects missing, non-existent, or empty ModernBERT checkpoint."""
    # None checkpoint path
    with pytest.raises(ProductionProfileError, match="ModernBERT model_path is required"):
        LearnedVerifier(model_path=None, profile=RuntimeProfile.PRODUCTION)

    # Non-existent checkpoint path
    with pytest.raises(ProductionProfileError, match="ModernBERT checkpoint not found"):
        LearnedVerifier(
            model_path=Path("non_existent/path/to/checkpoint.pt"),
            profile=RuntimeProfile.PRODUCTION,
        )


def test_production_config_rejects_missing_checkpoint():
    """ProductionPipelineConfig.validate() fails closed if checkpoint does not exist."""
    cfg = ProductionPipelineConfig(
        modernbert_checkpoint_path=Path("missing_file.pt"),
    )
    with pytest.raises(ProductionProfileError, match="ModernBERT checkpoint file not found"):
        cfg.validate()


# ─── 5. PRODUCTION PROFILE: CALIBRATION VALIDATION ───────────────────────────

def test_production_profile_rejects_invalid_calibration_in_config():
    """ProductionPipelineConfig.validate() rejects missing or non-positive temperature."""
    cfg_zero = ProductionPipelineConfig(calibration_temperature=0.0)
    with pytest.raises(ProductionProfileError, match="Invalid calibration temperature"):
        cfg_zero.validate()

    cfg_neg = ProductionPipelineConfig(calibration_temperature=-1.2)
    with pytest.raises(ProductionProfileError, match="Invalid calibration temperature"):
        cfg_neg.validate()


def test_production_profile_rejects_uncalibrated_scaler_in_verify():
    """LearnedVerifier fails closed if temperature scaler is not fitted in PRODUCTION profile."""
    scaler = TemperatureScaler(init_temperature=1.0)
    scaler.is_fitted = False

    with pytest.raises(ProductionProfileError, match="Stage B temperature scaler must be fitted"):
        LearnedVerifier(
            model_path=CANONICAL_MODERNBERT_CHECKPOINT,
            scaler=scaler,
            eager_load=False,
            profile=RuntimeProfile.PRODUCTION,
        )


# ─── 6. PRODUCTION PROFILE: INVALID VLLM ENDPOINT REJECTION ──────────────────

def test_production_profile_rejects_invalid_vllm_endpoint_in_stage_c():
    """StageCJudge rejects invalid base_url or empty model_name in PRODUCTION profile."""
    # Non-HTTP URL
    client_bad_url = VLLMJudgeClient(base_url="not_a_valid_url")
    with pytest.raises(ProductionProfileError, match="Invalid vLLM base_url"):
        StageCJudge(client=client_bad_url, profile=RuntimeProfile.PRODUCTION)

    # Empty model name
    client_empty_model = VLLMJudgeClient(base_url="http://localhost:8000/v1", model_name="")
    with pytest.raises(ProductionProfileError, match="Invalid vLLM model_name"):
        StageCJudge(client=client_empty_model, profile=RuntimeProfile.PRODUCTION)


def test_production_profile_rejects_invalid_vllm_endpoint_in_router():
    """ExtractionRouter rejects invalid VLLMProvider base_url in PRODUCTION profile."""
    vllm_bad = VLLMProvider(base_url="ftp://bad_scheme")
    with pytest.raises(ProductionProfileError, match="Invalid VLLMProvider base_url"):
        ExtractionRouter(llm_provider=vllm_bad, profile=RuntimeProfile.PRODUCTION)


def test_production_config_rejects_invalid_vllm_endpoint():
    """ProductionPipelineConfig.validate() rejects invalid endpoint configurations."""
    cfg = ProductionPipelineConfig(vllm_base_url="not_a_url")
    with pytest.raises(ProductionProfileError, match="Invalid vllm_base_url"):
        cfg.validate()


# ─── 7. PRODUCTION PROFILE: VALID CONFIGURATION ACCEPTED ─────────────────────

def test_production_profile_accepts_valid_configuration():
    """ProductionPipelineConfig passes validation with canonical parameters."""
    cfg = ProductionPipelineConfig()
    # Canonical checkpoint exists and is valid
    assert cfg.modernbert_checkpoint_path.exists()
    assert cfg.calibration_temperature == CANONICAL_CALIBRATION_TEMPERATURE
    assert cfg.stage_b_escalation_threshold == CANONICAL_STAGE_B_ESCALATION_THRESHOLD

    # Validating does not raise
    cfg.validate()

    # Factory builders construct valid production instances without error
    cascade = build_production_cascade(cfg)
    assert cascade.profile == RuntimeProfile.PRODUCTION
    assert cascade.stage_b.profile == RuntimeProfile.PRODUCTION
    assert cascade.stage_c.profile == RuntimeProfile.PRODUCTION
    assert cascade.stage_b.allow_heuristic_fallback is False
    assert cascade.stage_c.allow_mock_client is False

    router = build_production_router(cfg)
    assert router.profile == RuntimeProfile.PRODUCTION
    assert isinstance(router.narrative._provider, VLLMProvider)


# ─── 8. CLI / ORCHESTRATOR PRODUCTION CONTAINMENT ───────────────────────────

def test_cli_rejects_unconfigured_pipeline_in_production(tmp_path: Path):
    """Calling process_document() under PRODUCTION profile without production components fails closed."""
    dummy_doc = tmp_path / "dummy.pdf"
    dummy_doc.write_bytes(b"%PDF-1.4 dummy")

    # With explicit profile="production" and default router/cascade
    with pytest.raises(ProductionProfileError, match="Production profile requires an explicitly configured production"):
        process_document(
            input_file=dummy_doc,
            output_dir=tmp_path / "out",
            profile=RuntimeProfile.PRODUCTION,
        )


def test_cli_environment_variable_enforces_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Setting CLINDOC_PROFILE='production' in environment activates production profile containment."""
    monkeypatch.setenv("CLINDOC_PROFILE", "production")
    assert get_active_profile() == RuntimeProfile.PRODUCTION

    dummy_doc = tmp_path / "dummy.pdf"
    dummy_doc.write_bytes(b"%PDF-1.4 dummy")

    with pytest.raises(ProductionProfileError, match="Production profile requires an explicitly configured production"):
        process_document(
            input_file=dummy_doc,
            output_dir=tmp_path / "out",
        )
