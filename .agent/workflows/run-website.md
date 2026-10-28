---
description: Start the website locally and check it is healthy
---
1. Run `python run_server.py` (waits for the models, then opens http://127.0.0.1:8000).
2. Run `python run_server.py status` and confirm `"status": "ok"`.
3. If it did not start, read the last lines of `server.log`.
