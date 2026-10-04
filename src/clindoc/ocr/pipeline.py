"""Unified document ingestion, OCR, and text extraction pipeline."""

import json
from pathlib import Path
import subprocess
import time

from clindoc.ocr.born_digital import extract_born_digital_pdf
from clindoc.ocr.schema import DocumentOCRRecord


def _run_isolated_paddleocr(
    input_path: Path,
    output_cache_path: Path,
    venv_ocr_python: Path | None = None,
    dpi: int = 150,
) -> DocumentOCRRecord:
    """Execute PaddleOCR worker inside .venv_ocr and return parsed DocumentOCRRecord."""
    if venv_ocr_python is None:
        venv_ocr_python = Path(__file__).resolve().parents[3] / ".venv_ocr" / "Scripts" / "python.exe"

    worker_script = Path(__file__).resolve().parent / "paddle_worker.py"

    if not venv_ocr_python.exists():
        raise FileNotFoundError(f"Isolated PaddleOCR python environment not found at: {venv_ocr_python}")

    cmd = [
        str(venv_ocr_python),
        str(worker_script),
        "--input", str(input_path),
        "--output", str(output_cache_path),
        "--dpi", str(dpi),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"PaddleOCR worker failed (exit {result.returncode}):\n{result.stderr}")

    with open(output_cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return DocumentOCRRecord.model_validate(data)


def process_document(
    input_path: str | Path,
    output_dir: str | Path = "data/ocr_cache",
    engine: str = "auto",
    use_born_digital: bool = True,
    force: bool = False,
    dpi: int = 150,
) -> DocumentOCRRecord:
    """Process a single document (PDF or image) and save cached DocumentOCRRecord JSON."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    doc_id = input_path.stem
    cache_file = output_dir / f"{doc_id}.json"

    # Return cached if valid and not forcing
    if cache_file.exists() and not force:
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return DocumentOCRRecord.model_validate(data)
        except Exception:
            pass  # Recompute if cache is corrupted

    # Determine engine
    resolved_engine = engine
    if engine == "auto":
        resolved_engine = "pymupdf_digital" if (use_born_digital and input_path.suffix.lower() == ".pdf") else "paddleocr"

    if resolved_engine == "pymupdf_digital":
        record = extract_born_digital_pdf(input_path, doc_id=doc_id)
        cache_file.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return record
    elif resolved_engine == "paddleocr":
        return _run_isolated_paddleocr(input_path, cache_file, dpi=dpi)
    else:
        raise ValueError(f"Unknown OCR/extraction engine: {resolved_engine}")


def batch_process_dataset(
    input_dir: str | Path = "data/rendered",
    output_dir: str | Path = "data/ocr_cache",
    engine: str = "auto",
    use_born_digital: bool = True,
    force: bool = False,
) -> list[DocumentOCRRecord]:
    """Process all PDF files in input directory and cache results."""
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(input_dir.glob("*.pdf"))
    records = []

    for pdf_file in pdf_files:
        rec = process_document(
            input_path=pdf_file,
            output_dir=output_dir,
            engine=engine,
            use_born_digital=use_born_digital,
            force=force,
        )
        records.append(rec)

    return records


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest and OCR clinical documents")
    parser.add_argument("--input", default="data/rendered", help="Input file or directory")
    parser.add_argument("--output", default="data/ocr_cache", help="Output directory for cached JSON")
    parser.add_argument("--engine", default="auto", choices=["auto", "pymupdf_digital", "paddleocr"])
    parser.add_argument("--force", action="store_true", help="Force recomputation ignoring cache")
    args = parser.parse_args()

    in_p = Path(args.input)
    if in_p.is_file():
        rec = process_document(in_p, args.output, engine=args.engine, force=args.force)
        print(f"Processed {rec.doc_id}: {len(rec.lines)} lines, {len(rec.words)} words.")
    else:
        records = batch_process_dataset(in_p, args.output, engine=args.engine, force=args.force)
        print(f"Batch processed {len(records)} documents.")
