# Clindoc2FHIR: working rules
- Always use the project's `.venv` (`.venv/Scripts/python.exe`). The global Python's torch is not supported.
- Start/stop the website only with `python run_server.py` (`stop`, `restart`, `status`); it opens http://127.0.0.1:8000.
- Never read, print or commit `.env` (it holds the optional online-model key). Never put keys in code or chat.
- Tests: `python -m pytest -q`. 7 tests need data files that are not in the repo; the rest must pass.
- Work on branch `feature/web-platform`; do not push to `master`.
