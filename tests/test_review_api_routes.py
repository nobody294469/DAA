"""Integration and RBAC Tests for Phase 8.5 Step 4: FastAPI Review Routes & RBAC Integration.

Authority:
  - PRODUCTIZATION_SPEC.md §1.2 (Role separation: clinical_reviewer, approver, admin PHI denial)
  - PRODUCTIZATION_SPEC.md §4.3 (claims, review_actions, document_approvals, audit_events)
  - PRODUCTIZATION_SPEC.md §7.2 (Triage policy engine, review queue priority)
  - PRODUCTIZATION_SPEC.md §7.3 (Reviewer actions, Four-Eyes approval gate)
  - PRODUCTIZATION_SPEC.md §9.3 (Review endpoints under /api/v1)
  - PRODUCTIZATION_DECISIONS.md PD-002 (Mode 0: 100% human review prior to export)
  - PRODUCTIZATION_DECISIONS.md PD-009 (RBAC, Four-Eyes principle, system_admin PHI denial)
  - PRODUCTIZATION_DECISIONS.md PD-010 (Immutable claim history: edits create new versions)
  - PRODUCTIZATION_DECISIONS.md PD-012 (Hash-chained audit logging, fail-closed atomic)
  - PRODUCTIZATION_DECISIONS.md PD-015 (Explicit human patient identity confirmation)

Test Scenarios Covered:
  1. Reviewer can retrieve review queue
  2. Approver can retrieve review queue
  3. Unauthorized roles are denied queue access (document_operator, auditor, system_admin, integration_service)
  4. Authorized reviewer can retrieve claims
  5. Unauthorized user cannot retrieve another user's protected document/claims
  6. Reviewer can ACCEPT a claim
  7. Reviewer can EDIT a claim
  8. Reviewer can REJECT, ESCALATE, and REOPEN appropriately
  9. Invalid claim action returns correct HTTP error
  10. Missing reason code returns validation error (HTTP 422)
  11. Optimistic locking conflict returns conflict response (HTTP 409)
  12. Reviewer can confirm identity
  13. Approver can confirm identity where permitted
  14. Unauthorized identity confirmation is denied
  15. Approver can approve an eligible document
  16. Reviewer cannot approve a document (HTTP 403)
  17. Uploader cannot approve their own document (Four-Eyes HTTP 409)
  18. Approval prerequisites are enforced by the underlying service (HTTP 409)
  19. System_admin cannot access PHI review endpoints (HTTP 403 across all review routes)
  20. API responses do not expose ciphertext/secrets
  21. Audit behavior remains intact through API calls
"""

from __future__ import annotations

import datetime
from datetime import timezone
import io
import json
from pathlib import Path
from typing import Any, Optional
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from clindoc.api.app import create_app
from clindoc.api.audit.service import AuditService
from clindoc.api.auth import (
    CanonicalRole,
    OIDCConfig,
    OIDCTokenValidator,
    set_global_session_factory,
    set_global_token_validator,
)
from clindoc.api.deps import (
    set_global_approval_engine,
    set_global_audit_service,
    set_global_blob_store,
    set_global_claim_review_service,
    set_global_job_queue,
    set_global_triage_engine,
)
from clindoc.db import (
    AuditEvent,
    Base,
    Claim,
    ClaimOrigin,
    ClaimReviewState,
    ContradictionType,
    Document,
    DocumentApproval,
    DocumentApprovalStatus,
    DocumentStatus,
    Evidence,
    IdentityVerificationStatus,
    Role,
    TriagePolicy,
    User,
    UserRole,
    VerificationLabel,
    VerificationResult,
    VerificationStage,
)
from clindoc.jobs.queue import JobQueue
from clindoc.review.approval import DocumentApprovalEngine
from clindoc.review.service import ClaimReviewService
from clindoc.review.triage import TriageEngine, seed_default_triage_policy
from clindoc.storage.blob import EncryptedFileBlobStore


