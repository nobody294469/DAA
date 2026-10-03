"""Unit tests for Canonical E1–E4 Extraction Evaluation Harness.

Covers:
1. Exact match (string and numeric equivalence)
2. Partial and multiple claims (bipartite matching and group linkage)
3. Missing predictions (false negatives, recall impact)
4. Extra predictions (false positives, precision impact)
5. Per-field aggregation (macro-F1, micro-F1, across documents)
6. Empty and invalid inputs
7. Ground truth loader (LAB, MED, NARRATIVE)
8. End-to-end evaluation runner with mock providers
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from clindoc.extract.contracts import Claim, BBoxModel
from clindoc.extract.evaluate import (
    ALL_CANONICAL_FIELDS,
    GroundTruthClaim,
    evaluate_dataset,
    evaluate_document,
    is_value_match,
    is_partial_match,
    load_ground_truth_claims,
    match_claims_for_field,
    normalize_field_value,
    run_extraction_evaluation,
)
from clindoc.extract.router import ExtractionRouter
from clindoc.extract.gliner_fallback import MockGLiNERProvider
from clindoc.extract.llm_extractor import MockLLMProvider


def _make_claim(
    field_type: str,
    value: str,
    value_numeric: float | None = None,
    group_id: str | None = None,
    document_id: str = "TEST_DOC_001",
) -> Claim:
    """Helper to instantiate a valid canonical Claim."""
    return Claim(
        document_id=document_id,
        group_id=group_id,
        field_type=field_type,  # type: ignore[arg-type]
        value=value,
        value_numeric=value_numeric,
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id="ev_001",
        region_id="r_001",
    )


def test_exact_match():
    """1. Exact match: identical string values or equivalent numeric formats."""
    # Text exact match
    gt_name = GroundTruthClaim(document_id="DOC1", field_type="lab.test_name", value="Glucose")
    pred_name = _make_claim("lab.test_name", "Glucose")
    assert is_value_match(gt_name, pred_name)

    # Case & whitespace tolerance
    pred_case = _make_claim("lab.test_name", "  glucose  ")
    assert is_value_match(gt_name, pred_case)

    # Numeric exact match with different representations (86 vs 86.0)
    gt_val = GroundTruthClaim(document_id="DOC1", field_type="lab.value", value="86", value_numeric=86.0)
    pred_val = _make_claim("lab.value", "86.0", value_numeric=86.0)
    assert is_value_match(gt_val, pred_val)

    # Single-field match evaluation
    res = match_claims_for_field([gt_name], [pred_name], "lab.test_name")
    assert res["tp"] == 1
    assert res["fp"] == 0
    assert res["fn"] == 0
    assert res["precision"] == 1.0
    assert res["recall"] == 1.0
    assert res["f1"] == 1.0
    assert res["exact_match_rate"] == 1.0


def test_partial_and_multiple_claims():
    """2. Partial and multiple claims: greedy matching and non-exact tracking."""
    gt_claims = [
        GroundTruthClaim(document_id="DOC1", group_id="g1", field_type="med.name", value="Atorvastatin"),
        GroundTruthClaim(document_id="DOC1", group_id="g2", field_type="med.name", value="Metformin"),
    ]
    # One exact match, one partial match (Metformin HCl)
    pred_claims = [
        _make_claim("med.name", "Atorvastatin", group_id="g1"),
        _make_claim("med.name", "Metformin HCl", group_id="g2"),
    ]

    # Metformin HCl is a partial match for Metformin
    assert not is_value_match(gt_claims[1], pred_claims[1])
    assert is_partial_match(gt_claims[1], pred_claims[1])

    res = match_claims_for_field(gt_claims, pred_claims, "med.name")
    assert res["gt_count"] == 2
    assert res["pred_count"] == 2
    assert res["exact_matches"] == 1
    assert res["partial_matches"] == 1
    assert res["tp"] == 1  # TP requires exact value match
    assert res["fp"] == 1
    assert res["fn"] == 1
    assert res["precision"] == 0.5
    assert res["recall"] == 0.5
    assert res["f1"] == 0.5
    assert res["exact_match_rate"] == 0.5


def test_missing_predictions():
    """3. Missing predictions: false negatives lower recall."""
    gt_claims = [
        GroundTruthClaim(document_id="DOC1", field_type="lab.value", value="14.0", value_numeric=14.0),
        GroundTruthClaim(document_id="DOC1", field_type="lab.value", value="29.3", value_numeric=29.3),
    ]
    # Predict only 1 of the 2 values
    pred_claims = [
        _make_claim("lab.value", "14.0", value_numeric=14.0),
    ]

    res = match_claims_for_field(gt_claims, pred_claims, "lab.value")
    assert res["tp"] == 1
    assert res["fp"] == 0
    assert res["fn"] == 1
    assert res["precision"] == 1.0
    assert res["recall"] == 0.5
    assert res["f1"] == pytest.approx(0.6667, abs=1e-3)
    assert res["exact_match_rate"] == 0.5


def test_extra_predictions():
    """4. Extra predictions: false positives lower precision."""
    gt_claims = [
        GroundTruthClaim(document_id="DOC1", field_type="med.name", value="Lisinopril"),
    ]
    # Predict ground truth plus an extra hallucinated/misread medication
    pred_claims = [
        _make_claim("med.name", "Lisinopril"),
        _make_claim("med.name", "Amlodipine"),
    ]

    res = match_claims_for_field(gt_claims, pred_claims, "med.name")
    assert res["tp"] == 1
    assert res["fp"] == 1
    assert res["fn"] == 0
    assert res["precision"] == 0.5
    assert res["recall"] == 1.0
    assert res["f1"] == pytest.approx(0.6667, abs=1e-3)
    assert res["exact_match_rate"] == 1.0


def test_per_field_aggregation():
    """5. Per-field aggregation across multiple documents."""
    doc1_gt = [
        GroundTruthClaim(document_id="DOC1", field_type="lab.value", value="95"),
        GroundTruthClaim(document_id="DOC1", field_type="lab.unit", value="mg/dL"),
    ]
    doc1_pred = [
        _make_claim("lab.value", "95", document_id="DOC1"),
        _make_claim("lab.unit", "mg/dL", document_id="DOC1"),
    ]

    doc2_gt = [
        GroundTruthClaim(document_id="DOC2", field_type="lab.value", value="140"),
        GroundTruthClaim(document_id="DOC2", field_type="lab.flag", value="HIGH"),
    ]
    doc2_pred = [
        _make_claim("lab.value", "140", document_id="DOC2"),
        # Missed lab.flag in DOC2
    ]

    dataset_eval = evaluate_dataset(
        predictions_by_doc={"DOC1": doc1_pred, "DOC2": doc2_pred},
        gt_by_doc={"DOC1": doc1_gt, "DOC2": doc2_gt},
    )

    assert dataset_eval["num_documents"] == 2
    # Check lab.value: 2 GT, 2 Pred -> perfect
    assert dataset_eval["metrics_per_field"]["lab.value"]["tp"] == 2
    assert dataset_eval["metrics_per_field"]["lab.value"]["f1"] == 1.0

    # Check lab.unit: 1 GT, 1 Pred -> perfect
    assert dataset_eval["metrics_per_field"]["lab.unit"]["tp"] == 1
    assert dataset_eval["metrics_per_field"]["lab.unit"]["f1"] == 1.0

    # Check lab.flag: 1 GT, 0 Pred -> recall 0
    assert dataset_eval["metrics_per_field"]["lab.flag"]["tp"] == 0
    assert dataset_eval["metrics_per_field"]["lab.flag"]["fn"] == 1
    assert dataset_eval["metrics_per_field"]["lab.flag"]["recall"] == 0.0

    # Overall dataset metrics
    assert dataset_eval["total_gt_claims"] == 4
    assert dataset_eval["total_pred_claims"] == 3
    # Macro F1: average of active fields (lab.value: 1.0, lab.unit: 1.0, lab.flag: 0.0) -> 2.0 / 3 = 0.6667
    assert dataset_eval["macro_f1"] == pytest.approx(0.6667, abs=1e-3)


def test_empty_and_invalid_inputs():
    """6. Empty and invalid inputs handle gracefully."""
    # Completely empty dataset
    empty_eval = evaluate_dataset({})
    assert empty_eval["num_documents"] == 0
    assert empty_eval["macro_f1"] == 0.0
    assert empty_eval["micro_f1"] == 0.0

    # Empty predictions for a document
    doc_eval = evaluate_document("EMPTY_DOC", [], [])
    assert doc_eval["total_gt"] == 0
    assert doc_eval["total_pred"] == 0
    assert doc_eval["micro_f1"] == 0.0

    # Normalization of None / empty strings
    assert normalize_field_value("lab.value", None) == ""
    assert normalize_field_value("lab.flag", "") == ""


def test_ground_truth_loaders():
    """7. Ground truth loaders parse canonical LAB, MED, and NARRATIVE datasets."""
    # Lab ground truth
    lab_gt = load_ground_truth_claims("LAB_0001")
    assert len(lab_gt) > 0
    lab_fields = {c.field_type for c in lab_gt}
    assert "lab.test_name" in lab_fields
    assert "lab.value" in lab_fields
    assert "lab.unit" in lab_fields

    # Med ground truth
    med_gt = load_ground_truth_claims("MED_0001")
    assert len(med_gt) > 0
    med_fields = {c.field_type for c in med_gt}
    assert "med.name" in med_fields

    # Narrative ground truth
    narr_gt = load_ground_truth_claims("NARRATIVE_0001")
    assert len(narr_gt) > 0
    narr_fields = {c.field_type for c in narr_gt}
    assert "lab.value" in narr_fields or "med.name" in narr_fields


def test_run_extraction_evaluation_mock():
    """8. Test the E1-E4 runner on a small document sample using mock providers."""
    router = ExtractionRouter(
        gliner_provider=MockGLiNERProvider(),
        llm_provider=MockLLMProvider(),
    )

    report = run_extraction_evaluation(
        extraction_path="deterministic",
        doc_ids=["LAB_0001", "MED_0001"],
        router=router,
    )

    assert report["experiment_id"] == "E1"
    assert report["num_documents"] == 2
    assert "metrics_per_field" in report
    assert report["total_gt_claims"] > 0
    assert report["total_pred_claims"] > 0
    assert 0.0 <= report["macro_f1"] <= 1.0
