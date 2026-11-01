"""n2c2 2018 Track 2 Evaluation Runner for ClinicalDoc.

Authority: User Request / PROJECT_SPEC §18.3, §21 / DEC-010, DEC-017
Location: external_eval/runners/n2c2_2018_runner.py

Evaluates ClinicalDoc's extraction pipeline on n2c2 2018 Track 2 (Medication Extraction & ADE)
BRAT standoff data when authorized access is available.

Key Guarantees:
1. Preserves official dataset partitions (train=303, test=202); never randomly re-splits.
2. Zero-Mock Enforcement: Stops with ModelProviderUnavailableError if genuine model/vLLM is offline.
3. Uses canonical evaluate_dataset() contract from clindoc.extract.evaluate.
4. Comprehensive auditing: tracks accepted claims, unsupported labels, rejected annotations, and processing failures.
5. Reproducibility & PHI safety: records code revision, model config, timestamps; NEVER logs raw clinical text.
6. Official Protocol Disclaimer: Explicitly distinguishes ClinicalDoc 5-field canonical evaluation from official 9-entity n2c2 challenge scoring.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

# Ensure src and external_eval are on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "external_eval"))

from adapters.brat_adapter import (
    ALL_CANONICAL_FIELDS,
    BratParseResult,
    parse_brat_file,
    parse_brat_content,
    brat_result_to_document_text,
)
from clindoc.extract.contracts import Claim
from clindoc.extract.deterministic import DeterministicExtractor
from clindoc.extract.evaluate import evaluate_dataset, normalize_field_value, GroundTruthClaim
from clindoc.extract.gliner_fallback import SubprocessGLiNERProvider


class ModelProviderUnavailableError(RuntimeError):
    """Raised when a genuine model provider or required inference service is offline."""
    pass


class DatasetIntegrityError(ValueError):
    """Raised when an evaluation directory has fatal integrity flaws."""
    pass


@dataclass
class ProcessingFailure:
    document_id: str
    failure_type: Literal[
        "missing_ann_file",
        "missing_txt_file",
        "empty_document",
        "mismatched_id",
        "unparseable_annotation",
    ]
    message: str
    file_path: Optional[str] = None


@dataclass
class DatasetIngestionSummary:
    partition: str
    directory_path: str
    total_txt_files: int = 0
    total_ann_files: int = 0
    valid_document_pairs: int = 0
    total_accepted_claims: int = 0
    accepted_by_field: dict[str, int] = field(default_factory=dict)
    total_unsupported_annotations: int = 0
    unsupported_by_label: dict[str, int] = field(default_factory=dict)
    total_rejected_annotations: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    processing_failures: list[ProcessingFailure] = field(default_factory=list)
    expected_partition_documents: Optional[int] = None
    partition_coverage_complete: bool = True
    partition_warning: Optional[str] = None


@dataclass
class ModelMetadata:
    pipeline_name: str
    provider_class: str
    model_identifier: str
    endpoint_url: Optional[str] = None
    is_genuine_production: bool = True
    config_parameters: dict[str, Any] = field(default_factory=dict)


def get_git_revision() -> str:
    """Retrieve current git revision hash safely."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return "unknown"


