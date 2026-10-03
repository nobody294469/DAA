"""Pipeline service behind the web API: shared components, uploads, run store, page rendering.

Authority:
  - PROJECT_SPEC §5, §7 (Document -> FHIR Bundle + Audit Report), executed via clindoc.cli.process_document
  - DEC-016 (claim-level provenance: evidence span + bbox + verification), DEC-019 (field tiering)
  - DEC-003 / DEC-005 / DEC-009 (Stage B context window, escalation threshold, temperature)

Everything here is synchronous; FastAPI runs it in its threadpool.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from functools import lru_cache
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import threading
import time
from typing import Any, Optional
import uuid

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageDraw, ImageFont
import pymupdf

from clindoc.cli import process_document
from clindoc.extract.contracts import TIER_1_FIELDS, BBoxModel, Claim, Evidence
from clindoc.extract.llm_extractor import MockLLMProvider
from clindoc.extract.region_classifier import _MED_LINE_PATTERN, _NARRATIVE_PATTERN, _PRESCRIPTION_PATTERN
from clindoc.extract.router import ExtractionRouter
from clindoc.fhir.validator import FHIRValidator
from clindoc.m2.schema import LABEL_ORDER
from clindoc.normalize.normalizer import TerminologyNormalizer
from clindoc.ocr.document_text import DocumentText
from clindoc.ocr.ingest import IngestionError, OCREngineError, UnsupportedFileTypeError
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.claim_text import build_claim_text
from clindoc.verify.stage_b import LearnedVerifier
from clindoc.verify.stage_c import StageCJudge, VLLMJudgeClient

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_DIRS = {
    "LAB": REPO_ROOT / "data" / "rendered",
    "MED": REPO_ROOT / "data" / "med_rendered",
    "NARRATIVE": REPO_ROOT / "data" / "narrative_rendered",
}
CHECKPOINT_DIR = REPO_ROOT / "data" / "m2_results_v3" / "checkpoints"
DOC_ID_RE = re.compile(r"^(LAB|MED|NARRATIVE)_\d{4}$")
KIND_BY_PREFIX = {"LAB": "lab", "MED": "med", "NARRATIVE": "narrative"}
PREFIX_BY_KIND = {v: k for k, v in KIND_BY_PREFIX.items()}
DOC_TYPES = ("auto", "lab", "med", "narrative")
ALLOWED_EXTENSIONS = (".pdf", ".json", ".png", ".jpg", ".jpeg", ".tif", ".tiff")
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_RUNS = 32
RENDER_DPI = 144
MAX_RENDER_PX = 4000  # cap for DocumentText-JSON canvases (page sizes are client-controlled)

OCR_MISSING = (
    "This document has no text layer (scanned image or image-only PDF) and needs OCR, but the "
    "PaddleOCR environment (.venv_ocr) is not available on this server. Upload a born-digital PDF "
    "or a DocumentText JSON instead."
)

CLAIM_KEYS = ("claim_id", "group_id", "field_type", "value", "value_numeric", "unit_raw", "extraction_method",
              "extraction_confidence", "normalized_code", "code_system", "code_confidence")
EVIDENCE_KEYS = ("evidence_id", "page_number", "text", "char_start", "char_end", "bbox", "ocr_confidence")
VERIFICATION_KEYS = ("label", "confidence", "stage_reached", "stage_a_label", "stage_b_label",
                     "stage_b_raw_confidence", "contradiction_type", "rationale", "escalated")


# ─── Shared pipeline components ───────────────────────────────────────────────

@dataclass
class Components:
    router: ExtractionRouter
    normalizer: TerminologyNormalizer
    cascade: VerificationCascade
    checkpoint: Optional[str]
    vllm_base_url: Optional[str]


_BUILD_LOCK = threading.Lock()
# ponytail: one global lock serialises every pipeline call (normalizer caches, cascade last_stage_*,
# Stage B model); build per-request components if concurrent throughput ever matters.
PIPELINE_LOCK = threading.Lock()
_components: Optional[Components] = None


def resolve_checkpoint() -> Optional[Path]:
    """--checkpoint / CLINDOC_STAGE_B_CHECKPOINT ('none' forces the heuristic), else newest local *.pt."""
    raw = os.environ.get("CLINDOC_STAGE_B_CHECKPOINT", "").strip()
    if raw.lower() == "none":
        return None
    if raw:
        return Path(raw)
    ckpts = sorted(CHECKPOINT_DIR.glob("*.pt"), key=lambda p: p.stat().st_mtime) if CHECKPOINT_DIR.is_dir() else []
    return ckpts[-1] if ckpts else None


def _display_path(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(p)


def components() -> Components:
    """Build the router / normalizer / cascade once (first call pays model loading)."""
    global _components
    with _BUILD_LOCK:
        if _components is None:
            ckpt = resolve_checkpoint()
            device = os.environ.get("CLINDOC_DEVICE", "cpu").strip() or "cpu"
            stage_b = LearnedVerifier(model_path=ckpt, device=None if device == "auto" else device)
            base_url = os.environ.get("CLINDOC_VLLM_BASE_URL", "").strip() or None
            if base_url:
                model = os.environ.get("CLINDOC_VLLM_MODEL", "").strip()
                client = VLLMJudgeClient(base_url=base_url, **({"model_name": model} if model else {}))
                stage_c = StageCJudge(client=client)
            else:
                stage_c = StageCJudge()
            _components = Components(
                router=ExtractionRouter(),
                normalizer=TerminologyNormalizer(),
                cascade=VerificationCascade(stage_b=stage_b, stage_c=stage_c),
                checkpoint=_display_path(ckpt) if ckpt else None,
                vllm_base_url=base_url,
            )
        return _components


def stage_b_status() -> dict[str, Any]:
    c = components()
    sb = c.cascade.stage_b
    loaded = sb.is_model_loaded
    error = getattr(sb, "load_error", None)
    if c.checkpoint and not loaded and not error:
        error = "Stage B checkpoint could not be loaded; see the server log."
    return {
        "mode": "modernbert" if loaded else "heuristic",
        "checkpoint": c.checkpoint,
        "device": (getattr(sb, "device", None) or getattr(sb, "_device", None)) if loaded else None,
        "temperature": float(sb.scaler.temperature) if loaded else None,
        "base_model": getattr(sb, "base_model_id", None) if loaded else None,
        "load_error": error,
    }


def stage_c_status() -> dict[str, Any]:
    c = components()
    return {"mode": "vllm" if c.vllm_base_url else "mock",
            "model": getattr(c.cascade.stage_c.client, "model_name", None), "base_url": c.vllm_base_url}


def extraction_llm_status() -> dict[str, str]:
    provider = getattr(components().router.narrative, "_provider", None)
    return {"mode": "mock" if isinstance(provider, MockLLMProvider) else "vllm"}


def warnings() -> list[str]:
    out = []
    if stage_b_status()["mode"] == "heuristic":
        out.append("Stage B is running the offline heuristic (no ModernBERT checkpoint loaded); "
                   "Tier 1 verdicts are not model predictions.")
    if stage_c_status()["mode"] == "mock":
        out.append("Stage C is the offline mock judge (no vLLM endpoint configured); escalated verdicts "
                   "and rationales are placeholders.")
    return out


# ─── Run store ────────────────────────────────────────────────────────────────

@dataclass
class Run:
    payload: dict[str, Any]
    run_dir: Path
    source: Path  # file the page images are rendered from
    kind: str     # "pdf" | "json" | "image"


class RunStore:
    """LRU of the last MAX_RUNS runs; evicted runs lose their temp dir."""
    # ponytail: a hard kill (Stop-Process / SIGKILL) skips the lifespan clear(), leaving
    # %TEMP%/clindoc_run_* behind; add a startup sweep of a per-server parent dir if that piles up.

    def __init__(self) -> None:
        self._runs: OrderedDict[str, Run] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, run_id: str, run: Run) -> None:
        with self._lock:
            self._runs[run_id] = run
            while len(self._runs) > MAX_RUNS:
                _, old = self._runs.popitem(last=False)
                shutil.rmtree(old.run_dir, ignore_errors=True)

    def get(self, run_id: str) -> Run:
        try:
            uuid.UUID(run_id)
        except ValueError:
            raise HTTPException(404, "Unknown run") from None
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise HTTPException(404, "Unknown or expired run")
            self._runs.move_to_end(run_id)
            return run

    def clear(self) -> None:
        with self._lock:
            for run in self._runs.values():
                shutil.rmtree(run.run_dir, ignore_errors=True)
            self._runs.clear()


store = RunStore()


# ─── Inputs ───────────────────────────────────────────────────────────────────

def sample_pdf(doc_id: str) -> Optional[Path]:
    """Repo PDF for a LAB_/MED_/NARRATIVE_#### id, or None (ids are validated before any path use)."""
    if not DOC_ID_RE.match(doc_id or ""):
        return None
    path = SAMPLE_DIRS[doc_id.split("_")[0]] / f"{doc_id}.pdf"
    return path if path.is_file() else None


