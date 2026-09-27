"""Unit tests for atomic VerificationResult schema.

Authority: PROJECT_SPEC §10, schemas/verification_result.json, AUDIT-16
"""
import pytest
from pydantic import ValidationError

from clindoc.schema.verification import VerificationResult


def test_valid_supported_verification():
    vr = VerificationResult(
        verification_id="vr-001",
        claim_id="cl-001",
        label="SUPPORTED",
        confidence=0.94,
        stage_reached="A",
        stage_a_label="SUPPORTED",
        stage_b_label=None,
        stage_b_raw_confidence=None,
        contradiction_type=None,
        normalized_code_agreement=True,
        rationale=None,
        evidence_id="ev-001",
        escalated=False,
    )
    assert vr.label == "SUPPORTED"
    assert vr.confidence == 0.94
    assert vr.stage_reached == "A"
    assert vr.contradiction_type is None
    assert vr.rationale is None


def test_valid_discrepancy_with_contradiction_type():
    vr = VerificationResult(
        verification_id="vr-002",
        claim_id="cl-002",
        label="DISCREPANCY",
        confidence=0.88,
        stage_reached="C",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_raw_confidence=0.62,
        contradiction_type="value_mismatch",
        normalized_code_agreement=True,
        rationale="Evidence clearly indicates 140 mg/dL rather than 120 mg/dL.",
        evidence_id="ev-002",
        escalated=True,
    )
    assert vr.label == "DISCREPANCY"
    assert vr.contradiction_type == "value_mismatch"
    assert vr.rationale is not None
    assert vr.escalated is True


def test_discrepancy_without_contradiction_type_fails():
    with pytest.raises(ValidationError) as exc:
        VerificationResult(
            verification_id="vr-003",
            claim_id="cl-003",
            label="DISCREPANCY",
            confidence=0.90,
            stage_reached="B",
            stage_a_label=None,
            stage_b_label="DISCREPANCY",
            stage_b_raw_confidence=0.90,
            contradiction_type=None,  # MUST NOT be None
            evidence_id="ev-003",
        )
    assert "contradiction_type must not be null when label is DISCREPANCY" in str(exc.value)


def test_supported_with_contradiction_type_fails():
    with pytest.raises(ValidationError) as exc:
        VerificationResult(
            verification_id="vr-004",
            claim_id="cl-004",
            label="SUPPORTED",
            confidence=0.92,
            stage_reached="A",
            contradiction_type="value_mismatch",  # MUST be None
            evidence_id="ev-004",
        )
    assert "contradiction_type must be null when label is SUPPORTED" in str(exc.value)


def test_rationale_on_stage_a_or_b_fails():
    with pytest.raises(ValidationError) as exc:
        VerificationResult(
            verification_id="vr-005",
            claim_id="cl-005",
            label="SUPPORTED",
            confidence=0.95,
            stage_reached="B",
            rationale="Stage B should not emit free-text rationale.",  # Only stage C permitted
            evidence_id="ev-005",
        )
    assert "rationale must be null when stage_reached is 'B'" in str(exc.value)
