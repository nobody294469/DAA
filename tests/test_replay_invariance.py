"""Test suite for Phase 8.2 Task 4: Stage-B Qualified Configuration Replay Invariance.

Authority:
  - PROJECT_SPEC.md §3, §10, §12, §13, §17.1
  - PRODUCTIZATION_SPEC.md §3.6, §3.8, §8.2 (Phase 8.2 Task 4)
  - PRODUCTIZATION_DECISIONS.md PD-004, PD-005, PD-018, PD-019
  - experiments/FINAL_REPORT_DATA_SHEET.md
  - reports/FINAL_PROJECT_REPORT.md

Validates that:
  1. Frozen diagnostics artifact SHA-256 is strictly verified prior to replay.
  2. Stage B checkpoint SHA-256 is strictly verified prior to replay.
  3. Replay against frozen benchmark records reproduces deterministic predictions
     without numerical drift beyond floating-point tolerance (1e-4).
  4. Missing or corrupted fields are safely classified as NON_REPLAYABLE.
  5. Stage C historical limitation is explicitly documented in all replay reports.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from clindoc.config import ProductionProfileError, RuntimeProfile
from clindoc.manifest import QualifiedManifest
from eval.replay_qualified_stage_b import (
    DEFAULT_NUMERICAL_TOLERANCE,
    FROZEN_DIAGNOSTICS_CANONICAL_SHA256,
    FROZEN_DIAGNOSTICS_DEFAULT_PATH,
    ReplayDecisionStatus,
    execute_replay_harness,
    run_stage_b_replay,
    verify_frozen_artifact_hash,
)


def test_frozen_diagnostics_artifact_integrity():
    """Verify that canonical frozen diagnostics file exists and matches pinned SHA-256."""
    assert FROZEN_DIAGNOSTICS_DEFAULT_PATH.exists(), (
        f"Canonical diagnostics artifact not found at {FROZEN_DIAGNOSTICS_DEFAULT_PATH}"
    )
    is_valid, actual_sha256 = verify_frozen_artifact_hash(FROZEN_DIAGNOSTICS_DEFAULT_PATH)
    assert is_valid, (
        f"Diagnostics SHA-256 mismatch: expected {FROZEN_DIAGNOSTICS_CANONICAL_SHA256}, got {actual_sha256}"
    )


def test_replay_harness_rejects_corrupted_diagnostics():
    """Verify that replay harness fails closed if diagnostics file hash is tampered."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"diagnostics": {"ood": []}}, f)
        temp_path = f.name

    try:
        with pytest.raises(ProductionProfileError, match="SHA-256 mismatch"):
            execute_replay_harness(
                diagnostics_path=temp_path,
                manifest=QualifiedManifest.create_canonical_v1(),
            )
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_replay_harness_executes_on_subset_and_produces_valid_structure():
    """Verify that replay harness executes on a small subset and produces matching predictions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_json = Path(tmpdir) / "replay_test.json"
        result = execute_replay_harness(
            diagnostics_path=FROZEN_DIAGNOSTICS_DEFAULT_PATH,
            manifest=QualifiedManifest.create_canonical_v1(),
            output_json_path=out_json,
            max_records=5,
        )

        assert result["status"] == "PASS"
        assert result["harness_metadata"]["profile"] == RuntimeProfile.PRODUCTION.value
        assert result["artifact_hashes"]["frozen_diagnostics_verified"] is True
        assert result["artifact_hashes"]["manifest_verified"] is True
        assert result["artifact_hashes"]["checkpoint_verified"] is True
        assert "stage_c_historical_limitation" in result
        assert "vLLM" in result["stage_c_historical_limitation"]["rationale"]

        summary = result["summary"]
        assert summary["total_frozen_records"] == 5
        assert summary["replayable_records"] == 5
        assert summary["exact_decision_matches"] == 5
        assert summary["label_mismatches_count"] == 0
        assert summary["escalation_mismatches_count"] == 0
        assert summary["numerical_drift_count"] == 0
        assert summary["passed"] is True
        assert out_json.exists()


def test_non_replayable_handling():
    """Verify that malformed records without claim or evidence text are handled gracefully."""
    class DummyStageB:
        def predict_claim(self, claim, evidence):
            raise RuntimeError("Should not be called for malformed record")

    malformed_records = [
        {"example_id": "malformed_1", "claim_text": ""},  # missing evidence
        {"example_id": "malformed_2", "evidence_text": "evidence without claim"},
    ]

    summary, details = run_stage_b_replay(
        stage_b=DummyStageB(),
        records=malformed_records,
        tolerance=DEFAULT_NUMERICAL_TOLERANCE,
    )

    assert summary.total_frozen_records == 2
    assert summary.replayable_records == 0
    assert summary.non_replayable_records == 2
    assert summary.passed is False  # Non-replayable records cause failure
    assert len(details) == 2
    for d in details:
        assert d.status == ReplayDecisionStatus.NON_REPLAYABLE
        assert len(d.unreplayable_reasons) > 0
