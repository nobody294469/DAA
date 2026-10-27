"""Clindoc2FHIR web API + static site (FastAPI).

Authority:
  - PROJECT_SPEC §5, §7 (pipeline), §21 (evaluation); experiments/FINAL_REPORT_DATA_SHEET.md (numbers)
  - DEC-016 (provenance surfaced per claim), DEC-019 (tiering), docs/decisions/*.md (decision log)

Run: `clindoc serve` (127.0.0.1:8000) or `uvicorn clindoc.web.app:app`. The static site in
web/static is mounted last at "/"; every JSON route lives under /api.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
import threading
from typing import Any, Literal, Optional, get_args

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
import pymupdf
from starlette.middleware.sessions import SessionMiddleware

from clindoc.extract.contracts import FieldType
from clindoc.web import __version__, auth, service
from clindoc.web.service import KIND_BY_PREFIX, REPO_ROOT, sample_pdf

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
RESULTS_PATH = WEB_DIR / "data" / "results.json"
V8_DIAG = REPO_ROOT / "experiments" / "V8-verification-cascade" / "phase4_cascade_diagnostics.json"
V9_DIAG = REPO_ROOT / "experiments" / "V8-verification-cascade" / "v9_qwen_only" / "phase4_cascade_diagnostics.json"
EXAMPLES = REPO_ROOT / "data" / "m2_verification_v3" / "all_examples.jsonl"
SPLITS = REPO_ROOT / "data" / "m2_verification_v3" / "split_manifest.json"
DECISIONS_DIR = REPO_ROOT / "docs" / "decisions"
RUNS_DIR = REPO_ROOT / "reports" / "stage_b_runs"
PROGRESS_DIR = REPO_ROOT / "data" / "m2_results_v3"
PROGRESS_MAX_AGE = timedelta(minutes=15)
MULTIPART_OVERHEAD = 64 * 1024  # form fields + part headers on top of the file bytes
FIELD_TYPES = get_args(FieldType)
TITLES = {"lab": "Lab report", "med": "Medication sheet", "narrative": "Narrative note"}


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─── Read-only data (lazy, cached for the process lifetime) ───────────────────

@lru_cache(maxsize=1)
def results() -> dict[str, Any]:
    if not RESULTS_PATH.is_file():
        raise HTTPException(503, "results.json is missing; run scripts/build_web_data.py")
    return _read_json(RESULTS_PATH)


@lru_cache(maxsize=1)
def split_of() -> dict[str, str]:
    manifest = _read_json(SPLITS)
    return {d: s for s in ("train", "calibration", "validation", "ood") for d in manifest[f"{s}_doc_ids"]}


def _family(doc_id: str) -> str:
    return KIND_BY_PREFIX.get(doc_id.split("_")[0], "unknown")


@lru_cache(maxsize=1)
def explorer_data() -> tuple[list[dict[str, Any]], dict[str, dict], dict[str, dict], dict[str, dict]]:
    """Frozen V8 cascade diagnostics (V3 OOD, 1,826 claims) joined with V3 metadata and V9 Qwen-only."""
    diag = _read_json(V8_DIAG)["diagnostics"]["ood"]
    qwen = {r["example_id"]: r["cascade"] for r in _read_json(V9_DIAG)["diagnostics"]["ood"]}
    ids = {r["example_id"] for r in diag}
    meta = {}
    with open(EXAMPLES, encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e["example_id"] in ids:
                meta[e["example_id"]] = {k: e[k] for k in ("bbox", "page_number", "severity", "doc_template",
                                                           "generation_method", "contradiction_type")}
    by_id = {r["example_id"]: r for r in diag}
    items = sorted(({
        "example_id": r["example_id"], "document_id": r["document_id"], "doc_family": _family(r["document_id"]),
        "field_type": r["field_type"], "claim": r["claim"]["value"], "ground_truth": r["ground_truth_label"],
        "final_label": r["cascade"]["final_label"], "final_confidence": r["cascade"]["final_confidence"],
        "stage_reached": r["cascade"]["stage_reached"], "correct": r["cascade"]["is_correct"],
        "contradiction_type": r["cascade"]["contradiction_type"], "severity": meta[r["example_id"]]["severity"],
    } for r in diag), key=lambda i: i["document_id"])
    return items, by_id, meta, qwen


@lru_cache(maxsize=256)
def _page_size(path: str, n: int) -> Optional[tuple[float, float]]:
    with pymupdf.open(path) as doc:
        if not 1 <= n <= doc.page_count:
            return None
        rect = doc[n - 1].rect
        return float(rect.width), float(rect.height)


@lru_cache(maxsize=1)
def decisions() -> list[dict[str, str]]:
    out = []
    for path in sorted(DECISIONS_DIR.glob("DEC-*.md")):
        text = path.read_text(encoding="utf-8")
        title = re.search(r"^# Decision (DEC-\d+):\s*(.+?)\s*$", text, re.M)
        sections = dict(re.findall(r"^## (.+?)\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S))
        first = lambda name: next((ln.strip() for ln in sections.get(name, "").splitlines() if ln.strip()), "")  # noqa: E731
        out.append({"id": title[1] if title else path.stem[:7], "title": title[2] if title else path.stem,
                    "status": first("Status"), "decision": first("Decision"),
                    "path": path.relative_to(REPO_ROOT).as_posix()})
    return out


def _parse_time(value: Any) -> Optional[datetime]:
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.astimezone()  # naive = local time


def active_progress() -> Optional[dict[str, Any]]:
    """Newest *_progress.json that is still running and was updated in the last 15 minutes."""
    if not PROGRESS_DIR.is_dir():
        return None
    now = datetime.now(timezone.utc)
    for path in sorted(PROGRESS_DIR.glob("*_progress.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = _read_json(path)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("status") != "running":
            continue
        updated = _parse_time(data.get("updated_at")) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if now - updated <= PROGRESS_MAX_AGE:
            return data
    return None


def model_cards() -> list[dict[str, Any]]:
    cards = []
    for path in RUNS_DIR.glob("*.json") if RUNS_DIR.is_dir() else []:
        try:
            card = _read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(card, dict):
            cards.append(card)
    return sorted(cards, key=lambda c: str(c.get("created_at") or ""), reverse=True)


# ─── Request models ───────────────────────────────────────────────────────────

class GoogleSignIn(BaseModel):
    credential: str = Field(..., min_length=20, max_length=4096)  # GIS ID token (JWT)


class VerifyRequest(BaseModel):
    field_type: str = Field(max_length=40)
    value: str = Field(max_length=500)
    unit: Optional[str] = Field(None, max_length=50)
    entity: Optional[str] = Field(None, max_length=200)
    evidence_text: str = Field("", max_length=20_000)
    context_text: Optional[str] = Field(None, max_length=50_000)

    @field_validator("field_type")
    @classmethod
    def _known_field(cls, v: str) -> str:
        if v not in FIELD_TYPES:
            raise ValueError(f"must be one of {', '.join(FIELD_TYPES)}")
        return v

    @field_validator("value")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v


class CachedStatic(StaticFiles):
    """Static site: every file revalidated (ETag / Last-Modified -> cheap 304s).

    ponytail: no long-lived asset caching; a max-age without fingerprinted URLs served stale CSS
    after edits. Add hashed filenames + immutable caching if this is ever deployed publicly.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _png(content: Optional[bytes], cache: str) -> Response:
    if content is None:
        raise HTTPException(404, "Page not found")
    return Response(content, media_type="image/png", headers={"Cache-Control": cache})


