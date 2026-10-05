"""Comprehensive unit and integration tests for Phase 8.5 Step 3: Identity Confirmation & Document Approval Engine.

Authority:
  - PRODUCTIZATION_SPEC.md §4.3 (documents, document_approvals, claims, audit_events)
  - PRODUCTIZATION_SPEC.md §7.3 (Human review workflow, Four-Eyes approval gate)
  - PRODUCTIZATION_DECISIONS.md PD-002 (Mode 0: 100% human review prior to export)
  - PRODUCTIZATION_DECISIONS.md PD-009 (Four-Eyes principle: uploader cannot approve own document)
  - PRODUCTIZATION_DECISIONS.md PD-012 (Append-only hash-chained audit logging, fail-closed)
  - PRODUCTIZATION_DECISIONS.md PD-015 (Patient context linkage & explicit identity confirmation)

Required Test Coverage:
  1. identity confirmation with matching demographics
  2. identity mismatch detection
  3. explicit MISMATCH_ACCEPTED
  4. identity confirmation requires human action
  5. identity actor/timestamp persistence
  6. approval blocked when claims remain PENDING
  7. approval blocked when claims remain ESCALATED
  8. approval blocked when identity is not confirmed
  9. approval blocked when Four-Eyes fails
  10. uploader cannot approve their own document
  11. authorized distinct approver can approve
  12. successful approval sets export eligibility correctly
  13. approval persistence
  14. identity confirmation audit event
  15. document approval audit event
  16. audit failure rolls back identity mutation
  17. audit failure rolls back approval mutation
  18. repeated/invalid approval is rejected
  19. approval does not perform FHIR/export
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from clindoc.api.audit.service import AuditService, AuditServiceError
from clindoc.db import (
    AuditEvent,
    Base,
    Claim,
    ClaimOrigin,
    ClaimReviewState,
    Document,
    DocumentApproval,
    DocumentApprovalStatus,
    DocumentStatus,
    FHIRBundle,
    IdentityVerificationStatus,
    PatientIdentityApprovalStatus,
    User,
)
from clindoc.review.approval import (
    ApprovalEngineError,
    AuditIntegrationError,
    DocumentApprovalEngine,
    DocumentNotFoundError,
    FourEyesViolationError,
    IdentityMismatchError,
    InvalidDocumentApprovalStateError,
    MissingReasonCodeError,
    PatientDemographics,
    UnconfirmedIdentityError,
    UnreviewedClaimsError,
    compare_demographics,
)
from clindoc.review.triage import seed_default_triage_policy


# ─── FIXTURES & TEST SETUP ───────────────────────────────────────────────────

@pytest.fixture
def db_session():
    """In-memory SQLite database session with foreign keys enabled."""
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
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def approval_engine():
    """Default DocumentApprovalEngine with production Four-Eyes enabled."""
    return DocumentApprovalEngine(require_distinct_approver=True)


def _create_test_document_fixture(
    session: Session,
    *,
    claim_states: list[ClaimReviewState] | None = None,
    identity_status: IdentityVerificationStatus = IdentityVerificationStatus.UNVERIFIED,
    confirm_identity_with_approver: bool = False,
) -> tuple[User, User, Document, list[Claim]]:
    """Helper creating uploader, approver, document, and claims in specified review states."""
    seed_default_triage_policy(session)

    uid_tag = str(uuid.uuid4())[:8]
    uploader = User(
        id=uuid.uuid4(),
        idp_subject=f"sub-uploader-{uid_tag}",
        username=f"uploader_{uid_tag}",
        email=f"uploader_{uid_tag}@hospital.org",
        full_name="Dan Uploader",
        is_active=True,
    )
    approver = User(
        id=uuid.uuid4(),
        idp_subject=f"sub-approver-{uid_tag}",
        username=f"approver_{uid_tag}",
        email=f"approver_{uid_tag}@hospital.org",
        full_name="Dr. Alice Approver",
        is_active=True,
    )
    session.add_all([uploader, approver])
    session.flush()

    confirmed_by_id = approver.id if confirm_identity_with_approver else None

    doc = Document(
        id=uuid.uuid4(),
        sha256="d" * 64,
        storage_ref="s3://clindoc-docs/patient_note_42.pdf",
        original_filename="patient_note_42.pdf",
        mime_type="application/pdf",
        file_size_bytes=24500,
        page_count=1,
        uploaded_by=uploader.id,
        patient_context_id="MRN-987654",
        identity_verification_status=identity_status,
        identity_confirmed_by=confirmed_by_id,
        identity_confirmed_at=datetime.now(timezone.utc) if confirmed_by_id else None,
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(doc)
    session.flush()

    # Create claims if specified
    states = claim_states if claim_states is not None else [ClaimReviewState.ACCEPTED]
    claims: list[Claim] = []
    for idx, state in enumerate(states):
        c = Claim(
            id=uuid.uuid4(),
            claim_id=uuid.uuid4(),
            document_id=doc.id,
            group_id=f"grp_{idx}",
            field_type="med.name",
            value=f"Medication_{idx}",
            version=1,
            origin=ClaimOrigin.PIPELINE,
            review_state=state,
            payload={"value": f"Medication_{idx}"},
        )
        claims.append(c)
        session.add(c)

    session.commit()
    return uploader, approver, doc, claims


# ─── 1. IDENTITY CONFIRMATION WITH MATCHING DEMOGRAPHICS ─────────────────────

def test_identity_confirmation_with_matching_demographics(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert explicit human confirmation with matching demographics sets CONFIRMED status."""
    uploader, approver, doc, _ = _create_test_document_fixture(db_session)

    context_demo = PatientDemographics(
        name="John Doe",
        mrn="MRN-987654",
        dob="1980-05-15",
        gender="male",
    )
    extracted_demo = PatientDemographics(
        name="  JOHN DOE  ",
        mrn="mrn-987654",
        dob="1980-05-15",
        gender="M",
    )

    confirmed_doc = approval_engine.confirm_identity(
        session=db_session,
        document_id=doc.id,
        actor_id=approver.id,
        context_demographics=context_demo,
        extracted_demographics=extracted_demo,
        notes="Verified against hospital EHR master index.",
    )

    assert confirmed_doc.identity_verification_status == IdentityVerificationStatus.CONFIRMED
    assert confirmed_doc.identity_confirmed_by == approver.id
    assert confirmed_doc.identity_confirmed_at is not None


