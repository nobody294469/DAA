"""Comprehensive integration tests for Phase 8.3 Task 3B Step 5: Wire Verification Stage.

Validates:
  A. Successful verification:
     - VerificationCascade is invoked with normalized claims, evidence_map, and page_text_map.
     - VerificationResults are persisted into database with correct claim_id (FK to claims.id).
     - VerificationResults correctly reference job.model_run_id.
     - Final document state transitions to PENDING_REVIEW.
     - Final processing job transitions to COMPLETED.
     - VERIFICATION STARTED + COMPLETED stage events exist.
     - Operational metrics are strictly PHI-free.
  B. Verification result field fidelity:
     - SUPPORTED verdict.
     - REFUTED verdict (mapping DISCREPANCY -> DBVerificationLabel.REFUTED).
     - INSUFFICIENT_EVIDENCE verdict.
     - Cascade stages (STAGE_A, STAGE_B, STAGE_C).
     - Calibrated confidence in [0.0, 1.0].
     - Escalation flag fidelity.
     - ContradictionType mapping (NUMERICAL_MISMATCH, etc.).
     - Rationale preserved for Stage C LLM Judge verdicts.
  C. Idempotency & rerun safety:
     - Running verification multiple times produces exactly one VerificationResult per Claim.
  D. Partial persistence failure & retry:
     - Simulates failure midway through verification persistence.
     - Retry cleans up stale partial results and regenerates complete verification state.
  E. Verification failure handling:
     - Cascade exception records VERIFICATION / FAILED event.
     - Sanitizes PHI and paths.
     - Uses JobQueue retry semantics without false completion.
  F. Full worker end-to-end lifecycle:
     - OCR -> Extraction -> Normalization -> Verification -> Final Document PENDING_REVIEW.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import time
from typing import Any, Optional, Sequence
import uuid

import fitz
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

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
from clindoc.jobs.worker import DocumentProcessingWorker
from clindoc.normalize.normalizer import TerminologyNormalizer
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict
from clindoc.schema.verification import (
    ContradictionType as ContractContradictionType,
    VerificationLabel as ContractVerificationLabel,
    VerificationResult as ContractVerificationResult,
)
from clindoc.storage.blob import EncryptedFileBlobStore
from clindoc.verify.cascade import VerificationCascade


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
    session_factory = sessionmaker(bind=engine)
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
        idp_subject="sub_verify_worker_test_user",
        username="verify_tester",
        full_name="Verify Tester",
        email="verify_tester@hospital.org",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def blob_store(tmp_path: Path) -> EncryptedFileBlobStore:
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)
    key = b"0123456789abcdef0123456789abcdef"  # 32-byte AES-256 key
    return EncryptedFileBlobStore(root_dir=blob_dir, encryption_key=key)


def _generate_synthetic_pdf() -> bytes:
    """Generate a minimal valid 1-page PDF for worker ingestion."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(fitz.Point(72, 72), "CLINICAL LABORATORY REPORT")
    page.insert_text(fitz.Point(72, 100), "Glucose: 95 mg/dL")
    page.insert_text(fitz.Point(72, 120), "Medication: Atorvastatin 20 mg oral daily")
    page.insert_text(fitz.Point(72, 140), "Observation: Potassium 3.2 mmol/L (LOW)")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_mock_router_for_verification() -> ExtractionRouter:
    """Mock ExtractionRouter that outputs claims ready for normalization and verification."""
    router = ExtractionRouter()

    def mock_extract(doc: DocumentText, is_lab_default: bool | None = None) -> DocumentExtractionOutput:
        ev1 = ContractEvidence(
            evidence_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            page_number=1,
            text="Glucose: 95 mg/dL",
            char_start=20,
            char_end=37,
            bbox=BBoxModel(x0=72.0, y0=100.0, x1=180.0, y1=112.0),
        )
        ev2 = ContractEvidence(
            evidence_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            page_number=1,
            text="Atorvastatin 20 mg oral daily",
            char_start=40,
            char_end=69,
            bbox=BBoxModel(x0=72.0, y0=120.0, x1=250.0, y1=132.0),
        )
        ev3 = ContractEvidence(
            evidence_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            page_number=1,
            text="Potassium 3.2 mmol/L (LOW)",
            char_start=75,
            char_end=101,
            bbox=BBoxModel(x0=72.0, y0=140.0, x1=230.0, y1=152.0),
        )

        c1 = ContractClaim(
            claim_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            group_id="lab_group_1",
            field_type="lab.test_name",
            value="Glucose",
            extraction_method="deterministic",
            extraction_confidence=0.98,
            evidence_id=ev1.evidence_id,
            region_id="region_lab",
        )
        c2 = ContractClaim(
            claim_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            group_id="med_group_1",
            field_type="med.name",
            value="Atorvastatin",
            extraction_method="deterministic",
            extraction_confidence=0.96,
            evidence_id=ev2.evidence_id,
            region_id="region_med",
        )
        c3 = ContractClaim(
            claim_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            group_id="lab_group_2",
            field_type="lab.value",
            value="3.2",
            unit_raw="mmol/L",
            extraction_method="deterministic",
            extraction_confidence=0.95,
            evidence_id=ev3.evidence_id,
            region_id="region_lab",
        )

        rd1 = ContractRoutingDecision(
            document_id=doc.document_id,
            region_id="region_lab",
            route="deterministic",
            confidence=0.98,
            reason="High-confidence lab match",
        )
        rd2 = ContractRoutingDecision(
            document_id=doc.document_id,
            region_id="region_med",
            route="deterministic",
            confidence=0.96,
            reason="High-confidence medication match",
        )

        r1 = ExtractionResult(
            region_id="region_lab",
            routing_decision=rd1,
            claims=[c1, c3],
            evidence=[ev1, ev3],
        )
        r2 = ExtractionResult(
            region_id="region_med",
            routing_decision=rd2,
            claims=[c2],
            evidence=[ev2],
        )

        return DocumentExtractionOutput(
            document_id=doc.document_id,
            regions=[r1, r2],
            all_claims=[c1, c2, c3],
            all_evidence=[ev1, ev2, ev3],
            all_routing_decisions=[rd1, rd2],
        )

    router.extract_document = mock_extract
    return router


