"""Comprehensive tests for Phase 8.4 Step 4: RBAC, Object-Level Authorization, and system_admin PHI Denial.

Authority:
  - PRODUCTIZATION_SPEC.md §1.2 (Roles & Separation of Duties), §5 (T-01, T-02, T-16)
  - PRODUCTIZATION_DECISIONS.md PD-009 (RBAC, Separation of Duties, Four-Eyes)

Test Matrix:
  1. Canonical role resolution (all 6 roles: system_admin, document_operator,
     clinical_reviewer, approver, auditor, integration_service).
  2. Valid role -> allowed (HTTP 200).
  3. Missing role -> forbidden (HTTP 403).
  4. Insufficient role -> forbidden (HTTP 403).
  5. JWT role claim privilege escalation attempt -> still 403 (DB roles are authoritative).
  6. system_admin PHI isolation -> HTTP 403 for document content, claims, evidence, clinical text.
  7. system_admin administrative access -> allowed (HTTP 200) for non-PHI configuration & operations.
  8. Object-level access outside scope -> HTTP 403 (operator accessing someone else's document).
  9. Object-level access within scope -> allowed (operator accessing own document).
  10. Four-Eyes approval policy -> HTTP 403 when uploader == approver; allowed when distinct.
  11. integration_service restrictions -> HTTP 403 for review/approval; allowed for export.
  12. auditor restrictions -> allowed for audit logs; HTTP 403 for document content / PHI.
  13. Deny-by-default behavior -> empty / unknown roles return HTTP 403.
  14. Client-supplied query/body parameters (role=admin, user_id=...) never override server DB decisions.
"""

from __future__ import annotations

import datetime
from datetime import timezone
from typing import Any, Optional
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from clindoc.api.auth import (
    ALL_CANONICAL_ROLES,
    AuthenticatedUser,
    CanonicalRole,
    OIDCConfig,
    OIDCTokenValidator,
    authorize_claim_access,
    authorize_document_access,
    authorize_job_access,
    check_four_eyes_approval,
    deny_system_admin_phi,
    get_current_user,
    get_db_session,
    require_admin_access,
    require_audit_access,
    require_export_access,
    require_phi_access,
    require_role,
    set_global_session_factory,
    set_global_token_validator,
)
from clindoc.db import (
    Base,
    Claim,
    Document,
    ProcessingJob,
    Role,
    User,
    UserRole,
)
from clindoc.db.enums import (
    ClaimOrigin,
    ClaimReviewState,
    DocumentStatus,
    JobStatus,
    RetentionState,
)