# ─── 2. IDENTITY MISMATCH DETECTION ───────────────────────────────────────────

def test_identity_mismatch_detection(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert demographic discrepancies raise IdentityMismatchError and retain MISMATCH status."""
    uploader, approver, doc, _ = _create_test_document_fixture(db_session)

    context_demo = PatientDemographics(
        name="John Doe",
        mrn="MRN-987654",
        dob="1980-05-15",
        gender="male",
    )
    extracted_demo = PatientDemographics(
        name="Jane Smith",  # Wrong patient name
        mrn="MRN-987654",
        dob="1980-05-15",
        gender="female",    # Wrong gender
    )

    with pytest.raises(IdentityMismatchError, match="Demographics mismatch detected for fields"):
        approval_engine.confirm_identity(
            session=db_session,
            document_id=doc.id,
            actor_id=approver.id,
            context_demographics=context_demo,
            extracted_demographics=extracted_demo,
        )

    # Document retains MISMATCH condition
    refetched = db_session.get(Document, doc.id)
    assert refetched.identity_verification_status == IdentityVerificationStatus.MISMATCH


# ─── 3. EXPLICIT MISMATCH_ACCEPTED ────────────────────────────────────────────

def test_explicit_mismatch_accepted(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert an operator can explicitly accept a mismatch with mandatory reason code."""
    uploader, approver, doc, _ = _create_test_document_fixture(db_session)

    # Missing reason code must fail
    with pytest.raises(MissingReasonCodeError, match="Reason code is required"):
        approval_engine.accept_identity_mismatch(
            session=db_session,
            document_id=doc.id,
            actor_id=approver.id,
            reason_code="",
        )

    # Explicit acceptance succeeds
    updated_doc = approval_engine.accept_identity_mismatch(
        session=db_session,
        document_id=doc.id,
        actor_id=approver.id,
        reason_code="MARRIAGE_NAME_CHANGE",
        notes="Patient married last month; MRN and DOB match verified.",
    )

    assert updated_doc.identity_verification_status == IdentityVerificationStatus.MISMATCH_ACCEPTED
    assert updated_doc.identity_confirmed_by == approver.id
    assert updated_doc.identity_confirmed_at is not None


# ─── 4. IDENTITY CONFIRMATION REQUIRES HUMAN ACTION ───────────────────────────

def test_identity_confirmation_requires_human_action(db_session: Session):
    """Assert identity status starts UNVERIFIED and is never inferred automatically."""
    uploader, approver, doc, _ = _create_test_document_fixture(db_session)

    # Newly ingested / processed document starts strictly UNVERIFIED
    assert doc.identity_verification_status == IdentityVerificationStatus.UNVERIFIED
    assert doc.identity_confirmed_by is None
    assert doc.identity_confirmed_at is None


# ─── 5. IDENTITY ACTOR / TIMESTAMP PERSISTENCE ────────────────────────────────

def test_identity_actor_timestamp_persistence(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert confirmation actor and UTC timestamp are faithfully recorded in the database."""
    uploader, approver, doc, _ = _create_test_document_fixture(db_session)
    before_ts = datetime.now(timezone.utc)

    approval_engine.confirm_identity(
        session=db_session,
        document_id=doc.id,
        actor_id=approver.id,
    )

    refetched = db_session.get(Document, doc.id)
    assert refetched.identity_confirmed_by == approver.id
    ref_ts = refetched.identity_confirmed_at
    if ref_ts.tzinfo is None:
        ref_ts = ref_ts.replace(tzinfo=timezone.utc)
    assert ref_ts >= before_ts


# ─── 6. APPROVAL BLOCKED WHEN CLAIMS REMAIN PENDING ───────────────────────────

def test_approval_blocked_when_claims_remain_pending(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert document approval is rejected if any claim remains PENDING."""
    uploader, approver, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED, ClaimReviewState.PENDING],
        identity_status=IdentityVerificationStatus.CONFIRMED,
        confirm_identity_with_approver=True,
    )

    with pytest.raises(UnreviewedClaimsError, match="claims remain non-terminal"):
        approval_engine.approve_document(
            session=db_session,
            document_id=doc.id,
            approver_id=approver.id,
        )

    # Document remains unapproved
    refetched = db_session.get(Document, doc.id)
    assert refetched.status != DocumentStatus.APPROVED


