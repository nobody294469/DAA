"""Comprehensive tests for Phase 8.4 Step 3: OIDC/JWT Authentication & Canonical Role Mapping.

Authority:
  - PRODUCTIZATION_SPEC.md §1.2, §5 (T-01, T-02), §9.2, §16
  - PRODUCTIZATION_DECISIONS.md PD-006, PD-009

Tests:
  - Valid token resolution to local User
  - Missing Authorization header -> 401
  - Malformed Authorization header -> 401
  - Expired token -> 401
  - Invalid cryptographic signature -> 401
  - Wrong issuer -> 401
  - Wrong audience -> 401
  - Unsupported algorithm (none, HS256) -> 401
  - Unknown idp_subject -> 401 (rejects unregistered subjects)
  - Deactivated user -> 401
  - Client-supplied role claims do NOT override database roles
  - JWKS dict key resolution by kid
"""

from __future__ import annotations

import datetime
from datetime import timezone
import time
from typing import Any, Optional
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from clindoc.api.auth import (
    AuthenticatedUser,
    OIDCConfig,
    OIDCTokenValidator,
    get_current_user,
    get_db_session,
    get_token_validator,
    set_global_session_factory,
    set_global_token_validator,
)
from clindoc.db import (
    Base,
    Role,
    User,
    UserRole,
)


# ─── CRYPTOGRAPHIC FIXTURES ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def rsa_keypair():
    """Deterministic RSA keypair for signing and verification."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    # Generate JWK dict
    jwk_dict = RSAAlgorithm.to_jwk(public_key)
    if isinstance(jwk_dict, str):
        import json
        jwk_dict = json.loads(jwk_dict)
    jwk_dict["kid"] = "test-key-1"
    jwk_dict["alg"] = "RS256"
    jwk_dict["use"] = "sig"

    return {
        "private_key": private_key,
        "public_key": public_key,
        "public_pem": pub_pem,
        "kid": "test-key-1",
        "jwks": {"keys": [jwk_dict]},
    }


@pytest.fixture(scope="module")
def rogue_rsa_keypair():
    """Secondary RSA keypair simulating an attacker/rogue signer."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return {"private_key": private_key}


