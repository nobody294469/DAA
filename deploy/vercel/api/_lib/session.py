"""Signed session cookie shared by api/auth.py and api/solve.py (middleware.js verifies the same format).

cookie = <exp>.<base64url(email)>.<hex HMAC-SHA256(secret, "<exp>.<base64url(email)>")>
Sign-in is on only when CLINDOC_GOOGLE_CLIENT_ID and CLINDOC_SESSION_SECRET are both set.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

COOKIE = "clindoc_session"
TTL = 8 * 3600


def enabled() -> bool:
    return bool(os.environ.get("CLINDOC_GOOGLE_CLIENT_ID") and os.environ.get("CLINDOC_SESSION_SECRET"))


def _sig(msg: str) -> str:
    return hmac.new(os.environ["CLINDOC_SESSION_SECRET"].encode(), msg.encode(), hashlib.sha256).hexdigest()


def make(email: str, now: float | None = None) -> str:
    body = f"{int((now or time.time()) + TTL)}.{base64.urlsafe_b64encode(email.encode()).decode().rstrip('=')}"
    return f"{body}.{_sig(body)}"


def check(cookie_header: str | None, now: float | None = None) -> str | None:
    """The signed-in email, or None."""
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name != COOKIE:
            continue
        try:
            exp, em, sig = value.rsplit(".", 2) if value.count(".") == 2 else (None, None, None)
            if exp is None or not hmac.compare_digest(sig, _sig(f"{exp}.{em}")) or int(exp) < (now or time.time()):
                return None
            return base64.urlsafe_b64decode(em + "=" * (-len(em) % 4)).decode()
        except Exception:
            return None
    return None


def allowed(email: str) -> bool:
    """CLINDOC_ALLOWED_EMAILS / CLINDOC_ALLOWED_DOMAINS (comma lists); with neither set any verified account is allowed."""
    emails = {e.strip().lower() for e in os.environ.get("CLINDOC_ALLOWED_EMAILS", "").split(",") if e.strip()}
    domains = {d.strip().lower().lstrip("@") for d in os.environ.get("CLINDOC_ALLOWED_DOMAINS", "").split(",") if d.strip()}
    if not emails and not domains:
        return True
    e = email.lower()
    return e in emails or e.rpartition("@")[2] in domains


if __name__ == "__main__":
    os.environ.update(CLINDOC_GOOGLE_CLIENT_ID="x", CLINDOC_SESSION_SECRET="s" * 32)
    c = make("a@b.edu", now=1000)
    assert check(f"x=1; {COOKIE}={c}", now=1001) == "a@b.edu"
    assert check(f"{COOKIE}={c}", now=1000 + TTL + 1) is None  # expired
    assert check(f"{COOKIE}={c[:-1]}0") is None and check(None) is None  # tampered / missing
    os.environ["CLINDOC_ALLOWED_DOMAINS"] = "college.edu"
    assert allowed("p@College.edu") and not allowed("p@gmail.com")
    print("session ok")
