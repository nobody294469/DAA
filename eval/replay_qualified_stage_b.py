"""Qualified Configuration Replay-Invariance Verification Harness (Phase 8.2 Task 4).

Authority & Specifications:
  - PROJECT_SPEC.md §12.2, §21.5
  - PRODUCTIZATION_SPEC.md §3.6, §3.8, §10, §13
  - PRODUCTIZATION_DECISIONS.md PD-005, PD-018, PD-019
  - reports/PHASE8_2_TASK3_QUALIFIED_MANIFEST_REPORT.md

Goal:
  Verify that the currently qualified Stage-B configuration reproduces the
  audited Stage-B decisions recorded in the frozen Phase 4 diagnostic artifact
  (experiments/V8-verification-cascade/phase4_cascade_diagnostics.json).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from clindoc.config import (
    CANONICAL_CALIBRATION_TEMPERATURE,
    CANONICAL_MODERNBERT_CHECKPOINT,
    PROJECT_ROOT,
    ProductionProfileError,
    RuntimeProfile,
)
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.m2.calibration import TemperatureScaler
from clindoc.manifest import (
    CANONICAL_MODERNBERT_CHECKPOINT_SHA256,
    QUALIFIED_MANIFEST_DICT_V1,
    QualificationStatus,
    QualifiedManifest,
    evaluate_cascade_qualification,
)
from clindoc.verify.stage_b import LearnedVerifier, StageBResult

# ─── CANONICAL FROZEN ARTIFACT IDENTIFIERS ──────────────────────────────────
FROZEN_DIAGNOSTICS_DEFAULT_PATH = Path("experiments/V8-verification-cascade/phase4_cascade_diagnostics.json")
FROZEN_DIAGNOSTICS_CANONICAL_SHA256 = "6c2b7d1492d4dfa8e9bb0d08d3513bef4ba166f52b620c71864509594d3e1117"
DEFAULT_NUMERICAL_TOLERANCE = 1e-4

STAGE_C_HISTORICAL_LIMITATION_DOC = {
    "status": "NOT_QUALIFIED_FOR_HISTORICAL_BITWISE_REPRODUCTION",
    "rationale": (
        "Phase 4 audited cascade evaluations utilized an external vLLM instance serving "
        "Qwen/Qwen2.5-7B-Instruct-AWQ on Google Colab. The upstream HuggingFace Git revision hash "
        "and exact hardware kernel scheduling were not captured in phase4_cascade_diagnostics.json. "
        "Therefore, Stage C LLM generation is explicitly documented as NOT QUALIFIED for bitwise "
        "historical replay. Stage B neural verification remains 100% locally deterministic and verifiable."
    ),
}

logger = logging.getLogger("clindoc.replay")


class ReplayDecisionStatus(str, Enum):
    """Categorical verdict for individual record replay comparison."""

    EXACT_MATCH = "EXACT_MATCH"
    TOLERANCE_MATCH = "TOLERANCE_MATCH"
    NUMERICAL_DRIFT = "NUMERICAL_DRIFT"
    LABEL_MISMATCH = "LABEL_MISMATCH"
    ESCALATION_MISMATCH = "ESCALATION_MISMATCH"
    CONFIGURATION_MISMATCH = "CONFIGURATION_MISMATCH"
    NON_REPLAYABLE = "NON_REPLAYABLE"


@dataclass
class RecordReplayDetail:
    """Detailed evaluation result for a single diagnostic record."""

    example_id: str
    document_id: str
    field_type: str
    status: ReplayDecisionStatus
    frozen_label: Optional[str] = None
    current_label: Optional[str] = None
    label_match: bool = False
    frozen_escalated: Optional[bool] = None
    current_escalated: Optional[bool] = None
    escalation_match: bool = False
    frozen_confidence: Optional[float] = None
    current_confidence: Optional[float] = None
    confidence_difference: Optional[float] = None
    within_tolerance: bool = False
    unreplayable_reasons: List[str] = field(default_factory=list)


@dataclass
class ReplaySummary:
    """Aggregate statistics for a Stage B replay execution."""

    total_frozen_records: int = 0
    replayable_records: int = 0
    non_replayable_records: int = 0
    exact_decision_matches: int = 0
    exact_decision_match_rate: float = 0.0
    confidence_matches_within_tolerance: int = 0
    confidence_match_rate: float = 0.0
    label_mismatches_count: int = 0
    escalation_mismatches_count: int = 0
    numerical_drift_count: int = 0
    max_confidence_difference: float = 0.0
    mean_confidence_difference: float = 0.0
    numerical_tolerance: float = DEFAULT_NUMERICAL_TOLERANCE
    passed: bool = False


def compute_file_sha256(path: Union[str, Path]) -> str:
    """Compute SHA-256 hash of a file on disk."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_frozen_artifact_hash(
    path: Union[str, Path],
    expected_hash: str = FROZEN_DIAGNOSTICS_CANONICAL_SHA256,
) -> Tuple[bool, str]:
    """Verify that the frozen diagnostics artifact exists and has an untampered SHA-256 digest."""
    path_obj = Path(path)
    if not path_obj.exists() or not path_obj.is_file():
        raise FileNotFoundError(f"Frozen diagnostics artifact not found at: {path_obj}")

    computed_hash = compute_file_sha256(path_obj)
    is_valid = (computed_hash.lower() == expected_hash.lower())
    return is_valid, computed_hash


