"""Phase 8.7 Step 5: Automated Outage Drills (F-05 / F-06 / F-07).

Authority:
  - PROJECT_SPEC.md §3, §10, §12, §13.3
  - PRODUCTIZATION_SPEC.md §3.5, §7.2, §10.4, §13 (Failures F-05, F-06, F-07)
  - PRODUCTIZATION_DECISIONS.md Phase 8.7 Step 5

Simulates model failure modes using controlled dependency-boundary failure doubles:
  1. F-05: ModernBERT Unavailable (refuse job, preserve queue/attempts, fail-closed, no mock fallback)
  2. F-06: Qwen/vLLM Unavailable (connection error, timeout, circuit breaker OPEN, AWAITING_LLM review routing,
           preservation of Stage A/B, narrative marker, health API independence)
  3. F-07: Qwen Malformed / Schema-Invalid Output (JSON decode error, invariant violations, bounded retries,
           no output coercion/repair, llm_schema_invalid, human review routing)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from clindoc.config import (
    ProductionPipelineConfig,
    ProductionProfileError,
    RuntimeProfile,
)
from clindoc.db import (
    Base,
    Claim,
    ClaimReviewState,
    Document,
    DocumentStatus,
    JobStageEvent,
    JobStatus,
    ProcessingJob,
    User,
    VerificationLabel as DBVerificationLabel,
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
from clindoc.extract.router import ExtractionRouter
from clindoc.jobs.queue import JobQueue
from clindoc.jobs.worker import (
    DocumentProcessingWorker,
    ModernBERTUnavailableError,
)
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict
from clindoc.review.triage import ClaimTriageInput, TriageEngine, TriageCategory, TriageRiskTier
from clindoc.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    FALLBACK_REASON_LLM_SCHEMA_INVALID,
    FALLBACK_REASON_LLM_UNAVAILABLE,
)
from clindoc.services.model_health import (
    ModelHealthRegistry,
    ModernBERTStatus,
    probe_modernbert,
)
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.stage_b import LearnedVerifier, MockModelPredictor, StageBResult
from clindoc.verify.stage_c import (
    StageCJudge,
    StageCJudgeOutput,
    StageCResult,
)


# ─── DATABASE FIXTURES ────────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    """Isolated in-memory SQLite session with foreign key constraints enabled."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def test_user(db_session: Session) -> User:
    user = User(
        idp_subject="sub_outage_drill_user",
        username="outage_tester",
        full_name="Outage Drill Tester",
        email="outage@hospital.org",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def test_document(db_session: Session, test_user: User, tmp_path: Path) -> Document:
    fake_pdf = tmp_path / "clinical_record.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 Clinical Document Outage Drill Content")
    doc = Document(
        original_filename="clinical_record.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(fake_pdf.read_bytes()),
        sha256="abcdef0123456789" * 4,
        storage_ref=f"file://{fake_pdf.resolve()}",
        status=DocumentStatus.INGESTED,
        uploaded_by=test_user.id,
        patient_context_id="PT-DRILL-1001",
    )
    db_session.add(doc)
    db_session.flush()
    return doc


# ─── OCR & CONTRACT HELPERS ───────────────────────────────────────────────────

def create_sample_ocr_text(document_id: str) -> DocumentText:
    return DocumentText(
        document_id=document_id,
        source_type="born_digital",
        pages=[
            DocumentPage(
                page_number=1,
                width_px=612,
                height_px=792,
                tokens=[
                    Token(text="Serum", bbox=BBoxDict(x0=10, y0=10, x1=50, y1=20), line_id="L1"),
                    Token(text="Glucose", bbox=BBoxDict(x0=55, y0=10, x1=100, y1=20), line_id="L1"),
                    Token(text="140", bbox=BBoxDict(x0=105, y0=10, x1=130, y1=20), line_id="L1"),
                    Token(text="mg/dL", bbox=BBoxDict(x0=135, y0=10, x1=170, y1=20), line_id="L1"),
                ],
            )
        ],
    )


