"""Optional Google sign-in: Google Identity Services ID token -> signed session cookie.

Off unless CLINDOC_GOOGLE_CLIENT_ID is set, so local `clindoc serve` stays open. When on, every /api
route except PUBLIC needs a signed-in user; the pages still load and show the Google button (core.js).
  CLINDOC_GOOGLE_CLIENT_ID  OAuth 2.0 "Web application" client ID (public; Google Cloud Console ->
                            APIs & Services -> Credentials). Add the site's origin under
                            "Authorized JavaScript origins" (e.g. http://localhost:8000, the cloud URL).
  CLINDOC_SESSION_SECRET    random string that signs the session cookie (required when auth is on)
  CLINDOC_ALLOWED_EMAILS    optional comma-separated emails allowed in
  CLINDOC_ALLOWED_DOMAINS   optional comma-separated email domains allowed in (e.g. college.edu)
With neither allowlist set, any verified Google account can sign in.
"""
from __future__ import annotations

import os
import time

import httpx

TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
PUBLIC = {"/api/health", "/api/auth/me", "/api/auth/google", "/api/auth/logout"}
SESSION_MAX_AGE = 8 * 3600


class AuthError(Exception):
    pass


def client_id() -> str | None:
    return os.environ.get("CLINDOC_GOOGLE_CLIENT_ID") or None


def session_secret() -> str:
    secret = os.environ.get("CLINDOC_SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("CLINDOC_GOOGLE_CLIENT_ID is set: CLINDOC_SESSION_SECRET must be >= 32 random chars "
                           "(python -c \"import secrets; print(secrets.token_urlsafe(48))\")")
    return secret


def _csv(name: str) -> set[str]:
    return {x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()}


def check_claims(claims: dict, expected_aud: str, now: float | None = None) -> dict:
    """Validate Google ID-token claims and the allowlists; returns the session user."""
    if claims.get("aud") != expected_aud:
        raise AuthError("Sign-in token was issued for a different app.")
    if claims.get("iss") not in ISSUERS:
        raise AuthError("Sign-in token is not from Google.")
    if int(claims.get("exp", 0)) < (now or time.time()):
        raise AuthError("Sign-in token has expired; try again.")
    if str(claims.get("email_verified")).lower() != "true":
        raise AuthError("Your Google email address is not verified.")
    email = str(claims.get("email", "")).lower()
    emails, domains = _csv("CLINDOC_ALLOWED_EMAILS"), _csv("CLINDOC_ALLOWED_DOMAINS")
    if (emails or domains) and email not in emails and email.rsplit("@", 1)[-1] not in domains:
        raise AuthError(f"{email} is not allowed to use this deployment.")
    return {"email": email, "name": claims.get("name") or email, "picture": claims.get("picture")}


def verify_credential(credential: str) -> dict:
    """Verify a GIS credential (ID token) with Google and return the session user.

    ponytail: Google's tokeninfo endpoint checks the signature and expiry (one HTTPS call per sign-in,
    not per request); switch to google-auth's local JWKS verification if sign-ins become high-volume.
    """
    aud = client_id()
    if not aud:
        raise AuthError("Google sign-in is not configured on this server.")
    try:
        resp = httpx.get(TOKENINFO, params={"id_token": credential}, timeout=10)
    except httpx.HTTPError as exc:
        raise AuthError("Could not reach Google to verify the sign-in.") from exc
    if resp.status_code != 200:
        raise AuthError("Google rejected the sign-in token.")
    return check_claims(resp.json(), aud)


if __name__ == "__main__":  # self-check of the claim rules (no network)
    good = {"aud": "cid", "iss": "https://accounts.google.com", "exp": "9999999999",
            "email_verified": "true", "email": "Dr.A@College.edu", "name": "Dr A"}
    assert check_claims(good, "cid")["email"] == "dr.a@college.edu"
    for bad in ({"aud": "other"}, {"iss": "evil.com"}, {"exp": "1"}, {"email_verified": "false"}):
        try:
            check_claims({**good, **bad}, "cid")
            raise SystemExit(f"accepted {bad}")
        except AuthError:
            pass
    os.environ["CLINDOC_ALLOWED_DOMAINS"] = "college.edu"
    assert check_claims(good, "cid")
    try:
        check_claims({**good, "email": "x@gmail.com"}, "cid")
        raise SystemExit("allowlist ignored")
    except AuthError:
        pass
    print("ok")
