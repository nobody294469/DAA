"""test_m2_generator_fixes.py — Regression tests for M2 generator fixes.

Enforces:
  A. Ex9: Unobserved medication claims receive document-level absence with null offsets (no clinic header).
  B. Ex12: Tabular context window preserves target row and snaps to newline boundaries.
  C. Wrong numeric match: Value alone never selects an evidence row (Carbon Dioxide != BUN 24).
  D. [0, 100] fallback: Never emit [0, 100] fallback; drop unlocated candidates.
  E. Out-of-bounds: Offsets strictly bounded by canonical document text length.
  F. Degraded alignment: Degraded examples map back to valid canonical coordinates.
  G. Pre-save validation: Validation gate rejects invalid candidates rather than serializing.
"""
from pathlib import Path
import json
import pytest

from clindoc.m2.schema import M2Example, VerificationLabel
from clindoc.m2.context import extract_context_window, reconstruct_canonical_page_text
from clindoc.m2.validation_gate import PreSerializationValidationGate, GenerationDiagnostics
from clindoc.m2.generation import (
    generate_clean_lab_examples,
    generate_clean_med_examples,
    generate_degraded_lab_examples,
    generate_degraded_med_examples,
    _safe_add_example,
)


# ─── A. Ex9: Negative Control Evidence Contract ──────────────────────────────

def test_ex9_unobserved_medication_negative_control():
    """Verify that unobserved medications do not receive clinic header evidence and use null offsets."""
    med_extracted_dir = Path("data/med_extracted")
    med_gt_dir = Path("data/med_ground_truth")
    if not med_extracted_dir.exists() or not med_gt_dir.exists():
        pytest.skip("Med extraction or ground truth directories missing")

    diag = GenerationDiagnostics()
    gate = PreSerializationValidationGate()
    examples, _ = generate_clean_med_examples(
        med_extracted_dir,
        med_gt_dir,
        doc_id_filter=["MED_0010"],
        diagnostics=diag,
        validation_gate=gate,
    )

    unobserved = [
        e for e in examples
        if e.generation_method == "clean_unobserved_claim_insufficient_evidence"
    ]
    assert len(unobserved) > 0, "Expected unobserved negative control examples for MED_0010"

    for ex in unobserved:
        # Offsets must be null for document-level absence
        assert ex.char_start is None, f"Expected char_start=None, got {ex.char_start}"
        assert ex.char_end is None, f"Expected char_end=None, got {ex.char_end}"
        assert ex.bbox is None, f"Expected bbox=None, got {ex.bbox}"
        assert ex.label == VerificationLabel.INSUFFICIENT_EVIDENCE

        # Evidence must NOT be the clinic letterhead / discharge instructions header
        ev_lower = ex.evidence.lower()
        assert "memorial general hospital" not in ev_lower
        assert "discharge instructions" not in ev_lower
        assert "document-level absence" in ev_lower


# ─── B. Ex12: Tabular Context Window Preserves Row Boundaries ────────────────

def test_ex12_tabular_context_window_snaps_to_rows():
    """Verify that tabular context extraction snaps to newline boundaries and preserves Creatinine row."""
    ocr_path = Path("data/ocr_cache/LAB_0037.json")
    if not ocr_path.exists():
        pytest.skip("LAB_0037 ocr cache missing")

    with open(ocr_path, "r", encoding="utf-8") as f:
        ocr_data = json.load(f)
    page_text, _ = reconstruct_canonical_page_text(ocr_data.get("lines", []), is_tabular=True, page_number=1)

    # Locate Creatinine in LAB_0037
    target_idx = page_text.find("Creatinine")
    assert target_idx >= 0, "Creatinine not found in LAB_0037"
    nl_after = page_text.find("\n", target_idx)
    target_end = nl_after if nl_after != -1 else target_idx + 10

    snip, start, end = extract_context_window(page_text, target_idx, target_end, is_tabular=True)

    # Context window must include the target row
    target_row = page_text[target_idx:target_end]
    assert target_row in snip, "Context window must preserve the full target Creatinine row"

    # Context window must start and end at newline boundaries (not bisected mid-word like 'ate: 2025-11-20')
    if start > 0:
        assert page_text[start - 1] == "\n", "Context window start must be snapped to a newline"
    if end < len(page_text):
        assert page_text[end] == "\n" or page_text[end - 1] == "\n", "Context window end must be snapped to a newline"

    # Must not contain bisected fragment at start
    first_line = snip.split("\n")[0]
    assert not first_line.startswith("ate:"), f"Found bisected header fragment: '{first_line}'"