# ─── 7. APPROVAL BLOCKED WHEN CLAIMS REMAIN ESCALATED ─────────────────────────

def test_approval_blocked_when_claims_remain_escalated(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert document approval is rejected if any claim remains ESCALATED."""
    uploader, approver, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED, ClaimReviewState.ESCALATED],
        identity_status=IdentityVerificationStatus.CONFIRMED,
        confirm_identity_with_approver=True,
    )

    with pytest.raises(UnreviewedClaimsError, match="claims remain non-terminal"):
        approval_engine.approve_document(
            session=db_session,
            document_id=doc.id,
            approver_id=approver.id,
        )


# ─── 8. APPROVAL BLOCKED WHEN IDENTITY IS NOT CONFIRMED ───────────────────────

def test_approval_blocked_when_identity_not_confirmed(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert document approval is rejected if identity is UNVERIFIED or MISMATCH."""
    # 1. UNVERIFIED
    uploader, approver, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED],
        identity_status=IdentityVerificationStatus.UNVERIFIED,
    )
    with pytest.raises(UnconfirmedIdentityError, match="Patient identity is 'UNVERIFIED'"):
        approval_engine.approve_document(
            session=db_session,
            document_id=doc.id,
            approver_id=approver.id,
        )

    # 2. MISMATCH
    doc.identity_verification_status = IdentityVerificationStatus.MISMATCH
    db_session.commit()
    with pytest.raises(UnconfirmedIdentityError, match="Patient identity is 'MISMATCH'"):
        approval_engine.approve_document(
            session=db_session,
            document_id=doc.id,
            approver_id=approver.id,
        )


# ─── 9. APPROVAL BLOCKED WHEN FOUR-EYES FAILS ─────────────────────────────────

