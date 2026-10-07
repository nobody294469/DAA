"""Phase 8.7 Step 2 Comprehensive Tests: Structured Fallback Classification + Worker Integration.

Authority:
  - PROJECT_SPEC.md §3, §10, §12, §13.3
  - PRODUCTIZATION_SPEC.md §3.5, §7.2, §13 (Failures F-05, F-06, F-07)
  - PRODUCTIZATION_DECISIONS.md Phase 8.7

Validates:
  F-05:
    - ModernBERT unavailable/unloadable
    - Worker refuses to process job; job remains QUEUED (PENDING)
    - Document status remains INGESTED (not marked FAILED)
    - No heuristic/mock fallback is permitted
    - Production containment remains intact
  F-06:
    - Qwen unavailable after bounded retries / circuit breaker OPEN
    - VerificationResult.is_fallback == True
    - Machine-readable reason is exactly 'llm_unavailable'
    - Escalated claim enters AWAITING_LLM handling path and routes to human review
    - Non-escalated Stage A/B processing remains unaffected (graceful degradation)
    - Narrative regions that could not be processed by unavailable LLM marked 'narrative_not_processed'
    - Provenance preserved for UI / reviewer
  F-07:
    - Malformed Qwen output / schema-invalid JSON
    - Bounded retries occur
    - After exhaustion: is_fallback == True
    - Machine-readable reason is exactly 'llm_schema_invalid'
    - Malformed output rejected; no repair or substitution
    - Claim routes to human review with preserved provenance
  Normal Operation:
    - Normal successful Stage C behavior is unchanged (is_fallback == False, fallback_reason == None)
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any, Optional
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from clindoc.config import (
    ProductionProfileError,
    RuntimeProfile,
)
from clindoc.db import (
    Base,
    Claim,
    ClaimOrigin,
    ClaimReviewState,
    ContradictionType as DBContradictionType,
    Document,
    DocumentStatus,
    Evidence,
    JobStageEvent,
    JobStatus,
    ModelRun,
    ProcessingJob,
    RoutingDecision,
    TriageCategory,
    TriageRiskTier,
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
from clindoc.extract.llm_extractor import NarrativeExtractor
from clindoc.extract.router import ExtractionRouter
from clindoc.jobs.queue import JobQueue
from clindoc.jobs.worker import (
    DocumentProcessingWorker,
    ModernBERTUnavailableError,
)
from clindoc.normalize.normalizer import TerminologyNormalizer
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict
from clindoc.review.triage import ClaimTriageInput, TriageEngine
from clindoc.schema.verification import VerificationResult as ContractVerificationResult
from clindoc.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    FALLBACK_REASON_LLM_SCHEMA_INVALID,
    FALLBACK_REASON_LLM_UNAVAILABLE,
    classify_fallback_reason,
)
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.stage_b import LearnedVerifier, MockModelPredictor, StageBResult
from clindoc.verify.stage_c import (
    DEFAULT_STAGE_C_SYSTEM_PROMPT,
    DefaultMockLLMJudge,
    StageCJudge,
    StageCJudgeOutput,
    StageCResult,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    """Isolated in-memory SQLite session with foreign keys enabled."""
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
        idp_subject="sub_structured_fallback_tester",
        username="fallback_tester",
        full_name="Fallback Tester",
        email="fallback_tester@hospital.org",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def sample_document(db_session: Session, test_user: User, tmp_path: Path) -> Document:
    fake_pdf = tmp_path / "patient_discharge.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 sample content")
    doc = Document(
        original_filename="patient_discharge.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(b"%PDF-1.4 sample content"),
        sha256="0123456789abcdef" * 4,
        storage_ref=f"file://{fake_pdf.resolve()}",
        status=DocumentStatus.INGESTED,
        uploaded_by=test_user.id,
        patient_context_id="PT-998877",
    )
    db_session.add(doc)
    db_session.flush()
    return doc


# ─── UNIT TESTS: CLASSIFIER ───────────────────────────────────────────────────

def test_classify_fallback_reason_exact_codes():
    """Verify classify_fallback_reason produces exact machine-readable codes."""
    # F-07: Schema invalid / JSON parsing failures
    json_err = json.JSONDecodeError("Expecting value", "doc", 0)
    assert classify_fallback_reason(json_err) == FALLBACK_REASON_LLM_SCHEMA_INVALID
    assert classify_fallback_reason(json_err) == "llm_schema_invalid"

    val_err = ValueError("contradiction_type must not be null when label is DISCREPANCY")
    assert classify_fallback_reason(val_err) == "llm_schema_invalid"

    # F-06: Transport / timeout / circuit breaker errors
    cb_err = CircuitBreakerOpenError("Circuit is OPEN")
    assert classify_fallback_reason(cb_err) == FALLBACK_REASON_LLM_UNAVAILABLE
    assert classify_fallback_reason(cb_err) == "llm_unavailable"

    timeout_err = TimeoutError("Request timed out")
    assert classify_fallback_reason(timeout_err) == "llm_unavailable"

    conn_err = ConnectionError("Connection refused by model server")
    assert classify_fallback_reason(conn_err) == "llm_unavailable"

    assert classify_fallback_reason(None) is None


# ─── F-05: MODERNBERT UNAVAILABLE TESTS ────────────────────────────────────────

def test_f05_modernbert_unavailable_worker_refuses_job_and_remains_queued(
    db_session: Session, sample_document: Document
):
    """F-05: When ModernBERT is unavailable, worker must refuse to run and job remains QUEUED."""
    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=sample_document.id)
    attempts_before_claim = job.attempts

    # Claim job -> status becomes RUNNING
    claimed_job = queue.claim_next(session=db_session, worker_id="worker-test-01")
    assert claimed_job is not None
    assert claimed_job.status == JobStatus.RUNNING
    attempts_while_running = claimed_job.attempts

    # Create worker with a stage_b that fails to load (ModernBERT unavailable)
    class BrokenModernBERTStageB:
        is_model_loaded = False
        def verify_readiness(self):
            raise ModernBERTUnavailableError("ModernBERT checkpoint missing or unreadable")
        def load_model(self):
            raise ModernBERTUnavailableError("ModernBERT checkpoint missing or unreadable")

    cascade = VerificationCascade()
    cascade.stage_b = BrokenModernBERTStageB()

    worker = DocumentProcessingWorker(
        queue=queue,
        cascade=cascade,
        worker_id="worker-test-01",
    )

    # Execute worker processing
    result_job = worker.process_job(session=db_session, job=claimed_job)

    # Job MUST remain QUEUED (PENDING)
    assert result_job.status == JobStatus.PENDING
    assert result_job.worker_id is None
    assert result_job.lease_expires_at is None
    assert result_job.error_code == "MODERNBERT_UNAVAILABLE"

    # Retry/attempt budget MUST NOT be consumed by the refusal
    assert result_job.attempts == attempts_before_claim
    assert result_job.attempts <= attempts_while_running

    # Document state MUST revert to INGESTED (ready to be picked up once ML steward fixes weights)
    db_session.refresh(sample_document)
    assert sample_document.status == DocumentStatus.INGESTED

    # Verify stage event recorded FAILED with clean message
    events = (
        db_session.query(JobStageEvent)
        .filter(JobStageEvent.job_id == job.id)
        .all()
    )
    assert any(e.status == "FAILED" and "ModernBERT unavailable" in e.error_message for e in events)


def test_f05_production_mock_containment_intact(monkeypatch):
    """F-05: In PRODUCTION profile, mock predictor or heuristic fallback is forbidden."""
    monkeypatch.setenv("CLINDOC_PROFILE", "production")

    # In production, MockModelPredictor must raise immediately and be refused
    with pytest.raises(ProductionProfileError) as exc_info:
        LearnedVerifier(
            custom_predictor=MockModelPredictor(),
            profile=RuntimeProfile.PRODUCTION,
        )
    assert "MockModelPredictor is forbidden in PRODUCTION profile" in str(exc_info.value)

    # In production, heuristic fallback must raise
    with pytest.raises(ProductionProfileError) as exc_info:
        LearnedVerifier(
            model_path="/non/existent/model.pt",
            allow_heuristic_fallback=True,
            profile=RuntimeProfile.PRODUCTION,
        )
    assert "Heuristic fallback is forbidden in PRODUCTION profile" in str(exc_info.value)


# ─── F-06: QWEN UNAVAILABLE TESTS ──────────────────────────────────────────────

def test_f06_qwen_unavailable_after_bounded_retries_returns_fallback_llm_unavailable():
    """F-06: When Qwen is unavailable, bounded retries occur, is_fallback=True, reason='llm_unavailable'."""
    call_attempts = 0

    class FailingUnavailableClient:
        def generate(self, prompt: str, schema: type[Any]) -> str:
            nonlocal call_attempts
            call_attempts += 1
            raise ConnectionError("vLLM connection refused: endpoint unavailable")

    judge = StageCJudge(
        client=FailingUnavailableClient(),
        max_attempts=2,
    )

    claim = ContractClaim(
        claim_id="clm-f06-001",
        document_id="doc-f06-001",
        field_type="lab.value",
        value="140",
        value_numeric=140.0,
        unit_raw="mg/dL",
        extraction_method="deterministic",
        region_id="reg-f06-001",
        evidence_id="ev-f06-001",
        extraction_confidence=0.85,
    )
    evidence = ContractEvidence(
        evidence_id="ev-f06-001",
        document_id="doc-f06-001",
        page_number=1,
        char_start=0,
        char_end=20,
        text="Glucose: 140 mg/dL",
        bbox=BBoxModel(x0=10, y0=10, x1=100, y1=30),
    )

    res = judge.judge(
        claim=claim,
        evidence=evidence,
        context_text="Glucose: 140 mg/dL",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_confidence=0.55,
    )

    # 1. Bounded retries occurred (2 attempts)
    assert call_attempts == 2
    assert res.attempts == 2

    # 2. is_fallback is True
    assert res.is_fallback is True

    # 3. fallback_reason is exactly 'llm_unavailable'
    assert res.fallback_reason == "llm_unavailable"

    # 4. Safe verdict INSUFFICIENT_EVIDENCE
    assert res.label == "INSUFFICIENT_EVIDENCE"
    assert "Stage C verification fallback (llm_unavailable)" in res.rationale


def test_f06_circuit_breaker_open_fails_fast_with_llm_unavailable():
    """F-06: When circuit breaker is OPEN, call fails fast without retry and records llm_unavailable."""
    cb = CircuitBreaker(name="test_cb", failure_threshold=1, cooldown_seconds=60.0)
    cb.record_failure(ConnectionError("Service down"))
    assert cb.state == CircuitState.OPEN

    class BreakerProtectedClient:
        def __init__(self, breaker):
            self.circuit_breaker = breaker
        def generate(self, prompt: str, schema: type[Any]) -> str:
            with self.circuit_breaker:
                return "{}"

    judge = StageCJudge(
        client=BreakerProtectedClient(cb),
        max_attempts=3,
    )

    claim = ContractClaim(
        claim_id="clm-cb-001",
        document_id="doc-cb-001",
        field_type="med.name",
        value="Lisinopril",
        extraction_method="deterministic",
        region_id="reg-cb-001",
        evidence_id="ev-cb-001",
        extraction_confidence=0.90,
    )
    evidence = ContractEvidence(
        evidence_id="ev-cb-001",
        document_id="doc-cb-001",
        page_number=1,
        char_start=0,
        char_end=20,
        text="Lisinopril 10 mg",
        bbox=BBoxModel(x0=10, y0=10, x1=100, y1=30),
    )

    res = judge.judge(
        claim=claim,
        evidence=evidence,
        context_text="Lisinopril 10 mg",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_confidence=0.52,
    )

    # CircuitBreakerOpenError is non-retryable -> fails fast in 1 attempt
    assert res.attempts == 1
    assert res.is_fallback is True
    assert res.fallback_reason == "llm_unavailable"


def test_f06_worker_flow_escalated_claims_enter_awaiting_llm_and_stage_ab_unaffected(
    db_session: Session, sample_document: Document
):
    """F-06: Worker integration test:
      - Escalated claim enters AWAITING_LLM and routes to human review with reason 'llm_unavailable'
      - Non-escalated Stage A/B claim remains unaffected
      - Document proceeds to PENDING_REVIEW
    """
    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=sample_document.id)
    claimed_job = queue.claim_next(session=db_session, worker_id="worker-f06-test")
    assert claimed_job is not None

    # Construct two claims:
    # Claim 1: Tier 2 (lab.unit), resolved deterministically at Stage A without escalation
    claim_tier2 = ContractClaim(
        claim_id=str(uuid.uuid4()),
        document_id=str(sample_document.id),
        field_type="lab.unit",
        value="mg/dL",
        extraction_method="deterministic",
        region_id="reg-1",
        evidence_id="ev-unit-01",
        extraction_confidence=0.95,
        group_id="lab-01",
    )
    # Claim 2: Tier 1 (lab.value), escalated to Stage C
    claim_tier1 = ContractClaim(
        claim_id=str(uuid.uuid4()),
        document_id=str(sample_document.id),
        field_type="lab.value",
        value="120",
        value_numeric=120.0,
        unit_raw="mg/dL",
        extraction_method="deterministic",
        region_id="reg-1",
        evidence_id="ev-val-01",
        extraction_confidence=0.90,
        group_id="lab-01",
    )

    ev_unit = ContractEvidence(
        evidence_id="ev-unit-01",
        document_id=str(sample_document.id),
        page_number=1,
        char_start=8,
        char_end=13,
        text="mg/dL",
        bbox=BBoxModel(x0=50, y0=10, x1=80, y1=20),
    )
    ev_val = ContractEvidence(
        evidence_id="ev-val-01",
        document_id=str(sample_document.id),
        page_number=1,
        char_start=0,
        char_end=7,
        text="120 mg/dL",
        bbox=BBoxModel(x0=10, y0=10, x1=50, y1=20),
    )

    ocr_output = DocumentText(
        document_id=str(sample_document.id),
        source_type="born_digital",
        pages=[
            DocumentPage(
                page_number=1,
                width_px=612,
                height_px=792,
                tokens=[
                    Token(text="Glucose", bbox=BBoxDict(x0=10, y0=10, x1=50, y1=20), line_id="L1"),
                    Token(text="120", bbox=BBoxDict(x0=55, y0=10, x1=80, y1=20), line_id="L1"),
                    Token(text="mg/dL", bbox=BBoxDict(x0=85, y0=10, x1=120, y1=20), line_id="L1"),
                ],
            )
        ],
    )

    extraction_output = DocumentExtractionOutput(
        document_id=str(sample_document.id),
        regions=[
            ExtractionResult(
                routing_decision=ContractRoutingDecision(
                    document_id=str(sample_document.id),
                    region_id="reg-1",
                    route="deterministic",
                    reason="lab_table",
                    confidence=0.95,
                ),
                evidence=[ev_unit, ev_val],
                claims=[claim_tier2, claim_tier1],
            )
        ],
    )

    # Configure cascade:
    # Stage C fails with connection error
    class UnavailableLLMClient:
        def generate(self, prompt: str, schema: type[Any]) -> str:
            raise ConnectionError("vLLM unavailable")

    cascade = VerificationCascade()
    cascade.stage_c = StageCJudge(client=UnavailableLLMClient(), max_attempts=1)

    # Force claim_tier1 to escalate to Stage C
    class EscalatingStageB:
        def verify(self, claim, evidence, stage_a_label, context_text):
            return StageBResult(
                label="DISCREPANCY",
                raw_confidence=0.55,
                calibrated_confidence=0.55,
                logits=[0.0, 0.0, 0.0],
                escalated=True,
                escalation_reason="Low confidence disagreement",
            )
    cascade.stage_b = EscalatingStageB()

    # Ingest mock returning our test document text
    worker = DocumentProcessingWorker(
        queue=queue,
        cascade=cascade,
        worker_id="worker-f06-test",
        ingest_fn=lambda path, **kwargs: ocr_output,
    )
    # Inject extraction directly
    worker.router.extract_document = lambda doc, is_lab_default=None: extraction_output

    # Run processing
    processed_job = worker.process_job(session=db_session, job=claimed_job)
    assert processed_job.status == JobStatus.COMPLETED

    # Document proceeds to PENDING_REVIEW
    db_session.refresh(sample_document)
    assert sample_document.status == DocumentStatus.PENDING_REVIEW

    # Check persisted claims and verification results
    db_claims = (
        db_session.query(Claim)
        .filter(Claim.document_id == sample_document.id)
        .all()
    )
    assert len(db_claims) == 2

    # Find the tier 2 claim (lab.unit)
    db_unit = next(c for c in db_claims if c.field_type == "lab.unit")
    vr_unit = (
        db_session.query(DBVerificationResult)
        .filter(DBVerificationResult.claim_id == db_unit.id)
        .one()
    )
    # Tier 2 claim was resolved at Stage A, is_fallback is False
    assert vr_unit.is_fallback is False
    assert vr_unit.fallback_reason is None
    assert vr_unit.verification_stage == DBVerificationStage.STAGE_A
    assert db_unit.review_state == ClaimReviewState.PENDING

    # Find the escalated tier 1 claim (lab.value)
    db_val = next(c for c in db_claims if c.field_type == "lab.value")
    vr_val = (
        db_session.query(DBVerificationResult)
        .filter(DBVerificationResult.claim_id == db_val.id)
        .one()
    )
    # Tier 1 claim was escalated and Stage C failed
    assert vr_val.is_fallback is True
    assert vr_val.fallback_reason == "llm_unavailable"
    assert vr_val.stage_c_details["is_fallback"] is True
    assert vr_val.stage_c_details["fallback_reason"] == "llm_unavailable"
    assert vr_val.stage_c_details["handling_path"] == "AWAITING_LLM"

    # Escalated claim entered AWAITING_LLM handling path and routes to human review
    assert db_val.review_state == ClaimReviewState.ESCALATED
    assert db_val.payload.get("awaiting_llm") is True
    assert db_val.payload.get("fallback_reason") == "llm_unavailable"
    assert db_val.payload.get("review_reason") == "llm_unavailable"

    # Check triage engine output on this claim
    triage_engine = TriageEngine()
    triage_input = ClaimTriageInput.from_orm(db_val, vr_val)
    triage_result = triage_engine.classify_claim(triage_input)
    assert triage_result.category == TriageCategory.HUMAN_REVIEW_REQUIRED
    assert triage_result.risk_tier == TriageRiskTier.HIGH
    assert any("fallback" in r.lower() for r in triage_result.reasons)


def test_f06_narrative_extraction_unavailable_marks_narrative_not_processed():
    """F-06: When narrative extraction LLM is unavailable, region is marked narrative_not_processed."""
    class FailingNarrativeProvider:
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            raise ConnectionError("Qwen model serving endpoint down")

    router = ExtractionRouter(llm_provider=FailingNarrativeProvider())

    doc_text = DocumentText(
        document_id="doc_narrative_001",
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

    # Narrative regions must have reason 'narrative_not_processed'
    narrative_rds = [rd for rd in output.all_routing_decisions if rd.route == "llm_narrative"]
    assert len(narrative_rds) > 0
    for rd in narrative_rds:
        assert rd.reason == "narrative_not_processed"

    # Diagnostics indicate narrative_not_processed
    for region in output.regions:
        if region.routing_decision.route == "llm_narrative":
            assert region.diagnostics.get("status") == "narrative_not_processed"


# ─── F-07: QWEN MALFORMED OUTPUT TESTS ─────────────────────────────────────────

def test_f07_qwen_malformed_output_rejected_and_routes_to_human_review():
    """F-07: When Qwen returns malformed/schema-invalid JSON:
      - Bounded retries occur
      - is_fallback == True, reason == 'llm_schema_invalid'
      - Malformed result rejected; no repair substitution
      - Routes to human review
    """
    call_attempts = 0

    class MalformedLLMClient:
        def generate(self, prompt: str, schema: type[Any]) -> str:
            nonlocal call_attempts
            call_attempts += 1
            # Return invalid JSON syntax
            return "{ 'label': 'SUPPORTED', 'confidence': 'not-a-number' }"

    judge = StageCJudge(
        client=MalformedLLMClient(),
        max_attempts=2,
    )

    claim = ContractClaim(
        claim_id="clm-f07-001",
        document_id="doc-f07-001",
        field_type="lab.value",
        value="100",
        extraction_method="deterministic",
        region_id="reg-f07-001",
        evidence_id="ev-f07-001",
        extraction_confidence=0.85,
    )
    evidence = ContractEvidence(
        evidence_id="ev-f07-001",
        document_id="doc-f07-001",
        page_number=1,
        char_start=0,
        char_end=20,
        text="Glucose: 100 mg/dL",
        bbox=BBoxModel(x0=10, y0=10, x1=100, y1=30),
    )

    res = judge.judge(
        claim=claim,
        evidence=evidence,
        context_text="Glucose: 100 mg/dL",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_confidence=0.55,
    )

    # 1. Bounded retries occurred (2 attempts)
    assert call_attempts == 2
    assert res.attempts == 2

    # 2. is_fallback is True
    assert res.is_fallback is True

    # 3. Machine readable fallback reason is exactly 'llm_schema_invalid'
    assert res.fallback_reason == "llm_schema_invalid"

    # 4. Malformed output was rejected, safe fallback label emitted
    assert res.label == "INSUFFICIENT_EVIDENCE"
    assert "llm_schema_invalid" in res.rationale


def test_f07_schema_invariant_violation_triggers_llm_schema_invalid():
    """F-07: Valid JSON that violates StageCJudgeOutput invariant triggers llm_schema_invalid."""
    class InvariantViolatingClient:
        def generate(self, prompt: str, schema: type[Any]) -> str:
            # DISCREPANCY requires contradiction_type to be non-null. Violating invariant:
            return json.dumps({
                "label": "DISCREPANCY",
                "confidence": 0.85,
                "contradiction_type": None,  # INVARIANT VIOLATION
                "rationale": "Claim contradicts evidence but no contradiction_type supplied.",
            })

    judge = StageCJudge(
        client=InvariantViolatingClient(),
        max_attempts=2,
    )

    claim = ContractClaim(
        claim_id="clm-f07-inv",
        document_id="doc-f07-inv",
        field_type="lab.value",
        value="100",
        extraction_method="deterministic",
        region_id="reg-f07-inv",
        evidence_id="ev-f07-inv",
        extraction_confidence=0.85,
    )
    evidence = ContractEvidence(
        evidence_id="ev-f07-inv",
        document_id="doc-f07-inv",
        page_number=1,
        char_start=0,
        char_end=20,
        text="Glucose: 100 mg/dL",
        bbox=BBoxModel(x0=10, y0=10, x1=100, y1=30),
    )

    res = judge.judge(
        claim=claim,
        evidence=evidence,
        context_text="Glucose: 100 mg/dL",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_confidence=0.55,
    )

    assert res.is_fallback is True
    assert res.fallback_reason == "llm_schema_invalid"


# ─── NORMAL SUCCESSFUL STAGE C UNCHANGED ──────────────────────────────────────

def test_normal_successful_stage_c_is_fallback_false():
    """Verify normal successful Stage C judgment produces is_fallback == False, fallback_reason == None."""
    class SuccessfulClient:
        def generate(self, prompt: str, schema: type[Any]) -> str:
            return json.dumps({
                "label": "SUPPORTED",
                "confidence": 0.95,
                "contradiction_type": None,
                "rationale": "Extracted glucose value 95 mg/dL is explicitly corroborated by evidence text.",
            })

    judge = StageCJudge(client=SuccessfulClient())

    claim = ContractClaim(
        claim_id="clm-ok-001",
        document_id="doc-ok-001",
        field_type="lab.value",
        value="95",
        value_numeric=95.0,
        unit_raw="mg/dL",
        extraction_method="deterministic",
        region_id="reg-ok-001",
        evidence_id="ev-ok-001",
        extraction_confidence=0.92,
    )
    evidence = ContractEvidence(
        evidence_id="ev-ok-001",
        document_id="doc-ok-001",
        page_number=1,
        char_start=0,
        char_end=20,
        text="Glucose: 95 mg/dL",
        bbox=BBoxModel(x0=10, y0=10, x1=100, y1=30),
    )

    res = judge.judge(
        claim=claim,
        evidence=evidence,
        context_text="Glucose: 95 mg/dL",
        stage_a_label="SUPPORTED",
        stage_b_label="DISCREPANCY",
        stage_b_confidence=0.58,
    )

    assert res.label == "SUPPORTED"
    assert res.is_fallback is False
    assert res.fallback_reason is None
    assert "corroborated" in res.rationale
