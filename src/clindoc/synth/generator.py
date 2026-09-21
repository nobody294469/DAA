"""Deterministic generator for synthetic clinical laboratory ground-truth data."""

from datetime import date, timedelta
from pathlib import Path
import random
from typing import Any

from clindoc.synth.schema import LabReportGroundTruth, LabTestResult, PatientInfo

FIRST_NAMES_M = ["James", "Robert", "John", "Michael", "David", "William", "Richard", "Joseph", "Thomas", "Charles"]
FIRST_NAMES_F = ["Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica", "Sarah", "Karen"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson"]

DOCTORS = [
    "Dr. Robert Evans, MD",
    "Dr. Sarah Jenkins, MD",
    "Dr. Marcus Vance, DO",
    "Dr. Emily Chen, MD",
    "Dr. Gregory House, MD",
    "Dr. Lisa Cuddy, MD",
    "Dr. Allison Cameron, MD",
]

TEMPLATES = ["template_a", "template_b", "template_c"]

FACILITIES = {
    "template_a": "Memorial General Hospital - Clinical Laboratories",
    "template_b": "Metro Health Diagnostic Center",
    "template_c": "BioPath Reference Laboratory Services",
}

PANEL_DEFINITIONS: dict[str, list[dict[str, Any]]] = {
    "Complete Blood Count": [
        {"test_name": "Hemoglobin", "loinc_code": "718-7", "unit": "g/dL", "low": 12.0, "high": 16.0, "prec": 1},
        {"test_name": "Hematocrit", "loinc_code": "4544-3", "unit": "%", "low": 36.0, "high": 48.0, "prec": 1},
        {"test_name": "White Blood Cell Count", "loinc_code": "6690-2", "unit": "10*3/uL", "low": 4.5, "high": 11.0, "prec": 1},
        {"test_name": "Platelet Count", "loinc_code": "777-3", "unit": "10*3/uL", "low": 150.0, "high": 450.0, "prec": 0},
        {"test_name": "Red Blood Cell Count", "loinc_code": "789-8", "unit": "10*6/uL", "low": 4.2, "high": 5.8, "prec": 2},
    ],
    "Comprehensive Metabolic Panel": [
        {"test_name": "Glucose", "loinc_code": "2345-7", "unit": "mg/dL", "low": 70.0, "high": 99.0, "prec": 0},
        {"test_name": "BUN", "loinc_code": "3094-0", "unit": "mg/dL", "low": 7.0, "high": 20.0, "prec": 0},
        {"test_name": "Creatinine", "loinc_code": "2160-0", "unit": "mg/dL", "low": 0.6, "high": 1.3, "prec": 2},
        {"test_name": "Sodium", "loinc_code": "2951-2", "unit": "mmol/L", "low": 135.0, "high": 145.0, "prec": 0},
        {"test_name": "Potassium", "loinc_code": "2823-3", "unit": "mmol/L", "low": 3.5, "high": 5.1, "prec": 1},
        {"test_name": "Chloride", "loinc_code": "2075-0", "unit": "mmol/L", "low": 96.0, "high": 106.0, "prec": 0},
        {"test_name": "Carbon Dioxide", "loinc_code": "2028-9", "unit": "mmol/L", "low": 22.0, "high": 29.0, "prec": 0},
        {"test_name": "Calcium", "loinc_code": "17861-6", "unit": "mg/dL", "low": 8.5, "high": 10.2, "prec": 1},
        {"test_name": "Total Protein", "loinc_code": "2885-2", "unit": "g/dL", "low": 6.0, "high": 8.3, "prec": 1},
        {"test_name": "Albumin", "loinc_code": "1751-7", "unit": "g/dL", "low": 3.5, "high": 5.0, "prec": 1},
        {"test_name": "Total Bilirubin", "loinc_code": "1975-2", "unit": "mg/dL", "low": 0.1, "high": 1.2, "prec": 1},
        {"test_name": "Alkaline Phosphatase", "loinc_code": "6768-6", "unit": "U/L", "low": 44.0, "high": 147.0, "prec": 0},
        {"test_name": "Alanine Aminotransferase (ALT)", "loinc_code": "1742-6", "unit": "U/L", "low": 7.0, "high": 56.0, "prec": 0},
        {"test_name": "Aspartate Aminotransferase (AST)", "loinc_code": "1920-8", "unit": "U/L", "low": 10.0, "high": 40.0, "prec": 0},
    ],
    "Lipid Panel": [
        {"test_name": "Total Cholesterol", "loinc_code": "2093-3", "unit": "mg/dL", "low": 125.0, "high": 200.0, "prec": 0},
        {"test_name": "Triglycerides", "loinc_code": "2571-8", "unit": "mg/dL", "low": 50.0, "high": 150.0, "prec": 0},
        {"test_name": "HDL Cholesterol", "loinc_code": "2085-9", "unit": "mg/dL", "low": 40.0, "high": 60.0, "prec": 0},
        {"test_name": "LDL Cholesterol", "loinc_code": "13457-7", "unit": "mg/dL", "low": 50.0, "high": 100.0, "prec": 0},
    ],
}


def _format_value(val: float, precision: int) -> float:
    """Round and format float deterministically according to precision."""
    if precision == 0:
        return float(round(val))
    return round(val, precision)


def _format_ref_range(low: float, high: float, prec: int) -> str:
    """Format reference range string."""
    if prec == 0:
        return f"{int(low)} - {int(high)}"
    return f"{low:.{prec}f} - {high:.{prec}f}"


def generate_single_report(doc_index: int, rng: random.Random) -> LabReportGroundTruth:
    """Generate a single deterministic synthetic lab report ground truth record."""
    doc_id = f"LAB_{doc_index:04d}"
    
    # Patient demographics
    gender = rng.choice(["M", "F"])
    first_name = rng.choice(FIRST_NAMES_M if gender == "M" else FIRST_NAMES_F)
    last_name = rng.choice(LAST_NAMES)
    patient_id = f"PAT-{rng.randint(10000, 99999)}"
    
    birth_year = rng.randint(1950, 2005)
    birth_month = rng.randint(1, 12)
    birth_day = rng.randint(1, 28)
    dob = f"{birth_year:04d}-{birth_month:02d}-{birth_day:02d}"
    
    patient = PatientInfo(
        patient_id=patient_id,
        name=f"{first_name} {last_name}",
        dob=dob,
        gender=gender,
    )
    
    # Report metadata
    report_base = date(2025, 1, 1)
    report_date_obj = report_base + timedelta(days=rng.randint(0, 360))
    report_date = report_date_obj.strftime("%Y-%m-%d")
    
    doctor = rng.choice(DOCTORS)
    template_id = TEMPLATES[doc_index % len(TEMPLATES)]
    facility = FACILITIES[template_id]
    
    # Decide panels: alternate between CBC, CMP, and combined
    panel_pattern = doc_index % 4
    if panel_pattern == 0:
        selected_panels = ["Complete Blood Count"]
    elif panel_pattern == 1:
        selected_panels = ["Comprehensive Metabolic Panel"]
    elif panel_pattern == 2:
        selected_panels = ["Complete Blood Count", "Comprehensive Metabolic Panel"]
    else:
        selected_panels = ["Comprehensive Metabolic Panel", "Lipid Panel"]
    
    results: list[LabTestResult] = []
    
    for panel_name in selected_panels:
        tests_meta = PANEL_DEFINITIONS[panel_name]
        for meta in tests_meta:
            low = meta["low"]
            high = meta["high"]
            prec = meta["prec"]
            unit = meta["unit"]
            loinc = meta["loinc_code"]
            tname = meta["test_name"]
            
            # Determine status: 80% normal, 10% low, 10% high
            status_roll = rng.random()
            if status_roll < 0.10:
                flag = "LOW"
                # Below lower bound
                val = low - rng.uniform(0.05 * (high - low), 0.35 * (high - low))
                if val < 0 and low >= 0:
                    val = max(0.1, low * 0.7)
            elif status_roll < 0.20:
                flag = "HIGH"
                # Above upper bound
                val = high + rng.uniform(0.05 * (high - low), 0.45 * (high - low))
            else:
                flag = "NORMAL"
                # Within bounds
                val = rng.uniform(low, high)
            
            formatted_val = _format_value(val, prec)
            # Re-verify flag with formatted value
            if formatted_val > high:
                final_flag = "HIGH"
            elif formatted_val < low:
                final_flag = "LOW"
            else:
                final_flag = "NORMAL"
                
            ref_text = _format_ref_range(low, high, prec)
            
            results.append(
                LabTestResult(
                    test_name=tname,
                    loinc_code=loinc,
                    value=formatted_val,
                    unit=unit,
                    ref_range_low=low,
                    ref_range_high=high,
                    ref_range_text=ref_text,
                    flag=final_flag,
                    panel_name=panel_name,
                )
            )
            
    return LabReportGroundTruth(
        doc_id=doc_id,
        patient=patient,
        report_date=report_date,
        ordering_provider=doctor,
        facility_name=facility,
        template_id=template_id,
        tests=results,
    )


def generate_ground_truth_dataset(count: int = 50, seed: int = 42) -> list[LabReportGroundTruth]:
    """Generate a sequence of synthetic lab report ground truth records deterministically."""
    rng = random.Random(seed)
    return [generate_single_report(i + 1, rng) for i in range(count)]


def generate_synthetic_dataset(
    output_pdf_dir: str | Path = "data/rendered",
    output_gt_dir: str | Path = "data/ground_truth",
    count: int = 50,
    seed: int = 42,
) -> list[LabReportGroundTruth]:
    """Generate complete synthetic dataset: saves ground truth JSON and renders PDF for each record."""
    from clindoc.synth.renderer import render_report_to_pdf

    pdf_dir = Path(output_pdf_dir)
    gt_dir = Path(output_gt_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    gt_dir.mkdir(parents=True, exist_ok=True)

    records = generate_ground_truth_dataset(count=count, seed=seed)

    for record in records:
        # Save ground-truth JSON
        gt_file = gt_dir / f"{record.doc_id}.json"
        gt_file.write_text(record.model_dump_json(indent=2), encoding="utf-8")

        # Render PDF
        pdf_file = pdf_dir / f"{record.doc_id}.pdf"
        render_report_to_pdf(record, pdf_file)

    return records


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate synthetic laboratory reports and ground truth.")
    parser.add_argument("--count", type=int, default=50, help="Number of documents to generate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic generation")
    parser.add_argument("--pdf-dir", type=str, default="data/rendered", help="Output directory for PDFs")
    parser.add_argument("--gt-dir", type=str, default="data/ground_truth", help="Output directory for ground truth")
    args = parser.parse_args()

    generated = generate_synthetic_dataset(
        output_pdf_dir=args.pdf_dir,
        output_gt_dir=args.gt_dir,
        count=args.count,
        seed=args.seed,
    )
    print(f"Successfully generated {len(generated)} lab reports and ground-truth records.")

