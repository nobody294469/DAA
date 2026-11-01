"""Expanded Benchmark - Prescription Note Specifications (8 documents).

Authority: User Request / Expanded Synthetic Benchmark
Location: external_eval/expanded_benchmark/specs_prescriptions.py
"""
from __future__ import annotations

PRESCRIPTION_SPECS = [
    {
        "doc_id": "MED_EXP_01",
        "category": "structured_medication",
        "specialty": "Cardiology",
        "title": "Outpatient Cardiology Discharge Prescription",
        "format": "prescription_pad",
        "features": ["retail_order", "standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL PRESCRIPTION ORDER - NOT PHI - FOR EVALUATION ONLY]
MEMORIAL REGIONAL HEART CENTER
Order Date: 2026-04-10    Time: 09:15 EST
Patient: Synthetic Patient Med-Exp-01    MRN: PAT-849201
DOB: 1962-08-14    Age: 63    Gender: Male
Prescriber: Dr. Elena Rostova, MD (NPI: 1092847192)
Clinic Tel: (555) 432-8901    Fax: (555) 432-8902

Prescribed Medications:

Order #1:
Medication: Atorvastatin 40 mg oral tablet
Sig: Take 1 tablet by mouth once daily at bedtime
Dispense: 90 tablets    Refills: 3

Order #2:
Medication: Metoprolol Succinate 50 mg oral tablet
Sig: Take 1 tablet PO daily in the morning
Dispense: 90 tablets    Refills: 3
""",
        "claims": [
            # Order 1
            {"field_type": "med.name", "value": "Atorvastatin", "evidence_text": "Atorvastatin"},
            {"field_type": "med.dose", "value": "40 mg", "value_numeric": 40.0, "unit_raw": "mg", "evidence_text": "40 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            # Order 2
            {"field_type": "med.name", "value": "Metoprolol Succinate", "evidence_text": "Metoprolol Succinate"},
            {"field_type": "med.dose", "value": "50 mg", "value_numeric": 50.0, "unit_raw": "mg", "evidence_text": "50 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "MED_EXP_02",
        "category": "structured_medication",
        "specialty": "Endocrinology",
        "title": "Diabetes Care Prescription Pad",
        "format": "prescription_pad",
        "features": ["retail_order", "standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL PRESCRIPTION ORDER - NOT PHI - FOR EVALUATION ONLY]
METRO ENDOCRINE & METABOLISM ASSOCIATES
Date: 2026-04-12    Order ID: ORD-99214
Patient Name: Synthetic Patient Med-Exp-02
MRN: PAT-719302    DOB: 1975-11-20    Age: 50
Provider: Dr. Marcus Vance, MD    NPI: 1849204817

Prescribed Discharge Medications:

Order #1:
Medication: Metformin 1000 mg oral tablet
Sig: Take 1 tablet by mouth twice daily with meals
Dispense: 180 tablets    Refills: 2

Order #2:
Medication: Semaglutide 0.5 mg subcutaneous solution
Sig: Inject 0.5 mg subcutaneous once weekly
Dispense: 1 pen (2 mL)    Refills: 5
""",
        "claims": [
            # Order 1
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            {"field_type": "med.dose", "value": "1000 mg", "value_numeric": 1000.0, "unit_raw": "mg", "evidence_text": "1000 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            # Order 2
            {"field_type": "med.name", "value": "Semaglutide", "evidence_text": "Semaglutide"},
            {"field_type": "med.dose", "value": "0.5 mg", "value_numeric": 0.5, "unit_raw": "mg", "evidence_text": "0.5 mg"},
            {"field_type": "med.route", "value": "subcutaneous", "evidence_text": "subcutaneous"},
            {"field_type": "med.frequency", "value": "once weekly", "evidence_text": "once weekly"},
        ],
    },
    {
        "doc_id": "MED_EXP_03",
        "category": "structured_medication",
        "specialty": "Psychiatry",
        "title": "Behavioral Health E-Prescription Confirmation",
        "format": "prescription_pad",
        "features": ["retail_order", "standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL PRESCRIPTION ORDER - NOT PHI - FOR EVALUATION ONLY]
COMMUNITY BEHAVIORAL HEALTH CLINIC
Date of Order: 2026-04-15    Time: 14:22
Patient: Synthetic Patient Med-Exp-03    MRN: PAT-618401
DOB: 1991-03-05    Age: 35    Sex: Female
Prescriber: Dr. Sarah Lin, MD (NPI: 1493028172)
Phone: 555-019-3829    DEA: BL1948291

PRESCRIPTION DETAILS:

Rx 1:
Drug: Escitalopram 10 mg oral tablet
Sig: Take 1 tablet oral once daily in the morning
Dispense Quantity: 30    Refills: 4

Rx 2:
Drug: Hydroxyzine 25 mg oral capsule
Sig: Take 1 capsule PO every 8 hours as needed for anxiety
Dispense Quantity: 60    Refills: 2
""",
        "claims": [
            # Rx 1
            {"field_type": "med.name", "value": "Escitalopram", "evidence_text": "Escitalopram"},
            {"field_type": "med.dose", "value": "10 mg", "value_numeric": 10.0, "unit_raw": "mg", "evidence_text": "10 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            # Rx 2
            {"field_type": "med.name", "value": "Hydroxyzine", "evidence_text": "Hydroxyzine"},
            {"field_type": "med.dose", "value": "25 mg", "value_numeric": 25.0, "unit_raw": "mg", "evidence_text": "25 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "every 8 hours", "evidence_text": "every 8 hours"},
        ],
    },
    {
        "doc_id": "MED_EXP_04",
        "category": "structured_medication",
        "specialty": "Infectious Disease",
        "title": "Outpatient Antimicrobial Order",
        "format": "prescription_pad",
        "features": ["retail_order", "duration_claims", "header_noise"],
        "text": """[SYNTHETIC CLINICAL PRESCRIPTION ORDER - NOT PHI - FOR EVALUATION ONLY]
ID ASSOCIATES & TRAVEL MEDICINE
Order Date: 2026-04-18    Encounter: ENC-48192
Patient: Synthetic Patient Med-Exp-04    MRN: PAT-552910
DOB: 1984-07-29    Age: 41
Prescriber: Dr. Nathan Brooks, MD    NPI: 1928374650

Prescribed Medications:

Order #1:
Medication: Amoxicillin-Clavulanate 875 mg oral tablet
Instructions (Sig): Take 1 tablet by mouth twice daily for 10 days
Dispense: 20 tablets    Refills: 0

Order #2:
Medication: Doxycycline 100 mg oral capsule
Instructions (Sig): Take 1 capsule PO BID for 7 days
Dispense: 14 capsules    Refills: 0
""",
        "claims": [
            # Order 1
            {"field_type": "med.name", "value": "Amoxicillin-Clavulanate", "evidence_text": "Amoxicillin-Clavulanate"},
            {"field_type": "med.dose", "value": "875 mg", "value_numeric": 875.0, "unit_raw": "mg", "evidence_text": "875 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.duration", "value": "for 10 days", "evidence_text": "for 10 days"},
            # Order 2
            {"field_type": "med.name", "value": "Doxycycline", "evidence_text": "Doxycycline"},
            {"field_type": "med.dose", "value": "100 mg", "value_numeric": 100.0, "unit_raw": "mg", "evidence_text": "100 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "BID", "evidence_text": "BID"},
            {"field_type": "med.duration", "value": "for 7 days", "evidence_text": "for 7 days"},
        ],
    },
    {
        "doc_id": "MED_EXP_05",
        "category": "structured_medication",
        "specialty": "Pulmonology",
        "title": "Respiratory Inhaler Prescription Order",
        "format": "prescription_pad",
        "features": ["retail_order", "inhaler_routes", "header_noise"],
        "text": """[SYNTHETIC CLINICAL PRESCRIPTION ORDER - NOT PHI - FOR EVALUATION ONLY]
PULMONARY DISEASE & ALLERGY SPECIALISTS
Date: 2026-04-20    Order Number: 902148
Patient: Synthetic Patient Med-Exp-05    MRN: PAT-401928
DOB: 1968-12-01    Age: 57    Gender: Male
Physician: Dr. Rachel Green, MD    NPI: 1029384756

Prescriptions:

Order #1:
Medication: Fluticasone Propionate 110 mcg inhalation aerosol
Sig: Inhale 2 puffs inhalation twice daily
Dispense: 1 inhaler (12 g)    Refills: 3

Order #2:
Medication: Albuterol Sulfate 90 mcg inhalation aerosol
Sig: Inhale 1 to 2 puffs inhalation every 4 hours as needed for shortness of breath
Dispense: 1 canister (8.5 g)    Refills: 5
""",
        "claims": [
            # Order 1
            {"field_type": "med.name", "value": "Fluticasone Propionate", "evidence_text": "Fluticasone Propionate"},
            {"field_type": "med.dose", "value": "110 mcg", "value_numeric": 110.0, "unit_raw": "mcg", "evidence_text": "110 mcg"},
            {"field_type": "med.route", "value": "inhalation", "evidence_text": "inhalation"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            # Order 2
            {"field_type": "med.name", "value": "Albuterol Sulfate", "evidence_text": "Albuterol Sulfate"},
            {"field_type": "med.dose", "value": "90 mcg", "value_numeric": 90.0, "unit_raw": "mcg", "evidence_text": "90 mcg"},
            {"field_type": "med.route", "value": "inhalation", "evidence_text": "inhalation"},
            {"field_type": "med.frequency", "value": "every 4 hours", "evidence_text": "every 4 hours"},
        ],
    },
    {
        "doc_id": "MED_EXP_06",
        "category": "structured_medication",
        "specialty": "Neurology",
        "title": "Neurology Clinic Prescription - Missing Section Headers",
        "format": "prescription_pad",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL PRESCRIPTION ORDER - NOT PHI - FOR EVALUATION ONLY]
COMPREHENSIVE NEUROLOGY CLINIC
Rx Slip Date: 2026-04-22
Patient: Synthetic Patient Med-Exp-06    MRN: PAT-391024
DOB: 1982-05-18    Provider: Dr. James Wilson, MD (NPI: 1293847561)

Gabapentin 300 mg oral capsule
Take 1 capsule by mouth three times daily
Quantity: 90 capsules    Refills: 2

Levetiracetam 500 mg oral tablet
Take 1 tablet PO twice daily
Quantity: 60 tablets    Refills: 3
""",
        "claims": [
            # Drug 1
            {"field_type": "med.name", "value": "Gabapentin", "evidence_text": "Gabapentin"},
            {"field_type": "med.dose", "value": "300 mg", "value_numeric": 300.0, "unit_raw": "mg", "evidence_text": "300 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "three times daily", "evidence_text": "three times daily"},
            # Drug 2
            {"field_type": "med.name", "value": "Levetiracetam", "evidence_text": "Levetiracetam"},
            {"field_type": "med.dose", "value": "500 mg", "value_numeric": 500.0, "unit_raw": "mg", "evidence_text": "500 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
        ],
    },
    {
        "doc_id": "MED_EXP_07",
        "category": "structured_medication",
        "specialty": "Gastroenterology",
        "title": "GI Clinic Prescription - Irregular Spacing",
        "format": "prescription_pad",
        "features": ["irregular_spacing", "header_noise"],
        "text": """[SYNTHETIC CLINICAL PRESCRIPTION ORDER - NOT PHI - FOR EVALUATION ONLY]
GASTROENTEROLOGY ASSOCIATES
DATE: 2026-04-25        ACCESSION: RX-84920
PATIENT: Synthetic Patient Med-Exp-07        MRN: PAT-294018
DOB: 1971-09-09    AGE: 54    SEX: M
PRESCRIBER: Dr. Thomas Miller, MD (NPI: 1948273615)

PRESCRIBED MEDICATIONS:

Order #1:
    Medication:   Omeprazole 40 mg oral delayed-release capsule
    Sig:          Take 1 capsule oral once daily 30 minutes before breakfast
    Dispense:     30 capsules    Refills: 5

Order #2:
    Medication:   Sucralfate 1 g oral tablet
    Sig:          Take 1 tablet PO four times daily 1 hour before meals and at bedtime for 14 days
    Dispense:     56 tablets    Refills: 0
""",
        "claims": [
            # Order 1
            {"field_type": "med.name", "value": "Omeprazole", "evidence_text": "Omeprazole"},
            {"field_type": "med.dose", "value": "40 mg", "value_numeric": 40.0, "unit_raw": "mg", "evidence_text": "40 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            # Order 2
            {"field_type": "med.name", "value": "Sucralfate", "evidence_text": "Sucralfate"},
            {"field_type": "med.dose", "value": "1 g", "value_numeric": 1.0, "unit_raw": "g", "evidence_text": "1 g"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "four times daily", "evidence_text": "four times daily"},
            {"field_type": "med.duration", "value": "for 14 days", "evidence_text": "for 14 days"},
        ],
    },
    {
        "doc_id": "MED_EXP_08",
        "category": "structured_medication",
        "specialty": "Rheumatology",
        "title": "Rheumatology Rx - Simulated OCR Noise",
        "format": "prescription_pad",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL PRESCRIPTION ORDER - NOT PHI - FOR EVALUATION ONLY]
ARTHRITIS & RHEUMATOLOGY CLINIC
Date: 2O26-O4-28    Order: RX-19482
Patient: Synthetic Patient Med-Exp-08    MRN: PAT-184920
DOB: 1965-02-14    Physician: Dr. Sandra Hayes, MD (NPI: 1849203716)

Prescriptions:

Order #1:
Medication: Meloxicam 15 mg oral tablet
Sig: Take 1 tablet by mouth daily with meals
Dispense: 30 tablets    Refills: 3

Order #2:
Medication: Methotrexate 15 mg oral tablet
Sig: Take 15 mg PO once weekly on Mondays
Dispense: 12 tablets    Refills: 2
""",
        "claims": [
            # Order 1
            {"field_type": "med.name", "value": "Meloxicam", "evidence_text": "Meloxicam"},
            {"field_type": "med.dose", "value": "15 mg", "value_numeric": 15.0, "unit_raw": "mg", "evidence_text": "15 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
            # Order 2
            {"field_type": "med.name", "value": "Methotrexate", "evidence_text": "Methotrexate"},
            {"field_type": "med.dose", "value": "15 mg", "value_numeric": 15.0, "unit_raw": "mg", "evidence_text": "15 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once weekly", "evidence_text": "once weekly"},
        ],
    },
]
