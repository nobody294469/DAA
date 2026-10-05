"""Unit tests for Qualified Configuration Manifest & Provenance Interface.

Authority:
  - PROJECT_SPEC.md §3, §10, §12, §13, §17.1
  - PRODUCTIZATION_SPEC.md §3.6, §3.8, §10, §13 (Failures F-05, F-06, F-07)
  - PRODUCTIZATION_DECISIONS.md PD-005, PD-018, PD-019
"""
from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clindoc.config import (
    CANONICAL_AUDITED_VLLM_MODEL_NAME,
    CANONICAL_CALIBRATION_TEMPERATURE,
    CANONICAL_MODERNBERT_CHECKPOINT,
    CANONICAL_STAGE_B_AUTONOMOUS_THRESHOLD,
    CANONICAL_STAGE_B_ESCALATION_THRESHOLD,
    ProductionPipelineConfig,
    ProductionProfileError,
    RuntimeProfile,
)
from clindoc.m2.calibration import TemperatureScaler
from clindoc.manifest import (
    CANONICAL_MANIFEST_DIGEST_V1,
    CANONICAL_MANIFEST_VERSION,
    CANONICAL_MODERNBERT_CHECKPOINT_SHA256,
    CANONICAL_QWEN_MODEL_ID,
    CANONICAL_STAGE_C_PROMPT_TEXT_SHA256,
    CANONICAL_STAGE_C_PROMPT_VERSION,
    QUALIFIED_MANIFEST_DICT_V1,
    QualificationReport,
    QualificationStatus,
    QualifiedManifest,
    compute_canonical_manifest_digest,
    compute_stage_c_prompt_hash,
    evaluate_cascade_qualification,
    evaluate_config_qualification,
)
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.stage_a import DeterministicVerifier
from clindoc.verify.stage_b import LearnedVerifier
from clindoc.verify.stage_c import DefaultMockLLMJudge, StageCJudge, VLLMJudgeClient


# ─── FIXTURES & HELPERS ───────────────────────────────────────────────────────

@pytest.fixture
def fitted_canonical_scaler() -> TemperatureScaler:
    scaler = TemperatureScaler(init_temperature=CANONICAL_CALIBRATION_TEMPERATURE)
    scaler.temperature = CANONICAL_CALIBRATION_TEMPERATURE
    scaler.is_fitted = True
    return scaler


@pytest.fixture
def production_stage_b(fitted_canonical_scaler: TemperatureScaler) -> LearnedVerifier:
    return LearnedVerifier(
        model_path=CANONICAL_MODERNBERT_CHECKPOINT,
        scaler=fitted_canonical_scaler,
        escalation_threshold=CANONICAL_STAGE_B_ESCALATION_THRESHOLD,
        eager_load=False,
        allow_heuristic_fallback=False,
        profile=RuntimeProfile.PRODUCTION,
    )


@pytest.fixture
def production_stage_c() -> StageCJudge:
    client = VLLMJudgeClient(
        base_url="http://localhost:8000/v1",
        model_name=CANONICAL_QWEN_MODEL_ID,
    )
    return StageCJudge(
        client=client,
        allow_mock_client=False,
        profile=RuntimeProfile.PRODUCTION,
    )


@pytest.fixture
def production_cascade(
    production_stage_b: LearnedVerifier,
    production_stage_c: StageCJudge,
) -> VerificationCascade:
    return VerificationCascade(
        stage_a=DeterministicVerifier(),
        stage_b=production_stage_b,
        stage_c=production_stage_c,
        profile=RuntimeProfile.PRODUCTION,
    )


# ─── 1. EXACT QUALIFIED CONFIGURATION → QUALIFIED ────────────────────────────

def test_exact_qualified_configuration_is_qualified(
    production_cascade: VerificationCascade,
):
    """Exact canonical production configuration evaluates to QUALIFIED status."""
    report = evaluate_cascade_qualification(production_cascade)
    assert report.status == QualificationStatus.QUALIFIED
    assert report.is_qualified is True
    assert report.reasons == []
    assert report.manifest_version == CANONICAL_MANIFEST_VERSION
    assert report.manifest_digest == CANONICAL_MANIFEST_DIGEST_V1

    # Cascade instance exposes properties directly
    assert production_cascade.is_qualified is True
    assert production_cascade.qualification_status == QualificationStatus.QUALIFIED


