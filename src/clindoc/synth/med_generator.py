"""Deterministic generator for synthetic medication prescriptions and ground-truth data."""

from datetime import date, timedelta
import json
from pathlib import Path
import random
from typing import Any

from clindoc.synth.med_schema import MedicationGroundTruth, PrescriptionGroundTruth
from clindoc.synth.schema import PatientInfo

FIRST_NAMES_M = ["James", "Robert", "John", "Michael", "David", "William", "Richard", "Joseph", "Thomas", "Charles"]
FIRST_NAMES_F = ["Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica", "Sarah", "Karen"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson", "Anderson"]

DOCTORS = [
    "Dr. Robert Evans, MD",
    "Dr. Sarah Jenkins, MD",
    "Dr. Marcus Vance, DO",
    "Dr. Emily Chen, MD",
    "Dr. Gregory House, MD",
    "Dr. Lisa Cuddy, MD",
    "Dr. Allison Cameron, MD",
]

TEMPLATES = [
    "clinic_rx_pad",
    "discharge_summary",
    "pharmacy_label",
    "mar_order_sheet",
]

FACILITIES = {
    "clinic_rx_pad": "Family Medicine Associates of Healthcare City",
    "discharge_summary": "Memorial General Hospital - Department of Inpatient Medicine",
    "pharmacy_label": "Walgreens Community Pharmacy #4192",
    "mar_order_sheet": "Metro Health Ambulatory Infusion & Specialty Clinic",
}

DEFAULT_DICT_PATH = Path("configs/medication_subset.json")


def _load_med_catalog(path: Path = DEFAULT_DICT_PATH) -> dict[str, Any]:
    if not path.exists():
        proj_path = Path(__file__).resolve().parents[3] / path
        if proj_path.exists():
            path = proj_path
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


MED_PROFILES = [
    {
        "key": "amoxicillin",
        "doses": [250, 500, 875],
        "unit": "mg",
        "form": "capsule",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["three times daily", "TID", "three times a day", "every 8 hours"],
        "durations": ["5 days", "7 days", "10 days"],
        "sig_templates": [
            "Take 1 {form} {route} {freq} for {duration}",
            "1 {form} {route} {freq} x {duration}",
            "Take one {form} {route} {freq} for {duration}",
        ],
        "quantities": [15, 21, 30],
        "refills": [0, 1],
    },
    {
        "key": "lisinopril",
        "doses": [10, 20, 40],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily", "QD", "daily", "every morning"],
        "durations": ["30 days", "90 days", "ongoing"],
        "sig_templates": [
            "Take 1 {form} {route} {freq}",
            "Take one {form} {route} {freq} for {duration}",
            "1 {form} {route} {freq}",
        ],
        "quantities": [30, 90],
        "refills": [1, 2, 3],
    },
    {
        "key": "metformin",
        "doses": [500, 850, 1000],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["twice daily", "BID", "twice a day", "every 12 hours"],
        "durations": ["30 days", "90 days", "ongoing"],
        "sig_templates": [
            "Take 1 {form} {route} {freq} with meals",
            "1 {form} {route} {freq}",
            "Take one {form} {route} {freq} with food",
        ],
        "quantities": [60, 180],
        "refills": [1, 2, 3],
    },
    {
        "key": "atorvastatin",
        "doses": [10, 20, 40, 80],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily at bedtime", "once daily", "QD", "every evening"],
        "durations": ["30 days", "90 days", "ongoing"],
        "sig_templates": [
            "Take 1 {form} {route} {freq}",
            "Take one {form} {route} {freq}",
            "1 {form} {route} {freq}",
        ],
        "quantities": [30, 90],
        "refills": [2, 3],
    },
    {
        "key": "levothyroxine",
        "doses": [25, 50, 75, 100, 125],
        "unit": "mcg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily in the morning", "once daily", "QD", "every morning on an empty stomach"],
        "durations": ["30 days", "90 days", "ongoing"],
        "sig_templates": [
            "Take 1 {form} {route} {freq}",
            "Take one {form} {route} {freq} 30 minutes before breakfast",
            "1 {form} {route} {freq}",
        ],
        "quantities": [30, 90],
        "refills": [2, 3],
    },
    {
        "key": "amlodipine",
        "doses": [2.5, 5, 10],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily", "QD", "daily"],
        "durations": ["30 days", "90 days", "ongoing"],
        "sig_templates": [
            "Take 1 {form} {route} {freq}",
            "1 {form} {route} {freq}",
        ],
        "quantities": [30, 90],
        "refills": [1, 2, 3],
    },
    {
        "key": "metoprolol",
        "doses": [25, 50, 100],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["twice daily", "BID", "twice a day"],
        "durations": ["30 days", "90 days", "ongoing"],
        "sig_templates": [
            "Take 1 {form} {route} {freq} with or immediately after meals",
            "1 {form} {route} {freq}",
        ],
        "quantities": [60, 180],
        "refills": [2, 3],
    },
    {
        "key": "omeprazole",
        "doses": [20, 40],
        "unit": "mg",
        "form": "capsule",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily before breakfast", "once daily", "QD"],
        "durations": ["14 days", "30 days", "60 days"],
        "sig_templates": [
            "Take 1 {form} {route} {freq}",
            "Take one {form} {route} {freq} for {duration}",
        ],
        "quantities": [30, 60],
        "refills": [0, 1],
    },
    {
        "key": "losartan",
        "doses": [25, 50, 100],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily", "QD", "every morning"],
        "durations": ["30 days", "90 days", "ongoing"],
        "sig_templates": [
            "Take 1 {form} {route} {freq}",
            "1 {form} {route} {freq}",
        ],
        "quantities": [30, 90],
        "refills": [1, 2, 3],
    },
    {
        "key": "albuterol",
        "doses": [90],
        "unit": "mcg",
        "form": "inhaler",
        "route_variants": ["inhalation", "by inhalation"],
        "freq_variants": ["every 4 to 6 hours as needed", "every 4-6 hours PRN", "as needed for shortness of breath"],
        "durations": ["ongoing", "as needed"],
        "sig_templates": [
            "Inhale 2 puffs {route} {freq}",
            "2 puffs {route} {freq}",
        ],
        "quantities": [1, 2],
        "refills": [1, 2],
    },
    {
        "key": "gabapentin",
        "doses": [100, 300, 600],
        "unit": "mg",
        "form": "capsule",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["three times daily", "TID", "three times a day"],
        "durations": ["30 days", "90 days"],
        "sig_templates": [
            "Take 1 {form} {route} {freq}",
            "1 {form} {route} {freq} for {duration}",
        ],
        "quantities": [90, 270],
        "refills": [1, 2],
    },
    {
        "key": "hydrochlorothiazide",
        "doses": [12.5, 25, 50],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily in the morning", "once daily", "QD"],
        "durations": ["30 days", "90 days", "ongoing"],
        "sig_templates": [
            "Take 1 {form} {route} {freq}",
            "1 {form} {route} {freq}",
        ],
        "quantities": [30, 90],
        "refills": [1, 2, 3],
    },
    {
        "key": "sertraline",
        "doses": [25, 50, 100],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily in the morning", "once daily", "QD"],
        "durations": ["30 days", "90 days", "ongoing"],
        "sig_templates": [
            "Take 1 {form} {route} {freq}",
            "1 {form} {route} {freq}",
        ],
        "quantities": [30, 90],
        "refills": [1, 2, 3],
    },
    {
        "key": "prednisone",
        "doses": [5, 10, 20],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily in the morning with food", "once daily", "QD"],
        "durations": ["5 days", "7 days", "10 days"],
        "sig_templates": [
            "Take 1 {form} {route} {freq} for {duration}",
            "Take one {form} {route} {freq} for {duration}",
        ],
        "quantities": [10, 14, 20],
        "refills": [0],
    },
    {
        "key": "ciprofloxacin",
        "doses": [250, 500],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["twice daily", "BID", "every 12 hours"],
        "durations": ["7 days", "10 days", "14 days"],
        "sig_templates": [
            "Take 1 {form} {route} {freq} for {duration}",
            "1 {form} {route} {freq} x {duration}",
        ],
        "quantities": [14, 20, 28],
        "refills": [0],
    },
    {
        "key": "azithromycin",
        "doses": [250, 500],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily", "QD", "daily"],
        "durations": ["5 days", "3 days"],
        "sig_templates": [
            "Take 1 {form} {route} {freq} for {duration}",
            "1 {form} {route} {freq} x {duration}",
        ],
        "quantities": [6, 3],
        "refills": [0],
    },
    {
        "key": "furosemide",
        "doses": [20, 40],
        "unit": "mg",
        "form": "tablet",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["once daily in the morning", "twice daily", "BID"],
        "durations": ["30 days", "ongoing"],
        "sig_templates": [
            "Take 1 {form} {route} {freq}",
            "1 {form} {route} {freq}",
        ],
        "quantities": [30, 60],
        "refills": [1, 2],
    },
    {
        "key": "cephalexin",
        "doses": [250, 500],
        "unit": "mg",
        "form": "capsule",
        "route_variants": ["by mouth", "oral", "PO"],
        "freq_variants": ["four times daily", "QID", "every 6 hours"],
        "durations": ["7 days", "10 days"],
        "sig_templates": [
            "Take 1 {form} {route} {freq} for {duration}",
            "1 {form} {route} {freq} x {duration}",
        ],
        "quantities": [28, 40],
        "refills": [0],
    },
]