def create_dual_claims(document_id: str):
    """Returns (tier2_claim, tier1_claim, ev_unit, ev_val)."""
    tier2 = ContractClaim(
        claim_id=str(uuid.uuid4()),
        document_id=document_id,
        field_type="lab.unit",
        value="mg/dL",
        extraction_method="deterministic",
        region_id="reg-drill-01",
        evidence_id="ev-unit-drill",
        extraction_confidence=0.95,
        group_id="lab-group-1",
    )
    tier1 = ContractClaim(
        claim_id=str(uuid.uuid4()),
        document_id=document_id,
        field_type="lab.value",
        value="140",
        value_numeric=140.0,
        unit_raw="mg/dL",
        extraction_method="deterministic",
        region_id="reg-drill-01",
        evidence_id="ev-val-drill",
        extraction_confidence=0.90,
        group_id="lab-group-1",
    )
    ev_unit = ContractEvidence(
        evidence_id="ev-unit-drill",
        document_id=document_id,
        page_number=1,
        char_start=14,
        char_end=19,
        text="mg/dL",
        bbox=BBoxModel(x0=135, y0=10, x1=170, y1=20),
    )
    ev_val = ContractEvidence(
        evidence_id="ev-val-drill",
        document_id=document_id,
        page_number=1,
        char_start=6,
        char_end=13,
        text="140 mg/dL",
        bbox=BBoxModel(x0=105, y0=10, x1=170, y1=20),
    )
    return tier2, tier1, ev_unit, ev_val


# ==============================================================================
# F-05 OUTAGE DRILLS: MODERNBERT UNAVAILABLE
# ==============================================================================

class BrokenStageBModel:
    """Dependency failure double simulating unavailable/unloadable ModernBERT."""
    is_model_loaded = False

    def verify_readiness(self):
        raise ModernBERTUnavailableError("Model file missing or corrupted in weights registry")

    def load_model(self):
        raise ModernBERTUnavailableError("Model file missing or corrupted in weights registry")


def test_f05_drill_worker_refusal_queue_preservation_and_no_retry_penalty(
    db_session: Session, test_document: Document
):
    """F-05 Drill 1: When ModernBERT readiness fails:
      1. Worker refuses to process the job.
      2. Job remains PENDING/QUEUED (worker_id and lease cleared).
      3. Document returns to INGESTED state.
      4. error_code == 'MODERNBERT_UNAVAILABLE'.
      5. Retry budget is NOT consumed (job.attempts preserved).
      6. No uncaught exception escapes worker.process_job.
      7. No claims or clinical verdicts are fabricated or saved.
    """
    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=test_document.id)
    initial_attempts = job.attempts
    assert initial_attempts == 0

    # Claim the job for execution
    claimed_job = queue.claim_next(session=db_session, worker_id="drill-worker-f05")
    assert claimed_job is not None
    assert claimed_job.status == JobStatus.RUNNING

    # Setup worker with broken Stage B
    cascade = VerificationCascade()
    cascade.stage_b = BrokenStageBModel()

    worker = DocumentProcessingWorker(
        queue=queue,
        cascade=cascade,
        worker_id="drill-worker-f05",
    )

    # Execute drill - must NOT crash or raise
    returned_job = worker.process_job(session=db_session, job=claimed_job)

    # 1 & 2: Job remains QUEUED (PENDING) with cleared worker assignment
    assert returned_job.status == JobStatus.PENDING
    assert returned_job.worker_id is None
    assert returned_job.lease_expires_at is None
    assert returned_job.current_stage == "QUEUED"

    # 3: Document reverted to INGESTED
    db_session.refresh(test_document)
    assert test_document.status == DocumentStatus.INGESTED

    # 4: Error code is exactly MODERNBERT_UNAVAILABLE
    assert returned_job.error_code == "MODERNBERT_UNAVAILABLE"
    assert "ModernBERT" in returned_job.error_message

    # 5: Retry budget is NOT consumed by readiness failure
    assert returned_job.attempts == initial_attempts

    # 7: Stage A/B clinical results not fabricated in DB
    db_claims = db_session.query(Claim).filter(Claim.document_id == test_document.id).all()
    assert len(db_claims) == 0

    # Verify stage audit event is logged
    events = (
        db_session.query(JobStageEvent)
        .filter(JobStageEvent.job_id == job.id)
        .all()
    )
    assert any(e.status == "FAILED" and "ModernBERT unavailable" in e.error_message for e in events)


