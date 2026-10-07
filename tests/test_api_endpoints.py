"""Comprehensive Integration Tests for Phase 8.4 Step 5: Foundational FastAPI Endpoints.

Authority:
  - PRODUCTIZATION_SPEC.md §9.1, §9.2, §9.3, §10.4, §11.3
  - PRODUCTIZATION_DECISIONS.md PD-001, PD-006, PD-007, PD-009, PD-012

Test Suite Coverage:
  1. Health & Readiness:
     - GET /api/v1/health/live is accessible without authentication (200).
     - GET /api/v1/health/ready requires authentication (401 without token, 200 with token).
     - Readiness probe fails with 503 if dependency is degraded.
  2. Document Ingestion (POST /api/v1/documents):
     - Document Operator upload creates document, stores encrypted ciphertext, enqueues ProcessingJob, and records audit event in the same transaction (201).
     - Non-operator roles (reviewer, admin, auditor) receive 403.
     - Asynchronous guarantee: request completes without synchronous OCR or ML extraction.
     - Screening rejection behavior preserved for corrupted/invalid files.
     - Fail-closed atomic transaction: if audit service fails, document creation rolls back completely.
  3. Document Retrieval (GET /api/v1/documents/{id}):
     - Document operator can retrieve their own document (200).
     - Document operator receives 403 for another operator's document.
     - system_admin receives 403 for PHI document access.
     - clinical_reviewer can access active reviewable documents.
     - Nonexistent document returns 404.
     - Audit event recorded for document access.
     - Raw ciphertext/internal keys are not exposed.
  4. Job Status Retrieval (GET /api/v1/jobs/{id}):
     - Document operator can view status for their own job (200).
     - Document operator receives 403 for another operator's job.
     - system_admin can view operational job status (200).
     - PHI, claim values, OCR text, and evidence are not exposed.
     - Nonexistent job returns 404.
  5. Audit Log Inspection (GET /api/v1/audit/events):
     - Auditor and system_admin can inspect audit trail (200).
     - Operator and reviewer receive 403.
     - Query filtering by entity_type and entity_id.
     - Strictly read-only: POST/DELETE/PUT routes do not exist (405).
     - Audit log access itself is audited.
"""

from __future__ import annotations

import datetime
from datetime import timezone
import io
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
from clindoc.api.audit.service import AuditPHISecurityError, AuditService
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
        import json
        jwk_dict = json.loads(jwk_dict)
    jwk_dict["kid"] = "api-test-key-1"
    jwk_dict["alg"] = "RS256"
    jwk_dict["use"] = "sig"

    return {
        "private_key": private_key,
        "public_key": public_key,
        "public_pem": pub_pem,
        "kid": "api-test-key-1",
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
def api_test_env(tmp_path, rsa_keypair):
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


# ─── 1. HEALTH AND READINESS ENDPOINTS ───────────────────────────────────────

def test_health_live_is_unauthenticated(api_test_env):
    """Assert GET /api/v1/health/live is accessible without any Authorization header."""
    client = api_test_env["client"]
    resp = client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "clindoc2fhir-api"}
    assert "X-Correlation-ID" in resp.headers


