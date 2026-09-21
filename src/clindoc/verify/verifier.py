"""Deterministic evidence-grounded verification baseline for clinical laboratory reports."""

import json
from pathlib import Path
import re
from typing import Any

from clindoc.ocr.schema import DocumentOCRRecord, LineBox
from clindoc.schema.lab import ExtractedLabReport, ExtractedObservation
from clindoc.verify.schema import (
    FieldDiscrepancy,
    ObservationVerificationResult,
    ReportVerificationResult,
    VerificationStatus,
)

DEFAULT_LOINC_PATH = Path("configs/loinc_subset.json")
KNOWN_UNITS = {"g/dL", "mg/dL", "mmol/L", "10*3/uL", "10*6/uL", "U/L", "%"}


def _load_loinc_map(path: Path) -> dict[str, Any]:
    if not path.exists():
        proj_path = Path(__file__).resolve().parents[3] / path
        if proj_path.exists():
            path = proj_path
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _reconstruct_ocr_rows(lines: list[LineBox], y_tol: float = 4.0) -> list[list[LineBox]]:
    """Group OCR lines into physical table rows by page and y-center coordinate."""
    rows_dict: dict[tuple[int, float], list[LineBox]] = {}

    for line in lines:
        cy = round((line.bbox[1] + line.bbox[3]) / 2.0, 1)
        p = line.page

        matched_key = None
        for (rp, rcy) in rows_dict.keys():
            if rp == p and abs(rcy - cy) <= y_tol:
                matched_key = (p, rcy)
                break

        if matched_key is None:
            matched_key = (p, cy)
            rows_dict[matched_key] = []

        rows_dict[matched_key].append(line)

    sorted_rows = []
    for (p, rcy) in sorted(rows_dict.keys(), key=lambda k: (k[0], k[1])):
        row_tokens = sorted(rows_dict[(p, rcy)], key=lambda l: l.bbox[0])
        sorted_rows.append(row_tokens)

    return sorted_rows


def _parse_row_source_fields(row: list[LineBox], loinc_map: dict[str, Any]) -> dict[str, Any]:
    """Parse ground fields directly from an OCR row."""
    row_text = " ".join(c.text.strip() for c in row)

    matched_analyte = None
    for c in row:
        ct = c.text.strip()
        if ct in loinc_map:
            matched_analyte = loinc_map[ct].get("canonical_name", ct)
            break

    unit = None
    for c in row:
        ct = c.text.strip()
        if ct in KNOWN_UNITS:
            unit = ct
            break

    # Reference interval
    ref_low, ref_high, ref_text = None, None, None
    for c in row:
        ct = c.text.strip()
        m_ref = re.match(r"^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$", ct)
        if m_ref:
            ref_low = float(m_ref.group(1))
            ref_high = float(m_ref.group(2))
            ref_text = ct
            break

    # Measured numeric value
    val = None
    for c in row:
        ct = c.text.strip()
        if ct in KNOWN_UNITS or (ref_text and ct == ref_text):
            continue
        if re.match(r"^-?\d+(?:\.\d+)?$", ct):
            try:
                val = float(ct)
                break
            except ValueError:
                pass

    # Flag
    flag = None
    for c in row:
        ct = c.text.strip()
        if ct in ("NORMAL", "HIGH", "LOW", "H", "L"):
            flag = "HIGH" if ct == "H" else ("LOW" if ct == "L" else ct)
            break

    if flag is None and val is not None and ref_low is not None and ref_high is not None:
        if val > ref_high:
            flag = "HIGH"
        elif val < ref_low:
            flag = "LOW"
        else:
            flag = "NORMAL"

    # Enclosing bbox and confidence
    row_x0 = min(c.bbox[0] for c in row)
    row_y0 = min(c.bbox[1] for c in row)
    row_x1 = max(c.bbox[2] for c in row)
    row_y1 = max(c.bbox[3] for c in row)
    avg_conf = round(sum(c.confidence for c in row) / max(len(row), 1), 4)

    return {
        "analyte": matched_analyte,
        "value": val,
        "unit": unit,
        "ref_text": ref_text,
        "ref_low": ref_low,
        "ref_high": ref_high,
        "flag": flag,
        "text": row_text,
        "page": row[0].page,
        "bbox": [round(row_x0, 2), round(row_y0, 2), round(row_x1, 2), round(row_y1, 2)],
        "confidence": avg_conf,
    }


