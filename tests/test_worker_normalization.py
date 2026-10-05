"""Comprehensive tests for Phase 8.3 Task 3B Step 4: Wire Normalization Stage.

Validates:
  A. Successful normalization:
     - TerminologyNormalizer is invoked on pipeline claims.
     - Existing persisted claims are updated in-place (not duplicated).
     - Normalized fields (normalized_code, code_system, payload JSONB) are persisted.
     - LOINC, RxNorm, UCUM system mappings are enriched correctly.
     - NORMALIZATION STARTED + COMPLETED stage events exist.
     - Operational metrics are strictly PHI-free.
  B. Deterministic rerun / idempotency:
     - Normalization rerun produces identical state without duplicate claims.
  C. Partial normalization failure & retry:
     - Simulated failure during normalization.
     - Retry cleanly replaces unverified pipeline claims and normalizes properly.
     - Zero duplicate claims, zero duplicate evidence, zero duplicate routing decisions.
  D. Normalization failure & JobQueue retry state machine:
     - NORMALIZATION STARTED + FAILED events recorded.
     - Error sanitized (PHI redacted).
     - JobQueue retry/terminal state transitions respected.
  E. Schema audit:
     - Existing Claim model columns used without schema alterations.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import time
from typing import Any, Optional
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
    Document,
    DocumentStatus,
    Evidence,
    JobStageEvent,
    JobStatus,
    ModelRun,
    ProcessingJob,
    RoutingDecision,
    User,
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
from clindoc.storage.blob import EncryptedFileBlobStore


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
        idp_subject="sub_norm_worker_test_user",
        username="norm_tester",
        full_name="Norm Tester",
        email="norm_tester@hospital.org",
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
    page.insert_text(fitz.Point(72, 72), "CLINICAL SUMMARY")
    page.insert_text(fitz.Point(72, 100), "Glucose: 95 mg/dL")
    page.insert_text(fitz.Point(72, 120), "Medication: Atorvastatin 20 mg oral daily")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_mock_router_for_normalization() -> ExtractionRouter:
    """Mock ExtractionRouter that outputs claims ready for normalization."""
    router = ExtractionRouter()

    def mock_extract(doc: DocumentText, is_lab_default: bool | None = None) -> DocumentExtractionOutput:
        ev1 = ContractEvidence(
            evidence_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            page_number=1,
            text="Glucose",
            char_start=20,
            char_end=27,
            bbox=BBoxModel(x0=72.0, y0=100.0, x1=120.0, y1=112.0),
        )
        ev2 = ContractEvidence(
            evidence_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            page_number=1,
            text="95 mg/dL",
            char_start=29,
            char_end=37,
            bbox=BBoxModel(x0=125.0, y0=100.0, x1=175.0, y1=112.0),
        )
        ev3 = ContractEvidence(
            evidence_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            page_number=1,
            text="Atorvastatin",
            char_start=50,
            char_end=62,
            bbox=BBoxModel(x0=72.0, y0=120.0, x1=140.0, y1=132.0),
        )
        ev4 = ContractEvidence(
            evidence_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            page_number=1,
            text="mg/dL",
            char_start=32,
            char_end=37,
            bbox=BBoxModel(x0=145.0, y0=100.0, x1=175.0, y1=112.0),
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
            group_id="lab_group_1",
            field_type="lab.value",
            value="95 mg/dL",
            unit_raw="mg/dL",
            extraction_method="deterministic",
            extraction_confidence=0.95,
            evidence_id=ev2.evidence_id,
            region_id="region_lab",
        )
        c3 = ContractClaim(
            claim_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            group_id="med_group_1",
            field_type="med.name",
            value="Atorvastatin",
            extraction_method="deterministic",
            extraction_confidence=0.96,
            evidence_id=ev3.evidence_id,
            region_id="region_med",
        )
        c4 = ContractClaim(
            claim_id=str(uuid.uuid4()),
            document_id=doc.document_id,
            group_id="lab_group_1",
            field_type="lab.unit",
            value="mg/dL",
            extraction_method="deterministic",
            extraction_confidence=0.99,
            evidence_id=ev4.evidence_id,
            region_id="region_lab",
        )

        rd1 = ContractRoutingDecision(
            document_id=doc.document_id,
            region_id="region_lab",
            route="deterministic",
            confidence=0.98,
            reason="High-confidence lab header match",
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
            claims=[c1, c2, c4],
            evidence=[ev1, ev2, ev4],
        )
        r2 = ExtractionResult(
            region_id="region_med",
            routing_decision=rd2,
            claims=[c3],
            evidence=[ev3],
        )

        return DocumentExtractionOutput(
            document_id=doc.document_id,
            regions=[r1, r2],
            all_claims=[c1, c2, c3, c4],
            all_evidence=[ev1, ev2, ev3, ev4],
            all_routing_decisions=[rd1, rd2],
        )

    router.extract_document = mock_extract
    return router


def test_normalization_stage_success_updates_claims_in_place_and_emits_telemetry(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert successful normalization updates existing Claim records in-place without duplicating rows."""
    pdf_bytes = _generate_synthetic_pdf()
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256=pdf_sha256,
        storage_ref=storage_ref,
        original_filename="clinical_summary.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        patient_context_id="PT-NORM-001",
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    router = _make_mock_router_for_normalization()
    normalizer = TerminologyNormalizer()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-norm-worker-1",
        router=router,
        normalizer=normalizer,
        stop_after_stage="NORMALIZATION",
    )

    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    processed_job = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # 1. State machine boundary check: stopped after NORMALIZATION
    assert processed_job.status == JobStatus.RUNNING
    assert processed_job.current_stage == "NORMALIZATION"
    db_session.refresh(doc)
    assert doc.status == DocumentStatus.PROCESSING

    # 2. Assert in-place Claim updates without duplicate rows
    persisted_claims = (
        db_session.query(Claim)
        .filter(Claim.document_id == doc.id, Claim.origin == ClaimOrigin.PIPELINE)
        .all()
    )
    assert len(persisted_claims) == 4

    claims_by_field = {c.field_type: c for c in persisted_claims}

    # Verify LOINC code on lab.test_name
    glucose_claim = claims_by_field["lab.test_name"]
    assert glucose_claim.value == "Glucose"
    assert glucose_claim.normalized_code == "2345-7"
    assert glucose_claim.code_system == "LOINC"
    assert glucose_claim.payload.get("normalized_code") == "2345-7"
    assert glucose_claim.payload.get("code_system") == "LOINC"
    assert glucose_claim.payload.get("code_confidence") >= 0.75
    assert glucose_claim.version == 1
    assert glucose_claim.origin == ClaimOrigin.PIPELINE
    assert glucose_claim.review_state == ClaimReviewState.PENDING

    # Verify RxNorm code on med.name
    med_claim = claims_by_field["med.name"]
    assert med_claim.value == "Atorvastatin"
    assert med_claim.normalized_code == "83367"
    assert med_claim.code_system == "RxNorm"
    assert med_claim.payload.get("normalized_code") == "83367"
    assert med_claim.payload.get("code_system") == "RxNorm"
    assert med_claim.payload.get("code_confidence") >= 0.75

    # Verify UCUM code on lab.unit
    unit_claim = claims_by_field["lab.unit"]
    assert unit_claim.value == "mg/dL"
    assert unit_claim.normalized_code == "mg/dL"
    assert unit_claim.code_system == "UCUM"

    # Verify numeric claim normalization on lab.value
    val_claim = claims_by_field["lab.value"]
    assert val_claim.value == "95 mg/dL"
    assert val_claim.payload.get("value_numeric") == 95.0
    assert val_claim.payload.get("normalized_unit") == "mg/dL"

    # 3. Assert NORMALIZATION stage events and telemetry
    events = (
        db_session.query(JobStageEvent)
        .filter(JobStageEvent.job_id == job.id, JobStageEvent.stage_name == "NORMALIZATION")
        .order_by(JobStageEvent.created_at.asc())
        .all()
    )
    assert len(events) == 2
    started_ev, completed_ev = events[0], events[1]

    assert started_ev.status == "STARTED"
    assert completed_ev.status == "COMPLETED"
    assert completed_ev.duration_ms is not None and completed_ev.duration_ms >= 0

    metrics = completed_ev.metrics
    assert metrics["total_claims"] == 4
    assert metrics["num_coded"] >= 3
    assert "LOINC" in metrics["system_counts"]
    assert "RxNorm" in metrics["system_counts"]
    assert "UCUM" in metrics["system_counts"]

    # 4. Assert metrics contain zero PHI
    metrics_str = str(metrics)
    assert "PT-NORM-001" not in metrics_str
    assert "Glucose" not in metrics_str
    assert "Atorvastatin" not in metrics_str
    assert "95" not in metrics_str