def test_f05_drill_readiness_probe_fails_closed_without_model_reload():
    """F-05 Drill 2: ModernBERT probe accurately reports NOT_READY without attempting model reloads."""
    broken_stage_b = BrokenStageBModel()
    status, detail = probe_modernbert(broken_stage_b)
    assert status == ModernBERTStatus.NOT_READY
    assert detail == "model_not_loaded"

    status_none, detail_none = probe_modernbert(None)
    assert status_none == ModernBERTStatus.NOT_READY
    assert detail_none == "stage_b_not_configured"


def test_f05_drill_production_mock_containment():
    """F-05 Drill 3: Production profile explicitly forbids mock/heuristic fallback for ModernBERT."""
    with pytest.raises(ProductionProfileError, match="MockModelPredictor is forbidden in PRODUCTION profile"):
        LearnedVerifier(
            custom_predictor=MockModelPredictor(),
            profile=RuntimeProfile.PRODUCTION,
        )

    stage_b_mock = LearnedVerifier(
        custom_predictor=MockModelPredictor(),
        profile=RuntimeProfile.DEV,
    )
    status, detail = probe_modernbert(stage_b_mock)
    assert status == ModernBERTStatus.NOT_READY
    assert "mock_predictor_not_permitted_in_production" in detail

    with pytest.raises(ProductionProfileError, match="allow_mocks=True is forbidden"):
        ProductionPipelineConfig(profile=RuntimeProfile.PRODUCTION, allow_mocks=True).validate()


# ==============================================================================
# F-06 OUTAGE DRILLS: QWEN / VLLM UNAVAILABLE
# ==============================================================================

class ConnectionFailingLLMClient:
    """Dependency failure double: simulates network connection refused."""
    def __init__(self):
        self.call_count = 0

    def generate(self, prompt: str, schema: type[Any]) -> str:
        self.call_count += 1
        raise ConnectionError("Failed to connect to vLLM server: connection refused on port 8000")


class TimeoutFailingLLMClient:
    """Dependency failure double: simulates request timeout."""
    def __init__(self):
        self.call_count = 0

    def generate(self, prompt: str, schema: type[Any]) -> str:
        self.call_count += 1
        raise TimeoutError("vLLM generation timed out after 30.0s")


def test_f06_drill_connection_failure_bounded_retry_and_safe_fallback():
    """F-06 Drill 1: Connection failure triggers bounded retries, safe fallback, and correct reason."""
    client = ConnectionFailingLLMClient()
    judge = StageCJudge(client=client, max_attempts=3)

    claim = ContractClaim(
        claim_id="clm-f06-conn",
        document_id="doc-f06-conn",
        field_type="lab.value",
        value="140",
        value_numeric=140.0,
        extraction_method="deterministic",
        region_id="reg-1",
        evidence_id="ev-1",
        extraction_confidence=0.85,
    )
    evidence = ContractEvidence(
        evidence_id="ev-1",
        document_id="doc-f06-conn",
        page_number=1,
        char_start=0,
        char_end=10,
        text="140 mg/dL",
        bbox=BBoxModel(x0=0, y0=0, x1=10, y1=10),
    )

    result = judge.judge(
        claim=claim,
        evidence=evidence,
        context_text="Serum Glucose: 140 mg/dL",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_confidence=0.55,
    )

    # Bounded retries executed exactly 3 times
    assert client.call_count == 3
    assert result.attempts == 3

    # Fallback properties
    assert result.is_fallback is True
    assert result.fallback_reason == FALLBACK_REASON_LLM_UNAVAILABLE
    assert result.fallback_reason == "llm_unavailable"

    # Clinical safety: safe insufficient evidence, no fabricated label
    assert result.label == "INSUFFICIENT_EVIDENCE"
    assert result.contradiction_type is None
    assert result.calibrated_confidence == 0.50
    assert "llm_unavailable" in result.rationale


def test_f06_drill_timeout_failure():
    """F-06 Drill 2: Generation timeout triggers llm_unavailable fallback without crash."""
    client = TimeoutFailingLLMClient()
    judge = StageCJudge(client=client, max_attempts=2)

    claim = ContractClaim(
        claim_id="clm-f06-time",
        document_id="doc-f06-time",
        field_type="lab.value",
        value="140",
        extraction_method="deterministic",
        region_id="reg-1",
        evidence_id="ev-1",
        extraction_confidence=0.85,
    )
    evidence = ContractEvidence(
        evidence_id="ev-1",
        document_id="doc-f06-time",
        page_number=1,
        char_start=0,
        char_end=10,
        text="140 mg/dL",
        bbox=BBoxModel(x0=0, y0=0, x1=10, y1=10),
    )

    result = judge.judge(
        claim=claim,
        evidence=evidence,
        context_text="Serum Glucose: 140 mg/dL",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_confidence=0.55,
    )

    assert client.call_count == 2
    assert result.is_fallback is True
    assert result.fallback_reason == "llm_unavailable"
    assert result.label == "INSUFFICIENT_EVIDENCE"