def validate_and_load_dataset(
    data_dir: Path | str,
    partition: str = "custom",
) -> tuple[dict[str, list[GroundTruthClaim]], dict[str, str], DatasetIngestionSummary]:
    """Ingest a directory of BRAT (.txt and .ann) pairs with strict integrity validation.

    Guarantees:
    - Never silently ignores missing annotations or orphan text files.
    - Validates document IDs match across text and annotation files.
    - Rejects empty documents.
    """
    path = Path(data_dir)
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Dataset directory does not exist: {path}")

    summary = DatasetIngestionSummary(
        partition=partition,
        directory_path=str(path.resolve()),
    )

    txt_files = sorted(list(path.glob("*.txt")))
    ann_files = sorted(list(path.glob("*.ann")))

    summary.total_txt_files = len(txt_files)
    summary.total_ann_files = len(ann_files)

    ann_map = {f.stem: f for f in ann_files}
    txt_map = {f.stem: f for f in txt_files}

    # Check for orphan .ann files (ann without txt)
    for ann_stem, ann_file in ann_map.items():
        if ann_stem not in txt_map:
            summary.processing_failures.append(ProcessingFailure(
                document_id=ann_stem,
                failure_type="missing_txt_file",
                message=f"Orphan annotation file without corresponding .txt: {ann_file.name}",
                file_path=str(ann_file),
            ))

    gt_by_doc: dict[str, list[GroundTruthClaim]] = {}
    docs_text_by_id: dict[str, str] = {}

    for txt_file in txt_files:
        doc_id = txt_file.stem
        ann_file = ann_map.get(doc_id)

        if ann_file is None:
            summary.processing_failures.append(ProcessingFailure(
                document_id=doc_id,
                failure_type="missing_ann_file",
                message=f"Missing .ann standoff file for text document: {txt_file.name}",
                file_path=str(txt_file),
            ))
            continue

        raw_text = txt_file.read_text(encoding="utf-8", errors="replace")
        if not raw_text.strip():
            summary.processing_failures.append(ProcessingFailure(
                document_id=doc_id,
                failure_type="empty_document",
                message=f"Source text document is completely empty: {txt_file.name}",
                file_path=str(txt_file),
            ))
            continue

        try:
            parse_res = parse_brat_file(txt_file, ann_file)
        except Exception as e:
            summary.processing_failures.append(ProcessingFailure(
                document_id=doc_id,
                failure_type="unparseable_annotation",
                message=f"Failed to parse BRAT standoff file: {e}",
                file_path=str(ann_file),
            ))
            continue

        # Ingestion successful
        summary.valid_document_pairs += 1
        gt_by_doc[doc_id] = parse_res.accepted_claims
        docs_text_by_id[doc_id] = raw_text

        summary.total_accepted_claims += parse_res.accepted_count
        for c in parse_res.accepted_claims:
            summary.accepted_by_field[c.field_type] = summary.accepted_by_field.get(c.field_type, 0) + 1

        summary.total_unsupported_annotations += parse_res.unsupported_count
        for u in parse_res.unsupported_annotations:
            lbl = u["raw_label"]
            summary.unsupported_by_label[lbl] = summary.unsupported_by_label.get(lbl, 0) + 1

        summary.total_rejected_annotations += parse_res.rejected_count
        for r in parse_res.rejected_annotations:
            summary.rejected_by_reason[r.issue_type] = summary.rejected_by_reason.get(r.issue_type, 0) + 1

    # Official n2c2 2018 Track 2 partition size audit
    if partition == "test":
        summary.expected_partition_documents = 202
    elif partition == "train":
        summary.expected_partition_documents = 303

    if summary.expected_partition_documents is not None:
        if summary.valid_document_pairs != summary.expected_partition_documents:
            summary.partition_coverage_complete = False
            summary.partition_warning = (
                f"Document count mismatch for official '{partition}' partition: "
                f"found {summary.valid_document_pairs} valid pairs, "
                f"expected {summary.expected_partition_documents} per official n2c2 2018 Track 2 specification."
            )

    return gt_by_doc, docs_text_by_id, summary


