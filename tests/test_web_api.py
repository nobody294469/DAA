"""HTTP contract tests for the FastAPI web backend (clindoc.web.app).

Offline and deterministic: Stage B is forced to the heuristic (CLINDOC_STAGE_B_CHECKPOINT=none) and
Stage C is the default mock judge (no CLINDOC_VLLM_BASE_URL). Samples come from the committed
data/med_rendered and data/narrative_rendered PDFs (LAB_* only when data/rendered was regenerated).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

import pymupdf
import pytest
from fastapi.testclient import TestClient

from clindoc.web import app as web_app
from clindoc.web import service

REPO = Path(__file__).resolve().parents[1]
MED_PDF = REPO / "data" / "med_rendered" / "MED_0004.pdf"
NARR_PDF = REPO / "data" / "narrative_rendered" / "NARRATIVE_0016.pdf"
HAS_LAB = (REPO / "data" / "rendered" / "LAB_0002.pdf").is_file()
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def client():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CLINDOC_STAGE_B_CHECKPOINT", "none")
        mp.delenv("CLINDOC_VLLM_BASE_URL", raising=False)
        mp.setattr(service, "_components", None)
        with TestClient(web_app.app) as c:
            yield c


@pytest.fixture(scope="module")
def med_run(client):
    r = client.post("/api/process", data={"sample_id": "MED_0004"})
    assert r.status_code == 200, r.text
    return r.json()


def _upload(client, name: str, content: bytes, **data):
    return client.post("/api/process", files={"file": (name, content, "application/octet-stream")}, data=data)


# ─── Health / samples ─────────────────────────────────────────────────────────

def test_health_reports_offline_stages(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["version"] == "0.2.0"
    assert body["stage_b"] == {"mode": "heuristic", "checkpoint": None, "device": None, "temperature": None,
                               "base_model": None, "load_error": None}
    assert body["stage_c"]["mode"] == "mock" and body["stage_c"]["base_url"] is None
    assert body["extraction_llm"]["mode"] in ("mock", "vllm", "rules", "online")
    assert body["online"] == {"enabled": False} or "api_key" not in body["online"]  # the key is never exposed


def test_samples_listing(client):
    samples = {s["id"]: s for s in client.get("/api/samples").json()["samples"]}
    assert len([s for s in samples if s.startswith("MED_")]) == 50
    assert len([s for s in samples if s.startswith("NARRATIVE_")]) == 20
    assert any(s.startswith("LAB_") for s in samples) == HAS_LAB
    med = samples["MED_0004"]
    assert med["kind"] == "med" and med["split"] == "ood" and med["has_frozen_verdicts"] is True
    assert samples["MED_0001"]["split"] == "validation" and samples["MED_0001"]["has_frozen_verdicts"] is False


# ─── /api/process on a sample ─────────────────────────────────────────────────

def test_process_sample_end_to_end(client, med_run):
    run = med_run
    assert run["document_id"] == "MED_0004" and run["doc_type"] == "med" and run["source_name"] == "MED_0004.pdf"
    s = run["summary"]
    assert s["n_claims"] == len(run["claims"]) > 0
    assert s["n_supported"] + s["n_discrepancy"] + s["n_insufficient_evidence"] == s["n_claims"]
    assert sum(run["stages"].values()) == s["n_claims"] and set(run["stages"]) == {"A", "B", "C"}
    for claim in run["claims"]:
        assert claim["evidence"]["evidence_id"] and set(claim["evidence"]["bbox"]) == {"x0", "y0", "x1", "y1"}
        assert claim["verification"]["label"] in ("SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE")
        assert claim["claim_text"]
    dose = next(c for c in run["claims"] if c["field_type"] == "med.dose")
    assert dose["claim_text"].endswith(" dose: " + dose["value"] + " " + dose["unit_raw"])  # V3 training format
    assert run["pages"] == [{"number": 1, "width": 612.0, "height": 792.0,
                             "image_url": f"/api/runs/{run['run_id']}/pages/1.png"}]
    assert run["fhir"]["valid"] is True and run["fhir"]["errors"] == []
    assert run["fhir"]["resource_counts"]["Patient"] == 1
    assert len(run["warnings"]) == 2  # heuristic Stage B + mock Stage C

    assert client.get(f"/api/runs/{run['run_id']}").json() == run
    png = client.get(run["pages"][0]["image_url"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content.startswith(PNG_MAGIC)
    assert client.get(f"/api/runs/{run['run_id']}/pages/2.png").status_code == 404
    bundle = client.get(run["downloads"]["fhir_bundle"])
    assert bundle.status_code == 200 and "attachment" in bundle.headers["content-disposition"]
    assert bundle.json()["id"] == run["fhir"]["bundle_id"]
    audit = client.get(run["downloads"]["audit_report"]).json()
    assert audit["document_id"] == "MED_0004" and audit["summary"] == run["summary"]


@pytest.mark.skipif(not HAS_LAB, reason="data/rendered not regenerated")
def test_process_lab_sample(client):
    run = client.post("/api/process", data={"sample_id": "LAB_0002"}).json()
    assert run["doc_type"] == "lab" and run["summary"]["n_claims"] > 0 and run["stages"]["A"] > 0


def test_process_input_validation(client):
    assert client.post("/api/process", data={}).status_code == 400
    both = _upload(client, "x.pdf", MED_PDF.read_bytes(), sample_id="MED_0004")
    assert both.status_code == 400 and "exactly one" in both.json()["detail"]
    assert client.post("/api/process", data={"sample_id": "MED_9999"}).status_code == 404
    assert client.post("/api/process", data={"sample_id": "MED_0004", "doc_type": "xray"}).status_code == 400


# ─── Uploads ──────────────────────────────────────────────────────────────────

def test_upload_pdf_gets_fresh_id(client, med_run):
    run = _upload(client, "my prescription.pdf", MED_PDF.read_bytes(), doc_type="med").json()
    assert run["document_id"].startswith("MED_") and run["document_id"] != "MED_0004"
    assert run["doc_type"] == "med" and run["source_name"] == "my prescription.pdf"
    assert run["summary"] == med_run["summary"]  # same PDF, same routing -> same verdicts
    assert client.get(run["pages"][0]["image_url"]).content.startswith(PNG_MAGIC)


def test_upload_auto_detects_doc_type(client):
    assert _upload(client, "scan.pdf", MED_PDF.read_bytes()).json()["doc_type"] == "med"
    assert _upload(client, "note.pdf", NARR_PDF.read_bytes()).json()["doc_type"] == "narrative"
    by_name = _upload(client, "NARRATIVE_copy.pdf", NARR_PDF.read_bytes()).json()
    assert by_name["doc_type"] == "narrative" and by_name["document_id"].startswith("NARRATIVE_")
    assert service.sniff_doc_type("Glucose 95.0 mg/dL 70 - 99\nSodium 138 mmol/L") == "lab"


def test_upload_document_text_json(client):
    from clindoc.ocr.ingest import ingest_document
    doc = ingest_document(MED_PDF, document_id="MED_0004")
    run = _upload(client, "doc.json", doc.to_json().encode(), doc_type="med").json()
    assert run["summary"]["n_claims"] > 0 and run["pages"][0]["width"] == 612.0
    png = client.get(run["pages"][0]["image_url"])
    assert png.status_code == 200 and png.content.startswith(PNG_MAGIC)
    bad = _upload(client, "doc.json", b'{"not": "a document"}')
    assert bad.status_code == 422


def test_upload_rejections(client, monkeypatch):
    assert _upload(client, "tool.exe", b"MZ\x90\x00").status_code == 415
    assert _upload(client, "fake.pdf", b"hello, not a pdf").status_code == 415
    assert _upload(client, "empty.pdf", b"").status_code == 400
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 1000)
    big = _upload(client, "big.pdf", b"%PDF-1.7\n" + b"0" * 5000)  # passes the header gate, fails the byte count
    assert big.status_code == 413 and "limit" in big.json()["detail"]
    huge = _upload(client, "huge.pdf", b"%PDF-1.7\n" + b"0" * (web_app.MULTIPART_OVERHEAD + 5000))  # header gate
    assert huge.status_code == 413 and "limit" in huge.json()["detail"]


def test_upload_needing_ocr_is_422(client):
    if (REPO / ".venv_ocr").exists():
        pytest.skip("PaddleOCR environment present")
    with pymupdf.open() as doc:  # one blank page, no text layer
        doc.new_page()
        blank = doc.tobytes()
    before = set(Path(tempfile.gettempdir()).glob("clindoc_run_*"))
    r = _upload(client, "scan.pdf", blank, doc_type="lab")
    assert r.status_code == 422 and "OCR" in r.json()["detail"]
    assert _upload(client, "photo.png", PNG_MAGIC + b"\x00" * 64, doc_type="lab").status_code == 422
    assert set(Path(tempfile.gettempdir()).glob("clindoc_run_*")) <= before  # failed runs leave no temp dir


def test_path_traversal_rejected(client):
    r = _upload(client, "../../../evil.pdf", MED_PDF.read_bytes(), doc_type="med")
    assert r.status_code == 200 and r.json()["source_name"] == "evil.pdf"
    assert not (REPO / "evil.pdf").exists()
    assert client.post("/api/process", data={"sample_id": "../../pyproject"}).status_code == 404
    for url in ("/api/documents/..%2F..%2Fpyproject/pages/1.png", "/api/documents/MED_0004.pdf/pages/1.png",
                "/api/documents/med_0004/pages/1.png", "/api/runs/..%2F..%2Fetc/pages/1.png",
                "/api/runs/%2e%2e/fhir_bundle.json", "/api/explorer/..%2F..%2Fpyproject.toml",
                "/..%2Fpyproject.toml", "/assets/..%2F..%2F..%2Fpyproject.toml"):
        assert client.get(url).status_code == 404, url


def test_unknown_and_evicted_runs(client, monkeypatch):
    assert client.get("/api/runs/not-a-uuid").status_code == 404
    assert client.get("/api/runs/00000000-0000-4000-8000-000000000000").status_code == 404
    monkeypatch.setattr(service, "MAX_RUNS", 1)
    first = client.post("/api/process", data={"sample_id": "MED_0001"}).json()
    run_dir = service.store.get(first["run_id"]).run_dir
    client.post("/api/process", data={"sample_id": "MED_0002"})
    assert client.get(f"/api/runs/{first['run_id']}").status_code == 404
    assert client.get(first["downloads"]["fhir_bundle"]).status_code == 404
    assert not run_dir.exists()


# ─── /api/verify ──────────────────────────────────────────────────────────────

def test_verify_tier1_claim(client):
    r = client.post("/api/verify", json={"field_type": "lab.value", "value": "95.0", "unit": "mg/dL",
                                         "entity": "Glucose", "evidence_text": "Glucose 95.0 mg/dL 70 - 99"})
    body = r.json()
    assert r.status_code == 200 and body["tier"] == 1 and body["claim_text"] == "Glucose: 95.0 mg/dL"
    assert body["final"]["label"] == "SUPPORTED" and body["final"]["stage_reached"] in ("B", "C")
    assert body["stage_a"]["label"] == "SUPPORTED"
    assert abs(sum(body["stage_b"]["probabilities"].values()) - 1.0) < 1e-6
    assert set(body["stage_b"]["probabilities"]) == {"SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE"}


def test_verify_tier1_discrepancy_escalates_or_resolves(client):
    body = client.post("/api/verify", json={
        "field_type": "med.dose", "value": "50", "unit": "mg", "entity": "Losartan",
        "evidence_text": "Order #1: Losartan 100 mg", "context_text": "Order #1: Losartan 100 mg\nRoute: oral"}).json()
    assert body["final"]["label"] == "DISCREPANCY" and body["final"]["contradiction_type"]
    if body["final"]["stage_reached"] == "C":
        assert body["stage_c"]["model_name"] == "mock-judge-v1" and body["stage_c"]["rationale"]


def test_verify_tier2_and_missing_evidence(client):
    t2 = client.post("/api/verify", json={"field_type": "lab.unit", "value": "mg/dL",
                                          "evidence_text": "Glucose 95.0 mg/dL 70 - 99"}).json()
    assert t2["tier"] == 2 and t2["final"]["stage_reached"] == "A" and t2["stage_b"] is None and t2["stage_c"] is None
    missing = client.post("/api/verify", json={"field_type": "med.name", "value": "Metformin", "evidence_text": ""}).json()
    assert missing["final"] == {"label": "INSUFFICIENT_EVIDENCE", "confidence": 1.0, "stage_reached": "A",
                                "contradiction_type": None, "rationale": None, "escalated": False}


def test_verify_validation(client):
    assert client.post("/api/verify", json={"field_type": "lab.bogus", "value": "1"}).status_code == 422
    blank = client.post("/api/verify", json={"field_type": "lab.value", "value": "   "})
    assert blank.status_code == 422 and isinstance(blank.json()["detail"], str)


def test_verify_presets_run(client):
    presets = client.get("/api/verify/presets").json()["presets"]
    assert 6 <= len(presets) <= 10
    assert {p["field_type"] for p in presets} >= {"lab.value", "lab.flag", "med.name", "med.dose", "med.route",
                                                  "med.frequency", "lab.unit"}
    assert {"SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE"} <= {p["expected_label"] for p in presets}
    for p in presets:
        body = {k: p[k] for k in ("field_type", "value", "unit", "entity", "evidence_text")}
        assert client.post("/api/verify", json=body).status_code == 200, p["name"]


# ─── Results / explorer / documents ───────────────────────────────────────────

def test_results_sections_and_headline(client):
    res = client.get("/api/results").json()
    for key in ("_schema", "headline", "leaderboard", "confusion_matrices", "per_field", "per_class", "risk_coverage",
                "escalation", "prompt_case_study", "dataset", "robustness", "extraction", "fhir", "sources"):
        assert key in res, key
    h = res["headline"]
    assert (h["cascade_accuracy"], h["cascade_correct"], h["cascade_macro_f1"], h["cascade_discrepancy_recall"]) == \
        (0.9748, 1780, 0.9538, 0.9826)
    assert (h["cascade_errors"], h["modernbert_errors"], h["qwen_errors"]) == (46, 82, 116)
    assert (h["error_reduction_vs_modernbert"], h["error_reduction_vs_qwen"]) == (0.439, 0.6034)
    assert (h["autonomous_coverage"], h["autonomous_accuracy"], h["confidence_only_accuracy_at_same_coverage"]) == \
        (0.7913, 0.9931, 0.9578)
    assert (h["runtime_cascade_seconds"], h["runtime_qwen_seconds"]) == (1598.59, 6203.75)
    assert (h["benchmark_documents"], h["benchmark_claims"]) == (120, 5629)
    ids = [row["id"] for row in res["leaderboard"]]
    assert ids == ["V1", "V2", "V3", "V4", "V5", "V6", "V8_V0", "V8_V1", "V9"]
    assert res["confusion_matrices"]["matrices"]["V6"] == [[671, 58, 0], [12, 966, 0], [4, 8, 107]]
    assert res["per_field"]["models"]["V8_V1"]["lab.flag"]["accuracy"] == 0.9148
    pcs = res["prompt_case_study"]
    assert (pcs["v0"]["lab_flag_stage_c"]["correct"], pcs["v1"]["lab_flag_stage_c"]["correct"]) == (191, 220)
    assert res["fhir"]["coding"]["remediated"] == {"loinc": 603, "observations": 603, "rxnorm": 94,
                                                   "medications": 94, "ucum": 697, "quantities": 697}
    assert all((REPO / p).exists() for paths in res["sources"].values() for p in paths)


def test_explorer_filters_and_pagination(client):
    everything = client.get("/api/explorer").json()
    assert everything["total"] == 1826 and everything["errors"] == 46 and everything["escalated"] == 381
    assert everything["accuracy"] == 0.9748 and len(everything["items"]) == 50
    assert len(everything["facets"]["documents"]) == 34 and "lab.flag" in everything["facets"]["field_types"]
    assert client.get("/api/explorer", params={"correct": "errors"}).json()["total"] == 46
    assert client.get("/api/explorer", params={"stage": "C"}).json()["total"] == 381
    assert client.get("/api/explorer", params={"field_type": "lab.flag"}).json()["total"] == 317
    doc = client.get("/api/explorer", params={"document_id": "MED_0004", "limit": 200}).json()
    assert doc["total"] > 0 and all(i["document_id"] == "MED_0004" and i["doc_family"] == "med" for i in doc["items"])
    p1 = client.get("/api/explorer", params={"limit": 10, "offset": 0}).json()["items"]
    p2 = client.get("/api/explorer", params={"limit": 10, "offset": 10}).json()["items"]
    assert len(p1) == len(p2) == 10 and not {i["example_id"] for i in p1} & {i["example_id"] for i in p2}
    hits = client.get("/api/explorer", params={"q": "metformin"}).json()
    assert all("metformin" in i["claim"].lower() for i in hits["items"])
    assert client.get("/api/explorer", params={"correct": "maybe"}).status_code == 422
    assert client.get("/api/explorer", params={"limit": 0}).status_code == 422


def test_explorer_detail(client):
    item = client.get("/api/explorer", params={"correct": "errors", "stage": "C", "limit": 1}).json()["items"][0]
    d = client.get(f"/api/explorer/{item['example_id']}").json()
    assert d["claim"] == item["claim"] and d["ground_truth"] == item["ground_truth"] and d["evidence"]
    assert len(d["bbox"]) == 4 and d["page_number"] == 1
    assert set(d["stage_b"]["calibrated_probabilities"]) == {"SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE"}
    assert d["stage_c"]["rationale"] and d["stage_c"]["prompt_version"]
    assert set(d["qwen_only"]) == {"label", "rationale", "correct"}
    if (REPO / "data" / "rendered" / f"{d['document_id']}.pdf").exists() or not d["document_id"].startswith("LAB_"):
        assert d["page"]["image_url"] == f"/api/documents/{d['document_id']}/pages/1.png"
    assert client.get("/api/explorer/not-an-example").status_code == 404


def test_document_pages(client):
    r = client.get("/api/documents/MED_0004/pages/1.png")
    assert r.status_code == 200 and r.content.startswith(PNG_MAGIC) and "max-age" in r.headers["cache-control"]
    assert client.get("/api/documents/MED_0004/pages/2.png").status_code == 404
    assert client.get("/api/documents/MED_9999/pages/1.png").status_code == 404
    assert client.get("/api/documents/FOO_0001/pages/1.png").status_code == 404


# ─── Model / decisions / static ───────────────────────────────────────────────

def test_model_runs_and_progress(client, monkeypatch, tmp_path):
    runs, progress = tmp_path / "runs", tmp_path / "progress"
    runs.mkdir(), progress.mkdir()
    monkeypatch.setattr(web_app, "RUNS_DIR", runs)
    monkeypatch.setattr(web_app, "PROGRESS_DIR", progress)
    body = client.get("/api/model").json()
    assert body["runs"] == [] and body["active"] is None and body["serving"]["mode"] == "heuristic"
    assert body["reference"]["temperature"] == 1.146522 and len(body["reference"]["history"]) == 5

    (runs / "old.json").write_text(json.dumps({"run_name": "old", "created_at": "2026-10-01T00:00:00+00:00"}))
    (runs / "new.json").write_text(json.dumps({"run_name": "new", "created_at": "2026-10-03T00:00:00+00:00",
                                               "validation": None}))
    (runs / "partial.json").write_text(json.dumps({"run_name": "partial"}))  # missing keys tolerated
    (runs / "broken.json").write_text("{not json")
    assert [c["run_name"] for c in client.get("/api/model").json()["runs"]] == ["new", "old", "partial"]

    now = datetime.now(timezone.utc)
    (progress / "stale_progress.json").write_text(json.dumps(
        {"run_name": "stale", "status": "running", "updated_at": (now - timedelta(hours=1)).isoformat()}))
    assert client.get("/api/model").json()["active"] is None
    (progress / "live_progress.json").write_text(json.dumps(
        {"run_name": "live", "status": "running", "epoch": 1, "updated_at": now.isoformat()}))
    assert client.get("/api/model").json()["active"]["run_name"] == "live"


def test_decisions(client):
    decisions = client.get("/api/decisions").json()["decisions"]
    assert [d["id"] for d in decisions] == [f"DEC-{i:03d}" for i in range(1, 20)]
    assert all(d["title"] and d["status"] and d["decision"] and d["path"].startswith("docs/decisions/")
               for d in decisions)


def test_static_index(client):
    if not (web_app.STATIC_DIR / "index.html").is_file():
        pytest.skip("static site not built yet")
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache"


def test_api_errors_are_json(client):
    r = client.get("/api/explorer/" + "x" * 10)
    assert r.status_code == 404 and r.json() == {"detail": "Unknown example"}
