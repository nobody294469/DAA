"""E5 Extraction Robustness Evaluation Harness.

Authority: PROJECT_SPEC.md §18.2, §20.2, §21.1, §22.1 (Experiment E5).
Evaluates the canonical ExtractionRouter under Track A (Visual) and Track B (OCR Text)
degradations across configured severity levels (LOW, MEDIUM, HIGH).

Produces machine-readable results suitable for reporting degradation curves
(precision, recall, F1, exact-match rate) without using legacy Phase-0 schemas.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Sequence

from clindoc.degrade.engine import DegradationEngine
from clindoc.degrade.schema import (
    DegradationRecord,
    DegradationTrack,
    DegradationType,
    SeverityLevel,
)
from clindoc.extract.contracts import Claim
from clindoc.extract.evaluate import (
    ALL_CANONICAL_FIELDS,
    GroundTruthClaim,
    evaluate_dataset,
    load_document_text_for_eval,
    load_ground_truth_claims,
    load_split_manifest,
)
from clindoc.extract.gliner_fallback import MockGLiNERProvider
from clindoc.extract.llm_extractor import MockLLMProvider
from clindoc.extract.router import ExtractionRouter
from clindoc.ocr.document_text import DocumentText
from clindoc.ocr.ingest import ingest_from_record
from clindoc.ocr.schema import DocumentOCRRecord

_PROJ_ROOT = Path(__file__).resolve().parents[3]

TRACK_A_CONDITIONS: list[DegradationType] = [
    DegradationType.GAUSSIAN_BLUR,
    DegradationType.JPEG_COMPRESSION,
    DegradationType.RESOLUTION_REDUCTION,
    DegradationType.ROTATION_SKEW,
    DegradationType.CONTRAST_BRIGHTNESS,
    DegradationType.PARTIAL_LOCAL,
    DegradationType.COMBINED,
]

TRACK_B_CONDITIONS: list[DegradationType] = [
    DegradationType.OCR_CHARACTER_CORRUPTION,
]


def _normalize_condition(cond: str | DegradationType | None) -> DegradationType | None:
    if cond is None or str(cond).lower() in ("clean", "none"):
        return None
    if isinstance(cond, DegradationType):
        return cond
    try:
        return DegradationType(str(cond).lower())
    except ValueError:
        raise ValueError(
            f"Unknown degradation condition: '{cond}'. Valid: {[d.value for d in DegradationType]}"
        )


def _normalize_severity(sev: str | SeverityLevel | None) -> SeverityLevel | None:
    if sev is None or str(sev).lower() in ("none", "clean"):
        return None
    if isinstance(sev, SeverityLevel):
        return sev
    try:
        return SeverityLevel(str(sev).upper())
    except ValueError:
        raise ValueError(
            f"Unknown severity level: '{sev}'. Valid: {[s.value for s in SeverityLevel]}"
        )


def load_degraded_document_text(
    doc_id: str,
    condition: str | DegradationType | None = None,
    severity: str | SeverityLevel | None = None,
    engine: DegradationEngine | None = None,
    base_dir: Path | None = None,
    temp_dir: Path | None = None,
    seed: int = 42,
) -> DocumentText | None:
    """Load or generate degraded DocumentText for a document under specified degradation."""
    root = base_dir or _PROJ_ROOT
    deg_type = _normalize_condition(condition)
    sev_level = _normalize_severity(severity)

    # 1. Clean baseline: condition is None
    if deg_type is None or sev_level is None:
        return load_document_text_for_eval(doc_id, base_dir=root)

    sev_str = sev_level.value.lower()
    doc_id_upper = doc_id.upper()
    is_lab = doc_id_upper.startswith("LAB_")
    is_med = doc_id_upper.startswith("MED_")

    # 2. Check pre-cached degraded OCR records in data/degraded/ocr_cache/
    candidate_cache_paths: list[Path] = []

    if deg_type == DegradationType.OCR_CHARACTER_CORRUPTION:
        sub_dir = "corrupt_lab" if is_lab else ("corrupt_med" if is_med else "corrupt_lab")
        candidate_cache_paths.extend([
            root / "data" / "degraded" / "ocr_cache" / sub_dir / f"{doc_id}_ocr_corrupt_{sev_str}.json",
            root / "data" / "degraded" / "ocr_cache" / sub_dir / f"{doc_id}_{deg_type.value}_{sev_str}.json",
            root / "data" / "degraded" / "ocr_cache" / f"{doc_id}_ocr_corrupt_{sev_str}.json",
        ])
    else:
        sub_dir = "visual_lab" if is_lab else ("visual_med" if is_med else "visual_lab")
        candidate_cache_paths.extend([
            root / "data" / "degraded" / "ocr_cache" / sub_dir / f"{doc_id}_{deg_type.value}_{sev_str}.json",
            root / "data" / "degraded" / "ocr_cache" / f"{doc_id}_{deg_type.value}_{sev_str}.json",
        ])

    for p in candidate_cache_paths:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ingest_from_record(data, doc_id=doc_id)

    # 3. On-the-fly generation if not cached
    if engine is None:
        engine = DegradationEngine()

    out_dir = temp_dir or (root / "data" / "degraded" / "dynamic_tmp")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Track B on-the-fly corruption
    if deg_type == DegradationType.OCR_CHARACTER_CORRUPTION:
        clean_ocr_dir = "ocr_cache" if is_lab else ("med_ocr_cache" if is_med else "ocr_cache")
        clean_ocr_path = root / "data" / clean_ocr_dir / f"{doc_id}.json"
        if not clean_ocr_path.exists():
            return None

        with open(clean_ocr_path, "r", encoding="utf-8") as f:
            clean_record = DocumentOCRRecord.model_validate(json.load(f))

        corrupted_record, _ = engine.apply_ocr_corruption(
            clean_ocr_record=clean_record,
            severity=sev_level,
            output_dir=out_dir,
            seed=seed,
        )
        return ingest_from_record(corrupted_record.model_dump(), doc_id=doc_id)

    # Track A on-the-fly visual degradation
    rendered_dir = "rendered" if is_lab else ("med_rendered" if is_med else "rendered")
    clean_pdf_path = root / "data" / rendered_dir / f"{doc_id}.pdf"
    if not clean_pdf_path.exists():
        return None

    deg_record = engine.apply_visual_degradation(
        clean_pdf_path=clean_pdf_path,
        degradation_type=deg_type,
        severity=sev_level,
        output_dir=out_dir,
        seed=seed,
    )

    # Convert degraded PDF to DocumentText via ingestion or fallback
    from clindoc.ocr.ingest import ingest_document
    try:
        return ingest_document(deg_record.output_pdf, engine="auto")
    except Exception:
        # If OCR fails or engine unavailable, return empty/minimal DocumentText
        return DocumentText(document_id=doc_id, source_type="scanned_image", pages=[])


def evaluate_degradation_condition(
    condition: str | DegradationType | None,
    severity: str | SeverityLevel | None,
    doc_ids: Sequence[str],
    router: ExtractionRouter | None = None,
    engine: DegradationEngine | None = None,
    base_dir: Path | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Evaluate canonical ExtractionRouter on a list of documents under one degradation condition and severity."""
    root = base_dir or _PROJ_ROOT
    deg_type = _normalize_condition(condition)
    sev_level = _normalize_severity(severity)

    if router is None:
        router = ExtractionRouter(
            gliner_provider=MockGLiNERProvider(),
            llm_provider=MockLLMProvider(),
        )

    condition_str = deg_type.value if deg_type else "clean"
    severity_str = sev_level.value if sev_level else "NONE"
    track_str = (
        "clean" if deg_type is None
        else (DegradationTrack.OCR_TEXT.value if deg_type == DegradationType.OCR_CHARACTER_CORRUPTION
              else DegradationTrack.VISUAL.value)
    )

    predictions_by_doc: dict[str, list[Claim]] = {}
    gt_by_doc: dict[str, list[GroundTruthClaim]] = {}

    for d_id in doc_ids:
        doc_text = load_degraded_document_text(
            doc_id=d_id,
            condition=deg_type,
            severity=sev_level,
            engine=engine,
            base_dir=root,
            seed=seed,
        )
        if doc_text is None:
            continue

        ext_output = router.extract_document(doc_text)
        predictions_by_doc[d_id] = ext_output.all_claims

        gt_claims = load_ground_truth_claims(d_id, base_dir=root)
        gt_by_doc[d_id] = gt_claims

    eval_result = evaluate_dataset(predictions_by_doc, gt_by_doc)

    return {
        "condition": condition_str,
        "track": track_str,
        "severity": severity_str,
        "num_documents": eval_result["num_documents"],
        "macro_f1": eval_result["macro_f1"],
        "micro_precision": eval_result["micro_precision"],
        "micro_recall": eval_result["micro_recall"],
        "micro_f1": eval_result["micro_f1"],
        "overall_exact_match_rate": eval_result["overall_exact_match_rate"],
        "total_gt_claims": eval_result["total_gt_claims"],
        "total_pred_claims": eval_result["total_pred_claims"],
        "metrics_per_field": eval_result["metrics_per_field"],
    }


