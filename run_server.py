"""Start `clindoc serve` fully detached (no console window), wait until it is ready, and open the site.

Usage (any Python works: it re-launches itself with the project's .venv):
  python run_server.py            start, wait for the models to load, open http://127.0.0.1:8000
  python run_server.py stop       stop it
  python run_server.py restart    stop, then start
  python run_server.py status     print the health summary
Log: server.log. Settings (online model key etc.): .env
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
PIDFILE = ROOT / "server.pid"
PORTFILE = ROOT / "server.port"
import socket  # noqa: E402
cmd = sys.argv[1] if len(sys.argv) > 1 else "start"

# The global Python on this machine can have a broken torch; the project's own venv is the supported one.
if VENV_PY.exists() and Path(sys.executable).resolve() != VENV_PY.resolve():
    sys.exit(subprocess.call([str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]]))


def url():
    return f"http://127.0.0.1:{PORTFILE.read_text().strip() if PORTFILE.exists() else 8000}"


def health():
    """Our server's health, or None. Another app on the port (no stage_b in its answer) does not count."""
    try:
        h = json.load(urllib.request.urlopen(f"{url()}/api/health", timeout=3))
        return h if "stage_b" in h else None
    except Exception:
        return None


def free_port():
    for port in range(8000, 8020):  # 8000 first; skip ports other programs already hold
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    sys.exit("no free port between 8000 and 8019")


def stop():
    if PIDFILE.exists():
        subprocess.run(["taskkill", "/PID", PIDFILE.read_text().strip(), "/T", "/F"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PIDFILE.unlink()


def start():
    if health():
        print("already running:", url())
        return
    port = free_port()
    PORTFILE.write_text(str(port))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    args = [sys.executable, "-m", "clindoc.cli", "serve", "--port", str(port), "--device", "cpu",
            "--checkpoint", "data/m2_results_v3/checkpoints/modernbert_text_only_v3_openmix_fold0_seed42.pt",
            "--llm", "data/judge_lora/Qwen2.5-1.5B-Instruct-v3"]
    detached, new_group, no_window = 0x00000008, 0x00000200, 0x08000000
    p = subprocess.Popen(args, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=open(ROOT / "server.log", "w"),
                         stderr=subprocess.STDOUT, creationflags=detached | new_group | no_window, close_fds=True)
    PIDFILE.write_text(str(p.pid))
    print("starting (loading the models takes about a minute)...", flush=True)
    for _ in range(90):
        time.sleep(2)
        if health():
            print("ready:", url())
            webbrowser.open(url())
            return
        if p.poll() is not None:
            sys.exit(f"server exited early, see {ROOT / 'server.log'}")
    sys.exit(f"not ready after 3 minutes, see {ROOT / 'server.log'}")


if cmd == "stop":
    stop()
elif cmd == "restart":
    stop()
    start()
elif cmd == "status":
    h = health()
    print("not running" if not h else json.dumps({k: h[k] for k in ("status", "stage_b", "stage_c", "online") if k in h}, indent=1))
else:
    start()
