"""Standalone integration tests connecting BRAT adapter to canonical extraction evaluator.

Authority: User Request / Evaluation Contract Verification
Location: external_eval/tests/test_brat_evaluator_integration.py

Verifies:
1. Perfect synthetic predictions produce 100% precision, recall, and F1 across canonical fields.
2. Missing prediction produces an exact false negative (FN=1).
3. Incorrect extra prediction produces an exact false positive (FP=1).
4. Duplicate entity mentions are resolved via 1-to-1 bipartite greedy matching.
5. Unsupported annotations (Reason, ADE, Form) remain isolated and do not distort canonical metrics.
6. Evidence offsets and text slices match the source document verbatim.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "external_eval"))

from adapters.brat_adapter import parse_brat_file, parse_brat_content
from clindoc.extract.evaluate import (
    evaluate_document,
    evaluate_dataset,
    GroundTruthClaim,
    ALL_CANONICAL_FIELDS,
)
from clindoc.extract.contracts import Claim


@pytest.fixture
def sample_fixture_path() -> tuple[Path, Path]:
    fixtures_dir = Path(__file__).parent / "fixtures"
    txt_path = fixtures_dir / "sample_discharge_summary.txt"
    ann_path = fixtures_dir / "sample_discharge_summary.ann"
    assert txt_path.exists(), f"Missing fixture: {txt_path}"
    assert ann_path.exists(), f"Missing fixture: {ann_path}"
    return txt_path, ann_path


def test_perfect_predictions_produce_perfect_metrics(sample_fixture_path):
    """Verify that perfect synthetic predictions match BRAT ground truth with 1.0 F1."""
    txt_path, ann_path = sample_fixture_path
    res = parse_brat_file(txt_path, ann_path)

    assert res.accepted_count == 17

    # Build 1-to-1 matching synthetic predictions
    pred_claims = []
    for c in res.accepted_claims:
        pred_claims.append(Claim(
            document_id=c.document_id,
            field_type=c.field_type,
            value=c.value,
            value_numeric=c.value_numeric,
            unit_raw=c.unit_raw,
            extraction_method="deterministic",
            extraction_confidence=1.0,
            evidence_id="ev_test",
            region_id="reg_0",
        ))

    doc_eval = evaluate_document(res.document_id, pred_claims, res.accepted_claims)

    assert doc_eval["total_tp"] == 17
    assert doc_eval["total_fp"] == 0
    assert doc_eval["total_fn"] == 0
    assert doc_eval["micro_precision"] == 1.0
    assert doc_eval["micro_recall"] == 1.0
    assert doc_eval["micro_f1"] == 1.0
    assert doc_eval["exact_match_rate"] == 1.0

    # Dataset-level evaluator aggregation check
    dataset_eval = evaluate_dataset(
        {res.document_id: pred_claims},
        {res.document_id: res.accepted_claims},
    )
    assert dataset_eval["total_gt_claims"] == 17
    assert dataset_eval["total_pred_claims"] == 17
    assert dataset_eval["micro_precision"] == 1.0
    assert dataset_eval["micro_recall"] == 1.0
    assert dataset_eval["micro_f1"] == 1.0


def test_missing_prediction_produces_false_negative(sample_fixture_path):
    """Verify that omitting a ground truth entity produces exactly 1 False Negative."""
    txt_path, ann_path = sample_fixture_path
    res = parse_brat_file(txt_path, ann_path)

    # Predict everything EXCEPT the first drug (Lisinopril)
    omitted_claim = next(c for c in res.accepted_claims if c.field_type == "med.name" and "Lisinopril" in c.value)
    pred_claims = [
        Claim(
            document_id=c.document_id,
            field_type=c.field_type,
            value=c.value,
            value_numeric=c.value_numeric,
            unit_raw=c.unit_raw,
            extraction_method="deterministic",
            extraction_confidence=1.0,
            evidence_id="ev_test",
            region_id="reg_0",
        )
        for c in res.accepted_claims
        if c != omitted_claim
    ]

    doc_eval = evaluate_document(res.document_id, pred_claims, res.accepted_claims)

    assert doc_eval["total_tp"] == 16
    assert doc_eval["total_fp"] == 0
    assert doc_eval["total_fn"] == 1
    assert doc_eval["micro_precision"] == 1.0
    assert round(doc_eval["micro_recall"], 4) == round(16 / 17, 4)

    # Check field-level metrics for med.name specifically
    name_metrics = doc_eval["fields"]["med.name"]
    assert name_metrics["tp"] == 3
    assert name_metrics["fn"] == 1
    assert name_metrics["fp"] == 0
    assert name_metrics["recall"] == 0.75
    assert name_metrics["precision"] == 1.0


def test_extra_prediction_produces_false_positive(sample_fixture_path):
    """Verify that an extra unannotated prediction produces exactly 1 False Positive."""
    txt_path, ann_path = sample_fixture_path
    res = parse_brat_file(txt_path, ann_path)

    pred_claims = [
        Claim(
            document_id=c.document_id,
            field_type=c.field_type,
            value=c.value,
            value_numeric=c.value_numeric,
            unit_raw=c.unit_raw,
            extraction_method="deterministic",
            extraction_confidence=1.0,
            evidence_id="ev_test",
            region_id="reg_0",
        )
        for c in res.accepted_claims
    ]

    # Add 1 hallucinated prediction
    pred_claims.append(Claim(
        document_id=res.document_id,
        field_type="med.name",
        value="Warfarin",
        extraction_method="gliner",
        extraction_confidence=0.85,
        evidence_id="ev_hallucinated",
        region_id="reg_0",
    ))

    doc_eval = evaluate_document(res.document_id, pred_claims, res.accepted_claims)

    assert doc_eval["total_tp"] == 17
    assert doc_eval["total_fp"] == 1
    assert doc_eval["total_fn"] == 0
    assert doc_eval["micro_recall"] == 1.0
    assert round(doc_eval["micro_precision"], 4) == round(17 / 18, 4)

    name_metrics = doc_eval["fields"]["med.name"]
    assert name_metrics["tp"] == 4
    assert name_metrics["fp"] == 1
    assert name_metrics["fn"] == 0
    assert name_metrics["recall"] == 1.0
    assert name_metrics["precision"] == 0.8


def test_duplicate_mentions_greedy_matching():
    """Verify bipartite greedy matching handles duplicate mentions without double-counting."""
    doc_text = "Take Aspirin 81 mg in AM. Take Aspirin 81 mg in PM."
    ann_text = (
        "T1\tDrug 5 12\tAspirin\n"
        "T2\tStrength 13 18\t81 mg\n"
        "T3\tDrug 31 38\tAspirin\n"
        "T4\tStrength 39 44\t81 mg\n"
    )

    res = parse_brat_content("doc_dup_test", doc_text, ann_text)
    assert res.accepted_count == 4

    # Case A: Model predicts only 1 Aspirin
    pred_claims_1 = [
        Claim(
            document_id="doc_dup_test",
            field_type="med.name",
            value="Aspirin",
            extraction_method="gliner",
            extraction_confidence=0.9,
            evidence_id="ev_1",
            region_id="reg_0",
        )
    ]
    eval_1 = evaluate_document("doc_dup_test", pred_claims_1, res.accepted_claims)
    name_m1 = eval_1["fields"]["med.name"]
    assert name_m1["tp"] == 1
    assert name_m1["fn"] == 1  # Exactly 1 missed duplicate
    assert name_m1["fp"] == 0
    assert name_m1["precision"] == 1.0
    assert name_m1["recall"] == 0.5

    # Case B: Model predicts both Aspirin mentions
    pred_claims_2 = [
        Claim(
            document_id="doc_dup_test",
            field_type="med.name",
            value="Aspirin",
            extraction_method="gliner",
            extraction_confidence=0.9,
            evidence_id="ev_1",
            region_id="reg_0",
        ),
        Claim(
            document_id="doc_dup_test",
            field_type="med.name",
            value="Aspirin",
            extraction_method="gliner",
            extraction_confidence=0.9,
            evidence_id="ev_2",
            region_id="reg_0",
        ),
    ]
    eval_2 = evaluate_document("doc_dup_test", pred_claims_2, res.accepted_claims)
    name_m2 = eval_2["fields"]["med.name"]
    assert name_m2["tp"] == 2
    assert name_m2["fn"] == 0
    assert name_m2["fp"] == 0
    assert name_m2["precision"] == 1.0
    assert name_m2["recall"] == 1.0


def test_unsupported_annotations_isolation(sample_fixture_path):
    """Verify unsupported annotations (Reason, ADE) do not contaminate canonical evaluation."""
    txt_path, ann_path = sample_fixture_path
    res = parse_brat_file(txt_path, ann_path)

    # 4 unsupported annotations were captured by adapter
    assert len(res.unsupported_annotations) == 4
    unsupported_labels = {u["raw_label"] for u in res.unsupported_annotations}
    assert unsupported_labels == {"Reason", "ADE"}

    # Verify that accepted_claims contain strictly canonical field types
    for c in res.accepted_claims:
        assert c.field_type in ALL_CANONICAL_FIELDS

    # Verify that evaluator reports 0 ground truth for non-existent fields
    doc_eval = evaluate_document(res.document_id, [], res.accepted_claims)
    assert doc_eval["total_gt"] == 17
    # All 17 become false negatives when predictions are empty
    assert doc_eval["total_fn"] == 17
    assert doc_eval["total_tp"] == 0


def test_evidence_offsets_and_text_validity(sample_fixture_path):
    """Verify evidence text matches document slice at character offsets."""
    txt_path, ann_path = sample_fixture_path
    res = parse_brat_file(txt_path, ann_path)
    source_text = res.source_text

    for claim in res.accepted_claims:
        assert claim.char_start is not None
        assert claim.char_end is not None
        assert claim.char_start < claim.char_end
        assert claim.char_end <= len(source_text)

        # Slice verbatim text from source document
        sliced_text = source_text[claim.char_start:claim.char_end]
        assert sliced_text == claim.evidence_text
        assert sliced_text == claim.value