def verify_observation_against_evidence(
    obs: ExtractedObservation,
    doc_id: str,
    ocr_rows: list[list[LineBox]],
    loinc_map: dict[str, Any],
) -> ObservationVerificationResult:
    """Verify an individual extracted observation against OCR rows."""
    ev = obs.source_evidence
    discrepancies: list[FieldDiscrepancy] = []

    # 1. Locate matching OCR row by page and vertical alignment or text overlap
    matched_row_info = None
    best_iou = 0.0

    target_y_center = (ev.bbox[1] + ev.bbox[3]) / 2.0

    for row in ocr_rows:
        if row[0].page != ev.page:
            continue
        row_y_center = (min(c.bbox[1] for c in row) + max(c.bbox[3] for c in row)) / 2.0
        if abs(row_y_center - target_y_center) <= 5.0:
            row_info = _parse_row_source_fields(row, loinc_map)
            matched_row_info = row_info
            break

    # If not found by vertical position, try text search on page
    if matched_row_info is None:
        for row in ocr_rows:
            if row[0].page != ev.page:
                continue
            row_text = " ".join(c.text.strip() for c in row)
            if obs.test_name in row_text or (obs.raw_value and obs.raw_value in row_text):
                matched_row_info = _parse_row_source_fields(row, loinc_map)
                break

    # 2. If completely missing from OCR evidence -> MISSING_SOURCE_EVIDENCE / UNSUPPORTED
    if matched_row_info is None:
        disc = FieldDiscrepancy(
            doc_id=doc_id,
            observation=obs.test_name,
            field="observation_presence",
            extracted_value=obs.test_name,
            source_value=None,
            source_evidence_text=ev.text,
            page=ev.page,
            bbox=ev.bbox,
            ocr_confidence=ev.ocr_confidence,
            reason="UNSUPPORTED_OBSERVATION",
        )
        return ObservationVerificationResult(
            test_name=obs.test_name,
            status=VerificationStatus.MISSING_SOURCE_EVIDENCE,
            discrepancies=[disc],
        )

    src = matched_row_info

    # 3. Test Name check
    if src["analyte"] is not None and obs.test_name.strip() != src["analyte"].strip():
        discrepancies.append(
            FieldDiscrepancy(
                doc_id=doc_id,
                observation=obs.test_name,
                field="test_name",
                extracted_value=obs.test_name,
                source_value=src["analyte"],
                source_evidence_text=src["text"],
                page=src["page"],
                bbox=src["bbox"],
                ocr_confidence=src["confidence"],
                reason="TEST_NAME_MISMATCH",
            )
        )

    # 4. Measured Value check
    if src["value"] is not None and abs(obs.value - src["value"]) > 1e-4:
        discrepancies.append(
            FieldDiscrepancy(
                doc_id=doc_id,
                observation=obs.test_name,
                field="value",
                extracted_value=obs.value,
                source_value=src["value"],
                source_evidence_text=src["text"],
                page=src["page"],
                bbox=src["bbox"],
                ocr_confidence=src["confidence"],
                reason="VALUE_MISMATCH",
            )
        )

    # 5. Unit check
    if src["unit"] is not None:
        unit_match = (obs.unit.strip() == src["unit"].strip() or obs.normalized_unit.strip() == src["unit"].strip())
        if not unit_match:
            discrepancies.append(
                FieldDiscrepancy(
                    doc_id=doc_id,
                    observation=obs.test_name,
                    field="unit",
                    extracted_value=obs.unit,
                    source_value=src["unit"],
                    source_evidence_text=src["text"],
                    page=src["page"],
                    bbox=src["bbox"],
                    ocr_confidence=src["confidence"],
                    reason="UNIT_MISMATCH",
                )
            )

    # 6. Reference Range check
    if src["ref_text"] is not None and obs.ref_range_text.strip() != src["ref_text"].strip():
        discrepancies.append(
            FieldDiscrepancy(
                doc_id=doc_id,
                observation=obs.test_name,
                field="ref_range",
                extracted_value=obs.ref_range_text,
                source_value=src["ref_text"],
                source_evidence_text=src["text"],
                page=src["page"],
                bbox=src["bbox"],
                ocr_confidence=src["confidence"],
                reason="REF_RANGE_MISMATCH",
            )
        )

    # 7. Abnormal Flag check
    if src["flag"] is not None and obs.abnormal_flag != src["flag"]:
        discrepancies.append(
            FieldDiscrepancy(
                doc_id=doc_id,
                observation=obs.test_name,
                field="flag",
                extracted_value=obs.abnormal_flag,
                source_value=src["flag"],
                source_evidence_text=src["text"],
                page=src["page"],
                bbox=src["bbox"],
                ocr_confidence=src["confidence"],
                reason="FLAG_MISMATCH",
            )
        )

    # 8. LOINC Code check (against canonical LOINC for verified source analyte)
    ground_analyte = src["analyte"] if src["analyte"] else obs.test_name
    canonical_loinc = loinc_map.get(ground_analyte, {}).get("loinc_code")
    if canonical_loinc and obs.loinc_code != canonical_loinc:
        discrepancies.append(
            FieldDiscrepancy(
                doc_id=doc_id,
                observation=obs.test_name,
                field="loinc",
                extracted_value=obs.loinc_code,
                source_value=canonical_loinc,
                source_evidence_text=src["text"],
                page=src["page"],
                bbox=src["bbox"],
                ocr_confidence=src["confidence"],
                reason="LOINC_MISMATCH",
            )
        )

    status = VerificationStatus.DISCREPANCY if discrepancies else VerificationStatus.VERIFIED
    return ObservationVerificationResult(
        test_name=obs.test_name,
        status=status,
        discrepancies=discrepancies,
    )