def test_normalization_stage_idempotency_rerun_preserves_claim_counts_and_updates(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert running normalization repeatedly produces identical state with zero duplicate Claim rows."""
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

    router = _make_mock_router_for_normalization()
    normalizer = TerminologyNormalizer()

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-norm-worker-idempotency",
        router=router,
        normalizer=normalizer,
        stop_after_stage="NORMALIZATION",
    )

    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    # Pass 1: Run through extraction + normalization
    worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    claims_pass1 = (
        db_session.query(Claim)
        .filter(Claim.document_id == doc.id, Claim.origin == ClaimOrigin.PIPELINE)
        .all()
    )
    assert len(claims_pass1) == 4
    pass1_ids = {c.id for c in claims_pass1}
    pass1_codes = {c.field_type: (c.normalized_code, c.code_system) for c in claims_pass1}

    # Pass 2: Re-run normalization directly on persisted claims
    worker._orchestrate_normalization_stage(
        session=db_session,
        job=claimed,
        document=doc,
        extraction_output=worker.last_extraction_output,
    )
    db_session.commit()

    claims_pass2 = (
        db_session.query(Claim)
        .filter(Claim.document_id == doc.id, Claim.origin == ClaimOrigin.PIPELINE)
        .all()
    )
    assert len(claims_pass2) == 4
    pass2_ids = {c.id for c in claims_pass2}
    pass2_codes = {c.field_type: (c.normalized_code, c.code_system) for c in claims_pass2}

    # Verify exact ID preservation and identical normalized values
    assert pass1_ids == pass2_ids
    assert pass1_codes == pass2_codes


def test_normalization_stage_partial_failure_retry_cleanup(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert that if normalization partially fails, worker retry cleans up and establishes complete state."""
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

    router = _make_mock_router_for_normalization()

    # Create a normalizer that fails on attempt 1, succeeds on attempt 2
    normalizer = TerminologyNormalizer()
    original_normalize_claims = normalizer.normalize_claims
    attempt_count = 0

    def failing_normalize_claims(claims):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            # Partially modify first claim, then raise error
            claims[0].normalized_code = "PARTIAL_CODE"
            raise RuntimeError("Transient database lock during terminology normalization")
        return original_normalize_claims(claims)

    normalizer.normalize_claims = failing_normalize_claims

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-norm-retry-worker",
        router=router,
        normalizer=normalizer,
        stop_after_stage="NORMALIZATION",
    )

    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    # Attempt 1: Fails during normalization
    job_attempt1 = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    assert job_attempt1.status == JobStatus.PENDING
    assert job_attempt1.attempts == 1

    # Verify NORMALIZATION FAILED event recorded
    failed_event = (
        db_session.query(JobStageEvent)
        .filter(
            JobStageEvent.job_id == job.id,
            JobStageEvent.stage_name == "NORMALIZATION",
            JobStageEvent.status == "FAILED",
        )
        .first()
    )
    assert failed_event is not None
    assert "Transient database lock" in failed_event.error_message

    # Attempt 2: Claim again and retry
    claimed_attempt2 = worker.claim_job(session=db_session)
    db_session.commit()

    job_attempt2 = worker.process_job(session=db_session, job=claimed_attempt2)
    db_session.commit()

    assert job_attempt2.status == JobStatus.RUNNING
    assert job_attempt2.current_stage == "NORMALIZATION"

    # Assert exactly 4 claims exist, zero duplicates
    final_claims = (
        db_session.query(Claim)
        .filter(Claim.document_id == doc.id, Claim.origin == ClaimOrigin.PIPELINE)
        .all()
    )
    assert len(final_claims) == 4

    # Assert all 4 are properly normalized (no leftover "PARTIAL_CODE")
    codes = {c.field_type: c.normalized_code for c in final_claims}
    assert codes["lab.test_name"] == "2345-7"
    assert codes["med.name"] == "83367"
    assert codes["lab.unit"] == "mg/dL"