def run_e5_robustness_benchmark(
    conditions: Sequence[str | DegradationType] | None = None,
    severities: Sequence[str | SeverityLevel] | None = None,
    doc_ids: Sequence[str] | None = None,
    split: Literal["all", "cv", "validation", "ood"] = "cv",
    router: ExtractionRouter | None = None,
    engine: DegradationEngine | None = None,
    base_dir: Path | None = None,
    save_path: Path | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Execute complete E5 Robustness Benchmark across degradation conditions and severity levels."""
    root = base_dir or _PROJ_ROOT

    # Resolve document IDs
    if doc_ids is None:
        splits = load_split_manifest(root)
        if split == "all":
            doc_ids = sorted(list(set(splits["cv"] + splits["ood"])))
        elif split == "cv":
            doc_ids = splits["cv"]
        elif split == "validation":
            doc_ids = splits["validation"]
        elif split == "ood":
            doc_ids = splits["ood"]
        else:
            doc_ids = splits.get(split, [])

    # Default to all Track A and Track B conditions if not specified
    if conditions is None:
        eval_conditions: list[DegradationType] = TRACK_A_CONDITIONS + TRACK_B_CONDITIONS
    else:
        eval_conditions = [_normalize_condition(c) for c in conditions if _normalize_condition(c) is not None]  # type: ignore

    # Default to all 3 severities
    if severities is None:
        eval_severities: list[SeverityLevel] = [SeverityLevel.LOW, SeverityLevel.MEDIUM, SeverityLevel.HIGH]
    else:
        eval_severities = [_normalize_severity(s) for s in severities if _normalize_severity(s) is not None]  # type: ignore

    # 1. Evaluate clean baseline first
    clean_baseline = evaluate_degradation_condition(
        condition=None,
        severity=None,
        doc_ids=doc_ids,
        router=router,
        engine=engine,
        base_dir=root,
        seed=seed,
    )

    # 2. Evaluate all conditions across severities
    results: list[dict[str, Any]] = []
    degradation_curves: dict[str, dict[str, Any]] = {}

    for cond in eval_conditions:
        cond_name = cond.value
        degradation_curves[cond_name] = {
            "track": DegradationTrack.OCR_TEXT.value if cond == DegradationType.OCR_CHARACTER_CORRUPTION else DegradationTrack.VISUAL.value,
            "severities": [s.value for s in eval_severities],
            "macro_f1": [],
            "exact_match_rate": [],
            "micro_f1": [],
        }

        for sev in eval_severities:
            cond_res = evaluate_degradation_condition(
                condition=cond,
                severity=sev,
                doc_ids=doc_ids,
                router=router,
                engine=engine,
                base_dir=root,
                seed=seed,
            )
            results.append(cond_res)

            degradation_curves[cond_name]["macro_f1"].append(cond_res["macro_f1"])
            degradation_curves[cond_name]["exact_match_rate"].append(cond_res["overall_exact_match_rate"])
            degradation_curves[cond_name]["micro_f1"].append(cond_res["micro_f1"])

    report = {
        "experiment_id": "E5-degradation-extraction",
        "timestamp": datetime.now().isoformat(),
        "split": split,
        "num_documents": len(doc_ids),
        "clean_baseline": clean_baseline,
        "conditions_evaluated": [c.value for c in eval_conditions],
        "severities_evaluated": [s.value for s in eval_severities],
        "degradation_curves": degradation_curves,
        "results": results,
    }

    if save_path:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    return report
