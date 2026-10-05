"""Comprehensive unit and integration tests for Phase 8.5 Step 2: Claim Review Service.

Authority:
  - PRODUCTIZATION_SPEC.md §4.3 (claims, review_actions, verification_results)
  - PRODUCTIZATION_SPEC.md §7.3 (Reviewer Actions, Claim versioning, Inline re-verification)
  - PRODUCTIZATION_DECISIONS.md PD-002 (Mode 0: 100% human review)
  - PRODUCTIZATION_DECISIONS.md PD-010 (Immutable claim history)
  - PRODUCTIZATION_DECISIONS.md PD-012 (Append-only hash-chained audit logging, fail-closed atomic)

Required Test Coverage:
  1. ACCEPT transition (PENDING -> ACCEPTED)
  2. REJECT requires reason code
  3. EDIT requires reason code
  4. EDIT creates a new immutable claim version
  5. supersedes_claim_id is correct
  6. edited claim is re-verified
  7. triage is re-run after EDIT
  8. ESCALATE transition (PENDING -> ESCALATED)
  9. REOPEN transition (ACCEPTED / REJECTED / ESCALATED -> PENDING)
  10. invalid transitions are rejected
  11. optimistic/concurrent version protection
  12. ReviewAction is persisted correctly
  13. audit event is created
  14. audit failure rolls back the review mutation
  15. original claim remains unchanged after EDIT
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from clindoc.api.audit.service import AuditEventValidationError, AuditPHISecurityError, AuditService, AuditServiceError
from clindoc.db import (
    AuditEvent,
    Base,
    Claim,
    ClaimOrigin,
    ClaimReviewState,
    Document,
    DocumentStatus,
    Evidence,
    ReviewAction,
    ReviewActionType,
    TriageCategory,
    TriagePolicy,
    TriagePolicyMode,
    TriageRiskTier,
    User,
    VerificationLabel,
    VerificationResult,
    VerificationStage,
)
from clindoc.review.service import (
    AuditIntegrationError,
    ClaimNotFoundError,
    ClaimReviewService,
    EditClaimResult,
    InvalidStateTransitionError,
    MissingReasonCodeError,
    OptimisticLockError,
)
from clindoc.review.triage import (
    DEFAULT_MODE_0_POLICY_VERSION,
    ClaimTriageInput,
    ClaimTriageResult,
    TriageEngine,
    seed_default_triage_policy,
)
from clindoc.verify.cascade import VerificationCascade


# ─── FIXTURES & HELPERS ───────────────────────────────────────────────────────

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
def review_service():
    """Default ClaimReviewService instance."""
    return ClaimReviewService()


def _create_test_environment(session: Session) -> tuple[User, Document, Evidence, Claim]:
    """Helper creating a test user, document, grounding evidence, and initial claim."""
    seed_default_triage_policy(session)

    user = User(
        id=uuid.uuid4(),
        idp_subject="sub-sarah-12345",
        username="clinician_sarah",
        email="sarah@hospital.example.org",
        full_name="Dr. Sarah Connor",
        is_active=True,
    )
    session.add(user)
    session.flush()

    doc = Document(
        id=uuid.uuid4(),
        sha256="e" * 64,
        storage_ref="s3://clindoc-docs/patient_discharge_summary.pdf",
        original_filename="discharge_summary.pdf",
        mime_type="application/pdf",
        file_size_bytes=45210,
        page_count=2,
        uploaded_by=user.id,
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(doc)
    session.flush()

    evidence = Evidence(
        id=uuid.uuid4(),
        document_id=doc.id,
        page_number=1,
        char_start=150,
        char_end=180,
        bounding_box={"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.25},
        ocr_confidence=0.98,
        evidence_text="Metformin 1000mg PO BID with meals",
        payload={
            "evidence_id": str(uuid.uuid4()),
            "document_id": str(doc.id),
            "page_number": 1,
            "text": "Metformin 1000mg PO BID with meals",
            "char_start": 150,
            "char_end": 180,
            "bbox": [0.1, 0.2, 0.5, 0.25],
            "ocr_confidence": 0.98,
        },
    )
    session.add(evidence)
    session.flush()

    claim = Claim(
        id=uuid.uuid4(),
        claim_id=uuid.uuid4(),
        document_id=doc.id,
        group_id="rx_group_1",
        field_type="med.dose",
        value="500mg",  # Pipeline extracted 500mg (discrepancy against 1000mg in evidence)
        normalized_code="316049",
        code_system="RxNorm",
        version=1,
        origin=ClaimOrigin.PIPELINE,
        review_state=ClaimReviewState.PENDING,
        evidence_id=evidence.id,
        payload={
            "value": "500mg",
            "field_type": "med.dose",
            "region_id": "rx_group_1",
            "extraction_method": "deterministic",
            "extraction_confidence": 0.85,
        },
    )
    session.add(claim)
    session.commit()

    return user, doc, evidence, claim


# ─── 1. ACCEPT TRANSITION ─────────────────────────────────────────────────────

def test_accept_claim_transition(db_session: Session, review_service: ClaimReviewService):
    """Assert ACCEPT transitions claim from PENDING to ACCEPTED, creating ReviewAction and AuditEvent."""
    user, doc, ev, claim = _create_test_environment(db_session)

    accepted_claim = review_service.accept_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        expected_version=1,
        notes="Verified against clinical narrative.",
    )

    assert accepted_claim.id == claim.id
    assert accepted_claim.review_state == ClaimReviewState.ACCEPTED
    assert accepted_claim.version == 1

    # Verify persisted ReviewAction
    actions = db_session.query(ReviewAction).filter(ReviewAction.claim_id == claim.id).all()
    assert len(actions) == 1
    action = actions[0]
    assert action.action_type == ReviewActionType.ACCEPT
    assert action.previous_state == "PENDING"
    assert action.new_state == "ACCEPTED"
    assert action.actor_id == user.id
    assert action.notes == "Verified against clinical narrative."

    # Verify persisted AuditEvent
    audit_events = db_session.query(AuditEvent).filter(AuditEvent.entity_id == str(claim.id)).all()
    assert len(audit_events) == 1
    assert audit_events[0].action == "ACCEPT"
    assert audit_events[0].actor_id == str(user.id)


# ─── 2. REJECT REQUIRES REASON CODE ───────────────────────────────────────────

def test_reject_requires_reason_code(db_session: Session, review_service: ClaimReviewService):
    """Assert REJECT strictly requires a non-empty reason_code."""
    user, doc, ev, claim = _create_test_environment(db_session)

    # Missing / empty reason code must raise MissingReasonCodeError
    with pytest.raises(MissingReasonCodeError, match="Reason code is required for REJECT"):
        review_service.reject_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=user.id,
            reason_code="",
            expected_version=1,
        )

    # With valid reason code, succeeds
    rejected_claim = review_service.reject_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        reason_code="HALLUCINATED_DOSAGE",
        expected_version=1,
        notes="Dosage not present in note.",
    )
    assert rejected_claim.review_state == ClaimReviewState.REJECTED


# ─── 3. EDIT REQUIRES REASON CODE ─────────────────────────────────────────────

def test_edit_requires_reason_code(db_session: Session, review_service: ClaimReviewService):
    """Assert EDIT strictly requires a non-empty reason_code."""
    user, doc, ev, claim = _create_test_environment(db_session)

    with pytest.raises(MissingReasonCodeError, match="Reason code is required for EDIT"):
        review_service.edit_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=user.id,
            new_value="1000mg",
            reason_code="",
            expected_version=1,
        )


# ─── 4. EDIT CREATES A NEW IMMUTABLE CLAIM VERSION ────────────────────────────

def test_edit_creates_new_immutable_claim_version(db_session: Session, review_service: ClaimReviewService):
    """Assert EDIT creates a new Claim row with incremented version and reviewer origin."""
    user, doc, ev, claim = _create_test_environment(db_session)
    orig_id = claim.id
    stable_uuid = claim.claim_id

    result: EditClaimResult = review_service.edit_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        new_value="1000mg",
        reason_code="OCR_CORRECTION",
        expected_version=1,
        notes="Corrected 500mg to 1000mg verbatim.",
    )

    new_claim = result.new_claim
    assert new_claim.id != orig_id
    assert new_claim.claim_id == stable_uuid
    assert new_claim.version == 2
    assert new_claim.origin == ClaimOrigin.REVIEWER
    assert new_claim.created_by == user.id
    assert new_claim.value == "1000mg"
    assert new_claim.evidence_id == ev.id


# ─── 5. SUPERSEDES_CLAIM_ID IS CORRECT ─────────────────────────────────────────

def test_supersedes_claim_id_correct(db_session: Session, review_service: ClaimReviewService):
    """Assert the newly created claim version correctly points to the prior claim row."""
    user, doc, ev, claim = _create_test_environment(db_session)
    orig_row_id = claim.id

    result = review_service.edit_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        new_value="1000mg",
        reason_code="TYPO_FIX",
        expected_version=1,
    )

    assert result.new_claim.supersedes_claim_id == orig_row_id


# ─── 6. EDITED CLAIM IS RE-VERIFIED ───────────────────────────────────────────

def test_edited_claim_is_reverified(db_session: Session, review_service: ClaimReviewService):
    """Assert inline re-verification executes VerificationCascade and persists VerificationResult."""
    user, doc, ev, claim = _create_test_environment(db_session)

    # Edit to "1000mg" which matches the evidence "Metformin 1000mg PO BID with meals"
    result = review_service.edit_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        new_value="1000mg",
        reason_code="CLINICAL_CORRECTION",
        expected_version=1,
    )

    # Verify VerificationResult persistence
    vr = result.verification_result
    assert vr is not None
    assert vr.claim_id == result.new_claim.id
    assert vr.verification_stage in (VerificationStage.STAGE_A, VerificationStage.STAGE_B)
    assert vr.calibrated_confidence > 0.0

    # Ensure DB record exists
    persisted_vr = db_session.get(VerificationResult, vr.id)
    assert persisted_vr is not None
    assert persisted_vr.claim_id == result.new_claim.id


# ─── 7. TRIAGE IS RE-RUN AFTER EDIT ───────────────────────────────────────────

def test_triage_rerun_after_edit(db_session: Session, review_service: ClaimReviewService):
    """Assert Mode 0 triage engine is re-executed and the edited claim remains in PENDING review_state."""
    user, doc, ev, claim = _create_test_environment(db_session)

    result = review_service.edit_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        new_value="1000mg",
        reason_code="CORRECT_VALUE",
        expected_version=1,
    )

    triage_res: ClaimTriageResult = result.triage_result
    assert triage_res is not None
    assert triage_res.claim_id == result.new_claim.id
    assert triage_res.category in (TriageCategory.AUTO_PROCESSED_AGREED, TriageCategory.HUMAN_REVIEW_REQUIRED)
    assert triage_res.is_autonomously_exportable is False  # Strict Mode 0 invariant (PD-002)

    # Invariant: Edited claim must NOT become accepted merely because reviewer edited it
    assert result.new_claim.review_state == ClaimReviewState.PENDING


# ─── 8. ESCALATE TRANSITION ───────────────────────────────────────────────────

def test_escalate_claim_transition(db_session: Session, review_service: ClaimReviewService):
    """Assert ESCALATE transitions claim from PENDING to ESCALATED with audit linkage."""
    user, doc, ev, claim = _create_test_environment(db_session)

    escalated_claim = review_service.escalate_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        expected_version=1,
        reason_code="AMBIGUOUS_NARRATIVE",
        notes="Requires senior physician review.",
    )

    assert escalated_claim.review_state == ClaimReviewState.ESCALATED

    actions = db_session.query(ReviewAction).filter(ReviewAction.claim_id == claim.id).all()
    assert len(actions) == 1
    assert actions[0].action_type == ReviewActionType.ESCALATE
    assert actions[0].new_state == "ESCALATED"


# ─── 9. REOPEN TRANSITION ─────────────────────────────────────────────────────

def test_reopen_transitions(db_session: Session, review_service: ClaimReviewService):
    """Assert REOPEN transitions ACCEPTED, REJECTED, and ESCALATED claims back to PENDING."""
    user, doc, ev, claim = _create_test_environment(db_session)

    # 1. ACCEPT -> REOPEN -> PENDING
    review_service.accept_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        expected_version=1,
    )
    assert claim.review_state == ClaimReviewState.ACCEPTED

    reopened_1 = review_service.reopen_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        expected_version=1,
        reason_code="RECONSIDERATION",
    )
    assert reopened_1.review_state == ClaimReviewState.PENDING

    # 2. PENDING -> REJECT -> REOPEN -> PENDING
    review_service.reject_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        reason_code="MISTAKE",
        expected_version=1,
    )
    assert claim.review_state == ClaimReviewState.REJECTED

    reopened_2 = review_service.reopen_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        expected_version=1,
    )
    assert reopened_2.review_state == ClaimReviewState.PENDING

    # 3. PENDING -> ESCALATE -> REOPEN -> PENDING
    review_service.escalate_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        expected_version=1,
    )
    assert claim.review_state == ClaimReviewState.ESCALATED

    reopened_3 = review_service.reopen_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        expected_version=1,
    )
    assert reopened_3.review_state == ClaimReviewState.PENDING


# ─── 10. INVALID TRANSITIONS ARE REJECTED ─────────────────────────────────────

def test_invalid_transitions_are_rejected(db_session: Session, review_service: ClaimReviewService):
    """Assert illegal state transitions raise InvalidStateTransitionError."""
    user, doc, ev, claim = _create_test_environment(db_session)

    # 1. Cannot REOPEN an already PENDING claim
    with pytest.raises(InvalidStateTransitionError, match="Cannot REOPEN claim from 'PENDING'"):
        review_service.reopen_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=user.id,
            expected_version=1,
        )

    # Accept the claim
    review_service.accept_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        expected_version=1,
    )

    # 2. Cannot REJECT an ACCEPTED claim directly (must REOPEN first)
    with pytest.raises(InvalidStateTransitionError, match="Cannot REJECT claim from 'ACCEPTED'"):
        review_service.reject_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=user.id,
            reason_code="CHANGE_MIND",
            expected_version=1,
        )

    # 3. Cannot EDIT an ACCEPTED claim directly
    with pytest.raises(InvalidStateTransitionError, match="Cannot EDIT claim from 'ACCEPTED'"):
        review_service.edit_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=user.id,
            new_value="850mg",
            reason_code="EDIT_ATTEMPT",
            expected_version=1,
        )

    # 4. Reopen and edit to produce EDITED claim
    review_service.reopen_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        expected_version=1,
    )
    edit_res = review_service.edit_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        new_value="1000mg",
        reason_code="VALID_EDIT",
        expected_version=1,
    )

    # Superseded original claim is now EDITED
    assert edit_res.previous_claim.review_state == ClaimReviewState.EDITED

    # 5. Cannot perform actions on superseded EDITED claim
    with pytest.raises(InvalidStateTransitionError, match="Cannot ACCEPT claim from 'EDITED'"):
        review_service.accept_claim(
            session=db_session,
            claim_id=edit_res.previous_claim.id,
            actor_id=user.id,
            expected_version=1,
        )

    with pytest.raises(InvalidStateTransitionError, match="Cannot REOPEN claim from 'EDITED'"):
        review_service.reopen_claim(
            session=db_session,
            claim_id=edit_res.previous_claim.id,
            actor_id=user.id,
            expected_version=1,
        )


# ─── 11. OPTIMISTIC / CONCURRENT VERSION PROTECTION ───────────────────────────

def test_optimistic_concurrent_version_protection(db_session: Session, review_service: ClaimReviewService):
    """Assert stale version updates raise OptimisticLockError."""
    user, doc, ev, claim = _create_test_environment(db_session)

    # Claim is at version 1; expecting version 2 must fail
    with pytest.raises(OptimisticLockError, match="Optimistic lock conflict"):
        review_service.accept_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=user.id,
            expected_version=2,
        )

    with pytest.raises(OptimisticLockError, match="Optimistic lock conflict"):
        review_service.edit_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=user.id,
            new_value="1000mg",
            reason_code="FIX",
            expected_version=99,
        )


# ─── 12. REVIEW ACTION IS PERSISTED CORRECTLY ─────────────────────────────────

def test_review_action_persistence_fidelity(db_session: Session, review_service: ClaimReviewService):
    """Assert ReviewAction table contains all required auditable fields."""
    user, doc, ev, claim = _create_test_environment(db_session)

    res = review_service.edit_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        new_value="1000mg",
        reason_code="DOSAGE_CORRECTION",
        expected_version=1,
        notes="Reviewed lab and prescription line 1",
    )

    action: ReviewAction = res.review_action
    assert action.id is not None
    assert action.claim_id == res.new_claim.id
    assert action.actor_id == user.id
    assert action.action_type == ReviewActionType.EDIT
    assert action.previous_state == "PENDING"
    assert action.new_state == "EDITED"
    assert action.reason_code == "DOSAGE_CORRECTION"
    assert action.notes == "Reviewed lab and prescription line 1"
    assert action.before_payload is not None
    assert action.after_payload is not None
    assert action.created_at is not None


# ─── 13. AUDIT EVENT IS CREATED ───────────────────────────────────────────────

def test_audit_event_is_created(db_session: Session, review_service: ClaimReviewService):
    """Assert append-only hash-chained AuditEvent is created for review action."""
    user, doc, ev, claim = _create_test_environment(db_session)

    review_service.accept_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        expected_version=1,
    )

    ev_record = db_session.query(AuditEvent).filter(
        AuditEvent.entity_id == str(claim.id),
        AuditEvent.action == "ACCEPT",
    ).first()

    assert ev_record is not None
    assert ev_record.sequence_number == 1
    assert ev_record.actor_id == str(user.id)
    assert ev_record.current_event_hash is not None
    assert len(ev_record.current_event_hash) == 64
    assert ev_record.metadata_json.get("field_type") == "med.dose"


# ─── 14. AUDIT FAILURE ROLLS BACK REVIEW MUTATION ─────────────────────────────

def test_audit_failure_rolls_back_review_mutation(db_session: Session, review_service: ClaimReviewService):
    """Assert failure in audit persistence triggers fail-closed transaction rollback."""
    user, doc, ev, claim = _create_test_environment(db_session)
    orig_state = claim.review_state

    # Simulate an audit failure (e.g. storage error or integrity fault)
    with patch.object(review_service.audit_service, "record_event", side_effect=AuditServiceError("Database write error")):
        with pytest.raises(AuditIntegrationError, match="Audit event recording failed"):
            review_service.accept_claim(
                session=db_session,
                claim_id=claim.id,
                actor_id=user.id,
                expected_version=1,
            )

    # Verify claim in database remained completely untouched
    db_session.expire_all()
    refetched = db_session.get(Claim, claim.id)
    assert refetched.review_state == orig_state
    assert refetched.review_state == ClaimReviewState.PENDING

    # Ensure no ReviewAction or AuditEvent was saved
    assert db_session.query(ReviewAction).count() == 0
    assert db_session.query(AuditEvent).count() == 0


# ─── 15. ORIGINAL CLAIM REMAINS UNCHANGED AFTER EDIT ──────────────────────────

def test_original_claim_remains_unchanged_after_edit(db_session: Session, review_service: ClaimReviewService):
    """Assert original claim's clinical values, version, and provenance remain immutably preserved."""
    user, doc, ev, claim = _create_test_environment(db_session)
    orig_id = claim.id
    orig_val = claim.value
    orig_ver = claim.version
    orig_origin = claim.origin
    orig_ev_id = claim.evidence_id
    orig_code = claim.normalized_code

    result = review_service.edit_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=user.id,
        new_value="2000mg",
        reason_code="UPDATED_REGIMEN",
        expected_version=1,
    )

    db_session.expire_all()
    refetched_orig = db_session.get(Claim, orig_id)

    # Immutability guarantees: original clinical extraction is never overwritten
    assert refetched_orig.value == orig_val
    assert refetched_orig.version == orig_ver
    assert refetched_orig.origin == orig_origin
    assert refetched_orig.evidence_id == orig_ev_id
    assert refetched_orig.normalized_code == orig_code
    assert refetched_orig.supersedes_claim_id is None

    # Lifecycle state: marked EDITED to track that a newer version supersedes it
    assert refetched_orig.review_state == ClaimReviewState.EDITED

    # New claim version is distinct
    assert result.new_claim.id != orig_id
    assert result.new_claim.value == "2000mg"
    assert result.new_claim.version == 2
    assert result.new_claim.supersedes_claim_id == orig_id
