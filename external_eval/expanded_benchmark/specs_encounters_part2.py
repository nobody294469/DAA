"""Expanded Benchmark - Clinical Encounter Specs Part 2 (16 documents).
Infectious Disease, Oncology, Gastroenterology, Neurology (4 archetypes each).

Authority: User Request / Expanded Synthetic Benchmark
Location: external_eval/expanded_benchmark/specs_encounters_part2.py
"""
from __future__ import annotations

ENCOUNTERS_PART2 = [
    # -------------------------------------------------------------
    # INFECTIOUS DISEASE
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_INFECT_01",
        "category": "narrative_encounter",
        "specialty": "Infectious Disease",
        "title": "Bacteremia & Cellulitis Inpatient Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
INFECTIOUS DISEASE CONSULTATION
Date: 2026-04-21    Patient: Synthetic Patient Infect-Exp-01
MRN: PAT-101928    DOB: 1970-08-15    Dr. Angela Martin (NPI: 1849204920)

LABORATORY VALUES:
Serum Vancomycin trough level was 16.5 ug/mL (THERAPEUTIC).
C-Reactive Protein was 45 mg/L (HIGH).
Erythrocyte Sedimentation Rate was 52 mm/hr (HIGH).
Serum Creatinine was 0.9 mg/dL.

ANTIMICROBIAL REGIMEN:
Vancomycin 1250 mg IV every 12 hours.
Cefazolin 2 g IV every 8 hours.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Vancomycin", "evidence_text": "Vancomycin"},
            {"field_type": "lab.value", "value": "16.5", "value_numeric": 16.5, "evidence_text": "16.5"},
            {"field_type": "lab.unit", "value": "ug/mL", "evidence_text": "ug/mL"},
            {"field_type": "lab.test_name", "value": "C-Reactive Protein", "evidence_text": "C-Reactive Protein"},
            {"field_type": "lab.value", "value": "45", "value_numeric": 45.0, "evidence_text": "45"},
            {"field_type": "lab.unit", "value": "mg/L", "evidence_text": "mg/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Erythrocyte Sedimentation Rate", "evidence_text": "Erythrocyte Sedimentation Rate"},
            {"field_type": "lab.value", "value": "52", "value_numeric": 52.0, "evidence_text": "52"},
            {"field_type": "lab.unit", "value": "mm/hr", "evidence_text": "mm/hr"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "0.9", "value_numeric": 0.9, "evidence_text": "0.9"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Vancomycin", "evidence_text": "Vancomycin"},
            {"field_type": "med.dose", "value": "1250 mg", "value_numeric": 1250.0, "unit_raw": "mg", "evidence_text": "1250 mg"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "every 12 hours", "evidence_text": "every 12 hours"},
            {"field_type": "med.name", "value": "Cefazolin", "evidence_text": "Cefazolin"},
            {"field_type": "med.dose", "value": "2 g", "value_numeric": 2.0, "unit_raw": "g", "evidence_text": "2 g"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "every 8 hours", "evidence_text": "every 8 hours"},
        ],
    },
    {
        "doc_id": "EXP_INFECT_02",
        "category": "narrative_encounter",
        "specialty": "Infectious Disease",
        "title": "HIV / Viral Hepatitis Management",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
INFECTIOUS DISEASE OUTPATIENT CLINIC
Date: 2026-04-23    Patient: Synthetic Patient Infect-Exp-02
MRN: PAT-991028    DOB: 1985-02-14    Dr. Raymond Holt (NPI: 1749204921)

IMMUNOLOGY LABS:
CD4 Count was 680 cells/uL (NORMAL).
HIV-1 RNA was < 20 copies/mL (UNDETECTABLE).
Serum ALT was 22 U/L and AST was 20 U/L.

ANTIRETROVIRAL THERAPY:
Biktarvy 50-200-25 mg oral tablet once daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "CD4 Count", "evidence_text": "CD4 Count"},
            {"field_type": "lab.value", "value": "680", "value_numeric": 680.0, "evidence_text": "680"},
            {"field_type": "lab.unit", "value": "cells/uL", "evidence_text": "cells/uL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "ALT", "evidence_text": "ALT"},
            {"field_type": "lab.value", "value": "22", "value_numeric": 22.0, "evidence_text": "22"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            {"field_type": "lab.test_name", "value": "AST", "evidence_text": "AST"},
            {"field_type": "lab.value", "value": "20", "value_numeric": 20.0, "evidence_text": "20"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            # Meds
            {"field_type": "med.name", "value": "Biktarvy", "evidence_text": "Biktarvy"},
            {"field_type": "med.dose", "value": "50-200-25 mg", "evidence_text": "50-200-25 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
    {
        "doc_id": "EXP_INFECT_03",
        "category": "narrative_encounter",
        "specialty": "Infectious Disease",
        "title": "Lyme Disease Evaluation - Missing Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
LYME & VECTOR-BORNE CLINIC
Date: 2026-04-25    Patient: Synthetic Patient Infect-Exp-03
MRN: PAT-881028    DOB: 1992-06-18    Dr. Sarah Paulson (NPI: 1649204922)

Patient presenting with erythema migrans rash and fatigue.
Serum Western Blot was POSITIVE. Platelet Count is 230 10*3/uL.
Serum ALT is 32 U/L.
Doxycycline 100 mg oral twice daily for 21 days.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Platelet Count", "evidence_text": "Platelet Count"},
            {"field_type": "lab.value", "value": "230", "value_numeric": 230.0, "evidence_text": "230"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.test_name", "value": "ALT", "evidence_text": "ALT"},
            {"field_type": "lab.value", "value": "32", "value_numeric": 32.0, "evidence_text": "32"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            # Meds
            {"field_type": "med.name", "value": "Doxycycline", "evidence_text": "Doxycycline"},
            {"field_type": "med.dose", "value": "100 mg", "value_numeric": 100.0, "unit_raw": "mg", "evidence_text": "100 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.duration", "value": "for 21 days", "evidence_text": "for 21 days"},
        ],
    },
    {
        "doc_id": "EXP_INFECT_04",
        "category": "narrative_encounter",
        "specialty": "Infectious Disease",
        "title": "OPAT Osteomyelitis Note - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
OPAT INFECTIOUS DISEASE SERVICE
Date: 2O26-O4-27    Patient: Synthetic Patient Infect-Exp-04
MRN: PAT-771028    DOB: 1968-09-02    Dr. Peter Parker (NPI: 1549204923)

LABORATORIES:
ESR was 38 mm/hr (HIGH).
C-Reactive Protein was 18 mg/L (HIGH).
White Blood Cell Count was 6.9 10*3/uL.

OPAT PRESCRIPTION:
Ertapenem 1 g IV once daily for 4 weeks.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "ESR", "evidence_text": "ESR"},
            {"field_type": "lab.value", "value": "38", "value_numeric": 38.0, "evidence_text": "38"},
            {"field_type": "lab.unit", "value": "mm/hr", "evidence_text": "mm/hr"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "C-Reactive Protein", "evidence_text": "C-Reactive Protein"},
            {"field_type": "lab.value", "value": "18", "value_numeric": 18.0, "evidence_text": "18"},
            {"field_type": "lab.unit", "value": "mg/L", "evidence_text": "mg/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "White Blood Cell Count", "evidence_text": "White Blood Cell Count"},
            {"field_type": "lab.value", "value": "6.9", "value_numeric": 6.9, "evidence_text": "6.9"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            # Meds
            {"field_type": "med.name", "value": "Ertapenem", "evidence_text": "Ertapenem"},
            {"field_type": "med.dose", "value": "1 g", "value_numeric": 1.0, "unit_raw": "g", "evidence_text": "1 g"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.duration", "value": "for 4 weeks", "evidence_text": "for 4 weeks"},
        ],
    },

    # -------------------------------------------------------------
    # ONCOLOGY & HEMATOLOGY
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_ONCO_01",
        "category": "narrative_encounter",
        "specialty": "Oncology",
        "title": "Breast Cancer Adjuvant Therapy Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
CANCER CARE CENTER
Date: 2026-04-29    Patient: Synthetic Patient Onco-Exp-01
MRN: PAT-661028    DOB: 1961-05-19    Dr. Eleanor Vance (NPI: 1449204924)

LABORATORY MONITORING:
White Blood Cell Count was 5.4 10*3/uL (NORMAL).
Hemoglobin was 12.1 g/dL. Platelet Count was 185 10*3/uL.
Serum Alkaline Phosphatase was 65 IU/L.

ACTIVE REGIMEN:
Anastrozole 1 mg oral tablet once daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "White Blood Cell Count", "evidence_text": "White Blood Cell Count"},
            {"field_type": "lab.value", "value": "5.4", "value_numeric": 5.4, "evidence_text": "5.4"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "Hemoglobin", "evidence_text": "Hemoglobin"},
            {"field_type": "lab.value", "value": "12.1", "value_numeric": 12.1, "evidence_text": "12.1"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            {"field_type": "lab.test_name", "value": "Platelet Count", "evidence_text": "Platelet Count"},
            {"field_type": "lab.value", "value": "185", "value_numeric": 185.0, "evidence_text": "185"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.test_name", "value": "Alkaline Phosphatase", "evidence_text": "Alkaline Phosphatase"},
            {"field_type": "lab.value", "value": "65", "value_numeric": 65.0, "evidence_text": "65"},
            {"field_type": "lab.unit", "value": "IU/L", "evidence_text": "IU/L"},
            # Meds
            {"field_type": "med.name", "value": "Anastrozole", "evidence_text": "Anastrozole"},
            {"field_type": "med.dose", "value": "1 mg", "value_numeric": 1.0, "unit_raw": "mg", "evidence_text": "1 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
    {
        "doc_id": "EXP_ONCO_02",
        "category": "narrative_encounter",
        "specialty": "Hematology",
        "title": "Iron Deficiency Anemia Management",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
HEMATOLOGY CONSULTATION
Date: 2026-05-01    Patient: Synthetic Patient Onco-Exp-02
MRN: PAT-551028    DOB: 1988-11-23    Dr. Jonathan Crane (NPI: 1349204925)

LABORATORY FINDINGS:
Serum Ferritin was 8 ng/mL (LOW).
Hemoglobin was 9.4 g/dL (LOW).
Hematocrit was 29.5% (LOW).
MCV was 72 fL (LOW).

THERAPY:
Ferrous Sulfate 325 mg oral tablet once daily with orange juice.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Ferritin", "evidence_text": "Ferritin"},
            {"field_type": "lab.value", "value": "8", "value_numeric": 8.0, "evidence_text": "8"},
            {"field_type": "lab.unit", "value": "ng/mL", "evidence_text": "ng/mL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "Hemoglobin", "evidence_text": "Hemoglobin"},
            {"field_type": "lab.value", "value": "9.4", "value_numeric": 9.4, "evidence_text": "9.4"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "Hematocrit", "evidence_text": "Hematocrit"},
            {"field_type": "lab.value", "value": "29.5", "value_numeric": 29.5, "evidence_text": "29.5"},
            {"field_type": "lab.unit", "value": "%", "evidence_text": "%"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # Meds
            {"field_type": "med.name", "value": "Ferrous Sulfate", "evidence_text": "Ferrous Sulfate"},
            {"field_type": "med.dose", "value": "325 mg", "value_numeric": 325.0, "unit_raw": "mg", "evidence_text": "325 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
    {
        "doc_id": "EXP_ONCO_03",
        "category": "narrative_encounter",
        "specialty": "Oncology",
        "title": "Chemotherapy Follow-up - Missing Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
MEDICAL ONCOLOGY REVIEW
Date: 2026-05-03    Patient: Synthetic Patient Onco-Exp-03
MRN: PAT-441028    DOB: 1966-07-08    Dr. Victoria Newman (NPI: 1249204926)

Cycle 3 post-carboplatin monitoring.
Absolute Neutrophil Count was 0.8 10*3/uL (LOW).
Platelet Count is 95 10*3/uL (LOW).
White Blood Cell Count is 2.1 10*3/uL (LOW).
Filgrastim 300 mcg subcutaneous once daily for 3 days.
Ondansetron 8 mg PO every 8 hours as needed.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Neutrophil Count", "evidence_text": "Neutrophil Count"},
            {"field_type": "lab.value", "value": "0.8", "value_numeric": 0.8, "evidence_text": "0.8"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "Platelet Count", "evidence_text": "Platelet Count"},
            {"field_type": "lab.value", "value": "95", "value_numeric": 95.0, "evidence_text": "95"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "White Blood Cell Count", "evidence_text": "White Blood Cell Count"},
            {"field_type": "lab.value", "value": "2.1", "value_numeric": 2.1, "evidence_text": "2.1"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # Meds
            {"field_type": "med.name", "value": "Filgrastim", "evidence_text": "Filgrastim"},
            {"field_type": "med.dose", "value": "300 mcg", "value_numeric": 300.0, "unit_raw": "mcg", "evidence_text": "300 mcg"},
            {"field_type": "med.route", "value": "subcutaneous", "evidence_text": "subcutaneous"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.duration", "value": "for 3 days", "evidence_text": "for 3 days"},
            {"field_type": "med.name", "value": "Ondansetron", "evidence_text": "Ondansetron"},
            {"field_type": "med.dose", "value": "8 mg", "value_numeric": 8.0, "unit_raw": "mg", "evidence_text": "8 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "every 8 hours", "evidence_text": "every 8 hours"},
        ],
    },
    {
        "doc_id": "EXP_ONCO_04",
        "category": "narrative_encounter",
        "specialty": "Hematology",
        "title": "Multiple Myeloma Note - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
MYELOMA SPECIALTY CLINIC
Date: 2O26-O5-O5    Patient: Synthetic Patient Onco-Exp-04
MRN: PAT-331028    DOB: 1953-10-14    Dr. Gregory House (NPI: 1149204927)

LABS:
Serum Calcium was 10.8 mg/dL (HIGH).
Serum Creatinine was 1.5 mg/dL (HIGH).
Hemoglobin was 10.2 g/dL (LOW).

MEDS:
Lenalidomide 25 mg oral once daily for 21 days.
Dexamethasone 20 mg PO once weekly.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Calcium", "evidence_text": "Calcium"},
            {"field_type": "lab.value", "value": "10.8", "value_numeric": 10.8, "evidence_text": "10.8"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "1.5", "value_numeric": 1.5, "evidence_text": "1.5"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Hemoglobin", "evidence_text": "Hemoglobin"},
            {"field_type": "lab.value", "value": "10.2", "value_numeric": 10.2, "evidence_text": "10.2"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # Meds
            {"field_type": "med.name", "value": "Lenalidomide", "evidence_text": "Lenalidomide"},
            {"field_type": "med.dose", "value": "25 mg", "value_numeric": 25.0, "unit_raw": "mg", "evidence_text": "25 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.duration", "value": "for 21 days", "evidence_text": "for 21 days"},
            {"field_type": "med.name", "value": "Dexamethasone", "evidence_text": "Dexamethasone"},
            {"field_type": "med.dose", "value": "20 mg", "value_numeric": 20.0, "unit_raw": "mg", "evidence_text": "20 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once weekly", "evidence_text": "once weekly"},
        ],
    },

    # -------------------------------------------------------------
    # GASTROENTEROLOGY & HEPATOLOGY
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_GASTRO_01",
        "category": "narrative_encounter",
        "specialty": "Gastroenterology",
        "title": "Cirrhosis Outpatient Clinic Review",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
HEPATOLOGY OUTPATIENT CLINIC
Date: 2026-05-07    Patient: Synthetic Patient Gastro-Exp-01
MRN: PAT-221028    DOB: 1964-04-12    Dr. Daniel Cho (NPI: 1049204928)

LABORATORY RESULTS:
Serum Total Bilirubin was 2.8 mg/dL (HIGH).
Serum Albumin was 2.7 g/dL (LOW).
INR was 1.5 (HIGH). Platelet Count was 78 10*3/uL (LOW).
Serum Sodium was 132 mmol/L (LOW).

MEDICATION ORDERS:
Spironolactone 100 mg oral tablet once daily in the morning.
Furosemide 40 mg PO once daily.
Lactulose 20 g by mouth three times daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Total Bilirubin", "evidence_text": "Total Bilirubin"},
            {"field_type": "lab.value", "value": "2.8", "value_numeric": 2.8, "evidence_text": "2.8"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Albumin", "evidence_text": "Albumin"},
            {"field_type": "lab.value", "value": "2.7", "value_numeric": 2.7, "evidence_text": "2.7"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "INR", "evidence_text": "INR"},
            {"field_type": "lab.value", "value": "1.5", "value_numeric": 1.5, "evidence_text": "1.5"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Platelet Count", "evidence_text": "Platelet Count"},
            {"field_type": "lab.value", "value": "78", "value_numeric": 78.0, "evidence_text": "78"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # Meds
            {"field_type": "med.name", "value": "Spironolactone", "evidence_text": "Spironolactone"},
            {"field_type": "med.dose", "value": "100 mg", "value_numeric": 100.0, "unit_raw": "mg", "evidence_text": "100 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Furosemide", "evidence_text": "Furosemide"},
            {"field_type": "med.dose", "value": "40 mg", "value_numeric": 40.0, "unit_raw": "mg", "evidence_text": "40 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Lactulose", "evidence_text": "Lactulose"},
            {"field_type": "med.dose", "value": "20 g", "value_numeric": 20.0, "unit_raw": "g", "evidence_text": "20 g"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "three times daily", "evidence_text": "three times daily"},
        ],
    },
    {
        "doc_id": "EXP_GASTRO_02",
        "category": "narrative_encounter",
        "specialty": "Gastroenterology",
        "title": "Crohn's Disease Follow-up Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
INFLAMMATORY BOWEL DISEASE CENTER
Date: 2026-05-09    Patient: Synthetic Patient Gastro-Exp-02
MRN: PAT-111028    DOB: 1994-01-30    Dr. Rachel Bloom (NPI: 1948204929)

DIAGNOSTIC WORKUP:
Fecal Calprotectin was 420 ug/g (HIGH).
C-Reactive Protein was 14 mg/L (HIGH).
Hemoglobin was 11.2 g/dL (LOW).

MAINTENANCE THERAPY:
Infliximab 5 mg/kg IV infusion every 8 weeks.
Azathioprine 100 mg oral tablet once daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Calprotectin", "evidence_text": "Calprotectin"},
            {"field_type": "lab.value", "value": "420", "value_numeric": 420.0, "evidence_text": "420"},
            {"field_type": "lab.unit", "value": "ug/g", "evidence_text": "ug/g"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "C-Reactive Protein", "evidence_text": "C-Reactive Protein"},
            {"field_type": "lab.value", "value": "14", "value_numeric": 14.0, "evidence_text": "14"},
            {"field_type": "lab.unit", "value": "mg/L", "evidence_text": "mg/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Hemoglobin", "evidence_text": "Hemoglobin"},
            {"field_type": "lab.value", "value": "11.2", "value_numeric": 11.2, "evidence_text": "11.2"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # Meds
            {"field_type": "med.name", "value": "Infliximab", "evidence_text": "Infliximab"},
            {"field_type": "med.dose", "value": "5 mg/kg", "evidence_text": "5 mg/kg"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "every 8 weeks", "evidence_text": "every 8 weeks"},
            {"field_type": "med.name", "value": "Azathioprine", "evidence_text": "Azathioprine"},
            {"field_type": "med.dose", "value": "100 mg", "value_numeric": 100.0, "unit_raw": "mg", "evidence_text": "100 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
    {
        "doc_id": "EXP_GASTRO_03",
        "category": "narrative_encounter",
        "specialty": "Gastroenterology",
        "title": "Acute Pancreatitis Note - Missing Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
INPATIENT GASTROENTEROLOGY NOTE
Date: 2026-05-11    Patient: Synthetic Patient Gastro-Exp-03
MRN: PAT-992018    DOB: 1979-09-17    Dr. Mark Sloan (NPI: 1849204930)

Hospital day 2 for acute biliary pancreatitis. Epigastric pain is resolving.
Serum Lipase was 480 U/L (HIGH). Serum Amylase was 210 U/L (HIGH).
Serum Triglycerides were 145 mg/dL. Calcium was 8.9 mg/dL.
Tramadol 50 mg PO every 6 hours as needed for moderate pain.
Pantoprazole 40 mg IV daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Lipase", "evidence_text": "Lipase"},
            {"field_type": "lab.value", "value": "480", "value_numeric": 480.0, "evidence_text": "480"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Amylase", "evidence_text": "Amylase"},
            {"field_type": "lab.value", "value": "210", "value_numeric": 210.0, "evidence_text": "210"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Calcium", "evidence_text": "Calcium"},
            {"field_type": "lab.value", "value": "8.9", "value_numeric": 8.9, "evidence_text": "8.9"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Tramadol", "evidence_text": "Tramadol"},
            {"field_type": "med.dose", "value": "50 mg", "value_numeric": 50.0, "unit_raw": "mg", "evidence_text": "50 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "every 6 hours", "evidence_text": "every 6 hours"},
            {"field_type": "med.name", "value": "Pantoprazole", "evidence_text": "Pantoprazole"},
            {"field_type": "med.dose", "value": "40 mg", "value_numeric": 40.0, "unit_raw": "mg", "evidence_text": "40 mg"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "EXP_GASTRO_04",
        "category": "narrative_encounter",
        "specialty": "Gastroenterology",
        "title": "Peptic Ulcer Note - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
ENDOSCOPY CLINIC FOLLOWUP
Date: 2O26-O5-14    Patient: Synthetic Patient Gastro-Exp-04
MRN: PAT-882018    DOB: 1972-12-04    Dr. Owen Hunt (NPI: 1749204931)

LABORATORY DATA:
Hemoglobin was 13.5 g/dL (NORMAL).
Serum Creatinine was 0.8 mg/dL.

MEDICATIONS:
Famotidine 20 mg oral twice daily for 8 weeks.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Hemoglobin", "evidence_text": "Hemoglobin"},
            {"field_type": "lab.value", "value": "13.5", "value_numeric": 13.5, "evidence_text": "13.5"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "0.8", "value_numeric": 0.8, "evidence_text": "0.8"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Famotidine", "evidence_text": "Famotidine"},
            {"field_type": "med.dose", "value": "20 mg", "value_numeric": 20.0, "unit_raw": "mg", "evidence_text": "20 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.duration", "value": "for 8 weeks", "evidence_text": "for 8 weeks"},
        ],
    },

    # -------------------------------------------------------------
    # NEUROLOGY
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_NEURO_01",
        "category": "narrative_encounter",
        "specialty": "Neurology",
        "title": "Epilepsy Clinic Management Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
COMPREHENSIVE EPILEPSY CLINIC
Date: 2026-05-16    Patient: Synthetic Patient Neuro-Exp-01
MRN: PAT-772018    DOB: 1991-03-22    Dr. Amelia Shepherd (NPI: 1649204932)

THERAPEUTIC DRUG MONITORING:
Serum Levetiracetam was 28 ug/mL (THERAPEUTIC).
Serum Lamotrigine was 5.4 ug/mL (THERAPEUTIC).
Serum Sodium was 138 mmol/L.

ANTIEPILEPTIC REGIMEN:
Levetiracetam 750 mg oral tablet twice daily.
Lamotrigine 100 mg PO twice daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Levetiracetam", "evidence_text": "Levetiracetam"},
            {"field_type": "lab.value", "value": "28", "value_numeric": 28.0, "evidence_text": "28"},
            {"field_type": "lab.unit", "value": "ug/mL", "evidence_text": "ug/mL"},
            {"field_type": "lab.test_name", "value": "Lamotrigine", "evidence_text": "Lamotrigine"},
            {"field_type": "lab.value", "value": "5.4", "value_numeric": 5.4, "evidence_text": "5.4"},
            {"field_type": "lab.unit", "value": "ug/mL", "evidence_text": "ug/mL"},
            {"field_type": "lab.test_name", "value": "Sodium", "evidence_text": "Sodium"},
            {"field_type": "lab.value", "value": "138", "value_numeric": 138.0, "evidence_text": "138"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            # Meds
            {"field_type": "med.name", "value": "Levetiracetam", "evidence_text": "Levetiracetam"},
            {"field_type": "med.dose", "value": "750 mg", "value_numeric": 750.0, "unit_raw": "mg", "evidence_text": "750 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Lamotrigine", "evidence_text": "Lamotrigine"},
            {"field_type": "med.dose", "value": "100 mg", "value_numeric": 100.0, "unit_raw": "mg", "evidence_text": "100 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
        ],
    },
    {
        "doc_id": "EXP_NEURO_02",
        "category": "narrative_encounter",
        "specialty": "Neurology",
        "title": "Ischemic Stroke Prevention Follow-up",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
STROKE PREVENTION CLINIC
Date: 2026-05-18    Patient: Synthetic Patient Neuro-Exp-02
MRN: PAT-662018    DOB: 1958-08-14    Dr. Meredith Grey (NPI: 1549204933)

LABORATORY RESULTS:
LDL Cholesterol was 64 mg/dL (OPTIMAL).
Total Cholesterol was 142 mg/dL.
Hemoglobin A1c was 5.7% (NORMAL).

SECONDARY STROKE PREVENTION:
Clopidogrel 75 mg oral tablet once daily.
Rosuvastatin 20 mg PO once daily in the evening.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "LDL Cholesterol", "evidence_text": "LDL Cholesterol"},
            {"field_type": "lab.value", "value": "64", "value_numeric": 64.0, "evidence_text": "64"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.test_name", "value": "Total Cholesterol", "evidence_text": "Total Cholesterol"},
            {"field_type": "lab.value", "value": "142", "value_numeric": 142.0, "evidence_text": "142"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.test_name", "value": "Hemoglobin A1c", "evidence_text": "Hemoglobin A1c"},
            {"field_type": "lab.value", "value": "5.7", "value_numeric": 5.7, "evidence_text": "5.7"},
            {"field_type": "lab.unit", "value": "%", "evidence_text": "%"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            # Meds
            {"field_type": "med.name", "value": "Clopidogrel", "evidence_text": "Clopidogrel"},
            {"field_type": "med.dose", "value": "75 mg", "value_numeric": 75.0, "unit_raw": "mg", "evidence_text": "75 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Rosuvastatin", "evidence_text": "Rosuvastatin"},
            {"field_type": "med.dose", "value": "20 mg", "value_numeric": 20.0, "unit_raw": "mg", "evidence_text": "20 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
    {
        "doc_id": "EXP_NEURO_03",
        "category": "narrative_encounter",
        "specialty": "Neurology",
        "title": "Parkinson's Disease Note - Missing Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
MOVEMENT DISORDERS PROGRAM
Date: 2026-05-20    Patient: Synthetic Patient Neuro-Exp-03
MRN: PAT-552018    DOB: 1950-10-10    Dr. Derek Shepherd (NPI: 1449204934)

Follow-up idiopathic Parkinson disease. Tremor well controlled.
Recent basic metabolic panel shows Potassium 4.2 mmol/L.
Serum Creatinine is 0.9 mg/dL and BUN is 16 mg/dL.
Carbidopa-Levodopa 25-100 mg oral tablet three times daily.
Pramipexole 0.5 mg PO three times daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.2", "value_numeric": 4.2, "evidence_text": "4.2"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "0.9", "value_numeric": 0.9, "evidence_text": "0.9"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.test_name", "value": "BUN", "evidence_text": "BUN"},
            {"field_type": "lab.value", "value": "16", "value_numeric": 16.0, "evidence_text": "16"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Carbidopa-Levodopa", "evidence_text": "Carbidopa-Levodopa"},
            {"field_type": "med.dose", "value": "25-100 mg", "evidence_text": "25-100 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "three times daily", "evidence_text": "three times daily"},
            {"field_type": "med.name", "value": "Pramipexole", "evidence_text": "Pramipexole"},
            {"field_type": "med.dose", "value": "0.5 mg", "value_numeric": 0.5, "unit_raw": "mg", "evidence_text": "0.5 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "three times daily", "evidence_text": "three times daily"},
        ],
    },
    {
        "doc_id": "EXP_NEURO_04",
        "category": "narrative_encounter",
        "specialty": "Neurology",
        "title": "Multiple Sclerosis Note - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
MULTIPLE SCLEROSIS CENTER
Date: 2O26-O5-22    Patient: Synthetic Patient Neuro-Exp-04
MRN: PAT-442018    DOB: 1986-06-25    Dr. Callie Torres (NPI: 1349204935)

LABORATORY FINDINGS:
White Blood Cell Count was 6.2 10*3/uL (NORMAL).
Serum ALT was 24 U/L.

MEDICATIONS:
Glatiramer Acetate 20 mg subcutaneous once daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "White Blood Cell Count", "evidence_text": "White Blood Cell Count"},
            {"field_type": "lab.value", "value": "6.2", "value_numeric": 6.2, "evidence_text": "6.2"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "ALT", "evidence_text": "ALT"},
            {"field_type": "lab.value", "value": "24", "value_numeric": 24.0, "evidence_text": "24"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            # Meds
            {"field_type": "med.name", "value": "Glatiramer Acetate", "evidence_text": "Glatiramer Acetate"},
            {"field_type": "med.dose", "value": "20 mg", "value_numeric": 20.0, "unit_raw": "mg", "evidence_text": "20 mg"},
            {"field_type": "med.route", "value": "subcutaneous", "evidence_text": "subcutaneous"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
]