def select_replayable_records(
    diagnostic_records: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Tuple[Dict[str, Any], List[str]]]]:
    """Partition records into replayable vs non-replayable based on mandatory input presence.

    A record is replayable for Stage B if and only if:
      1. claim dictionary exists with a valid non-empty value.
      2. evidence dictionary exists with valid non-empty text.
      3. field_type is present.
      4. frozen stage_b decision is recorded (label is present).
    """
    replayable: List[Dict[str, Any]] = []
    non_replayable: List[Tuple[Dict[str, Any], List[str]]] = []

    for idx, r in enumerate(diagnostic_records):
        reasons: List[str] = []
        claim_data = r.get("claim")
        if not claim_data or not isinstance(claim_data, dict) or not claim_data.get("value"):
            reasons.append("missing_or_blank_claim_value")

        ev_data = r.get("evidence")
        if not ev_data or not isinstance(ev_data, dict) or not ev_data.get("text"):
            reasons.append("missing_or_blank_evidence_text")

        if not r.get("field_type"):
            reasons.append("missing_field_type")

        stage_b_data = r.get("stage_b")
        if not stage_b_data or not isinstance(stage_b_data, dict) or not stage_b_data.get("label"):
            reasons.append("missing_frozen_stage_b_label")

        if reasons:
            non_replayable.append((r, reasons))
        else:
            replayable.append(r)

    return replayable, non_replayable


def evaluate_single_record(
    record: Dict[str, Any],
    stage_b: LearnedVerifier,
    tolerance: float = DEFAULT_NUMERICAL_TOLERANCE,
) -> RecordReplayDetail:
    """Replay Stage B inference on a single record and compare with frozen audit values."""
    example_id = str(record.get("example_id") or "unknown")
    doc_id = str(record.get("document_id") or "unknown")
    field_type = str(record.get("field_type") or "unknown")

    # Construct Claim and Evidence objects
    claim_dict = record["claim"]
    ev_dict = record["evidence"]
    frozen_stage_b = record["stage_b"]

    claim = Claim(
        claim_id=claim_dict.get("claim_id") or example_id,
        document_id=doc_id,
        group_id=record.get("group_id") or f"group-{doc_id}",
        field_type=field_type,
        value=claim_dict["value"],
        value_numeric=claim_dict.get("value_numeric"),
        unit_raw=claim_dict.get("unit_raw"),
        extraction_method="deterministic",
        extraction_confidence=1.0,
        evidence_id=ev_dict.get("evidence_id") or f"ev-{example_id}",
        region_id=f"reg-{doc_id}",
    )

    ev_text = ev_dict["text"]
    char_start = ev_dict.get("char_start", 0) or 0
    char_end = ev_dict.get("char_end", len(ev_text)) or len(ev_text)

    evidence = Evidence(
        evidence_id=ev_dict.get("evidence_id") or f"ev-{example_id}",
        document_id=doc_id,
        text=ev_text,
        char_start=char_start,
        char_end=char_end,
        page_number=ev_dict.get("page_number", 1) or 1,
        bbox=BBoxModel(x0=0.0, y0=0.0, x1=100.0, y1=20.0),
        ocr_confidence=ev_dict.get("ocr_confidence", 1.0) or 1.0,
    )

    stage_a_label = record.get("stage_a", {}).get("label") if isinstance(record.get("stage_a"), dict) else None

    # Run inference through current qualified Stage B verifier
    current_result: StageBResult = stage_b.verify(
        claim=claim,
        evidence=evidence,
        stage_a_label=stage_a_label,
    )

    frozen_label = str(frozen_stage_b["label"])
    current_label = str(current_result.label)
    label_match = (frozen_label == current_label)

    frozen_escalated = bool(frozen_stage_b.get("escalated", False))
    current_escalated = bool(current_result.escalated)
    escalation_match = (frozen_escalated == current_escalated)

    frozen_conf = float(frozen_stage_b.get("calibrated_confidence", 0.0))
    current_conf = float(current_result.calibrated_confidence)
    conf_diff = abs(current_conf - frozen_conf)
    within_tolerance = (conf_diff <= tolerance)

    # Determine status
    if not label_match:
        status = ReplayDecisionStatus.LABEL_MISMATCH
    elif not escalation_match:
        status = ReplayDecisionStatus.ESCALATION_MISMATCH
    elif not within_tolerance:
        status = ReplayDecisionStatus.NUMERICAL_DRIFT
    elif conf_diff < 1e-6:
        status = ReplayDecisionStatus.EXACT_MATCH
    else:
        status = ReplayDecisionStatus.TOLERANCE_MATCH

    return RecordReplayDetail(
        example_id=example_id,
        document_id=doc_id,
        field_type=field_type,
        status=status,
        frozen_label=frozen_label,
        current_label=current_label,
        label_match=label_match,
        frozen_escalated=frozen_escalated,
        current_escalated=current_escalated,
        escalation_match=escalation_match,
        frozen_confidence=frozen_conf,
        current_confidence=current_conf,
        confidence_difference=conf_diff,
        within_tolerance=within_tolerance,
    )


