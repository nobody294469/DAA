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
import time
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
    primary = os.environ.get("CLINDOC_ONLINE_MODEL", "gpt-4o-mini")
    models = [primary] + [m.strip() for m in os.environ.get("CLINDOC_ONLINE_FALLBACK_MODELS", "").split(",") if m.strip()]
    out, deadline = None, time.monotonic() + 52  # Vercel stops the function at 60 s
    for model in models:  # a busy or retired model should not stop the others
        body = json.dumps({"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text[:14000]}]})
        req = urllib.request.Request(f"{base}/chat/completions", body.encode(), {
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        for attempt in range(2):  # 503/429 spikes and timeouts are often gone a second later
            try:
                with urllib.request.urlopen(req, timeout=max(5, min(22, deadline - time.monotonic()))) as r:
                    out = json.loads(json.loads(r.read())["choices"][0]["message"]["content"])["extractions"]
                break
            except Exception as e:
                why = type(e).__name__
                retry = not isinstance(e, urllib.error.HTTPError) or e.code in (429, 500, 502, 503, 504)
                if isinstance(e, urllib.error.HTTPError):  # provider error text goes to the Vercel logs only, never the page
                    why += f" {e.code}: " + e.read().decode("utf-8", "ignore")[:200].replace(key, "***")
                print(f"smart extraction {model} attempt {attempt + 1} failed:", why, file=sys.stderr)
                if not retry or time.monotonic() > deadline - 5:
                    break
                time.sleep(1)
        if out is not None:
            break
    if out is None:
        return [], "failed"
    keep = []
    for x in out if isinstance(out, list) else []:
        ev = _find(text, str(x.get("evidence_text", "")))
        if x.get("field_type") in FIELDS and str(x.get("value", "")).strip() and ev:
            keep.append({**x, "evidence_text": ev, "group": "ai-" + str(x.get("group", "0"))})
    return keep, "ok"


def solve(text: str) -> dict:
    rules = [{**e, "source": "rules"} for e in extract_text(text)]
    ai, ai_info = _ai(text)
    seen = {(e["field_type"], e["evidence_text"].lower()) for e in rules}
    merged = rules + [{**e, "source": "ai"} for e in ai if (e["field_type"], e["evidence_text"].lower()) not in seen]
    online = bool(os.environ.get("CLINDOC_ONLINE_API_KEY", "").strip())
    return {"ai": "on" if ai_info == "ok" else ("failed" if online else "off"),
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
        self._send(200, {"ai": "on" if online else "off"})

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