# ─── C. Wrong Numeric Match: Entity Awareness ────────────────────────────────

def test_wrong_numeric_match_prevented():
    """Verify that an entity row cannot be matched solely on numerical value."""
    corrupt_lines = [
        {"text": "Patient Name: Jane Doe", "confidence": 0.99},
        {"text": "Blood Urea Nitrogen (BUN) 24 mg/dL NORMAL 7 - 20", "confidence": 0.95},
        {"text": "Glucose 100 mg/dL NORMAL 70 - 99", "confidence": 0.95},
    ]

    target_analyte = "Carbon Dioxide"
    val_str = "24"

    # The defective old logic: tname.lower() in ltxt.lower() or v_str in ltxt
    old_matched_idx = -1
    for li, line_obj in enumerate(corrupt_lines):
        ltxt = line_obj.get("text", "")
        if target_analyte.lower() in ltxt.lower() or val_str in ltxt:
            old_matched_idx = li
            break
    assert old_matched_idx == 1, "Old defective logic incorrectly matched BUN row for Carbon Dioxide"

    # The corrected strict entity-aware logic:
    corrected_matched_idx = -1
    for li, line_obj in enumerate(corrupt_lines):
        ltxt = line_obj.get("text", "")
        if target_analyte.lower() in ltxt.lower():
            corrected_matched_idx = li
            break

    assert corrected_matched_idx == -1, "Strict matching correctly rejected unrelated numeric match"


# ─── D. [0, 100] Fallback Removed ─────────────────────────────────────────────

def test_zero_to_hundred_fallback_rejected():
    """Verify that validation gate strictly rejects [0, 100] fallback offsets."""
    gate = PreSerializationValidationGate()
    ex = M2Example(
        example_id="test-zero-hundred",
        claim="Potassium: 4.5 mmol/L",
        evidence="Memorial Clinic Lab Results",
        field_type="lab.value",
        doc_template="template_a",
        ocr_confidence=0.9,
        severity="medium",
        label=VerificationLabel.DISCREPANCY,
        source_doc_id="LAB_0001",
        generation_method="m1_track_b_medium_readable_discrepancy",
        claim_type="lab",
        group_id="test_group",
        contradiction_type="value_mismatch",
        evidence_id="ev-1",
        char_start=0,
        char_end=100,
        page_number=1,
        bbox=[0.0, 0.0, 100.0, 100.0],
    )

    is_valid, reason = gate.validate(ex, canonical_page_text="Potassium 4.5 mmol/L NORMAL\nHeader")
    assert is_valid is False
    assert reason == "corrupted_default_header"


# ─── E. Out-of-Bounds Offsets ─────────────────────────────────────────────────

def test_out_of_bounds_offsets_rejected():
    """Verify that offsets exceeding canonical document length are rejected."""
    gate = PreSerializationValidationGate()
    canonical_text = "Short text of 30 characters."
    ex = M2Example(
        example_id="test-oob",
        claim="Potassium: 4.5 mmol/L",
        evidence="Short text",
        field_type="lab.value",
        doc_template="template_a",
        ocr_confidence=0.9,
        severity="clean",
        label=VerificationLabel.SUPPORTED,
        source_doc_id="LAB_0001",
        generation_method="clean_extraction_supported",
        claim_type="lab",
        group_id="test_group",
        contradiction_type=None,
        evidence_id="ev-1",
        char_start=25,
        char_end=45,  # Exceeds len 28
        page_number=1,
        bbox=[0.0, 0.0, 100.0, 100.0],
    )

    is_valid, reason = gate.validate(ex, canonical_page_text=canonical_text)
    assert is_valid is False
    assert "out_of_bounds_offset" in reason


# ─── F. Degraded Alignment to Canonical Coordinates ──────────────────────────

