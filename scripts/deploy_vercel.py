"""Build the hosted site and deploy it to the clindoc2fhir Vercel project, the same way every time.

Static pages (scripts/build_static_site.py) + deploy/vercel (solve + sign-in functions) + the shared extractor.
Usage (repo root):  python scripts/deploy_vercel.py            build and deploy to production
                    python scripts/deploy_vercel.py --no-deploy  build only (prints the folder)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECKPOINT = "data/m2_results_v3/checkpoints/modernbert_text_only_v3_openmix_fold0_seed42.pt"
# Not secrets: they only say which Vercel project receives the upload (a wrong link silently creates a new project).
PROJECT = {"projectId": "prj_TJz5zDd2nHuMz1vys35IP97ntMkb", "orgId": "team_xRzMu99GBoeZQTZalYKbcsjp", "projectName": "clindoc2fhir"}
AUTHBAR = '<script type="module" src="/assets/js/authbar.js"></script></body>'


def build(out: Path) -> None:
    py = REPO / ".venv" / "Scripts" / "python.exe"
    subprocess.run([str(py if py.exists() else sys.executable), "scripts/build_static_site.py", "--checkpoint", CHECKPOINT,
                    "--out", str(out)], cwd=REPO, check=True, env={**__import__("os").environ, "PYTHONPATH": str(REPO / "src")})
    shutil.copytree(REPO / "deploy" / "vercel", out, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    lib = out / "api" / "_lib"
    (lib / "data").mkdir(parents=True, exist_ok=True)
    extract = REPO / "src" / "clindoc" / "extract"
    shutil.copy(extract / "text_provider.py", lib)
    shutil.copy(extract / "data" / "drug_lexicon.txt", lib / "data")
    for page in out.glob("*.html"):  # signed-in tag on every page except the login page itself
        html = page.read_text(encoding="utf-8")
        if page.name != "login.html" and "authbar.js" not in html:
            page.write_text(html.replace("</body>", AUTHBAR, 1), encoding="utf-8")
    assert not list(out.rglob(".env*")), "refusing to upload env files"
    (out / ".vercel").mkdir(exist_ok=True)
    (out / ".vercel" / "project.json").write_text(json.dumps(PROJECT))


if __name__ == "__main__":
    out = Path(tempfile.mkdtemp(prefix="clindoc_site_"))
    build(out)
    print("built:", out)
    if "--no-deploy" not in sys.argv:
        subprocess.run("npx --yes vercel deploy --prod --yes", cwd=out, shell=True, check=True)
        print("live: https://clindoc2fhir.vercel.app")