def _make_mock_cascade_for_verification() -> VerificationCascade:
    """Mock VerificationCascade producing diverse verdicts across Stages A, B, and C."""
    cascade = VerificationCascade()

    def mock_verify_claims(
        claims: Sequence[ContractClaim],
        evidence_map: dict[str, ContractEvidence],
        page_text_map: Optional[dict[str, str]] = None,
    ) -> list[ContractVerificationResult]:
        results: list[ContractVerificationResult] = []
        for idx, claim in enumerate(claims):
            ev = evidence_map.get(claim.evidence_id)
            ev_id = ev.evidence_id if ev else claim.evidence_id

            if idx == 0:
                # Claim 1: SUPPORTED via Stage A (Deterministic)
                res = ContractVerificationResult(
                    verification_id=str(uuid.uuid4()),
                    claim_id=claim.claim_id,
                    label="SUPPORTED",
                    confidence=0.99,
                    stage_reached="A",
                    stage_a_label="SUPPORTED",
                    stage_b_label=None,
                    stage_b_raw_confidence=None,
                    contradiction_type=None,
                    normalized_code_agreement=True,
                    rationale=None,
                    evidence_id=ev_id,
                    escalated=False,
                )
            elif idx == 1:
                # Claim 2: DISCREPANCY via Stage B (escalated to C with contradiction type and rationale)
                res = ContractVerificationResult(
                    verification_id=str(uuid.uuid4()),
                    claim_id=claim.claim_id,
                    label="DISCREPANCY",
                    confidence=0.92,
                    stage_reached="C",
                    stage_a_label="SUPPORTED",
                    stage_b_label="DISCREPANCY",
                    stage_b_raw_confidence=0.48,
                    contradiction_type="wrong_entity",
                    normalized_code_agreement=False,
                    rationale="The evidence mentions Atorvastatin but clinical notes conflict with active prescription.",
                    evidence_id=ev_id,
                    escalated=True,
                )
            else:
                # Claim 3: INSUFFICIENT_EVIDENCE via Stage B
                res = ContractVerificationResult(
                    verification_id=str(uuid.uuid4()),
                    claim_id=claim.claim_id,
                    label="INSUFFICIENT_EVIDENCE",
                    confidence=0.88,
                    stage_reached="B",
                    stage_a_label=None,
                    stage_b_label="INSUFFICIENT_EVIDENCE",
                    stage_b_raw_confidence=0.85,
                    contradiction_type=None,
                    normalized_code_agreement=None,
                    rationale=None,
                    evidence_id=ev_id,
                    escalated=False,
                )
            results.append(res)
        return results

    cascade.verify_claims = mock_verify_claims
    return cascade