def test_f06_drill_circuit_breaker_open_fails_fast_without_network_flood():
    """F-06 Drill 3: When circuit breaker is OPEN, request fails fast (1 attempt, no flood)."""
    cb = CircuitBreaker(name="drill_cb", failure_threshold=1, cooldown_seconds=120.0)
    cb.record_failure(ConnectionError("vLLM host unreachable"))
    assert cb.state == CircuitState.OPEN

    calls_made = 0

    class ProtectedClient:
        def __init__(self, breaker):
            self.circuit_breaker = breaker

        def generate(self, prompt: str, schema: type[Any]) -> str:
            nonlocal calls_made
            with self.circuit_breaker:
                calls_made += 1
                return "{}"

    judge = StageCJudge(client=ProtectedClient(cb), max_attempts=3)

    claim = ContractClaim(
        claim_id="clm-f06-cb",
        document_id="doc-f06-cb",
        field_type="med.name",
        value="Aspirin",
        extraction_method="deterministic",
        region_id="reg-1",
        evidence_id="ev-1",
        extraction_confidence=0.85,
    )
    evidence = ContractEvidence(
        evidence_id="ev-1",
        document_id="doc-f06-cb",
        page_number=1,
        char_start=0,
        char_end=10,
        text="Aspirin 81 mg",
        bbox=BBoxModel(x0=0, y0=0, x1=10, y1=10),
    )

    result = judge.judge(
        claim=claim,
        evidence=evidence,
        context_text="Aspirin 81 mg",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_confidence=0.50,
    )

    # Fails fast on CircuitBreakerOpenError (non-retryable): exactly 1 attempt, 0 calls executed
    assert calls_made == 0
    assert result.attempts == 1
    assert result.is_fallback is True
    assert result.fallback_reason == "llm_unavailable"
    assert cb.state == CircuitState.OPEN


