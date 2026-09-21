"""Deterministic laboratory row reconstruction and field extraction pipeline."""

import json
from pathlib import Path
import re
from typing import Any

from clindoc.ocr.schema import DocumentOCRRecord, LineBox
from clindoc.schema.evidence import SourceEvidence
from clindoc.schema.lab import ExtractedLabReport, ExtractedObservation, ExtractedPatientInfo

DEFAULT_LOINC_PATH = Path("configs/loinc_subset.json")
DEFAULT_UCUM_PATH = Path("configs/ucum_subset.json")

PANEL_ALIASES = {
    "COMPLETE BLOOD COUNT": "Complete Blood Count",
    "COMPREHENSIVE METABOLIC PANEL": "Comprehensive Metabolic Panel",
    "LIPID PANEL": "Lipid Panel",
}

KNOWN_UNITS = {"g/dL", "mg/dL", "mmol/L", "10*3/uL", "10*6/uL", "U/L", "%"}


def _load_json_resource(path: Path) -> dict[str, Any]:
    if not path.exists():
        # Fallback relative to project root
        proj_path = Path(__file__).resolve().parents[3] / path
        if proj_path.exists():
            path = proj_path
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _reconstruct_rows(lines: list[LineBox], y_tol: float = 4.0) -> list[list[LineBox]]:
    """Group lines on the same page with vertical centers within y_tol into table rows."""
    rows_dict: dict[tuple[int, float], list[LineBox]] = {}

    for line in lines:
        cy = round((line.bbox[1] + line.bbox[3]) / 2.0, 1)
        page = line.page

        matched_key = None
        for (p, rcy) in rows_dict.keys():
            if p == page and abs(rcy - cy) <= y_tol:
                matched_key = (p, rcy)
                break

        if matched_key is None:
            matched_key = (page, cy)
            rows_dict[matched_key] = []

        rows_dict[matched_key].append(line)

    # Sort rows by page then y, and sort tokens within row by x0
    sorted_rows = []
    for (p, rcy) in sorted(rows_dict.keys(), key=lambda k: (k[0], k[1])):
        row_tokens = sorted(rows_dict[(p, rcy)], key=lambda l: l.bbox[0])
        sorted_rows.append(row_tokens)

    return sorted_rows


def _extract_patient_metadata(rows: list[list[LineBox]]) -> tuple[ExtractedPatientInfo, str | None, str | None, str | None]:
    """Extract patient info, report date, provider, and facility from header rows."""
    # Build text lines in reading order
    text_lines = [" ".join(l.text.strip() for l in row) for row in rows]
    full_header_text = "\n".join(text_lines[:15])

    # 1. Facility Name (usually first line or two)
    facility_name = None
    for line_str in text_lines[:3]:
        if any(kw in line_str for kw in ["Hospital", "Diagnostic", "Laboratory", "Pathology", "Clinic", "Center"]):
            facility_name = line_str.split("|")[0].strip()
            break

    # 2. Patient Name
    m_name = re.search(
        r"Patient(?:\s+Name)?:\s*([A-Za-z\s]+?)(?=\s+(?:Patient|Requisition|MRN|DOB|Date|Report|Document|Physician|Ordering|$))",
        full_header_text,
    )
    p_name = m_name.group(1).strip() if m_name else None

    # 3. Patient ID
    m_pid = re.search(r"(PAT-\d+)", full_header_text)
    patient_id = m_pid.group(1).strip() if m_pid else None

    # 4. DOB & Gender
    m_dob = re.search(r"(?:DOB|Date of Birth|DOB\s*/\s*Gender)[\s/A-Za-z:]*(\d{4}-\d{2}-\d{2})", full_header_text)
    dob = m_dob.group(1).strip() if m_dob else None

    gender = None
    m_gen = re.search(r"(?:Gender:\s*(Male|Female|M|F)|\((M|F)\))", full_header_text)
    if m_gen:
        g_raw = next(g for g in m_gen.groups() if g is not None).strip()
        gender = "M" if g_raw in ("Male", "M") else "F"

    # 5. Ordering Provider
    m_doc = re.search(r"(Dr\.\s+[A-Za-z\s]+(?:,\s*(?:MD|DO))?)", full_header_text)
    ordering_provider = m_doc.group(1).strip() if m_doc else None

    # 6. Report Date
    m_date = re.search(r"(?:Report Date|Collection Date):\s*(\d{4}-\d{2}-\d{2})", full_header_text)
    report_date = m_date.group(1).strip() if m_date else None

    patient = ExtractedPatientInfo(
        patient_id=patient_id,
        name=p_name,
        dob=dob,
        gender=gender,
    )

    return patient, report_date, ordering_provider, facility_name


