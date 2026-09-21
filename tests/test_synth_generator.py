"""Tests for deterministic synthetic lab document and ground-truth generation."""

import json
from pathlib import Path
import tempfile
import pytest

from clindoc.synth.generator import generate_ground_truth_dataset, generate_synthetic_dataset
from clindoc.synth.schema import LabReportGroundTruth


def test_generator_deterministic_seed_42():
    """Verify that running generation with seed 42 produces identical ground-truth records across runs."""
    run_1 = generate_ground_truth_dataset(count=10, seed=42)
    run_2 = generate_ground_truth_dataset(count=10, seed=42)

    assert len(run_1) == 10
    assert len(run_2) == 10

    for r1, r2 in zip(run_1, run_2):
        assert r1.doc_id == r2.doc_id
        assert r1.patient.model_dump() == r2.patient.model_dump()
        assert r1.report_date == r2.report_date
        assert r1.template_id == r2.template_id
        assert len(r1.tests) == len(r2.tests)
        for t1, t2 in zip(r1.tests, r2.tests):
            assert t1.model_dump() == t2.model_dump()


def test_different_seeds_produce_variation():
    """Verify that different seeds produce different outputs."""
    run_a = generate_ground_truth_dataset(count=5, seed=42)
    run_b = generate_ground_truth_dataset(count=5, seed=99)

    assert run_a[0].patient.name != run_b[0].patient.name or run_a[0].tests[0].value != run_b[0].tests[0].value


def test_required_lab_fields_present_and_valid():
    """Verify that all generated lab fields are present, properly typed, and clinically plausible."""
    records = generate_ground_truth_dataset(count=20, seed=42)

    for record in records:
        assert record.doc_id.startswith("LAB_")
        assert record.patient.patient_id.startswith("PAT-")
        assert record.patient.name
        assert record.patient.dob
        assert record.patient.gender in ("M", "F")
        assert record.report_date
        assert record.ordering_provider
        assert record.facility_name
        assert record.template_id in ("template_a", "template_b", "template_c")
        assert len(record.tests) > 0

        for t in record.tests:
            assert t.test_name
            assert t.loinc_code
            assert isinstance(t.value, float)
            assert t.unit
            assert t.ref_range_low < t.ref_range_high
            assert "-" in t.ref_range_text
            assert t.flag in ("NORMAL", "HIGH", "LOW")

            # Check flag logic consistency
            if t.value > t.ref_range_high:
                assert t.flag == "HIGH"
            elif t.value < t.ref_range_low:
                assert t.flag == "LOW"
            else:
                assert t.flag == "NORMAL"


def test_dataset_generation_count_and_file_correspondence():
    """Verify that generate_synthetic_dataset produces matching PDFs and ground-truth JSONs."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        pdf_dir = tmp_path / "rendered"
        gt_dir = tmp_path / "ground_truth"

        count = 15
        records = generate_synthetic_dataset(
            output_pdf_dir=pdf_dir,
            output_gt_dir=gt_dir,
            count=count,
            seed=42,
        )

        assert len(records) == count

        pdf_files = list(pdf_dir.glob("*.pdf"))
        gt_files = list(gt_dir.glob("*.json"))

        assert len(pdf_files) == count
        assert len(gt_files) == count

        # Check 1-to-1 correspondence
        for pdf_file in pdf_files:
            doc_id = pdf_file.stem
            gt_file = gt_dir / f"{doc_id}.json"
            assert gt_file.exists(), f"Missing ground-truth JSON for {pdf_file.name}"
            assert pdf_file.stat().st_size > 1000, f"Rendered PDF {pdf_file.name} is suspiciously small"

            # Check valid JSON deserialization
            with open(gt_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            parsed = LabReportGroundTruth.model_validate(data)
            assert parsed.doc_id == doc_id