def test_f06_drill_worker_graceful_degradation_and_stage_ab_preservation(
    db_session: Session, test_document: Document
):
    """F-06 Drill 4: Full worker execution with Qwen outage:
      - Non-escalated Stage A/B claims remain fully operational (graceful degradation)
      - Escalated claim routes to AWAITING_LLM and human review
      - Document proceeds to PENDING_REVIEW
      - Audit events record fallback details
    """
    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=test_document.id)
    claimed_job = queue.claim_next(session=db_session, worker_id="drill-worker-f06")
    assert claimed_job is not None

    tier2_claim, tier1_claim, ev_unit, ev_val = create_dual_claims(str(test_document.id))
    ocr_output = create_sample_ocr_text(str(test_document.id))

    extraction_output = DocumentExtractionOutput(
        document_id=str(test_document.id),
        regions=[
            ExtractionResult(
                routing_decision=ContractRoutingDecision(
                    document_id=str(test_document.id),
                    region_id="reg-drill-01",
                    route="deterministic",
                    reason="lab_table",
                    confidence=0.95,
                ),
                evidence=[ev_unit, ev_val],
                claims=[tier2_claim, tier1_claim],
            )
        ],
    )

    cascade = VerificationCascade()

    class EscalatingStageB:
        is_model_loaded = True
        def verify(self, claim, evidence, stage_a_label, context_text):
            return StageBResult(
                label="DISCREPANCY",
                raw_confidence=0.52,
                calibrated_confidence=0.52,
                logits=[0.0, 0.0, 0.0],
                escalated=True,
                escalation_reason="Confidence below autonomous threshold",
            )
    cascade.stage_b = EscalatingStageB()
    cascade.stage_c = StageCJudge(client=ConnectionFailingLLMClient(), max_attempts=1)

    worker = DocumentProcessingWorker(
        queue=queue,
        cascade=cascade,
        worker_id="drill-worker-f06",
        ingest_fn=lambda path, **kwargs: ocr_output,
    )
    worker.router.extract_document = lambda doc, is_lab_default=None: extraction_output

    processed_job = worker.process_job(session=db_session, job=claimed_job)
    assert processed_job.status == JobStatus.COMPLETED

    db_session.refresh(test_document)
    assert test_document.status == DocumentStatus.PENDING_REVIEW

    db_claims = db_session.query(Claim).filter(Claim.document_id == test_document.id).all()
    assert len(db_claims) == 2

    # 1. Non-escalated claim (lab.unit): Stage A preserved, NOT fallback
    unit_claim = next(c for c in db_claims if c.field_type == "lab.unit")
    unit_vr = db_session.query(DBVerificationResult).filter(DBVerificationResult.claim_id == unit_claim.id).one()
    assert unit_vr.is_fallback is False
    assert unit_vr.fallback_reason is None
    assert unit_vr.verification_stage == DBVerificationStage.STAGE_A
    assert unit_claim.review_state == ClaimReviewState.PENDING

    # 2. Escalated claim (lab.value): routed to AWAITING_LLM and human review
    val_claim = next(c for c in db_claims if c.field_type == "lab.value")
    val_vr = db_session.query(DBVerificationResult).filter(DBVerificationResult.claim_id == val_claim.id).one()
    assert val_vr.is_fallback is True
    assert val_vr.fallback_reason == "llm_unavailable"
    assert val_vr.stage_c_details["handling_path"] == "AWAITING_LLM"
    assert val_claim.review_state == ClaimReviewState.ESCALATED
    assert val_claim.payload.get("awaiting_llm") is True
    assert val_claim.payload.get("fallback_reason") == "llm_unavailable"

    # Triage engine routes escalated claim to HIGH risk human review
    triage_input = ClaimTriageInput.from_orm(val_claim, val_vr)
    triage_res = TriageEngine().classify_claim(triage_input)
    assert triage_res.category == TriageCategory.HUMAN_REVIEW_REQUIRED
    assert triage_res.risk_tier == TriageRiskTier.HIGH

    # Verify audit event in JobStageEvent records escalated and insufficient evidence claim counts
    stage_events = db_session.query(JobStageEvent).filter(JobStageEvent.job_id == processed_job.id).all()
    verif_events = [e for e in stage_events if e.stage_name == "VERIFICATION"]
    assert len(verif_events) > 0
    assert verif_events[-1].metrics.get("num_insufficient_evidence") == 1
    assert verif_events[-1].metrics.get("num_escalated") == 1


def test_f06_drill_narrative_extraction_unavailable_marks_routing_decision():
    """F-06 Drill 5: Narrative extraction failure marks region narrative_not_processed."""
    class FailingProvider:
        def complete(self, sys_prompt: str, user_prompt: str) -> str:
            raise ConnectionError("vLLM narrative endpoint unavailable")

    router = ExtractionRouter(llm_provider=FailingProvider())
    doc_text = DocumentText(
        document_id="doc_narr_drill",
        source_type="born_digital",
        pages=[
            DocumentPage(
                page_number=1,
                width_px=612,
                height_px=792,
                tokens=[
                    Token(text="Patient", bbox=BBoxDict(x0=10, y0=10, x1=50, y1=20), line_id="L0"),
                    Token(text="is", bbox=BBoxDict(x0=55, y0=10, x1=70, y1=20), line_id="L0"),
                    Token(text="a", bbox=BBoxDict(x0=75, y0=10, x1=85, y1=20), line_id="L0"),
                    Token(text="65-year-old", bbox=BBoxDict(x0=90, y0=10, x1=150, y1=20), line_id="L0"),
                    Token(text="male.", bbox=BBoxDict(x0=155, y0=10, x1=190, y1=20), line_id="L0"),
                    Token(text="The", bbox=BBoxDict(x0=10, y0=25, x1=35, y1=35), line_id="L1"),
                    Token(text="patient", bbox=BBoxDict(x0=40, y0=25, x1=80, y1=35), line_id="L1"),
                    Token(text="reports", bbox=BBoxDict(x0=85, y0=25, x1=130, y1=35), line_id="L1"),
                    Token(text="he", bbox=BBoxDict(x0=135, y0=25, x1=150, y1=35), line_id="L1"),
                    Token(text="was", bbox=BBoxDict(x0=155, y0=25, x1=180, y1=35), line_id="L1"),
                    Token(text="started", bbox=BBoxDict(x0=185, y0=25, x1=230, y1=35), line_id="L1"),
                    Token(text="on", bbox=BBoxDict(x0=235, y0=25, x1=250, y1=35), line_id="L1"),
                    Token(text="Metformin", bbox=BBoxDict(x0=255, y0=25, x1=310, y1=35), line_id="L1"),
                    Token(text="daily.", bbox=BBoxDict(x0=315, y0=25, x1=350, y1=35), line_id="L1"),
                ],
            )
        ],
    )

    output = router.extract_document(doc_text)
    narrative_rds = [rd for rd in output.all_routing_decisions if rd.route == "llm_narrative"]
    assert len(narrative_rds) > 0
    for rd in narrative_rds:
        assert rd.reason == "narrative_not_processed"