def test_verification_stage_success_persists_results_and_completes_job(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert successful verification invokes cascade, persists results, and completes job/document."""
    pdf_bytes = _generate_synthetic_pdf()
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256=pdf_sha256,
        storage_ref=storage_ref,
        original_filename="clinical_lab_report.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        patient_context_id="PT-VERIFY-001",
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    router = _make_mock_router_for_verification()
    normalizer = TerminologyNormalizer()
    cascade = _make_mock_cascade_for_verification()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-verify-worker-1",
        router=router,
        normalizer=normalizer,
        cascade=cascade,
        stop_after_stage=None,  # Run to completion!
    )

    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    completed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # 1. Pipeline Completion State Verification
    assert completed_job.status == JobStatus.COMPLETED
    assert completed_job.current_stage == "COMPLETED"
    assert completed_job.completed_at is not None

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.PENDING_REVIEW

    # 2. Database VerificationResult Record Persistence
    persisted_results = (
        db_session.query(DBVerificationResult)
        .all()
    )
    assert len(persisted_results) == 3

    # Linkage to Claim.id and ModelRun.id
    pipeline_claims = (
        db_session.query(Claim)
        .filter(Claim.document_id == doc.id)
        .all()
    )
    claim_ids = {c.id for c in pipeline_claims}

    for vr in persisted_results:
        assert vr.claim_id in claim_ids
        assert vr.model_run_id == completed_job.model_run_id
        assert 0.0 <= vr.calibrated_confidence <= 1.0

    # 3. Telemetry: Record VERIFICATION STARTED and COMPLETED events
    verify_events = (
        db_session.query(JobStageEvent)
        .filter(JobStageEvent.job_id == job.id, JobStageEvent.stage_name == "VERIFICATION")
        .order_by(JobStageEvent.created_at.asc())
        .all()
    )
    assert len(verify_events) == 2
    started_ev, completed_ev = verify_events[0], verify_events[1]

    assert started_ev.status == "STARTED"
    assert completed_ev.status == "COMPLETED"
    assert completed_ev.duration_ms is not None and completed_ev.duration_ms >= 0

    metrics = completed_ev.metrics
    assert metrics["total_claims_verified"] == 3
    assert metrics["num_supported"] == 1
    assert metrics["num_discrepancy"] == 1
    assert metrics["num_insufficient_evidence"] == 1
    assert metrics["num_escalated"] == 1
    assert metrics["stage_counts"]["A"] == 1
    assert metrics["stage_counts"]["B"] == 1
    assert metrics["stage_counts"]["C"] == 1

    # 4. Strict PHI-Free Check in Metrics
    metrics_str = str(metrics)
    assert "PT-VERIFY-001" not in metrics_str
    assert "Glucose" not in metrics_str
    assert "Atorvastatin" not in metrics_str
    assert "95" not in metrics_str


def test_verification_result_field_fidelity(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert field-level mapping fidelity between cascade outputs and database VerificationResult models."""
    pdf_bytes = _generate_synthetic_pdf()
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256=pdf_sha256,
        storage_ref=storage_ref,
        original_filename="sample.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    router = _make_mock_router_for_verification()
    normalizer = TerminologyNormalizer()
    cascade = _make_mock_cascade_for_verification()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-verify-fields-worker",
        router=router,
        normalizer=normalizer,
        cascade=cascade,
        stop_after_stage="VERIFICATION",
    )

    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    results = db_session.query(DBVerificationResult).all()
    results_by_stage = {r.verification_stage: r for r in results}

    # Verify Stage A outcome (SUPPORTED, no escalation, no contradiction_type, no rationale)
    res_a = results_by_stage[DBVerificationStage.STAGE_A]
    assert res_a.label == DBVerificationLabel.SUPPORTED
    assert res_a.calibrated_confidence == 0.99
    assert res_a.escalated is False
    assert res_a.contradiction_type is None
    assert res_a.rationale is None
    assert res_a.stage_a_details == {"label": "SUPPORTED"}

    # Verify Stage C outcome (REFUTED from DISCREPANCY, escalated=True, contradiction_type=ENTITY_MISMATCH, rationale populated)
    res_c = results_by_stage[DBVerificationStage.STAGE_C]
    assert res_c.label == DBVerificationLabel.REFUTED  # Actual DB enum mapping!
    assert res_c.calibrated_confidence == 0.92
    assert res_c.escalated is True
    assert res_c.contradiction_type == DBContradictionType.ENTITY_MISMATCH
    assert "Atorvastatin but clinical notes conflict" in res_c.rationale
    assert res_c.stage_c_details.get("rationale") == res_c.rationale

    # Verify Stage B outcome (INSUFFICIENT_EVIDENCE, escalated=False)
    res_b = results_by_stage[DBVerificationStage.STAGE_B]
    assert res_b.label == DBVerificationLabel.INSUFFICIENT_EVIDENCE
    assert res_b.calibrated_confidence == 0.88
    assert res_b.escalated is False
    assert res_b.contradiction_type is None