def extract_lab_report(
    ocr_record: DocumentOCRRecord,
    loinc_map_path: Path = DEFAULT_LOINC_PATH,
    ucum_map_path: Path = DEFAULT_UCUM_PATH,
) -> ExtractedLabReport:
    """Parse OCR lines into structured ExtractedLabReport with ground evidence."""
    loinc_map = _load_json_resource(loinc_map_path)
    ucum_map = _load_json_resource(ucum_map_path)

    # 1. Reconstruct table rows from OCR line boxes
    rows = _reconstruct_rows(ocr_record.lines)

    # 2. Extract patient and report metadata
    patient, report_date, ordering_provider, facility_name = _extract_patient_metadata(rows)

    # 3. Iterate rows to extract panels and observation fields
    observations: list[ExtractedObservation] = []
    current_panel: str | None = None

    for row in rows:
        row_text_line = " ".join(cell.text.strip() for cell in row).strip()

        # Check for panel header
        m_panel = re.search(r"(?:Panel|SECTION):\s*(.+)", row_text_line, re.IGNORECASE)
        if m_panel:
            p_candidate = m_panel.group(1).strip()
            current_panel = PANEL_ALIASES.get(p_candidate.upper(), p_candidate)
            continue
        elif row_text_line.upper() in PANEL_ALIASES:
            current_panel = PANEL_ALIASES[row_text_line.upper()]
            continue

        # Skip header rows
        if any(h in row_text_line for h in ["Test Name", "Test Description", "Analyte", "Reference Range", "Observed Value"]):
            continue

        # Check if row contains a known analyte from LOINC catalog
        matched_analyte = None
        matched_cell_idx = None
        for idx, cell in enumerate(row):
            ctext = cell.text.strip()
            if ctext in loinc_map:
                matched_analyte = ctext
                matched_cell_idx = idx
                break

        if not matched_analyte:
            continue

        meta = loinc_map[matched_analyte]
        canonical_test_name = meta.get("canonical_name", matched_analyte)
        loinc_code = meta.get("loinc_code")

        # Parse other cells in row
        other_cells = [c for i, c in enumerate(row) if i != matched_cell_idx]

        unit_str: str = meta.get("default_unit", "")
        ref_text: str = ""
        ref_low: float | None = None
        ref_high: float | None = None
        raw_val: str = ""
        val_float: float | None = None
        flag_str: str | None = None

        for cell in other_cells:
            ct = cell.text.strip()

            # Check for unit
            if ct in KNOWN_UNITS:
                unit_str = ct
                continue

            # Check for reference interval e.g. "70 - 99" or "12.0 - 16.0"
            m_ref = re.match(r"^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$", ct)
            if m_ref:
                ref_text = ct
                ref_low = float(m_ref.group(1))
                ref_high = float(m_ref.group(2))
                continue

            # Check for flag
            if ct in ("NORMAL", "HIGH", "LOW", "H", "L"):
                if ct == "H":
                    flag_str = "HIGH"
                elif ct == "L":
                    flag_str = "LOW"
                else:
                    flag_str = ct
                continue

            # Check for numeric result value
            if re.match(r"^-?\d+(?:\.\d+)?$", ct):
                # Prefer value if not already set
                if val_float is None:
                    raw_val = ct
                    try:
                        val_float = float(ct)
                    except ValueError:
                        pass
                    continue

        if val_float is None:
            continue  # Could not find measured value

        # Derive flag if omitted (e.g. in Template A normal results)
        if flag_str is None or flag_str == "":
            if ref_low is not None and ref_high is not None:
                if val_float > ref_high:
                    flag_str = "HIGH"
                elif val_float < ref_low:
                    flag_str = "LOW"
                else:
                    flag_str = "NORMAL"
            else:
                flag_str = "NORMAL"

        # Normalized unit
        normalized_unit = ucum_map.get(unit_str, {}).get("ucum", unit_str)

        # Build Source Evidence
        row_x0 = min(c.bbox[0] for c in row)
        row_y0 = min(c.bbox[1] for c in row)
        row_x1 = max(c.bbox[2] for c in row)
        row_y1 = max(c.bbox[3] for c in row)
        avg_conf = round(sum(c.confidence for c in row) / max(len(row), 1), 4)

        evidence = SourceEvidence(
            text=row_text_line,
            page=row[0].page,
            bbox=[round(row_x0, 2), round(row_y0, 2), round(row_x1, 2), round(row_y1, 2)],
            ocr_confidence=avg_conf,
        )

        obs = ExtractedObservation(
            test_name=canonical_test_name,
            raw_value=raw_val,
            value=val_float,
            unit=unit_str,
            normalized_unit=normalized_unit,
            ref_range_low=ref_low,
            ref_range_high=ref_high,
            ref_range_text=ref_text,
            abnormal_flag=flag_str,
            loinc_code=loinc_code,
            panel_name=current_panel,
            source_evidence=evidence,
            source_model="lab_row_parser",
        )
        observations.append(obs)

    return ExtractedLabReport(
        doc_id=ocr_record.doc_id,
        patient=patient,
        report_date=report_date,
        ordering_provider=ordering_provider,
        facility_name=facility_name,
        observations=observations,
        metadata={"engine": ocr_record.engine, "device": ocr_record.device},
    )
