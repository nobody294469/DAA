"""Phase 8.7 Step 6: Synthetic Load-Testing Harness.

Authority:
  - PRODUCTIZATION_SPEC.md §3.5, §10, §13
  - PRODUCTIZATION_DECISIONS.md Phase 8.7 Step 6

Provides a deterministic synthetic load testing harness:
  - Generates synthetic clinical document workloads (54 claims/doc, 11 escalations).
  - Executes Scenarios A (Baseline), B (Concurrent Documents: 1, 2, 4, 8), and C (Concurrency Cap).
  - Collects throughput, latency (p50, p95, mean, max), concurrency, and reliability metrics.
  - Fail-closed: No real PHI used; all workload is strictly synthetic.
  - Transparent boundary: Clear separation between measured orchestration latency and external GPU inference.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import math
import os
from pathlib import Path
import platform
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from clindoc.db import (
    Base,
    Claim,
    ClaimReviewState,
    Document,
    DocumentStatus,
    JobStatus,
    ProcessingJob,
    User,
    VerificationResult as DBVerificationResult,
    VerificationStage as DBVerificationStage,
)
from clindoc.extract.contracts import (
    BBoxModel,
    Claim as ContractClaim,
    DocumentExtractionOutput,
    Evidence as ContractEvidence,
    ExtractionResult,
    RoutingDecision as ContractRoutingDecision,
)
from clindoc.jobs.queue import JobQueue
from clindoc.jobs.worker import DocumentProcessingWorker
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict
from clindoc.services.circuit_breaker import (
    ConcurrencyLimiter,
    CircuitBreaker,
)
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.stage_a import DeterministicVerifier
from clindoc.verify.stage_b import StageBResult
from clindoc.verify.stage_c import (
    StageCJudge,
    StageCJudgeOutput,
    StageCResult,
)

logger = logging.getLogger(__name__)


# ─── 1. SYNTHETIC WORKLOAD GENERATOR ──────────────────────────────────────────

class SyntheticWorkloadGenerator:
    """Generates deterministic synthetic clinical documents matching Phase 8.7 specs:
      - Total claims: 54 per document
      - Escalated claims: 11 per document (Tier 1 claims triggering Stage C)
      - Autonomous Stage B claims: 23 per document (Tier 1 claims resolved autonomously)
      - Autonomous Stage A claims: 20 per document (Tier 2 deterministic claims)
    """

    @staticmethod
    def generate_synthetic_document_data(
        document_id: str,
    ) -> Tuple[DocumentExtractionOutput, DocumentText, List[ContractClaim]]:
        claims: List[ContractClaim] = []
        evidences: List[ContractEvidence] = []
        tokens: List[Token] = []

        line_idx = 1
        y_pos = 20

        # Group 1: 20 Tier 2 Claims (resolved autonomously at Stage A)
        tier2_configs = [
            ("lab.unit", "mg/dL", 6),
            ("lab.reference_range", "70-99", 6),
            ("lab.test_name", "Serum Glucose", 5),
            ("med.duration", "30 days", 3),
        ]
        claim_counter = 1
        for field_type, val_str, count in tier2_configs:
            for _ in range(count):
                cid = f"clm-t2-{document_id}-{claim_counter}"
                eid = f"ev-t2-{document_id}-{claim_counter}"
                claims.append(
                    ContractClaim(
                        claim_id=cid,
                        document_id=document_id,
                        field_type=field_type,
                        value=val_str,
                        extraction_method="deterministic",
                        region_id=f"reg-{document_id}-1",
                        evidence_id=eid,
                        extraction_confidence=0.98,
                        group_id=f"grp-{document_id}-t2",
                    )
                )
                evidences.append(
                    ContractEvidence(
                        evidence_id=eid,
                        document_id=document_id,
                        page_number=1,
                        char_start=0,
                        char_end=len(val_str),
                        text=val_str,
                        bbox=BBoxModel(x0=10, y0=y_pos, x1=100, y1=y_pos + 15),
                    )
                )
                tokens.append(
                    Token(
                        text=val_str,
                        bbox=BBoxDict(x0=10, y0=y_pos, x1=100, y1=y_pos + 15),
                        line_id=f"L{line_idx}",
                    )
                )
                claim_counter += 1
                line_idx += 1
                y_pos += 20

        # Group 2: 23 Tier 1 Autonomous Claims (resolved autonomously at Stage B)
        tier1_auto_configs = [
            ("lab.value", "95", 95.0, "mg/dL", 12),
            ("med.name", "Metformin", None, None, 4),
            ("med.dose", "500 mg", None, None, 4),
            ("med.frequency", "BID", None, None, 3),
        ]
        for field_type, val_str, num_val, unit_val, count in tier1_auto_configs:
            for _ in range(count):
                cid = f"clm-t1-auto-{document_id}-{claim_counter}"
                eid = f"ev-t1-auto-{document_id}-{claim_counter}"
                claims.append(
                    ContractClaim(
                        claim_id=cid,
                        document_id=document_id,
                        field_type=field_type,
                        value=val_str,
                        value_numeric=num_val,
                        unit_raw=unit_val,
                        extraction_method="deterministic",
                        region_id=f"reg-{document_id}-1",
                        evidence_id=eid,
                        extraction_confidence=0.92,
                        group_id=f"grp-{document_id}-t1-auto",
                    )
                )
                evidences.append(
                    ContractEvidence(
                        evidence_id=eid,
                        document_id=document_id,
                        page_number=1,
                        char_start=0,
                        char_end=len(val_str),
                        text=val_str,
                        bbox=BBoxModel(x0=10, y0=y_pos, x1=120, y1=y_pos + 15),
                    )
                )
                tokens.append(
                    Token(
                        text=val_str,
                        bbox=BBoxDict(x0=10, y0=y_pos, x1=120, y1=y_pos + 15),
                        line_id=f"L{line_idx}",
                    )
                )
                claim_counter += 1
                line_idx += 1
                y_pos += 20

        # Group 3: 11 Tier 1 Escalated Claims (escalated to Stage C / Qwen)
        tier1_esc_configs = [
            ("lab.value", "185", 185.0, "mg/dL", 6),
            ("lab.flag", "HIGH", None, None, 3),
            ("med.name", "Atorvastatin", None, None, 2),
        ]
        for field_type, val_str, num_val, unit_val, count in tier1_esc_configs:
            for _ in range(count):
                cid = f"clm-t1-esc-{document_id}-{claim_counter}"
                eid = f"ev-t1-esc-{document_id}-{claim_counter}"
                claims.append(
                    ContractClaim(
                        claim_id=cid,
                        document_id=document_id,
                        field_type=field_type,
                        value=val_str,
                        value_numeric=num_val,
                        unit_raw=unit_val,
                        extraction_method="deterministic",
                        region_id=f"reg-{document_id}-1",
                        evidence_id=eid,
                        extraction_confidence=0.88,
                        group_id=f"grp-{document_id}-t1-esc",
                    )
                )
                evidences.append(
                    ContractEvidence(
                        evidence_id=eid,
                        document_id=document_id,
                        page_number=1,
                        char_start=0,
                        char_end=len(val_str),
                        text=val_str,
                        bbox=BBoxModel(x0=10, y0=y_pos, x1=120, y1=y_pos + 15),
                    )
                )
                tokens.append(
                    Token(
                        text=val_str,
                        bbox=BBoxDict(x0=10, y0=y_pos, x1=120, y1=y_pos + 15),
                        line_id=f"L{line_idx}",
                    )
                )
                claim_counter += 1
                line_idx += 1
                y_pos += 20

        assert len(claims) == 54, f"Expected exactly 54 claims, got {len(claims)}"

        doc_text = DocumentText(
            document_id=document_id,
            source_type="born_digital",
            pages=[
                DocumentPage(
                    page_number=1,
                    width_px=612,
                    height_px=1000,
                    tokens=tokens,
                )
            ],
        )

        extraction_output = DocumentExtractionOutput(
            document_id=document_id,
            regions=[
                ExtractionResult(
                    routing_decision=ContractRoutingDecision(
                        document_id=document_id,
                        region_id=f"reg-{document_id}-1",
                        route="deterministic",
                        reason="synthetic_lab_table",
                        confidence=0.95,
                    ),
                    evidence=evidences,
                    claims=claims,
                )
            ],
        )

        return extraction_output, doc_text, claims


# ─── 2. DETERMINISTIC COMPONENT DOUBLES FOR BENCHMARK ─────────────────────────

class DeterministicLoadStageB:
    """Deterministic Stage B classifier for load harness:
      - 20 Tier 2 claims are handled prior to Stage B by Stage A.
      - 23 Tier 1 claims receive calibrated_confidence = 0.95 (autonomous resolution).
      - 11 Tier 1 claims receive calibrated_confidence = 0.52 (escalation to Stage C).
    """
    is_model_loaded = True

    def verify(self, claim: Any, evidence: Any, stage_a_label: Optional[str], context_text: Optional[str]) -> StageBResult:
        gid = str(getattr(claim, "group_id", "") or "")
        cid = str(getattr(claim, "claim_id", "") or "")
        method = str(getattr(claim, "extraction_method", "") or "")
        if "t1-esc" in gid or "t1-esc" in cid or method == "llm_narrative":
            # Designated for escalation
            return StageBResult(
                label="DISCREPANCY",
                raw_confidence=0.52,
                calibrated_confidence=0.52,
                logits=[0.0, 0.0, 0.0],
                escalated=True,
                escalation_reason="Low calibrated confidence below autonomous threshold",
            )
        # Autonomous Stage B
        return StageBResult(
            label="SUPPORTED",
            raw_confidence=0.95,
            calibrated_confidence=0.95,
            logits=[0.0, 0.0, 0.0],
            escalated=False,
            escalation_reason=None,
        )


class DeterministicLoadJudgeClient:
    """Thread-safe Stage C judge client double:
      - Enforces ConcurrencyLimiter and CircuitBreaker invariants.
      - Measures peak active concurrency in flight.
      - Returns schema-conforming JSON string.
    """
    def __init__(
        self,
        simulated_latency_seconds: float = 0.002,
        concurrency_limiter: Optional[ConcurrencyLimiter] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.simulated_latency = float(simulated_latency_seconds)
        self.concurrency_limiter = concurrency_limiter
        self.circuit_breaker = circuit_breaker
        self.call_count = 0
        self.max_observed_concurrency = 0
        self._current_concurrency = 0
        self._lock = threading.Lock()

    def generate(self, prompt: str, schema: type[Any]) -> str:
        from contextlib import nullcontext
        cm = self.concurrency_limiter if self.concurrency_limiter is not None else nullcontext()
        cb = self.circuit_breaker if self.circuit_breaker is not None else nullcontext()

        with cm:
            with cb:
                with self._lock:
                    self._current_concurrency += 1
                    if self._current_concurrency > self.max_observed_concurrency:
                        self.max_observed_concurrency = self._current_concurrency
                    self.call_count += 1

                try:
                    if self.simulated_latency > 0:
                        time.sleep(self.simulated_latency)
                    return json.dumps({
                        "label": "SUPPORTED",
                        "confidence": 0.96,
                        "contradiction_type": None,
                        "rationale": "Synthetic load test: claim is verified against source evidence.",
                    })
                finally:
                    with self._lock:
                        self._current_concurrency -= 1


# ─── 3. METRICS COLLECTOR & STATISTICS ────────────────────────────────────────

def compute_percentiles(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0, "min": 0.0}
    sorted_v = sorted(values)
    n = len(sorted_v)
    mean_val = sum(sorted_v) / n
    p50_idx = int(0.50 * (n - 1))
    p95_idx = int(0.95 * (n - 1))
    return {
        "mean": mean_val,
        "p50": sorted_v[p50_idx],
        "p95": sorted_v[p95_idx],
        "max": sorted_v[-1],
        "min": sorted_v[0],
    }


# ─── 4. LOAD SCENARIO RUNNER ──────────────────────────────────────────────────

class LoadScenarioRunner:
    """Orchestrates Scenarios A, B, and C."""

    def __init__(
        self,
        simulated_llm_latency: float = 0.002,
        model_concurrency_cap: int = 10,
    ):
        self.simulated_llm_latency = simulated_llm_latency
        self.model_concurrency_cap = model_concurrency_cap
        self.concurrency_limiter = ConcurrencyLimiter(
            max_concurrency=model_concurrency_cap,
            timeout=30.0,
        )
        self.circuit_breaker = CircuitBreaker(
            name="load_test_cb",
            failure_threshold=5,
            cooldown_seconds=30.0,
        )
        self.judge_client = DeterministicLoadJudgeClient(
            simulated_latency_seconds=self.simulated_llm_latency,
            concurrency_limiter=self.concurrency_limiter,
            circuit_breaker=self.circuit_breaker,
        )

    def _create_cascade(self) -> VerificationCascade:
        cascade = VerificationCascade()
        cascade.stage_b = DeterministicLoadStageB()
        cascade.stage_c = StageCJudge(
            client=self.judge_client,
            max_attempts=3,
        )
        return cascade

    def _create_isolated_db(self) -> Tuple[Any, sessionmaker, uuid.UUID]:
        from sqlalchemy.pool import StaticPool
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        session = session_factory()
        user = User(
            idp_subject="sub_load_tester",
            username="load_runner",
            full_name="Load Harness Runner",
            email="load@hospital.org",
            is_active=True,
        )
        session.add(user)
        session.commit()
        user_id = user.id
        session.close()
        return engine, session_factory, user_id

    def process_single_synthetic_document(
        self,
        doc_idx: int,
    ) -> Dict[str, Any]:
        """Process one synthetic document end-to-end through the production worker."""
        engine, session_factory, user_id = self._create_isolated_db()
        session = session_factory()

        doc_uuid = uuid.uuid4()
        document_id = str(doc_uuid)

        # Temporary scratch file for document storage_ref
        import tempfile
        dummy_file = Path(tempfile.gettempdir()) / f"clindoc_syn_{doc_uuid.hex[:8]}.pdf"
        dummy_file.write_bytes(b"%PDF-1.4 Synthetic Clinical Record")

        # Generate synthetic workload (54 claims, 11 escalations)
        extraction_output, doc_text, raw_claims = (
            SyntheticWorkloadGenerator.generate_synthetic_document_data(document_id)
        )

        doc = Document(
            id=doc_uuid,
            original_filename=f"synthetic_doc_{doc_idx}.pdf",
            mime_type="application/pdf",
            file_size_bytes=len(dummy_file.read_bytes()),
            sha256="0" * 64,
            storage_ref=f"file://{dummy_file.resolve()}",
            status=DocumentStatus.INGESTED,
            uploaded_by=user_id,
            patient_context_id=f"PT-SYN-{doc_idx}",
        )
        session.add(doc)
        session.flush()

        queue = JobQueue()
        job = queue.enqueue(session=session, document_id=doc.id)
        claimed_job = queue.claim_next(session=session, worker_id=f"worker-load-{doc_idx}")
        assert claimed_job is not None

        cascade = self._create_cascade()
        worker = DocumentProcessingWorker(
            queue=queue,
            cascade=cascade,
            worker_id=f"worker-load-{doc_idx}",
            ingest_fn=lambda path, **kwargs: doc_text,
        )
        worker.router.extract_document = lambda *args, **kwargs: extraction_output

        t0 = time.monotonic()
        t_cpu_0 = time.process_time()

        processed_job = worker.process_job(session=session, job=claimed_job)

        elapsed = time.monotonic() - t0
        cpu_elapsed = time.process_time() - t_cpu_0

        # Verification checks
        assert processed_job.status == JobStatus.COMPLETED
        session.refresh(doc)
        assert doc.status == DocumentStatus.PENDING_REVIEW

        db_claims = session.query(Claim).filter(Claim.document_id == doc.id).all()
        assert len(db_claims) == 54

        db_vrs = session.query(DBVerificationResult).all()
        assert len(db_vrs) == 54

        stage_a_count = sum(1 for v in db_vrs if v.verification_stage == DBVerificationStage.STAGE_A)
        stage_b_count = sum(1 for v in db_vrs if v.verification_stage == DBVerificationStage.STAGE_B)
        stage_c_count = sum(1 for v in db_vrs if v.verification_stage == DBVerificationStage.STAGE_C)

        assert stage_a_count == 20
        assert stage_b_count == 23
        assert stage_c_count == 11

        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
        if dummy_file.exists():
            try:
                dummy_file.unlink()
            except Exception:
                pass

        return {
            "document_id": document_id,
            "elapsed_seconds": elapsed,
            "cpu_seconds": cpu_elapsed,
            "total_claims": 54,
            "escalated_claims": stage_c_count,
            "stage_a_claims": stage_a_count,
            "stage_b_claims": stage_b_count,
            "per_claim_ms": (elapsed / 54.0) * 1000.0,
        }

    # --------------------------------------------------------------------------
    # Scenario A: Baseline (Single Document)
    # --------------------------------------------------------------------------
    def run_scenario_a(self) -> Dict[str, Any]:
        """Execute baseline single-document test."""
        res = self.process_single_synthetic_document(doc_idx=1)
        return {
            "scenario": "A_BASELINE",
            "concurrency": 1,
            "documents_tested": 1,
            "elapsed_seconds": res["elapsed_seconds"],
            "docs_per_second": 1.0 / res["elapsed_seconds"],
            "claims_per_second": 54.0 / res["elapsed_seconds"],
            "per_claim_ms": res["per_claim_ms"],
            "escalated_claims": res["escalated_claims"],
            "max_observed_concurrency": self.judge_client.max_observed_concurrency,
        }

    # --------------------------------------------------------------------------
    # Scenario B: Concurrent Documents (1, 2, 4, 8)
    # --------------------------------------------------------------------------
    def run_scenario_b(
        self,
        concurrency_levels: Tuple[int, ...] = (1, 2, 4, 8),
    ) -> List[Dict[str, Any]]:
        """Execute multi-document concurrent processing across specified concurrency levels."""
        results = []

        for concurrency in concurrency_levels:
            num_docs = concurrency
            t0 = time.monotonic()
            latencies: List[float] = []
            errors: List[str] = []

            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [
                    executor.submit(self.process_single_synthetic_document, doc_idx=i + 1)
                    for i in range(num_docs)
                ]
                for fut in concurrent.futures.as_completed(futures):
                    try:
                        doc_res = fut.result()
                        latencies.append(doc_res["elapsed_seconds"])
                    except Exception as e:
                        errors.append(str(e))

            total_elapsed = time.monotonic() - t0
            stats = compute_percentiles(latencies)
            total_claims = num_docs * 54

            results.append({
                "concurrency": concurrency,
                "documents": num_docs,
                "total_claims": total_claims,
                "total_elapsed_seconds": total_elapsed,
                "docs_per_second": num_docs / total_elapsed if total_elapsed > 0 else 0.0,
                "claims_per_second": total_claims / total_elapsed if total_elapsed > 0 else 0.0,
                "p50_seconds": stats["p50"],
                "p95_seconds": stats["p95"],
                "mean_seconds": stats["mean"],
                "max_seconds": stats["max"],
                "errors_count": len(errors),
                "peak_in_flight_llm": self.judge_client.max_observed_concurrency,
            })

        return results

    # --------------------------------------------------------------------------
    # Scenario C: Concurrency-Cap Verification
    # --------------------------------------------------------------------------
    def run_scenario_c(
        self,
        total_requests: int = 24,
        max_concurrency: int = 5,
        hold_time_seconds: float = 0.01,
    ) -> Dict[str, Any]:
        """Verify that ConcurrencyLimiter strictly bounds active concurrency and leaks zero permits."""
        limiter = ConcurrencyLimiter(max_concurrency=max_concurrency, timeout=30.0)

        lock = threading.Lock()
        active_in_flight = 0
        peak_observed = 0
        completed = 0
        timed_out = 0
        exceptions = []

        def worker_task(idx: int):
            nonlocal active_in_flight, peak_observed, completed, timed_out
            try:
                with limiter:
                    with lock:
                        active_in_flight += 1
                        if active_in_flight > peak_observed:
                            peak_observed = active_in_flight

                    time.sleep(hold_time_seconds)

                    with lock:
                        active_in_flight -= 1
                        completed += 1
            except Exception as exc:
                with lock:
                    exceptions.append(str(exc))

        t0 = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=total_requests) as executor:
            futures = [executor.submit(worker_task, i) for i in range(total_requests)]
            concurrent.futures.wait(futures)
        elapsed = time.monotonic() - t0

        return {
            "scenario": "C_CONCURRENCY_CAP",
            "total_requests": total_requests,
            "configured_cap": max_concurrency,
            "peak_observed_concurrency": peak_observed,
            "cap_respected": peak_observed <= max_concurrency,
            "completed_requests": completed,
            "failed_requests": len(exceptions),
            "final_active_count": limiter.active_count,
            "permit_leaks": limiter.active_count != 0,
            "elapsed_seconds": elapsed,
        }


# ─── 5. FULL BENCHMARK SUITE EXECUTOR ─────────────────────────────────────────

def run_full_benchmark_suite() -> Dict[str, Any]:
    """Execute complete Step 6 synthetic load benchmark and return structured results."""
    runner = LoadScenarioRunner(
        simulated_llm_latency=0.002,
        model_concurrency_cap=10,
    )

    t_start = time.time()
    scenario_a = runner.run_scenario_a()
    scenario_b = runner.run_scenario_b(concurrency_levels=(1, 2, 4, 8))
    scenario_c = runner.run_scenario_c(total_requests=24, max_concurrency=5)
    t_end = time.time()

    env_info = {
        "os": platform.platform(),
        "python_version": sys.version.split()[0],
        "cpu_count": os.cpu_count() or 1,
        "processor": platform.processor(),
        "real_gpu_available": False,
        "real_vllm_available": False,
        "model_double_used": "DeterministicLoadJudgeClient (0.002s sim latency)",
        "configured_concurrency_limit": 10,
    }

    return {
        "timestamp_unix": t_start,
        "total_suite_runtime_seconds": t_end - t_start,
        "environment": env_info,
        "scenario_a_baseline": scenario_a,
        "scenario_b_concurrency": scenario_b,
        "scenario_c_concurrency_cap": scenario_c,
    }


if __name__ == "__main__":
    print("Running Phase 8.7 Step 6 Synthetic Load Benchmark...")
    report_data = run_full_benchmark_suite()
    print(json.dumps(report_data, indent=2))