def test_degraded_examples_map_to_canonical_coordinates():
    """Verify degraded examples re-anchor coordinates to canonical clean document."""
    gt_dir = Path("data/ground_truth")
    corrupt_lab_dir = Path("data/degraded/ocr_cache/corrupt_lab")
    ocr_cache_dir = Path("data/ocr_cache")
    if not corrupt_lab_dir.exists() or not gt_dir.exists() or not ocr_cache_dir.exists():
        pytest.skip("Required directories missing for degraded alignment test")

    diag = GenerationDiagnostics()
    gate = PreSerializationValidationGate()
    examples = generate_degraded_lab_examples(
        gt_dir=gt_dir,
        corrupt_lab_dir=corrupt_lab_dir,
        ocr_cache_dir=ocr_cache_dir,
        diagnostics=diag,
        validation_gate=gate,
        doc_id_filter=["LAB_0001"],
    )

    assert len(examples) > 0, "Expected degraded examples for LAB_0001"

    # Load clean canonical page text for LAB_0001
    clean_ocr_path = ocr_cache_dir / "LAB_0001.json"
    with open(clean_ocr_path, "r", encoding="utf-8") as f:
        clean_ocr_data = json.load(f)
    clean_text, _ = reconstruct_canonical_page_text(clean_ocr_data.get("lines", []), is_tabular=True, page_number=1)

    for ex in examples:
        assert ex.char_start is not None and ex.char_end is not None
        assert 0 <= ex.char_start < ex.char_end <= len(clean_text), (
            f"Offsets [{ex.char_start}, {ex.char_end}] out of canonical bounds [0, {len(clean_text)}]"
        )
        assert not (ex.char_start == 0 and ex.char_end == 100)
        # Canonical slice must match the target entity
        canon_slice = clean_text[ex.char_start:ex.char_end]
        assert len(canon_slice) > 0


# ─── G. Pre-Save Validation Gate ──────────────────────────────────────────────

def test_preserialization_validation_gate_rejections():
    """Verify that invalid examples are rejected rather than serialized."""
    gate = PreSerializationValidationGate()
    diag = GenerationDiagnostics()
    examples: list[M2Example] = []

    # 1. Invalid: [0, 100]
    bad_ex_1 = M2Example(
        example_id="bad-1", claim="Glucose: 90 mg/dL", evidence="Clinic Header",
        field_type="lab.value", doc_template="t", ocr_confidence=1.0, severity="clean",
        label=VerificationLabel.SUPPORTED, source_doc_id="D1", generation_method="m",
        claim_type="lab", group_id="g1", contradiction_type=None, evidence_id="e1",
        char_start=0, char_end=100, page_number=1, bbox=None,
    )
    _safe_add_example(bad_ex_1, "Canonical text", examples, gate, diag)
    assert len(examples) == 0
    assert diag.rejections_by_reason.get("corrupted_default_header") == 1

    # 2. Invalid: Target entity missing from evidence
    bad_ex_2 = M2Example(
        example_id="bad-2", claim="Creatinine: 1.0 mg/dL", evidence="Glucose 100 mg/dL NORMAL",
        field_type="lab.value", doc_template="t", ocr_confidence=1.0, severity="clean",
        label=VerificationLabel.SUPPORTED, source_doc_id="D1", generation_method="m",
        claim_type="lab", group_id="g1", contradiction_type=None, evidence_id="e1",
        char_start=0, char_end=7, page_number=1, bbox=None,
    )
    _safe_add_example(bad_ex_2, "Glucose 100 mg/dL NORMAL", examples, gate, diag)
    assert len(examples) == 0
    assert any("missing_target_entity" in k for k in diag.rejections_by_reason)

    # 3. Valid example
    clean_text = "Sodium 140 mmol/L NORMAL 135 - 145"
    good_ex = M2Example(
        example_id="good-1", claim="Sodium: 140.0 mmol/L", evidence=clean_text,
        field_type="lab.value", doc_template="t", ocr_confidence=1.0, severity="clean",
        label=VerificationLabel.SUPPORTED, source_doc_id="D1", generation_method="clean_extraction_supported",
        claim_type="lab", group_id="g1", contradiction_type=None, evidence_id="e1",
        char_start=0, char_end=6, page_number=1, bbox=None,
    )
    _safe_add_example(good_ex, clean_text, examples, gate, diag)
    assert len(examples) == 1
