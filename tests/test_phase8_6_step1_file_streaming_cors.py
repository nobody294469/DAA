"""Focused Integration Tests for Phase 8.6 Step 1: Document File Streaming, CORS & Security Headers.

Authority:
  - PRODUCTIZATION_SPEC.md §7.3 (Reviewer sees source page with evidence highlighted)
  - PRODUCTIZATION_SPEC.md §9.3 (GET /documents/{id}/file endpoint specification)
  - PRODUCTIZATION_SPEC.md §5 (Threat T-21: XSS, CSRF, framing denied, CSP)
  - PRODUCTIZATION_DECISIONS.md PD-007 (AES-256-GCM encrypted blob storage)
  - PRODUCTIZATION_DECISIONS.md PD-009 (RBAC object authorization, system_admin PHI denial)
  - PRODUCTIZATION_DECISIONS.md PD-012 (Fail-closed audit logging)
  - PRODUCTIZATION_DECISIONS.md PD-013 (Browser-based review workbench SPA, high-DPI PDF.js viewer, CORS)

Test Coverage:
  1. Authorized document file streaming (Operator, Reviewer, Approver).
  2. Strict object-level RBAC enforcement (system_admin PHI denial, other operator denial, auditor/integration service denial).
  3. Missing/deleted/purged document error handling.
  4. Missing storage blob error handling (HTTP 404).
  5. Audit logging behavior (DOCUMENT_FILE_DOWNLOADED event recorded, fail-closed rollback on audit failure).
  6. CORS middleware behavior (allowed origins, credentials, preflight OPTIONS, disallowed origin rejection).
  7. Browser security response headers across successful, unauthorized, and health endpoints (X-Frame-Options, CSP, X-Content-Type-Options).
"""

from __future__ import annotations

import datetime
from datetime import timezone
import hashlib
import io
import os
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
from clindoc.api.audit.service import AuditService
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
)
from clindoc.db import (
    AuditEvent,
    Base,
    Document,
    Role,
    User,
    UserRole,
)
from clindoc.db.enums import DocumentStatus, RetentionState
from clindoc.storage.blob import EncryptedFileBlobStore


# ─── FIXTURES & HELPERS ───────────────────────────────────────────────────────

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
        import json
        jwk_dict = json.loads(jwk_dict)
    jwk_dict["kid"] = "phase8-6-test-key-1"
    jwk_dict["alg"] = "RS256"
    jwk_dict["use"] = "sig"

    return {
        "private_key": private_key,
        "public_key": public_key,
        "public_pem": pub_pem,
        "kid": "phase8-6-test-key-1",
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
def env_setup(tmp_path, rsa_keypair):
    """Integrated test environment with in-memory SQLite and encrypted blob store."""
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
        "approver": User(id=uuid.uuid4(), idp_subject="sub-appr-05", username="approver", full_name="Approver", email="appr@test.local", is_active=True),
        "auditor": User(id=uuid.uuid4(), idp_subject="sub-aud-06", username="auditor", full_name="Auditor", email="aud@test.local", is_active=True),
        "integration": User(id=uuid.uuid4(), idp_subject="sub-int-07", username="integrator", full_name="Integrator", email="int@test.local", is_active=True),
    }
    for u in users.values():
        session.add(u)
    session.flush()

    # 3. Assign roles
    assignments = [
        UserRole(user_id=users["admin"].id, role_id=CanonicalRole.SYSTEM_ADMIN.value),
        UserRole(user_id=users["operator1"].id, role_id=CanonicalRole.DOCUMENT_OPERATOR.value),
        UserRole(user_id=users["operator2"].id, role_id=CanonicalRole.DOCUMENT_OPERATOR.value),
        UserRole(user_id=users["reviewer"].id, role_id=CanonicalRole.CLINICAL_REVIEWER.value),
        UserRole(user_id=users["approver"].id, role_id=CanonicalRole.APPROVER.value),
        UserRole(user_id=users["auditor"].id, role_id=CanonicalRole.AUDITOR.value),
        UserRole(user_id=users["integration"].id, role_id=CanonicalRole.INTEGRATION_SERVICE.value),
    ]
    for a in assignments:
        session.add(a)
    session.commit()

    # 4. Initialize encrypted blob store
    blob_dir = tmp_path / "test_blobs"
    blob_store = EncryptedFileBlobStore(root_dir=blob_dir)
    set_global_blob_store(blob_store)

    # 5. Initialize audit service
    audit_service = AuditService()
    set_global_audit_service(audit_service)

    # 6. Initialize OIDC validator
    oidc_config = OIDCConfig.from_env(
        issuer="https://idp.clindoc.test/auth",
        audience="clindoc-api",
        public_key_pem=rsa_keypair["public_pem"],
    )
    validator = OIDCTokenValidator(config=oidc_config)
    set_global_token_validator(validator)
    set_global_session_factory(SessionLocal)

    # 7. Create FastAPI app with custom CORS origins for testing
    app = create_app(
        cors_origins=["http://localhost:5173", "http://localhost:3000"],
    )
    client = TestClient(app)

    yield {
        "client": client,
        "session": session,
        "SessionLocal": SessionLocal,
        "users": users,
        "blob_store": blob_store,
        "audit_service": audit_service,
    }

    # Teardown
    set_global_token_validator(None)
    set_global_session_factory(None)
    set_global_blob_store(None)
    set_global_audit_service(None)
    session.close()


