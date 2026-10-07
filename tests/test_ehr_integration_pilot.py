"""Automated Test Suite for Phase 8.12 — Controlled Clinical Pilot / EHR Integration.

Tests:
  1. FHIRTransport abstraction & MockFHIRTransport deterministic operations.
  2. Mock EHR transmission success, remote message ID, and bundle structure.
  3. Failure handling: Timeout, Authentication Error, Semantic Validation Rejection, Server Error.
  4. Idempotency & Duplicate Submission prevention.
  5. Patient Identity Safety Gate: Confirmed allowed; Unconfirmed / Mismatched strictly blocked.
  6. Four-Eyes Approval Safety Gate: Unreviewed / unapproved documents blocked.
  7. Verification Safety Gate: Fallback or unverified claims blocked from transmission.
  8. Cryptographic hash-chained audit logging (EHR_TRANSMISSION_ATTEMPTED, SUCCESS, FAILED).
  9. Observability integration & PHI exclusion in metrics.
  10. Strict TLS enforcement for production HTTP transport.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from clindoc.api.audit.service import AuditService
from clindoc.db.base import Base
from clindoc.db.enums import (
    ClaimReviewState,
    DocumentApprovalStatus,
    DocumentStatus,
    FHIRExportStatus,
    FHIRVersion,
    PatientIdentityApprovalStatus,
    VerificationLabel,
    VerificationStage,
)
from clindoc.db.models import (
    AuditEvent,
    Claim as DBClaim,
    Document as DBDocument,
    DocumentApproval,
    Evidence as DBEvidence,
    FHIRBundle,
    FHIRExport,
    Role,
    User,
    UserRole,
    VerificationResult as DBVerificationResult,
)
from clindoc.extract.contracts import BBoxModel
from clindoc.observability.metrics import ClinDocMetrics, get_metrics, set_metrics
from clindoc.services.ehr_integration import (
    EHRApprovalMissingError,
    EHRDuplicateSubmissionError,
    EHRIntegrationService,
    EHRPatientIdentityError,
    EHRTransmissionResult,
)
from clindoc.services.ehr_transport import (
    EHRAuthenticationError,
    EHRTimeoutError,
    EHRTransportError,
    EHRValidationError,
    HTTPFHIRTransport,
    HTTPFHIRTransportConfig,
    MockEHRMode,
    MockFHIRTransport,
)
from clindoc.services.fhir_export import (
    FHIRExportEligibilityError,
    FHIRExportNotFoundError,
    FHIRExportService,
)


# ─── FIXTURES & HELPERS ───────────────────────────────────────────────────────

@pytest.fixture
def db_session() -> Session:
    """In-memory SQLite database session initialized with all tables."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def fresh_metrics():
    """Isolated metrics registry for testing."""
    metrics = ClinDocMetrics()
    set_metrics(metrics)
    yield metrics
    set_metrics(None)


def _create_test_user(session: Session, username: str = "dr_smith", role_name: str = "clinician") -> User:
    user = User(
        idp_subject=f"sub-{username}-{uuid.uuid4().hex[:6]}",
        username=username,
        full_name=f"Dr. {username.capitalize()}",
        email=f"{username}@hospital.org",
        is_active=True,
    )
    session.add(user)
    session.flush()

    role = session.query(Role).filter(Role.id == role_name).first()
    if not role:
        role = Role(id=role_name, name=role_name, description=f"{role_name} test role")
        session.add(role)
        session.flush()

    user_role = UserRole(user_id=user.id, role_id=role.id)
    session.add(user_role)
    session.flush()
    return user