def run_stage_b_replay(
    stage_b: LearnedVerifier,
    records: List[Dict[str, Any]],
    tolerance: float = DEFAULT_NUMERICAL_TOLERANCE,
    max_records: Optional[int] = None,
) -> Tuple[ReplaySummary, List[RecordReplayDetail]]:
    """Execute Stage B replay over the provided records and compute summary metrics."""
    target_records = records[:max_records] if max_records is not None else records
    replayable, non_replayable = select_replayable_records(target_records)

    details: List[RecordReplayDetail] = []
    diffs: List[float] = []
    exact_matches = 0
    tolerance_matches = 0
    label_mismatches = 0
    escalation_mismatches = 0
    numerical_drifts = 0

    # Process replayable records
    for r in replayable:
        detail = evaluate_single_record(r, stage_b, tolerance=tolerance)
        details.append(detail)
        if detail.confidence_difference is not None:
            diffs.append(detail.confidence_difference)

        if detail.status == ReplayDecisionStatus.EXACT_MATCH:
            exact_matches += 1
            tolerance_matches += 1
        elif detail.status == ReplayDecisionStatus.TOLERANCE_MATCH:
            tolerance_matches += 1
        elif detail.status == ReplayDecisionStatus.NUMERICAL_DRIFT:
            numerical_drifts += 1
        elif detail.status == ReplayDecisionStatus.LABEL_MISMATCH:
            label_mismatches += 1
        elif detail.status == ReplayDecisionStatus.ESCALATION_MISMATCH:
            escalation_mismatches += 1

    # Record non-replayable records
    for nr_rec, reasons in non_replayable:
        details.append(
            RecordReplayDetail(
                example_id=str(nr_rec.get("example_id") or "unknown"),
                document_id=str(nr_rec.get("document_id") or "unknown"),
                field_type=str(nr_rec.get("field_type") or "unknown"),
                status=ReplayDecisionStatus.NON_REPLAYABLE,
                unreplayable_reasons=reasons,
            )
        )

    n_replayable = len(replayable)
    exact_rate = (exact_matches / n_replayable) if n_replayable > 0 else 0.0
    tol_rate = (tolerance_matches / n_replayable) if n_replayable > 0 else 0.0
    max_diff = max(diffs) if diffs else 0.0
    mean_diff = (sum(diffs) / len(diffs)) if diffs else 0.0

    passed = (
        len(non_replayable) == 0
        and label_mismatches == 0
        and escalation_mismatches == 0
        and numerical_drifts == 0
        and n_replayable > 0
    )

    summary = ReplaySummary(
        total_frozen_records=len(target_records),
        replayable_records=n_replayable,
        non_replayable_records=len(non_replayable),
        exact_decision_matches=exact_matches,
        exact_decision_match_rate=round(exact_rate, 6),
        confidence_matches_within_tolerance=tolerance_matches,
        confidence_match_rate=round(tol_rate, 6),
        label_mismatches_count=label_mismatches,
        escalation_mismatches_count=escalation_mismatches,
        numerical_drift_count=numerical_drifts,
        max_confidence_difference=round(max_diff, 10),
        mean_confidence_difference=round(mean_diff, 10),
        numerical_tolerance=tolerance,
        passed=passed,
    )

    return summary, details


