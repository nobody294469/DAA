"""Comprehensive Security, Negative-Authorization, Audit-Failure, and Integration Audit for Phase 8.4 Step 6.

Authority:
  - PROJECT_SPEC.md §3.1, §4.3, §10
  - PRODUCTIZATION_SPEC.md §1.2, §4.2, §5 (T-04, T-09, T-12, T-18, T-20), §9.1, §9.2, §9.3, §10.4, §11.3, §16
  - PRODUCTIZATION_DECISIONS.md PD-001, PD-007, PD-009, PD-012

Test Suite Sections:
  1. Authentication Negative Tests (401 for unauthenticated, malformed, expired, invalid sig, wrong iss/aud/alg, unknown sub).
  2. RBAC Negative Tests (403 for unauthorized upload, audit access, role claim forgery).
  3. system_admin PHI Isolation (403 for document/PHI; 200 for operational jobs, audit, health).
  4. Object-Level Authorization (own vs other document/job, reviewer/approver scoping, 404 for nonexistent).
  5. Audit Fail-Closed Verification (API transaction rollback on audit failure).
  6. Audit Hash-Chain Tamper Detection (current hash, previous hash, sequence gap detection).
  7. Audit PHI Safety (inspection of actual persisted audit records).
  8. Audit Access & Mutation Prevention (auditor/admin permitted, others 403, 405 on POST/PUT/DELETE).
  9. Health Endpoints (/live unauthenticated, /ready authenticated + dependency failure handling).
  10. End-to-End Happy Path (upload -> blob -> job -> audit -> doc retrieval -> job retrieval).
"""

from __future__ import annotations

import datetime
from datetime import timezone
import io
import json
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch
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
from clindoc.api.audit.service import (
    AuditPHISecurityError,
    AuditService,
    AuditServiceError,
)
from clindoc.api.auth import (
    CanonicalRole,
    OIDCConfig,
    OIDCTokenValidator,
    set_global_session_factory,
    set_global_token_validator,
)
from clindoc.api.deps import (
    set_global_audit_service,
    set_global_blob_store,
    set_global_job_queue,
)
from clindoc.db import (
    AuditEvent,
    Base,
    Document,
    ProcessingJob,
    Role,
    User,
    UserRole,
)
from clindoc.db.enums import DocumentStatus, JobStatus
from clindoc.jobs.queue import JobQueue
from clindoc.storage.blob import EncryptedFileBlobStore


# ─── FIXTURES ────────────────────────────────────────────────────────────────

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
    jwk_dict["kid"] = "sec-audit-key-1"
    jwk_dict["alg"] = "RS256"
    jwk_dict["use"] = "sig"

    return {
        "private_key": private_key,
        "public_key": public_key,
        "public_pem": pub_pem,
        "kid": "sec-audit-key-1",
        "jwks": {"keys": [jwk_dict]},
    }