_LAB_HINT = re.compile(r"\d+(?:\.\d+)?\s*(?:g/dL|mg/dL|mmol/L|U/L|10\*\d/\w+|%)", re.IGNORECASE)


def sniff_doc_type(text: str) -> str:
    """lab | med | narrative from DEC-001 cue counts (120/120 on the repo benchmark PDFs)."""
    lab = len(_LAB_HINT.findall(text))
    med = len(_MED_LINE_PATTERN.findall(text)) + len(_PRESCRIPTION_PATTERN.findall(text))
    if lab and med and len(_NARRATIVE_PATTERN.findall(text)) >= 2:
        return "narrative"  # prose mentioning both lab values and medications
    return "lab" if lab > med else "med"


def too_large() -> str:
    return f"File exceeds the {MAX_UPLOAD_BYTES / (1024 * 1024):g} MB upload limit."


def _display_name(filename: str) -> str:
    """Sanitised name for display only; never used to build a path."""
    name = PurePosixPath(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")[:100]
    return name or "upload"


def _quick_text(path: Path, kind: str) -> str:
    try:
        if kind == "pdf":
            with pymupdf.open(path) as doc:
                return "\n".join(doc[i].get_text() for i in range(min(3, doc.page_count)))
        if kind == "json":
            return DocumentText.load(path).get_full_text()
    except Exception:  # unreadable input surfaces later as a 422 from the pipeline
        pass
    return ""


def _save_upload(file: UploadFile, run_dir: Path, doc_type: str) -> tuple[Path, str, str, str, str]:
    """Stream the upload into run_dir; returns (source, kind, document_id, display_name, doc_type)."""
    display = _display_name(file.filename or "")
    ext = PurePosixPath(display).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, f"Unsupported file type '{ext or '(none)'}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}.")
    tmp = run_dir / f"upload{ext}"
    size = 0
    with open(tmp, "wb") as out:
        while chunk := file.file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, too_large())
            out.write(chunk)
    if size == 0:
        raise HTTPException(400, "Uploaded file is empty.")
    kind = {".pdf": "pdf", ".json": "json"}.get(ext, "image")
    if kind == "pdf":
        with open(tmp, "rb") as f:
            if b"%PDF-" not in f.read(1024):
                raise HTTPException(415, "File does not look like a PDF (no %PDF- header).")
    if kind == "json":
        try:
            DocumentText.load(tmp)
        except Exception as err:
            raise HTTPException(422, f"JSON upload is not a valid DocumentText document: {str(err)[:300]}") from None
    if doc_type == "auto":  # filename prefix, else a text sniff, else lab
        prefix, sep, _ = display.upper().partition("_")
        doc_type = KIND_BY_PREFIX.get(prefix) if sep else None
        if doc_type is None:
            text = _quick_text(tmp, kind)
            doc_type = sniff_doc_type(text) if text.strip() else "lab"
    # Fresh id per upload: routing keys off the id prefix, and OCR caches are keyed by id / file stem,
    # so a repo id must never be reused for uploaded content.
    document_id = f"{PREFIX_BY_KIND[doc_type]}_{uuid.uuid4().hex[:8]}"
    source = tmp.rename(run_dir / f"{document_id}{ext}")
    return source, kind, document_id, display, doc_type


