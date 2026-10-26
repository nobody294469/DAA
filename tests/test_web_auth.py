"""Optional Google sign-in gate (clindoc/web/auth.py): off by default, protects /api when configured."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clindoc.web import app as web_app
from clindoc.web import auth

USER = {"email": "dr.a@college.edu", "name": "Dr A", "picture": None}


def test_auth_off_by_default(monkeypatch):
    monkeypatch.delenv("CLINDOC_GOOGLE_CLIENT_ID", raising=False)
    c = TestClient(web_app.create_app())
    assert c.get("/api/auth/me").json() == {"enabled": False, "client_id": None, "user": None}
    assert c.get("/api/decisions").status_code == 200


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setenv("CLINDOC_GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setenv("CLINDOC_SESSION_SECRET", "x" * 48)
    monkeypatch.setattr(auth, "verify_credential", lambda cred: USER if cred == "g" * 30 else (_ for _ in ()).throw(
        auth.AuthError("Google rejected the sign-in token.")))
    return TestClient(web_app.create_app(), base_url="https://testserver")  # session cookie is Secure


def test_gate_blocks_api_until_signed_in(gated):
    assert gated.get("/api/health").status_code == 200
    assert gated.get("/api/decisions").status_code == 401
    assert gated.get("/api/auth/me").json()["user"] is None
    assert gated.post("/api/auth/google", json={"credential": "b" * 30}).status_code == 401
    assert gated.post("/api/auth/google", json={"credential": "g" * 30}).json() == {"user": USER}
    assert gated.get("/api/auth/me").json()["user"] == USER
    assert gated.get("/api/decisions").status_code == 200
    gated.post("/api/auth/logout")
    assert gated.get("/api/decisions").status_code == 401


def test_gate_requires_strong_session_secret(monkeypatch):
    monkeypatch.setenv("CLINDOC_GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("CLINDOC_SESSION_SECRET", "short")
    with pytest.raises(RuntimeError):
        web_app.create_app()
