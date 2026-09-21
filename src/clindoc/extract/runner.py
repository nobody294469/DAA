"""Batch extraction runner for clinical laboratory reports."""

import argparse
import json
from pathlib import Path
import time

from clindoc.extract.lab_parser import extract_lab_report
from clindoc.ocr.schema import DocumentOCRRecord
from clindoc.schema.lab import ExtractedLabReport


def extract_cached_document(
    ocr_json_path: str | Path,
    output_dir: str | Path = "data/extracted",
) -> ExtractedLabReport:
    """Extract a single document from its cached OCR JSON and write to data/extracted/."""
    ocr_json_path = Path(ocr_json_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(ocr_json_path, "r", encoding="utf-8") as f:
        ocr_data = json.load(f)

    ocr_record = DocumentOCRRecord.model_validate(ocr_data)
    extracted_report = extract_lab_report(ocr_record)

    out_file = output_dir / f"{extracted_report.doc_id}.json"
    out_file.write_text(extracted_report.model_dump_json(indent=2), encoding="utf-8")

    return extracted_report


def batch_extract_all(
    ocr_cache_dir: str | Path = "data/ocr_cache",
    output_dir: str | Path = "data/extracted",
) -> list[ExtractedLabReport]:
    """Run extraction across all documents in ocr_cache_dir."""
    ocr_cache_dir = Path(ocr_cache_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(ocr_cache_dir.glob("LAB_*.json"))
    reports = []

    for jf in json_files:
        rep = extract_cached_document(jf, output_dir=output_dir)
        reports.append(rep)

    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract structured lab reports from OCR cache")
    parser.add_argument("--cache-dir", default="data/ocr_cache", help="Path to cached OCR JSON files")
    parser.add_argument("--output-dir", default="data/extracted", help="Output directory for extracted reports")
    args = parser.parse_args()

    t0 = time.perf_counter()
    extracted = batch_extract_all(args.cache_dir, args.output_dir)
    elapsed = time.perf_counter() - t0

    print(f"Successfully extracted {len(extracted)} documents in {elapsed:.3f} s ({elapsed*1000/max(len(extracted),1):.2f} ms/doc).")
