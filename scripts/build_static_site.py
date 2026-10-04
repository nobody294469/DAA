"""Build a static, host-anywhere snapshot of the website (Firebase Hosting, Vercel, GitHub Pages...).

Authority: src/clindoc/web/app.py (HTTP API) and src/clindoc/web/static/assets/js/core.js (static mode).
Runs the real FastAPI app in-process with the trained Stage B checkpoint and records:
GET endpoints, the whole frozen explorer, pipeline runs for a few samples, and every playground preset.
Pages then read /static-api/* instead of /api/* (window.CLINDOC_STATIC). Uploads and custom inputs stay
local-only because inference needs PyTorch + a 600 MB model.

Usage (repo root):  $env:PYTHONPATH="src"; python scripts/build_static_site.py --checkpoint <file.pt> [--out dist]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "src" / "clindoc" / "web" / "static"
SAMPLES = ["LAB_0002", "LAB_0005", "LAB_0044", "MED_0004", "MED_0008", "MED_0040",
           "NARRATIVE_0016", "NARRATIVE_0018"]
VERIFY_KEYS = ("field_type", "value", "unit", "entity", "evidence_text", "context_text")
# Field flags from FIELDS in assets/js/playground.js (which inputs the playground sends).
USES_UNIT = {"lab.value", "med.dose"}
USES_ENTITY = {"lab.value", "lab.flag", "med.dose", "med.route", "med.frequency"}
MARKER ="<script>window.CLINDOC_STATIC=true</script>"


def verify_key(req: dict) -> str:
    """Mirror of verifyKey() in core.js: first 8 bytes of SHA-256 over the newline-joined fields."""
    s = "\n".join(str(req.get(k) if req.get(k) is not None else "") for k in VERIFY_KEYS)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--checkpoint", required=True, help="trained Stage B .pt (recorded verdicts come from it)")
    ap.add_argument("--out", type=Path, default=REPO / "dist")
    args = ap.parse_args()

    os.environ["CLINDOC_STAGE_B_CHECKPOINT"] = str(Path(args.checkpoint).resolve())
    os.environ.setdefault("CLINDOC_DEVICE", "cpu")
    os.chdir(REPO)  # pipeline reads data/, configs/, schemas/ relative to the repo root
    from fastapi.testclient import TestClient
    from clindoc.web.app import create_app

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC, out)
    for page in out.glob("*.html"):  # switch every page into static mode
        html = page.read_text(encoding="utf-8")
        page.write_text(html.replace("<head>", "<head>\n  " + MARKER, 1), encoding="utf-8")
    api_root = out / "static-api"

    def save(rel: str, data) -> None:
        dest = api_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            dest.write_bytes(data)
        else:  # asset URLs inside JSON must point at the snapshot tree too
            dest.write_text(json.dumps(data, ensure_ascii=False).replace('"/api/', '"/static-api/'), encoding="utf-8")

    with TestClient(create_app()) as c:
        def get(path: str):
            r = c.get(path)
            r.raise_for_status()
            return r.json() if "json" in r.headers.get("content-type", "") else r.content

        health = get("/api/health")
        assert health["stage_b"]["mode"] == "modernbert", f"checkpoint did not load: {health['stage_b']}"
        save("health.json", health)
        for name in ("results", "model", "decisions", "verify/presets"):
            save(f"{name}.json", get(f"/api/{name}"))

        # Runs: real pipeline output for a few samples, with page images and downloads.
        samples = [s for s in get("/api/samples")["samples"] if s["id"] in SAMPLES]
        save("samples.json", {"samples": samples})
        for s in samples:
            r = c.post("/api/process", data={"sample_id": s["id"], "doc_type": "auto"})
            r.raise_for_status()
            run = r.json()
            save(f"runs/by-sample/{s['id']}.json", run)
            save(f"runs/{run['run_id']}.json", run)
            for p in run["pages"]:
                save(p["image_url"].removeprefix("/api/"), get(p["image_url"]))
            for url in run["downloads"].values():
                save(url.removeprefix("/api/"), get(url))
            print(f"run {s['id']}: {len(run['claims'])} claims, stages {run['stages']}")

        # Playground presets, keyed exactly as core.js keys a request.
        for p in get("/api/verify/presets")["presets"]:
            # exactly what playground.js request() sends after fill(preset)
            ft = p["field_type"]
            req = {"field_type": ft, "value": str(p["value"]).strip(),
                   "unit": (p.get("unit") or "").strip() or None if ft in USES_UNIT else None,
                   "entity": (p.get("entity") or "").strip() or None if ft in USES_ENTITY else None,
                   "evidence_text": p["evidence_text"], "context_text": None}
            r = c.post("/api/verify", json={k: v for k, v in req.items() if v is not None})
            r.raise_for_status()
            save(f"verify/{verify_key(req)}.json", r.json())

        # Explorer: full index (filtered in the browser) + every detail record + referenced page images.
        items, offset = [], 0
        while True:
            page = get(f"/api/explorer?limit=200&offset={offset}")
            items += page["items"]
            offset += 200
            if offset >= page["total"]:
                break
        save("explorer/index.json", items)
        images = set()
        for it in items:
            d = get(f"/api/explorer/{it['example_id']}")
            save(f"explorer/{it['example_id']}.json", d)
            if d.get("page") and d["page"].get("image_url"):
                images.add(d["page"]["image_url"])
        for url in images:
            save(url.removeprefix("/api/"), get(url))
        print(f"explorer: {len(items)} claims, {len(images)} page images")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"static site -> {out} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