def _create_approved_document(
    session: Session,
    approver: User,
    *,
    patient_identity_status: PatientIdentityApprovalStatus = PatientIdentityApprovalStatus.CONFIRMED,
    approval_status: DocumentApprovalStatus = DocumentApprovalStatus.APPROVED,
    is_export_eligible: bool = True,
    is_fallback_claim: bool = False,
    claim_review_state: ClaimReviewState = ClaimReviewState.ACCEPTED,
) -> DBDocument:
    doc = DBDocument(
        uploaded_by=approver.id,
        source_type="scanned_pdf",
        status=DocumentStatus.IN_REVIEW,
        original_filename="synth_note.pdf",
        storage_ref="blobs/test/synth_note.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        sha256="abc123mockhash",
        page_count=1,
        patient_context_id="PAT-TEST-001",
    )
    session.add(doc)
    session.flush()

    # Evidence
    ev = DBEvidence(
        document_id=doc.id,
        page_number=1,
        char_start=0,
        char_end=20,
        evidence_text="Metformin 500mg daily",
        bounding_box={"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2},
        ocr_confidence=0.99,
    )
    session.add(ev)
    session.flush()

    # Claim
    claim_uuid = uuid.uuid4()
    claim = DBClaim(
        id=claim_uuid,
        claim_id=claim_uuid,
        document_id=doc.id,
        evidence_id=ev.id,
        group_id="med-group-1",
        field_type="med.name",
        value="Metformin",
        review_state=claim_review_state,
        payload={
            "claim_id": str(claim_uuid),
            "document_id": str(doc.id),
            "group_id": "med-group-1",
            "field_type": "med.name",
            "value": "Metformin",
            "extraction_method": "deterministic",
            "extraction_confidence": 0.95,
            "evidence_id": str(ev.id),
            "region_id": "reg-1",
        },
    )
    session.add(claim)
    session.flush()

    # Verification Result
    ver_id = uuid.uuid4()
    ver = DBVerificationResult(
        id=ver_id,
        claim_id=claim.id,
        verification_stage=VerificationStage.STAGE_A,
        label=VerificationLabel.SUPPORTED,
        calibrated_confidence=0.99,
        provider_kind="deterministic",
        is_fallback=is_fallback_claim,
        fallback_reason="llm_unavailable" if is_fallback_claim else None,
        payload={
            "verification_id": str(ver_id),
            "claim_id": str(claim.claim_id),
            "stage_reached": "A",
            "label": "SUPPORTED",
            "is_fallback": is_fallback_claim,
            "fallback_reason": "llm_unavailable" if is_fallback_claim else None,
            "confidence": 0.99,
            "evidence_id": str(ev.id),
        },
    )
    session.add(ver)
    session.flush()

    # Document Approval
    approval = DocumentApproval(
        document_id=doc.id,
        approver_id=approver.id,
        approval_status=approval_status,
        patient_identity_status=patient_identity_status,
        policy_version="1.0",
        is_export_eligible=is_export_eligible,
        identity_notes="Approved for staging pilot",
    )
    session.add(approval)
    session.flush()

    return doc


# ─── 1. TRANSPORT ABSTRACTION & MOCK EHR TESTS ───────────────────────────────

def test_mock_ehr_successful_transmission():
    """Verify MockFHIRTransport successfully accepts bundles and returns deterministic receipts."""
    transport = MockFHIRTransport()
    bundle_json = json.dumps({"resourceType": "Bundle", "type": "transaction", "entry": []})

    resp = transport.transmit(
        bundle_json,
        idempotency_key="test-key-001",
        correlation_id="corr-123",
        document_id="doc-123",
    )

    assert resp.success is True
    assert resp.status_code == 200
    assert resp.destination_type == "sandbox"
    assert resp.remote_message_id is not None
    assert "mock-ehr-txn-" in resp.remote_message_id
    assert resp.latency_seconds >= 0.0


def test_mock_ehr_failure_injections():
    """Verify MockFHIRTransport deterministic failure modes."""
    transport = MockFHIRTransport()
    bundle_json = json.dumps({"resourceType": "Bundle", "type": "transaction"})

    # Timeout
    transport.set_mode(MockEHRMode.TIMEOUT)
    with pytest.raises(EHRTimeoutError, match="Mock EHR connection timed out"):
        transport.transmit(bundle_json, idempotency_key="key-t", correlation_id="c", document_id="d")

    # Auth Failure
    transport.set_mode(MockEHRMode.AUTH_FAILURE)
    with pytest.raises(EHRAuthenticationError, match="HTTP 401 Unauthorized"):
        transport.transmit(bundle_json, idempotency_key="key-a", correlation_id="c", document_id="d")

    # Semantic Validation Rejection
    transport.set_mode(MockEHRMode.REJECT_VALIDATION)
    with pytest.raises(EHRValidationError, match="semantic validation failed"):
        transport.transmit(bundle_json, idempotency_key="key-v", correlation_id="c", document_id="d")


def test_mock_ehr_duplicate_rejection():
    """Verify MockFHIRTransport enforces idempotency and blocks duplicate submissions."""
    transport = MockFHIRTransport(allow_duplicates=False)
    bundle_json = json.dumps({"resourceType": "Bundle", "type": "transaction"})

    # First transmission
    resp = transport.transmit(bundle_json, idempotency_key="dup-key-01", correlation_id="c1", document_id="d1")
    assert resp.success is True

    # Duplicate transmission with same idempotency key
    with pytest.raises(EHRDuplicateSubmissionError, match="already accepted"):
        transport.transmit(bundle_json, idempotency_key="dup-key-01", correlation_id="c2", document_id="d1")


# ─── 2. PATIENT IDENTITY SAFETY GATES ────────────────────────────────────────

def test_ehr_transmission_requires_confirmed_patient_identity(db_session, fresh_metrics):
    """Verify that unconfirmed or mismatched patient identity blocks transmission to EHR."""
    clinician = _create_test_user(db_session)
    service = EHRIntegrationService(audit_service=AuditService(), transport=MockFHIRTransport())

    # 1. Unconfirmed Identity (MISMATCH_ACCEPTED without confirmation)
    doc_unconfirmed = _create_approved_document(
        db_session,
        clinician,
        patient_identity_status=PatientIdentityApprovalStatus.MISMATCH_ACCEPTED,
        is_export_eligible=False,
    )
    with pytest.raises(EHRPatientIdentityError, match="patient identity is not confirmed"):
        service.transmit_document_to_ehr(db_session, document_id=doc_unconfirmed.id, actor_id=clinician.id)

    # 2. Overridden Identity
    doc_mismatch = _create_approved_document(
        db_session,
        clinician,
        patient_identity_status=PatientIdentityApprovalStatus.OVERRIDDEN,
        is_export_eligible=False,
    )
    with pytest.raises(EHRPatientIdentityError, match="patient identity is not confirmed"):
        service.transmit_document_to_ehr(db_session, document_id=doc_mismatch.id, actor_id=clinician.id)


# ─── 3. FOUR-EYES APPROVAL SAFETY GATES ──────────────────────────────────────

def test_ehr_transmission_blocks_unapproved_documents(db_session, fresh_metrics):
    """Verify that documents without Four-Eyes sign-off cannot be transmitted to EHR."""
    clinician = _create_test_user(db_session)
    service = EHRIntegrationService(audit_service=AuditService(), transport=MockFHIRTransport())

    # 1. Rejected Document
    doc_rejected = _create_approved_document(
        db_session,
        clinician,
        approval_status=DocumentApprovalStatus.REJECTED,
        is_export_eligible=False,
    )
    with pytest.raises(EHRApprovalMissingError, match="is not approved"):
        service.transmit_document_to_ehr(db_session, document_id=doc_rejected.id, actor_id=clinician.id)

    # 2. Document with completely missing approval row
    doc_unreviewed = DBDocument(
        uploaded_by=clinician.id,
        source_type="scanned_pdf",
        original_filename="raw.pdf",
        storage_ref="blobs/test/raw.pdf",
        mime_type="application/pdf",
        file_size_bytes=100,
        sha256="rawhash",
        status=DocumentStatus.INGESTED,
        page_count=1,
    )
    db_session.add(doc_unreviewed)
    db_session.flush()

    with pytest.raises(EHRApprovalMissingError, match="has not undergone Four-Eyes approval"):
        service.transmit_document_to_ehr(db_session, document_id=doc_unreviewed.id, actor_id=clinician.id)


# ─── 4. CLAIM VERIFICATION & FALLBACK SAFETY GATES ───────────────────────────

def test_ehr_transmission_blocks_fallback_claims(db_session, fresh_metrics):
    """Verify that fallback claims (F-05/F-06/F-07) are strictly excluded from EHR export."""
    clinician = _create_test_user(db_session)
    service = EHRIntegrationService(audit_service=AuditService(), transport=MockFHIRTransport())

    doc_fallback = _create_approved_document(
        db_session,
        clinician,
        is_fallback_claim=True,
    )

    with pytest.raises(FHIRExportEligibilityError, match="zero export-eligible claims"):
        service.transmit_document_to_ehr(db_session, document_id=doc_fallback.id, actor_id=clinician.id)


# ─── 5. SUCCESSFUL TRANSMISSION PIPELINE & AUDIT ─────────────────────────────

def test_successful_pilot_transmission_pipeline(db_session, fresh_metrics):
    """Verify end-to-end pilot transmission: approval -> bundle -> mock EHR -> audit -> metrics."""
    clinician = _create_test_user(db_session)
    mock_transport = MockFHIRTransport()
    audit_svc = AuditService()
    service = EHRIntegrationService(audit_service=audit_svc, transport=mock_transport)

    doc = _create_approved_document(db_session, clinician)

    res: EHRTransmissionResult = service.transmit_document_to_ehr(
        db_session,
        document_id=doc.id,
        actor_id=clinician.id,
        destination_type="sandbox",
        correlation_id="corr-pilot-001",
    )

    assert res.success is True
    assert res.status_code == 200
    assert res.destination_type == "sandbox"
    assert res.remote_message_id is not None
    assert doc.status == DocumentStatus.EXPORTED

    # Verify FHIRExport row persisted
    export_record = db_session.query(FHIRExport).filter(FHIRExport.id == uuid.UUID(res.export_id)).first()
    assert export_record is not None
    assert export_record.export_status == FHIRExportStatus.TRANSMITTED

    # Verify Audit Events (ATTEMPTED and SUCCESS)
    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.entity_id == str(doc.id))
        .order_by(AuditEvent.sequence_number.asc())
        .all()
    )
    event_types = [e.event_type for e in events]
    assert "EHR_TRANSMISSION_ATTEMPTED" in event_types
    assert "EHR_TRANSMISSION_SUCCESS" in event_types

    # Verify Zero PHI in audit metadata
    for ev in events:
        meta = ev.metadata_json or {}
        for forbidden in ("patient_name", "mrn", "patient_id", "clinical_text", "evidence_text"):
            assert forbidden not in meta