# ─── App ──────────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        threading.Thread(target=service.components, daemon=True, name="clindoc-warmup").start()
        yield
        service.store.clear()

    app = FastAPI(title="Clindoc2FHIR", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def _upload_gate(request: Request, call_next):
        # Refuse a declared-oversize upload before Starlette spools the multipart body to disk;
        # service._save_upload re-counts the bytes, which also covers chunked (length-less) bodies.
        length = request.headers.get("content-length", "")
        if (request.url.path == "/api/process" and length.isdigit()
                and int(length) > service.MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD):
            return JSONResponse({"detail": service.too_large()}, status_code=413)
        return await call_next(request)

    if auth.client_id():  # optional Google sign-in (clindoc/web/auth.py)
        @app.middleware("http")
        async def _auth_gate(request: Request, call_next):
            path = request.url.path
            if path.startswith("/api/") and path not in auth.PUBLIC and not request.session.get("user"):
                return JSONResponse({"detail": "Sign in with Google to use this app."}, status_code=401)
            return await call_next(request)

        # Added after the gate so it wraps it: the session is decoded before the gate reads it.
        app.add_middleware(SessionMiddleware, secret_key=auth.session_secret(), session_cookie="clindoc_session",
                           max_age=auth.SESSION_MAX_AGE, same_site="lax", https_only=True)

    @app.get("/api/auth/me")
    def auth_me(request: Request) -> dict[str, Any]:
        cid = auth.client_id()
        return {"enabled": bool(cid), "client_id": cid, "user": request.session.get("user") if cid else None}

    @app.post("/api/auth/google")
    def auth_google(request: Request, body: GoogleSignIn) -> dict[str, Any]:
        try:
            user = auth.verify_credential(body.credential)
        except auth.AuthError as exc:
            raise HTTPException(401, str(exc)) from exc
        request.session["user"] = user
        logger.info("signed in: %s", user["email"])
        return {"user": user}

    @app.post("/api/auth/logout")
    def auth_logout(request: Request) -> dict[str, Any]:
        if auth.client_id():
            request.session.clear()
        return {"ok": True}

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:]) or e['loc'][0]}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"detail": msg or "Invalid request"}, status_code=422)

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "stage_b": service.stage_b_status(),
                "stage_c": service.stage_c_status(), "extraction_llm": service.extraction_llm_status(), "online": service.online_status()}

    @app.get("/api/samples")
    def samples() -> dict[str, Any]:
        splits = split_of()
        out = []
        for prefix, folder in service.SAMPLE_DIRS.items():
            for pdf in sorted(folder.glob(f"{prefix}_*.pdf")):
                doc_id, kind = pdf.stem, KIND_BY_PREFIX[prefix]
                if service.DOC_ID_RE.match(doc_id):
                    out.append({"id": doc_id, "kind": kind, "title": f"{TITLES[kind]} {doc_id.split('_')[1]}",
                                "split": splits.get(doc_id), "has_frozen_verdicts": splits.get(doc_id) == "ood"})
        return {"samples": out}

    @app.post("/api/process")
    def process(file: Optional[UploadFile] = File(None), sample_id: Optional[str] = Form(None, max_length=64),
                doc_type: str = Form("auto", max_length=16)) -> dict[str, Any]:
        # ponytail: a chunked body with no Content-Length is still spooled by Starlette before the
        # byte count rejects it; cap request size at a reverse proxy before exposing beyond localhost.
        return service.process(file, sample_id, doc_type)

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> dict[str, Any]:
        return service.store.get(run_id).payload

    @app.get("/api/runs/{run_id}/pages/{n}.png")
    def run_page(run_id: str, n: int) -> Response:
        return _png(service.render_run_page(service.store.get(run_id), n), "private, max-age=3600")

    @app.get("/api/runs/{run_id}/fhir_bundle.json")
    def fhir_bundle(run_id: str) -> FileResponse:
        r = service.store.get(run_id)
        return FileResponse(r.run_dir / "out" / "fhir_bundle.json", media_type="application/json",
                            filename=f"{r.payload['document_id']}_fhir_bundle.json")

    @app.get("/api/runs/{run_id}/audit_report.json")
    def audit_report(run_id: str) -> FileResponse:
        r = service.store.get(run_id)
        return FileResponse(r.run_dir / "out" / "audit_report.json", media_type="application/json",
                            filename=f"{r.payload['document_id']}_audit_report.json")

    @app.post("/api/verify")
    def verify(req: VerifyRequest) -> dict[str, Any]:
        return service.verify(req.field_type, req.value, req.unit, req.entity, req.evidence_text, req.context_text)

    @app.get("/api/verify/presets")
    def presets() -> dict[str, Any]:
        return {"presets": results()["verify_presets"]}

    @app.get("/api/results")
    def get_results() -> dict[str, Any]:
        return results()

    @app.get("/api/explorer")
    def explorer(document_id: Optional[str] = Query(None, max_length=32),
                 field_type: Optional[str] = Query(None, max_length=40),
                 ground_truth: Optional[str] = Query(None, max_length=32),
                 label: Optional[str] = Query(None, max_length=32),
                 stage: Optional[Literal["A", "B", "C"]] = None,
                 correct: Literal["all", "correct", "errors"] = "all",
                 q: Optional[str] = Query(None, max_length=200),
                 offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
        items = explorer_data()[0]
        needle = (q or "").strip().lower()
        rows = [i for i in items
                if (not document_id or i["document_id"] == document_id)
                and (not field_type or i["field_type"] == field_type)
                and (not ground_truth or i["ground_truth"] == ground_truth)
                and (not label or i["final_label"] == label)
                and (not stage or i["stage_reached"] == stage)
                and (correct == "all" or i["correct"] == (correct == "correct"))
                and (not needle or needle in f"{i['claim']} {i['example_id']} {i['document_id']}".lower())]
        n_correct = sum(i["correct"] for i in rows)
        return {"total": len(rows), "accuracy": round(n_correct / len(rows), 4) if rows else 0.0,
                "errors": len(rows) - n_correct, "escalated": sum(i["stage_reached"] == "C" for i in rows),
                "items": rows[offset:offset + limit],
                "facets": {"documents": sorted({i["document_id"] for i in items}),
                           "field_types": sorted({i["field_type"] for i in items})}}

    @app.get("/api/explorer/{example_id}")
    def explorer_detail(example_id: str) -> dict[str, Any]:
        _, by_id, meta, qwen = explorer_data()
        rec = by_id.get(example_id)
        if rec is None:
            raise HTTPException(404, "Unknown example")
        m, doc = meta[example_id], rec["document_id"]
        pdf = sample_pdf(doc)
        size = _page_size(str(pdf), m["page_number"]) if pdf else None
        page = {"image_url": f"/api/documents/{doc}/pages/{m['page_number']}.png", "width": size[0],
                "height": size[1]} if size else None
        q9 = qwen.get(example_id)
        return {
            "example_id": example_id, "document_id": doc, "doc_family": _family(doc), "field_type": rec["field_type"],
            "group_id": rec["group_id"], "claim": rec["claim"]["value"], "claim_detail": rec["claim"],
            "evidence": rec["evidence"]["text"], "evidence_detail": rec["evidence"],
            "ground_truth": rec["ground_truth_label"], "ground_truth_contradiction_type": m["contradiction_type"],
            "stage_a": rec["stage_a"], "stage_b": rec["stage_b"], "stage_c": rec["stage_c"], "cascade": rec["cascade"],
            "severity": m["severity"], "doc_template": m["doc_template"], "generation_method": m["generation_method"],
            "bbox": m["bbox"], "page_number": m["page_number"], "page": page,
            "qwen_only": None if q9 is None else {"label": q9["final_label"], "rationale": q9["rationale"],
                                                  "correct": q9["is_correct"]},
        }

    @app.get("/api/documents/{doc_id}/pages/{n}.png")
    def document_page(doc_id: str, n: int) -> Response:
        pdf = sample_pdf(doc_id)
        if pdf is None:
            raise HTTPException(404, "Unknown document")
        return _png(service.render_pdf_page(str(pdf), n), "public, max-age=86400")

    @app.get("/api/model")
    def model() -> dict[str, Any]:
        return {"runs": model_cards(), "active": active_progress(),
                "reference": results().get("stage_b_reference"), "serving": service.stage_b_status()}

    @app.get("/api/decisions")
    def get_decisions() -> dict[str, Any]:
        return {"decisions": decisions()}

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
                   include_in_schema=False)
    def _api_not_found(rest: str) -> JSONResponse:
        # Unknown API paths get JSON; the static mount below serves 404.html for everything else.
        return JSONResponse({"detail": "Not found"}, status_code=404)

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/", CachedStatic(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