def create_test_token(
    keypair: dict[str, Any],
    *,
    sub: str = "sub-alice-001",
    iss: str = "https://idp.clindoc.test/auth",
    aud: str = "clindoc-api",
    exp_delta_seconds: int = 3600,
    nbf_delta_seconds: int = -10,
    alg: str = "RS256",
    kid: Optional[str] = "test-key-1",
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    """Generate a signed JWT using the provided RSA keypair and claims."""
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

    headers = {"kid": kid} if kid else {}

    return jwt.encode(
        claims,
        keypair["private_key"],
        algorithm=alg,
        headers=headers,
    )


# ─── DATABASE & FASTAPI FIXTURES ─────────────────────────────────────────────

@pytest.fixture
def auth_db_session():
    """In-memory SQLite database populated with canonical roles and test users."""
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

    # Seed canonical roles
    roles = [
        Role(id="document_operator", name="Document Operator", description="Uploads and monitors documents"),
        Role(id="clinical_reviewer", name="Clinical Reviewer", description="Reviews clinical claims and evidence"),
        Role(id="approver", name="Approver", description="Approves verified documents for export"),
        Role(id="system_admin", name="System Administrator", description="Administers platform operations"),
        Role(id="auditor", name="Auditor", description="Inspects audit logs and metrics"),
    ]
    for r in roles:
        session.add(r)

    # Seed test users
    user_alice = User(
        id=uuid.uuid4(),
        idp_subject="sub-alice-001",
        username="alice",
        full_name="Alice Reviewer",
        email="alice@hospital.example.org",
        is_active=True,
    )
    user_bob = User(
        id=uuid.uuid4(),
        idp_subject="sub-bob-002",
        username="bob",
        full_name="Bob Operator",
        email="bob@hospital.example.org",
        is_active=True,
    )
    user_inactive = User(
        id=uuid.uuid4(),
        idp_subject="sub-inactive-003",
        username="charlie",
        full_name="Charlie Inactive",
        email="charlie@hospital.example.org",
        is_active=False,
    )
    session.add_all([user_alice, user_bob, user_inactive])
    session.flush()

    # Assign roles: Alice -> clinical_reviewer, Bob -> document_operator
    session.add(UserRole(user_id=user_alice.id, role_id="clinical_reviewer"))
    session.add(UserRole(user_id=user_bob.id, role_id="document_operator"))
    session.commit()

    set_global_session_factory(lambda: SessionLocal())

    yield session
    session.close()


@pytest.fixture
def auth_api_client(rsa_keypair, auth_db_session):
    """FastAPI test client configured with OIDC authentication dependencies."""
    config = OIDCConfig(
        issuer="https://idp.clindoc.test/auth",
        audience="clindoc-api",
        public_key_pem=rsa_keypair["public_pem"],
        jwks_dict=rsa_keypair["jwks"],
        algorithms=["RS256"],
    )
    validator = OIDCTokenValidator(config)
    set_global_token_validator(validator)

    app = FastAPI(title="Auth Test App")

    @app.get("/api/v1/test/me")
    def me_endpoint(current_user: AuthenticatedUser = Depends(get_current_user)):
        return {
            "username": current_user.username,
            "subject": current_user.subject,
            "roles": sorted(list(current_user.roles)),
        }

    client = TestClient(app)
    return client


# ─── 1. POSITIVE AUTHENTICATION & LOCAL USER RESOLUTION ──────────────────────

def test_valid_token_resolves_correct_local_user(auth_api_client, rsa_keypair):
    """Assert valid signed JWT resolves identity, User, and roles from database."""
    token = create_test_token(rsa_keypair, sub="sub-alice-001")
    resp = auth_api_client.get(
        "/api/v1/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "alice"
    assert data["subject"] == "sub-alice-001"
    assert data["roles"] == ["clinical_reviewer"]


# ─── 2. MISSING & MALFORMED AUTHORIZATION HEADER ─────────────────────────────

def test_missing_authorization_header_returns_401(auth_api_client):
    """Assert missing Authorization header returns HTTP 401 with WWW-Authenticate."""
    resp = auth_api_client.get("/api/v1/test/me")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers
    assert "Bearer" in resp.headers["WWW-Authenticate"]


def test_malformed_authorization_header_returns_401(auth_api_client):
    """Assert malformed authorization header formats are rejected with 401."""
    malformed_headers = [
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "Token abcdef",
        "Bearer not.a.real.jwt.token",
    ]
    for h in malformed_headers:
        resp = auth_api_client.get("/api/v1/test/me", headers={"Authorization": h})
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers


# ─── 3. TEMPORAL CLAIMS (EXPIRED TOKEN) ──────────────────────────────────────

def test_expired_token_returns_401(auth_api_client, rsa_keypair):
    """Assert token with exp in the past is rejected with HTTP 401."""
    token = create_test_token(rsa_keypair, sub="sub-alice-001", exp_delta_seconds=-300)
    resp = auth_api_client.get(
        "/api/v1/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


# ─── 4. INVALID CRYPTOGRAPHIC SIGNATURE ──────────────────────────────────────

def test_invalid_signature_returns_401(auth_api_client, rogue_rsa_keypair):
    """Assert token signed with an untrusted rogue RSA key is rejected with HTTP 401."""
    token = create_test_token(rogue_rsa_keypair, sub="sub-alice-001")
    resp = auth_api_client.get(
        "/api/v1/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "signature" in resp.json()["detail"].lower()


# ─── 5. WRONG ISSUER & AUDIENCE ──────────────────────────────────────────────

def test_wrong_issuer_returns_401(auth_api_client, rsa_keypair):
    """Assert token with unrecognized issuer is rejected with HTTP 401."""
    token = create_test_token(rsa_keypair, iss="https://rogue-idp.example.com")
    resp = auth_api_client.get(
        "/api/v1/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "issuer" in resp.json()["detail"].lower()


def test_wrong_audience_returns_401(auth_api_client, rsa_keypair):
    """Assert token with unrecognized audience is rejected with HTTP 401."""
    token = create_test_token(rsa_keypair, aud="foreign-service-audience")
    resp = auth_api_client.get(
        "/api/v1/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "audience" in resp.json()["detail"].lower()


# ─── 6. UNSUPPORTED ALGORITHMS & NONE ALGORITHM GUARD ────────────────────────

def test_unsupported_algorithm_returns_401(auth_api_client):
    """Assert unsigned JWTs ('none' algorithm) or symmetric tokens are strictly rejected."""
    # Attempt unsigned 'none' algorithm token
    unsigned_token = jwt.encode(
        {"sub": "sub-alice-001", "iss": "https://idp.clindoc.test/auth", "aud": "clindoc-api", "exp": 9999999999},
        key="",
        algorithm="none",
    )
    resp = auth_api_client.get(
        "/api/v1/test/me",
        headers={"Authorization": f"Bearer {unsigned_token}"},
    )
    assert resp.status_code == 401
    assert "forbidden" in resp.json()["detail"].lower() or "algorithm" in resp.json()["detail"].lower()

    # Attempt HS256 HMAC token
    hmac_token = jwt.encode(
        {"sub": "sub-alice-001", "iss": "https://idp.clindoc.test/auth", "aud": "clindoc-api", "exp": 9999999999},
        key="supersecretkey12345678901234567890",
        algorithm="HS256",
    )
    resp_hmac = auth_api_client.get(
        "/api/v1/test/me",
        headers={"Authorization": f"Bearer {hmac_token}"},
    )
    assert resp_hmac.status_code == 401
    assert "algorithm" in resp_hmac.json()["detail"].lower()


# ─── 7. UNKNOWN IDP SUBJECT & DEACTIVATED USER GUARDS ────────────────────────

def test_valid_token_with_unknown_idp_subject_returns_401(auth_api_client, rsa_keypair):
    """Assert valid token with unregistered subject is rejected with HTTP 401."""
    token = create_test_token(rsa_keypair, sub="sub-unknown-stranger-999")
    resp = auth_api_client.get(
        "/api/v1/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "not registered" in resp.json()["detail"].lower()


def test_deactivated_user_returns_401(auth_api_client, rsa_keypair):
    """Assert valid token for inactive user is rejected with HTTP 401."""
    token = create_test_token(rsa_keypair, sub="sub-inactive-003")
    resp = auth_api_client.get(
        "/api/v1/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "deactivated" in resp.json()["detail"].lower()


# ─── 8. CLIENT ROLE CLAIM DOES NOT OVERRIDE DATABASE ROLES ───────────────────

def test_client_role_claim_does_not_override_database_roles(auth_api_client, rsa_keypair):
    """Assert client-injected role/group claims in JWT are strictly ignored (PD-009)."""
    # Bob is only a 'document_operator' in database.
    # Attacker injects claims attempting privilege escalation:
    malicious_claims = {
        "roles": ["system_admin", "approver"],
        "role": "system_admin",
        "groups": ["realm-admin", "clinical_reviewer"],
    }
    token = create_test_token(
        rsa_keypair,
        sub="sub-bob-002",
        extra_claims=malicious_claims,
    )
    resp = auth_api_client.get(
        "/api/v1/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "bob"
    # Must only possess database role; attacker-injected roles are ignored!
    assert data["roles"] == ["document_operator"]
    assert "system_admin" not in data["roles"]
    assert "approver" not in data["roles"]


# ─── 9. JWKS KEY RESOLUTION BY KID ───────────────────────────────────────────

def test_jwks_dict_resolution(rsa_keypair):
    """Assert OIDCTokenValidator correctly resolves keys from a multi-key JWKS structure."""
    config = OIDCConfig(
        issuer="https://idp.clindoc.test/auth",
        audience="clindoc-api",
        jwks_dict=rsa_keypair["jwks"],
        algorithms=["RS256"],
    )
    validator = OIDCTokenValidator(config)

    token = create_test_token(rsa_keypair, sub="sub-alice-001", kid="test-key-1")
    claims = validator.validate_token(token)
    assert claims["sub"] == "sub-alice-001"

    # Token with non-existent kid should be rejected
    token_bad_kid = create_test_token(rsa_keypair, sub="sub-alice-001", kid="non-existent-kid")
    with pytest.raises(Exception) as excinfo:
        validator.validate_token(token_bad_kid)
    assert "kid" in str(excinfo.value).lower()