def test_production_pipeline_config_evaluates_qualified():
    """ProductionPipelineConfig with exact canonical defaults evaluates to QUALIFIED."""
    cfg = ProductionPipelineConfig(vllm_model_name=CANONICAL_QWEN_MODEL_ID)
    report = evaluate_config_qualification(cfg)
    assert report.status == QualificationStatus.QUALIFIED
    assert report.is_qualified is True
    assert report.reasons == []


# ─── 2. WRONG CHECKPOINT HASH → UNQUALIFIED ──────────────────────────────────

def test_wrong_checkpoint_hash_evaluates_unqualified(
    production_cascade: VerificationCascade,
):
    """If Stage B checkpoint hash diverges from the manifest, status is UNQUALIFIED."""
    # Simulate a checkpoint with altered bytes / hash
    report = evaluate_cascade_qualification(
        production_cascade,
        checkpoint_sha256="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    )
    assert report.status == QualificationStatus.UNQUALIFIED
    assert report.is_qualified is False
    assert any("ModernBERT checkpoint SHA-256 mismatch" in r for r in report.reasons)


# ─── 3. WRONG CALIBRATION TEMPERATURE → UNQUALIFIED ──────────────────────────

def test_wrong_calibration_temperature_evaluates_unqualified(
    production_stage_c: StageCJudge,
):
    """If calibration temperature differs from audited T = 1.146522, status is UNQUALIFIED."""
    wrong_scaler = TemperatureScaler(init_temperature=1.0)
    wrong_scaler.temperature = 1.0  # Uncalibrated identity
    wrong_scaler.is_fitted = True

    stage_b = LearnedVerifier(
        model_path=CANONICAL_MODERNBERT_CHECKPOINT,
        scaler=wrong_scaler,
        escalation_threshold=CANONICAL_STAGE_B_ESCALATION_THRESHOLD,
        eager_load=False,
        allow_heuristic_fallback=False,
        profile=RuntimeProfile.PRODUCTION,
    )
    cascade = VerificationCascade(
        stage_b=stage_b,
        stage_c=production_stage_c,
        profile=RuntimeProfile.PRODUCTION,
    )

    assert cascade.is_qualified is False
    assert cascade.qualification_status == QualificationStatus.UNQUALIFIED
    assert any("ModernBERT calibration temperature mismatch" in r for r in cascade.qualification_report.reasons)


# ─── 4. WRONG STAGE B THRESHOLD → UNQUALIFIED ────────────────────────────────

def test_wrong_stage_b_escalation_threshold_evaluates_unqualified(
    fitted_canonical_scaler: TemperatureScaler,
    production_stage_c: StageCJudge,
):
    """If escalation threshold differs from 0.60 (e.g. 0.75), status is UNQUALIFIED."""
    stage_b = LearnedVerifier(
        model_path=CANONICAL_MODERNBERT_CHECKPOINT,
        scaler=fitted_canonical_scaler,
        escalation_threshold=0.75,  # Modified threshold
        eager_load=False,
        allow_heuristic_fallback=False,
        profile=RuntimeProfile.PRODUCTION,
    )
    cascade = VerificationCascade(
        stage_b=stage_b,
        stage_c=production_stage_c,
        profile=RuntimeProfile.PRODUCTION,
    )

    assert cascade.is_qualified is False
    assert cascade.qualification_status == QualificationStatus.UNQUALIFIED
    assert any("Stage B escalation threshold mismatch" in r for r in cascade.qualification_report.reasons)


# ─── 5. WRONG QWEN MODEL IDENTIFIER → UNQUALIFIED ────────────────────────────

def test_wrong_qwen_model_identifier_evaluates_unqualified(
    production_stage_b: LearnedVerifier,
):
    """If Stage C model name differs from Qwen/Qwen2.5-7B-Instruct-AWQ, status is UNQUALIFIED."""
    client_non_awq = VLLMJudgeClient(
        base_url="http://localhost:8000/v1",
        model_name="Qwen/Qwen2.5-7B-Instruct",  # Missing -AWQ
    )
    stage_c = StageCJudge(
        client=client_non_awq,
        allow_mock_client=False,
        profile=RuntimeProfile.PRODUCTION,
    )
    cascade = VerificationCascade(
        stage_b=production_stage_b,
        stage_c=stage_c,
        profile=RuntimeProfile.PRODUCTION,
    )

    assert cascade.is_qualified is False
    assert cascade.qualification_status == QualificationStatus.UNQUALIFIED
    assert any("Stage C model name mismatch" in r for r in cascade.qualification_report.reasons)


# ─── 6. WRONG PROMPT HASH OR VERSION → UNQUALIFIED ───────────────────────────

def test_wrong_prompt_version_evaluates_unqualified(
    production_stage_b: LearnedVerifier,
    production_stage_c: StageCJudge,
):
    """If Stage C prompt version diverges from V8_STAGE_C_LABFLAG_PROMPT_V1, status is UNQUALIFIED."""
    # Mutate prompt version on stage_c
    object.__setattr__(production_stage_c, "prompt_version", "V0_LEGACY_PROMPT")

    cascade = VerificationCascade(
        stage_b=production_stage_b,
        stage_c=production_stage_c,
        profile=RuntimeProfile.PRODUCTION,
    )

    assert cascade.is_qualified is False
    assert cascade.qualification_status == QualificationStatus.UNQUALIFIED
    assert any("Stage C prompt version mismatch" in r for r in cascade.qualification_report.reasons)


def test_tampered_prompt_text_hash_evaluates_unqualified(
    production_cascade: VerificationCascade,
):
    """If manifest expects a different prompt text hash, evaluation flags UNQUALIFIED."""
    custom_manifest_dict = copy.deepcopy(QUALIFIED_MANIFEST_DICT_V1)
    custom_manifest_dict["stage_c_prompt"]["prompt_text_sha256"] = (
        "0000000000000000000000000000000000000000000000000000000000000000"
    )
    custom_manifest = QualifiedManifest.from_dict(custom_manifest_dict)

    report = evaluate_cascade_qualification(production_cascade, manifest=custom_manifest)
    assert report.status == QualificationStatus.UNQUALIFIED
    assert report.is_qualified is False
    assert any("Stage C prompt text SHA-256 mismatch" in r for r in report.reasons)


# ─── 7. MISSING MANDATORY MANIFEST COMPONENT → FAIL CLOSED ───────────────────

def test_missing_mandatory_manifest_component_fails_closed():
    """Manifest missing mandatory top-level or section key raises ProductionProfileError."""
    # Missing top-level key
    broken_top = copy.deepcopy(QUALIFIED_MANIFEST_DICT_V1)
    del broken_top["modernbert"]
    with pytest.raises(ProductionProfileError, match="missing mandatory top-level key 'modernbert'"):
        QualifiedManifest.from_dict(broken_top)

    # Missing section key
    broken_sec = copy.deepcopy(QUALIFIED_MANIFEST_DICT_V1)
    del broken_sec["modernbert"]["checkpoint_sha256"]
    with pytest.raises(ProductionProfileError, match="missing mandatory ModernBERT key 'checkpoint_sha256'"):
        QualifiedManifest.from_dict(broken_sec)


# ─── 8. DEV PROFILE DOES NOT CLAIM QUALIFIED STATUS ──────────────────────────

def test_dev_profile_evaluates_unqualified():
    """A cascade initialized under DEV profile must NOT claim QUALIFIED production status."""
    dev_cascade = VerificationCascade(profile=RuntimeProfile.DEV)
    assert dev_cascade.profile == RuntimeProfile.DEV

    assert dev_cascade.is_qualified is False
    assert dev_cascade.qualification_status == QualificationStatus.UNQUALIFIED
    assert any("Runtime profile is 'dev'" in r for r in dev_cascade.qualification_report.reasons)


# ─── 9. MANIFEST INTEGRITY MISMATCH IS DETECTED ──────────────────────────────

def test_manifest_integrity_mismatch_detected():
    """Altering manifest dictionary without updating manifest_digest raises ProductionProfileError."""
    tampered_dict = copy.deepcopy(QUALIFIED_MANIFEST_DICT_V1)
    tampered_dict["modernbert"]["temperature"] = 2.0  # Tampered field
    # Pass canonical digest rather than recomputing:
    with pytest.raises(ProductionProfileError, match="Manifest integrity verification failed"):
        QualifiedManifest(raw_dict=tampered_dict, manifest_digest=CANONICAL_MANIFEST_DIGEST_V1).verify_integrity()


def test_canonical_manifest_digest_computation_deterministic():
    """Canonical manifest digest computation is fully deterministic and matches reference."""
    d1 = compute_canonical_manifest_digest(QUALIFIED_MANIFEST_DICT_V1)
    d2 = compute_canonical_manifest_digest(QUALIFIED_MANIFEST_DICT_V1)
    assert d1 == d2 == CANONICAL_MANIFEST_DIGEST_V1
