"""Vercel serverless function: POST /api/solve  {"text": "..."} or {"pdf_b64": "..."}.

Offline rules (the drug list + lab/vital patterns shared with the local server) always run. If the Vercel project
has CLINDOC_ONLINE_API_KEY set, an OpenAI-compatible model reads the same text and its findings are merged in.
Only spans that appear verbatim in the document are kept. No model weights run here, so claims are not verified.
The key lives only in the Vercel environment (never in code, never returned).
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "_lib"))
import session  # noqa: E402
from text_provider import extract_text  # noqa: E402  (copied from src/clindoc/extract by the deploy step)

MAX_BYTES = 4_000_000
FIELDS = {"lab.value", "lab.flag", "lab.test_name", "med.name", "med.dose", "med.route", "med.frequency"}
SYSTEM = ("You extract clinical facts from text. Return ONLY JSON: "
          '{"extractions":[{"field_type":"...","value":"...","evidence_text":"...","unit_raw":null,"group":"g1"}]}. '
          "field_type is one of lab.test_name, lab.value, lab.flag (HIGH/LOW/CRITICAL/NORMAL), med.name, med.dose, "
          "med.route, med.frequency. Items about the same test or medicine share a group. evidence_text must be an exact "
          "substring of the input. Vital signs (blood pressure, heart rate, temperature, SpO2) count as lab.test_name + "
          "lab.value. Never invent anything that is not written.")


def _pdf_text(data: bytes) -> str:
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = []
    for page in doc:
        plain = page.get_text()
        rows: dict[int, list] = {}
        for w in page.get_text("words"):  # rows rebuilt from positions: some PDFs store table cells out of order
            rows.setdefault(round((w[1] + w[3]) / 6), []).append(w)
        laid = "\n".join(" ".join(x[4] for x in sorted(r)) for _, r in sorted(rows.items()))
        pages.append(laid if len(extract_text(laid)) > len(extract_text(plain)) else plain)
    return "\n\n".join(pages)


def _find(text: str, span: str) -> str | None:
    """The document's own text for `span` (whitespace-tolerant), or None if it is not in the document."""
    if span and span in text:
        return span
    hit = re.search(r"\s+".join(map(re.escape, span.split())), text) if span.strip() else None
    return hit.group(0) if hit else None


def _ai(text: str) -> tuple[list[dict], str | None]:
    key = os.environ.get("CLINDOC_ONLINE_API_KEY", "").strip()
    if not key:
        return [], None
    base = os.environ.get("CLINDOC_ONLINE_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("CLINDOC_ONLINE_MODEL", "gpt-4o-mini")
    body = json.dumps({"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text[:14000]}]})
    req = urllib.request.Request(f"{base}/chat/completions", body.encode(), {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            out = json.loads(json.loads(r.read())["choices"][0]["message"]["content"])["extractions"]
    except Exception as e:  # network, auth, quota, bad JSON: fall back to the rules result
        why = f"{type(e).__name__}"
        if isinstance(e, urllib.error.HTTPError):  # the provider's own error text (never contains our key)
            why += f" {e.code}: " + e.read().decode("utf-8", "ignore")[:240].replace(key, "***")
        return [], why
    keep = []
    for x in out if isinstance(out, list) else []:
        ev = _find(text, str(x.get("evidence_text", "")))
        if x.get("field_type") in FIELDS and str(x.get("value", "")).strip() and ev:
            keep.append({**x, "evidence_text": ev, "group": "ai-" + str(x.get("group", "0"))})
    return keep, f"{urlparse(base).hostname} · {model}"


def solve(text: str) -> dict:
    rules = [{**e, "source": "rules"} for e in extract_text(text)]
    ai, ai_info = _ai(text)
    seen = {(e["field_type"], e["evidence_text"].lower()) for e in rules}
    merged = rules + [{**e, "source": "ai"} for e in ai if (e["field_type"], e["evidence_text"].lower()) not in seen]
    online = bool(os.environ.get("CLINDOC_ONLINE_API_KEY", "").strip())
    return {"ai": "on" if ai_info and "." in ai_info else ("failed" if online else "off"), "ai_info": ai_info,
            "n_rules": len(rules), "n_ai": len(merged) - len(rules), "claims": merged}


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _signed_in(self) -> bool:
        if session.enabled() and not session.check(self.headers.get("Cookie")):
            self._send(401, {"error": "Please sign in with Google first."})
            return False
        return True

    def do_GET(self):
        if not self._signed_in():
            return
        online = bool(os.environ.get("CLINDOC_ONLINE_API_KEY", "").strip())
        self._send(200, {"ai": "on" if online else "off",
                         "model": os.environ.get("CLINDOC_ONLINE_MODEL", "gpt-4o-mini") if online else None})

    def do_POST(self):
        if not self._signed_in():
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > MAX_BYTES:
                return self._send(413, {"error": "File too large (max 4 MB on the hosted demo)."})
            body = json.loads(self.rfile.read(n) or b"{}")
            text = _pdf_text(base64.b64decode(body["pdf_b64"])) if body.get("pdf_b64") else str(body.get("text", ""))
            if not text.strip():
                return self._send(400, {"error": "No readable text. Scanned images need the local server (OCR)."})
            self._send(200, solve(text))
        except Exception as e:
            self._send(400, {"error": f"Could not process the document ({type(e).__name__})."})