def _pages(source: Path, kind: str, run_id: str) -> list[dict[str, Any]]:
    if kind == "pdf":
        with pymupdf.open(source) as doc:
            sizes = [(p.rect.width, p.rect.height) for p in doc]
    elif kind == "json":
        sizes = [(p.width_px, p.height_px) for p in DocumentText.load(source).pages]
    else:
        sizes = [(612, 792)]  # ponytail: OCR records use the pipeline's fixed 612x792 page frame
    return [{"number": i, "width": float(w), "height": float(h), "image_url": f"/api/runs/{run_id}/pages/{i}.png"}
            for i, (w, h) in enumerate(sizes, start=1)]


# ─── /api/process ─────────────────────────────────────────────────────────────

def process(file: Optional[UploadFile], sample_id: Optional[str], doc_type: str) -> dict[str, Any]:
    if doc_type not in DOC_TYPES:
        raise HTTPException(400, f"doc_type must be one of {', '.join(DOC_TYPES)}.")
    has_file = file is not None and bool(file.filename)
    sample_id = (sample_id or "").strip()
    if has_file == bool(sample_id):
        raise HTTPException(400, "Provide exactly one of 'file' or 'sample_id'.")
    if sample_id and sample_pdf(sample_id) is None:
        raise HTTPException(404, f"Unknown sample '{sample_id[:40]}'.")

    run_id = str(uuid.uuid4())
    run_dir = Path(tempfile.mkdtemp(prefix="clindoc_run_"))
    try:
        if has_file:
            source, kind, document_id, source_name, doc_type = _save_upload(file, run_dir, doc_type)
        else:
            source, kind, document_id, source_name = sample_pdf(sample_id), "pdf", sample_id, f"{sample_id}.pdf"
            doc_type = KIND_BY_PREFIX[sample_id.split("_")[0]]
        c = components()
        start = time.perf_counter()
        with PIPELINE_LOCK:
            c.normalizer.clear_audit_candidates()
            result = process_document(source, run_dir / "out", document_id=document_id,
                                      router=c.router, normalizer=c.normalizer, cascade=c.cascade)
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        payload = _payload(run_id, result, doc_type, source_name, elapsed_ms, _pages(source, kind, run_id))
    except Exception as err:
        failure = _http_error(err)
        if failure is None:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise
    else:
        store.add(run_id, Run(payload, run_dir, source, kind))
        return payload
    # Clean up outside the except block: on Windows the pipeline's traceback frames keep the
    # uploaded file open (ingest_document never closes its PyMuPDF handle) until it is released.
    shutil.rmtree(run_dir, ignore_errors=True)
    raise failure


