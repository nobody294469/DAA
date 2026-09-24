"""Unit tests for M2 dataset schema, generation logic, and metadata."""
import json
import pytest
from pathlib import Path

from clindoc.m2.schema import (
    M2Example, VerificationLabel, FieldType, M2DatasetMetadata,
    LABEL_ORDER, AUX_DIM
)
from clindoc.m2.dataset import save_examples, load_examples, compute_metadata


def test_m2_example_schema():
    ex = M2Example(
        example_id="test_001",
        claim="Glucose: 95 mg/dL [70 - 99] NORMAL",
        evidence="Glucose: 95 mg/dL",
        field_type="lab.value",
        doc_template="template_a",
        ocr_confidence=0.98,
        severity="clean",
        label=VerificationLabel.SUPPORTED,
        source_doc_id="SYNTH_LAB_0001",
        generation_method="clean_extraction",
        claim_type="lab",
        split="cv",
    )
    assert ex.label == VerificationLabel.SUPPORTED
    assert ex.ocr_confidence == 0.98
    assert ex.field_type in [f.value for f in FieldType]


def test_m2_example_invalid_confidence():
    with pytest.raises(Exception):
        M2Example(
            example_id="test_bad",
            claim="test",
            evidence="test",
            field_type="lab.value",
            doc_template="template_a",
            ocr_confidence=1.5,  # Invalid: > 1.0
            severity="clean",
            label=VerificationLabel.SUPPORTED,
            source_doc_id="SYNTH_LAB_0001",
            generation_method="clean_extraction",
            claim_type="lab",
        )


def test_dataset_jsonl_roundtrip(tmp_path: Path):
    examples = [
        M2Example(
            example_id=f"ex_{i}",
            claim=f"Test claim {i}",
            evidence=f"Test evidence {i}",
            field_type="lab.value",
            doc_template="template_a",
            ocr_confidence=0.9,
            severity="clean",
            label=VerificationLabel.SUPPORTED,
            source_doc_id=f"DOC_{i}",
            generation_method="clean",
            claim_type="lab",
            split="cv",
        )
        for i in range(5)
    ]
    p = tmp_path / "test.jsonl"
    save_examples(examples, p)
    loaded = load_examples(p)
    assert len(loaded) == 5
    assert loaded[0].example_id == "ex_0"
    assert loaded[0].label == VerificationLabel.SUPPORTED


def test_metadata_computation():
    examples = [
        M2Example(
            example_id="1",
            claim="c1",
            evidence="e1",
            field_type="lab.value",
            doc_template="template_a",
            ocr_confidence=1.0,
            severity="clean",
            label=VerificationLabel.SUPPORTED,
            source_doc_id="DOC_1",
            generation_method="clean",
            claim_type="lab",
        ),
        M2Example(
            example_id="2",
            claim="c2",
            evidence="e2",
            field_type="med.dose",
            doc_template="mar_order_sheet",
            ocr_confidence=0.8,
            severity="low",
            label=VerificationLabel.DISCREPANCY,
            source_doc_id="DOC_2",
            generation_method="sub",
            claim_type="med",
        ),
    ]
    meta = compute_metadata(examples, ood_doc_ids=["DOC_2"], cv_doc_ids=["DOC_1"])
    assert meta.total_examples == 2
    assert meta.label_counts["SUPPORTED"] == 1
    assert meta.label_counts["DISCREPANCY"] == 1
    assert "DOC_2" in meta.ood_doc_ids