# ─── CRYPTOGRAPHIC FIXTURES ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def rsa_keypair():
    """Deterministic RSA keypair for cryptographic JWT signing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    jwk_dict = RSAAlgorithm.to_jwk(public_key)
    if isinstance(jwk_dict, str):
        import json
        jwk_dict = json.loads(jwk_dict)
    jwk_dict["kid"] = "authz-key-1"
    jwk_dict["alg"] = "RS256"
    jwk_dict["use"] = "sig"

    return {
        "private_key": private_key,
        "public_key": public_key,
        "public_pem": pub_pem,
        "kid": "authz-key-1",
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


# ─── DATABASE FIXTURES & SEED DATA ───────────────────────────────────────────

@pytest.fixture
def rbac_db_session():
    """In-memory SQLite database populated with all 6 canonical roles and representative users."""
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

    # Seed all 6 canonical roles
    canonical_role_defs = [
        Role(id=CanonicalRole.SYSTEM_ADMIN.value, name="System Administrator", description="Administers platform operations"),
        Role(id=CanonicalRole.DOCUMENT_OPERATOR.value, name="Document Operator", description="Uploads and monitors documents"),
        Role(id=CanonicalRole.CLINICAL_REVIEWER.value, name="Clinical Reviewer", description="Reviews clinical claims and evidence"),
        Role(id=CanonicalRole.APPROVER.value, name="Approver", description="Approves verified documents for export"),
        Role(id=CanonicalRole.AUDITOR.value, name="Auditor", description="Inspects audit logs and metrics"),
        Role(id=CanonicalRole.INTEGRATION_SERVICE.value, name="Integration Service", description="Pulls approved FHIR bundles"),
    ]
    for r in canonical_role_defs:
        session.add(r)

    # Seed users representing each canonical role
    users = {
        "admin": User(id=uuid.uuid4(), idp_subject="sub-admin-01", username="admin_user", full_name="Sys Admin", email="admin@hospital.test", is_active=True),
        "operator1": User(id=uuid.uuid4(), idp_subject="sub-op1-02", username="operator_one", full_name="Operator One", email="op1@hospital.test", is_active=True),
        "operator2": User(id=uuid.uuid4(), idp_subject="sub-op2-03", username="operator_two", full_name="Operator Two", email="op2@hospital.test", is_active=True),
        "reviewer": User(id=uuid.uuid4(), idp_subject="sub-rev-04", username="reviewer_user", full_name="Clinical Reviewer", email="rev@hospital.test", is_active=True),
        "approver": User(id=uuid.uuid4(), idp_subject="sub-app-05", username="approver_user", full_name="Supervisor Approver", email="app@hospital.test", is_active=True),
        "auditor": User(id=uuid.uuid4(), idp_subject="sub-aud-06", username="auditor_user", full_name="Compliance Auditor", email="aud@hospital.test", is_active=True),
        "integration": User(id=uuid.uuid4(), idp_subject="sub-int-07", username="integration_svc", full_name="EHR Integration Svc", email="int@hospital.test", is_active=True),
        "noroles": User(id=uuid.uuid4(), idp_subject="sub-none-08", username="empty_user", full_name="No Role User", email="none@hospital.test", is_active=True),
    }
    for u in users.values():
        session.add(u)
    session.flush()

    # Assign single canonical roles
    session.add(UserRole(user_id=users["admin"].id, role_id=CanonicalRole.SYSTEM_ADMIN.value))
    session.add(UserRole(user_id=users["operator1"].id, role_id=CanonicalRole.DOCUMENT_OPERATOR.value))
    session.add(UserRole(user_id=users["operator2"].id, role_id=CanonicalRole.DOCUMENT_OPERATOR.value))
    session.add(UserRole(user_id=users["reviewer"].id, role_id=CanonicalRole.CLINICAL_REVIEWER.value))
    session.add(UserRole(user_id=users["approver"].id, role_id=CanonicalRole.APPROVER.value))
    session.add(UserRole(user_id=users["auditor"].id, role_id=CanonicalRole.AUDITOR.value))
    session.add(UserRole(user_id=users["integration"].id, role_id=CanonicalRole.INTEGRATION_SERVICE.value))
    session.commit()

    set_global_session_factory(lambda: SessionLocal())

    yield {
        "session": session,
        "users": users,
        "SessionLocal": SessionLocal,
    }
    session.close()


@pytest.fixture
def authz_app(rsa_keypair, rbac_db_session):
    """FastAPI test application with RBAC-protected test endpoints."""
    config = OIDCConfig(
        issuer="https://idp.clindoc.test/auth",
        audience="clindoc-api",
        public_key_pem=rsa_keypair["public_pem"],
        jwks_dict=rsa_keypair["jwks"],
        algorithms=["RS256"],
    )
    validator = OIDCTokenValidator(config)
    set_global_token_validator(validator)

    app = FastAPI(title="RBAC Authorization Test App")

    # 1. Endpoint requiring document_operator
    @app.get("/api/v1/test/operator-only")
    def operator_route(user: AuthenticatedUser = Depends(require_role(CanonicalRole.DOCUMENT_OPERATOR))):
        return {"authorized": True, "role": "document_operator", "username": user.username}

    # 2. Endpoint requiring clinical_reviewer OR approver
    @app.get("/api/v1/test/reviewer-or-approver")
    def review_route(user: AuthenticatedUser = Depends(require_role(CanonicalRole.CLINICAL_REVIEWER, CanonicalRole.APPROVER))):
        return {"authorized": True, "username": user.username}

    # 3. Endpoint requiring system_admin (non-PHI admin)
    @app.get("/api/v1/test/admin/config")
    def admin_route(user: AuthenticatedUser = Depends(require_admin_access)):
        return {"authorized": True, "action": "system_configuration", "username": user.username}

    # 4. Clinical document content / PHI endpoint (system_admin MUST BE DENIED)
    @app.get("/api/v1/test/documents/{doc_id}/phi-content")
    def phi_content_route(
        doc_id: uuid.UUID,
        user: AuthenticatedUser = Depends(require_phi_access),
        session: Session = Depends(get_db_session),
    ):
        doc = session.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        # Enforce object-level access control
        authorize_document_access(doc, user, action="read")
        return {"authorized": True, "doc_id": str(doc.id), "phi": "Sensitive clinical content"}

    # 5. Job status endpoint (object-level scoped)
    @app.get("/api/v1/test/jobs/{job_id}")
    def job_status_route(
        job_id: uuid.UUID,
        user: AuthenticatedUser = Depends(get_current_user),
        session: Session = Depends(get_db_session),
    ):
        job = session.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        authorize_job_access(job, user)
        return {"authorized": True, "job_id": str(job.id), "status": job.status}

    # 6. Audit log access endpoint
    @app.get("/api/v1/test/audit/events")
    def audit_route(user: AuthenticatedUser = Depends(require_audit_access)):
        return {"authorized": True, "view": "audit_events"}

    # 7. FHIR export download endpoint
    @app.get("/api/v1/test/export/bundle")
    def export_route(user: AuthenticatedUser = Depends(require_export_access)):
        return {"authorized": True, "export": "fhir_bundle_json"}

    # 8. Document approval endpoint (enforcing Four-Eyes)
    @app.post("/api/v1/test/documents/{doc_id}/approve")
    def approve_route(
        doc_id: uuid.UUID,
        user: AuthenticatedUser = Depends(require_role(CanonicalRole.APPROVER)),
        session: Session = Depends(get_db_session),
    ):
        doc = session.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        check_four_eyes_approval(doc, user)
        return {"approved": True, "doc_id": str(doc.id), "approver": user.username}

    return TestClient(app)


# ─── 1. CANONICAL ROLE MATRIX RESOLUTION TESTS ────────────────────────────────

def test_all_six_canonical_roles_exist_and_resolve(rbac_db_session):
    """Verify that all six canonical roles from PRODUCTIZATION_SPEC §1.2 are recognized."""
    expected_roles = {
        "system_admin",
        "document_operator",
        "clinical_reviewer",
        "approver",
        "auditor",
        "integration_service",
    }
    assert ALL_CANONICAL_ROLES == expected_roles
    for role_name in expected_roles:
        canonical_enum = CanonicalRole(role_name)
        assert canonical_enum.value == role_name


def test_authenticated_user_role_helper_methods(rbac_db_session):
    """Verify AuthenticatedUser convenience properties and role checking methods."""
    users = rbac_db_session["users"]

    # Verify admin user principal
    admin_auth = AuthenticatedUser(user=users["admin"], subject=users["admin"].idp_subject, roles={"system_admin"})
    assert admin_auth.is_admin is True
    assert admin_auth.is_operator is False
    assert admin_auth.has_role(CanonicalRole.SYSTEM_ADMIN) is True
    assert admin_auth.has_role("system_admin") is True
    assert admin_auth.has_any_role(CanonicalRole.SYSTEM_ADMIN, CanonicalRole.APPROVER) is True
    assert admin_auth.has_any_role(CanonicalRole.CLINICAL_REVIEWER) is False

    # Verify operator user principal
    op_auth = AuthenticatedUser(user=users["operator1"], subject=users["operator1"].idp_subject, roles={"document_operator"})
    assert op_auth.is_operator is True
    assert op_auth.is_admin is False

    # Verify reviewer user principal
    rev_auth = AuthenticatedUser(user=users["reviewer"], subject=users["reviewer"].idp_subject, roles={"clinical_reviewer"})
    assert rev_auth.is_reviewer is True

    # Verify approver user principal
    app_auth = AuthenticatedUser(user=users["approver"], subject=users["approver"].idp_subject, roles={"approver"})
    assert app_auth.is_approver is True

    # Verify auditor user principal
    aud_auth = AuthenticatedUser(user=users["auditor"], subject=users["auditor"].idp_subject, roles={"auditor"})
    assert aud_auth.is_auditor is True

    # Verify integration service principal
    int_auth = AuthenticatedUser(user=users["integration"], subject=users["integration"].idp_subject, roles={"integration_service"})
    assert int_auth.is_integration_service is True


# ─── 2. REQUIRE_ROLE: VALID, MISSING, AND INSUFFICIENT ROLES ─────────────────

def test_require_role_allowed_when_user_has_required_role(authz_app, rsa_keypair, rbac_db_session):
    """Verify require_role returns 200 when authenticated user holds the required role."""
    token = make_token(rsa_keypair, sub="sub-op1-02")
    resp = authz_app.get(
        "/api/v1/test/operator-only",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["authorized"] is True
    assert resp.json()["username"] == "operator_one"


def test_require_role_denies_missing_role_with_403(authz_app, rsa_keypair, rbac_db_session):
    """Verify require_role returns HTTP 403 when user has NO roles assigned."""
    token = make_token(rsa_keypair, sub="sub-none-08")  # User with no roles
    resp = authz_app.get(
        "/api/v1/test/operator-only",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "Forbidden: Insufficient permissions" in resp.json()["detail"]


def test_require_role_denies_insufficient_role_with_403(authz_app, rsa_keypair, rbac_db_session):
    """Verify require_role returns HTTP 403 when user has a valid role, but not the required one."""
    # Reviewer attempting to access operator-only route
    token = make_token(rsa_keypair, sub="sub-rev-04")
    resp = authz_app.get(
        "/api/v1/test/operator-only",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "Forbidden: Insufficient permissions" in resp.json()["detail"]


def test_require_role_accepts_any_of_multiple_allowed_roles(authz_app, rsa_keypair, rbac_db_session):
    """Verify require_role allows access if user has ANY of the specified acceptable roles."""
    # Both reviewer and approver are allowed on /reviewer-or-approver
    rev_token = make_token(rsa_keypair, sub="sub-rev-04")
    resp_rev = authz_app.get("/api/v1/test/reviewer-or-approver", headers={"Authorization": f"Bearer {rev_token}"})
    assert resp_rev.status_code == 200

    app_token = make_token(rsa_keypair, sub="sub-app-05")
    resp_app = authz_app.get("/api/v1/test/reviewer-or-approver", headers={"Authorization": f"Bearer {app_token}"})
    assert resp_app.status_code == 200

    # Operator is NOT allowed
    op_token = make_token(rsa_keypair, sub="sub-op1-02")
    resp_op = authz_app.get("/api/v1/test/reviewer-or-approver", headers={"Authorization": f"Bearer {op_token}"})
    assert resp_op.status_code == 403


# ─── 3. JWT PRIVILEGE ESCALATION ATTEMPT REJECTED ────────────────────────────

def test_jwt_role_claim_privilege_escalation_is_denied(authz_app, rsa_keypair, rbac_db_session):
    """Verify attacker-injected JWT role claim cannot bypass server RBAC (PD-009)."""
    # Operator 1 injects 'system_admin' and 'approver' into JWT payload
    malicious_token = make_token(
        rsa_keypair,
        sub="sub-op1-02",  # Operator in local DB
        extra_claims={
            "roles": ["system_admin", "approver"],
            "role": "system_admin",
            "groups": ["administrators"],
        },
    )

    # Attempting to access admin route must be REJECTED with 403
    resp_admin = authz_app.get(
        "/api/v1/test/admin/config",
        headers={"Authorization": f"Bearer {malicious_token}"},
    )
    assert resp_admin.status_code == 403
    assert "Forbidden" in resp_admin.json()["detail"]


def test_client_supplied_request_parameters_cannot_override_authorization(authz_app, rsa_keypair, rbac_db_session):
    """Verify query or body parameters like ?role=system_admin or ?user_id=... are ignored."""
    token = make_token(rsa_keypair, sub="sub-op1-02")
    # Attacker tries sending ?role=system_admin to spoof permissions
    resp = authz_app.get(
        "/api/v1/test/admin/config?role=system_admin&user_id=admin",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ─── 4. SYSTEM_ADMIN PHI ISOLATION POLICY ────────────────────────────────────

def test_system_admin_denied_phi_document_content_access(authz_app, rsa_keypair, rbac_db_session):
    """Verify system_admin receives HTTP 403 when attempting to access PHI / document content (PD-009)."""
    session = rbac_db_session["session"]
    op1 = rbac_db_session["users"]["operator1"]

    # Create a test document
    doc = Document(
        id=uuid.uuid4(),
        sha256="0" * 64,
        storage_ref="enc://docs/test1.pdf",
        original_filename="clinical_note.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        uploaded_by=op1.id,
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(doc)
    session.commit()

    # Admin requests document content
    admin_token = make_token(rsa_keypair, sub="sub-admin-01")
    resp = authz_app.get(
        f"/api/v1/test/documents/{doc.id}/phi-content",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"].lower()
    assert "system administrators are strictly prohibited" in detail or "phi" in detail


def test_system_admin_allowed_only_for_non_phi_administrative_access(authz_app, rsa_keypair, rbac_db_session):
    """Verify system_admin is permitted for non-PHI configuration and audit oversight."""
    admin_token = make_token(rsa_keypair, sub="sub-admin-01")

    # Config endpoint allowed
    resp_cfg = authz_app.get("/api/v1/test/admin/config", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp_cfg.status_code == 200
    assert resp_cfg.json()["authorized"] is True

    # Audit endpoint allowed
    resp_aud = authz_app.get("/api/v1/test/audit/events", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp_aud.status_code == 200


def test_system_admin_denied_claim_access_via_helper(rbac_db_session):
    """Verify authorize_claim_access helper strictly rejects system_admin."""
    admin_user = rbac_db_session["users"]["admin"]
    admin_principal = AuthenticatedUser(user=admin_user, subject=admin_user.idp_subject, roles={"system_admin"})

    claim = Claim(
        id=uuid.uuid4(),
        claim_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        group_id="g1",
        field_type="medication",
        value="Lisinopril 10mg",
        version=1,
        origin=ClaimOrigin.PIPELINE,
        review_state=ClaimReviewState.PENDING,
        payload={"field": "medication"},
    )

    with pytest.raises(HTTPException) as excinfo:
        authorize_claim_access(claim, admin_principal)
    assert excinfo.value.status_code == 403
    assert "system administrators are strictly prohibited" in excinfo.value.detail.lower()


# ─── 5. OBJECT-LEVEL AUTHORIZATION: DOCUMENT & JOB SCOPE ─────────────────────

def test_document_operator_can_access_own_uploaded_document(authz_app, rsa_keypair, rbac_db_session):
    """Verify document_operator is allowed access to documents they uploaded."""
    session = rbac_db_session["session"]
    op1 = rbac_db_session["users"]["operator1"]

    doc1 = Document(
        id=uuid.uuid4(),
        sha256="1" * 64,
        storage_ref="enc://docs/op1_doc.pdf",
        original_filename="op1_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        page_count=1,
        uploaded_by=op1.id,
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(doc1)
    session.commit()

    op1_token = make_token(rsa_keypair, sub="sub-op1-02")
    resp = authz_app.get(
        f"/api/v1/test/documents/{doc1.id}/phi-content",
        headers={"Authorization": f"Bearer {op1_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["authorized"] is True


def test_document_operator_cannot_access_other_operator_document(authz_app, rsa_keypair, rbac_db_session):
    """Verify document_operator receives HTTP 403 when accessing a document uploaded by another operator."""
    session = rbac_db_session["session"]
    op2 = rbac_db_session["users"]["operator2"]

    doc2 = Document(
        id=uuid.uuid4(),
        sha256="2" * 64,
        storage_ref="enc://docs/op2_doc.pdf",
        original_filename="op2_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        page_count=1,
        uploaded_by=op2.id,  # Uploaded by Operator 2
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(doc2)
    session.commit()

    # Operator 1 attempts to access Operator 2's document
    op1_token = make_token(rsa_keypair, sub="sub-op1-02")
    resp = authz_app.get(
        f"/api/v1/test/documents/{doc2.id}/phi-content",
        headers={"Authorization": f"Bearer {op1_token}"},
    )
    assert resp.status_code == 403
    assert "Document operators may only access documents they uploaded" in resp.json()["detail"]


def test_clinical_reviewer_can_access_active_documents(authz_app, rsa_keypair, rbac_db_session):
    """Verify clinical_reviewer can access active clinical documents regardless of uploader."""
    session = rbac_db_session["session"]
    op2 = rbac_db_session["users"]["operator2"]

    doc = Document(
        id=uuid.uuid4(),
        sha256="3" * 64,
        storage_ref="enc://docs/review_doc.pdf",
        original_filename="review_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        page_count=1,
        uploaded_by=op2.id,
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(doc)
    session.commit()

    rev_token = make_token(rsa_keypair, sub="sub-rev-04")
    resp = authz_app.get(
        f"/api/v1/test/documents/{doc.id}/phi-content",
        headers={"Authorization": f"Bearer {rev_token}"},
    )
    assert resp.status_code == 200


def test_object_job_access_scoping(authz_app, rsa_keypair, rbac_db_session):
    """Verify object-level job monitoring scoping for operator, admin, and other operator."""
    session = rbac_db_session["session"]
    op1 = rbac_db_session["users"]["operator1"]
    op2 = rbac_db_session["users"]["operator2"]

    doc1 = Document(
        id=uuid.uuid4(),
        sha256="4" * 64,
        storage_ref="enc://docs/doc1.pdf",
        original_filename="doc1.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        uploaded_by=op1.id,
        status=DocumentStatus.PROCESSING,
    )
    job1 = ProcessingJob(
        id=uuid.uuid4(),
        document_id=doc1.id,
        status=JobStatus.RUNNING,
        current_stage="OCR",
    )
    job1.document = doc1
    session.add_all([doc1, job1])
    session.commit()

    # Operator 1 can view own job
    op1_token = make_token(rsa_keypair, sub="sub-op1-02")
    resp_op1 = authz_app.get(f"/api/v1/test/jobs/{job1.id}", headers={"Authorization": f"Bearer {op1_token}"})
    assert resp_op1.status_code == 200

    # Operator 2 CANNOT view Operator 1's job -> 403
    op2_token = make_token(rsa_keypair, sub="sub-op2-03")
    resp_op2 = authz_app.get(f"/api/v1/test/jobs/{job1.id}", headers={"Authorization": f"Bearer {op2_token}"})
    assert resp_op2.status_code == 403
    assert "Document operators may only monitor jobs for their own uploaded documents" in resp_op2.json()["detail"]

    # Admin CAN view job operational metadata
    admin_token = make_token(rsa_keypair, sub="sub-admin-01")
    resp_adm = authz_app.get(f"/api/v1/test/jobs/{job1.id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp_adm.status_code == 200


# ─── 6. FOUR-EYES APPROVAL POLICY ────────────────────────────────────────────

def test_four_eyes_approval_denied_when_uploader_is_approver(authz_app, rsa_keypair, rbac_db_session):
    """Verify Four-Eyes policy: uploader cannot approve their own document for export (PD-009)."""
    session = rbac_db_session["session"]
    approver = rbac_db_session["users"]["approver"]

    # Document uploaded by the approver user
    self_uploaded_doc = Document(
        id=uuid.uuid4(),
        sha256="5" * 64,
        storage_ref="enc://docs/self.pdf",
        original_filename="self.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        uploaded_by=approver.id,  # Uploader is the Approver!
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(self_uploaded_doc)
    session.commit()

    approver_token = make_token(rsa_keypair, sub="sub-app-05")
    resp = authz_app.post(
        f"/api/v1/test/documents/{self_uploaded_doc.id}/approve",
        headers={"Authorization": f"Bearer {approver_token}"},
    )
    assert resp.status_code == 403
    assert "Four-Eyes Policy violation" in resp.json()["detail"]


def test_four_eyes_approval_allowed_when_uploader_and_approver_are_distinct(authz_app, rsa_keypair, rbac_db_session):
    """Verify Four-Eyes policy: approval succeeds when approver is distinct from uploader."""
    session = rbac_db_session["session"]
    op1 = rbac_db_session["users"]["operator1"]

    # Document uploaded by Operator 1
    doc = Document(
        id=uuid.uuid4(),
        sha256="6" * 64,
        storage_ref="enc://docs/distinct.pdf",
        original_filename="distinct.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        uploaded_by=op1.id,  # Uploader is Operator 1
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(doc)
    session.commit()

    # Approver approves document uploaded by Operator 1
    approver_token = make_token(rsa_keypair, sub="sub-app-05")
    resp = authz_app.post(
        f"/api/v1/test/documents/{doc.id}/approve",
        headers={"Authorization": f"Bearer {approver_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["approved"] is True


# ─── 7. INTEGRATION SERVICE & AUDITOR RESTRICTIONS ───────────────────────────

def test_integration_service_cannot_review_or_approve(authz_app, rsa_keypair, rbac_db_session):
    """Verify integration_service cannot access reviewer, operator, or approval endpoints."""
    int_token = make_token(rsa_keypair, sub="sub-int-07")

    # Cannot access review route
    resp_rev = authz_app.get("/api/v1/test/reviewer-or-approver", headers={"Authorization": f"Bearer {int_token}"})
    assert resp_rev.status_code == 403

    # Cannot access operator route
    resp_op = authz_app.get("/api/v1/test/operator-only", headers={"Authorization": f"Bearer {int_token}"})
    assert resp_op.status_code == 403

    # IS allowed for export endpoint
    resp_exp = authz_app.get("/api/v1/test/export/bundle", headers={"Authorization": f"Bearer {int_token}"})
    assert resp_exp.status_code == 200


def test_auditor_restrictions(authz_app, rsa_keypair, rbac_db_session):
    """Verify auditor can view audit events but is denied direct document PHI access."""
    aud_token = make_token(rsa_keypair, sub="sub-aud-06")

    # Auditor can access audit events
    resp_aud = authz_app.get("/api/v1/test/audit/events", headers={"Authorization": f"Bearer {aud_token}"})
    assert resp_aud.status_code == 200

    # Auditor is denied direct PHI document content
    doc_id = uuid.uuid4()
    resp_phi = authz_app.get(f"/api/v1/test/documents/{doc_id}/phi-content", headers={"Authorization": f"Bearer {aud_token}"})
    assert resp_phi.status_code == 403


# ─── 8. DENY BY DEFAULT FOR EMPTY OR UNKNOWN ROLES ───────────────────────────

def test_deny_by_default_empty_roles(authz_app, rsa_keypair, rbac_db_session):
    """Verify user with empty roles is denied across all endpoints."""
    no_roles_token = make_token(rsa_keypair, sub="sub-none-08")

    endpoints = [
        "/api/v1/test/operator-only",
        "/api/v1/test/reviewer-or-approver",
        "/api/v1/test/admin/config",
        "/api/v1/test/audit/events",
        "/api/v1/test/export/bundle",
    ]
    for ep in endpoints:
        resp = authz_app.get(ep, headers={"Authorization": f"Bearer {no_roles_token}"})
        assert resp.status_code == 403