def verify_model_provider_availability(
    pipeline: Literal["gliner", "deterministic", "cascade", "llm_narrative"],
    vllm_base_url: str = "http://localhost:8000/v1",
) -> ModelMetadata:
    """Validate that the requested model provider is genuine and accessible.

    Zero-Mock Rule: If the real model service is offline, raise ModelProviderUnavailableError.
    Never substitute mock predictions as evaluation results.
    """
    if pipeline == "deterministic":
        return ModelMetadata(
            pipeline_name="deterministic",
            provider_class="DeterministicExtractor",
            model_identifier="rules/regex/medication_catalog",
            config_parameters={"confidence_threshold": 0.75},
        )

    if pipeline == "gliner":
        venv_python = REPO_ROOT / ".venv_ner" / "Scripts" / "python.exe"
        if not venv_python.exists():
            raise ModelProviderUnavailableError(
                f"GLiNER subprocess environment not found at: {venv_python}. "
                "Ensure .venv_ner is provisioned before running GLiNER external evaluation."
            )
        return ModelMetadata(
            pipeline_name="gliner",
            provider_class="SubprocessGLiNERProvider",
            model_identifier="Ihor/gliner-biomed-base-v1.0",
            config_parameters={"threshold": 0.30, "venv_python": str(venv_python)},
        )

    if pipeline == "cascade":
        venv_python = REPO_ROOT / ".venv_ner" / "Scripts" / "python.exe"
        if not venv_python.exists():
            raise ModelProviderUnavailableError(
                f"Cascade requires GLiNER subprocess at: {venv_python}. Environment missing."
            )
        return ModelMetadata(
            pipeline_name="cascade",
            provider_class="DeterministicExtractor + SubprocessGLiNERProvider",
            model_identifier="DEC-004-Cascade (Deterministic threshold=0.75 -> GLiNER fallback)",
            config_parameters={"deterministic_threshold": 0.75, "gliner_threshold": 0.30},
        )

    if pipeline == "llm_narrative":
        # Probe vLLM endpoint
        try:
            req = urllib.request.Request(f"{vllm_base_url}/models")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status != 200:
                    raise ModelProviderUnavailableError(
                        f"vLLM endpoint {vllm_base_url} returned status {resp.status}."
                    )
        except Exception as e:
            raise ModelProviderUnavailableError(
                f"Genuine Qwen/vLLM inference endpoint at {vllm_base_url} is UNAVAILABLE ({e}). "
                "Evaluation aborted: MockLLMProvider is strictly forbidden for real-data evaluation."
            )
        return ModelMetadata(
            pipeline_name="llm_narrative",
            provider_class="VLLMProvider",
            model_identifier="Qwen/Qwen2.5-7B-Instruct",
            endpoint_url=vllm_base_url,
        )

    raise ValueError(f"Unknown pipeline: {pipeline}")


def generate_pipeline_predictions(
    docs_text_by_id: dict[str, str],
    model_metadata: ModelMetadata,
) -> dict[str, list[Claim]]:
    """Generate genuine predictions for evaluation documents using the verified provider."""
    predictions_by_doc: dict[str, list[Claim]] = {}

    if model_metadata.pipeline_name == "deterministic":
        extractor = DeterministicExtractor()
        for doc_id, text in docs_text_by_id.items():
            # Ingest text as DocumentText
            doc_obj = brat_result_to_document_text(BratParseResult(document_id=doc_id, source_text=text))
            page = doc_obj.pages[0]
            _, claims = extractor.extract_med_line(doc_id, page, text, 0, len(text))
            predictions_by_doc[doc_id] = claims

    elif model_metadata.pipeline_name == "gliner":
        provider = SubprocessGLiNERProvider()
        doc_ids = list(docs_text_by_id.keys())
        texts = [docs_text_by_id[did] for did in doc_ids]

        raw_preds_batch = provider.predict_batch(texts)
        gliner_label_map = {
            "medication": "med.name",
            "dosage": "med.dose",
            "route": "med.route",
            "frequency": "med.frequency",
        }

        for doc_id, raw_preds in zip(doc_ids, raw_preds_batch):
            claims = []
            for p in raw_preds:
                plbl = p.get("label")
                ptxt = p.get("text", "").strip()
                if plbl in gliner_label_map and ptxt:
                    claims.append(Claim(
                        document_id=doc_id,
                        field_type=gliner_label_map[plbl],
                        value=ptxt,
                        extraction_method="gliner",
                        extraction_confidence=p.get("score", 1.0),
                        evidence_id="ev_gliner",
                        region_id="reg_0",
                    ))
            predictions_by_doc[doc_id] = claims

    elif model_metadata.pipeline_name == "cascade":
        det_extractor = DeterministicExtractor()
        provider = SubprocessGLiNERProvider()
        doc_ids = list(docs_text_by_id.keys())
        texts = [docs_text_by_id[did] for did in doc_ids]

        raw_gliner_batch = provider.predict_batch(texts)
        gliner_label_map = {
            "medication": "med.name",
            "dosage": "med.dose",
            "route": "med.route",
            "frequency": "med.frequency",
        }
        threshold = model_metadata.config_parameters.get("deterministic_threshold", 0.75)

        for doc_id, text, g_preds in zip(doc_ids, texts, raw_gliner_batch):
            doc_obj = brat_result_to_document_text(BratParseResult(document_id=doc_id, source_text=text))
            page = doc_obj.pages[0]
            _, det_claims = det_extractor.extract_med_line(doc_id, page, text, 0, len(text))

            claims = []
            resolved_keys = set()

            # High confidence deterministic claims and Tier 2 duration
            for dc in det_claims:
                if dc.field_type == "med.duration" or dc.extraction_confidence >= threshold:
                    claims.append(dc)
                    resolved_keys.add((dc.field_type, dc.value.lower().strip()))

            # GLiNER fallback
            for gp in g_preds:
                plbl = gp.get("label")
                ptxt = gp.get("text", "").strip()
                if plbl in gliner_label_map and ptxt:
                    fld = gliner_label_map[plbl]
                    if (fld, ptxt.lower()) not in resolved_keys:
                        claims.append(Claim(
                            document_id=doc_id,
                            field_type=fld,
                            value=ptxt,
                            extraction_method="gliner",
                            extraction_confidence=gp.get("score", 1.0),
                            evidence_id="ev_gliner",
                            region_id="reg_0",
                        ))
            predictions_by_doc[doc_id] = claims

    return predictions_by_doc


