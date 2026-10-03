"""Unit and integration tests for Experiment E3 Standalone Narrative Extraction Evaluation.

Authority: PROJECT_SPEC.md §13, §21.1, §22.1, §24
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import pytest

from clindoc.extract.contracts import (
    TIER_1_FIELDS,
    TIER_2_FIELDS,
    Claim,
    Evidence,
)
from clindoc.extract.evaluate import (
    ALL_CANONICAL_FIELDS,
    evaluate_dataset,
    evaluate_document,
    load_document_text_for_eval,
    load_ground_truth_claims,
)
from clindoc.extract.llm_extractor import (
    PERMITTED_NARRATIVE_FIELDS,
    MockLLMProvider,
    NarrativeExtractor,
)

_PROJ_ROOT = Path(__file__).resolve().parents[1]
_E3_DIR = _PROJ_ROOT / "experiments" / "E3-narrative-extraction"
if str(_E3_DIR) not in sys.path:
    sys.path.insert(0, str(_E3_DIR))

from run_e3_evaluation import (
    load_narrative_document_ids,
    load_narrative_splits,
    run_e3_evaluation,
)


def test_e3_narrative_document_counts():
    """Verify that E3 narrative document loader discovers exactly 20 narrative documents."""
    docs = load_narrative_document_ids()
    assert len(docs) == 20
    assert docs[0] == "NARRATIVE_0001"
    assert docs[-1] == "NARRATIVE_0020"


def test_e3_narrative_split_partition():
    """Verify that E3 partitions the 20 narrative documents into 15 CV and 5 OOD."""
    splits = load_narrative_splits(_PROJ_ROOT)
    assert len(splits["cv"]) == 15
    assert len(splits["ood"]) == 5
    assert set(splits["cv"]).isdisjoint(set(splits["ood"]))
    assert len(set(splits["cv"]) | set(splits["ood"])) == 20


def test_e3_runner_execution(tmp_path: Path):
    """Verify end-to-end execution of E3 runner script with MockLLMProvider."""
    results = run_e3_evaluation(provider_type="mock", scope="narrative", output_dir=tmp_path)
    assert results["experiment_id"] == "E3-narrative-extraction"
    assert results["is_mock"] is True
    assert (tmp_path / "e3_results.json").exists()

    nb = results["primary_narrative_benchmark"]
    assert nb["num_documents"] == 20
    assert nb["total_gt_claims"] == 540
    assert nb["total_pred_claims"] > 0
    assert 0.0 <= nb["micro_f1"] <= 1.0
    assert 0.0 <= nb["macro_f1"] <= 1.0

    # Ensure all 10 canonical fields are represented
    metrics = nb["metrics_per_field"]
    for ft in ALL_CANONICAL_FIELDS:
        assert ft in metrics, f"Missing canonical field in E3 metrics: {ft}"
        assert 0.0 <= metrics[ft]["precision"] <= 1.0
        assert 0.0 <= metrics[ft]["recall"] <= 1.0
        assert 0.0 <= metrics[ft]["f1"] <= 1.0


def test_e3_results_json_schema():
    """Verify the persisted real E3 results artifact in experiments/E3-narrative-extraction/."""
    results_path = _PROJ_ROOT / "experiments" / "E3-narrative-extraction" / "e3_results.json"
    assert results_path.exists(), "experiments/E3-narrative-extraction/e3_results.json must exist"

    with open(results_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["experiment_id"] == "E3-narrative-extraction"
    assert data["is_mock"] is True
    assert "target_production_model" in data
    assert data["evaluation_scope"]["narrative_documents"] == 20
    assert data["evaluation_scope"]["narrative_cv_documents"] == 15
    assert data["evaluation_scope"]["narrative_ood_documents"] == 5

    nb = data["primary_narrative_benchmark"]
    assert nb["num_documents"] == 20
    assert nb["total_gt_claims"] == 540
    assert nb["total_pred_claims"] == 115
    assert nb["micro_precision"] == 0.6783
    assert nb["micro_recall"] == 0.1444
    assert nb["micro_f1"] == 0.2382
    assert nb["macro_f1"] == 0.2047

    # Check Tier 2 fields are strictly 0 per DEC-019 / DEC-020
    metrics = nb["metrics_per_field"]
    for t2_field in TIER_2_FIELDS:
        assert metrics[t2_field]["pred_count"] == 0, f"Tier 2 field {t2_field} must not be emitted by NarrativeExtractor"
        assert metrics[t2_field]["f1"] == 0.0

    # Check split breakdown existence
    assert "cross_validation" in nb["by_split"]
    assert "out_of_distribution" in nb["by_split"]
    assert len(nb["per_document"]) == 20


def test_narrative_extractor_contracts():
    """Test that NarrativeExtractor outputs conform to canonical Claim and Evidence contracts."""
    provider = MockLLMProvider()
    extractor = NarrativeExtractor(provider=provider)
    doc_text = load_document_text_for_eval("NARRATIVE_0001", _PROJ_ROOT)
    assert doc_text is not None

    page = doc_text.pages[0]
    evs, claims, diag = extractor.extract(
        document_id="NARRATIVE_0001",
        page=page,
        region_text=page.get_page_text(),
        region_id="NARRATIVE_0001::p1::r0",
    )

    assert len(claims) > 0
    assert len(evs) == len(claims)
    for c, e in zip(claims, evs):
        assert isinstance(c, Claim)
        assert isinstance(e, Evidence)
        assert c.extraction_method == "llm_narrative"
        assert c.evidence_id == e.evidence_id
        assert c.field_type in ALL_CANONICAL_FIELDS
        assert c.field_type in PERMITTED_NARRATIVE_FIELDS
        assert e.document_id == "NARRATIVE_0001"
        # Substring verification test (§7.4)
        assert e.text in page.get_page_text()
