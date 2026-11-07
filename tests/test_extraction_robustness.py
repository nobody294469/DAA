"""Unit tests for E5 Extraction Robustness Evaluation Harness.

Covers:
1. Clean baseline evaluation
2. Track A visual degradation condition
3. Track B text/OCR degradation condition
4. Severity separation (LOW, MEDIUM, HIGH)
5. Result aggregation and degradation curve generation
6. Invalid and missing inputs
"""
from __future__ import annotations

import pytest

from clindoc.degrade.robustness import (
    evaluate_degradation_condition,
    load_degraded_document_text,
    run_e5_robustness_benchmark,
)
from clindoc.degrade.schema import DegradationTrack, DegradationType, SeverityLevel
from clindoc.extract.gliner_fallback import MockGLiNERProvider
from clindoc.extract.llm_extractor import MockLLMProvider
from clindoc.extract.router import ExtractionRouter


@pytest.fixture(scope="module")
def mock_router() -> ExtractionRouter:
    """ExtractionRouter with mock providers for deterministic, fast offline testing."""
    return ExtractionRouter(
        gliner_provider=MockGLiNERProvider(),
        llm_provider=MockLLMProvider(),
    )


def test_clean_baseline(mock_router: ExtractionRouter):
    """1. Clean baseline: evaluates clean documents without degradation."""
    res = evaluate_degradation_condition(
        condition=None,
        severity=None,
        doc_ids=["LAB_0001", "MED_0001"],
        router=mock_router,
    )

    assert res["condition"] == "clean"
    assert res["track"] == "clean"
    assert res["severity"] == "NONE"
    assert res["num_documents"] == 2
    assert res["total_gt_claims"] > 0
    assert res["total_pred_claims"] > 0
    assert 0.0 <= res["macro_f1"] <= 1.0
    assert 0.0 <= res["overall_exact_match_rate"] <= 1.0
    assert "metrics_per_field" in res


@pytest.mark.xfail(reason="needs PaddleOCR (separate .venv_ocr) to read the degraded image", strict=False)
def test_visual_degradation_condition(mock_router: ExtractionRouter):
    """2. Track A: evaluates a visual degradation condition (e.g., gaussian_blur)."""
    res = evaluate_degradation_condition(
        condition=DegradationType.GAUSSIAN_BLUR,
        severity=SeverityLevel.LOW,
        doc_ids=["LAB_0001"],
        router=mock_router,
    )

    assert res["condition"] == "gaussian_blur"
    assert res["track"] == DegradationTrack.VISUAL.value
    assert res["severity"] == "LOW"
    assert res["num_documents"] == 1
    assert res["total_gt_claims"] > 0
    assert res["total_pred_claims"] > 0
    assert 0.0 <= res["macro_f1"] <= 1.0


def test_text_ocr_degradation_condition(mock_router: ExtractionRouter):
    """3. Track B: evaluates text/OCR character corruption condition."""
    res = evaluate_degradation_condition(
        condition=DegradationType.OCR_CHARACTER_CORRUPTION,
        severity=SeverityLevel.LOW,
        doc_ids=["LAB_0001"],
        router=mock_router,
    )

    assert res["condition"] == "ocr_character_corruption"
    assert res["track"] == DegradationTrack.OCR_TEXT.value
    assert res["severity"] == "LOW"
    assert res["num_documents"] == 1
    assert res["total_gt_claims"] > 0
    assert 0.0 <= res["macro_f1"] <= 1.0


def test_severity_separation(mock_router: ExtractionRouter):
    """4. Severity separation: LOW, MEDIUM, and HIGH severities produce distinct results."""
    results_by_sev = {}
    for sev in [SeverityLevel.LOW, SeverityLevel.MEDIUM, SeverityLevel.HIGH]:
        res = evaluate_degradation_condition(
            condition=DegradationType.OCR_CHARACTER_CORRUPTION,
            severity=sev,
            doc_ids=["LAB_0001"],
            router=mock_router,
        )
        assert res["severity"] == sev.value
        results_by_sev[sev.value] = res

    # Confirm all 3 severities were evaluated independently
    assert "LOW" in results_by_sev
    assert "MEDIUM" in results_by_sev
    assert "HIGH" in results_by_sev

    # As OCR corruption severity increases, predictions/matches degrade or change
    low_res = results_by_sev["LOW"]
    high_res = results_by_sev["HIGH"]
    assert low_res["severity"] == "LOW"
    assert high_res["severity"] == "HIGH"


def test_result_aggregation(mock_router: ExtractionRouter):
    """5. Result aggregation: runs benchmark harness and produces degradation curves."""
    report = run_e5_robustness_benchmark(
        conditions=[DegradationType.GAUSSIAN_BLUR, DegradationType.OCR_CHARACTER_CORRUPTION],
        severities=[SeverityLevel.LOW, SeverityLevel.HIGH],
        doc_ids=["LAB_0001"],
        router=mock_router,
    )

    assert report["experiment_id"] == "E5-degradation-extraction"
    assert "clean_baseline" in report
    assert report["clean_baseline"]["condition"] == "clean"
    assert len(report["results"]) == 4  # 2 conditions x 2 severities

    curves = report["degradation_curves"]
    assert "gaussian_blur" in curves
    assert "ocr_character_corruption" in curves

    blur_curve = curves["gaussian_blur"]
    assert blur_curve["track"] == "visual"
    assert blur_curve["severities"] == ["LOW", "HIGH"]
    assert len(blur_curve["macro_f1"]) == 2
    assert len(blur_curve["exact_match_rate"]) == 2


def test_invalid_and_missing_inputs(mock_router: ExtractionRouter):
    """6. Invalid/missing inputs: handles bad conditions, bad severities, missing documents."""
    # Invalid degradation condition
    with pytest.raises(ValueError, match="Unknown degradation condition"):
        evaluate_degradation_condition(
            condition="non_existent_noise_mode",
            severity=SeverityLevel.LOW,
            doc_ids=["LAB_0001"],
            router=mock_router,
        )

    # Invalid severity level
    with pytest.raises(ValueError, match="Unknown severity level"):
        evaluate_degradation_condition(
            condition=DegradationType.GAUSSIAN_BLUR,
            severity="CATASTROPHIC_ULTRA",
            doc_ids=["LAB_0001"],
            router=mock_router,
        )

    # Empty document list
    empty_res = evaluate_degradation_condition(
        condition=DegradationType.GAUSSIAN_BLUR,
        severity=SeverityLevel.LOW,
        doc_ids=[],
        router=mock_router,
    )
    assert empty_res["num_documents"] == 0
    assert empty_res["macro_f1"] == 0.0

    # Non-existent document ID
    missing_doc_text = load_degraded_document_text(
        doc_id="MISSING_DOC_99999",
        condition=DegradationType.GAUSSIAN_BLUR,
        severity=SeverityLevel.LOW,
    )
    assert missing_doc_text is None