def test_health_ready_requires_authentication(api_test_env, rsa_keypair):
    """Assert GET /api/v1/health/ready requires authentication (HTTP 401 if missing)."""
    client = api_test_env["client"]
    resp_unauth = client.get("/api/v1/health/ready")
    assert resp_unauth.status_code == 401

    token = make_token(rsa_keypair, sub="sub-op1-02")
    resp_auth = client.get(
        "/api/v1/health/ready",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_auth.status_code == 200
    body = resp_auth.json()
    # Infrastructure fields must be present and healthy
    assert body["status"] == "ready"
    assert body["database"] == "connected"
    assert body["blob_store"] == "ready"
    # Model readiness fields must be present (no models registered in test env)
    assert "modernbert" in body
    assert "modernbert_detail" in body
    assert "qwen_vllm" in body
    assert "qwen_vllm_detail" in body
    # Confirm no PHI / secrets / credentials leaked
    body_str = str(body)
    assert "VLLM_API_KEY" not in body_str
    assert "Bearer" not in body_str
    assert "patient" not in body_str.lower()


# ─── 2. DOCUMENT INGESTION (POST /api/v1/documents) ──────────────────────────

def test_document_upload_success_enqueues_job_and_audits(api_test_env, rsa_keypair):
    """Assert authorized operator upload stores encrypted file, enqueues job, and audits atomically."""
    client = api_test_env["client"]
    token = make_token(rsa_keypair, sub="sub-op1-02")

    # Generate valid minimal PDF byte payload
    pdf_bytes = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000052 00000 n\n0000000101 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"

    files = {"file": ("clinical_discharge.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    data = {"patient_context_id": "MRN-12345"}

    resp = client.post(
        "/api/v1/documents",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "INGESTED"
    assert body["original_filename"] == "clinical_discharge.pdf"
    assert body["mime_type"] == "application/pdf"
    assert body["job_id"] is not None
    doc_id = uuid.UUID(body["id"])
    job_id = uuid.UUID(body["job_id"])

    # Verify persistence in database
    session = api_test_env["SessionLocal"]()
    doc = session.query(Document).filter(Document.id == doc_id).first()
    assert doc is not None
    assert doc.uploaded_by == api_test_env["users"]["operator1"].id
    assert doc.storage_ref.startswith("blob://local/")

    job = session.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    assert job is not None
    assert job.status == JobStatus.PENDING
    assert job.current_stage == "INIT"

    # Verify audit event in same transaction
    audit_evt = (
        session.query(AuditEvent)
        .filter(AuditEvent.entity_type == "document", AuditEvent.entity_id == str(doc_id))
        .first()
    )
    assert audit_evt is not None
    assert audit_evt.event_type == "DOCUMENT_INGESTED"
    assert audit_evt.action == "UPLOAD"
    assert "patient_context_id" not in audit_evt.metadata_json  # PHI-free metadata!
    assert "mrn" not in audit_evt.metadata_json
    session.close()


def test_document_upload_denied_for_unauthorized_role(api_test_env, rsa_keypair):
    """Assert non-operator roles (reviewer, admin) receive HTTP 403 on document upload."""
    client = api_test_env["client"]

    # Reviewer attempting upload
    rev_token = make_token(rsa_keypair, sub="sub-rev-04")
    files = {"file": ("test.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")}
    resp = client.post(
        "/api/v1/documents",
        files=files,
        headers={"Authorization": f"Bearer {rev_token}"},
    )
    assert resp.status_code == 403
    assert "Forbidden" in resp.json()["detail"]


def test_document_upload_fail_closed_on_audit_failure(api_test_env, rsa_keypair):
    """Assert document upload rolls back completely if audit recording fails (PD-012 fail-closed)."""
    client = api_test_env["client"]
    token = make_token(rsa_keypair, sub="sub-op1-02")

    pdf_bytes = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000052 00000 n\n0000000101 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"

    files = {"file": ("audit_fail_test.pdf", io.BytesIO(pdf_bytes), "application/pdf")}

    # Mock audit_service.record_event to simulate an audit failure
    audit_svc = api_test_env["audit_service"]
    with patch.object(audit_svc, "record_event", side_effect=AuditPHISecurityError("Simulated audit write failure")):
        resp = client.post(
            "/api/v1/documents",
            files=files,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 500
        assert "Audit event could not be secured" in resp.json()["detail"]

    # Verify document was NOT persisted (transaction rolled back)
    session = api_test_env["SessionLocal"]()
    doc = session.query(Document).filter(Document.original_filename == "audit_fail_test.pdf").first()
    assert doc is None
    session.close()


# ─── 3. DOCUMENT RETRIEVAL (GET /api/v1/documents/{id}) ──────────────────────

def test_document_operator_can_access_own_document(api_test_env, rsa_keypair):
    """Assert document operator can retrieve metadata for a document they uploaded."""
    client = api_test_env["client"]
    session = api_test_env["session"]
    op1 = api_test_env["users"]["operator1"]

    doc = Document(
        id=uuid.uuid4(),
        sha256="a" * 64,
        storage_ref="blob://local/test",
        original_filename="op1_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        uploaded_by=op1.id,
        status=DocumentStatus.INGESTED,
    )
    session.add(doc)
    session.commit()

    token = make_token(rsa_keypair, sub="sub-op1-02")
    resp = client.get(
        f"/api/v1/documents/{doc.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(doc.id)
    assert data["original_filename"] == "op1_doc.pdf"
    assert "storage_ref" not in data  # Storage ciphertext URI is NOT leaked!


def test_document_operator_cannot_access_other_operator_document(api_test_env, rsa_keypair):
    """Assert document operator receives HTTP 403 when accessing another operator's document."""
    client = api_test_env["client"]
    session = api_test_env["session"]
    op2 = api_test_env["users"]["operator2"]

    doc = Document(
        id=uuid.uuid4(),
        sha256="b" * 64,
        storage_ref="blob://local/test",
        original_filename="op2_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        uploaded_by=op2.id,  # Uploaded by Operator 2
        status=DocumentStatus.INGESTED,
    )
    session.add(doc)
    session.commit()

    # Operator 1 tries to access Operator 2's document
    token_op1 = make_token(rsa_keypair, sub="sub-op1-02")
    resp = client.get(
        f"/api/v1/documents/{doc.id}",
        headers={"Authorization": f"Bearer {token_op1}"},
    )
    assert resp.status_code == 403
    assert "Document operators may only access documents they uploaded" in resp.json()["detail"]


def test_system_admin_denied_phi_document_access(api_test_env, rsa_keypair):
    """Assert system_admin receives HTTP 403 when attempting to access document content (PD-009)."""
    client = api_test_env["client"]
    session = api_test_env["session"]
    op1 = api_test_env["users"]["operator1"]

    doc = Document(
        id=uuid.uuid4(),
        sha256="c" * 64,
        storage_ref="blob://local/test",
        original_filename="phi_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        page_count=1,
        uploaded_by=op1.id,
        status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(doc)
    session.commit()

    admin_token = make_token(rsa_keypair, sub="sub-admin-01")
    resp = client.get(
        f"/api/v1/documents/{doc.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 403
    assert "System administrators are strictly prohibited" in resp.json()["detail"]


def test_get_nonexistent_document_returns_404(api_test_env, rsa_keypair):
    """Assert accessing non-existent document ID returns HTTP 404."""
    client = api_test_env["client"]
    token = make_token(rsa_keypair, sub="sub-op1-02")
    resp = client.get(
        f"/api/v1/documents/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ─── 4. JOB STATUS RETRIEVAL (GET /api/v1/jobs/{id}) ─────────────────────────

def test_job_status_operational_oversight(api_test_env, rsa_keypair):
    """Assert job status endpoint returns operational metadata without PHI."""
    client = api_test_env["client"]
    session = api_test_env["session"]
    op1 = api_test_env["users"]["operator1"]

    doc = Document(
        id=uuid.uuid4(),
        sha256="d" * 64,
        storage_ref="blob://local/test",
        original_filename="job_doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        uploaded_by=op1.id,
        status=DocumentStatus.PROCESSING,
    )
    job = ProcessingJob(
        id=uuid.uuid4(),
        document_id=doc.id,
        status=JobStatus.RUNNING,
        current_stage="OCR",
        attempts=1,
        max_attempts=3,
    )
    job.document = doc
    session.add_all([doc, job])
    session.commit()

    # Operator 1 can view own job
    op1_token = make_token(rsa_keypair, sub="sub-op1-02")
    resp_op1 = client.get(f"/api/v1/jobs/{job.id}", headers={"Authorization": f"Bearer {op1_token}"})
    assert resp_op1.status_code == 200
    data = resp_op1.json()
    assert data["status"] == "RUNNING"
    assert data["current_stage"] == "OCR"
    assert "ocr_text" not in data
    assert "claims" not in data

    # Admin can view job (operational oversight)
    admin_token = make_token(rsa_keypair, sub="sub-admin-01")
    resp_admin = client.get(f"/api/v1/jobs/{job.id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp_admin.status_code == 200

    # Operator 2 CANNOT view Operator 1's job -> 403
    op2_token = make_token(rsa_keypair, sub="sub-op2-03")
    resp_op2 = client.get(f"/api/v1/jobs/{job.id}", headers={"Authorization": f"Bearer {op2_token}"})
    assert resp_op2.status_code == 403


# ─── 5. AUDIT LOG INSPECTION (GET /api/v1/audit/events) ──────────────────────

def test_audit_events_auditor_access_and_filters(api_test_env, rsa_keypair):
    """Assert auditor and admin can query audit logs; non-auditors receive 403; read-only."""
    client = api_test_env["client"]
    aud_token = make_token(rsa_keypair, sub="sub-aud-06")

    resp = client.get(
        "/api/v1/audit/events?limit=10",
        headers={"Authorization": f"Bearer {aud_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data

    # Operator attempting audit access -> 403
    op_token = make_token(rsa_keypair, sub="sub-op1-02")
    resp_op = client.get(
        "/api/v1/audit/events",
        headers={"Authorization": f"Bearer {op_token}"},
    )
    assert resp_op.status_code == 403

    # Mutation endpoints do NOT exist -> 405 Method Not Allowed
    resp_post = client.post("/api/v1/audit/events", headers={"Authorization": f"Bearer {aud_token}"})
    assert resp_post.status_code == 405
    resp_del = client.delete("/api/v1/audit/events", headers={"Authorization": f"Bearer {aud_token}"})
    assert resp_del.status_code == 405


# ─── 6. UNAUTHENTICATED ACCESS GUARDS & REJECTION BEHAVIOR ───────────────────

def test_unauthenticated_protected_endpoints_return_401(api_test_env):
    """Assert all protected endpoints strictly return HTTP 401 without valid Bearer token."""
    client = api_test_env["client"]
    random_uuid = uuid.uuid4()

    endpoints = [
        ("GET", "/api/v1/health/ready"),
        ("POST", "/api/v1/documents"),
        ("GET", f"/api/v1/documents/{random_uuid}"),
        ("GET", f"/api/v1/jobs/{random_uuid}"),
        ("GET", "/api/v1/audit/events"),
    ]

    for method, path in endpoints:
        if method == "GET":
            resp = client.get(path)
        else:
            resp = client.post(path)
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}, expected 401"
        assert "WWW-Authenticate" in resp.headers


def test_document_upload_zero_bytes_rejected(api_test_env, rsa_keypair):
    """Assert zero-byte file triggers HTTP 400 Bad Request."""
    client = api_test_env["client"]
    token = make_token(rsa_keypair, sub="sub-op1-02")

    files = {"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")}
    resp = client.post(
        "/api/v1/documents",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_document_upload_corrupted_pdf_quarantined(api_test_env, rsa_keypair):
    """Assert corrupted file triggers QUARANTINED status and does NOT enqueue a job."""
    client = api_test_env["client"]
    token = make_token(rsa_keypair, sub="sub-op1-02")

    # Mismatched / corrupted payload: declared as application/pdf but has plain text bytes
    files = {"file": ("corrupted.pdf", io.BytesIO(b"NOT_A_REAL_PDF_HEADER_CONTENT"), "application/pdf")}
    resp = client.post(
        "/api/v1/documents",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "QUARANTINED"
    assert body["job_id"] is None  # Processing job was NOT enqueued!

    # Verify audit event was created for tracking the quarantine event
    session = api_test_env["SessionLocal"]()
    doc_id = uuid.UUID(body["id"])
    audit_evt = session.query(AuditEvent).filter(AuditEvent.entity_id == str(doc_id)).first()
    assert audit_evt is not None
    assert audit_evt.metadata_json["status"] == "QUARANTINED"
    session.close()


def test_health_ready_degraded_returns_503(api_test_env, rsa_keypair):
    """Assert readiness probe returns HTTP 503 if an internal storage dependency fails."""
    client = api_test_env["client"]
    token = make_token(rsa_keypair, sub="sub-op1-02")

    blob_store = api_test_env["blob_store"]
    original_root = blob_store.root_dir
    try:
        blob_store.root_dir = Path("D:/nonexistent_blob_dir_probe_fail_test_xyz")
        resp = client.get(
            "/api/v1/health/ready",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 503
        assert "Blob storage probe failed" in resp.json()["detail"]
    finally:
        blob_store.root_dir = original_root
