"""Unit tests for standalone BRAT Annotation Adapter.

Authority: User Request / BRAT Adapter Evaluation Protocol
Location: external_eval/tests/test_brat_adapter.py

Tests:
1. Valid annotations (all canonical medication fields).
2. Multiple entities and duplicate mentions.
3. Whitespace, punctuation, and newline handling.
4. Unsupported labels (Form, Reason, ADE) reported with auditability.
5. Malformed lines (missing columns, non-integer offsets, bad bounds).
6. Offset text mismatch and out-of-bounds detection.
7. Discontinuous spans explicitly flagged and rejected.
8. Missing file handling.
9. Integration with DocumentText container without geometry invention.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

# Add src and external_eval to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "external_eval"))

from adapters.brat_adapter import (
    parse_brat_content,
    parse_brat_file,
    brat_result_to_document_text,
    BratParseResult,
)


def test_valid_medication_annotations():
    """Verify clean parsing of canonical medication fields."""
    doc_text = "Discharged on Lisinopril 10 mg PO once daily for 14 days."
    ann_text = (
        "T1\tDrug 14 24\tLisinopril\n"
        "T2\tStrength 25 30\t10 mg\n"
        "T3\tRoute 31 33\tPO\n"
        "T4\tFrequency 34 44\tonce daily\n"
        "T5\tDuration 49 56\t14 days\n"
    )

    res = parse_brat_content("doc_001", doc_text, ann_text)

    assert res.accepted_count == 5
    assert res.unsupported_count == 0
    assert res.rejected_count == 0

    field_map = {c.field_type: c.value for c in res.accepted_claims}
    assert field_map["med.name"] == "Lisinopril"
    assert field_map["med.dose"] == "10 mg"
    assert field_map["med.route"] == "PO"
    assert field_map["med.frequency"] == "once daily"
    assert field_map["med.duration"] == "14 days"


def test_duplicate_mentions():
    """Verify duplicate mentions of the same drug in a single document."""
    doc_text = "Take Metformin in morning. Metformin repeated in evening."
    ann_text = (
        "T1\tDrug 5 14\tMetformin\n"
        "T2\tDrug 27 36\tMetformin\n"
    )

    res = parse_brat_content("doc_dup", doc_text, ann_text)

    assert res.accepted_count == 2
    claims = [c for c in res.accepted_claims if c.field_type == "med.name"]
    assert len(claims) == 2
    assert claims[0].char_start == 5
    assert claims[1].char_start == 27


def test_unsupported_labels_reported_never_discarded():
    """Verify Form, Reason, and ADE are recorded under unsupported_annotations."""
    doc_text = "Aspirin 81 mg tablet for stroke prevention caused gastritis."
    ann_text = (
        "T1\tDrug 0 7\tAspirin\n"
        "T2\tStrength 8 13\t81 mg\n"
        "T3\tForm 14 20\ttablet\n"
        "T4\tReason 25 42\tstroke prevention\n"
        "T5\tADE 50 59\tgastritis\n"
    )

    res = parse_brat_content("doc_unsupported", doc_text, ann_text)

    # 2 accepted canonical claims
    assert res.accepted_count == 2
    accepted_fields = {c.field_type for c in res.accepted_claims}
    assert accepted_fields == {"med.name", "med.dose"}

    # 3 unsupported labels explicitly preserved
    assert res.unsupported_count == 3
    unsupported_labels = {u["raw_label"] for u in res.unsupported_annotations}
    assert unsupported_labels == {"Form", "Reason", "ADE"}

    # Ensure none were silently dropped
    summary = res.summary()
    assert summary["accepted_claims_count"] == 2
    assert summary["unsupported_annotations_count"] == 3
    assert summary["rejected_annotations_count"] == 0


def test_malformed_lines():
    """Verify robust handling of malformed lines without throwing exceptions."""
    doc_text = "Lisinopril 10 mg."
    ann_text = (
        "T1 Drug 0 10 Lisinopril\n"  # Missing tabs
        "NotAnEntityLine\n"           # Completely malformed
        "T2\tStrength\n"             # Missing text column
        "T3\tRoute invalid 10\tPO\n" # Non-integer offset
        "T4\tFrequency 20 10\tBID\n" # End < Start
    )

    res = parse_brat_content("doc_malformed", doc_text, ann_text)

    assert res.accepted_count == 0
    assert res.rejected_count == 5
    rejected_types = {r.issue_type for r in res.rejected_annotations}
    assert "malformed_line" in rejected_types
    assert "invalid_offsets" in rejected_types


def test_offset_mismatch_and_bounds():
    """Verify mismatch between annotated string and document text slice."""
    doc_text = "Metoprolol 25 mg."
    ann_text = (
        "T1\tDrug 0 10\tLisinopril\n"  # Slice is 'Metoprolol', not 'Lisinopril'
        "T2\tStrength 11 50\t25 mg\n"   # End offset (50) exceeds document length (17)
    )

    res = parse_brat_content("doc_mismatch", doc_text, ann_text)

    assert res.accepted_count == 0
    assert res.rejected_count == 2

    mismatch_issue = next(r for r in res.rejected_annotations if r.issue_type == "offset_text_mismatch")
    assert mismatch_issue.expected_text == "Lisinopril"
    assert mismatch_issue.actual_text == "Metoprolol"

    bounds_issue = next(r for r in res.rejected_annotations if r.issue_type == "out_of_bounds")
    assert "exceeds document length" in bounds_issue.message


def test_discontinuous_spans():
    """Verify that discontinuous spans are captured as explicit parse issues."""
    doc_text = "Patient was prescribed calcium and vitamin D."
    ann_text = (
        "T1\tDrug 23 30;35 44\tcalcium vitamin D\n"
    )

    res = parse_brat_content("doc_discont", doc_text, ann_text)

    assert res.accepted_count == 0
    assert res.rejected_count == 1
    issue = res.rejected_annotations[0]
    assert issue.issue_type == "discontinuous_span"
    assert "cannot be represented as single scalar span" in issue.message


def test_file_reading_and_fixtures(tmp_path):
    """Test reading from actual files on disk."""
    txt_file = tmp_path / "test_doc.txt"
    ann_file = tmp_path / "test_doc.ann"

    txt_file.write_text("Atorvastatin 20 mg oral daily.", encoding="utf-8")
    ann_file.write_text(
        "T1\tDrug 0 12\tAtorvastatin\n"
        "T2\tStrength 13 18\t20 mg\n"
        "T3\tRoute 19 23\toral\n"
        "T4\tFrequency 24 29\tdaily\n"
        "R1\tStrength-Drug Arg1:T2 Arg2:T1\n",
        encoding="utf-8",
    )

    res = parse_brat_file(txt_file, ann_file)
    assert res.accepted_count == 4
    assert len(res.relations) == 1
    assert res.relations[0]["relation_name"] == "Strength-Drug"

    # Convert to DocumentText
    doc_text_obj = brat_result_to_document_text(res)
    assert doc_text_obj.document_id == "test_doc"
    assert doc_text_obj.num_pages == 1
    assert len(doc_text_obj.pages[0].tokens) == 5
    # Verify geometry is degenerate (no false layout claim)
    assert doc_text_obj.pages[0].tokens[0].bbox.to_list() == [0.0, 0.0, 0.0, 0.0]


def test_missing_files_error(tmp_path):
    """Verify FileNotFoundError when files are missing."""
    txt_file = tmp_path / "nonexistent.txt"
    ann_file = tmp_path / "nonexistent.ann"

    with pytest.raises(FileNotFoundError):
        parse_brat_file(txt_file, ann_file)


def test_synthetic_discharge_summary_fixture():
    """Verify parsing of the multi-order synthetic discharge summary fixture."""
    fixture_dir = Path(__file__).parent / "fixtures"
    txt_file = fixture_dir / "sample_discharge_summary.txt"
    ann_file = fixture_dir / "sample_discharge_summary.ann"

    assert txt_file.exists()
    assert ann_file.exists()

    res = parse_brat_file(txt_file, ann_file)

    # 17 accepted claims: 4 drugs, 4 doses, 4 routes, 4 frequencies, 1 duration
    assert res.accepted_count == 17
    # 4 unsupported annotations: 3 Reasons (hypertension, wheezing, sinusitis), 1 ADE (mild nausea)
    assert res.unsupported_count == 4
    # 0 rejected annotations
    assert res.rejected_count == 0
    # 13 relations preserved
    assert len(res.relations) == 13

    summary = res.summary()
    assert summary["accepted_by_field"]["med.name"] == 4
    assert summary["accepted_by_field"]["med.dose"] == 4
    assert summary["accepted_by_field"]["med.route"] == 4
    assert summary["accepted_by_field"]["med.frequency"] == 4
    assert summary["accepted_by_field"]["med.duration"] == 1

    assert summary["unsupported_by_label"]["Reason"] == 3
    assert summary["unsupported_by_label"]["ADE"] == 1