def test_f06_drill_health_ready_preserves_application_readiness_during_vllm_outage():
    """F-06 Drill 6: vLLM outage does NOT make /health/ready report entire application unavailable."""
    registry = ModelHealthRegistry()

    class ReadyStageB:
        is_model_loaded = True

    class OutageVLLMProvider:
        base_url = "http://127.0.0.1:9999/v1"
        circuit_breaker = None

    registry.register_stage_b(ReadyStageB())
    registry.register_vllm_provider(OutageVLLMProvider())

    summary = registry.readiness_summary(vllm_probe_timeout=0.2)
    assert summary["modernbert"] == "ready"
    assert summary["qwen_vllm"] == "temporarily_unavailable"
    assert summary["qwen_vllm_detail"] == "server_unreachable"


# ==============================================================================
# F-07 OUTAGE DRILLS: QWEN MALFORMED / SCHEMA-INVALID OUTPUT
# ==============================================================================

class MalformedJsonSyntaxClient:
    """Dependency failure double: returns invalid non-JSON string."""
    def __init__(self):
        self.call_count = 0

    def generate(self, prompt: str, schema: type[Any]) -> str:
        self.call_count += 1
        return "<html><body>502 Bad Gateway - vLLM Proxy Error</body></html>"


class SchemaInvariantViolatingClient:
    """Dependency failure double: returns valid JSON that violates domain invariants."""
    def __init__(self):
        self.call_count = 0

    def generate(self, prompt: str, schema: type[Any]) -> str:
        self.call_count += 1
        # Invariant violation: DISCREPANCY requires contradiction_type != None
        return json.dumps({
            "label": "DISCREPANCY",
            "confidence": 0.88,
            "contradiction_type": None,  # INVARIANT VIOLATION
            "rationale": "Claim contradicts evidence but no contradiction type provided.",
        })


def test_f07_drill_malformed_json_syntax_rejected_with_bounded_retries():
    """F-07 Drill 1: Malformed JSON syntax is rejected without coercion; retries bounded to max_attempts."""
    client = MalformedJsonSyntaxClient()
    judge = StageCJudge(client=client, max_attempts=3)

    claim = ContractClaim(
        claim_id="clm-f07-syn",
        document_id="doc-f07-syn",
        field_type="lab.value",
        value="100",
        extraction_method="deterministic",
        region_id="reg-1",
        evidence_id="ev-1",
        extraction_confidence=0.85,
    )
    evidence = ContractEvidence(
        evidence_id="ev-1",
        document_id="doc-f07-syn",
        page_number=1,
        char_start=0,
        char_end=10,
        text="100 mg/dL",
        bbox=BBoxModel(x0=0, y0=0, x1=10, y1=10),
    )

    result = judge.judge(
        claim=claim,
        evidence=evidence,
        context_text="100 mg/dL",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_confidence=0.50,
    )

    # 1. Bounded retries executed exactly max_attempts
    assert client.call_count == 3
    assert result.attempts == 3

    # 2. No repair/coercion into fake success: is_fallback == True
    assert result.is_fallback is True

    # 3. Exactly llm_schema_invalid
    assert result.fallback_reason == FALLBACK_REASON_LLM_SCHEMA_INVALID
    assert result.fallback_reason == "llm_schema_invalid"

    # 4. Safe clinical verdict
    assert result.label == "INSUFFICIENT_EVIDENCE"
    assert result.contradiction_type is None
    assert result.calibrated_confidence == 0.50


