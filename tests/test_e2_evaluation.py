"""Unit and integration tests for Experiment E2 Standalone GLiNER Extraction Evaluation.

Authority: PROJECT_SPEC.md §21.1, §22.1, §24
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from clindoc.extract.contracts import TIER_1_FIELDS, TIER_2_FIELDS, Claim, Evidence
from clindoc.extract.gliner_fallback import (
    GLiNERFallbackExtractor,
    MockGLiNERProvider,
    SubprocessGLiNERProvider,
)
from clindoc.extract.evaluate import (
    ALL_CANONICAL_FIELDS,
    evaluate_dataset,
    evaluate_document,
    load_document_text_for_eval,
    load_ground_truth_claims,
)
import sys

_PROJ_ROOT = Path(__file__).resolve().parents[1]
_E2_DIR = _PROJ_ROOT / "experiments" / "E2-gliner-extraction"
if str(_E2_DIR) not in sys.path:
    sys.path.insert(0, str(_E2_DIR))

from run_e2_evaluation import (
    load_all_benchmark_documents,
    load_split_definitions,
    run_e2_evaluation,
)


def test_e2_benchmark_document_counts():
    """Verify that E2 benchmark loader discovers the canonical 120 documents."""
    docs = load_all_benchmark_documents(_PROJ_ROOT)
    assert len(docs) == 120
    assert len([d for d in docs if d.startswith("LAB_")]) == 50
    assert len([d for d in docs if d.startswith("MED_")]) == 50
    assert len([d for d in docs if d.startswith("NARRATIVE_")]) == 20


def test_e2_split_definitions():
    """Verify that E2 split manifest partitioning yields 86 CV and 34 OOD documents."""
    splits = load_split_definitions(_PROJ_ROOT)
    assert len(splits["cv"]) == 86
    assert len(splits["ood"]) == 34
    assert set(splits["cv"]).isdisjoint(set(splits["ood"]))
    assert len(set(splits["cv"]) | set(splits["ood"])) == 120


def test_e2_mock_evaluation_run(tmp_path: Path):
    """Verify end-to-end execution of E2 runner with mock provider."""
    results = run_e2_evaluation(use_mock=True, batch_size=30, output_dir=tmp_path)
    assert results["experiment_id"] == "E2-gliner-extraction"
    assert results["is_mock"] is True
    assert (tmp_path / "e2_results.json").exists()

    # Check overall metrics structure
    ov = results["overall"]
    assert ov["num_documents"] == 120
    assert ov["total_gt_claims"] == 4456
    assert ov["total_pred_claims"] > 0
    assert 0.0 <= ov["micro_f1"] <= 1.0
    assert 0.0 <= ov["macro_f1"] <= 1.0

    # Ensure all 10 canonical fields are reported
    metrics = ov["metrics_per_field"]
    for ft in ALL_CANONICAL_FIELDS:
        assert ft in metrics, f"Missing canonical field in E2 metrics: {ft}"
        assert 0.0 <= metrics[ft]["precision"] <= 1.0
        assert 0.0 <= metrics[ft]["recall"] <= 1.0
        assert 0.0 <= metrics[ft]["f1"] <= 1.0


def test_e2_results_json_integrity():
    """Verify the persisted real E2 results artifact in experiments/E2-gliner-extraction/."""
    results_path = _PROJ_ROOT / "experiments" / "E2-gliner-extraction" / "e2_results.json"
    assert results_path.exists(), "experiments/E2-gliner-extraction/e2_results.json must exist"

    with open(results_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["experiment_id"] == "E2-gliner-extraction"
    assert data["model_name"] == "Ihor/gliner-biomed-base-v1.0"
    assert data["is_mock"] is False
    assert data["evaluation_scope"]["total_documents"] == 120
    assert data["evaluation_scope"]["lab_documents"] == 50
    assert data["evaluation_scope"]["med_documents"] == 50
    assert data["evaluation_scope"]["narrative_documents"] == 20
    assert data["evaluation_scope"]["cv_documents"] == 86
    assert data["evaluation_scope"]["ood_documents"] == 34

    ov = data["overall"]
    assert ov["num_documents"] == 120
    assert ov["total_gt_claims"] == 4456
    assert ov["total_pred_claims"] == 1763
    assert ov["micro_precision"] == 0.4390
    assert ov["micro_recall"] == 0.1737
    assert ov["micro_f1"] == 0.2489
    assert ov["macro_f1"] == 0.3455

    # Check Tier 1 vs Tier 2 behavior
    metrics = ov["metrics_per_field"]
    for t2_field in TIER_2_FIELDS:
        assert metrics[t2_field]["pred_count"] == 0, f"Tier 2 field {t2_field} must not be emitted by GLiNER"
        assert metrics[t2_field]["f1"] == 0.0

    # Check high medication performance
    assert metrics["med.name"]["f1"] > 0.90
    assert metrics["med.dose"]["f1"] > 0.90


def test_gliner_provider_output_contracts():
    """Test that GLiNER provider outputs conform to canonical Claim and Evidence contracts."""
    provider = MockGLiNERProvider()
    extractor = GLiNERFallbackExtractor(provider=provider)
    doc_text = load_document_text_for_eval("MED_0001", _PROJ_ROOT)
    assert doc_text is not None

    page = doc_text.pages[0]
    evs, claims = extractor.extract(
        document_id="MED_0001",
        page=page,
        region_text=page.get_page_text(),
        region_id="MED_0001::p1::r0",
    )

    assert len(claims) > 0
    assert len(evs) == len(claims)
    for c, e in zip(claims, evs):
        assert isinstance(c, Claim)
        assert isinstance(e, Evidence)
        assert c.extraction_method == "gliner"
        assert c.evidence_id == e.evidence_id
        assert c.field_type in ALL_CANONICAL_FIELDS
        assert e.document_id == "MED_0001"