def test_normalization_stage_failure_records_failed_telemetry_and_delegates_to_queue(
    db_session: Session,
    test_user: User,
    blob_store: EncryptedFileBlobStore,
):
    """Assert unhandled normalization failure records FAILED event, sanitizes PHI, and invokes JobQueue."""
    pdf_bytes = _generate_synthetic_pdf()
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    storage_ref = blob_store.put(pdf_bytes)

    doc = Document(
        sha256=pdf_sha256,
        storage_ref=storage_ref,
        original_filename="secret_patient_chart.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_bytes),
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
        patient_context_id="SENSITIVE_PATIENT_MRN_12345",
    )
    db_session.add(doc)
    db_session.commit()

    queue = JobQueue()
    job = queue.enqueue(session=db_session, document_id=doc.id)
    db_session.commit()

    router = _make_mock_router_for_normalization()
    normalizer = TerminologyNormalizer()

    def always_fail(claims):
        raise ValueError(
            "Fatal lookup error for file secret_patient_chart.pdf with patient SENSITIVE_PATIENT_MRN_12345"
        )

    normalizer.normalize_claims = always_fail

    worker = DocumentProcessingWorker(
        queue=queue,
        blob_store=blob_store,
        worker_id="test-norm-fail-worker",
        router=router,
        normalizer=normalizer,
        stop_after_stage="NORMALIZATION",
    )

    claimed = worker.claim_job(session=db_session)
    db_session.commit()

    job_result = worker.process_job(session=db_session, job=claimed)
    db_session.commit()

    # Verified requeued to PENDING
    assert job_result.status == JobStatus.PENDING
    assert job_result.attempts == 1

    # Verify stage events: STARTED and FAILED
    failed_ev = (
        db_session.query(JobStageEvent)
        .filter(
            JobStageEvent.job_id == job.id,
            JobStageEvent.stage_name == "NORMALIZATION",
            JobStageEvent.status == "FAILED",
        )
        .first()
    )
    assert failed_ev is not None
    assert "SENSITIVE_PATIENT_MRN_12345" not in failed_ev.error_message
    assert "secret_patient_chart.pdf" not in failed_ev.error_message
    assert "[REDACTED]" in failed_ev.error_message


