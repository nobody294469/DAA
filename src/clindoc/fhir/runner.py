"""Batch execution script converting extracted reports into validated FHIR R4 bundles."""

from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

from clindoc.fhir.mapper import lab_report_to_fhir_bundle
from clindoc.fhir.validator import FHIRValidator
from clindoc.ocr.schema import DocumentOCRRecord
from clindoc.schema.lab import ExtractedLabReport
from clindoc.verify.verifier import verify_report


def run_fhir_conversion(
    extracted_dir: Path = Path("data/extracted"),
    ocr_dir: Path = Path("data/ocr_cache"),
    output_dir: Path = Path("data/fhir"),
) -> dict[str, Any]:
    """Convert all extracted laboratory reports into FHIR R4 bundles, validate them, and save them.

    Args:
        extracted_dir: Directory containing ExtractedLabReport JSONs.
        ocr_dir: Directory containing DocumentOCRRecord JSONs.
        output_dir: Destination directory for FHIR bundles and validation report.

    Returns:
        Aggregated summary dictionary.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    extracted_files = sorted(list(extracted_dir.glob("LAB_*.json")))
    if not extracted_files:
        raise FileNotFoundError(f"No extracted JSON files found in {extracted_dir}")

    start_time = time.time()
    documents_converted = 0
    validation_failures = 0
    failure_details: list[dict[str, Any]] = []

    resource_totals: dict[str, int] = {}
    verification_state_totals: dict[str, int] = {
        "VERIFIED": 0,
        "DISCREPANCY": 0,
        "MISSING_SOURCE_EVIDENCE": 0,
        "UNCHECKED": 0,
    }
    per_doc_summaries: list[dict[str, Any]] = []

    for ext_path in extracted_files:
        doc_id = ext_path.stem
        ocr_path = ocr_dir / f"{doc_id}.json"

        # Load extracted report
        with open(ext_path, "r", encoding="utf-8") as f:
            report_dict = json.load(f)
        report = ExtractedLabReport.model_validate(report_dict)

        # Load OCR record if available to run verification
        ver_result = None
        if ocr_path.exists():
            with open(ocr_path, "r", encoding="utf-8") as f:
                ocr_dict = json.load(f)
            ocr_record = DocumentOCRRecord.model_validate(ocr_dict)
            ver_result = verify_report(report, ocr_record)

        # Convert to FHIR R4 Bundle
        bundle = lab_report_to_fhir_bundle(report, ver_result)

        # Validate Bundle against fhir.resources schema & integrity rules
        val_res = FHIRValidator.validate_bundle(bundle)

        if not val_res.is_valid:
            validation_failures += 1
            failure_details.append({
                "doc_id": doc_id,
                "errors": val_res.errors,
            })

        # Save FHIR Bundle JSON
        out_file = output_dir / f"{doc_id}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(bundle.json(indent=2))

        # Accumulate metrics
        documents_converted += 1
        for r_type, count in val_res.resource_counts.items():
            resource_totals[r_type] = resource_totals.get(r_type, 0) + count

        for state, count in val_res.verification_status_counts.items():
            verification_state_totals[state] = verification_state_totals.get(state, 0) + count

        per_doc_summaries.append({
            "doc_id": doc_id,
            "is_valid": val_res.is_valid,
            "resource_counts": val_res.resource_counts,
            "verification_status_counts": val_res.verification_status_counts,
            "file_size_bytes": out_file.stat().st_size,
        })

    elapsed_time = time.time() - start_time
    avg_time_ms = (elapsed_time / documents_converted) * 1000.0 if documents_converted else 0.0

    summary = {
        "timestamp": datetime.now().isoformat(),
        "fhir_package_version": "fhir.resources 7.1.0 (sub-package: fhir.resources.R4B, FHIR release 4.3.0)",
        "documents_processed": len(extracted_files),
        "documents_converted": documents_converted,
        "validation_failures": validation_failures,
        "failure_details": failure_details,
        "resources_generated": resource_totals,
        "total_resources": sum(resource_totals.values()),
        "observations_represented": resource_totals.get("Observation", 0),
        "verification_states_represented": verification_state_totals,
        "elapsed_seconds": round(elapsed_time, 3),
        "avg_ms_per_document": round(avg_time_ms, 2),
        "per_document_summary": per_doc_summaries,
    }

    # Save validation report
    report_file = output_dir / "validation_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    summary = run_fhir_conversion()
    print("=" * 60)
    print("FHIR R4 CONVERSION & VALIDATION COMPLETE")
    print(f"Documents converted: {summary['documents_converted']} / {summary['documents_processed']}")
    print(f"Validation failures: {summary['validation_failures']}")
    print(f"Total FHIR resources generated: {summary['total_resources']}")
    print(f"Resource breakdown: {summary['resources_generated']}")
    print(f"Verification states: {summary['verification_states_represented']}")
    print(f"Total time: {summary['elapsed_seconds']} s ({summary['avg_ms_per_document']} ms/doc)")
    print(f"Validation report saved: data/fhir/validation_report.json")
    print("=" * 60)