def _create_test_document_with_blob(
    env: dict[str, Any],
    *,
    raw_content: bytes = b"%PDF-1.4\n%Clinical Lab Report Sample\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
    uploader_id: uuid.UUID,
    filename: str = "cbc_report.pdf",
    mime_type: str = "application/pdf",
    status: DocumentStatus = DocumentStatus.PENDING_REVIEW,
) -> Document:
    """Helper to store encrypted blob and insert corresponding Document record."""
    session = env["session"]
    blob_store = env["blob_store"]

    storage_ref = blob_store.put(raw_content)
    sha256_hex = hashlib.sha256(raw_content).hexdigest()

    doc = Document(
        id=uuid.uuid4(),
        sha256=sha256_hex,
        storage_ref=storage_ref,
        original_filename=filename,
        mime_type=mime_type,
        file_size_bytes=len(raw_content),
        page_count=1,
        uploaded_by=uploader_id,
        status=status,
    )
    session.add(doc)
    session.commit()
    return doc


# ─── 1. AUTHORIZED DOCUMENT FILE STREAMING ───────────────────────────────────

def test_operator_can_stream_own_document_file(env_setup, rsa_keypair):
    """Assert document operator can download/stream the file for a document they uploaded."""
    client = env_setup["client"]
    op1 = env_setup["users"]["operator1"]
    raw_pdf = b"%PDF-1.4 sample lab report for operator"

    doc = _create_test_document_with_blob(
        env_setup,
        raw_content=raw_pdf,
        uploader_id=op1.id,
        filename="my_lab_report.pdf",
        mime_type="application/pdf",
    )

    token = make_token(rsa_keypair, sub="sub-op1-02")
    resp = client.get(
        f"/api/v1/documents/{doc.id}/file",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.content == raw_pdf
    assert resp.headers["Content-Type"] == "application/pdf"
    assert resp.headers["Content-Disposition"] == 'inline; filename="my_lab_report.pdf"'
    assert resp.headers["Content-Length"] == str(len(raw_pdf))
    # Browser security headers
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


def test_clinical_reviewer_can_stream_document_file(env_setup, rsa_keypair):
    """Assert clinical reviewer can stream document file for clinical review inspection (PD-013)."""
    client = env_setup["client"]
    op1 = env_setup["users"]["operator1"]
    raw_pdf = b"%PDF-1.4 test document for reviewer"

    doc = _create_test_document_with_blob(
        env_setup,
        raw_content=raw_pdf,
        uploader_id=op1.id,
        filename="discharge_summary.pdf",
    )

    rev_token = make_token(rsa_keypair, sub="sub-rev-04")
    resp = client.get(
        f"/api/v1/documents/{doc.id}/file",
        headers={"Authorization": f"Bearer {rev_token}"},
    )

    assert resp.status_code == 200
    assert resp.content == raw_pdf
    assert resp.headers["Content-Type"] == "application/pdf"
    assert resp.headers["Content-Disposition"] == 'inline; filename="discharge_summary.pdf"'


def test_approver_can_stream_document_file(env_setup, rsa_keypair):
    """Assert approver can stream document file before final approval decision."""
    client = env_setup["client"]
    op1 = env_setup["users"]["operator1"]
    raw_pdf = b"%PDF-1.4 test document for approver"

    doc = _create_test_document_with_blob(
        env_setup,
        raw_content=raw_pdf,
        uploader_id=op1.id,
        filename="approved_candidate.pdf",
    )

    appr_token = make_token(rsa_keypair, sub="sub-appr-05")
    resp = client.get(
        f"/api/v1/documents/{doc.id}/file",
        headers={"Authorization": f"Bearer {appr_token}"},
    )

    assert resp.status_code == 200
    assert resp.content == raw_pdf


# ─── 2. STRICT OBJECT-LEVEL RBAC ENFORCEMENT ─────────────────────────────────

def test_unauthenticated_request_rejected(env_setup):
    """Assert request without Bearer token is rejected with HTTP 401."""
    client = env_setup["client"]
    random_doc_id = uuid.uuid4()

    resp = client.get(f"/api/v1/documents/{random_doc_id}/file")
    assert resp.status_code == 401
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_system_admin_strictly_denied_file_access(env_setup, rsa_keypair):
    """Assert system_admin receives HTTP 403 when attempting to stream document PHI (PD-009)."""
    client = env_setup["client"]
    op1 = env_setup["users"]["operator1"]
    doc = _create_test_document_with_blob(env_setup, uploader_id=op1.id)

    admin_token = make_token(rsa_keypair, sub="sub-admin-01")
    resp = client.get(
        f"/api/v1/documents/{doc.id}/file",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 403
    assert "System administrators are strictly prohibited from accessing document content" in resp.json()["detail"]


def test_operator_denied_other_operator_document_file(env_setup, rsa_keypair):
    """Assert document operator receives HTTP 403 when requesting another operator's file."""
    client = env_setup["client"]
    op2 = env_setup["users"]["operator2"]
    doc = _create_test_document_with_blob(env_setup, uploader_id=op2.id)

    op1_token = make_token(rsa_keypair, sub="sub-op1-02")
    resp = client.get(
        f"/api/v1/documents/{doc.id}/file",
        headers={"Authorization": f"Bearer {op1_token}"},
    )

    assert resp.status_code == 403
    assert "Document operators may only access documents they uploaded" in resp.json()["detail"]


def test_auditor_and_integration_service_denied_file_access(env_setup, rsa_keypair):
    """Assert auditor and integration service accounts cannot download document files."""
    client = env_setup["client"]
    op1 = env_setup["users"]["operator1"]
    doc = _create_test_document_with_blob(env_setup, uploader_id=op1.id)

    aud_token = make_token(rsa_keypair, sub="sub-aud-06")
    resp_aud = client.get(
        f"/api/v1/documents/{doc.id}/file",
        headers={"Authorization": f"Bearer {aud_token}"},
    )
    assert resp_aud.status_code == 403
    assert "Auditors have access to audit logs and metrics" in resp_aud.json()["detail"]

    int_token = make_token(rsa_keypair, sub="sub-int-07")
    resp_int = client.get(
        f"/api/v1/documents/{doc.id}/file",
        headers={"Authorization": f"Bearer {int_token}"},
    )
    assert resp_int.status_code == 403
    assert "Integration service accounts are restricted" in resp_int.json()["detail"]


def test_deleted_or_purged_document_denied(env_setup, rsa_keypair):
    """Assert deleted or purged documents return HTTP 403."""
    client = env_setup["client"]
    session = env_setup["session"]
    op1 = env_setup["users"]["operator1"]

    doc = _create_test_document_with_blob(env_setup, uploader_id=op1.id)
    doc.deleted_at = datetime.datetime.now(timezone.utc)
    doc.retention_state = RetentionState.PURGED
    session.commit()

    token = make_token(rsa_keypair, sub="sub-op1-02")
    resp = client.get(
        f"/api/v1/documents/{doc.id}/file",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "deleted or purged" in resp.json()["detail"]


# ─── 3. MISSING DOCUMENT & STORAGE ERRORS ────────────────────────────────────

def test_missing_document_returns_404(env_setup, rsa_keypair):
    """Assert request for non-existent document ID returns HTTP 404."""
    client = env_setup["client"]
    random_id = uuid.uuid4()
    token = make_token(rsa_keypair, sub="sub-op1-02")

    resp = client.get(
        f"/api/v1/documents/{random_id}/file",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert f"Document with ID '{random_id}' was not found" in resp.json()["detail"]


def test_missing_blob_returns_404(env_setup, rsa_keypair):
    """Assert document whose storage_ref is missing from blob storage returns HTTP 404."""
    client = env_setup["client"]
    session = env_setup["session"]
    op1 = env_setup["users"]["operator1"]

    # Document pointing to non-existent blob
    doc = Document(
        id=uuid.uuid4(),
        sha256="0" * 64,
        storage_ref="blob://local/0000000000000000000000000000000000000000000000000000000000000000",
        original_filename="ghost.pdf",
        mime_type="application/pdf",
        file_size_bytes=100,
        page_count=1,
        uploaded_by=op1.id,
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(doc)
    session.commit()

    token = make_token(rsa_keypair, sub="sub-op1-02")
    resp = client.get(
        f"/api/v1/documents/{doc.id}/file",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert "was not found in storage" in resp.json()["detail"]


# ─── 4. AUDIT LOGGING & FAIL-CLOSED BEHAVIOR ─────────────────────────────────

def test_file_download_generates_audit_event(env_setup, rsa_keypair):
    """Assert successful document file download records an append-only audit event."""
    client = env_setup["client"]
    session = env_setup["session"]
    op1 = env_setup["users"]["operator1"]
    raw_pdf = b"%PDF-1.4 audit test document"

    doc = _create_test_document_with_blob(
        env_setup,
        raw_content=raw_pdf,
        uploader_id=op1.id,
        filename="audited_doc.pdf",
    )

    token = make_token(rsa_keypair, sub="sub-op1-02")
    resp = client.get(
        f"/api/v1/documents/{doc.id}/file",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    # Query audit event
    audit_evt = (
        session.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "document",
            AuditEvent.entity_id == str(doc.id),
            AuditEvent.event_type == "DOCUMENT_FILE_DOWNLOADED",
        )
        .first()
    )
    assert audit_evt is not None
    assert audit_evt.action == "DOWNLOAD"
    assert audit_evt.actor_id == str(op1.id)
    assert audit_evt.metadata_json["file_size_bytes"] == len(raw_pdf)
    assert audit_evt.metadata_json["mime_type"] == "application/pdf"
    assert audit_evt.current_event_hash is not None


def test_audit_failure_fails_closed(env_setup, rsa_keypair):
    """Assert document file streaming fails closed (HTTP 500) if audit recording fails (PD-012)."""
    client = env_setup["client"]
    op1 = env_setup["users"]["operator1"]
    doc = _create_test_document_with_blob(env_setup, uploader_id=op1.id)

    token = make_token(rsa_keypair, sub="sub-op1-02")

    with patch.object(AuditService, "record_event", side_effect=RuntimeError("Disk full / DB unavailable")):
        resp = client.get(
            f"/api/v1/documents/{doc.id}/file",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 500
        assert "Audit event could not be secured" in resp.json()["detail"]


# ─── 5. CORS MIDDLEWARE BEHAVIOR ─────────────────────────────────────────────

def test_cors_preflight_for_allowed_origin(env_setup):
    """Assert OPTIONS preflight request for allowed origin returns 200 with CORS headers."""
    client = env_setup["client"]
    random_doc_id = uuid.uuid4()

    headers = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization, Content-Type, X-Correlation-ID",
    }
    resp = client.options(f"/api/v1/documents/{random_doc_id}/file", headers=headers)

    assert resp.status_code == 200
    assert resp.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert resp.headers["Access-Control-Allow-Credentials"] == "true"
    assert "GET" in resp.headers["Access-Control-Allow-Methods"]
    assert "Authorization" in resp.headers["Access-Control-Allow-Headers"]
    # Security headers attached even on preflight
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_cors_disallows_unauthorized_origin(env_setup):
    """Assert requests from untrusted origins do not receive Access-Control-Allow-Origin."""
    client = env_setup["client"]
    random_doc_id = uuid.uuid4()

    headers = {
        "Origin": "http://evil-attacker.malicious.com",
        "Access-Control-Request-Method": "GET",
    }
    resp = client.options(f"/api/v1/documents/{random_doc_id}/file", headers=headers)

    # Starlette CORS middleware omits Access-Control-Allow-Origin for untrusted origins
    assert "Access-Control-Allow-Origin" not in resp.headers


# ─── 6. BROWSER SECURITY HEADERS ─────────────────────────────────────────────

def test_security_headers_present_on_health_and_error_endpoints(env_setup, rsa_keypair):
    """Assert X-Frame-Options, nosniff, and CSP headers are attached across all endpoints."""
    client = env_setup["client"]

    # 1. Unauthenticated health live endpoint
    resp_live = client.get("/api/v1/health/live")
    assert resp_live.status_code == 200
    assert resp_live.headers["X-Frame-Options"] == "DENY"
    assert resp_live.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in resp_live.headers["Content-Security-Policy"]
    assert "X-Correlation-ID" in resp_live.headers

    # 2. 401 Unauthorized endpoint
    resp_401 = client.get(f"/api/v1/documents/{uuid.uuid4()}/file")
    assert resp_401.status_code == 401
    assert resp_401.headers["X-Frame-Options"] == "DENY"
    assert resp_401.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in resp_401.headers["Content-Security-Policy"]

    # 3. 404 Not Found endpoint
    token = make_token(rsa_keypair, sub="sub-op1-02")
    resp_404 = client.get(
        f"/api/v1/documents/{uuid.uuid4()}/file",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_404.status_code == 404
    assert resp_404.headers["X-Frame-Options"] == "DENY"
    assert resp_404.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in resp_404.headers["Content-Security-Policy"]
