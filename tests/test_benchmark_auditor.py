"""test_benchmark_auditor.py — Unit tests for the Benchmark Evidence and Provenance Auditor.

Authority:
  - PROJECT_SPEC.md §7.1, §7.8, §8, §9, §10, §20.1
  - DEC-003: Document context window bounding
  - DEC-015: Group ID entity linking
  - DEC-017: Canonical field vocabulary
"""
from pathlib import Path
import pytest

from clindoc.audit.benchmark_auditor import (
    BenchmarkAuditor,
    IntegrityLevel,
    ProvenanceStatus,
    SemanticCategory,
)
from clindoc.m2.schema import M2Example, VerificationLabel


@pytest.fixture
def auditor():
    return BenchmarkAuditor()


def _create_test_example(
    example_id: str = "test-ex-1",
    source_doc_id: str = "LAB_0001",
    field_type: str = "lab.value",
    claim: str = "Glucose: 95 mg/dL",
    label: str = "SUPPORTED",
    evidence: str = "Glucose 95 mg/dL NORMAL 70 - 99 mg/dL",
    char_start: int = 10,
    char_end: int = 25,
    severity: str = "clean",
    page_number: int = 1,
) -> M2Example:
    return M2Example(
        example_id=example_id,
        claim=claim,
        evidence=evidence,
        field_type=field_type,
        doc_template="hospital_lab_cbc",
        ocr_confidence=0.99,
        severity=severity,
        label=VerificationLabel(label),
        source_doc_id=source_doc_id,
        generation_method="vocab_substitution",
        claim_type="lab",
        split="cv",
        group_id="group_1",
        contradiction_type=None,
        evidence_id="ev-1",
        char_start=char_start,
        char_end=char_end,
        page_number=page_number,
        bbox=[10.0, 20.0, 100.0, 40.0],
    )


def test_auditor_detects_out_of_bounds_offsets(auditor):
    """Verify that offsets exceeding document character length are marked OUT_OF_BOUNDS and DEFINITELY_INVALID."""
    ex = _create_test_example(
        source_doc_id="LAB_0028",
        char_start=598,
        char_end=605,  # Exceeds doc length 598
        claim="Red Blood Cell Count: 6.12 10*6/uL",
        label="SUPPORTED",
    )
    record = auditor.audit_example(ex)
    assert record.provenance_status == ProvenanceStatus.OUT_OF_BOUNDS
    assert record.integrity_level == IntegrityLevel.DEFINITELY_INVALID
    assert record.remediation_bucket == "exclude"
    assert "exceed document length" in (record.problem_reason or "")


def test_auditor_detects_corrupted_default_header(auditor):
    """Verify that [0, 100] default fallback offsets pointing to clinic headers are flagged."""
    ex = _create_test_example(
        source_doc_id="LAB_0025",
        char_start=0,
        char_end=100,
        claim="Potassium: 0.51 mmol/L",
        label="DISCREPANCY",
        severity="medium",
    )
    record = auditor.audit_example(ex)
    assert record.provenance_status == ProvenanceStatus.CORRUPTED_DEFAULT
    assert record.integrity_level == IntegrityLevel.DEFINITELY_INVALID
    assert record.remediation_bucket == "regenerate"
    assert "Corrupted default fallback" in (record.problem_reason or "")


def test_auditor_detects_wrong_entity_conflict(auditor):
    """Verify that an example claiming SUPPORTED but containing wrong entity is marked DEFINITELY_INVALID."""
    ex = _create_test_example(
        source_doc_id="LAB_0001",
        field_type="lab.value",
        claim="Potassium: 4.5 mmol/L",
        label="SUPPORTED",
        evidence="Sodium 140 mmol/L NORMAL 135 - 145 mmol/L",
        char_start=10,
        char_end=20,
    )
    record = auditor.audit_example(ex)
    assert record.semantic_category == SemanticCategory.WRONG_ENTITY
    assert record.is_conflict_with_gt is True
    assert record.integrity_level == IntegrityLevel.DEFINITELY_INVALID


def test_auditor_detects_contradictory_supported_claim(auditor):
    """Verify that an example claiming SUPPORTED where evidence contradicts value is flagged as conflict."""
    ex = _create_test_example(
        source_doc_id="LAB_0001",
        field_type="lab.value",
        claim="Glucose: 150 mg/dL",
        label="SUPPORTED",
        evidence="Glucose 85 mg/dL NORMAL 70 - 99 mg/dL",
        char_start=10,
        char_end=20,
    )
    record = auditor.audit_example(ex)
    assert record.semantic_category == SemanticCategory.CONTRADICTS_CLAIM
    assert record.is_conflict_with_gt is True
    assert record.integrity_level == IntegrityLevel.DEFINITELY_INVALID


def test_auditor_validates_clean_atomic_example(auditor):
    """Verify that a legitimate clean example is classified as CLEAN."""
    # Test on a known clean document from dataset
    page_text = auditor.get_clean_page_text("LAB_0001", 1)
    assert page_text is not None
    # Find Glucose position in LAB_0001
    idx = page_text.find("Glucose")
    assert idx >= 0
    end_idx = page_text.find("\n", idx)
    snippet = page_text[idx:end_idx]

    ex = _create_test_example(
        source_doc_id="LAB_0001",
        field_type="lab.value",
        claim="Glucose: 86.0 mg/dL",
        label="SUPPORTED",
        evidence=snippet,
        char_start=idx,
        char_end=idx + 7,  # points to 'Glucose'
    )
    record = auditor.audit_example(ex)
    assert record.provenance_status in (ProvenanceStatus.VALID_ATOMIC, ProvenanceStatus.VALID_VERBATIM)
    assert record.integrity_level in (IntegrityLevel.CLEAN, IntegrityLevel.SUSPICIOUS)
