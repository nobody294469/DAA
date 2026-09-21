"""Medication regimen extraction runner."""

import argparse
import json
from pathlib import Path
import time

from clindoc.extract.med_parser import MedicationRegimenParser
from clindoc.ocr.schema import DocumentOCRRecord
from clindoc.schema.medication import ExtractedPrescriptionDocument

DEFAULT_OCR_DIR = Path("data/med_ocr_cache")
DEFAULT_OUTPUT_DIR = Path("data/med_extracted")


def run_medication_extraction(
    ocr_dir: Path = DEFAULT_OCR_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    parser: MedicationRegimenParser | None = None,
) -> list[ExtractedPrescriptionDocument]:
    """Run medication extraction across all OCR records in ocr_dir, saving results to output_dir."""
    ocr_files = sorted(ocr_dir.glob("*.json"))
    if not ocr_files:
        print(f"No OCR files found in {ocr_dir}")
        return []

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {len(ocr_files)} OCR records from {ocr_dir}...")
    records: list[DocumentOCRRecord] = []
    for f in ocr_files:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            records.append(DocumentOCRRecord.model_validate(data))

    if parser is None:
        parser = MedicationRegimenParser()

    print(f"Executing batch GLiNER-BioMed + rule parsing on {len(records)} documents...")
    t0 = time.time()
    extracted_docs = parser.parse_documents(records)
    total_time = time.time() - t0

    total_meds = sum(len(d.medications) for d in extracted_docs)
    print(
        f"Extracted {total_meds} medications across {len(extracted_docs)} documents in {total_time:.2f}s "
        f"({total_time / len(extracted_docs):.3f}s / doc)"
    )

    for doc in extracted_docs:
        out_file = output_dir / f"{doc.doc_id}.json"
        with open(out_file, "w", encoding="utf-8") as fp:
            json.dump(doc.model_dump(), fp, indent=2, default=str)

    print(f"Saved {len(extracted_docs)} extracted records to {output_dir}")
    return extracted_docs


def main():
    parser = argparse.ArgumentParser(description="Medication Regimen Extraction Runner")
    parser.add_argument("--ocr-dir", type=str, default=str(DEFAULT_OCR_DIR), help="Input OCR directory")
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR), help="Output extracted directory")
    args = parser.parse_args()

    run_medication_extraction(
        ocr_dir=Path(args.ocr_dir),
        output_dir=Path(args.output_dir),
    )


if __name__ == "__main__":
    main()