def verify_report(
    extracted_report: ExtractedLabReport,
    ocr_record: DocumentOCRRecord,
    loinc_map_path: Path = DEFAULT_LOINC_PATH,
) -> ReportVerificationResult:
    """Verify an entire extracted report against OCR evidence and identify any discrepancies or dropped fields."""
    loinc_map = _load_loinc_map(loinc_map_path)
    ocr_rows = _reconstruct_ocr_rows(ocr_record.lines)

    obs_results: list[ObservationVerificationResult] = []
    all_discrepancies: list[FieldDiscrepancy] = []

    # 1. Verify each extracted observation against OCR
    accounted_analytes = set()
    for obs in extracted_report.observations:
        res = verify_observation_against_evidence(obs, extracted_report.doc_id, ocr_rows, loinc_map)
        obs_results.append(res)
        all_discrepancies.extend(res.discrepancies)
        if res.status != VerificationStatus.MISSING_SOURCE_EVIDENCE:
            accounted_analytes.add(obs.test_name)

    # 2. Check for omitted observations present in OCR source but missing from extraction
    for row in ocr_rows:
        row_info = _parse_row_source_fields(row, loinc_map)
        analyte = row_info.get("analyte")
        if analyte and analyte not in accounted_analytes:
            disc = FieldDiscrepancy(
                doc_id=extracted_report.doc_id,
                observation=analyte,
                field="observation_presence",
                extracted_value=None,
                source_value=analyte,
                source_evidence_text=row_info["text"],
                page=row_info["page"],
                bbox=row_info["bbox"],
                ocr_confidence=row_info["confidence"],
                reason="OMITTED_SOURCE_OBSERVATION",
            )
            all_discrepancies.append(disc)
            obs_results.append(
                ObservationVerificationResult(
                    test_name=analyte,
                    status=VerificationStatus.DISCREPANCY,
                    discrepancies=[disc],
                )
            )

    verified_count = sum(1 for r in obs_results if r.status == VerificationStatus.VERIFIED)
    overall_status = VerificationStatus.VERIFIED if not all_discrepancies else VerificationStatus.DISCREPANCY

    return ReportVerificationResult(
        doc_id=extracted_report.doc_id,
        overall_status=overall_status,
        total_observations=len(obs_results),
        verified_observations=verified_count,
        discrepancy_count=len(all_discrepancies),
        observation_results=obs_results,
        discrepancies=all_discrepancies,
        metadata={"engine": ocr_record.engine},
    )
