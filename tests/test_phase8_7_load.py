"""Tests for Phase 8.7 Step 6: Synthetic Load Testing Harness and Invariants.

Authority:
  - PROJECT_SPEC §7, §10, §12, §16, §21
  - Phase 8.7 Step 6: Synthetic Load Testing + Capacity/Sizing Report
"""
from __future__ import annotations

import time
import uuid
import pytest

from benchmark.load_harness import (
    DeterministicLoadJudgeClient,
    DeterministicLoadStageB,
    LoadScenarioRunner,
    SyntheticWorkloadGenerator,
)
from clindoc.extract.contracts import TIER_1_FIELDS, TIER_2_FIELDS
from clindoc.services.circuit_breaker import ConcurrencyLimiter


class TestSyntheticWorkloadGenerator:
    """Verifies synthetic workload specification: ~54 claims/doc, ~11 escalated."""

    def test_workload_shape_and_tier_distribution(self):
        doc_id = str(uuid.uuid4())
        ext_out, doc_text, claims = SyntheticWorkloadGenerator.generate_synthetic_document_data(doc_id)

        assert len(claims) == 54, f"Expected exactly 54 claims, got {len(claims)}"
        assert len(ext_out.all_evidence) == 54
        assert len(doc_text.pages) == 1
        assert len(doc_text.pages[0].tokens) == 54

        # Verify Tier 2 distribution (Stage A deterministic only)
        tier_2_claims = [c for c in claims if c.field_type in TIER_2_FIELDS]
        assert len(tier_2_claims) == 20

        # Verify Tier 1 distribution (Stage B + selective Stage C)
        tier_1_claims = [c for c in claims if c.field_type in TIER_1_FIELDS]
        assert len(tier_1_claims) == 34

        # Group 3 is specifically designated for Stage C escalation
        escalated_claims = [c for c in claims if "t1-esc" in (c.group_id or "")]
        assert len(escalated_claims) == 11

        # Autonomous Stage B
        auto_claims = [c for c in claims if "t1-auto" in (c.group_id or "")]
        assert len(auto_claims) == 23

    def test_reproducibility_of_synthetic_generation(self):
        doc_id = "test-doc-reproducible-uuid"
        ext_out1, _, claims1 = SyntheticWorkloadGenerator.generate_synthetic_document_data(doc_id)
        ext_out2, _, claims2 = SyntheticWorkloadGenerator.generate_synthetic_document_data(doc_id)

        assert len(claims1) == len(claims2) == 54
        for c1, c2 in zip(claims1, claims2):
            assert c1.field_type == c2.field_type
            assert c1.value == c2.value
            assert c1.group_id == c2.group_id
            assert c1.extraction_confidence == c2.extraction_confidence


class TestDeterministicDoubles:
    """Verifies behavior of component doubles used for local orchestration load tests."""

    def test_stage_b_escalation_classification(self):
        stage_b = DeterministicLoadStageB()
        doc_id = "doc-double-test"
        _, _, claims = SyntheticWorkloadGenerator.generate_synthetic_document_data(doc_id)

        auto_claims = [c for c in claims if "t1-auto" in (c.group_id or "")]
        esc_claims = [c for c in claims if "t1-esc" in (c.group_id or "")]

        for c in auto_claims:
            res = stage_b.verify(claim=c, evidence=None, stage_a_label=None, context_text=None)
            assert res.escalated is False
            assert res.calibrated_confidence >= 0.90
            assert res.label == "SUPPORTED"

        for c in esc_claims:
            res = stage_b.verify(claim=c, evidence=None, stage_a_label=None, context_text=None)
            assert res.escalated is True
            assert res.calibrated_confidence == 0.52
            assert res.label == "DISCREPANCY"

    def test_judge_client_concurrency_tracking(self):
        limiter = ConcurrencyLimiter(max_concurrency=4)
        client = DeterministicLoadJudgeClient(
            concurrency_limiter=limiter,
            simulated_latency_seconds=0.005,
        )

        assert client.max_observed_concurrency == 0
        prompt = "Synthetic Prompt"
        res_str = client.generate(prompt=prompt, schema=dict)
        assert "SUPPORTED" in res_str
        assert client.max_observed_concurrency == 1
        assert limiter.active_count == 0  # No permit leak


class TestScenarioExecutions:
    """Verifies end-to-end execution of load scenarios and architectural invariants."""

    def test_scenario_a_baseline_terminal_state(self):
        runner = LoadScenarioRunner(simulated_llm_latency=0.001, model_concurrency_cap=5)
        res = runner.process_single_synthetic_document(doc_idx=1)

        assert res["total_claims"] == 54
        assert res["stage_a_claims"] == 20
        assert res["stage_b_claims"] == 23
        assert res["escalated_claims"] == 11
        assert res["elapsed_seconds"] > 0
        assert res["per_claim_ms"] > 0

    def test_scenario_b_concurrency_and_no_deadlocks(self):
        runner = LoadScenarioRunner(simulated_llm_latency=0.001, model_concurrency_cap=5)
        # Fast test with concurrency 1 and 2
        results = runner.run_scenario_b(concurrency_levels=(1, 2))

        assert len(results) == 2
        for r in results:
            assert r["errors_count"] == 0
            assert r["docs_per_second"] > 0
            assert r["claims_per_second"] > 0
            assert r["p50_seconds"] > 0
            assert r["p95_seconds"] >= r["p50_seconds"]

    def test_scenario_c_concurrency_cap_and_no_permit_leaks(self):
        runner = LoadScenarioRunner(simulated_llm_latency=0.005, model_concurrency_cap=3)
        res = runner.run_scenario_c(total_requests=15, max_concurrency=3)

        assert res["scenario"] == "C_CONCURRENCY_CAP"
        assert res["configured_cap"] == 3
        assert res["peak_observed_concurrency"] <= 3
        assert res["cap_respected"] is True
        assert res["completed_requests"] == 15
        assert res["failed_requests"] == 0
        assert res["final_active_count"] == 0
        assert res["permit_leaks"] is False