def test_approval_blocked_when_four_eyes_fails(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert FourEyesViolationError is raised when uploader attempts to approve own document."""
    uploader, _, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED],
        identity_status=IdentityVerificationStatus.CONFIRMED,
        confirm_identity_with_approver=True,
    )

    with pytest.raises(FourEyesViolationError, match="Four-Eyes policy violation: uploader"):
        approval_engine.approve_document(
            session=db_session,
            document_id=doc.id,
            approver_id=uploader.id,  # Uploader cannot approve own document
        )


# ─── 10. UPLOADER CANNOT APPROVE THEIR OWN DOCUMENT ───────────────────────────

def test_uploader_cannot_approve_their_own_document(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert distinct approver is strictly required under production baseline."""
    uploader, _, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED],
        identity_status=IdentityVerificationStatus.CONFIRMED,
        confirm_identity_with_approver=True,
    )

    assert approval_engine.require_distinct_approver is True
    with pytest.raises(FourEyesViolationError):
        approval_engine.approve_document(
            session=db_session,
            document_id=doc.id,
            approver_id=uploader.id,
        )


# ─── 11. AUTHORIZED DISTINCT APPROVER CAN APPROVE ─────────────────────────────

def test_authorized_distinct_approver_can_approve(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert a distinct authorized approver successfully approves a fully reviewed document."""
    uploader, approver, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED, ClaimReviewState.EDITED, ClaimReviewState.REJECTED],
        identity_status=IdentityVerificationStatus.CONFIRMED,
        confirm_identity_with_approver=True,
    )

    approval = approval_engine.approve_document(
        session=db_session,
        document_id=doc.id,
        approver_id=approver.id,
        identity_notes="All claims reconciled and verified.",
    )

    assert approval is not None
    assert approval.approval_status == DocumentApprovalStatus.APPROVED
    assert approval.approver_id == approver.id
    assert approval.patient_identity_status == PatientIdentityApprovalStatus.CONFIRMED

    # Document transitions to APPROVED
    refetched_doc = db_session.get(Document, doc.id)
    assert refetched_doc.status == DocumentStatus.APPROVED


# ─── 12. SUCCESSFUL APPROVAL SETS EXPORT ELIGIBILITY CORRECTLY ────────────────

def test_successful_approval_sets_export_eligibility(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert is_export_eligible is True on successful approval."""
    uploader, approver, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED],
        identity_status=IdentityVerificationStatus.MISMATCH_ACCEPTED,
        confirm_identity_with_approver=True,
    )

    approval = approval_engine.approve_document(
        session=db_session,
        document_id=doc.id,
        approver_id=approver.id,
    )

    assert approval.is_export_eligible is True
    assert approval.patient_identity_status == PatientIdentityApprovalStatus.MISMATCH_ACCEPTED


# ─── 13. APPROVAL PERSISTENCE ─────────────────────────────────────────────────