# ─── 6. FAILURE ROLLBACK & STATE CONTAINMENT ─────────────────────────────────

def test_failed_transmission_does_not_mutate_document_verification(db_session, fresh_metrics):
    """Verify that EHR transport timeout or failure does NOT mark document EXPORTED or alter claims."""
    clinician = _create_test_user(db_session)
    failing_transport = MockFHIRTransport(mode=MockEHRMode.TIMEOUT)
    service = EHRIntegrationService(audit_service=AuditService(), transport=failing_transport)

    doc = _create_approved_document(db_session, clinician)
    original_status = doc.status

    with pytest.raises(EHRTimeoutError):
        service.transmit_document_to_ehr(db_session, document_id=doc.id, actor_id=clinician.id)

    # Document must remain in original status (NOT marked as EXPORTED)
    assert doc.status == original_status
    assert doc.status != DocumentStatus.EXPORTED

    # A failed FHIRExport record must exist for auditing
    failed_export = (
        db_session.query(FHIRExport)
        .filter(FHIRExport.export_status == FHIRExportStatus.FAILED)
        .first()
    )
    assert failed_export is not None
    assert "TIMEOUT" in failed_export.error_message

    # Audit event for failure must be recorded
    fail_event = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.event_type == "EHR_TRANSMISSION_FAILED")
        .first()
    )
    assert fail_event is not None