def run_n2c2_evaluation(
    data_dir: Path | str,
    partition: str = "test",
    pipeline: Literal["gliner", "deterministic", "cascade", "llm_narrative"] = "gliner",
    output_dir: Path | str | None = None,
    vllm_base_url: str = "http://localhost:8000/v1",
) -> dict[str, Any]:
    """Execute evaluation on an n2c2/i2b2 BRAT dataset partition.

    Enforces:
    - Partition preservation: records partition name; no mixing of test and train.
    - Zero mocks: fails fast if model service is unreachable.
    - PHI safety: zero clinical note strings in output reports.
    """
    timestamp_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    git_rev = get_git_revision()

    # 1. Ingest and validate dataset
    gt_by_doc, docs_text_by_id, ingestion_summary = validate_and_load_dataset(
        data_dir=data_dir,
        partition=partition,
    )

    if ingestion_summary.valid_document_pairs == 0:
        raise DatasetIntegrityError(
            f"No valid document pairs found in {data_dir}. Check directory structure."
        )

    # 2. Verify model provider availability (Zero-mock rule)
    model_metadata = verify_model_provider_availability(
        pipeline=pipeline,
        vllm_base_url=vllm_base_url,
    )

    # 3. Generate genuine model predictions
    predictions_by_doc = generate_pipeline_predictions(
        docs_text_by_id=docs_text_by_id,
        model_metadata=model_metadata,
    )

    # Defensive safeguard against silent metric inflation:
    # Ensure every document in gt_by_doc is represented in predictions_by_doc.
    # If a document failed inference, it must be evaluated as empty predictions ([])
    # so all its ground truth claims become False Negatives (FN).
    for did in gt_by_doc:
        if did not in predictions_by_doc:
            predictions_by_doc[did] = []

    # 4. Canonical evaluation via evaluate_dataset
    eval_result = evaluate_dataset(
        predictions_by_doc=predictions_by_doc,
        gt_by_doc=gt_by_doc,
    )

    # 5. Build auditable output report (PHI-Safe: counts, IDs, metrics only)
    report: dict[str, Any] = {
        "benchmark_metadata": {
            "dataset_name": "n2c2 2018 Track 2 (Medication Extraction & ADE)",
            "partition": partition,
            "evaluation_timestamp_utc": timestamp_utc,
            "git_revision": git_rev,
            "data_directory": str(Path(data_dir).resolve()),
            "total_documents_evaluated": eval_result["num_documents"],
            "total_gt_claims": eval_result["total_gt_claims"],
            "total_pred_claims": eval_result["total_pred_claims"],
        },
        "model_metadata": asdict(model_metadata),
        "protocol_disclaimer": (
            "ClinicalDoc Canonical Evaluation Protocol: Evaluates 5 canonical medication fields "
            "(med.name, med.dose, med.route, med.frequency, med.duration) using canonical value matching. "
            "Official n2c2 2018 Challenge Protocol Disclaimer: Official challenge evaluation evaluated "
            "relaxed/exact F1 across 9 entity types (including Form, Reason, ADE) and relation links. "
            "ClinicalDoc field-level metrics must not be conflated with official challenge leaderboard scores."
        ),
        "ingestion_summary": {
            "partition": ingestion_summary.partition,
            "expected_partition_documents": ingestion_summary.expected_partition_documents,
            "partition_coverage_complete": ingestion_summary.partition_coverage_complete,
            "partition_warning": ingestion_summary.partition_warning,
            "valid_document_pairs": ingestion_summary.valid_document_pairs,
            "accepted_claims_count": ingestion_summary.total_accepted_claims,
            "accepted_by_field": ingestion_summary.accepted_by_field,
            "unsupported_annotations_count": ingestion_summary.total_unsupported_annotations,
            "unsupported_by_label": ingestion_summary.unsupported_by_label,
            "rejected_annotations_count": ingestion_summary.total_rejected_annotations,
            "rejected_by_reason": ingestion_summary.rejected_by_reason,
            "processing_failures_count": len(ingestion_summary.processing_failures),
            "processing_failures": [asdict(f) for f in ingestion_summary.processing_failures],
        },
        "canonical_evaluation_metrics": {
            "micro_precision": eval_result["micro_precision"],
            "micro_recall": eval_result["micro_recall"],
            "micro_f1": eval_result["micro_f1"],
            "macro_f1": eval_result["macro_f1"],
            "overall_exact_match_rate": eval_result["overall_exact_match_rate"],
            "metrics_per_field": eval_result["metrics_per_field"],
        },
    }

    if output_dir:
        out_p = Path(output_dir)
        out_p.mkdir(parents=True, exist_ok=True)
        out_file = out_p / f"n2c2_{partition}_{pipeline}_{int(datetime.datetime.now().timestamp())}.json"
        out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["saved_report_path"] = str(out_file)

    return report


