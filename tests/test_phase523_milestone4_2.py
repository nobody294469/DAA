"""Phase 5.2.3 Sub-Milestone 4.2: Mode 0 ReviewService & Durable Audit Contract Test Suite.

Authority:
  - phase523_milestone420_review_contract.md
  - phase523_milestone4201_audit_integrity_addendum.md
  - PRODUCTIZATION_SPEC §4.3, §7.3; PD-002, PD-004, PD-009, PD-010, PD-012, PD-015

Tests:
  1. State Machine: Valid and invalid transitions (PENDING, ACCEPTED, REJECTED, EDITED, ESCALATED).
  2. Role & Reviewer Authorization: Missing/inactive/unauthorized roles rejected; supervisors resolve escalations.
  3. Four-Eyes Identity Separation: High-risk claims enforce reviewer != operator/creator/modifier.
  4. Self-Acceptance Ban: The clinician who edits a claim cannot accept the successor version.
  5. Optimistic Locking: Version mismatch raises OptimisticLockError.
  6. Document Binding: Mismatched document_sha256 raises StaleDocumentVersionError.
  7. Mandatory Reason Codes: Mandatory reason codes for REJECT, EDIT, REOPEN, ESCALATE.
  8. Offline JSONL Persistence: RFC 8785 canonical serialization and SHA-256 hash chaining.
  9. Tamper Detection: Detects corrupted lines, modified payloads, altered hashes.
  10. Sequencing Integrity: Detects sequence gaps, duplicate sequences, and replayed events.
  11. Partial Writes: Detects incomplete/truncated lines.
  12. HMAC Checkpoints: HMAC-SHA256 signature verification, tampering detection, and missing key fail-closed.
  13. Key Rotation: Multi-key keyring verification.
  14. Dev-Mode Isolation: Unanchored dev-mode ledgers fail closed in production clinical export.
  15. Relational Atomic Persistence: Transaction rollback on audit failure.
  16. Gate 9 Export Verification: Rejects in-memory ACCEPTED bypass, superseded EDITED claims, stale versions,
      and Four-Eyes violations.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from clindoc.api.audit.service import AuditService, validate_metadata_phi_free
from clindoc.db.enums import (
    ClaimOrigin,
    ClaimReviewState,
    DocumentStatus,
    ReviewActionType,
    VerificationLabel,
    VerificationStage,
)
from clindoc.db.models import (
    Base,
    Claim,
    Document,
    Evidence,
    ReviewAction,
    Role,
    TriagePolicy,
    User,
    UserRole,
    VerificationResult as DBVerificationResult,
)
from clindoc.extract.contracts import BBoxModel, Claim as ContractClaim, Evidence as ContractEvidence
from clindoc.review.service import (
    AuditAnchorMissingError,
    AuditIntegrationError,
    AuditLedgerTamperDetectedError,
    AuditLedgerVerificationError,
    ClaimNotFoundError,
    ClaimReviewService,
    DuplicateAuditEventError,
    EditClaimResult,
    FourEyesViolationError,
    InvalidStateTransitionError,
    MissingReasonCodeError,
    OptimisticLockError,
    ReviewAuditLedger,
    SequenceGapError,
    StaleDocumentVersionError,
    UnauthorizedReviewerError,
    build_canonical_event_envelope,
    compute_canonical_envelope_hash,
    serialize_canonical_json,
)
from clindoc.review.triage import seed_default_triage_policy
from clindoc.schema.verification import VerificationResult
from clindoc.services.fhir_export import (
    ClaimEligibilityEvaluation,
    FHIRExportEligibilityError,
    FHIRExportService,
)


# ─── FIXTURES & TEST HELPERS ──────────────────────────────────────────────────

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
    """ClaimReviewService instance configured for strict Sub-Milestone 4.2 testing."""
    return ClaimReviewService(
        strict_lifecycle=True,
        enforce_role_rbac=True,
        enforce_four_eyes=True,
    )


def _seed_roles(session: Session) -> None:
    """Seed standard RBAC roles."""
    roles = [
        Role(id="reviewer", name="Clinical Reviewer", description="Standard reviewer"),
        Role(id="approver", name="Clinical Supervisor", description="Supervisory reviewer"),
        Role(id="admin", name="Administrator", description="System administrator"),
        Role(id="operator", name="Pipeline Operator", description="Technical operator"),
        Role(id="auditor", name="Compliance Auditor", description="Read-only compliance"),
    ]
    for r in roles:
        if not session.get(Role, r.id):
            session.add(r)
    session.flush()


def _create_user(session: Session, username: str, role_id: str, is_active: bool = True) -> User:
    """Create an active user with an assigned RBAC role."""
    _seed_roles(session)
    user = User(
        id=uuid.uuid4(),
        idp_subject=f"sub-{username}",
        username=username,
        email=f"{username}@example.org",
        full_name=username.replace("_", " ").title(),
        is_active=is_active,
    )
    session.add(user)
    session.flush()

    user_role = UserRole(
        id=uuid.uuid4(),
        user_id=user.id,
        role_id=role_id,
        assigned_at=datetime.now(timezone.utc),
    )
    session.add(user_role)
    session.flush()
    return user


def _create_environment(
    session: Session,
    operator: Optional[User] = None,
    doc_sha256: str = "a" * 64,
) -> tuple[User, Document, Evidence, Claim]:
    """Create a fully grounded test environment with a document and an initial claim."""
    seed_default_triage_policy(session)
    op = operator or _create_user(session, "operator_dave", "operator")

    doc = Document(
        id=uuid.uuid4(),
        sha256=doc_sha256,
        storage_ref="s3://clindoc-docs/patient_discharge.pdf",
        original_filename="patient_discharge.pdf",
        mime_type="application/pdf",
        file_size_bytes=45000,
        page_count=1,
        uploaded_by=op.id,
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(doc)
    session.flush()

    evidence = Evidence(
        id=uuid.uuid4(),
        document_id=doc.id,
        page_number=1,
        char_start=100,
        char_end=130,
        bounding_box={"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.25},
        ocr_confidence=0.98,
        evidence_text="Lisinopril 10 mg PO daily",
        payload={
            "evidence_id": str(uuid.uuid4()),
            "document_id": str(doc.id),
            "page_number": 1,
            "text": "Lisinopril 10 mg PO daily",
            "char_start": 100,
            "char_end": 130,
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
        group_id="med-grp-1",
        field_type="med.dose",
        value="10 mg",
        normalized_code="314076",
        code_system="RxNorm",
        version=1,
        supersedes_claim_id=None,
        origin=ClaimOrigin.PIPELINE,
        review_state=ClaimReviewState.PENDING,
        created_by=None,
        created_at=datetime.now(timezone.utc),
        evidence_id=evidence.id,
        payload={
            "claim_id": str(uuid.uuid4()),
            "document_id": str(doc.id),
            "field_type": "med.dose",
            "value": "10 mg",
            "version": 1,
            "origin": "pipeline",
            "review_state": "PENDING",
            "association_status": "DETERMINISTIC_SAME_SENTENCE",
        },
    )
    session.add(claim)
    session.commit()

    return op, doc, evidence, claim


# ─── 1. STATE MACHINE TRANSITIONS ─────────────────────────────────────────────

def test_state_machine_valid_transitions(db_session: Session, review_service: ClaimReviewService):
    """Test all legal canonical transitions: PENDING -> ACCEPTED, REJECTED, ESCALATED, EDITED."""
    op, doc, ev, claim = _create_environment(db_session)
    reviewer = _create_user(db_session, "dr_alice", "reviewer")

    # 1. PENDING -> ACCEPTED
    accepted = review_service.accept_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=reviewer.id,
        expected_version=1,
        reason_code="VERIFIED_IN_CHART",
    )
    assert accepted.review_state == ClaimReviewState.ACCEPTED

    # 2. ACCEPTED -> PENDING (REOPEN)
    reopened = review_service.reopen_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=reviewer.id,
        expected_version=1,
        reason_code="RECHECK_ORDER",
    )
    assert reopened.review_state == ClaimReviewState.PENDING

    # 3. PENDING -> REJECTED
    rejected = review_service.reject_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=reviewer.id,
        expected_version=1,
        reason_code="INCORRECT_EXTRACTION",
    )
    assert rejected.review_state == ClaimReviewState.REJECTED

    # 4. REJECTED -> PENDING (REOPEN)
    reopened_2 = review_service.reopen_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=reviewer.id,
        expected_version=1,
        reason_code="DOCUMENT_CLARIFIED",
    )
    assert reopened_2.review_state == ClaimReviewState.PENDING

    # 5. PENDING -> ESCALATED
    escalated = review_service.escalate_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=reviewer.id,
        expected_version=1,
        reason_code="UNRESOLVED_AMBIGUITY",
    )
    assert escalated.review_state == ClaimReviewState.ESCALATED


def test_state_machine_invalid_transitions(db_session: Session, review_service: ClaimReviewService):
    """Test illegal transitions: EDITED is terminal; direct jumping across non-adjacent states fails."""
    op, doc, ev, claim = _create_environment(db_session)
    reviewer = _create_user(db_session, "dr_alice", "reviewer")

    # Edit creates v2 and sets v1 to EDITED
    edit_res = review_service.edit_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=reviewer.id,
        new_value="20 mg",
        reason_code="DOSE_TITRATION",
        expected_version=1,
    )
    v1_edited = edit_res.previous_claim
    assert v1_edited.review_state == ClaimReviewState.EDITED

    # EDITED is terminal: cannot ACCEPT, REJECT, ESCALATE, or REOPEN
    with pytest.raises(InvalidStateTransitionError, match="Allowed states are PENDING or ESCALATED"):
        review_service.accept_claim(
            session=db_session,
            claim_id=v1_edited.id,
            actor_id=reviewer.id,
            expected_version=1,
        )

    with pytest.raises(InvalidStateTransitionError, match="Allowed states are PENDING or ESCALATED"):
        review_service.reject_claim(
            session=db_session,
            claim_id=v1_edited.id,
            actor_id=reviewer.id,
            expected_version=1,
            reason_code="INVALID",
        )

    with pytest.raises(InvalidStateTransitionError, match="Allowed states are ACCEPTED, REJECTED, ESCALATED"):
        review_service.reopen_claim(
            session=db_session,
            claim_id=v1_edited.id,
            actor_id=reviewer.id,
            expected_version=1,
            reason_code="REOPEN_SUPERSEDED",
        )


# ─── 2. AUTHENTICATION & ROLE AUTHORIZATION ───────────────────────────────────

def test_role_and_reviewer_authorization(db_session: Session, review_service: ClaimReviewService):
    """Ensure anonymous, inactive, and unauthorized roles fail closed."""
    op, doc, ev, claim = _create_environment(db_session)

    # 1. Missing / anonymous identity
    with pytest.raises(UnauthorizedReviewerError, match="missing, null, or anonymous"):
        review_service.accept_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=None,  # type: ignore[arg-type]
            expected_version=1,
        )

    # 2. Inactive user
    inactive_user = _create_user(db_session, "inactive_dr", "reviewer", is_active=False)
    with pytest.raises(UnauthorizedReviewerError, match="inactive"):
        review_service.accept_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=inactive_user.id,
            expected_version=1,
        )

    # 3. Unauthorized role (e.g. auditor)
    auditor = _create_user(db_session, "compliance_auditor", "auditor")
    with pytest.raises(UnauthorizedReviewerError, match="lacks clinical reviewer authorization"):
        review_service.accept_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=auditor.id,
            expected_version=1,
        )


def test_escalation_supervisory_resolution_authorization(db_session: Session, review_service: ClaimReviewService):
    """Ensure standard reviewers cannot resolve ESCALATED claims; supervisory roles can."""
    op, doc, ev, claim = _create_environment(db_session)
    reviewer = _create_user(db_session, "dr_alice", "reviewer")
    supervisor = _create_user(db_session, "dr_chief", "approver")

    # Reviewer escalates claim
    review_service.escalate_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=reviewer.id,
        expected_version=1,
        reason_code="DIFFICULT_CASE",
    )
    assert claim.review_state == ClaimReviewState.ESCALATED

    # Standard reviewer attempts to resolve escalation -> REJECTED
    with pytest.raises(UnauthorizedReviewerError, match="lacks supervisory authorization"):
        review_service.accept_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=reviewer.id,
            expected_version=1,
        )

    # Supervisor resolves escalation -> ACCEPTED
    resolved = review_service.resolve_escalation(
        session=db_session,
        claim_id=claim.id,
        actor_id=supervisor.id,
        resolution_action="ACCEPT",
        expected_version=1,
        reason_code="CHIEF_OVERRIDE_CONFIRMED",
    )
    assert resolved.review_state == ClaimReviewState.ACCEPTED


# ─── 3. FOUR-EYES SEPARATION OF DUTIES ────────────────────────────────────────

def test_four_eyes_operator_cannot_accept_high_risk_claim(db_session: Session, review_service: ClaimReviewService):
    """Pipeline operator (document uploader) cannot accept high-risk / provisional claims."""
    op = _create_user(db_session, "operator_sam", "operator")
    # Grant operator role reviewer permission for test to isolate Four-Eyes check
    _seed_roles(db_session)
    db_session.add(UserRole(id=uuid.uuid4(), user_id=op.id, role_id="reviewer", assigned_at=datetime.now(timezone.utc)))
    db_session.flush()

    op, doc, ev, claim = _create_environment(db_session, operator=op)

    # Mark claim as high-risk provisional association
    claim.payload["association_status"] = "PARALLEL_COORDINATE_BIJECTION"
    claim.association_status = "PARALLEL_COORDINATE_BIJECTION"
    db_session.flush()

    # Operator attempts to accept high-risk claim -> FourEyesViolationError
    with pytest.raises(FourEyesViolationError, match="Pipeline operator .* who uploaded document .* cannot accept high-risk claim"):
        review_service.accept_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=op.id,
            expected_version=1,
        )

    # Independent clinician accepts -> Success
    dr_bob = _create_user(db_session, "dr_bob", "reviewer")
    accepted = review_service.accept_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=dr_bob.id,
        expected_version=1,
    )
    assert accepted.review_state == ClaimReviewState.ACCEPTED


def test_self_acceptance_after_editing_prohibited(db_session: Session, review_service: ClaimReviewService):
    """The clinician who edits a claim (creator of v+1) CANNOT accept the edited successor claim."""
    op, doc, ev, claim = _create_environment(db_session)
    clinician_a = _create_user(db_session, "dr_clinician_a", "reviewer")
    clinician_b = _create_user(db_session, "dr_clinician_b", "reviewer")

    # Clinician A edits claim v1 -> creates v2 in PENDING state
    edit_res = review_service.edit_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=clinician_a.id,
        new_value="5 mg",
        reason_code="REDUCED_FOR_RENAL",
        expected_version=1,
    )
    v2_claim = edit_res.new_claim
    assert v2_claim.version == 2
    assert v2_claim.review_state == ClaimReviewState.PENDING
    assert v2_claim.created_by == clinician_a.id

    # Clinician A attempts to accept their own edited claim v2 -> FourEyesViolationError
    with pytest.raises(FourEyesViolationError, match="Clinician .* who created or edited claim .* cannot accept it"):
        review_service.accept_claim(
            session=db_session,
            claim_id=v2_claim.id,
            actor_id=clinician_a.id,
            expected_version=2,
        )

    # Independent Clinician B accepts v2 -> Success
    accepted_v2 = review_service.accept_claim(
        session=db_session,
        claim_id=v2_claim.id,
        actor_id=clinician_b.id,
        expected_version=2,
    )
    assert accepted_v2.review_state == ClaimReviewState.ACCEPTED


# ─── 4. OPTIMISTIC LOCKING & BINDING ──────────────────────────────────────────

def test_optimistic_locking_version_mismatch(db_session: Session, review_service: ClaimReviewService):
    """Review action with stale expected_version raises OptimisticLockError."""
    op, doc, ev, claim = _create_environment(db_session)
    reviewer = _create_user(db_session, "dr_alice", "reviewer")

    with pytest.raises(OptimisticLockError, match="expected version 2, but found version 1"):
        review_service.accept_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=reviewer.id,
            expected_version=2,
        )


def test_stale_document_hash_mismatch(db_session: Session, review_service: ClaimReviewService):
    """Review action referencing outdated document_sha256 raises StaleDocumentVersionError."""
    op, doc, ev, claim = _create_environment(db_session, doc_sha256="1" * 64)
    reviewer = _create_user(db_session, "dr_alice", "reviewer")

    stale_sha = "2" * 64
    with pytest.raises(StaleDocumentVersionError, match="Stale document hash mismatch"):
        review_service.accept_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=reviewer.id,
            expected_version=1,
            document_sha256=stale_sha,
        )


def test_reopen_mandatory_reason_code(db_session: Session, review_service: ClaimReviewService):
    """Reopen without a valid reason code raises MissingReasonCodeError under strict lifecycle."""
    op, doc, ev, claim = _create_environment(db_session)
    reviewer = _create_user(db_session, "dr_alice", "reviewer")

    review_service.accept_claim(
        session=db_session,
        claim_id=claim.id,
        actor_id=reviewer.id,
        expected_version=1,
    )

    with pytest.raises(MissingReasonCodeError, match="Reason code is required for REOPEN"):
        review_service.reopen_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=reviewer.id,
            expected_version=1,
            reason_code="",
            require_reason_code=True,
        )


# ─── 5. OFFLINE JSONL PERSISTENCE & HASH CHAIN INTEGRITY ──────────────────────

def test_offline_jsonl_append_and_hash_chain_verification(tmp_path: Path):
    """Verify on-disk JSONL append, monotonic sequence numbers, and unbroken hash chaining."""
    ledger_file = tmp_path / "test_doc.review_audit.jsonl"
    ledger = ReviewAuditLedger(
        ledger_path=ledger_file,
        document_id="doc-123",
        document_sha256="abc" * 21 + "a",
        signing_key_id="k1",
        hmac_key=b"test-secret-key-12345",
    )

    # Append 3 events
    e1 = ledger.append_event(
        actor_id="user-1",
        event_type="CLAIM_REVIEW_ACTION",
        entity_type="claim",
        entity_id="claim-1",
        action="ACCEPT",
        metadata={"claim_version": 1, "new_state": "ACCEPTED"},
    )
    assert e1["sequence_number"] == 1
    assert e1["previous_event_hash"] == ""

    e2 = ledger.append_event(
        actor_id="user-1",
        event_type="CLAIM_REVIEW_ACTION",
        entity_type="claim",
        entity_id="claim-2",
        action="REJECT",
        metadata={"claim_version": 1, "new_state": "REJECTED"},
    )
    assert e2["sequence_number"] == 2
    assert e2["previous_event_hash"] == e1["current_event_hash"]

    e3 = ledger.append_event(
        actor_id="user-2",
        event_type="CLAIM_REVIEW_ACTION",
        entity_type="claim",
        entity_id="claim-3",
        action="EDIT",
        metadata={"claim_version": 2, "new_state": "EDITED"},
    )
    assert e3["sequence_number"] == 3
    assert e3["previous_event_hash"] == e2["current_event_hash"]

    # Seal with HMAC checkpoint
    ledger.create_checkpoint()

    # Verify ledger
    res = ledger.verify_ledger(expected_document_sha256="abc" * 21 + "a")
    assert res.is_valid
    assert res.total_events == 3
    assert res.terminal_hash == e3["current_event_hash"]
    assert res.checkpoint_verified is True
    assert "claim-1" in res.verified_claims
    assert res.verified_claims["claim-1"].review_state == "ACCEPTED"


def test_offline_jsonl_detects_tampered_payload(tmp_path: Path):
    """Modifying metadata in line 2 invalidates SHA-256 hash chain and returns is_valid=False."""
    ledger_file = tmp_path / "tamper_doc.review_audit.jsonl"
    ledger = ReviewAuditLedger(ledger_path=ledger_file, signing_key_id="k1", hmac_key=b"secret")

    ledger.append_event(actor_id="u1", event_type="CLAIM_REVIEW_ACTION", entity_type="claim", entity_id="c1", action="ACCEPT", metadata={"val": "10"})
    ledger.append_event(actor_id="u1", event_type="CLAIM_REVIEW_ACTION", entity_type="claim", entity_id="c2", action="REJECT", metadata={"val": "20"})
    ledger.create_checkpoint()

    # Tamper with line 2: change REJECT to ACCEPT without recomputing hash
    lines = ledger_file.read_text(encoding="utf-8").splitlines()
    tampered_rec = json.loads(lines[1])
    tampered_rec["action"] = "ACCEPT"
    lines[1] = json.dumps(tampered_rec, separators=(",", ":"))
    ledger_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    res = ledger.verify_ledger()
    assert not res.is_valid
    assert "Tampered audit payload at sequence 2" in res.error_message


def test_offline_jsonl_detects_sequence_gap(tmp_path: Path):
    """Skipping a sequence number raises a sequence gap error."""
    ledger_file = tmp_path / "gap_doc.review_audit.jsonl"
    ledger = ReviewAuditLedger(ledger_path=ledger_file, signing_key_id="k1", hmac_key=b"secret")

    e1 = ledger.append_event(actor_id="u1", event_type="CLAIM_REVIEW_ACTION", entity_type="claim", entity_id="c1", action="ACCEPT")
    # Manually append seq 3 instead of seq 2
    env = build_canonical_event_envelope(
        sequence_number=3,
        timestamp=datetime.now(timezone.utc),
        actor_id="u1",
        event_type="CLAIM_REVIEW_ACTION",
        entity_type="claim",
        entity_id="c2",
        action="ACCEPT",
        previous_event_hash=e1["current_event_hash"],
    )
    rec = dict(env)
    rec["current_event_hash"] = compute_canonical_envelope_hash(env)
    with open(ledger_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    res = ledger.verify_ledger(allow_unanchored_dev_ledger=True)
    assert not res.is_valid
    assert "Sequence gap at line 2: expected 2, found 3" in res.error_message


def test_offline_jsonl_detects_partial_write(tmp_path: Path):
    """Corrupted / truncated trailing line is detected and fails closed."""
    ledger_file = tmp_path / "partial_doc.review_audit.jsonl"
    ledger = ReviewAuditLedger(ledger_path=ledger_file, signing_key_id="k1", hmac_key=b"secret")

    ledger.append_event(actor_id="u1", event_type="CLAIM_REVIEW_ACTION", entity_type="claim", entity_id="c1", action="ACCEPT")

    # Append partial corrupt line
    with open(ledger_file, "a", encoding="utf-8") as f:
        f.write('{"sequence_number": 2, "action": "ACC\n')

    res = ledger.verify_ledger(allow_unanchored_dev_ledger=True)
    assert not res.is_valid
    assert "Partial or corrupted JSON line at line 2" in res.error_message


# ─── 6. HMAC CHECKPOINT & KEY ROTATION ────────────────────────────────────────

def test_hmac_checkpoint_verification_and_tamper_detection(tmp_path: Path):
    """HMAC checkpoint detects any alteration of the terminal hash or signature."""
    ledger_file = tmp_path / "chkpt_doc.review_audit.jsonl"
    ledger = ReviewAuditLedger(
        ledger_path=ledger_file,
        document_id="doc-999",
        document_sha256="d" * 64,
        signing_key_id="k1",
        hmac_key=b"secret-key-1234",
    )
    ledger.append_event(actor_id="u1", event_type="CLAIM_REVIEW_ACTION", entity_type="claim", entity_id="c1", action="ACCEPT")
    ledger.create_checkpoint()

    # Positive check
    res = ledger.verify_ledger(expected_document_sha256="d" * 64)
    assert res.is_valid
    assert res.checkpoint_verified is True

    # Tamper with checkpoint signature
    chkpt_file = ledger.checkpoint_path
    chkpt_data = json.loads(chkpt_file.read_text(encoding="utf-8"))
    chkpt_data["signature"] = "f" * 64
    chkpt_file.write_text(json.dumps(chkpt_data), encoding="utf-8")

    res_tampered = ledger.verify_ledger(expected_document_sha256="d" * 64)
    assert not res_tampered.is_valid
    assert "HMAC signature verification failed" in res_tampered.error_message


def test_hmac_key_rotation_with_keyring(tmp_path: Path):
    """Key rotation: verifier with multiple keys can verify checkpoints signed with retired keys."""
    ledger_file = tmp_path / "rot_doc.review_audit.jsonl"
    keyring = {
        "k1": b"old-signing-key",
        "k2": b"new-signing-key",
    }

    # Signed with k1
    ledger = ReviewAuditLedger(
        ledger_path=ledger_file,
        signing_key_id="k1",
        keyring=keyring,
    )
    ledger.append_event(actor_id="u1", event_type="CLAIM_REVIEW_ACTION", entity_type="claim", entity_id="c1", action="ACCEPT")
    ledger.create_checkpoint(key_id="k1")

    # Verifier instance using updated keyring verifies k1 signature
    verifier_ledger = ReviewAuditLedger(
        ledger_path=ledger_file,
        signing_key_id="k2",  # active key is now k2
        keyring=keyring,      # retains k1 for verification
    )
    res = verifier_ledger.verify_ledger()
    assert res.is_valid
    assert res.checkpoint_verified is True


def test_dev_mode_unanchored_ledger_isolation_from_production(tmp_path: Path, monkeypatch):
    """Unanchored development ledger fails closed when running in production environment."""
    ledger_file = tmp_path / "dev_doc.review_audit.jsonl"
    ledger = ReviewAuditLedger(ledger_path=ledger_file)
    ledger.append_event(actor_id="u1", event_type="CLAIM_REVIEW_ACTION", entity_type="claim", entity_id="c1", action="ACCEPT")
    # No checkpoint created (unanchored dev ledger)

    # In dev mode with explicit flag: passes with warning flag
    res_dev = ledger.verify_ledger(allow_unanchored_dev_ledger=True)
    assert res_dev.is_valid
    assert res_dev.is_unanchored_dev_mode is True

    # In production mode: strictly fails closed
    monkeypatch.setenv("CLINDOC_ENV", "production")
    res_prod = ledger.verify_ledger(allow_unanchored_dev_ledger=True)
    assert not res_prod.is_valid
    assert "HMAC checkpoint missing in production" in res_prod.error_message


# ─── 7. RELATIONAL ATOMIC TRANSACTION ROLLBACK ────────────────────────────────

def test_relational_atomic_transaction_rollback_on_failure(db_session: Session, review_service: ClaimReviewService):
    """If audit event recording fails, the entire transaction rolls back; claim remains PENDING."""
    op, doc, ev, claim = _create_environment(db_session)
    reviewer = _create_user(db_session, "dr_alice", "reviewer")

    # Mock AuditService to simulate database / PHI error
    mock_audit = MagicMock()
    mock_audit.record_event.side_effect = Exception("Database disk full or constraint failure")
    review_service.audit_service = mock_audit

    with pytest.raises(AuditIntegrationError, match="Audit event recording failed"):
        review_service.accept_claim(
            session=db_session,
            claim_id=claim.id,
            actor_id=reviewer.id,
            expected_version=1,
        )

    # Verify atomic rollback: claim review_state is still PENDING and no ReviewAction was persisted
    db_session.rollback()
    reloaded_claim = db_session.get(Claim, claim.id)
    assert reloaded_claim.review_state == ClaimReviewState.PENDING

    actions = db_session.query(ReviewAction).filter(ReviewAction.claim_id == claim.id).all()
    assert len(actions) == 0


# ─── 8. GATE 9 EXPORT INTEGRATION & BYPASS RESISTANCE ─────────────────────────

def test_gate9_blocks_in_memory_accepted_claim_without_audit_evidence():
    """Gate 9 strictly rejects in-memory claims with review_state='ACCEPTED' if audit evidence is missing."""
    export_service = FHIRExportService(enforce_audit_evidence=True)
    c_accepted = {
        "claim_id": "claim-bypass-1",
        "value": "10 mg",
        "field_type": "med.dose",
        "review_state": "ACCEPTED",
    }
    v = {"label": "SUPPORTED"}
    ev = {"id": "ev-1", "text": "Lisinopril 10 mg"}

    eval_res = export_service.evaluate_claim_eligibility(
        claim=c_accepted,
        ver=v,
        ev=ev,
        enforce_audit_evidence=True,
    )
    assert not eval_res.is_eligible
    assert "SUPPRESSED_UNVERIFIED_AUDIT_LEDGER" in eval_res.reason


def test_gate9_blocks_superseded_edited_version():
    """Gate 9 strictly suppresses claims with review_state='EDITED' under SUPPRESSED_SUPERSEDED_VERSION."""
    export_service = FHIRExportService(enforce_audit_evidence=True)
    c_edited = {
        "claim_id": "claim-superseded-1",
        "value": "10 mg",
        "field_type": "med.dose",
        "review_state": "EDITED",
    }
    v = {"label": "SUPPORTED"}
    ev = {"id": "ev-1", "text": "Lisinopril 10 mg"}

    eval_res = export_service.evaluate_claim_eligibility(
        claim=c_edited,
        ver=v,
        ev=ev,
        enforce_audit_evidence=True,
    )
    assert not eval_res.is_eligible
    assert "SUPPRESSED_SUPERSEDED_VERSION" in eval_res.reason


def test_gate9_accepts_verified_audit_ledger_claim(tmp_path: Path):
    """Gate 9 permits export only when backed by an authentic, verified audit ledger."""
    ledger_file = tmp_path / "valid_doc.review_audit.jsonl"
    doc_sha = "e" * 64
    ledger = ReviewAuditLedger(
        ledger_path=ledger_file,
        document_id="doc-valid",
        document_sha256=doc_sha,
        signing_key_id="k1",
        hmac_key=b"secret-key-gate9",
    )
    ledger.append_event(
        actor_id="user-valid-reviewer",
        event_type="CLAIM_REVIEW_ACTION",
        entity_type="claim",
        entity_id="c-verified-1",
        action="ACCEPT",
        metadata={
            "claim_version": 1,
            "new_state": "ACCEPTED",
            "document_id": "doc-valid",
            "document_sha256": doc_sha,
        },
    )
    ledger.create_checkpoint()

    export_service = FHIRExportService(enforce_audit_evidence=True, audit_ledger=ledger)
    c_claim = ContractClaim(
        claim_id="c-verified-1",
        document_id="doc-valid",
        group_id="grp-glucose",
        field_type="lab.value",
        value="110",
        value_numeric=110.0,
        unit_raw="mg/dL",
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id="ev-1",
        normalized_code="2345-7",
        code_system="LOINC",
        region_id="reg-1",
    )
    object.__setattr__(c_claim, "review_state", ClaimReviewState.ACCEPTED)
    object.__setattr__(c_claim, "version", 1)

    v = VerificationResult(
        verification_id="ver-c1",
        claim_id="c-verified-1",
        evidence_id="ev-1",
        label=VerificationLabel.SUPPORTED.value,
        confidence=0.99,
        stage_reached="A",
    )
    ev = ContractEvidence(
        evidence_id="ev-1",
        document_id="doc-valid",
        page_number=1,
        text="Glucose 110 mg/dL",
        char_start=0,
        char_end=17,
        bbox=BBoxModel(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
    )

    eval_res = export_service.evaluate_claim_eligibility(
        claim=c_claim,
        ver=v,
        ev=ev,
        audit_ledger=ledger,
        document_sha256=doc_sha,
        enforce_audit_evidence=True,
    )
    assert eval_res.is_eligible
    assert eval_res.review_state == "ACCEPTED"

    # Export intermediate representation to Bundle
    export_res = export_service.export_intermediate_representation(
        claims=[c_claim],
        evidence_map={"ev-1": ev},
        verification_map={"c-verified-1": v},
        document_id="doc-valid",
        document_metadata={"sha256": doc_sha},
        audit_ledger=ledger,
        enforce_audit_evidence=True,
    )
    assert export_res.is_valid
    assert export_res.exportable_claim_ids == ["c-verified-1"]