def test_verification_stage_idempotency_rerun_prevents_duplicate_results(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert running verification twice produces exactly one VerificationResult per claim without duplication."""
    pdf_bytes = _generate_synthetic_pdf()
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256=pdf_sha256,
        storage_ref=storage_ref,
        original_filename="sample.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    router = _make_mock_router_for_verification()
    normalizer = TerminologyNormalizer()
    cascade = _make_mock_cascade_for_verification()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-verify-idempotency",
        router=router,
        normalizer=normalizer,
        cascade=cascade,
        stop_after_stage="VERIFICATION",
    )

    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    # Pass 1: Run through verification
    worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    results_pass1 = db_session.query(DBVerificationResult).all()
    assert len(results_pass1) == 3

    # Pass 2: Rerun verification stage directly
    worker._orchestrate_verification_stage(
        session=db_session,
        job=claimed,
        document=doc,
        normalized_claims=worker.last_normalized_claims,
        extraction_output=worker.last_extraction_output,
        ocr_output=worker.last_ocr_output,
    )
    db_session.commit()

    results_pass2 = db_session.query(DBVerificationResult).all()
    assert len(results_pass2) == 3  # Zero duplicate records!


def test_verification_stage_partial_persistence_failure_retry_recovery(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert retry recovers cleanly if verification persistence fails midway."""
    pdf_bytes = _generate_synthetic_pdf()
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256=pdf_sha256,
        storage_ref=storage_ref,
        original_filename="sample.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    router = _make_mock_router_for_verification()
    normalizer = TerminologyNormalizer()
    cascade = _make_mock_cascade_for_verification()

    # Worker configured to stop after normalization first
    prep_worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-prep-worker",
        router=router,
        normalizer=normalizer,
        cascade=cascade,
        stop_after_stage="NORMALIZATION",
    )
    claimed = prep_worker.claim_job(session=db_session)
    db_session.commit()
    prep_worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    claims = db_session.query(Claim).filter(Claim.document_id == doc.id).all()
    assert len(claims) == 3

    # Inject 1 stale / partial VerificationResult
    stale_vr = DBVerificationResult(
        claim_id=claims[0].id,
        label=DBVerificationLabel.SUPPORTED,
        calibrated_confidence=0.50,
        verification_stage=DBVerificationStage.STAGE_A,
        provider_kind="mock_dev",
        model_run_id=claimed.model_run_id,
        payload={"label": "STALE_PARTIAL"},
    )
    db_session.add(stale_vr)
    db_session.commit()
    assert len(db_session.query(DBVerificationResult).all()) == 1

    # Retry verification stage
    exec_worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-retry-worker",
        router=router,
        normalizer=normalizer,
        cascade=cascade,
        stop_after_stage=None,
    )

    exec_worker._orchestrate_verification_stage(
        session=db_session,
        job=claimed,
        document=doc,
        normalized_claims=None,  # Reconstructed from DB!
        extraction_output=None,
        ocr_output=None,
    )
    db_session.commit()

    # Assert exactly 3 results exist (stale result removed, fresh results saved)
    final_results = db_session.query(DBVerificationResult).all()
    assert len(final_results) == 3
    assert not any(vr.payload.get("label") == "STALE_PARTIAL" for vr in final_results)


