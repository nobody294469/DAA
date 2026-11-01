"""Adversarial and Concurrency Integration Tests for Step 1:
F-12 Four-Eyes Multi-Hop Ancestry, Root Locking & Branching Prevention.

Authorities:
  - CLINICALDOC_P0_IMPLEMENTATION_EXECUTION_PLAN.md Step 1
  - phase523_milestone44_req44_remediation_design_review_v3_1.md §3 (F-12)
  - PRODUCTIZATION_DECISIONS.md PD-010 (Immutable claim history)
  - PRODUCTIZATION_DECISIONS.md PD-012 (Tamper-evident audit logging)
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Generator
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

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
    Role,
    TriagePolicy,
    User,
    UserRole,
    VerificationLabel,
    VerificationResult,
    VerificationStage,
)
from clindoc.review.service import (
    ActorProvenanceMismatchError,
    BranchingProhibitedError,
    ClaimReviewService,
    CorruptedClaimAncestryError,
    DeactivatedUserActionError,
    FourEyesViolationError,
    InvalidClaimLifecycleStateError,
    InvalidStateTransitionError,
    OptimisticLockError,
    UnauthorizedReviewerError,
    UnresolvableActorError,
    UnverifiableHistoricalActorError,
)
from clindoc.api.audit.service import compute_audit_event_hash
from clindoc.review.triage import seed_default_triage_policy


def _ensure_roles_seeded(session: Session) -> None:
    """Ensure standard roles exist in roles table."""
    standard_roles = [
        ("clinical_reviewer", "Clinical Reviewer"),
        ("pipeline_operator", "Pipeline Operator"),
        ("reviewer", "Reviewer"),
        ("approver", "Approver"),
        ("admin", "Administrator"),
        ("supervisory_reviewer", "Supervisory Reviewer"),
        ("chief_medical_officer", "Chief Medical Officer"),
    ]
    for r_id, r_name in standard_roles:
        if not session.get(Role, r_id):
            session.add(Role(id=r_id, name=r_name, description=r_name, permissions={}))
    session.flush()


# ─── SQLite FIXTURE FOR UNIT TESTS ──────────────────────────────────────────

@pytest.fixture
def sqlite_session() -> Generator[Session, None, None]:
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
    seed_default_triage_policy(session)
    _ensure_roles_seeded(session)
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def review_svc() -> ClaimReviewService:
    """Service with strict lifecycle and Four-Eyes enforced."""
    svc = ClaimReviewService(enforce_four_eyes=True)
    svc.strict_lifecycle = True
    return svc


def _seed_user(
    session: Session,
    username: str,
    role: str = "clinical_reviewer",
    is_active: bool = True,
) -> User:
    """Helper to seed an active or deactivated user with a role."""
    _ensure_roles_seeded(session)
    user = User(
        id=uuid.uuid4(),
        idp_subject=f"sub-{username}",
        username=username,
        email=f"{username}@hospital.org",
        full_name=f"Dr. {username.capitalize()}",
        is_active=is_active,
    )
    session.add(user)
    session.flush()

    user_role = UserRole(
        id=uuid.uuid4(),
        user_id=user.id,
        role_id=role,
    )
    session.add(user_role)
    session.flush()
    return user


def _seed_document(session: Session, uploader: User) -> Document:
    """Helper to seed a document uploaded by a specific user."""
    doc = Document(
        id=uuid.uuid4(),
        sha256="a" * 64,
        storage_ref="s3://test-bucket/doc.pdf",
        original_filename="doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=45210,
        page_count=1,
        status=DocumentStatus.PENDING_REVIEW,
        uploaded_by=uploader.id,
    )
    session.add(doc)
    session.flush()
    return doc


def _seed_evidence(session: Session, doc: Document) -> Evidence:
    """Helper to seed grounding evidence."""
    ev = Evidence(
        id=uuid.uuid4(),
        document_id=doc.id,
        page_number=1,
        char_start=0,
        char_end=35,
        ocr_confidence=0.98,
        evidence_text="Patient prescribed Metoprolol 25mg daily.",
        bounding_box={"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.3},
    )
    session.add(ev)
    session.flush()
    return ev


def _seed_genesis_claim(
    session: Session,
    doc: Document,
    evidence: Evidence,
    field_type: str = "med.name",
    value: str = "Metoprolol 25mg",
    creator: User | None = None,
) -> Claim:
    """Helper to seed version 1 of a claim."""
    stable_claim_id = uuid.uuid4()
    claim = Claim(
        id=uuid.uuid4(),
        claim_id=stable_claim_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=evidence.id,
        version=1,
        field_type=field_type,
        value=value,
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.PIPELINE if creator is None else ClaimOrigin.REVIEWER,
        created_by=creator.id if creator else None,
        supersedes_claim_id=None,
        payload={
            "claim_id": str(stable_claim_id),
            "document_id": str(doc.id),
            "association_status": "PARALLEL_COORDINATE_BIJECTION",
        },
    )
    if creator:
        claim.payload["edited_by"] = str(creator.id)

    session.add(claim)
    session.commit()
    return claim


# ─── 1. MULTI-HOP FOUR-EYES SEPARATION TESTS ────────────────────────────────

def test_multi_hop_four_eyes_separation(sqlite_session: Session, review_svc: ClaimReviewService):
    """
    v1 (pipeline) -> v2 (edited by A) -> v3 (edited by B) -> v4 (edited by C).
    - Clinicians A, B, and C must be prohibited from ACCEPTING v4.
    - Independent Clinician D must succeed in ACCEPTING v4.
    """
    user_u = _seed_user(sqlite_session, "uploader", role="pipeline_operator")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)

    user_a = _seed_user(sqlite_session, "clinician_a")
    user_b = _seed_user(sqlite_session, "clinician_b")
    user_c = _seed_user(sqlite_session, "clinician_c")
    user_d = _seed_user(sqlite_session, "clinician_d")

    # Genesis v1
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)

    # v2 created by A
    res2 = review_svc.edit_claim(
        session=sqlite_session,
        claim_id=v1.id,
        actor_id=user_a.id,
        new_value="Metoprolol 50mg",
        reason_code="DOSAGE_CORRECTION",
        expected_version=1,
    )
    v2 = res2.new_claim

    # v3 created by B
    res3 = review_svc.edit_claim(
        session=sqlite_session,
        claim_id=v2.id,
        actor_id=user_b.id,
        new_value="Metoprolol Tartrate 50mg",
        reason_code="FORMULATION_CORRECTION",
        expected_version=2,
    )
    v3 = res3.new_claim

    # v4 created by C
    res4 = review_svc.edit_claim(
        session=sqlite_session,
        claim_id=v3.id,
        actor_id=user_c.id,
        new_value="Metoprolol Tartrate 50mg oral tablet",
        reason_code="SIG_CORRECTION",
        expected_version=3,
    )
    v4 = res4.new_claim

    # Clinician A (edited v2) attempts to ACCEPT v4 -> MUST FAIL
    with pytest.raises(FourEyesViolationError, match="Four-Eyes Violation"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v4.id,
            actor_id=user_a.id,
            expected_version=4,
        )

    # Clinician B (edited v3) attempts to ACCEPT v4 -> MUST FAIL
    with pytest.raises(FourEyesViolationError, match="Four-Eyes Violation"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v4.id,
            actor_id=user_b.id,
            expected_version=4,
        )

    # Clinician C (created v4) attempts to ACCEPT v4 -> MUST FAIL
    with pytest.raises(FourEyesViolationError, match="Four-Eyes Violation"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v4.id,
            actor_id=user_c.id,
            expected_version=4,
        )

    # Clinician D (independent, never modified any version) accepts v4 -> MUST SUCCEED
    accepted = review_svc.accept_claim(
        session=sqlite_session,
        claim_id=v4.id,
        actor_id=user_d.id,
        expected_version=4,
    )
    assert accepted.review_state == ClaimReviewState.ACCEPTED
    assert accepted.payload["accepted_by"] == str(user_d.id)


def test_document_uploader_cannot_accept_claim(sqlite_session: Session, review_svc: ClaimReviewService):
    """Pipeline operator / document uploader cannot accept high-risk claim."""
    uploader = _seed_user(sqlite_session, "operator", role="clinical_reviewer")
    doc = _seed_document(sqlite_session, uploader)
    ev = _seed_evidence(sqlite_session, doc)
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)

    with pytest.raises(FourEyesViolationError, match="Pipeline operator"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v1.id,
            actor_id=uploader.id,
            expected_version=1,
        )


# ─── 2. CORRUPTED ANCESTRY TESTS ─────────────────────────────────────────────

def test_corrupted_ancestry_genesis_missing(sqlite_session: Session, review_svc: ClaimReviewService):
    """Claim lineage with no version 1 root raises CorruptedClaimAncestryError."""
    user = _seed_user(sqlite_session, "reviewer")
    doc = _seed_document(sqlite_session, user)
    ev = _seed_evidence(sqlite_session, doc)

    # Seed v2 without a v1
    stable_id = uuid.uuid4()
    orphaned_v2 = Claim(
        id=uuid.uuid4(),
        claim_id=stable_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=2,
        field_type="med.name",
        value="Aspirin 81mg",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=user.id,
        supersedes_claim_id=None,
        payload={"claim_id": str(stable_id), "document_id": str(doc.id), "edited_by": str(user.id)},
    )
    sqlite_session.add(orphaned_v2)
    sqlite_session.commit()

    rev2 = _seed_user(sqlite_session, "reviewer2")
    with pytest.raises(CorruptedClaimAncestryError, match="Genesis version 1 .* not found"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=orphaned_v2.id,
            actor_id=rev2.id,
            expected_version=2,
        )


def test_corrupted_ancestry_version_gap(sqlite_session: Session, review_svc: ClaimReviewService):
    """Lineage missing an intermediate version (e.g. [1, 3]) raises CorruptedClaimAncestryError."""
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)

    user_a = _seed_user(sqlite_session, "reviewer_a")
    # Manually create v3 skipping v2
    v3 = Claim(
        id=uuid.uuid4(),
        claim_id=v1.claim_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=3,
        field_type="med.name",
        value="Metoprolol 100mg",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=user_a.id,
        supersedes_claim_id=v1.id,
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc.id), "edited_by": str(user_a.id)},
    )
    sqlite_session.add(v3)
    sqlite_session.commit()

    user_b = _seed_user(sqlite_session, "reviewer_b")
    with pytest.raises(CorruptedClaimAncestryError, match="version sequence gap"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v3.id,
            actor_id=user_b.id,
            expected_version=3,
        )


def test_corrupted_ancestry_bad_parent_pointer(sqlite_session: Session, review_svc: ClaimReviewService):
    """Lineage where v3 points to an external claim instead of v2 raises CorruptedClaimAncestryError."""
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)
    v1.review_state = ClaimReviewState.EDITED

    other_claim = _seed_genesis_claim(sqlite_session, doc, ev, value="Other Claim")

    user_a = _seed_user(sqlite_session, "reviewer_a")
    v2 = Claim(
        id=uuid.uuid4(),
        claim_id=v1.claim_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=2,
        field_type="med.name",
        value="Metoprolol 50mg",
        review_state=ClaimReviewState.EDITED,
        origin=ClaimOrigin.REVIEWER,
        created_by=user_a.id,
        supersedes_claim_id=v1.id,
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc.id), "edited_by": str(user_a.id)},
    )
    sqlite_session.add(v2)

    user_b = _seed_user(sqlite_session, "reviewer_b")
    # v3 points to other_claim instead of v2
    v3 = Claim(
        id=uuid.uuid4(),
        claim_id=v1.claim_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=3,
        field_type="med.name",
        value="Metoprolol 100mg",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=user_b.id,
        supersedes_claim_id=other_claim.id,  # BROKEN: Should be v2.id
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc.id), "edited_by": str(user_b.id)},
    )
    sqlite_session.add(v3)
    sqlite_session.commit()

    user_c = _seed_user(sqlite_session, "reviewer_c")
    with pytest.raises(CorruptedClaimAncestryError, match="Corrupted claim ancestry: version 3 supersedes"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v3.id,
            actor_id=user_c.id,
            expected_version=3,
        )


def test_corrupted_ancestry_intermediate_state_not_edited(sqlite_session: Session, review_svc: ClaimReviewService):
    """Intermediate claim version not marked EDITED raises CorruptedClaimAncestryError."""
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)
    v1.review_state = ClaimReviewState.PENDING  # BROKEN: Should be EDITED

    user_a = _seed_user(sqlite_session, "reviewer_a")
    v2 = Claim(
        id=uuid.uuid4(),
        claim_id=v1.claim_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=2,
        field_type="med.name",
        value="Metoprolol 50mg",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=user_a.id,
        supersedes_claim_id=v1.id,
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc.id), "edited_by": str(user_a.id)},
    )
    sqlite_session.add(v2)
    sqlite_session.commit()

    user_b = _seed_user(sqlite_session, "reviewer_b")
    with pytest.raises(CorruptedClaimAncestryError, match="intermediate version 1 has state 'PENDING'"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v2.id,
            actor_id=user_b.id,
            expected_version=2,
        )


def test_corrupted_ancestry_document_mismatch(sqlite_session: Session, review_svc: ClaimReviewService):
    """Lineage version with different document_id raises CorruptedClaimAncestryError."""
    user_u = _seed_user(sqlite_session, "uploader")
    doc1 = _seed_document(sqlite_session, user_u)
    doc2 = _seed_document(sqlite_session, user_u)
    ev1 = _seed_evidence(sqlite_session, doc1)
    ev2 = _seed_evidence(sqlite_session, doc2)

    v1 = _seed_genesis_claim(sqlite_session, doc1, ev1)
    v1.review_state = ClaimReviewState.EDITED

    user_a = _seed_user(sqlite_session, "reviewer_a")
    v2 = Claim(
        id=uuid.uuid4(),
        claim_id=v1.claim_id,
        document_id=doc2.id,  # MISMATCH: doc2 instead of doc1
        group_id="group_1",
        evidence_id=ev2.id,
        version=2,
        field_type="med.name",
        value="Metoprolol 50mg",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=user_a.id,
        supersedes_claim_id=v1.id,
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc2.id), "edited_by": str(user_a.id)},
    )
    sqlite_session.add(v2)
    sqlite_session.commit()

    user_b = _seed_user(sqlite_session, "reviewer_b")
    with pytest.raises(CorruptedClaimAncestryError, match="Document ID mismatch in version 2"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v2.id,
            actor_id=user_b.id,
            expected_version=2,
        )


# ─── 3. LEAF-ONLY OPERATIONS & BRANCHING PREVENTION ──────────────────────────

def test_non_leaf_operations_prohibited(sqlite_session: Session, review_svc: ClaimReviewService):
    """Operating on a superseded intermediate version raises InvalidClaimLifecycleStateError."""
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)

    user_a = _seed_user(sqlite_session, "reviewer_a")
    res2 = review_svc.edit_claim(
        session=sqlite_session,
        claim_id=v1.id,
        actor_id=user_a.id,
        new_value="Metoprolol 50mg",
        reason_code="DOSE_ADJUSTMENT",
        expected_version=1,
    )
    v2 = res2.new_claim

    user_b = _seed_user(sqlite_session, "reviewer_b")
    # 1. Trying to ACCEPT superseded v1 raises InvalidStateTransitionError
    with pytest.raises(InvalidStateTransitionError):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v1.id,
            actor_id=user_b.id,
            expected_version=1,
        )

    # 2. Directly verifying non-leaf v1 raises InvalidClaimLifecycleStateError
    with pytest.raises(InvalidClaimLifecycleStateError, match="Non-leaf review prohibited"):
        review_svc._lock_and_validate_lineage(
            sqlite_session,
            v1,
            user_b.id,
            is_accept=False,
        )


def test_branching_prohibited_multiple_children(sqlite_session: Session, review_svc: ClaimReviewService):
    """Intermediate claim having more than one child raises BranchingProhibitedError."""
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)
    v1.review_state = ClaimReviewState.EDITED

    user_a = _seed_user(sqlite_session, "reviewer_a")
    user_b = _seed_user(sqlite_session, "reviewer_b")

    # Manually insert branch A and branch B both pointing to v1
    branch_a = Claim(
        id=uuid.uuid4(),
        claim_id=v1.claim_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=2,
        field_type="med.name",
        value="Branch A Value",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=user_a.id,
        supersedes_claim_id=v1.id,
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc.id), "edited_by": str(user_a.id)},
    )
    branch_b = Claim(
        id=uuid.uuid4(),
        claim_id=uuid.uuid4(),
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=1,  # Independent claim pointing to v1
        field_type="med.name",
        value="Branch B Value",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=user_b.id,
        supersedes_claim_id=v1.id,
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc.id), "edited_by": str(user_b.id)},
    )
    sqlite_session.add(branch_a)
    sqlite_session.add(branch_b)
    sqlite_session.commit()

    user_c = _seed_user(sqlite_session, "reviewer_c")
    with pytest.raises(BranchingProhibitedError, match="Branching prohibited"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=branch_a.id,
            actor_id=user_c.id,
            expected_version=2,
        )


# ─── 4. ACTOR PROVENANCE INTEGRITY TESTS ─────────────────────────────────────

def test_actor_provenance_mismatch(sqlite_session: Session, review_svc: ClaimReviewService):
    """Claim version where created_by != payload['edited_by'] raises ActorProvenanceMismatchError."""
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)
    v1.review_state = ClaimReviewState.EDITED

    user_a = _seed_user(sqlite_session, "reviewer_a")
    user_b = _seed_user(sqlite_session, "reviewer_b")

    # Mismatch between created_by and payload['edited_by']
    v2 = Claim(
        id=uuid.uuid4(),
        claim_id=v1.claim_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=2,
        field_type="med.name",
        value="Metoprolol 50mg",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=user_a.id,
        supersedes_claim_id=v1.id,
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc.id), "edited_by": str(user_b.id)},
    )
    sqlite_session.add(v2)
    sqlite_session.commit()

    user_c = _seed_user(sqlite_session, "reviewer_c")
    with pytest.raises(ActorProvenanceMismatchError, match="Actor provenance mismatch"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v2.id,
            actor_id=user_c.id,
            expected_version=2,
        )


def test_actor_unresolvable_fails_closed(sqlite_session: Session, review_svc: ClaimReviewService):
    """Claim version referencing a phantom actor ID not in User table raises UnresolvableActorError."""
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)
    v1.review_state = ClaimReviewState.EDITED

    user_a = _seed_user(sqlite_session, "reviewer_a")
    v2 = Claim(
        id=uuid.uuid4(),
        claim_id=v1.claim_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=2,
        field_type="med.name",
        value="Metoprolol 50mg",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=user_a.id,
        supersedes_claim_id=v1.id,
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc.id), "edited_by": str(user_a.id)},
    )
    sqlite_session.add(v2)
    sqlite_session.commit()

    user_c = _seed_user(sqlite_session, "reviewer_c")
    orig_get = sqlite_session.get

    def mock_get(entity, ident, **kwargs):
        if entity is User and ident == user_a.id:
            return None
        return orig_get(entity, ident, **kwargs)

    from unittest.mock import patch
    with patch.object(sqlite_session, "get", side_effect=mock_get):
        with pytest.raises(UnresolvableActorError, match="cannot be resolved to a User"):
            review_svc.accept_claim(
                session=sqlite_session,
                claim_id=v2.id,
                actor_id=user_c.id,
                expected_version=2,
            )


# ─── 5. DEACTIVATED USER & HISTORICAL OPTION B TESTS ─────────────────────────

def test_deactivated_user_cannot_perform_new_action(sqlite_session: Session, review_svc: ClaimReviewService):
    """Deactivated user attempting to review, edit, reject or reopen fails with DeactivatedUserActionError."""
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)

    deact_user = _seed_user(sqlite_session, "deactivated_doc", is_active=False)

    # 1. Accept attempt fails
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)
    with pytest.raises(DeactivatedUserActionError, match="is inactive|is deactivated"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v1.id,
            actor_id=deact_user.id,
            expected_version=1,
        )

    # 2. Reject attempt fails
    v1_b = _seed_genesis_claim(sqlite_session, doc, ev)
    with pytest.raises(DeactivatedUserActionError, match="is inactive|is deactivated"):
        review_svc.reject_claim(
            session=sqlite_session,
            claim_id=v1_b.id,
            actor_id=deact_user.id,
            reason_code="CLINICALLY_INCORRECT",
            expected_version=1,
        )

    # 3. Edit attempt fails
    v1_c = _seed_genesis_claim(sqlite_session, doc, ev)
    with pytest.raises(DeactivatedUserActionError, match="is inactive|is deactivated"):
        review_svc.edit_claim(
            session=sqlite_session,
            claim_id=v1_c.id,
            actor_id=deact_user.id,
            new_value="Metoprolol 50mg",
            reason_code="DOSE_CHANGE",
            expected_version=1,
        )


def test_historical_deactivated_user_with_audit_succeeds(sqlite_session: Session, review_svc: ClaimReviewService):
    """
    Option B: Historical claim version modified by a user who later became deactivated.
    If trustworthy immutable audit evidence exists, review of terminal leaf SUCCEEDS.
    """
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)

    # User A creates v2 while active
    user_a = _seed_user(sqlite_session, "historical_editor", is_active=True)
    res2 = review_svc.edit_claim(
        session=sqlite_session,
        claim_id=v1.id,
        actor_id=user_a.id,
        new_value="Metoprolol 50mg",
        reason_code="DOSE_CHANGE",
        expected_version=1,
    )
    v2 = res2.new_claim

    # User A is subsequently deactivated
    user_a.is_active = False
    sqlite_session.commit()

    # User B edits v3
    user_b = _seed_user(sqlite_session, "active_editor_b")
    res3 = review_svc.edit_claim(
        session=sqlite_session,
        claim_id=v2.id,
        actor_id=user_b.id,
        new_value="Metoprolol 50mg tablet",
        reason_code="FORMULATION_NOTE",
        expected_version=2,
    )
    v3 = res3.new_claim

    # Independent active User C reviews v3
    # Should SUCCEED because User A's v2 edit has verified immutable audit records
    user_c = _seed_user(sqlite_session, "independent_reviewer")
    accepted = review_svc.accept_claim(
        session=sqlite_session,
        claim_id=v3.id,
        actor_id=user_c.id,
        expected_version=3,
    )
    assert accepted.review_state == ClaimReviewState.ACCEPTED


def test_historical_deactivated_user_without_audit_fails_closed(sqlite_session: Session, review_svc: ClaimReviewService):
    """
    Option B: Historical claim version modified by a deactivated user WITHOUT audit evidence.
    Fails closed with UnverifiableHistoricalActorError.
    """
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)
    v1 = _seed_genesis_claim(sqlite_session, doc, ev)
    v1.review_state = ClaimReviewState.EDITED

    # Deactivated user with no audit trail
    deact_user = _seed_user(sqlite_session, "unverified_historical", is_active=False)

    v2 = Claim(
        id=uuid.uuid4(),
        claim_id=v1.claim_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=2,
        field_type="med.name",
        value="Metoprolol 50mg",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=deact_user.id,
        supersedes_claim_id=v1.id,
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc.id), "edited_by": str(deact_user.id)},
    )
    sqlite_session.add(v2)
    sqlite_session.commit()

    user_c = _seed_user(sqlite_session, "independent_reviewer")
    with pytest.raises(UnverifiableHistoricalActorError, match="lacks verifiable audit evidence"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v2.id,
            actor_id=user_c.id,
            expected_version=2,
        )


def _seed_valid_audit_event(
    session: Session,
    claim: Claim,
    actor_id: uuid.UUID,
    sequence_number: int = 1,
    action: Optional[str] = None,
    previous_event_hash: Optional[str] = None,
    claim_version: Optional[int] = None,
    stable_claim_id: Optional[str] = None,
    timestamp: Optional[datetime] = None,
    override_hash: Optional[str] = None,
) -> AuditEvent:
    ts = timestamp if timestamp is not None else datetime.now(timezone.utc)
    c_ver = claim_version if claim_version is not None else claim.version
    s_cid = stable_claim_id if stable_claim_id is not None else str(claim.claim_id)
    actual_action = action if action is not None else ("CREATE" if c_ver == 1 else "EDIT")

    metadata = {
        "action": actual_action,
        "previous_state": "PENDING",
        "new_state": "PENDING",
        "claim_version": c_ver,
        "claim_id": str(claim.id),
        "stable_claim_id": s_cid,
        "document_id": str(claim.document_id),
        "document_sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "field_type": claim.field_type,
        "origin": "reviewer",
    }

    if override_hash is not None:
        c_hash = override_hash
    else:
        c_hash = compute_audit_event_hash(
            sequence_number=sequence_number,
            timestamp=ts,
            actor_id=str(actor_id),
            event_type="CLAIM_REVIEW_ACTION",
            entity_type="claim",
            entity_id=str(claim.id),
            action=actual_action,
            metadata_json=metadata,
            previous_event_hash=previous_event_hash,
        )

    evt = AuditEvent(
        id=uuid.uuid4(),
        sequence_number=sequence_number,
        timestamp=ts,
        actor_id=str(actor_id),
        event_type="CLAIM_REVIEW_ACTION",
        entity_type="claim",
        entity_id=str(claim.id),
        action=actual_action,
        metadata_json=metadata,
        previous_event_hash=previous_event_hash,
        current_event_hash=c_hash,
    )
    session.add(evt)
    session.flush()
    return evt


def _setup_deactivated_claim_env(session: Session):
    user_u = _seed_user(session, f"uploader_{uuid.uuid4().hex[:6]}")
    doc = _seed_document(session, user_u)
    ev = _seed_evidence(session, doc)
    v1 = _seed_genesis_claim(session, doc, ev)
    v1.review_state = ClaimReviewState.EDITED

    deact_user = _seed_user(session, f"deact_{uuid.uuid4().hex[:6]}", is_active=False)
    v2 = Claim(
        id=uuid.uuid4(),
        claim_id=v1.claim_id,
        document_id=doc.id,
        group_id="group_1",
        evidence_id=ev.id,
        version=2,
        field_type="med.name",
        value="Metoprolol 50mg",
        review_state=ClaimReviewState.PENDING,
        origin=ClaimOrigin.REVIEWER,
        created_by=deact_user.id,
        supersedes_claim_id=v1.id,
        payload={"claim_id": str(v1.claim_id), "document_id": str(doc.id), "edited_by": str(deact_user.id)},
    )
    session.add(v2)
    session.commit()
    reviewer = _seed_user(session, f"rev_{uuid.uuid4().hex[:6]}")
    return v1, v2, deact_user, reviewer


def test_historical_audit_wrong_claim_version(sqlite_session: Session, review_svc: ClaimReviewService):
    v1, v2, deact_user, reviewer = _setup_deactivated_claim_env(sqlite_session)
    _seed_valid_audit_event(sqlite_session, v2, deact_user.id, claim_version=99)
    sqlite_session.commit()
    with pytest.raises(UnverifiableHistoricalActorError, match="failed integrity or provenance verification"):
        review_svc.accept_claim(session=sqlite_session, claim_id=v2.id, actor_id=reviewer.id, expected_version=2)


def test_historical_audit_wrong_stable_claim_id(sqlite_session: Session, review_svc: ClaimReviewService):
    v1, v2, deact_user, reviewer = _setup_deactivated_claim_env(sqlite_session)
    _seed_valid_audit_event(sqlite_session, v2, deact_user.id, stable_claim_id=str(uuid.uuid4()))
    sqlite_session.commit()
    with pytest.raises(UnverifiableHistoricalActorError, match="failed integrity or provenance verification"):
        review_svc.accept_claim(session=sqlite_session, claim_id=v2.id, actor_id=reviewer.id, expected_version=2)


def test_historical_audit_wrong_actor(sqlite_session: Session, review_svc: ClaimReviewService):
    v1, v2, deact_user, reviewer = _setup_deactivated_claim_env(sqlite_session)
    wrong_actor_id = uuid.uuid4()
    _seed_valid_audit_event(sqlite_session, v2, wrong_actor_id)
    sqlite_session.commit()
    with pytest.raises(UnverifiableHistoricalActorError, match="lacks verifiable audit evidence"):
        review_svc.accept_claim(session=sqlite_session, claim_id=v2.id, actor_id=reviewer.id, expected_version=2)


def test_historical_audit_unauthorized_action_type(sqlite_session: Session, review_svc: ClaimReviewService):
    v1, v2, deact_user, reviewer = _setup_deactivated_claim_env(sqlite_session)
    _seed_valid_audit_event(sqlite_session, v2, deact_user.id, action="EXPORT")
    sqlite_session.commit()
    with pytest.raises(UnverifiableHistoricalActorError, match="failed integrity or provenance verification"):
        review_svc.accept_claim(session=sqlite_session, claim_id=v2.id, actor_id=reviewer.id, expected_version=2)


def test_historical_audit_malformed_hash(sqlite_session: Session, review_svc: ClaimReviewService):
    v1, v2, deact_user, reviewer = _setup_deactivated_claim_env(sqlite_session)
    _seed_valid_audit_event(sqlite_session, v2, deact_user.id, override_hash="not_a_valid_hex_hash")
    sqlite_session.commit()
    with pytest.raises(UnverifiableHistoricalActorError, match="failed integrity or provenance verification"):
        review_svc.accept_claim(session=sqlite_session, claim_id=v2.id, actor_id=reviewer.id, expected_version=2)


def test_historical_audit_cryptographically_incorrect_hash(sqlite_session: Session, review_svc: ClaimReviewService):
    v1, v2, deact_user, reviewer = _setup_deactivated_claim_env(sqlite_session)
    _seed_valid_audit_event(sqlite_session, v2, deact_user.id, override_hash="0" * 64)
    sqlite_session.commit()
    with pytest.raises(UnverifiableHistoricalActorError, match="failed integrity or provenance verification"):
        review_svc.accept_claim(session=sqlite_session, claim_id=v2.id, actor_id=reviewer.id, expected_version=2)


def test_historical_audit_broken_previous_hash_chain(sqlite_session: Session, review_svc: ClaimReviewService):
    v1, v2, deact_user, reviewer = _setup_deactivated_claim_env(sqlite_session)
    # Event 1 genesis
    _seed_valid_audit_event(sqlite_session, v1, deact_user.id, sequence_number=1)
    # Event 2 with broken previous hash link
    _seed_valid_audit_event(sqlite_session, v2, deact_user.id, sequence_number=2, previous_event_hash="f" * 64)
    sqlite_session.commit()
    with pytest.raises(UnverifiableHistoricalActorError, match="failed integrity or provenance verification"):
        review_svc.accept_claim(session=sqlite_session, claim_id=v2.id, actor_id=reviewer.id, expected_version=2)


def test_historical_audit_future_timestamp(sqlite_session: Session, review_svc: ClaimReviewService):
    v1, v2, deact_user, reviewer = _setup_deactivated_claim_env(sqlite_session)
    future_time = datetime(2099, 1, 1, tzinfo=timezone.utc)
    _seed_valid_audit_event(sqlite_session, v2, deact_user.id, timestamp=future_time)
    sqlite_session.commit()
    with pytest.raises(UnverifiableHistoricalActorError, match="failed integrity or provenance verification"):
        review_svc.accept_claim(session=sqlite_session, claim_id=v2.id, actor_id=reviewer.id, expected_version=2)


def test_historical_audit_invalid_review_action_fallback(sqlite_session: Session, review_svc: ClaimReviewService):
    v1, v2, deact_user, reviewer = _setup_deactivated_claim_env(sqlite_session)
    # Only a ReviewAction exists; no AuditEvent with cryptographic hash chain
    ra = ReviewAction(
        id=uuid.uuid4(),
        claim_id=v2.id,
        actor_id=deact_user.id,
        action_type=ReviewActionType.EDIT,
        previous_state="PENDING",
        new_state="PENDING",
        reason_code="DOSE_CHANGE",
        created_at=datetime.now(timezone.utc),
    )
    sqlite_session.add(ra)
    sqlite_session.commit()

    with pytest.raises(UnverifiableHistoricalActorError, match="lacks verifiable audit evidence"):
        review_svc.accept_claim(session=sqlite_session, claim_id=v2.id, actor_id=reviewer.id, expected_version=2)


def test_historical_audit_tampered_earlier_event_in_chain(sqlite_session: Session, review_svc: ClaimReviewService):
    """
    Three-event audit chain test:
    Events [1, 2, 3] represent a valid cryptographic audit chain.
    Event 3 belongs to the target claim version (v2).
    An attacker tampers with Event 1's content.
    Even though Event 3's immediate link to Event 2 is intact and Event 2's hash is intact,
    the verifier validates the full chain back to genesis (start_sequence=1) and detects
    the corrupted earlier event, failing closed with UnverifiableHistoricalActorError.
    """
    v1, v2, deact_user, reviewer = _setup_deactivated_claim_env(sqlite_session)

    # 1. Build a valid 3-event chain
    # Event 1 (genesis claim creation)
    evt1 = _seed_valid_audit_event(
        sqlite_session,
        v1,
        deact_user.id,
        sequence_number=1,
        action="CREATE",
        claim_version=1,
    )
    # Event 2 (intermediate claim edit / transition)
    evt2 = _seed_valid_audit_event(
        sqlite_session,
        v1,
        deact_user.id,
        sequence_number=2,
        action="EDIT",
        claim_version=1,
        previous_event_hash=evt1.current_event_hash,
    )
    # Event 3 (v2 creation / edit by deact_user)
    evt3 = _seed_valid_audit_event(
        sqlite_session,
        v2,
        deact_user.id,
        sequence_number=3,
        action="EDIT",
        claim_version=2,
        previous_event_hash=evt2.current_event_hash,
    )
    sqlite_session.commit()

    # Pre-condition check: verify that before tampering, the chain is completely valid
    pre_check = review_svc.audit_service.verify_audit_chain(sqlite_session, start_sequence=1, end_sequence=3)
    assert pre_check.is_valid is True, f"Pre-condition failed: audit chain was not valid: {pre_check.errors}"
    assert pre_check.total_events == 3

    # 2. Tamper with earlier event (Event 1): alter its stored metadata payload without recomputing hash
    tampered_meta = dict(evt1.metadata_json)
    tampered_meta["field_type"] = "tampered_med_name"
    evt1.metadata_json = tampered_meta
    sqlite_session.commit()

    # 3. Target claim review must fail closed because earlier event is tampered
    with pytest.raises(UnverifiableHistoricalActorError, match="failed integrity or provenance verification"):
        review_svc.accept_claim(
            session=sqlite_session,
            claim_id=v2.id,
            actor_id=reviewer.id,
            expected_version=2,
        )


# ─── 6. BATCH REVIEW & ATOMIC ROLLBACK TESTS ─────────────────────────────────

def test_batch_review_claims_atomic_success_and_rollback(sqlite_session: Session, review_svc: ClaimReviewService):
    """Batch review executes all operations or rolls back atomically on failure."""
    user_u = _seed_user(sqlite_session, "uploader")
    doc = _seed_document(sqlite_session, user_u)
    ev = _seed_evidence(sqlite_session, doc)

    c1 = _seed_genesis_claim(sqlite_session, doc, ev, value="Claim 1")
    c2 = _seed_genesis_claim(sqlite_session, doc, ev, value="Claim 2")

    reviewer = _seed_user(sqlite_session, "batch_reviewer")

    # 1. Successful batch
    batch_spec = [
        {"action": "ACCEPT", "claim_id": c1.id, "expected_version": 1},
        {"action": "REJECT", "claim_id": c2.id, "expected_version": 1, "reason_code": "CLINICALLY_INCORRECT"},
    ]
    results = review_svc.batch_review_claims(sqlite_session, batch_spec, reviewer.id)
    assert len(results) == 2
    assert results[0].review_state == ClaimReviewState.ACCEPTED
    assert results[1].review_state == ClaimReviewState.REJECTED

    # 2. Failing batch rolls back all items
    c3 = _seed_genesis_claim(sqlite_session, doc, ev, value="Claim 3")
    c4 = _seed_genesis_claim(sqlite_session, doc, ev, value="Claim 4")

    bad_batch = [
        {"action": "ACCEPT", "claim_id": c3.id, "expected_version": 1},
        {"action": "REJECT", "claim_id": c4.id, "expected_version": 999, "reason_code": "BAD_VER"},  # Version conflict
    ]
    with pytest.raises(OptimisticLockError):
        review_svc.batch_review_claims(sqlite_session, bad_batch, reviewer.id)

    # Verify c3 remains PENDING due to atomic rollback
    sqlite_session.expire_all()
    c3_db = sqlite_session.get(Claim, c3.id)
    assert c3_db.review_state == ClaimReviewState.PENDING


# ─── 7. REAL POSTGRESQL CONCURRENCY & DEADLOCK INTEGRATION TESTS ─────────────

@pytest.fixture(scope="module")
def pg_cluster() -> Generator[str, None, None]:
    """Start an ephemeral PostgreSQL cluster on port 54329 and yield connection URL."""
    initdb_path = r"D:\postgresql\bin\initdb.exe"
    postgres_path = r"D:\postgresql\bin\postgres.exe"

    if not os.path.exists(initdb_path) or not os.path.exists(postgres_path):
        pytest.skip("PostgreSQL 18 binaries not installed at D:\\postgresql\\bin")

    td = tempfile.mkdtemp(prefix="pg_f12_test_")
    port = 54329
    url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"

    try:
        subprocess.run(
            [initdb_path, "-D", td, "-U", "postgres", "-A", "trust", "--no-locale", "-E", "UTF8"],
            check=True,
            capture_output=True,
        )
        proc = subprocess.Popen(
            [postgres_path, "-D", td, "-p", str(port), "-c", "deadlock_timeout=50ms"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        import psycopg
        ready = False
        for _ in range(60):
            try:
                with psycopg.connect(f"postgresql://postgres@127.0.0.1:{port}/postgres", connect_timeout=1) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                ready = True
                break
            except Exception:
                time.sleep(0.1)

        if not ready:
            proc.terminate()
            raise RuntimeError("Ephemeral PostgreSQL failed to start.")

        yield url

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_postgresql_root_lock_blocks_concurrent_branching(pg_cluster: str):
    """
    Real PostgreSQL concurrency test:
    Verify that row-level SELECT ... FOR UPDATE on genesis v1 blocks concurrent edits,
    and the second unblocked transaction detects the update and rejects split-brain branching.
    """
    engine = create_engine(pg_cluster, pool_size=10, max_overflow=5)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    # Seed data
    with SessionMaker() as init_sess:
        seed_default_triage_policy(init_sess)
        op = _seed_user(init_sess, "pg_op", role="pipeline_operator")
        doc = _seed_document(init_sess, op)
        ev = _seed_evidence(init_sess, doc)
        v1 = _seed_genesis_claim(init_sess, doc, ev, value="Initial Value")
        user1 = _seed_user(init_sess, "pg_clinician_1")
        user2 = _seed_user(init_sess, "pg_clinician_2")
        init_sess.commit()
        claim_id = v1.id
        u1_id = user1.id
        u2_id = user2.id

    svc = ClaimReviewService(enforce_four_eyes=True)
    svc.strict_lifecycle = True

    t1_lock_acquired = threading.Event()
    t1_can_commit = threading.Event()
    t2_started = threading.Event()

    t1_result = {}
    t2_result = {}

    def worker_1():
        with SessionMaker() as s1:
            try:
                # Resolve and lock
                claim = svc._resolve_claim(s1, claim_id, expected_version=1)
                svc._lock_and_validate_lineage(s1, claim, u1_id, is_accept=False)
                t1_lock_acquired.set()

                # Wait for T2 to start attempting lock acquisition
                t1_can_commit.wait(timeout=5)

                # Complete edit
                res = svc.edit_claim(
                    session=s1,
                    claim_id=claim_id,
                    actor_id=u1_id,
                    new_value="Edited by T1",
                    reason_code="T1_EDIT",
                    expected_version=1,
                    commit=True,
                )
                t1_result["success"] = True
                t1_result["version"] = res.new_claim.version
            except Exception as exc:
                t1_result["error"] = exc

    def worker_2():
        # Wait until T1 holds the root lock
        t1_lock_acquired.wait(timeout=5)
        t2_started.set()

        with SessionMaker() as s2:
            try:
                # Concurrent edit attempt with expected_version=1
                # Must block on PostgreSQL SELECT ... FOR UPDATE until T1 commits
                # Once T1 commits, T2 will see v1 is EDITED or latest version is 2
                res = svc.edit_claim(
                    session=s2,
                    claim_id=claim_id,
                    actor_id=u2_id,
                    new_value="Edited by T2 (Concurrent Branch)",
                    reason_code="T2_EDIT",
                    expected_version=1,
                    commit=True,
                )
                t2_result["success"] = True
            except Exception as exc:
                t2_result["error"] = exc

    th1 = threading.Thread(target=worker_1)
    th2 = threading.Thread(target=worker_2)

    th1.start()
    th2.start()

    t2_started.wait(timeout=2)
    # T2 is now blocked on root lock in Postgres! Let T1 proceed to commit
    time.sleep(0.3)
    t1_can_commit.set()

    th1.join(timeout=5)
    th2.join(timeout=5)

    # Assertions
    assert t1_result.get("success") is True, f"T1 failed: {t1_result.get('error')}"
    assert t1_result.get("version") == 2

    # T2 must fail cleanly because v1 was already superseded; concurrent branching is prevented!
    assert t2_result.get("success") is not True, "T2 unexpectedly succeeded, causing split-brain branch!"
    err = t2_result.get("error")
    assert isinstance(err, (InvalidStateTransitionError, BranchingProhibitedError, InvalidClaimLifecycleStateError, OptimisticLockError, CorruptedClaimAncestryError))


def test_postgresql_deadlock_free_concurrent_batches(pg_cluster: str):
    """
    Real PostgreSQL deadlock prevention test:
    Two concurrent worker threads repeatedly submit batch reviews covering overlapping claims
    in conflicting input order (Batch 1: [A, B] vs Batch 2: [B, A]).
    Because batch_review_claims sorts lineages canonically before acquiring root locks,
    no PostgreSQL deadlock occurs.
    """
    engine = create_engine(pg_cluster, pool_size=10, max_overflow=5)
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)

    # Seed two independent lineages
    with SessionMaker() as s:
        seed_default_triage_policy(s)
        op = _seed_user(s, "batch_op", role="pipeline_operator")
        doc = _seed_document(s, op)
        ev = _seed_evidence(s, doc)
        ca = _seed_genesis_claim(s, doc, ev, value="Claim A")
        cb = _seed_genesis_claim(s, doc, ev, value="Claim B")
        u1 = _seed_user(s, "batch_user_1")
        u2 = _seed_user(s, "batch_user_2")
        s.commit()

        ca_id = ca.id
        cb_id = cb.id
        u1_id = u1.id
        u2_id = u2.id

    svc = ClaimReviewService(enforce_four_eyes=True)
    svc.strict_lifecycle = True

    errors = []

    def run_worker_a():
        with SessionMaker() as sess_a:
            try:
                # Reviews [ca, cb]
                svc.batch_review_claims(
                    sess_a,
                    [
                        {"action": "ESCALATE", "claim_id": ca_id, "expected_version": 1, "reason_code": "ESC_A"},
                        {"action": "ESCALATE", "claim_id": cb_id, "expected_version": 1, "reason_code": "ESC_B"},
                    ],
                    actor_id=u1_id,
                    commit=True,
                )
            except Exception as exc:
                errors.append(("Worker A", exc))

    def run_worker_b():
        with SessionMaker() as sess_b:
            try:
                # Reviews [cb, ca] in reverse order
                svc.batch_review_claims(
                    sess_b,
                    [
                        {"action": "ESCALATE", "claim_id": cb_id, "expected_version": 1, "reason_code": "ESC_B"},
                        {"action": "ESCALATE", "claim_id": ca_id, "expected_version": 1, "reason_code": "ESC_A"},
                    ],
                    actor_id=u2_id,
                    commit=True,
                )
            except Exception as exc:
                errors.append(("Worker B", exc))

    th_a = threading.Thread(target=run_worker_a)
    th_b = threading.Thread(target=run_worker_b)

    th_a.start()
    th_b.start()

    th_a.join(timeout=10)
    th_b.join(timeout=10)

    # Either one succeeds and the other gets an expected state transition conflict,
    # but CRITICALLY no PostgreSQL deadlock error ("deadlock detected") occurs!
    for worker_name, exc in errors:
        assert "deadlock detected" not in str(exc).lower(), f"Deadlock occurred in {worker_name}: {exc}"