def execute_replay_harness(
    diagnostics_path: Union[str, Path] = FROZEN_DIAGNOSTICS_DEFAULT_PATH,
    manifest: Optional[QualifiedManifest] = None,
    output_json_path: Optional[Union[str, Path]] = None,
    tolerance: float = DEFAULT_NUMERICAL_TOLERANCE,
    max_records: Optional[int] = None,
    split_key: str = "ood",
) -> Dict[str, Any]:
    """Top-level entrypoint for the read-only replay harness.

    Performs full pre-flight verification:
      1. Verifies frozen artifact existence and SHA-256 hash.
      2. Loads and verifies qualified manifest integrity.
      3. Verifies checkpoint existence and SHA-256 hash.
      4. Instantiates production Stage B verifier under fail-closed profile.
      5. Runs replay and formats machine-readable results.
    """
    t0 = time.time()
    diag_path_obj = Path(diagnostics_path)

    # 1. Verify frozen artifact SHA-256
    is_diag_valid, diag_sha256 = verify_frozen_artifact_hash(diag_path_obj)
    if not is_diag_valid:
        raise ProductionProfileError(
            f"Frozen diagnostics artifact SHA-256 mismatch!\n"
            f"  Expected: {FROZEN_DIAGNOSTICS_CANONICAL_SHA256}\n"
            f"  Actual:   {diag_sha256}"
        )

    # 2. Load and verify qualified manifest
    manifest_obj = manifest or QualifiedManifest.create_canonical_v1()
    manifest_obj.verify_integrity()

    # 3. Verify checkpoint SHA-256
    mb_dict = manifest_obj.modernbert
    ckpt_rel = mb_dict.get("checkpoint_path", CANONICAL_MODERNBERT_CHECKPOINT)
    ckpt_path = Path(ckpt_rel)
    if not ckpt_path.is_absolute() and not ckpt_path.exists():
        ckpt_path = PROJECT_ROOT / ckpt_rel

    if not ckpt_path.exists() or not ckpt_path.is_file():
        raise ProductionProfileError(f"Stage B checkpoint missing at: {ckpt_path}")
    ckpt_sha256 = compute_file_sha256(ckpt_path)
    expected_ckpt_hash = mb_dict.get("checkpoint_sha256", CANONICAL_MODERNBERT_CHECKPOINT_SHA256)
    if ckpt_sha256.lower() != expected_ckpt_hash.lower():
        raise ProductionProfileError(
            f"Stage B checkpoint SHA-256 mismatch!\n"
            f"  Expected: {expected_ckpt_hash}\n"
            f"  Actual:   {ckpt_sha256}"
        )

    # 4. Instantiate production Stage B verifier
    temp = float(mb_dict.get("temperature", CANONICAL_CALIBRATION_TEMPERATURE))
    esc_thresh = float(mb_dict.get("escalation_threshold", 0.60))
    scaler = TemperatureScaler(init_temperature=temp)
    scaler.temperature = temp
    scaler.is_fitted = True

    stage_b = LearnedVerifier(
        model_path=str(ckpt_path),
        scaler=scaler,
        escalation_threshold=esc_thresh,
        profile=RuntimeProfile.PRODUCTION,
        allow_heuristic_fallback=False,
    )
    stage_b.load_model()

    # 5. Load frozen diagnostics data
    with open(diag_path_obj, "r", encoding="utf-8") as f:
        diag_data = json.load(f)

    diagnostics_container = diag_data.get("diagnostics", {})
    if isinstance(diagnostics_container, dict):
        raw_records = diagnostics_container.get(split_key, [])
    elif isinstance(diagnostics_container, list):
        raw_records = diagnostics_container
    else:
        raise ValueError(f"Unexpected diagnostics schema in {diag_path_obj}")

    # 6. Run replay
    summary, details = run_stage_b_replay(
        stage_b=stage_b,
        records=raw_records,
        tolerance=tolerance,
        max_records=max_records,
    )

    elapsed = time.time() - t0

    # 7. Compile report dictionary
    result_payload: Dict[str, Any] = {
        "status": "PASS" if summary.passed else "FAIL",
        "harness_metadata": {
            "execution_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_seconds": round(elapsed, 2),
            "profile": RuntimeProfile.PRODUCTION.value,
            "split_evaluated": split_key,
        },
        "artifact_hashes": {
            "frozen_diagnostics_path": str(diag_path_obj),
            "frozen_diagnostics_sha256": diag_sha256,
            "frozen_diagnostics_verified": is_diag_valid,
            "manifest_version": manifest_obj.version,
            "manifest_digest": manifest_obj.manifest_digest,
            "manifest_verified": True,
            "checkpoint_path": str(ckpt_path),
            "checkpoint_sha256": ckpt_sha256,
            "checkpoint_verified": True,
        },
        "stage_c_historical_limitation": STAGE_C_HISTORICAL_LIMITATION_DOC,
        "summary": asdict(summary),
        "mismatches": [asdict(d) for d in details if d.status not in (ReplayDecisionStatus.EXACT_MATCH, ReplayDecisionStatus.TOLERANCE_MATCH)],
        "non_replayable_records": [asdict(d) for d in details if d.status == ReplayDecisionStatus.NON_REPLAYABLE],
    }

    # 8. Persist machine-readable JSON if requested
    if output_json_path:
        out_path = Path(output_json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result_payload, f, indent=2)
        logger.info("Persisted replay results to: %s", out_path)

    return result_payload


