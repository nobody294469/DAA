"""Start `clindoc serve` fully detached (no console window), so closing a terminal cannot kill it.

Usage: python run_server.py   ->  http://127.0.0.1:8000   (log: server.log, stop: python run_server.py stop)
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIDFILE = ROOT / "server.pid"
if len(sys.argv) > 1 and sys.argv[1] == "stop":
    if PIDFILE.exists():
        subprocess.run(["taskkill", "/PID", PIDFILE.read_text().strip(), "/T", "/F"], check=False)
        PIDFILE.unlink()
    sys.exit(0)

env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
args = [sys.executable, "-m", "clindoc.cli", "serve", "--port", "8000", "--device", "cpu",
        "--checkpoint", "data/m2_results_v3/checkpoints/modernbert_text_only_v3_openmix_fold0_seed42.pt",
        "--llm", "data/judge_lora/Qwen2.5-1.5B-Instruct-v3"]
DETACHED, NEW_GROUP, NO_WINDOW = 0x00000008, 0x00000200, 0x08000000
p = subprocess.Popen(args, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=open(ROOT / "server.log", "w"),
                     stderr=subprocess.STDOUT, creationflags=DETACHED | NEW_GROUP | NO_WINDOW, close_fds=True)
PIDFILE.write_text(str(p.pid))
print("server started, pid", p.pid)
