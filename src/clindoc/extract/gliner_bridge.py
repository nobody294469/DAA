"""Bridge to execute GLiNER-BioMed NER inside the dedicated .venv_ner environment."""

import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any


class GLiNERBioMedBridge:
    """Bridge for running GLiNER-BioMed entity extraction via subprocess in .venv_ner."""

    def __init__(
        self,
        venv_ner_python: Path | None = None,
        worker_script: Path | None = None,
    ):
        if venv_ner_python is None:
            # Default location of dedicated .venv_ner
            venv_ner_python = Path(__file__).resolve().parents[3] / ".venv_ner" / "Scripts" / "python.exe"
        if worker_script is None:
            worker_script = Path(__file__).resolve().parent / "gliner_worker.py"

        self.venv_ner_python = venv_ner_python
        self.worker_script = worker_script

        if not self.venv_ner_python.exists():
            raise FileNotFoundError(
                f"Dedicated GLiNER environment not found at: {self.venv_ner_python}. "
                "Ensure .venv_ner is created and gliner is installed."
            )
        if not self.worker_script.exists():
            raise FileNotFoundError(f"GLiNER worker script not found at: {self.worker_script}")

    def predict_batch(self, texts: list[str]) -> list[list[dict[str, Any]]]:
        """Run GLiNER entity prediction for a batch of strings.

        Args:
            texts: List of strings (e.g. prescription lines or sig blocks).

        Returns:
            List of entity span lists, matching input texts order.
        """
        if not texts:
            return []

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            in_file = tmp_path / "gliner_in.json"
            out_file = tmp_path / "gliner_out.json"

            with open(in_file, "w", encoding="utf-8") as f:
                json.dump({"texts": texts}, f)

            cmd = [
                str(self.venv_ner_python),
                str(self.worker_script),
                "--input", str(in_file),
                "--output", str(out_file),
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                raise RuntimeError(
                    f"GLiNER worker failed with exit code {proc.returncode}:\n{proc.stderr}"
                )

            if not out_file.exists():
                raise FileNotFoundError(f"GLiNER worker output not produced at: {out_file}")

            with open(out_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            return data.get("results", [])

    def predict_single(self, text: str) -> list[dict[str, Any]]:
        """Run entity prediction for a single string."""
        results = self.predict_batch([text])
        return results[0] if results else []
