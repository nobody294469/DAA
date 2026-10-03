"""Focused tests for the canonical Audit Report Generator (§7.8).

Tests cover:
  1. Supported claims
  2. Discrepancy claims (with taxonomy preserved)
  3. Insufficient evidence claims
  4. Stage C escalation with LLM rationale
  5. Multiple claims with shared evidence (evidence deduplication)
  6. Summary metrics computation
  7. Schema validation against schemas/audit_report.json
  8. Rejection of invalid, missing, or mismatched inputs
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from clindoc.audit import (
    AuditReport,
    AuditReportSummary,
    generate_audit_report,
    validate_audit_report,
)
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.schema.verification import VerificationResult


@pytest.fixture
def sample_evidence() -> Evidence:
    return Evidence(
        evidence_id="ev-lab-001",
        document_id="doc-audit-test-1",
        page_number=1,
        text="Glucose 115 mg/dL [70-99]",
        char_start=50,
        char_end=74,
        bbox=BBoxModel(x0=100.0, y0=200.0, x1=300.0, y1=220.0),
        ocr_confidence=0.98,
    )


@pytest.fixture
def sample_evidence_2() -> Evidence:
    return Evidence(
        evidence_id="ev-med-002",
        document_id="doc-audit-test-1",
        page_number=1,
        text="Metformin 500 mg PO daily",
        char_start=120,
        char_end=145,
        bbox=BBoxModel(x0=100.0, y0=300.0, x1=350.0, y1=320.0),
        ocr_confidence=0.95,
    )


def test_generate_supported_claim_audit_report(sample_evidence: Evidence) -> None:
    """Test generating audit report for a supported claim."""
    claim = Claim(
        claim_id="cl-001",
        document_id="doc-audit-test-1",
        field_type="lab.value",
        value="115",
        value_numeric=115.0,
        unit_raw="mg/dL",
        extraction_method="deterministic",
        extraction_confidence=0.99,
        evidence_id=sample_evidence.evidence_id,
        normalized_code="2345-7",
        code_system="LOINC",
        code_confidence=0.95,
        region_id="reg-1",
    )
    verification = VerificationResult(
        verification_id="vr-001",
        claim_id=claim.claim_id,
        label="SUPPORTED",
        confidence=0.98,
        stage_reached="A",
        stage_a_label="SUPPORTED",
        evidence_id=sample_evidence.evidence_id,
        normalized_code_agreement=True,
        escalated=False,
    )

    report = generate_audit_report(
        doc_id="doc-audit-test-1",
        claims=[claim],
        evidence=[sample_evidence],
        verifications=[verification],
        fhir_bundle_id="bundle-12345",
    )

    assert report.document_id == "doc-audit-test-1"
    assert report.fhir_bundle_id == "bundle-12345"
    assert len(report.claims) == 1
    assert len(report.evidence) == 1
    assert len(report.verifications) == 1

    # Check summary counts
    assert report.summary.n_claims == 1
    assert report.summary.n_supported == 1
    assert report.summary.n_discrepancy == 0
    assert report.summary.n_insufficient_evidence == 0
    assert report.summary.n_escalated_to_llm == 0

    # Validate against schemas/audit_report.json
    assert report.validate_schema() is True
    assert validate_audit_report(report.to_dict()) is True


def test_generate_discrepancy_claim_audit_report(sample_evidence: Evidence) -> None:
    """Test generating audit report for a discrepancy claim with contradiction taxonomy."""
    claim = Claim(
        claim_id="cl-disc-001",
        document_id="doc-audit-test-1",
        field_type="lab.value",
        value="125",
        value_numeric=125.0,
        extraction_method="gliner",
        extraction_confidence=0.90,
        evidence_id=sample_evidence.evidence_id,
        region_id="reg-1",
    )
    verification = VerificationResult(
        verification_id="vr-disc-001",
        claim_id=claim.claim_id,
        label="DISCREPANCY",
        confidence=0.92,
        stage_reached="B",
        stage_a_label="DISCREPANCY",
        stage_b_label="DISCREPANCY",
        stage_b_raw_confidence=0.91,
        contradiction_type="value_mismatch",
        evidence_id=sample_evidence.evidence_id,
        escalated=False,
    )

    report = generate_audit_report(
        document_id="doc-audit-test-1",
        claims=[claim],
        evidence={sample_evidence.evidence_id: sample_evidence},
        verifications={claim.claim_id: verification},
        fhir_bundle_id="bundle-disc-001",
    )

    assert report.summary.n_claims == 1
    assert report.summary.n_supported == 0
    assert report.summary.n_discrepancy == 1
    assert report.summary.n_insufficient_evidence == 0
    assert report.summary.n_escalated_to_llm == 0

    # Preserves contradiction taxonomy and confidence
    assert report.verifications[0].contradiction_type == "value_mismatch"
    assert report.verifications[0].confidence == 0.92
    assert report.validate_schema() is True


def test_generate_insufficient_evidence_claim_audit_report(sample_evidence: Evidence) -> None:
    """Test generating audit report for an insufficient-evidence claim."""
    claim = Claim(
        claim_id="cl-insuff-001",
        document_id="doc-audit-test-1",
        field_type="med.duration",
        value="30 days",
        extraction_method="llm_narrative",
        extraction_confidence=0.60,
        evidence_id=sample_evidence.evidence_id,
        region_id="reg-1",
    )
    verification = VerificationResult(
        verification_id="vr-insuff-001",
        claim_id=claim.claim_id,
        label="INSUFFICIENT_EVIDENCE",
        confidence=0.55,
        stage_reached="A",
        stage_a_label="INSUFFICIENT_EVIDENCE",
        evidence_id=sample_evidence.evidence_id,
        escalated=False,
    )

    report = generate_audit_report(
        doc_id="doc-audit-test-1",
        claims=[claim],
        evidence=[sample_evidence],
        verifications=[verification],
        fhir_bundle_id="bundle-insuff-001",
    )

    assert report.summary.n_claims == 1
    assert report.summary.n_supported == 0
    assert report.summary.n_discrepancy == 0
    assert report.summary.n_insufficient_evidence == 1
    assert report.summary.n_escalated_to_llm == 0
    assert report.validate_schema() is True


def test_generate_escalated_stage_c_claim_audit_report(sample_evidence: Evidence) -> None:
    """Test generating audit report with Stage C escalation and LLM rationale."""
    claim = Claim(
        claim_id="cl-esc-001",
        document_id="doc-audit-test-1",
        field_type="lab.value",
        value="115",
        value_numeric=115.0,
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id=sample_evidence.evidence_id,
        region_id="reg-1",
    )
    verification = VerificationResult(
        verification_id="vr-esc-001",
        claim_id=claim.claim_id,
        label="SUPPORTED",
        confidence=0.88,
        stage_reached="C",
        stage_a_label="INSUFFICIENT_EVIDENCE",
        stage_b_label="SUPPORTED",
        stage_b_raw_confidence=0.72,
        rationale="Stage B low confidence resolved by Stage C LLM: context confirms glucose value of 115 mg/dL.",
        evidence_id=sample_evidence.evidence_id,
        escalated=True,
    )

    report = generate_audit_report(
        doc_id="doc-audit-test-1",
        claims=[claim],
        evidence=[sample_evidence],
        verifications=[verification],
        fhir_bundle_id="bundle-esc-001",
    )

    assert report.summary.n_claims == 1
    assert report.summary.n_supported == 1
    assert report.summary.n_escalated_to_llm == 1
    assert report.verifications[0].stage_reached == "C"
    assert report.verifications[0].escalated is True
    assert "Stage B low confidence" in (report.verifications[0].rationale or "")
    assert report.validate_schema() is True


def test_multiple_claims_with_shared_evidence_deduplication(
    sample_evidence: Evidence, sample_evidence_2: Evidence
) -> None:
    """Test generating audit report with multiple claims sharing evidence and verifying deduplication."""
    # 2 lab claims sharing sample_evidence (e.g. lab.value + lab.unit)
    c1 = Claim(
        claim_id="c-lab-val",
        document_id="doc-audit-test-1",
        field_type="lab.value",
        value="115",
        value_numeric=115.0,
        extraction_method="deterministic",
        extraction_confidence=0.99,
        evidence_id=sample_evidence.evidence_id,
        region_id="reg-1",
    )
    c2 = Claim(
        claim_id="c-lab-unit",
        document_id="doc-audit-test-1",
        field_type="lab.unit",
        value="mg/dL",
        extraction_method="deterministic",
        extraction_confidence=0.99,
        evidence_id=sample_evidence.evidence_id,
        region_id="reg-1",
    )

    # 2 med claims sharing sample_evidence_2 (e.g. med.name + med.dose)
    c3 = Claim(
        claim_id="c-med-name",
        document_id="doc-audit-test-1",
        field_type="med.name",
        value="Metformin",
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id=sample_evidence_2.evidence_id,
        normalized_code="6809",
        code_system="RxNorm",
        code_confidence=0.99,
        region_id="reg-1",
    )
    c4 = Claim(
        claim_id="c-med-dose",
        document_id="doc-audit-test-1",
        field_type="med.dose",
        value="1000 mg",  # intentional discrepancy: source text says 500 mg
        value_numeric=1000.0,
        unit_raw="mg",
        extraction_method="gliner",
        extraction_confidence=0.85,
        evidence_id=sample_evidence_2.evidence_id,
        region_id="reg-1",
    )

    v1 = VerificationResult(
        verification_id="v-1",
        claim_id=c1.claim_id,
        label="SUPPORTED",
        confidence=0.99,
        stage_reached="A",
        stage_a_label="SUPPORTED",
        evidence_id=sample_evidence.evidence_id,
    )
    v2 = VerificationResult(
        verification_id="v-2",
        claim_id=c2.claim_id,
        label="SUPPORTED",
        confidence=0.98,
        stage_reached="A",
        stage_a_label="SUPPORTED",
        evidence_id=sample_evidence.evidence_id,
    )
    v3 = VerificationResult(
        verification_id="v-3",
        claim_id=c3.claim_id,
        label="INSUFFICIENT_EVIDENCE",
        confidence=0.60,
        stage_reached="A",
        stage_a_label="INSUFFICIENT_EVIDENCE",
        evidence_id=sample_evidence_2.evidence_id,
    )
    v4 = VerificationResult(
        verification_id="v-4",
        claim_id=c4.claim_id,
        label="DISCREPANCY",
        confidence=0.94,
        stage_reached="C",
        stage_a_label="DISCREPANCY",
        stage_b_label="DISCREPANCY",
        stage_b_raw_confidence=0.90,
        contradiction_type="dose_mismatch",
        rationale="Source evidence explicitly states 500 mg, not 1000 mg.",
        evidence_id=sample_evidence_2.evidence_id,
        escalated=True,
    )

    # Pass evidence list containing redundant entries to test deduplication
    report = generate_audit_report(
        doc_id="doc-audit-test-1",
        claims=[c1, c2, c3, c4],
        evidence=[sample_evidence, sample_evidence_2, sample_evidence, sample_evidence_2],
        verifications=[v1, v2, v3, v4],
        fhir_bundle_id="bundle-multi-001",
    )

    # Summary verification
    assert report.summary.n_claims == 4
    assert report.summary.n_supported == 2
    assert report.summary.n_discrepancy == 1
    assert report.summary.n_insufficient_evidence == 1
    assert report.summary.n_escalated_to_llm == 1

    # Evidence deduplication check
    assert len(report.evidence) == 2
    ev_ids = [e.evidence_id for e in report.evidence]
    assert ev_ids == [sample_evidence.evidence_id, sample_evidence_2.evidence_id]

    # Full schema validation
    assert report.validate_schema() is True


def test_invalid_and_missing_inputs(sample_evidence: Evidence) -> None:
    """Test that generator strictly rejects invalid, missing, or mismatched inputs."""
    claim = Claim(
        claim_id="c-test-01",
        document_id="doc-audit-test-1",
        field_type="lab.value",
        value="100",
        value_numeric=100.0,
        extraction_method="deterministic",
        extraction_confidence=1.0,
        evidence_id=sample_evidence.evidence_id,
        region_id="reg-1",
    )
    verification = VerificationResult(
        verification_id="v-test-01",
        claim_id=claim.claim_id,
        label="SUPPORTED",
        confidence=0.99,
        stage_reached="A",
        stage_a_label="SUPPORTED",
        evidence_id=sample_evidence.evidence_id,
    )

    # 1. Missing or blank doc_id
    with pytest.raises(ValueError, match="doc_id / document_id must be a non-empty string"):
        generate_audit_report(
            doc_id="",
            claims=[claim],
            evidence=[sample_evidence],
            verifications=[verification],
            fhir_bundle_id="b-1",
        )

    # 2. Missing or blank fhir_bundle_id
    with pytest.raises(ValueError, match="fhir_bundle_id must be a non-empty string"):
        generate_audit_report(
            doc_id="doc-audit-test-1",
            claims=[claim],
            evidence=[sample_evidence],
            verifications=[verification],
            fhir_bundle_id="  ",
        )

    # 3. Empty claims list
    with pytest.raises(ValueError, match="claims must be a non-empty sequence"):
        generate_audit_report(
            doc_id="doc-audit-test-1",
            claims=[],
            evidence=[sample_evidence],
            verifications=[verification],
            fhir_bundle_id="b-1",
        )

    # 4. Non-Claim element
    with pytest.raises(TypeError, match="must be a Claim instance"):
        generate_audit_report(
            doc_id="doc-audit-test-1",
            claims=[{"dummy": "dict"}],  # type: ignore
            evidence=[sample_evidence],
            verifications=[verification],
            fhir_bundle_id="b-1",
        )

    # 5. Claim document_id mismatch
    mismatched_claim = Claim(
        claim_id="c-mismatch",
        document_id="different-doc",
        field_type="lab.value",
        value="100",
        extraction_method="deterministic",
        extraction_confidence=1.0,
        evidence_id=sample_evidence.evidence_id,
        region_id="reg-1",
    )
    with pytest.raises(ValueError, match="does not match"):
        generate_audit_report(
            doc_id="doc-audit-test-1",
            claims=[mismatched_claim],
            evidence=[sample_evidence],
            verifications=[verification],
            fhir_bundle_id="b-1",
        )

    # 6. Evidence document_id mismatch
    mismatched_ev = Evidence(
        evidence_id="ev-mismatch",
        document_id="different-doc",
        page_number=1,
        text="text",
        char_start=0,
        char_end=4,
        bbox=BBoxModel(x0=0, y0=0, x1=10, y1=10),
    )
    with pytest.raises(ValueError, match="does not match"):
        generate_audit_report(
            doc_id="doc-audit-test-1",
            claims=[claim],
            evidence=[mismatched_ev],
            verifications=[verification],
            fhir_bundle_id="b-1",
        )

    # 7. Claim referencing unknown evidence_id
    orphan_evidence_claim = Claim(
        claim_id="c-orphan-ev",
        document_id="doc-audit-test-1",
        field_type="lab.value",
        value="100",
        extraction_method="deterministic",
        extraction_confidence=1.0,
        evidence_id="non-existent-evidence-id",
        region_id="reg-1",
    )
    orphan_ver = VerificationResult(
        verification_id="v-orphan",
        claim_id=orphan_evidence_claim.claim_id,
        label="SUPPORTED",
        confidence=0.9,
        stage_reached="A",
        evidence_id="non-existent-evidence-id",
    )
    with pytest.raises(ValueError, match="references unknown evidence_id"):
        generate_audit_report(
            doc_id="doc-audit-test-1",
            claims=[orphan_evidence_claim],
            evidence=[sample_evidence],
            verifications=[orphan_ver],
            fhir_bundle_id="b-1",
        )

    # 8. Missing verification for a claim
    with pytest.raises(ValueError, match="Missing VerificationResult for claim_id"):
        generate_audit_report(
            doc_id="doc-audit-test-1",
            claims=[claim],
            evidence=[sample_evidence],
            verifications=[],
            fhir_bundle_id="b-1",
        )

    # 9. Verification evidence_id mismatch
    mismatched_ver_ev = VerificationResult(
        verification_id="v-mismatch-ev",
        claim_id=claim.claim_id,
        label="SUPPORTED",
        confidence=0.99,
        stage_reached="A",
        stage_a_label="SUPPORTED",
        evidence_id="different-ev-id",
    )
    with pytest.raises(ValueError, match="does not match claim.evidence_id"):
        generate_audit_report(
            doc_id="doc-audit-test-1",
            claims=[claim],
            evidence=[sample_evidence],
            verifications=[mismatched_ver_ev],
            fhir_bundle_id="b-1",
        )

    # 10. Orphaned verification (not belonging to any claim in document)
    extra_ver = VerificationResult(
        verification_id="v-extra",
        claim_id="non-existent-claim",
        label="SUPPORTED",
        confidence=0.99,
        stage_reached="A",
        evidence_id=sample_evidence.evidence_id,
    )
    with pytest.raises(ValueError, match="has no corresponding Claim in document"):
        generate_audit_report(
            doc_id="doc-audit-test-1",
            claims=[claim],
            evidence=[sample_evidence],
            verifications=[verification, extra_ver],
            fhir_bundle_id="b-1",
        )


def test_serialization_persistence_and_validator(sample_evidence: Evidence, tmp_path: Path) -> None:
    """Test saving, loading, JSON dumping, and strict validator failure modes."""
    claim = Claim(
        claim_id="c-save-01",
        document_id="doc-audit-test-1",
        field_type="lab.value",
        value="100",
        value_numeric=100.0,
        extraction_method="deterministic",
        extraction_confidence=1.0,
        evidence_id=sample_evidence.evidence_id,
        region_id="reg-1",
    )
    verification = VerificationResult(
        verification_id="v-save-01",
        claim_id=claim.claim_id,
        label="SUPPORTED",
        confidence=0.99,
        stage_reached="A",
        stage_a_label="SUPPORTED",
        evidence_id=sample_evidence.evidence_id,
    )

    report = generate_audit_report(
        doc_id="doc-audit-test-1",
        claims=[claim],
        evidence=[sample_evidence],
        verifications=[verification],
        fhir_bundle_id="b-save-01",
    )

    # 1. Roundtrip file I/O
    save_path = tmp_path / "audit_report.json"
    report.save(save_path)
    loaded = AuditReport.load(save_path)

    assert loaded.document_id == report.document_id
    assert loaded.summary.n_claims == report.summary.n_claims
    assert len(loaded.claims) == 1
    assert loaded.claims[0].claim_id == claim.claim_id

    # 2. Strict validator rejects forbidden extra properties
    bad_data = report.to_dict()
    bad_data["extra_forbidden_key"] = "illegal"
    with pytest.raises(ValueError, match="forbidden additional properties"):
        validate_audit_report(bad_data)

    # 3. Strict validator rejects corrupted summary count
    bad_summary_data = report.to_dict()
    bad_summary_data["summary"]["n_supported"] = 999
    with pytest.raises(ValueError, match="Sum of labels"):
        validate_audit_report(bad_summary_data)
