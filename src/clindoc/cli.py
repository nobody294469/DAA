"""Unified CLI and end-to-end clinical document verification orchestrator.

Authority:
  - PROJECT_SPEC.md §5, §6 (Data Flow: Document → Audit Report), §7
  - DEC-001 (Region Classification), DEC-002 (Terminology Threshold), DEC-008 (Medication Mapping),
    DEC-016 (Two-tier FHIR Provenance), DEC-019 (Field Verification Tiering)

Pipeline:
  Input document (PDF / image / DocumentText JSON)
  → Ingestion & OCR (§7.1)
  → ExtractionRouter (§11)
  → Terminology Normalization (§14)
  → Verification Cascade (§12)
  → FHIR R4B Bundle Generation (§15, §16)
  → Audit Report Generation (§7.8)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Optional, Sequence, Union

from fhir.resources.R4B.bundle import Bundle

from clindoc.audit import AuditReport, generate_audit_report
from clindoc.extract.contracts import Claim, Evidence
from clindoc.extract.router import ExtractionRouter
from clindoc.fhir.mapper import claims_to_fhir_bundle
from clindoc.normalize.normalizer import TerminologyNormalizer
from clindoc.ocr.document_text import DocumentText
from clindoc.ocr.ingest import (
    EmptyDocumentError,
    IngestionError,
    SUPPORTED_EXTENSIONS,
    UnreadableDocumentError,
    UnsupportedFileTypeError,
    ingest_document,
)
from clindoc.schema.verification import VerificationResult
from clindoc.verify.cascade import VerificationCascade


@dataclass
class ProcessResult:
    """Encapsulates output and summary metrics of an end-to-end document conversion."""
    document_id: str
    fhir_bundle: Bundle
    audit_report: AuditReport
    fhir_bundle_path: Path
    audit_report_path: Path
    n_claims: int
    n_supported: int
    n_discrepancy: int
    n_insufficient_evidence: int
    n_escalated_to_llm: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "fhir_bundle_id": self.fhir_bundle.id,
            "fhir_bundle_path": str(self.fhir_bundle_path),
            "audit_report_path": str(self.audit_report_path),
            "summary": {
                "n_claims": self.n_claims,
                "n_supported": self.n_supported,
                "n_discrepancy": self.n_discrepancy,
                "n_insufficient_evidence": self.n_insufficient_evidence,
                "n_escalated_to_llm": self.n_escalated_to_llm,
            },
        }


def process_document(
    input_file: Union[Path, str],
    output_dir: Union[Path, str],
    document_id: Optional[str] = None,
    router: Optional[ExtractionRouter] = None,
    normalizer: Optional[TerminologyNormalizer] = None,
    cascade: Optional[VerificationCascade] = None,
) -> ProcessResult:
    """Execute the canonical end-to-end verification and FHIR conversion pipeline for one document.

    Args:
        input_file: Path to source PDF, image, or pre-extracted DocumentText JSON.
        output_dir: Directory where fhir_bundle.json and audit_report.json will be saved.
        document_id: Optional explicit document identifier. Defaults to file stem.
        router: Optional pre-configured ExtractionRouter instance (e.g. for testing/mocking).
        normalizer: Optional pre-configured TerminologyNormalizer instance.
        cascade: Optional pre-configured VerificationCascade instance.

    Returns:
        ProcessResult containing generated Bundle, AuditReport, paths, and summary counts.

    Raises:
        FileNotFoundError: If input_file does not exist.
        IngestionError: If file is unsupported, corrupted, or unreadable.
        ValueError: If no claims can be extracted or inputs are inconsistent.
    """
    file_path = Path(input_file)
    if not file_path.exists():
        raise FileNotFoundError(f"Input document file not found: {file_path}")

    effective_doc_id = document_id or file_path.stem
    if not effective_doc_id or not effective_doc_id.strip():
        raise ValueError("Document ID cannot be empty")

    # ─── 1. Ingestion / OCR (§7.1) ──────────────────────────────────────────
    if file_path.suffix.lower() == ".json":
        try:
            doc_text = DocumentText.load(file_path)
            if document_id:
                doc_text.document_id = document_id
        except Exception:
            # Fall back to ingest_document if not directly a DocumentText JSON
            doc_text = ingest_document(file_path, document_id=effective_doc_id)
    else:
        doc_text = ingest_document(file_path, document_id=effective_doc_id)

    if not doc_text.pages:
        raise EmptyDocumentError(f"Document contains 0 pages or no extractable content: {file_path}")

    # ─── 2. Extraction Router (§11) ─────────────────────────────────────────
    active_router = router or ExtractionRouter()
    extraction_output = active_router.extract_document(doc_text)
    claims: list[Claim] = extraction_output.all_claims
    evidence_list: list[Evidence] = extraction_output.all_evidence

    if not claims:
        raise ValueError(
            f"No clinical claims were extracted from document '{doc_text.document_id}'. "
            f"Cannot emit FHIR resource or audit report for empty claim set (§5 #10, §7.8)."
        )

    # ─── 3. Terminology Normalization (§14) ─────────────────────────────────
    active_normalizer = normalizer or TerminologyNormalizer()
    claims = active_normalizer.normalize_claims(claims)

    # ─── 4. Verification Cascade (§12) ───────────────────────────────────────
    active_cascade = cascade or VerificationCascade()
    evidence_map = {e.evidence_id: e for e in evidence_list}
    page_text_map = {str(p.page_number): p.get_page_text() for p in doc_text.pages}

    verifications: list[VerificationResult] = active_cascade.verify_claims(
        claims=claims,
        evidence_map=evidence_map,
        page_text_map=page_text_map,
    )
    verification_map = {v.claim_id: v for v in verifications}

    # ─── 5. FHIR R4B Bundle Generation (§15, §16) ───────────────────────────
    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=evidence_map,
        verification_map=verification_map,
        document_id=doc_text.document_id,
    )

    # ─── 6. Audit Report Generation (§7.8) ──────────────────────────────────
    audit_report = generate_audit_report(
        doc_id=doc_text.document_id,
        claims=claims,
        evidence=evidence_list,
        verifications=verifications,
        fhir_bundle_id=bundle.id,
    )

    # ─── 7. Output Persistence ───────────────────────────────────────────────
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fhir_bundle_path = out_dir / "fhir_bundle.json"
    with open(fhir_bundle_path, "w", encoding="utf-8") as f:
        f.write(bundle.json(indent=2))

    audit_report_path = out_dir / "audit_report.json"
    audit_report.save(audit_report_path, indent=2)

    return ProcessResult(
        document_id=doc_text.document_id,
        fhir_bundle=bundle,
        audit_report=audit_report,
        fhir_bundle_path=fhir_bundle_path,
        audit_report_path=audit_report_path,
        n_claims=audit_report.summary.n_claims,
        n_supported=audit_report.summary.n_supported,
        n_discrepancy=audit_report.summary.n_discrepancy,
        n_insufficient_evidence=audit_report.summary.n_insufficient_evidence,
        n_escalated_to_llm=audit_report.summary.n_escalated_to_llm,
    )


def batch_process(
    input_dir: Union[Path, str],
    output_dir: Union[Path, str],
    pattern: Optional[str] = None,
    router: Optional[ExtractionRouter] = None,
    normalizer: Optional[TerminologyNormalizer] = None,
    cascade: Optional[VerificationCascade] = None,
) -> list[ProcessResult]:
    """Batch-process multiple clinical documents in an input directory.

    Args:
        input_dir: Directory containing clinical documents.
        output_dir: Base output directory.
        pattern: Optional glob pattern (e.g. '*.pdf').
        router: Reusable ExtractionRouter instance.
        normalizer: Reusable TerminologyNormalizer instance.
        cascade: Reusable VerificationCascade instance.

    Returns:
        List of ProcessResult objects for all successfully processed documents.

    Raises:
        FileNotFoundError: If input_dir does not exist.
        ValueError: If no matching documents are found.
    """
    in_dir = Path(input_dir)
    if not in_dir.exists() or not in_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {in_dir}")

    out_base = Path(output_dir)
    out_base.mkdir(parents=True, exist_ok=True)

    # Discover candidate files
    if pattern:
        candidate_files = [f for f in in_dir.glob(pattern) if f.is_file()]
    else:
        allowed_exts = SUPPORTED_EXTENSIONS | {".json"}
        candidate_files = [
            f for f in in_dir.iterdir()
            if f.is_file() and f.suffix.lower() in allowed_exts
        ]

    candidate_files.sort(key=lambda p: p.name)

    if not candidate_files:
        raise ValueError(f"No clinical document files found in {in_dir} (pattern: {pattern or 'default'})")

    # Reuse pipeline components across the entire batch
    shared_router = router or ExtractionRouter()
    shared_normalizer = normalizer or TerminologyNormalizer()
    shared_cascade = cascade or VerificationCascade()

    results: list[ProcessResult] = []
    failures: list[dict[str, str]] = []

    for file_path in candidate_files:
        doc_out_dir = out_base / file_path.stem
        try:
            res = process_document(
                input_file=file_path,
                output_dir=doc_out_dir,
                router=shared_router,
                normalizer=shared_normalizer,
                cascade=shared_cascade,
            )
            # Also write prefixed artifacts to out_base for easy flat discovery
            with open(out_base / f"{res.document_id}_fhir_bundle.json", "w", encoding="utf-8") as f:
                f.write(res.fhir_bundle.json(indent=2))
            res.audit_report.save(out_base / f"{res.document_id}_audit_report.json", indent=2)

            results.append(res)
        except Exception as err:
            failures.append({
                "file": file_path.name,
                "error": str(err),
            })

    # Write batch execution summary
    batch_summary = {
        "total_documents": len(candidate_files),
        "successful": len(results),
        "failed": len(failures),
        "failures": failures,
        "results": [r.to_dict() for r in results],
    }
    with open(out_base / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(batch_summary, f, indent=2)

    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point for the clindoc command."""
    parser = argparse.ArgumentParser(
        prog="clindoc",
        description="Clindoc2FHIR: Multimodal Clinical Document-to-FHIR Verification Pipeline",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Pipeline command to execute")

    # clindoc process <input_file> --output-dir <out_dir>
    proc_parser = subparsers.add_parser(
        "process",
        help="Process a single clinical document through the end-to-end pipeline",
    )
    proc_parser.add_argument(
        "input_file",
        type=str,
        help="Path to input document (.pdf, .png, .jpg, .jpeg, .tiff, or .json DocumentText)",
    )
    proc_parser.add_argument(
        "--output-dir", "-o",
        type=str,
        required=True,
        help="Directory to save output fhir_bundle.json and audit_report.json",
    )
    proc_parser.add_argument(
        "--document-id",
        type=str,
        default=None,
        help="Explicit document identifier (defaults to input file stem)",
    )

    # clindoc batch <input_dir> --output-dir <out_dir>
    batch_parser = subparsers.add_parser(
        "batch",
        help="Batch-process all clinical documents in a directory",
    )
    batch_parser.add_argument(
        "input_dir",
        type=str,
        help="Directory containing clinical documents to process",
    )
    batch_parser.add_argument(
        "--output-dir", "-o",
        type=str,
        required=True,
        help="Base directory to save output artifacts",
    )
    batch_parser.add_argument(
        "--pattern",
        type=str,
        default=None,
        help="Optional glob pattern to filter files (e.g. '*.pdf', '*.json')",
    )

    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 1

    try:
        if args.subcommand == "process":
            res = process_document(
                input_file=args.input_file,
                output_dir=args.output_dir,
                document_id=args.document_id,
            )
            print(f"[OK] Successfully processed document: {res.document_id}")
            print(f"     FHIR Bundle:  {res.fhir_bundle_path}")
            print(f"     Audit Report: {res.audit_report_path}")
            print(
                f"     Claims: {res.n_claims} (Supported: {res.n_supported}, "
                f"Discrepancy: {res.n_discrepancy}, Insufficient: {res.n_insufficient_evidence}, "
                f"Escalated to LLM: {res.n_escalated_to_llm})"
            )
            return 0

        elif args.subcommand == "batch":
            results = batch_process(
                input_dir=args.input_dir,
                output_dir=args.output_dir,
                pattern=args.pattern,
            )
            print(f"[OK] Batch processing completed. Successfully processed {len(results)} document(s).")
            print(f"     Artifacts saved to: {args.output_dir}")
            return 0

        else:
            parser.print_help()
            return 1

    except (FileNotFoundError, IngestionError, UnsupportedFileTypeError, EmptyDocumentError, ValueError) as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1
    except Exception as err:
        sys.stderr.write(f"Unexpected pipeline error: {err}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
