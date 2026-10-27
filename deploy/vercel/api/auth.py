"""Vercel serverless function: Google sign-in.

GET    /api/auth  -> {enabled, client_id, email}
POST   /api/auth  {"credential": "<Google ID token>"} -> sets the session cookie
DELETE /api/auth  -> signs out
The ID token is verified by Google's tokeninfo endpoint (signature, expiry) and checked against our client ID.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "_lib"))
import session  # noqa: E402


def verify_google(credential: str) -> str:
    url = "https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(credential)
    with urllib.request.urlopen(url, timeout=10) as r:
        info = json.loads(r.read())
    if info.get("aud") != os.environ["CLINDOC_GOOGLE_CLIENT_ID"] or str(info.get("email_verified")).lower() != "true":
        raise ValueError("token not for this app or email not verified")
    return str(info["email"])


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict, cookie: str | None = None) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not session.enabled():
            return self._send(200, {"enabled": False})
        self._send(200, {"enabled": True, "client_id": os.environ["CLINDOC_GOOGLE_CLIENT_ID"],
                         "email": session.check(self.headers.get("Cookie"))})

    def do_POST(self):
        if not session.enabled():
            return self._send(400, {"error": "Sign-in is not configured."})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            email = verify_google(str(body.get("credential", "")))
        except Exception:
            return self._send(401, {"error": "Google sign-in could not be verified."})
        if not session.allowed(email):
            return self._send(403, {"error": "This Google account is not allowed to use this site."})
        flags = f"Path=/; Max-Age={session.TTL}; HttpOnly; Secure; SameSite=Lax"
        self._send(200, {"email": email}, f"{session.COOKIE}={session.make(email)}; {flags}")

    def do_DELETE(self):
        self._send(200, {"ok": True}, f"{session.COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