def test_f07_drill_schema_invariant_violation_triggers_fallback():
    """F-07 Drill 2: Syntactically valid JSON violating Pydantic/domain invariants triggers fallback."""
    client = SchemaInvariantViolatingClient()
    judge = StageCJudge(client=client, max_attempts=2)

    claim = ContractClaim(
        claim_id="clm-f07-inv",
        document_id="doc-f07-inv",
        field_type="lab.value",
        value="100",
        extraction_method="deterministic",
        region_id="reg-1",
        evidence_id="ev-1",
        extraction_confidence=0.85,
    )
    evidence = ContractEvidence(
        evidence_id="ev-1",
        document_id="doc-f07-inv",
        page_number=1,
        char_start=0,
        char_end=10,
        text="100 mg/dL",
        bbox=BBoxModel(x0=0, y0=0, x1=10, y1=10),
    )

    result = judge.judge(
        claim=claim,
        evidence=evidence,
        context_text="100 mg/dL",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_confidence=0.50,
    )

    assert client.call_count == 2
    assert result.is_fallback is True
    assert result.fallback_reason == "llm_schema_invalid"
    assert result.label == "INSUFFICIENT_EVIDENCE"


def test_f07_drill_worker_malformed_output_routes_to_human_review(
    db_session: Session, test_document: Document
):
    """F-07 Drill 3: Full worker processing with schema-invalid output:
      - Claim routes to human review with fallback_reason 'llm_schema_invalid'
      - Stage A/B results remain unaffected
      - No fabricated contradiction type is saved
    """
    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=test_document.id)
    claimed_job = queue.claim_next(session=db_session, worker_id="drill-worker-f07")
    assert claimed_job is not None

    tier2_claim, tier1_claim, ev_unit, ev_val = create_dual_claims(str(test_document.id))
    ocr_output = create_sample_ocr_text(str(test_document.id))

    extraction_output = DocumentExtractionOutput(
        document_id=str(test_document.id),
        regions=[
            ExtractionResult(
                routing_decision=ContractRoutingDecision(
                    document_id=str(test_document.id),
                    region_id="reg-drill-01",
                    route="deterministic",
                    reason="lab_table",
                    confidence=0.95,
                ),
                evidence=[ev_unit, ev_val],
                claims=[tier2_claim, tier1_claim],
            )
        ],
    )

    cascade = VerificationCascade()

    class EscalatingStageB:
        is_model_loaded = True
        def verify(self, claim, evidence, stage_a_label, context_text):
            return StageBResult(
                label="DISCREPANCY",
                raw_confidence=0.55,
                calibrated_confidence=0.55,
                logits=[0.0, 0.0, 0.0],
                escalated=True,
                escalation_reason="Low confidence escalation",
            )
    cascade.stage_b = EscalatingStageB()
    cascade.stage_c = StageCJudge(client=MalformedJsonSyntaxClient(), max_attempts=1)

    worker = DocumentProcessingWorker(
        queue=queue,
        cascade=cascade,
        worker_id="drill-worker-f07",
        ingest_fn=lambda path, **kwargs: ocr_output,
    )
    worker.router.extract_document = lambda doc, is_lab_default=None: extraction_output

    processed_job = worker.process_job(session=db_session, job=claimed_job)
    assert processed_job.status == JobStatus.COMPLETED

    db_claims = db_session.query(Claim).filter(Claim.document_id == test_document.id).all()
    assert len(db_claims) == 2

    # Escalated claim has fallback_reason 'llm_schema_invalid'
    val_claim = next(c for c in db_claims if c.field_type == "lab.value")
    val_vr = db_session.query(DBVerificationResult).filter(DBVerificationResult.claim_id == val_claim.id).one()

    assert val_vr.is_fallback is True
    assert val_vr.fallback_reason == "llm_schema_invalid"
    assert val_vr.stage_c_details["fallback_reason"] == "llm_schema_invalid"
    assert val_vr.contradiction_type is None  # NOT fabricated

    assert val_claim.review_state == ClaimReviewState.ESCALATED
    assert val_claim.payload.get("fallback_reason") == "llm_schema_invalid"

    # Triage routing
    triage_res = TriageEngine().classify_claim(ClaimTriageInput.from_orm(val_claim, val_vr))
    assert triage_res.category == TriageCategory.HUMAN_REVIEW_REQUIRED
    assert triage_res.risk_tier == TriageRiskTier.HIGH