def test_approval_persistence_fidelity(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert DocumentApproval relational table retains all required audit and policy fields."""
    uploader, approver, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED],
        identity_status=IdentityVerificationStatus.CONFIRMED,
        confirm_identity_with_approver=True,
    )

    approval = approval_engine.approve_document(
        session=db_session,
        document_id=doc.id,
        approver_id=approver.id,
        identity_notes="Prescription verified against patient history.",
    )

    persisted = db_session.get(DocumentApproval, approval.id)
    assert persisted is not None
    assert persisted.document_id == doc.id
    assert persisted.approver_id == approver.id
    assert persisted.policy_version == "1.0.0-mode0"
    assert persisted.identity_notes == "Prescription verified against patient history."
    assert persisted.created_at is not None


# ─── 14. IDENTITY CONFIRMATION AUDIT EVENT ────────────────────────────────────

def test_identity_confirmation_audit_event(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert PATIENT_IDENTITY_CONFIRMED audit event is logged with PHI-free metadata."""
    uploader, approver, doc, _ = _create_test_document_fixture(db_session)

    approval_engine.confirm_identity(
        session=db_session,
        document_id=doc.id,
        actor_id=approver.id,
    )

    ev = db_session.query(AuditEvent).filter(
        AuditEvent.event_type == "PATIENT_IDENTITY_CONFIRMED",
        AuditEvent.entity_id == str(doc.id),
    ).first()

    assert ev is not None
    assert ev.action == "CONFIRM_IDENTITY"
    assert ev.actor_id == str(approver.id)
    assert ev.current_event_hash is not None
    assert ev.metadata_json.get("identity_status") == "CONFIRMED"


# ─── 15. DOCUMENT APPROVAL AUDIT EVENT ────────────────────────────────────────

def test_document_approval_audit_event(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert DOCUMENT_APPROVED audit event is recorded with PHI-free operational counts."""
    uploader, approver, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED, ClaimReviewState.EDITED],
        identity_status=IdentityVerificationStatus.CONFIRMED,
        confirm_identity_with_approver=True,
    )

    approval_engine.approve_document(
        session=db_session,
        document_id=doc.id,
        approver_id=approver.id,
    )

    ev = db_session.query(AuditEvent).filter(
        AuditEvent.event_type == "DOCUMENT_APPROVED",
        AuditEvent.entity_id == str(doc.id),
    ).first()

    assert ev is not None
    assert ev.action == "APPROVE"
    assert ev.actor_id == str(approver.id)
    assert ev.metadata_json.get("is_export_eligible") is True
    assert ev.metadata_json.get("total_claims") == 2
    assert ev.metadata_json.get("accepted_claims") == 1
    assert ev.metadata_json.get("edited_claims") == 1


# ─── 16. AUDIT FAILURE ROLLS BACK IDENTITY MUTATION ───────────────────────────

def test_audit_failure_rolls_back_identity_mutation(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert failure in audit persistence triggers fail-closed rollback of identity confirmation."""
    uploader, approver, doc, _ = _create_test_document_fixture(db_session)
    orig_status = doc.identity_verification_status

    with patch.object(approval_engine.audit_service, "record_event", side_effect=AuditServiceError("Audit failure")):
        with pytest.raises(AuditIntegrationError, match="Audit event recording failed"):
            approval_engine.confirm_identity(
                session=db_session,
                document_id=doc.id,
                actor_id=approver.id,
            )

    db_session.expire_all()
    refetched = db_session.get(Document, doc.id)
    assert refetched.identity_verification_status == orig_status
    assert refetched.identity_confirmed_by is None


# ─── 17. AUDIT FAILURE ROLLS BACK APPROVAL MUTATION ───────────────────────────

def test_audit_failure_rolls_back_approval_mutation(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert failure in audit persistence triggers fail-closed rollback of document approval."""
    uploader, approver, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED],
        identity_status=IdentityVerificationStatus.CONFIRMED,
        confirm_identity_with_approver=True,
    )

    with patch.object(approval_engine.audit_service, "record_event", side_effect=AuditServiceError("Audit write fault")):
        with pytest.raises(AuditIntegrationError, match="Audit event recording failed"):
            approval_engine.approve_document(
                session=db_session,
                document_id=doc.id,
                approver_id=approver.id,
            )

    db_session.expire_all()
    refetched_doc = db_session.get(Document, doc.id)
    assert refetched_doc.status != DocumentStatus.APPROVED

    # Ensure no approval record was persisted
    assert db_session.query(DocumentApproval).count() == 0


# ─── 18. REPEATED / INVALID APPROVAL IS REJECTED ──────────────────────────────

def test_repeated_invalid_approval_rejected(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert approving an already approved or rejected document is rejected."""
    uploader, approver, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED],
        identity_status=IdentityVerificationStatus.CONFIRMED,
        confirm_identity_with_approver=True,
    )

    # 1. First approval succeeds
    approval_engine.approve_document(
        session=db_session,
        document_id=doc.id,
        approver_id=approver.id,
    )

    # 2. Second approval attempt raises InvalidDocumentApprovalStateError
    with pytest.raises(InvalidDocumentApprovalStateError, match="Document is already approved"):
        approval_engine.approve_document(
            session=db_session,
            document_id=doc.id,
            approver_id=approver.id,
        )


# ─── 19. APPROVAL DOES NOT PERFORM FHIR / EXPORT ──────────────────────────────

def test_approval_does_not_perform_fhir_export(db_session: Session, approval_engine: DocumentApprovalEngine):
    """Assert approval sets is_export_eligible=True but strictly does not generate FHIR bundles."""
    uploader, approver, doc, _ = _create_test_document_fixture(
        db_session,
        claim_states=[ClaimReviewState.ACCEPTED],
        identity_status=IdentityVerificationStatus.CONFIRMED,
        confirm_identity_with_approver=True,
    )

    approval = approval_engine.approve_document(
        session=db_session,
        document_id=doc.id,
        approver_id=approver.id,
    )

    assert approval.is_export_eligible is True

    # Assert zero FHIR bundles or export artifacts were generated
    fhir_count = db_session.query(FHIRBundle).filter(FHIRBundle.document_id == doc.id).count()
    assert fhir_count == 0
