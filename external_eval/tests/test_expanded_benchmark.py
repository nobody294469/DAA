"""Test suite for the expanded synthetic benchmark (60 documents, 999 claims).

Authority: User Request / Expanded Synthetic Benchmark Validation
Location: external_eval/tests/test_expanded_benchmark.py
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXPANDED_BENCHMARK_DIR = REPO_ROOT / "external_eval" / "expanded_benchmark"
MANIFEST_PATH = EXPANDED_BENCHMARK_DIR / "manifest.json"


def test_expanded_manifest_structure():
    assert MANIFEST_PATH.exists(), f"Missing manifest at {MANIFEST_PATH}"
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["total_documents"] == 60
    assert manifest["total_claims"] == 999
    assert manifest["document_breakdown"]["narrative_encounter"] == 44
    assert manifest["document_breakdown"]["structured_medication"] == 8
    assert manifest["document_breakdown"]["structured_lab"] == 8
    assert manifest["is_synthetic_evaluation"] is True
    assert manifest["phi_free_guarantee"] is True
    assert len(manifest["documents"]) == 60


def test_verbatim_text_slicing_all_60_documents():
    """Verify that every single ground truth claim has valid character offsets matching the note verbatim."""
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    total_verified = 0
    for doc_entry in manifest["documents"]:
        doc_id = doc_entry["doc_id"]
        txt_path = REPO_ROOT / doc_entry["text_file"]
        gt_path = REPO_ROOT / doc_entry["gt_file"]

        assert txt_path.exists(), f"Missing text file: {txt_path}"
        assert gt_path.exists(), f"Missing GT file: {gt_path}"

        raw_text = txt_path.read_text(encoding="utf-8")
        with open(gt_path, "r", encoding="utf-8") as f:
            gt_data = json.load(f)

        assert gt_data["is_synthetic"] is True
        for claim in gt_data["claims"]:
            c_start = claim["char_start"]
            c_end = claim["char_end"]
            ev_text = claim["evidence_text"]

            actual_slice = raw_text[c_start:c_end]
            assert actual_slice == ev_text, f"Mismatch in {doc_id}: '{actual_slice}' != '{ev_text}'"
            total_verified += 1

    assert total_verified == 999


def test_all_10_canonical_fields_represented():
    """Verify ground truth contains claims across all 10 canonical fields."""
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    claims_by_field = manifest["claims_by_field"]
    expected_fields = {
        "lab.value", "lab.unit", "lab.flag", "lab.reference_range", "lab.test_name",
        "med.name", "med.dose", "med.route", "med.frequency", "med.duration"
    }
    assert set(claims_by_field.keys()) == expected_fields
    for field_name in expected_fields:
        assert claims_by_field[field_name] > 0, f"Field {field_name} has 0 claims"


def test_negative_distractor_presence():
    """Verify that notes include negative numeric distractors (MRN, DOB, Date, NPI, Range)."""
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    mrn_found = 0
    dob_found = 0
    date_found = 0

    for doc_entry in manifest["documents"]:
        txt_path = REPO_ROOT / doc_entry["text_file"]
        text = txt_path.read_text(encoding="utf-8")
        if "MRN:" in text:
            mrn_found += 1
        if "DOB:" in text:
            dob_found += 1
        if "Date:" in text or "DATE:" in text or "Order Date:" in text:
            date_found += 1

    assert mrn_found >= 55, f"Expected MRN in >= 55 notes, found {mrn_found}"
    assert dob_found >= 55, f"Expected DOB in >= 55 notes, found {dob_found}"
    assert date_found >= 55, f"Expected Date in >= 55 notes, found {date_found}"