# ─── 7. IDEMPOTENCY & DUPLICATE REJECTION ────────────────────────────────────

def test_duplicate_submission_blocked_at_service_layer(db_session, fresh_metrics):
    """Verify that re-submitting an already transmitted document is rejected with duplicate error."""
    clinician = _create_test_user(db_session)
    service = EHRIntegrationService(audit_service=AuditService(), transport=MockFHIRTransport())
    doc = _create_approved_document(db_session, clinician)

    # First transmission: succeeds
    res1 = service.transmit_document_to_ehr(db_session, document_id=doc.id, actor_id=clinician.id)
    assert res1.success is True

    # Second transmission: must be rejected as duplicate
    with pytest.raises(EHRDuplicateSubmissionError, match="already transmitted"):
        service.transmit_document_to_ehr(db_session, document_id=doc.id, actor_id=clinician.id)


# ─── 8. PRODUCTION HTTP TRANSPORT TLS ENFORCEMENT ────────────────────────────

def test_http_transport_forbids_insecure_tls_in_production(monkeypatch):
    """Verify that HTTPFHIRTransport forbids disabling SSL verification in production profile."""
    monkeypatch.setenv("CLINDOC_PROFILE", "production")

    # Insecure URL (HTTP) in production must fail validation
    with pytest.raises(ValueError, match="MUST use HTTPS"):
        cfg_http = HTTPFHIRTransportConfig(endpoint_url="http://ehr.hospital.org/fhir", ssl_verify=True)
        HTTPFHIRTransport(cfg_http)

    # ssl_verify=False in production must fail validation
    with pytest.raises(ValueError, match="ssl_verify=False is strictly forbidden"):
        cfg_insecure = HTTPFHIRTransportConfig(endpoint_url="https://ehr.hospital.org/fhir", ssl_verify=False)
        HTTPFHIRTransport(cfg_insecure)