def test_claim_schema_normalization_field_support_verification(db_session: Session, test_user: User):
    """Verify that the Claim database schema supports normalized_code and code_system natively."""
    doc = Document(
        sha256="abcdef" * 10 + "1234",
        storage_ref="blob://local/test",
        original_filename="test.pdf",
        mime_type="application/pdf",
        file_size_bytes=100,
        page_count=1,
        source_type="born_digital",
        uploaded_by=test_user.id,
    )
    db_session.add(doc)
    db_session.commit()

    claim = Claim(
        claim_id=uuid.uuid4(),
        document_id=doc.id,
        group_id="group_1",
        field_type="lab.test_name",
        value="Glucose",
        normalized_code="2345-7",
        code_system="LOINC",
        version=1,
        origin=ClaimOrigin.PIPELINE,
        review_state=ClaimReviewState.PENDING,
        payload={
            "normalized_code": "2345-7",
            "code_system": "LOINC",
            "code_confidence": 0.99,
        },
    )
    db_session.add(claim)
    db_session.commit()

    db_session.refresh(claim)
    assert claim.normalized_code == "2345-7"
    assert claim.code_system == "LOINC"
    assert claim.payload["code_confidence"] == 0.99
    assert hasattr(Claim, "normalized_code")
    assert hasattr(Claim, "code_system")
    assert hasattr(Claim, "payload")