# ─── FIXTURES & HARNESS ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def rsa_keypair():
    """Deterministic RSA keypair for cryptographic JWT signing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    jwk_dict = RSAAlgorithm.to_jwk(public_key)
    if isinstance(jwk_dict, str):
        jwk_dict = json.loads(jwk_dict)
    jwk_dict["kid"] = "review-test-key-1"
    jwk_dict["alg"] = "RS256"
    jwk_dict["use"] = "sig"

    return {
        "private_key": private_key,
        "public_key": public_key,
        "public_pem": pub_pem,
        "kid": "review-test-key-1",
        "jwks": {"keys": [jwk_dict]},
    }


def make_token(
    keypair: dict[str, Any],
    *,
    sub: str,
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    """Generate signed JWT token for subject with given claims."""
    now = datetime.datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": sub,
        "iss": "https://idp.clindoc.test/auth",
        "aud": "clindoc-api",
        "iat": int(now.timestamp()),
        "nbf": int((now - datetime.timedelta(seconds=10)).timestamp()),
        "exp": int((now + datetime.timedelta(seconds=3600)).timestamp()),
    }
    if extra_claims:
        claims.update(extra_claims)

    return jwt.encode(
        claims,
        keypair["private_key"],
        algorithm="RS256",
        headers={"kid": keypair["kid"]},
    )


@pytest.fixture
def review_env(tmp_path, rsa_keypair):
    """Integrated review test environment with in-memory SQLite and seeded roles/users."""
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

    # 1. Seed canonical roles
    canonical_roles = [
        Role(id=CanonicalRole.SYSTEM_ADMIN.value, name="System Administrator", description="Admin operations"),
        Role(id=CanonicalRole.DOCUMENT_OPERATOR.value, name="Document Operator", description="Document uploads"),
        Role(id=CanonicalRole.CLINICAL_REVIEWER.value, name="Clinical Reviewer", description="Clinical review"),
        Role(id=CanonicalRole.APPROVER.value, name="Approver", description="Document approval"),
        Role(id=CanonicalRole.AUDITOR.value, name="Auditor", description="Audit logs"),
        Role(id=CanonicalRole.INTEGRATION_SERVICE.value, name="Integration Service", description="FHIR export"),
    ]
    for r in canonical_roles:
        session.add(r)

    # 2. Seed test users
    users = {
        "admin": User(id=uuid.uuid4(), idp_subject="sub-admin", username="admin_user", full_name="Admin User", email="admin@test.local", is_active=True),
        "operator": User(id=uuid.uuid4(), idp_subject="sub-op", username="op_user", full_name="Operator User", email="op@test.local", is_active=True),
        "operator2": User(id=uuid.uuid4(), idp_subject="sub-op2", username="op_user_2", full_name="Operator User 2", email="op2@test.local", is_active=True),
        "reviewer": User(id=uuid.uuid4(), idp_subject="sub-rev", username="rev_user", full_name="Reviewer User", email="rev@test.local", is_active=True),
        "approver": User(id=uuid.uuid4(), idp_subject="sub-app", username="app_user", full_name="Approver User", email="app@test.local", is_active=True),
        "auditor": User(id=uuid.uuid4(), idp_subject="sub-aud", username="aud_user", full_name="Auditor User", email="aud@test.local", is_active=True),
        "integration": User(id=uuid.uuid4(), idp_subject="sub-int", username="int_user", full_name="Integration User", email="int@test.local", is_active=True),
    }
    for u in users.values():
        session.add(u)
    session.flush()

    # 3. Assign roles
    session.add(UserRole(user_id=users["admin"].id, role_id=CanonicalRole.SYSTEM_ADMIN.value))
    session.add(UserRole(user_id=users["operator"].id, role_id=CanonicalRole.DOCUMENT_OPERATOR.value))
    session.add(UserRole(user_id=users["operator2"].id, role_id=CanonicalRole.DOCUMENT_OPERATOR.value))
    session.add(UserRole(user_id=users["reviewer"].id, role_id=CanonicalRole.CLINICAL_REVIEWER.value))
    session.add(UserRole(user_id=users["approver"].id, role_id=CanonicalRole.APPROVER.value))
    session.add(UserRole(user_id=users["auditor"].id, role_id=CanonicalRole.AUDITOR.value))
    session.add(UserRole(user_id=users["integration"].id, role_id=CanonicalRole.INTEGRATION_SERVICE.value))

    # 4. Seed default Mode 0 triage policy
    seed_default_triage_policy(session)
    session.commit()

    # 5. Configure services
    blob_dir = tmp_path / "blobs"
    blob_store = EncryptedFileBlobStore(root_dir=blob_dir, encryption_key=b"k" * 32)
    audit_service = AuditService()
    job_queue = JobQueue()
    claim_review_service = ClaimReviewService(audit_service=audit_service)
    approval_engine = DocumentApprovalEngine(audit_service=audit_service)

    set_global_session_factory(lambda: SessionLocal())
    set_global_blob_store(blob_store)
    set_global_audit_service(audit_service)
    set_global_job_queue(job_queue)
    set_global_claim_review_service(claim_review_service)
    set_global_approval_engine(approval_engine)
    set_global_triage_engine(None)

    # 6. Configure Token Validator
    config = OIDCConfig(
        issuer="https://idp.clindoc.test/auth",
        audience="clindoc-api",
        public_key_pem=rsa_keypair["public_pem"],
        jwks_dict=rsa_keypair["jwks"],
        algorithms=["RS256"],
    )
    validator = OIDCTokenValidator(config)
    set_global_token_validator(validator)

    # 7. Instantiate App & Client
    app = create_app()
    client = TestClient(app)

    yield {
        "client": client,
        "session": session,
        "SessionLocal": SessionLocal,
        "users": users,
        "audit_service": audit_service,
        "rsa_keypair": rsa_keypair,
    }

    session.close()


def auth_header(keypair: dict[str, Any], sub: str) -> dict[str, str]:
    """Helper returning Authorization bearer header for a sub."""
    token = make_token(keypair, sub=sub)
    return {"Authorization": f"Bearer {token}"}


def create_sample_document(
    session: Session,
    uploaded_by: uuid.UUID,
    *,
    status: DocumentStatus = DocumentStatus.PENDING_REVIEW,
    patient_context_id: str = "MRN-1001",
    identity_status: IdentityVerificationStatus = IdentityVerificationStatus.UNVERIFIED,
) -> Document:
    """Helper to create a test Document with an associated Evidence and Claim."""
    doc = Document(
        id=uuid.uuid4(),
        sha256="a" * 64,
        storage_ref="enc://blobs/doc-1.enc",
        original_filename="discharge_summary.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=2,
        uploaded_by=uploaded_by,
        status=status,
        patient_context_id=patient_context_id,
        identity_verification_status=identity_status,
    )
    session.add(doc)
    session.flush()
    return doc


def create_sample_claim(
    session: Session,
    doc: Document,
    *,
    field_type: str = "med.name",
    value: str = "Metformin 500mg",
    normalized_code: str = "6809",
    code_system: str = "RxNorm",
    review_state: ClaimReviewState = ClaimReviewState.PENDING,
    version: int = 1,
) -> tuple[Claim, Evidence]:
    """Helper to create a grounded Claim and Evidence."""
    ev = Evidence(
        id=uuid.uuid4(),
        document_id=doc.id,
        page_number=1,
        char_start=0,
        char_end=len(value),
        bounding_box={"x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.3},
        ocr_confidence=0.95,
        evidence_text=value,
    )
    session.add(ev)
    session.flush()

    c_stable_id = uuid.uuid4()
    claim = Claim(
        id=uuid.uuid4(),
        claim_id=c_stable_id,
        document_id=doc.id,
        group_id="med-group-1",
        field_type=field_type,
        value=value,
        normalized_code=normalized_code,
        code_system=code_system,
        version=version,
        origin=ClaimOrigin.PIPELINE,
        review_state=review_state,
        evidence_id=ev.id,
        payload={
            "field_type": field_type,
            "value": value,
            "normalized_code": normalized_code,
            "code_system": code_system,
        },
    )
    session.add(claim)

    vr = VerificationResult(
        id=uuid.uuid4(),
        claim_id=claim.id,
        verification_stage=VerificationStage.STAGE_A,
        label=VerificationLabel.SUPPORTED,
        calibrated_confidence=0.92,
        provider_kind="mock_dev",
        contradiction_type=None,
        payload={},
    )
    session.add(vr)
    session.commit()
    return claim, ev


# ─── 1 & 2. REVIEW QUEUE RETRIEVAL ───────────────────────────────────────────

def test_01_reviewer_can_retrieve_review_queue(review_env):
    """1. Reviewer can retrieve review queue with sorted claims and summary metrics."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, _ = create_sample_claim(session, doc)

    res = client.get(
        "/api/v1/review/queue",
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert data["total"] >= 1
    item = next(i for i in data["items"] if i["document_id"] == str(doc.id))
    assert item["total_claims"] == 1
    assert item["status"] == DocumentStatus.PENDING_REVIEW.value
    assert len(item["claims"]) == 1
    assert item["claims"][0]["claim_id"] == str(claim.id)
    assert item["claims"][0]["priority_rank"] >= 1


def test_02_approver_can_retrieve_review_queue(review_env):
    """2. Approver can retrieve review queue."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    res = client.get(
        "/api/v1/review/queue",
        headers=auth_header(kp, users["approver"].idp_subject),
    )
    assert res.status_code == 200
    data = res.json()
    assert "items" in data


def test_03_unauthorized_roles_denied_queue_access(review_env):
    """3. Unauthorized roles (document_operator, auditor, system_admin, integration_service) receive 403."""
    client = review_env["client"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    for role_key in ["operator", "auditor", "admin", "integration"]:
        res = client.get(
            "/api/v1/review/queue",
            headers=auth_header(kp, users[role_key].idp_subject),
        )
        assert res.status_code == 403, f"Role {role_key} should have been forbidden (403)"


# ─── 4 & 5. CLAIM RETRIEVAL & OBJECT AUTHORIZATION ───────────────────────────

def test_04_authorized_reviewer_can_retrieve_claims(review_env):
    """4. Authorized reviewer can retrieve claims preserving lineage and grounding."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, ev = create_sample_claim(session, doc)

    res = client.get(
        f"/api/v1/documents/{doc.id}/claims",
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["document_id"] == str(doc.id)
    assert data["total"] == 1
    c_data = data["claims"][0]
    assert c_data["id"] == str(claim.id)
    assert c_data["value"] == "Metformin 500mg"
    assert c_data["version"] == 1
    assert c_data["grounding"]["page_number"] == 1
    assert c_data["grounding"]["text_snippet"] == "Metformin 500mg"
    assert c_data["verification"]["label"] == "SUPPORTED"


def test_05_unauthorized_user_cannot_retrieve_protected_document_claims(review_env):
    """5. Unauthorized roles cannot retrieve document claims."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    create_sample_claim(session, doc)

    # Document operator attempting to access claims -> 403
    res_op = client.get(
        f"/api/v1/documents/{doc.id}/claims",
        headers=auth_header(kp, users["operator"].idp_subject),
    )
    assert res_op.status_code == 403

    # Auditor attempting to access claims -> 403
    res_aud = client.get(
        f"/api/v1/documents/{doc.id}/claims",
        headers=auth_header(kp, users["auditor"].idp_subject),
    )
    assert res_aud.status_code == 403


# ─── 6, 7, 8. CLAIM ACTIONS: ACCEPT, EDIT, REJECT, ESCALATE, REOPEN ─────────

def test_06_reviewer_can_accept_claim(review_env):
    """6. Reviewer can ACCEPT a pending claim."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, _ = create_sample_claim(session, doc)

    payload = {
        "action": "ACCEPT",
        "expected_version": 1,
        "notes": "Verified against lab panel.",
    }
    res = client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json=payload,
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["action"] == "ACCEPT"
    assert data["review_state"] == "ACCEPTED"
    assert data["version"] == 1

    # Verify database state
    c_db = session.get(Claim, claim.id)
    session.refresh(c_db)
    assert c_db.review_state == ClaimReviewState.ACCEPTED


def test_07_reviewer_can_edit_claim(review_env):
    """7. Reviewer can EDIT a claim creating new version with immediate re-verification."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, _ = create_sample_claim(session, doc, value="Lisinopril 10mg")

    payload = {
        "action": "EDIT",
        "expected_version": 1,
        "reason_code": "CLINICAL_CORRECTION",
        "new_value": "Lisinopril 20mg",
        "new_normalized_code": "206805",
        "new_code_system": "RxNorm",
        "notes": "Corrected dose to 20mg per chart review.",
    }
    res = client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json=payload,
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["action"] == "EDIT"
    assert data["version"] == 2
    assert data["previous_version"] == 1
    assert data["supersedes_claim_id"] == str(claim.id)
    assert data["origin"] == "reviewer"
    assert data["review_state"] == "PENDING"
    assert "verification_label" in data
    assert "triage_category" in data

    # Verify old claim is in EDITED state and new claim exists in DB
    session.expire_all()
    old_c = session.get(Claim, claim.id)
    assert old_c.review_state == ClaimReviewState.EDITED

    new_c = session.get(Claim, uuid.UUID(data["row_id"]))
    assert new_c is not None
    assert new_c.value == "Lisinopril 20mg"
    assert new_c.version == 2
    assert new_c.supersedes_claim_id == claim.id


def test_08_reviewer_can_reject_escalate_reopen(review_env):
    """8. Reviewer can REJECT, ESCALATE, and REOPEN claims."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    # Test REJECT
    doc1 = create_sample_document(session, users["operator"].id)
    claim1, _ = create_sample_claim(session, doc1)
    res_rej = client.post(
        f"/api/v1/claims/{claim1.id}/actions",
        json={
            "action": "REJECT",
            "expected_version": 1,
            "reason_code": "ERRONEOUS_EXTRACTION",
            "notes": "Hallucinated concept.",
        },
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res_rej.status_code == 200
    assert res_rej.json()["review_state"] == "REJECTED"

    # Test ESCALATE
    doc2 = create_sample_document(session, users["operator"].id)
    claim2, _ = create_sample_claim(session, doc2)
    res_esc = client.post(
        f"/api/v1/claims/{claim2.id}/actions",
        json={
            "action": "ESCALATE",
            "expected_version": 1,
            "reason_code": "PHYSICIAN_CONSULT_NEEDED",
            "notes": "Complex ambiguous wording.",
        },
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res_esc.status_code == 200
    assert res_esc.json()["review_state"] == "ESCALATED"

    # Test REOPEN from REJECTED
    res_reopen = client.post(
        f"/api/v1/claims/{claim1.id}/actions",
        json={
            "action": "REOPEN",
            "expected_version": 1,
            "notes": "Reopening for secondary verification.",
        },
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res_reopen.status_code == 200
    assert res_reopen.json()["review_state"] == "PENDING"


# ─── 9, 10, 11. CLAIM ACTION ERROR HANDLING ───────────────────────────────────

def test_09_invalid_claim_action_returns_http_error(review_env):
    """9. Invalid claim action or illegal state transition returns HTTP 409 or 422."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, _ = create_sample_claim(session, doc)

    # First accept claim
    client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json={"action": "ACCEPT", "expected_version": 1},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )

    # Attempting to ACCEPT an already ACCEPTED claim -> 409 Conflict
    res_invalid_trans = client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json={"action": "ACCEPT", "expected_version": 1},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res_invalid_trans.status_code == 409

    # Malformed action string -> 422
    res_bad_action = client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json={"action": "INVALID_ACTION", "expected_version": 1},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res_bad_action.status_code == 422


def test_10_missing_reason_code_returns_validation_error(review_env):
    """10. Missing reason code for REJECT or EDIT returns HTTP 422."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, _ = create_sample_claim(session, doc)

    # REJECT without reason_code
    res_rej = client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json={"action": "REJECT", "expected_version": 1},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res_rej.status_code == 422

    # EDIT without reason_code
    res_edit = client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json={"action": "EDIT", "expected_version": 1, "new_value": "New Val"},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res_edit.status_code == 422


def test_11_optimistic_locking_conflict_returns_conflict_response(review_env):
    """11. Optimistic locking conflict (wrong expected_version) returns HTTP 409."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, _ = create_sample_claim(session, doc)

    # Expected version 999 when current version is 1
    res = client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json={"action": "ACCEPT", "expected_version": 999},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res.status_code == 409
    assert "version" in res.json()["detail"].lower()


# ─── 12, 13, 14. PATIENT IDENTITY CONFIRMATION ────────────────────────────────

def test_12_reviewer_can_confirm_identity(review_env):
    """12. Reviewer can explicitly confirm patient identity."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)

    payload = {
        "action": "CONFIRMED",
        "notes": "Verified MRN matches chart.",
        "context_demographics": {"name": "Jane Doe", "mrn": "MRN-1001", "dob": "1980-01-01"},
        "extracted_demographics": {"name": "Jane Doe", "mrn": "MRN-1001", "dob": "1980-01-01"},
    }
    res = client.post(
        f"/api/v1/documents/{doc.id}/identity-confirmation",
        json=payload,
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["identity_verification_status"] == "CONFIRMED"
    assert data["identity_confirmed_by"] == str(users["reviewer"].id)
    assert data["identity_confirmed_at"] is not None


def test_13_approver_can_confirm_identity_and_accept_mismatch(review_env):
    """13. Approver can confirm identity and accept identity mismatch with reason code."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)

    # Demographics mismatch without acceptance -> 409
    mismatch_payload = {
        "action": "CONFIRMED",
        "context_demographics": {"name": "John Doe", "mrn": "MRN-1001"},
        "extracted_demographics": {"name": "Jane Smith", "mrn": "MRN-9999"},
    }
    res_mismatch = client.post(
        f"/api/v1/documents/{doc.id}/identity-confirmation",
        json=mismatch_payload,
        headers=auth_header(kp, users["approver"].idp_subject),
    )
    assert res_mismatch.status_code == 409

    # Explicit MISMATCH_ACCEPTED with reason_code -> 200
    accept_payload = {
        "action": "MISMATCH_ACCEPTED",
        "reason_code": "MAIDEN_NAME_AND_OLD_MRN_VERIFIED",
        "notes": "Cross-referenced alias in MPI record.",
    }
    res_accept = client.post(
        f"/api/v1/documents/{doc.id}/identity-confirmation",
        json=accept_payload,
        headers=auth_header(kp, users["approver"].idp_subject),
    )
    assert res_accept.status_code == 200
    assert res_accept.json()["identity_verification_status"] == "MISMATCH_ACCEPTED"


def test_14_unauthorized_identity_confirmation_is_denied(review_env):
    """14. Unauthorized roles (document_operator, system_admin) receive 403 for identity confirmation."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)

    # Operator cannot confirm identity -> 403
    res_op = client.post(
        f"/api/v1/documents/{doc.id}/identity-confirmation",
        json={"action": "CONFIRMED"},
        headers=auth_header(kp, users["operator"].idp_subject),
    )
    assert res_op.status_code == 403

    # Admin cannot confirm identity -> 403
    res_admin = client.post(
        f"/api/v1/documents/{doc.id}/identity-confirmation",
        json={"action": "CONFIRMED"},
        headers=auth_header(kp, users["admin"].idp_subject),
    )
    assert res_admin.status_code == 403


# ─── 15, 16, 17, 18. DOCUMENT APPROVAL & FOUR-EYES ────────────────────────────

def test_15_approver_can_approve_eligible_document(review_env):
    """15. Approver can approve an eligible document when all claims and identity are satisfied."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, _ = create_sample_claim(session, doc)

    # Step 1: Accept claim
    client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json={"action": "ACCEPT", "expected_version": 1},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )

    # Step 2: Confirm identity
    client.post(
        f"/api/v1/documents/{doc.id}/identity-confirmation",
        json={"action": "CONFIRMED"},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )

    # Step 3: Formal approval by distinct approver
    res_app = client.post(
        f"/api/v1/documents/{doc.id}/approval",
        json={"identity_notes": "Identity double-checked and verified."},
        headers=auth_header(kp, users["approver"].idp_subject),
    )
    assert res_app.status_code == 200
    data = res_app.json()
    assert data["status"] == "APPROVED"
    assert data["is_export_eligible"] is True
    assert data["approved_by"] == str(users["approver"].id)

    # Verify document status updated to APPROVED
    session.expire_all()
    doc_db = session.get(Document, doc.id)
    assert doc_db.status == DocumentStatus.APPROVED


def test_16_reviewer_cannot_approve_document(review_env):
    """16. Reviewer is denied document approval (HTTP 403)."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)

    res = client.post(
        f"/api/v1/documents/{doc.id}/approval",
        json={},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    assert res.status_code == 403


def test_17_uploader_cannot_approve_their_own_document(review_env):
    """17. Four-Eyes violation: uploader cannot approve their own document (HTTP 409)."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    # Approver uploads a document
    doc = create_sample_document(session, users["approver"].id)
    claim, _ = create_sample_claim(session, doc)

    # Accept claim
    client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json={"action": "ACCEPT", "expected_version": 1},
        headers=auth_header(kp, users["approver"].idp_subject),
    )
    # Confirm identity
    client.post(
        f"/api/v1/documents/{doc.id}/identity-confirmation",
        json={"action": "CONFIRMED"},
        headers=auth_header(kp, users["approver"].idp_subject),
    )

    # Approver tries to approve own document -> 409 Four-Eyes violation
    res = client.post(
        f"/api/v1/documents/{doc.id}/approval",
        json={},
        headers=auth_header(kp, users["approver"].idp_subject),
    )
    assert res.status_code == 409
    assert "four-eyes" in res.json()["detail"].lower()


def test_18_approval_prerequisites_enforced(review_env):
    """18. Approval prerequisites enforced: unreviewed claims or unconfirmed identity return 409."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    # Document with unconfirmed identity and unreviewed claims
    doc = create_sample_document(session, users["operator"].id)
    create_sample_claim(session, doc)

    res_fail = client.post(
        f"/api/v1/documents/{doc.id}/approval",
        json={},
        headers=auth_header(kp, users["approver"].idp_subject),
    )
    assert res_fail.status_code == 409


# ─── 19. SYSTEM ADMIN PHI DENIAL ─────────────────────────────────────────────

def test_19_system_admin_cannot_access_phi_review_endpoints(review_env):
    """19. System admin is strictly denied access across all PHI review endpoints (HTTP 403)."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, _ = create_sample_claim(session, doc)
    admin_hdr = auth_header(kp, users["admin"].idp_subject)

    # 1. Review queue
    res_q = client.get("/api/v1/review/queue", headers=admin_hdr)
    assert res_q.status_code == 403

    # 2. Document claims
    res_c = client.get(f"/api/v1/documents/{doc.id}/claims", headers=admin_hdr)
    assert res_c.status_code == 403

    # 3. Claim action
    res_act = client.post(f"/api/v1/claims/{claim.id}/actions", json={"action": "ACCEPT", "expected_version": 1}, headers=admin_hdr)
    assert res_act.status_code == 403

    # 4. Identity confirmation
    res_id = client.post(f"/api/v1/documents/{doc.id}/identity-confirmation", json={"action": "CONFIRMED"}, headers=admin_hdr)
    assert res_id.status_code == 403

    # 5. Document approval
    res_app = client.post(f"/api/v1/documents/{doc.id}/approval", json={}, headers=admin_hdr)
    assert res_app.status_code == 403

    # 6. Supporting review summary
    res_sum = client.get(f"/api/v1/documents/{doc.id}/review-summary", headers=admin_hdr)
    assert res_sum.status_code == 403


# ─── 20. SECURITY: NO CIPHERTEXT OR SECRETS EXPOSED ──────────────────────────

def test_20_api_responses_do_not_expose_ciphertext_or_secrets(review_env):
    """20. API responses do not expose ciphertext, storage URIs, or internal secrets."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, _ = create_sample_claim(session, doc)

    # Check queue response
    res_q = client.get("/api/v1/review/queue", headers=auth_header(kp, users["reviewer"].idp_subject))
    raw_q_text = res_q.text
    assert "storage_ref" not in raw_q_text
    assert "enc://" not in raw_q_text
    assert "encryption_key" not in raw_q_text

    # Check claims response
    res_c = client.get(f"/api/v1/documents/{doc.id}/claims", headers=auth_header(kp, users["reviewer"].idp_subject))
    raw_c_text = res_c.text
    assert "storage_ref" not in raw_c_text
    assert "enc://" not in raw_c_text
    assert "encryption_key" not in raw_c_text


# ─── 21. AUDIT BEHAVIOR REMAINS INTACT ───────────────────────────────────────

def test_21_audit_behavior_remains_intact_through_api(review_env):
    """21. Authoritative AuditEvents are recorded through API review and approval operations."""
    client = review_env["client"]
    session = review_env["session"]
    users = review_env["users"]
    kp = review_env["rsa_keypair"]

    doc = create_sample_document(session, users["operator"].id)
    claim, _ = create_sample_claim(session, doc)

    # 1. Claim Action Audit
    client.post(
        f"/api/v1/claims/{claim.id}/actions",
        json={"action": "ACCEPT", "expected_version": 1, "notes": "Approved in API"},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    claim_audit = (
        session.query(AuditEvent)
        .filter(AuditEvent.entity_id == str(claim.id), AuditEvent.action == "ACCEPT")
        .first()
    )
    assert claim_audit is not None
    assert claim_audit.actor_id == str(users["reviewer"].id)
    assert claim_audit.event_type == "CLAIM_REVIEW_ACTION"

    # 2. Identity Confirmation Audit
    client.post(
        f"/api/v1/documents/{doc.id}/identity-confirmation",
        json={"action": "CONFIRMED"},
        headers=auth_header(kp, users["reviewer"].idp_subject),
    )
    id_audit = (
        session.query(AuditEvent)
        .filter(AuditEvent.entity_id == str(doc.id), AuditEvent.action == "CONFIRM_IDENTITY")
        .first()
    )
    assert id_audit is not None
    assert id_audit.actor_id == str(users["reviewer"].id)
    assert id_audit.event_type == "PATIENT_IDENTITY_CONFIRMED"

    # 3. Document Approval Audit
    client.post(
        f"/api/v1/documents/{doc.id}/approval",
        json={},
        headers=auth_header(kp, users["approver"].idp_subject),
    )
    approval_audit = (
        session.query(AuditEvent)
        .filter(AuditEvent.entity_id == str(doc.id), AuditEvent.action == "APPROVE")
        .first()
    )
    assert approval_audit is not None
    assert approval_audit.actor_id == str(users["approver"].id)
    assert approval_audit.event_type == "DOCUMENT_APPROVED"