def main():
    parser = argparse.ArgumentParser(description="n2c2 2018 Track 2 Evaluation Runner for ClinicalDoc")
    parser.add_argument("--data-dir", required=True, help="Directory containing n2c2 .txt and .ann files")
    parser.add_argument("--partition", default="test", choices=["train", "test", "dev", "custom"], help="Dataset partition name")
    parser.add_argument("--pipeline", default="gliner", choices=["gliner", "deterministic", "cascade", "llm_narrative"], help="Extraction pipeline to evaluate")
    parser.add_argument("--output-dir", default=None, help="Directory to save evaluation report JSON")
    parser.add_argument("--vllm-url", default="http://localhost:8000/v1", help="vLLM base URL for llm_narrative")

    args = parser.parse_args()

    try:
        report = run_n2c2_evaluation(
            data_dir=args.data_dir,
            partition=args.partition,
            pipeline=args.pipeline,
            output_dir=args.output_dir,
            vllm_base_url=args.vllm_url,
        )
        print(f"\nEvaluation completed on {report['benchmark_metadata']['total_documents_evaluated']} documents.")
        print(f"Micro Precision: {report['canonical_evaluation_metrics']['micro_precision']}")
        print(f"Micro Recall   : {report['canonical_evaluation_metrics']['micro_recall']}")
        print(f"Micro F1       : {report['canonical_evaluation_metrics']['micro_f1']}")
        if "saved_report_path" in report:
            print(f"Report saved to: {report['saved_report_path']}")
    except ModelProviderUnavailableError as e:
        print(f"\n[EVALUATION BLOCKED - ZERO MOCK POLICY]: {e}", file=sys.stderr)
        sys.exit(2)
    except DatasetIntegrityError as e:
        print(f"\n[DATASET INTEGRITY ERROR]: {e}", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