def main() -> None:
    """CLI driver for the Stage B replay harness."""
    parser = argparse.ArgumentParser(
        description="Verify Stage-B replay invariance against frozen Phase 4 diagnostics."
    )
    parser.add_argument(
        "--diagnostics-path",
        type=Path,
        default=FROZEN_DIAGNOSTICS_DEFAULT_PATH,
        help="Path to frozen phase4_cascade_diagnostics.json",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("reports/phase4_stage_b_replay_results.json"),
        help="Destination path for machine-readable JSON replay report",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_NUMERICAL_TOLERANCE,
        help="Allowed numerical tolerance for confidence floating-point comparison (default: 1e-4)",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Optional ceiling on number of records to replay (for quick checks)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("\n" + "=" * 80)
    print("PHASE 8.2 TASK 4: STAGE-B QUALIFIED CONFIGURATION REPLAY HARNESS")
    print("=" * 80)

    try:
        results = execute_replay_harness(
            diagnostics_path=args.diagnostics_path,
            output_json_path=args.output_json,
            tolerance=args.tolerance,
            max_records=args.max_records,
        )
    except Exception as err:
        print(f"\n[FATAL FAIL-CLOSED] Replay harness aborted: {err}")
        sys.exit(1)

    sumry = results["summary"]
    print(f"\nStatus:                          {results['status']}")
    print(f"Total Frozen Records Evaluated:  {sumry['total_frozen_records']}")
    print(f"Replayable Records:              {sumry['replayable_records']}")
    print(f"Non-Replayable Records:          {sumry['non_replayable_records']}")
    print(f"Exact Decision Matches:          {sumry['exact_decision_matches']} / {sumry['replayable_records']} ({sumry['exact_decision_match_rate']*100:.2f}%)")
    print(f"Matches Within Tolerance:        {sumry['confidence_matches_within_tolerance']} / {sumry['replayable_records']} ({sumry['confidence_match_rate']*100:.2f}%)")
    print(f"Label Mismatches:                {sumry['label_mismatches_count']}")
    print(f"Escalation Mismatches:           {sumry['escalation_mismatches_count']}")
    print(f"Numerical Drifts:                {sumry['numerical_drift_count']}")
    print(f"Max Confidence Difference:       {sumry['max_confidence_difference']:.10f}")
    print(f"Tolerance Applied:               {sumry['numerical_tolerance']}")
    print(f"Machine-readable JSON:           {args.output_json}")
    print("=" * 80 + "\n")

    if not sumry["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