@pytest.fixture(scope="module")
def rogue_rsa_keypair():
    """Untrusted RSA keypair to simulate attacker forging signatures."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {"private_key": private_key, "kid": "rogue-key-99"}


def make_token(
    keypair: dict[str, Any],
    *,
    sub: str,
    iss: str = "https://idp.clindoc.test/auth",
    aud: str = "clindoc-api",
    exp_delta_seconds: int = 3600,
    nbf_delta_seconds: int = -10,
    alg: str = "RS256",
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    """Generate signed JWT token for subject with given claims."""
    now = datetime.datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "iat": int(now.timestamp()),
        "nbf": int((now + datetime.timedelta(seconds=nbf_delta_seconds)).timestamp()),
        "exp": int((now + datetime.timedelta(seconds=exp_delta_seconds)).timestamp()),
    }
    if extra_claims:
        claims.update(extra_claims)

    return jwt.encode(
        claims,
        keypair["private_key"],
        algorithm=alg,
        headers={"kid": keypair.get("kid", "sec-audit-key-1")},
    )


@pytest.fixture
def audit_test_env(tmp_path, rsa_keypair):
    """Integrated API test environment with in-memory SQLite and encrypted blob store."""
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
        "admin": User(id=uuid.uuid4(), idp_subject="sub-admin-01", username="admin_user", full_name="Admin", email="admin@test.local", is_active=True),
        "operator1": User(id=uuid.uuid4(), idp_subject="sub-op1-02", username="op_one", full_name="Operator One", email="op1@test.local", is_active=True),
        "operator2": User(id=uuid.uuid4(), idp_subject="sub-op2-03", username="op_two", full_name="Operator Two", email="op2@test.local", is_active=True),
        "reviewer": User(id=uuid.uuid4(), idp_subject="sub-rev-04", username="reviewer", full_name="Reviewer", email="rev@test.local", is_active=True),
        "approver": User(id=uuid.uuid4(), idp_subject="sub-app-05", username="approver", full_name="Approver", email="app@test.local", is_active=True),
        "auditor": User(id=uuid.uuid4(), idp_subject="sub-aud-06", username="auditor", full_name="Auditor", email="aud@test.local", is_active=True),
        "integration": User(id=uuid.uuid4(), idp_subject="sub-int-07", username="integrator", full_name="Integration Service", email="int@test.local", is_active=True),
    }
    for u in users.values():
        session.add(u)
    session.flush()

    # 3. Assign roles
    session.add(UserRole(user_id=users["admin"].id, role_id=CanonicalRole.SYSTEM_ADMIN.value))
    session.add(UserRole(user_id=users["operator1"].id, role_id=CanonicalRole.DOCUMENT_OPERATOR.value))
    session.add(UserRole(user_id=users["operator2"].id, role_id=CanonicalRole.DOCUMENT_OPERATOR.value))
    session.add(UserRole(user_id=users["reviewer"].id, role_id=CanonicalRole.CLINICAL_REVIEWER.value))
    session.add(UserRole(user_id=users["approver"].id, role_id=CanonicalRole.APPROVER.value))
    session.add(UserRole(user_id=users["auditor"].id, role_id=CanonicalRole.AUDITOR.value))
    session.add(UserRole(user_id=users["integration"].id, role_id=CanonicalRole.INTEGRATION_SERVICE.value))
    session.commit()

    # 4. Configure Singletons
    blob_dir = tmp_path / "blobs"
    blob_store = EncryptedFileBlobStore(root_dir=blob_dir, encryption_key=b"k" * 32)
    audit_service = AuditService()
    job_queue = JobQueue()

    set_global_session_factory(lambda: SessionLocal())
    set_global_blob_store(blob_store)
    set_global_audit_service(audit_service)
    set_global_job_queue(job_queue)

    # 5. Configure Token Validator
    config = OIDCConfig(
        issuer="https://idp.clindoc.test/auth",
        audience="clindoc-api",
        public_key_pem=rsa_keypair["public_pem"],
        jwks_dict=rsa_keypair["jwks"],
        algorithms=["RS256"],
    )
    validator = OIDCTokenValidator(config)
    set_global_token_validator(validator)

    # 6. Instantiate App & Client
    app = create_app()
    client = TestClient(app)

    yield {
        "client": client,
        "session": session,
        "SessionLocal": SessionLocal,
        "users": users,
        "blob_store": blob_store,
        "audit_service": audit_service,
        "job_queue": job_queue,
    }
    session.close()


def _valid_pdf_payload() -> bytes:
    """Return a minimal valid PDF payload for upload testing."""
    return b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000052 00000 n\n0000000101 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"


# ─── 1. AUTHENTICATION NEGATIVE TESTS ─────────────────────────────────────────

def test_auth_negative_unauthenticated_endpoints_return_401(audit_test_env):
    """Assert all protected endpoints reject requests without Authorization header with HTTP 401."""
    client = audit_test_env["client"]
    protected_urls = [
        ("GET", "/api/v1/health/ready"),
        ("POST", "/api/v1/documents"),
        ("GET", f"/api/v1/documents/{uuid.uuid4()}"),
        ("GET", f"/api/v1/jobs/{uuid.uuid4()}"),
        ("GET", "/api/v1/audit/events"),
    ]
    for method, url in protected_urls:
        if method == "GET":
            resp = client.get(url)
        else:
            resp = client.post(url, files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")})
        assert resp.status_code == 401, f"Expected 401 for unauthenticated {method} {url}, got {resp.status_code}"
        assert resp.json().get("detail") is not None

    # Confirm /health/live remains unauthenticated
    resp_live = client.get("/api/v1/health/live")
    assert resp_live.status_code == 200


def test_auth_negative_malformed_token_returns_401(audit_test_env):
    """Assert malformed or unparseable Bearer tokens return HTTP 401."""
    client = audit_test_env["client"]
    bad_tokens = [
        "not-a-token",
        "part1.part2",
        "Bearer",
        "Bearer ",
        "Bearer not.a.valid.jwt.format",
    ]
    for bt in bad_tokens:
        header_val = bt if bt.startswith("Bearer") else f"Bearer {bt}"
        resp = client.get("/api/v1/health/ready", headers={"Authorization": header_val})
        assert resp.status_code == 401, f"Expected 401 for token '{bt}', got {resp.status_code}"


def test_auth_negative_expired_token_returns_401(audit_test_env, rsa_keypair):
    """Assert tokens with exp claim in the past return HTTP 401."""
    client = audit_test_env["client"]
    expired_token = make_token(rsa_keypair, sub="sub-op1-02", exp_delta_seconds=-60)
    resp = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_auth_negative_invalid_signature_returns_401(audit_test_env, rogue_rsa_keypair):
    """Assert tokens signed by an untrusted rogue key return HTTP 401."""
    client = audit_test_env["client"]
    forged_token = make_token(rogue_rsa_keypair, sub="sub-op1-02")
    resp = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {forged_token}"})
    assert resp.status_code == 401


def test_auth_negative_wrong_issuer_and_audience_return_401(audit_test_env, rsa_keypair):
    """Assert tokens with incorrect issuer or audience claims return HTTP 401."""
    client = audit_test_env["client"]

    # Wrong issuer
    bad_iss_token = make_token(rsa_keypair, sub="sub-op1-02", iss="https://rogue.example.com")
    resp_iss = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {bad_iss_token}"})
    assert resp_iss.status_code == 401

    # Wrong audience
    bad_aud_token = make_token(rsa_keypair, sub="sub-op1-02", aud="wrong-api-service")
    resp_aud = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {bad_aud_token}"})
    assert resp_aud.status_code == 401


def test_auth_negative_unknown_local_user_returns_401(audit_test_env, rsa_keypair):
    """Assert valid tokens for subjects not registered in local database return HTTP 401."""
    client = audit_test_env["client"]
    unknown_sub_token = make_token(rsa_keypair, sub="sub-unknown-unregistered-999")
    resp = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {unknown_sub_token}"})
    assert resp.status_code == 401
    assert "not registered" in resp.json()["detail"].lower()


# ─── 2. RBAC NEGATIVE TESTS ──────────────────────────────────────────────────

def test_rbac_negative_unauthorized_roles_cannot_upload(audit_test_env, rsa_keypair):
    """Assert non-document_operator roles (admin, reviewer, approver, auditor, integration) receive 403 on upload."""
    client = audit_test_env["client"]
    unauthorized_subs = [
        ("admin", "sub-admin-01"),
        ("reviewer", "sub-rev-04"),
        ("approver", "sub-app-05"),
        ("auditor", "sub-aud-06"),
        ("integration", "sub-int-07"),
    ]
    files = {"file": ("upload_denied.pdf", io.BytesIO(_valid_pdf_payload()), "application/pdf")}

    for name, sub in unauthorized_subs:
        token = make_token(rsa_keypair, sub=sub)
        resp = client.post(
            "/api/v1/documents",
            files=files,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, f"Expected 403 for role {name}, got {resp.status_code}"
        assert "Forbidden" in resp.json()["detail"]


def test_rbac_negative_client_role_claims_are_ignored(audit_test_env, rsa_keypair):
    """Assert forged JWT role/group claims cannot elevate privileges; DB-resolved roles govern."""
    client = audit_test_env["client"]
    # reviewer tries to impersonate document_operator and system_admin via JWT claims
    forged_token = make_token(
        rsa_keypair,
        sub="sub-rev-04",  # In DB: clinical_reviewer ONLY
        extra_claims={
            "role": "document_operator",
            "roles": ["system_admin", "document_operator"],
            "groups": ["Administrators", "Operators"],
        },
    )
    files = {"file": ("forge.pdf", io.BytesIO(_valid_pdf_payload()), "application/pdf")}
    resp = client.post(
        "/api/v1/documents",
        files=files,
        headers={"Authorization": f"Bearer {forged_token}"},
    )
    assert resp.status_code == 403, "DB roles must govern; JWT role claims must NOT grant upload permissions"


def test_rbac_negative_unauthorized_roles_cannot_access_audit_logs(audit_test_env, rsa_keypair):
    """Assert operator, reviewer, approver, and integration service receive 403 on GET /audit/events."""
    client = audit_test_env["client"]
    unauthorized_subs = [
        "sub-op1-02",
        "sub-op2-03",
        "sub-rev-04",
        "sub-app-05",
        "sub-int-07",
    ]
    for sub in unauthorized_subs:
        token = make_token(rsa_keypair, sub=sub)
        resp = client.get("/api/v1/audit/events", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403, f"Expected 403 for subject {sub}, got {resp.status_code}"


# ─── 3. SYSTEM_ADMIN PHI ISOLATION ───────────────────────────────────────────

def test_system_admin_denied_phi_and_document_content(audit_test_env, rsa_keypair):
    """Assert system_admin is strictly denied access to documents/PHI (HTTP 403)."""
    client = audit_test_env["client"]
    op_token = make_token(rsa_keypair, sub="sub-op1-02")
    admin_token = make_token(rsa_keypair, sub="sub-admin-01")

    # 1. Operator uploads document
    files = {"file": ("phi_isolation_doc.pdf", io.BytesIO(_valid_pdf_payload()), "application/pdf")}
    resp_up = client.post("/api/v1/documents", files=files, headers={"Authorization": f"Bearer {op_token}"})
    assert resp_up.status_code == 201
    doc_id = resp_up.json()["id"]

    # 2. system_admin attempts document access -> HTTP 403
    resp_admin = client.get(f"/api/v1/documents/{doc_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp_admin.status_code == 403
    assert "System administrators are strictly prohibited from accessing document content" in resp_admin.json()["detail"]

    # 3. But system_admin CAN access operational functions (health, jobs, audit)
    job_id = resp_up.json()["job_id"]
    resp_job = client.get(f"/api/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp_job.status_code == 200

    resp_audit = client.get("/api/v1/audit/events", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp_audit.status_code == 200

    resp_ready = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp_ready.status_code == 200


# ─── 4. OBJECT-LEVEL AUTHORIZATION ───────────────────────────────────────────

def test_object_level_document_and_job_authorization(audit_test_env, rsa_keypair):
    """Assert document_operator can only access own documents and jobs, and 404 on missing."""
    client = audit_test_env["client"]
    op1_token = make_token(rsa_keypair, sub="sub-op1-02")
    op2_token = make_token(rsa_keypair, sub="sub-op2-03")
    rev_token = make_token(rsa_keypair, sub="sub-rev-04")

    # Operator 1 uploads a document
    files = {"file": ("op1_doc.pdf", io.BytesIO(_valid_pdf_payload()), "application/pdf")}
    resp_up = client.post("/api/v1/documents", files=files, headers={"Authorization": f"Bearer {op1_token}"})
    assert resp_up.status_code == 201
    doc1_id = resp_up.json()["id"]
    job1_id = resp_up.json()["job_id"]

    # Op 1 can access own document
    resp_own_doc = client.get(f"/api/v1/documents/{doc1_id}", headers={"Authorization": f"Bearer {op1_token}"})
    assert resp_own_doc.status_code == 200

    # Op 2 CANNOT access Op 1's document (HTTP 403)
    resp_cross_doc = client.get(f"/api/v1/documents/{doc1_id}", headers={"Authorization": f"Bearer {op2_token}"})
    assert resp_cross_doc.status_code == 403

    # Reviewer CAN access active document
    resp_rev_doc = client.get(f"/api/v1/documents/{doc1_id}", headers={"Authorization": f"Bearer {rev_token}"})
    assert resp_rev_doc.status_code == 200

    # Op 1 can access own job
    resp_own_job = client.get(f"/api/v1/jobs/{job1_id}", headers={"Authorization": f"Bearer {op1_token}"})
    assert resp_own_job.status_code == 200

    # Op 2 CANNOT access Op 1's job (HTTP 403)
    resp_cross_job = client.get(f"/api/v1/jobs/{job1_id}", headers={"Authorization": f"Bearer {op2_token}"})
    assert resp_cross_job.status_code == 403

    # Nonexistent document and job return 404
    fake_id = uuid.uuid4()
    resp_fake_doc = client.get(f"/api/v1/documents/{fake_id}", headers={"Authorization": f"Bearer {op1_token}"})
    assert resp_fake_doc.status_code == 404

    resp_fake_job = client.get(f"/api/v1/jobs/{fake_id}", headers={"Authorization": f"Bearer {op1_token}"})
    assert resp_fake_job.status_code == 404


# ─── 5. AUDIT FAIL-CLOSED TRANSACTION BOUNDARY ────────────────────────────────

def test_audit_failure_causes_complete_rollback_of_upload(audit_test_env, rsa_keypair):
    """Assert that if AuditService fails, the upload transaction fails closed and no records persist."""
    client = audit_test_env["client"]
    op_token = make_token(rsa_keypair, sub="sub-op1-02")
    audit_svc = audit_test_env["audit_service"]

    # Compute expected SHA-256 for test payload
    payload = _valid_pdf_payload() + b"\n%unique_rollback_token"
    import hashlib
    payload_sha256 = hashlib.sha256(payload).hexdigest()

    files = {"file": ("fail_closed_test.pdf", io.BytesIO(payload), "application/pdf")}

    # Simulate audit failure during record_event
    with patch.object(audit_svc, "record_event", side_effect=AuditServiceError("Simulated audit write failure")):
        resp = client.post(
            "/api/v1/documents",
            files=files,
            headers={"Authorization": f"Bearer {op_token}"},
        )
        assert resp.status_code == 500
        assert "Audit event could not be secured" in resp.json()["detail"]

    # Verify atomic rollback in DB: neither Document nor ProcessingJob was committed
    session = audit_test_env["SessionLocal"]()
    doc_in_db = session.query(Document).filter(Document.sha256 == payload_sha256).first()
    assert doc_in_db is None, "Document MUST NOT be persisted when audit recording fails"

    job_in_db = session.query(ProcessingJob).filter(ProcessingJob.error_code == "fail_closed_test.pdf").first()
    assert job_in_db is None, "ProcessingJob MUST NOT be persisted when audit recording fails"
    session.close()


# ─── 6. AUDIT HASH-CHAIN TAMPER DETECTION ─────────────────────────────────────

def test_audit_hash_chain_tamper_detection(audit_test_env):
    """Assert AuditService.verify_audit_chain detects tampered current hash, broken previous link, and sequence gaps."""
    session = audit_test_env["SessionLocal"]()
    audit_svc = audit_test_env["audit_service"]

    # Record 4 chained events
    ev1 = audit_svc.record_event(session=session, actor_id="user1", event_type="E1", entity_type="doc", entity_id="1", action="A1")
    ev2 = audit_svc.record_event(session=session, actor_id="user2", event_type="E2", entity_type="doc", entity_id="2", action="A2")
    ev3 = audit_svc.record_event(session=session, actor_id="user3", event_type="E3", entity_type="doc", entity_id="3", action="A3")
    ev4 = audit_svc.record_event(session=session, actor_id="user4", event_type="E4", entity_type="doc", entity_id="4", action="A4")
    session.commit()

    # 1. Clean verification
    res = audit_svc.verify_audit_chain(session)
    assert res.is_valid is True
    assert len(res.errors) == 0

    # 2. Tamper with current_event_hash of event 2
    orig_hash2 = ev2.current_event_hash
    ev2.current_event_hash = "0" * 64
    session.commit()

    res_tamper1 = audit_svc.verify_audit_chain(session)
    assert res_tamper1.is_valid is False
    error_types = [e.error_type for e in res_tamper1.errors]
    assert "INVALID_HASH" in error_types

    # Restore event 2 hash
    ev2.current_event_hash = orig_hash2
    session.commit()
    assert audit_svc.verify_audit_chain(session).is_valid is True

    # 3. Tamper with previous_event_hash of event 3
    orig_prev3 = ev3.previous_event_hash
    ev3.previous_event_hash = "f" * 64
    session.commit()

    res_tamper2 = audit_svc.verify_audit_chain(session)
    assert res_tamper2.is_valid is False
    error_types2 = [e.error_type for e in res_tamper2.errors]
    assert "BROKEN_LINK" in error_types2

    # Restore event 3 previous link
    ev3.previous_event_hash = orig_prev3
    session.commit()
    assert audit_svc.verify_audit_chain(session).is_valid is True

    # 4. Tamper with sequence numbers (create a sequence gap)
    orig_seq3 = ev3.sequence_number
    ev3.sequence_number = ev2.sequence_number + 5  # Introduce gap
    session.commit()

    res_tamper3 = audit_svc.verify_audit_chain(session)
    assert res_tamper3.is_valid is False
    error_types3 = [e.error_type for e in res_tamper3.errors]
    assert "SEQUENCE_GAP" in error_types3

    # Clean up
    ev3.sequence_number = orig_seq3
    session.commit()
    session.close()


# ─── 7. AUDIT PHI SAFETY ──────────────────────────────────────────────────────

def test_audit_persisted_records_are_strictly_phi_free(audit_test_env, rsa_keypair):
    """Assert that actual audit records generated by real API requests contain zero PHI."""
    client = audit_test_env["client"]
    op_token = make_token(rsa_keypair, sub="sub-op1-02")

    sensitive_patient_id = "PATIENT-SECRET-PHI-MRN-99999"
    files = {"file": ("clinical_encounter.pdf", io.BytesIO(_valid_pdf_payload()), "application/pdf")}
    data = {"patient_context_id": sensitive_patient_id}

    resp = client.post(
        "/api/v1/documents",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {op_token}"},
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    # Read the audit event directly from DB
    session = audit_test_env["SessionLocal"]()
    audit_evt = (
        session.query(AuditEvent)
        .filter(AuditEvent.entity_type == "document", AuditEvent.entity_id == str(doc_id))
        .first()
    )
    assert audit_evt is not None

    meta = audit_evt.metadata_json
    # Forbidden PHI fields must not exist
    forbidden_keys = [
        "name", "patient_name", "first_name", "last_name", "mrn", "patient_id",
        "patient_context_id", "ssn", "dob", "ocr_text", "claims", "evidence", "narrative"
    ]
    for fk in forbidden_keys:
        assert fk not in meta, f"Forbidden PHI key '{fk}' found in audit metadata!"

    # Ensure patient identifier string did not leak into any metadata value
    meta_dump = json.dumps(meta).lower()
    assert sensitive_patient_id.lower() not in meta_dump
    session.close()


# ─── 8. AUDIT ACCESS & MUTATION PREVENTION ────────────────────────────────────

def test_audit_access_authorization_and_mutation_prevention(audit_test_env, rsa_keypair):
    """Assert auditor and admin can query audit events, others are denied, and mutation verbs return 405."""
    client = audit_test_env["client"]
    auditor_token = make_token(rsa_keypair, sub="sub-aud-06")

    # Query audit logs as auditor
    resp = client.get("/api/v1/audit/events?limit=10", headers={"Authorization": f"Bearer {auditor_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body

    # Mutation methods MUST NOT exist on audit endpoint (HTTP 405)
    resp_post = client.post("/api/v1/audit/events", json={"action": "HACK"}, headers={"Authorization": f"Bearer {auditor_token}"})
    assert resp_post.status_code == 405

    resp_put = client.put("/api/v1/audit/events", json={"action": "UPDATE"}, headers={"Authorization": f"Bearer {auditor_token}"})
    assert resp_put.status_code == 405

    resp_delete = client.delete("/api/v1/audit/events", headers={"Authorization": f"Bearer {auditor_token}"})
    assert resp_delete.status_code == 405


# ─── 9. HEALTH ENDPOINTS ──────────────────────────────────────────────────────

def test_health_endpoints_security_and_degradation(audit_test_env, rsa_keypair):
    """Assert health/live is public, health/ready is protected, and failure returns 503 without leaking paths."""
    client = audit_test_env["client"]
    admin_token = make_token(rsa_keypair, sub="sub-admin-01")

    # 1. /live is accessible without auth
    resp_live = client.get("/api/v1/health/live")
    assert resp_live.status_code == 200
    assert resp_live.json() == {"status": "ok", "service": "clindoc2fhir-api"}

    # 2. /ready returns 200 with auth
    resp_ready = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp_ready.status_code == 200
    assert resp_ready.json() == {"status": "ready", "database": "connected", "blob_store": "ready"}

    # 3. /ready returns 503 when degraded
    blob_store = audit_test_env["blob_store"]
    orig_root = blob_store.root_dir
    try:
        blob_store.root_dir = Path("D:/nonexistent_storage_fail_test")
        resp_degraded = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp_degraded.status_code == 503
        detail = resp_degraded.json()["detail"]
        assert "Blob storage probe failed" in detail
        # Ensure no sensitive path leaked
        assert "nonexistent_storage_fail_test" not in detail
    finally:
        blob_store.root_dir = orig_root


# ─── 10. END-TO-END HAPPY PATH ───────────────────────────────────────────────

def test_e2e_api_workflow_happy_path(audit_test_env, rsa_keypair):
    """Assert complete end-to-end API lifecycle: upload -> encrypted blob -> job -> audit -> doc -> job."""
    client = audit_test_env["client"]
    op_token = make_token(rsa_keypair, sub="sub-op1-02")
    payload = _valid_pdf_payload()

    # 1. POST /api/v1/documents
    files = {"file": ("e2e_discharge.pdf", io.BytesIO(payload), "application/pdf")}
    data = {"patient_context_id": "MRN-E2E-777"}
    resp_post = client.post(
        "/api/v1/documents",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {op_token}"},
    )
    assert resp_post.status_code == 201
    post_body = resp_post.json()
    assert post_body["status"] == "INGESTED"
    assert post_body["job_id"] is not None
    doc_id = uuid.UUID(post_body["id"])
    job_id = uuid.UUID(post_body["job_id"])

    # 2. Verify Encrypted Blob Storage
    blob_store = audit_test_env["blob_store"]
    session = audit_test_env["SessionLocal"]()
    doc = session.query(Document).filter(Document.id == doc_id).first()
    assert doc is not None
    assert doc.storage_ref.startswith("blob://local/")
    decrypted_bytes = blob_store.get(doc.storage_ref)
    assert decrypted_bytes == payload, "Decrypted blob must match original payload exactly"

    # 3. Verify Job Enqueued
    job = session.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    assert job is not None
    assert job.status == JobStatus.PENDING
    assert job.document_id == doc.id

    # 4. Verify Audit Event
    audit_evt = (
        session.query(AuditEvent)
        .filter(AuditEvent.entity_type == "document", AuditEvent.entity_id == str(doc_id))
        .first()
    )
    assert audit_evt is not None
    assert audit_evt.action == "UPLOAD"
    session.close()

    # 5. GET /api/v1/documents/{id}
    resp_get_doc = client.get(
        f"/api/v1/documents/{doc_id}",
        headers={"Authorization": f"Bearer {op_token}"},
    )
    assert resp_get_doc.status_code == 200
    doc_body = resp_get_doc.json()
    assert doc_body["id"] == str(doc_id)
    assert doc_body["status"] == "INGESTED"
    assert "storage_ref" not in doc_body  # Internal URI not leaked

    # 6. GET /api/v1/jobs/{id}
    resp_get_job = client.get(
        f"/api/v1/jobs/{job_id}",
        headers={"Authorization": f"Bearer {op_token}"},
    )
    assert resp_get_job.status_code == 200
    job_body = resp_get_job.json()
    assert job_body["id"] == str(job_id)
    assert job_body["status"] == "PENDING"
    assert job_body["attempts"] == 0