def generate_single_prescription(
    doc_id: str,
    catalog: dict[str, Any],
    rng: random.Random,
    template_override: str | None = None,
) -> PrescriptionGroundTruth:
    """Generate a single realistic synthetic prescription ground-truth record."""
    template_type = template_override or rng.choice(TEMPLATES)
    facility_name = FACILITIES[template_type]
    prescriber = rng.choice(DOCTORS)

    # Synthetic patient
    gender = rng.choice(["M", "F"])
    first_name = rng.choice(FIRST_NAMES_M if gender == "M" else FIRST_NAMES_F)
    last_name = rng.choice(LAST_NAMES)
    dob_year = rng.randint(1945, 2005)
    dob_month = rng.randint(1, 12)
    dob_day = rng.randint(1, 28)
    dob_str = f"{dob_year:04d}-{dob_month:02d}-{dob_day:02d}"

    doc_num = int(doc_id.split("_")[1]) if "_" in doc_id else rng.randint(1000, 9999)
    patient_id = f"PAT-{doc_num + 30000:05d}"
    patient = PatientInfo(
        patient_id=patient_id,
        name=f"{first_name} {last_name}",
        dob=dob_str,
        gender=gender,
    )

    base_date = date(2025, 1, 1)
    doc_date = base_date + timedelta(days=rng.randint(0, 180))
    date_str = doc_date.isoformat()

    # Number of medications depends on template
    if template_type in ("discharge_summary", "mar_order_sheet"):
        med_count = rng.choice([2, 3])
    else:
        med_count = 1

    selected_profiles = rng.sample(MED_PROFILES, med_count)
    medications: list[MedicationGroundTruth] = []

    for prof in selected_profiles:
        key = prof["key"]
        cat_info = catalog[key]
        canonical_name = cat_info["canonical_name"]
        rxcui = cat_info["rxcui"]

        dose = rng.choice(prof["doses"])
        unit = prof["unit"]
        form = prof["form"]
        route = rng.choice(prof["route_variants"])
        freq = rng.choice(prof["freq_variants"])
        duration = rng.choice(prof["durations"])
        quantity = rng.choice(prof["quantities"])
        refills = rng.choice(prof["refills"])

        sig_tmpl = rng.choice(prof["sig_templates"])
        sig_text = sig_tmpl.format(
            form=form,
            route=route,
            freq=freq,
            duration=duration,
        )

        is_dur_rendered = ("{duration}" in sig_tmpl) or (template_type == "mar_order_sheet")
        rendered_duration = duration if (is_dur_rendered and duration != "ongoing") else None

        med_gt = MedicationGroundTruth(
            medication_name=canonical_name,
            dose=float(dose),
            dose_unit=unit,
            dosage_form=form,
            route=route,
            frequency=freq,
            duration=rendered_duration,
            quantity=quantity,
            refills=refills,
            sig_text=sig_text,
            canonical_name=canonical_name,
            rxcui=rxcui,
        )
        medications.append(med_gt)

    return PrescriptionGroundTruth(
        doc_id=doc_id,
        template_type=template_type,
        facility_name=facility_name,
        prescriber=prescriber,
        prescription_date=date_str,
        patient=patient,
        medications=medications,
    )


def generate_synthetic_medication_dataset(
    count: int = 50,
    seed: int = 42,
    output_gt_dir: Path = Path("data/med_ground_truth"),
) -> list[PrescriptionGroundTruth]:
    """Generate count synthetic prescription records deterministically with seed 42."""
    rng = random.Random(seed)
    catalog = _load_med_catalog()
    output_gt_dir.mkdir(parents=True, exist_ok=True)

    records: list[PrescriptionGroundTruth] = []
    for i in range(1, count + 1):
        doc_id = f"MED_{i:04d}"
        # Ensure even distribution across templates
        template_override = TEMPLATES[(i - 1) % len(TEMPLATES)]
        record = generate_single_prescription(
            doc_id=doc_id,
            catalog=catalog,
            rng=rng,
            template_override=template_override,
        )
        records.append(record)

        gt_file = output_gt_dir / f"{doc_id}.json"
        with open(gt_file, "w", encoding="utf-8") as f:
            f.write(record.model_dump_json(indent=2))

    return records
