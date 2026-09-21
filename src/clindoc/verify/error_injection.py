"""Controlled error-injection engine for evaluating clinical extraction verifiers."""

import copy
import random
from typing import Literal

from clindoc.schema.evidence import SourceEvidence
from clindoc.schema.lab import ExtractedLabReport, ExtractedObservation
from clindoc.verify.schema import InjectedErrorRecord

ERROR_TYPES = [
    "VALUE_CORRUPTION",
    "UNIT_CORRUPTION",
    "TEST_NAME_CORRUPTION",
    "REF_RANGE_CORRUPTION",
    "FLAG_CORRUPTION",
    "LOINC_CORRUPTION",
    "DROPPED_OBSERVATION",
    "HALLUCINATED_OBSERVATION",
]

ALTERNATIVE_UNITS = {
    "mg/dL": "g/dL",
    "g/dL": "mg/dL",
    "mmol/L": "mg/dL",
    "10*3/uL": "10*6/uL",
    "10*6/uL": "10*3/uL",
    "U/L": "%",
    "%": "U/L",
}

ALTERNATIVE_TEST_NAMES = [
    ("Glucose", "Albumin"),
    ("Hemoglobin", "Hematocrit"),
    ("Sodium", "Potassium"),
    ("Creatinine", "BUN"),
    ("Total Bilirubin", "Direct Bilirubin"),
    ("Alkaline Phosphatase", "Acid Phosphatase"),
]


def inject_single_error(
    report: ExtractedLabReport,
    error_type: str,
    target_idx: int = 0,
    rng: random.Random | None = None,
) -> tuple[ExtractedLabReport, InjectedErrorRecord]:
    """Inject a single specific controlled error into an extracted report copy."""
    if rng is None:
        rng = random.Random(42)

    corrupted_report = copy.deepcopy(report)
    obs_list = corrupted_report.observations
    target_idx = target_idx % max(len(obs_list), 1)

    if error_type == "DROPPED_OBSERVATION":
        dropped_obs = obs_list.pop(target_idx)
        record = InjectedErrorRecord(
            doc_id=report.doc_id,
            observation=dropped_obs.test_name,
            field="observation_presence",
            original_value=dropped_obs.test_name,
            corrupted_value=None,
            error_type="DROPPED_OBSERVATION",
        )
        return corrupted_report, record

    if error_type == "HALLUCINATED_OBSERVATION":
        hallucinated = ExtractedObservation(
            test_name="Lipase",
            raw_value="45.0",
            value=45.0,
            unit="U/L",
            normalized_unit="U/L",
            ref_range_low=10.0,
            ref_range_high=140.0,
            ref_range_text="10 - 140",
            abnormal_flag="NORMAL",
            loinc_code="3040-3",
            panel_name="Pancreatic Enzyme Panel",
            source_evidence=SourceEvidence(
                text="Lipase 45.0 U/L (Hallucinated)",
                page=1,
                bbox=[52.0, 720.0, 517.0, 734.0],  # fabricate non-existent bottom line
                ocr_confidence=0.50,
            ),
            source_model="hallucinated_model",
        )
        obs_list.append(hallucinated)
        record = InjectedErrorRecord(
            doc_id=report.doc_id,
            observation=hallucinated.test_name,
            field="observation_presence",
            original_value=None,
            corrupted_value=hallucinated.test_name,
            error_type="HALLUCINATED_OBSERVATION",
        )
        return corrupted_report, record

    target_obs = obs_list[target_idx]

    if error_type == "VALUE_CORRUPTION":
        orig_val = target_obs.value
        # Value digit swap or magnitude shift
        corrupted_val = round(orig_val * 10.0 if orig_val < 50 else orig_val / 10.0, 2)
        target_obs.value = corrupted_val
        target_obs.raw_value = str(corrupted_val)
        record = InjectedErrorRecord(
            doc_id=report.doc_id,
            observation=target_obs.test_name,
            field="value",
            original_value=orig_val,
            corrupted_value=corrupted_val,
            error_type="VALUE_CORRUPTION",
        )

    elif error_type == "UNIT_CORRUPTION":
        orig_unit = target_obs.unit
        corrupted_unit = ALTERNATIVE_UNITS.get(orig_unit, "mg/dL" if orig_unit != "mg/dL" else "g/dL")
        target_obs.unit = corrupted_unit
        target_obs.normalized_unit = corrupted_unit
        record = InjectedErrorRecord(
            doc_id=report.doc_id,
            observation=target_obs.test_name,
            field="unit",
            original_value=orig_unit,
            corrupted_value=corrupted_unit,
            error_type="UNIT_CORRUPTION",
        )

    elif error_type == "TEST_NAME_CORRUPTION":
        orig_name = target_obs.test_name
        corrupted_name = "Albumin" if orig_name != "Albumin" else "Glucose"
        target_obs.test_name = corrupted_name
        record = InjectedErrorRecord(
            doc_id=report.doc_id,
            observation=orig_name,
            field="test_name",
            original_value=orig_name,
            corrupted_value=corrupted_name,
            error_type="TEST_NAME_CORRUPTION",
        )

    elif error_type == "REF_RANGE_CORRUPTION":
        orig_ref = target_obs.ref_range_text
        corrupted_ref = "10.0 - 20.0" if orig_ref != "10.0 - 20.0" else "100 - 200"
        target_obs.ref_range_text = corrupted_ref
        target_obs.ref_range_low = 10.0
        target_obs.ref_range_high = 20.0
        record = InjectedErrorRecord(
            doc_id=report.doc_id,
            observation=target_obs.test_name,
            field="ref_range",
            original_value=orig_ref,
            corrupted_value=corrupted_ref,
            error_type="REF_RANGE_CORRUPTION",
        )

    elif error_type == "FLAG_CORRUPTION":
        orig_flag = target_obs.abnormal_flag
        corrupted_flag = "HIGH" if orig_flag == "NORMAL" else "NORMAL"
        target_obs.abnormal_flag = corrupted_flag
        record = InjectedErrorRecord(
            doc_id=report.doc_id,
            observation=target_obs.test_name,
            field="flag",
            original_value=orig_flag,
            corrupted_value=corrupted_flag,
            error_type="FLAG_CORRUPTION",
        )

    elif error_type == "LOINC_CORRUPTION":
        orig_loinc = target_obs.loinc_code
        corrupted_loinc = "9999-9" if orig_loinc != "9999-9" else "0000-0"
        target_obs.loinc_code = corrupted_loinc
        record = InjectedErrorRecord(
            doc_id=report.doc_id,
            observation=target_obs.test_name,
            field="loinc",
            original_value=orig_loinc,
            corrupted_value=corrupted_loinc,
            error_type="LOINC_CORRUPTION",
        )
    else:
        raise ValueError(f"Unknown error type: {error_type}")

    return corrupted_report, record


def generate_corrupted_dataset(
    reports: list[ExtractedLabReport],
    seed: int = 42,
) -> list[tuple[ExtractedLabReport, list[InjectedErrorRecord]]]:
    """Deterministically corrupt copies of reports cycling through all error types."""
    rng = random.Random(seed)
    corrupted_dataset = []

    for idx, rep in enumerate(reports):
        # Pick error type deterministically
        err_type = ERROR_TYPES[idx % len(ERROR_TYPES)]
        target_obs_idx = rng.randint(0, max(len(rep.observations) - 1, 0))
        corrupted_rep, err_record = inject_single_error(rep, err_type, target_idx=target_obs_idx, rng=rng)
        corrupted_dataset.append((corrupted_rep, [err_record]))

    return corrupted_dataset