def _http_error(err: Exception) -> Optional[HTTPException]:
    """Map input / pipeline failures to API errors; None = unexpected (500)."""
    if isinstance(err, HTTPException):
        return err
    if isinstance(err, OCREngineError):
        return HTTPException(422, OCR_MISSING)
    if isinstance(err, UnsupportedFileTypeError):
        return HTTPException(415, str(err))
    if isinstance(err, IngestionError):
        return HTTPException(422, f"Document could not be read: {err}")
    if isinstance(err, ValueError):  # e.g. "No clinical claims were extracted"
        return HTTPException(400, str(err)[:500])
    return None


def _payload(run_id: str, result: Any, doc_type: str, source_name: str, elapsed_ms: int,
             pages: list[dict[str, Any]]) -> dict[str, Any]:
    report = result.audit_report.to_dict()
    evidence = {e["evidence_id"]: e for e in report["evidence"]}
    verification = {v["claim_id"]: v for v in report["verifications"]}
    # DEC-015: the entity (test / drug name) lives on the sibling claim in the same group
    entity = {c["group_id"]: c["value"] for c in report["claims"]
              if c["group_id"] and c["field_type"] in ("lab.test_name", "med.name")}
    claims = []
    for c in report["claims"]:
        ev = evidence.get(c["evidence_id"])
        claims.append({
            **{k: c[k] for k in CLAIM_KEYS},
            "claim_text": build_claim_text(c["field_type"], c["value"], c["unit_raw"], entity.get(c["group_id"])),
            "evidence": {k: ev[k] for k in EVIDENCE_KEYS} if ev else None,
            "verification": {k: verification[c["claim_id"]][k] for k in VERIFICATION_KEYS},
        })
    stages = Counter(v["stage_reached"] for v in report["verifications"])
    check = FHIRValidator.validate_bundle(result.fhir_bundle)
    return {
        "run_id": run_id, "document_id": result.document_id, "doc_type": doc_type, "source_name": source_name,
        "elapsed_ms": elapsed_ms, "summary": report["summary"],
        "stages": {s: stages.get(s, 0) for s in ("A", "B", "C")},
        "pages": pages, "claims": claims,
        "fhir": {"bundle_id": result.fhir_bundle.id, "valid": check.is_valid, "errors": check.errors,
                 "resource_counts": check.resource_counts},
        "downloads": {"fhir_bundle": f"/api/runs/{run_id}/fhir_bundle.json",
                      "audit_report": f"/api/runs/{run_id}/audit_report.json"},
        "warnings": warnings(),
    }


# ─── /api/verify ──────────────────────────────────────────────────────────────