def test_verification_stage_failure_records_failed_telemetry_and_delegates_to_queue(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert unhandled cascade error records VERIFICATION / FAILED event and delegates to JobQueue."""
    pdf_bytes = _generate_synthetic_pdf()
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256=pdf_sha256,
        storage_ref=storage_ref,
        original_filename="confidential_patient.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        patient_context_id="MRN_TOP_SECRET_9999",
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    router = _make_mock_router_for_verification()
    normalizer = TerminologyNormalizer()
    cascade = VerificationCascade()

    def always_fail_verify(*args, **kwargs):
        raise RuntimeError("GPU cluster unreachable for MRN_TOP_SECRET_9999 in confidential_patient.pdf")

    cascade.verify_claims = always_fail_verify

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-verify-fail-worker",
        router=router,
        normalizer=normalizer,
        cascade=cascade,
        stop_after_stage=None,
    )

    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    failed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # Assert requeued via JobQueue
    assert failed_job.status == JobStatus.PENDING
    assert failed_job.attempts == 1

    # Document remains PROCESSING, NOT PENDING_REVIEW
    db_session.refresh(doc)
    assert doc.status == DocumentStatus.PROCESSING

    # Assert VERIFICATION / FAILED stage event exists with redacted error
    failed_ev = (
        db_session.query(JobStageEvent)
        .filter(
            JobStageEvent.job_id == job.id,
            JobStageEvent.stage_name == "VERIFICATION",
            JobStageEvent.status == "FAILED",
        )
        .first()
    )
    assert failed_ev is not None
    assert "MRN_TOP_SECRET_9999" not in failed_ev.error_message
    assert "confidential_patient.pdf" not in failed_ev.error_message
    assert "[REDACTED]" in failed_ev.error_message


def test_full_worker_completion_lifecycle(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """End-to-end lifecycle verification: OCR -> Extraction -> Normalization -> Verification -> COMPLETED."""
    pdf_bytes = _generate_synthetic_pdf()
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256=pdf_sha256,
        storage_ref=storage_ref,
        original_filename="complete_flow.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    router = _make_mock_router_for_verification()
    normalizer = TerminologyNormalizer()
    cascade = _make_mock_cascade_for_verification()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-full-worker",
        router=router,
        normalizer=normalizer,
        cascade=cascade,
    )

    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    completed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    assert completed_job.status == JobStatus.COMPLETED
    assert completed_job.completed_at is not None

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.PENDING_REVIEW

    # Assert stage events for all 4 core stages exist in sequence
    stage_names = [
        ev.stage_name
        for ev in (
            db_session.query(JobStageEvent)
            .filter(JobStageEvent.job_id == job.id, JobStageEvent.status == "COMPLETED")
            .order_by(JobStageEvent.created_at.asc())
            .all()
        )
    ]
    assert stage_names == ["OCR", "EXTRACTION", "NORMALIZATION", "VERIFICATION"]