def verify(field_type: str, value: str, unit: Optional[str], entity: Optional[str],
           evidence_text: str, context_text: Optional[str]) -> dict[str, Any]:
    c = components()
    start = time.perf_counter()
    claim = Claim(document_id="PLAYGROUND", field_type=field_type, value=value.strip(), unit_raw=(unit or "").strip() or None,
                  extraction_method="deterministic", extraction_confidence=1.0, evidence_id="playground-evidence",
                  region_id="playground")
    evidence, page_text = None, None
    if evidence_text.strip():
        offset = context_text.find(evidence_text) if context_text else -1
        if offset >= 0:  # context only usable when the span sits inside it (DEC-003 window offsets)
            page_text = context_text
        offset = max(offset, 0)
        evidence = Evidence(evidence_id="playground-evidence", document_id="PLAYGROUND", page_number=1,
                            text=evidence_text, char_start=offset, char_end=offset + len(evidence_text),
                            bbox=BBoxModel(x0=0, y0=0, x1=0, y1=0))
    claim_text = build_claim_text(field_type, claim.value, claim.unit_raw, entity)
    with PIPELINE_LOCK:
        c.normalizer.normalize_claim(claim)
        c.normalizer.clear_audit_candidates()
        cascade = VerificationCascade(stage_a=c.cascade.stage_a, stage_b=c.cascade.stage_b, stage_c=c.cascade.stage_c)
        res, a, b, sc = cascade.verify_claim_all_stages(claim, evidence, page_text, claim_text=claim_text)
        probs = c.cascade.stage_b.get_calibrated_probabilities(b) if b else None
    return {
        "final": {"label": res.label, "confidence": res.confidence, "stage_reached": res.stage_reached,
                  "contradiction_type": res.contradiction_type, "rationale": res.rationale, "escalated": res.escalated},
        "claim_text": claim_text,
        "tier": 1 if field_type in TIER_1_FIELDS else 2,
        "stage_a": None if a is None else {
            "label": a.label, "confidence": a.confidence, "contradiction_type": a.contradiction_type,
            "details": json.loads(json.dumps(a.details, default=str))},
        "stage_b": None if b is None else {
            "label": b.label, "raw_confidence": float(b.raw_confidence),
            "calibrated_confidence": float(b.calibrated_confidence),
            "probabilities": {lbl: float(p) for lbl, p in zip(LABEL_ORDER, probs.ravel())},
            "escalated": b.escalated, "escalation_reason": b.escalation_reason},
        "stage_c": None if sc is None else {
            "label": sc.label, "confidence": sc.calibrated_confidence, "contradiction_type": sc.contradiction_type,
            "rationale": sc.rationale, "model_name": sc.model_name, "is_fallback": sc.is_fallback},
        "elapsed_ms": round((time.perf_counter() - start) * 1000),
    }


# ─── Page rendering ───────────────────────────────────────────────────────────

@lru_cache(maxsize=64)
def render_pdf_page(path: str, n: int) -> Optional[bytes]:
    with pymupdf.open(path) as doc:
        if not 1 <= n <= doc.page_count:
            return None
        return doc[n - 1].get_pixmap(dpi=RENDER_DPI).tobytes("png")


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except Exception:  # Pillow built without FreeType
        return ImageFont.load_default()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def render_json_page(path: Path, n: int) -> Optional[bytes]:
    """Draw DocumentText tokens at their bboxes on a white page-sized canvas."""
    pages = DocumentText.load(path).pages
    if not 1 <= n <= len(pages):
        return None
    page = pages[n - 1]
    scale = min(RENDER_DPI / 72, MAX_RENDER_PX / max(page.width_px, page.height_px))
    img = Image.new("RGB", (max(1, round(page.width_px * scale)), max(1, round(page.height_px * scale))), "white")
    draw = ImageDraw.Draw(img)
    for tok in page.tokens:
        b = tok.bbox
        size = min(200, max(6, round((b.y1 - b.y0) * scale * 0.8)))
        draw.text((b.x0 * scale, b.y0 * scale), tok.text, fill="black", font=_font(size))
    return _png(img)


def render_run_page(run: Run, n: int) -> Optional[bytes]:
    if not 1 <= n <= len(run.payload["pages"]):
        return None
    if run.kind == "pdf":
        return render_pdf_page(str(run.source), n)
    if run.kind == "json":
        return render_json_page(run.source, n)
    with Image.open(run.source) as img:
        return _png(img.convert("RGB"))
