"""Expanded Benchmark - Clinical Encounter Specs Part 3 (12 documents).
Rheumatology, Psychiatry, Emergency Medicine (4 archetypes each).

Authority: User Request / Expanded Synthetic Benchmark
Location: external_eval/expanded_benchmark/specs_encounters_part3.py
"""
from __future__ import annotations

ENCOUNTERS_PART3 = [
    # -------------------------------------------------------------
    # RHEUMATOLOGY
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_RHEUM_01",
        "category": "narrative_encounter",
        "specialty": "Rheumatology",
        "title": "Rheumatoid Arthritis Outpatient Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
ARTHRITIS & RHEUMATOLOGY CENTER
Date: 2026-05-24    Patient: Synthetic Patient Rheum-Exp-01
MRN: PAT-332018    DOB: 1974-05-11    Dr. Jackson Avery (NPI: 1249204936)

DIAGNOSTIC WORKUP:
C-Reactive Protein was 22 mg/L (HIGH).
Erythrocyte Sedimentation Rate was 44 mm/hr (HIGH).
Rheumatoid Factor was 68 IU/mL (HIGH).
Serum ALT was 21 U/L.

IMMUNOMODULATORY REGIMEN:
Methotrexate 15 mg oral tablet once weekly on Sundays.
Folic Acid 1 mg PO once daily.
Adalimumab 40 mg subcutaneous every 2 weeks.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "C-Reactive Protein", "evidence_text": "C-Reactive Protein"},
            {"field_type": "lab.value", "value": "22", "value_numeric": 22.0, "evidence_text": "22"},
            {"field_type": "lab.unit", "value": "mg/L", "evidence_text": "mg/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Erythrocyte Sedimentation Rate", "evidence_text": "Erythrocyte Sedimentation Rate"},
            {"field_type": "lab.value", "value": "44", "value_numeric": 44.0, "evidence_text": "44"},
            {"field_type": "lab.unit", "value": "mm/hr", "evidence_text": "mm/hr"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Rheumatoid Factor", "evidence_text": "Rheumatoid Factor"},
            {"field_type": "lab.value", "value": "68", "value_numeric": 68.0, "evidence_text": "68"},
            {"field_type": "lab.unit", "value": "IU/mL", "evidence_text": "IU/mL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # Meds
            {"field_type": "med.name", "value": "Methotrexate", "evidence_text": "Methotrexate"},
            {"field_type": "med.dose", "value": "15 mg", "value_numeric": 15.0, "unit_raw": "mg", "evidence_text": "15 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once weekly", "evidence_text": "once weekly"},
            {"field_type": "med.name", "value": "Folic Acid", "evidence_text": "Folic Acid"},
            {"field_type": "med.dose", "value": "1 mg", "value_numeric": 1.0, "unit_raw": "mg", "evidence_text": "1 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Adalimumab", "evidence_text": "Adalimumab"},
            {"field_type": "med.dose", "value": "40 mg", "value_numeric": 40.0, "unit_raw": "mg", "evidence_text": "40 mg"},
            {"field_type": "med.route", "value": "subcutaneous", "evidence_text": "subcutaneous"},
            {"field_type": "med.frequency", "value": "every 2 weeks", "evidence_text": "every 2 weeks"},
        ],
    },
    {
        "doc_id": "EXP_RHEUM_02",
        "category": "narrative_encounter",
        "specialty": "Rheumatology",
        "title": "Systemic Lupus Erythematosus Progress Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
LUPUS CLINIC PROGRESS NOTE
Date: 2026-05-26    Patient: Synthetic Patient Rheum-Exp-02
MRN: PAT-222018    DOB: 1989-10-04    Dr. April Kepner (NPI: 1149204937)

SEROLOGIC MONITORING:
Complement C3 was 68 mg/dL (LOW).
Complement C4 was 11 mg/dL (LOW).
Platelet Count was 165 10*3/uL (NORMAL).
Serum Creatinine was 0.8 mg/dL.

ACTIVE MEDICATIONS:
Hydroxychloroquine 200 mg oral tablet once daily.
Prednisone 5 mg PO daily in the morning.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Complement C3", "evidence_text": "Complement C3"},
            {"field_type": "lab.value", "value": "68", "value_numeric": 68.0, "evidence_text": "68"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "Complement C4", "evidence_text": "Complement C4"},
            {"field_type": "lab.value", "value": "11", "value_numeric": 11.0, "evidence_text": "11"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "0.8", "value_numeric": 0.8, "evidence_text": "0.8"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Hydroxychloroquine", "evidence_text": "Hydroxychloroquine"},
            {"field_type": "med.dose", "value": "200 mg", "value_numeric": 200.0, "unit_raw": "mg", "evidence_text": "200 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Prednisone", "evidence_text": "Prednisone"},
            {"field_type": "med.dose", "value": "5 mg", "value_numeric": 5.0, "unit_raw": "mg", "evidence_text": "5 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "EXP_RHEUM_03",
        "category": "narrative_encounter",
        "specialty": "Rheumatology",
        "title": "Gouty Arthritis Note - Missing Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
GOUT MANAGEMENT CLINIC
Date: 2026-05-28    Patient: Synthetic Patient Rheum-Exp-03
MRN: PAT-112018    DOB: 1962-07-19    Dr. Arizona Robbins (NPI: 1049204938)

Follow-up chronic tophaceous gout.
Serum Uric Acid was 5.4 mg/dL (in goal range < 6.0).
Serum Creatinine is 1.1 mg/dL and eGFR is 74 mL/min.
Allopurinol 300 mg PO once daily.
Colchicine 0.6 mg by mouth daily for flare prophylaxis.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Uric Acid", "evidence_text": "Uric Acid"},
            {"field_type": "lab.value", "value": "5.4", "value_numeric": 5.4, "evidence_text": "5.4"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "1.1", "value_numeric": 1.1, "evidence_text": "1.1"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Allopurinol", "evidence_text": "Allopurinol"},
            {"field_type": "med.dose", "value": "300 mg", "value_numeric": 300.0, "unit_raw": "mg", "evidence_text": "300 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Colchicine", "evidence_text": "Colchicine"},
            {"field_type": "med.dose", "value": "0.6 mg", "value_numeric": 0.6, "unit_raw": "mg", "evidence_text": "0.6 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "EXP_RHEUM_04",
        "category": "narrative_encounter",
        "specialty": "Rheumatology",
        "title": "Ankylosing Spondylitis - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
SPONDYLOARTHRITIS CLINIC
Date: 2O26-O5-3O    Patient: Synthetic Patient Rheum-Exp-04
MRN: PAT-993018    DOB: 1983-02-17    Dr. Lexie Grey (NPI: 1948204939)

LABORATORY FINDINGS:
C-Reactive Protein was 12 mg/L (HIGH).
Hemoglobin was 14.1 g/dL.

MEDICATIONS:
Secukinumab 150 mg subcutaneous every 4 weeks.
Celecoxib 200 mg oral once daily with food.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "C-Reactive Protein", "evidence_text": "C-Reactive Protein"},
            {"field_type": "lab.value", "value": "12", "value_numeric": 12.0, "evidence_text": "12"},
            {"field_type": "lab.unit", "value": "mg/L", "evidence_text": "mg/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Hemoglobin", "evidence_text": "Hemoglobin"},
            {"field_type": "lab.value", "value": "14.1", "value_numeric": 14.1, "evidence_text": "14.1"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            # Meds
            {"field_type": "med.name", "value": "Secukinumab", "evidence_text": "Secukinumab"},
            {"field_type": "med.dose", "value": "150 mg", "value_numeric": 150.0, "unit_raw": "mg", "evidence_text": "150 mg"},
            {"field_type": "med.route", "value": "subcutaneous", "evidence_text": "subcutaneous"},
            {"field_type": "med.frequency", "value": "every 4 weeks", "evidence_text": "every 4 weeks"},
            {"field_type": "med.name", "value": "Celecoxib", "evidence_text": "Celecoxib"},
            {"field_type": "med.dose", "value": "200 mg", "value_numeric": 200.0, "unit_raw": "mg", "evidence_text": "200 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },

    # -------------------------------------------------------------
    # PSYCHIATRY
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_PSYCH_01",
        "category": "narrative_encounter",
        "specialty": "Psychiatry",
        "title": "Bipolar Maintenance Progress Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
ADULT PSYCHIATRIC SERVICES
Date: 2026-06-02    Patient: Synthetic Patient Psych-Exp-01
MRN: PAT-883018    DOB: 1987-12-09    Dr. Miranda Bailey (NPI: 1849204940)

MONITORING LABS:
Serum Lithium level was 0.85 mEq/L (THERAPEUTIC).
Serum TSH was 2.4 uIU/mL (NORMAL).
Serum Creatinine was 0.9 mg/dL.

PSYCHOTROPIC MEDICATIONS:
Lithium Carbonate 600 mg oral tablet twice daily.
Quetiapine 100 mg PO daily at bedtime.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Lithium", "evidence_text": "Lithium"},
            {"field_type": "lab.value", "value": "0.85", "value_numeric": 0.85, "evidence_text": "0.85"},
            {"field_type": "lab.unit", "value": "mEq/L", "evidence_text": "mEq/L"},
            {"field_type": "lab.test_name", "value": "TSH", "evidence_text": "TSH"},
            {"field_type": "lab.value", "value": "2.4", "value_numeric": 2.4, "evidence_text": "2.4"},
            {"field_type": "lab.unit", "value": "uIU/mL", "evidence_text": "uIU/mL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "0.9", "value_numeric": 0.9, "evidence_text": "0.9"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Lithium Carbonate", "evidence_text": "Lithium Carbonate"},
            {"field_type": "med.dose", "value": "600 mg", "value_numeric": 600.0, "unit_raw": "mg", "evidence_text": "600 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Quetiapine", "evidence_text": "Quetiapine"},
            {"field_type": "med.dose", "value": "100 mg", "value_numeric": 100.0, "unit_raw": "mg", "evidence_text": "100 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "EXP_PSYCH_02",
        "category": "narrative_encounter",
        "specialty": "Psychiatry",
        "title": "Major Depression Evaluation Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
OUTPATIENT MOOD DISORDERS CLINIC
Date: 2026-06-04    Patient: Synthetic Patient Psych-Exp-02
MRN: PAT-773018    DOB: 1993-04-16    Dr. Richard Webber (NPI: 1749204941)

LABORATORY FINDINGS:
Serum Sodium was 139 mmol/L (NORMAL).
Serum Potassium was 4.1 mmol/L.

PLAN:
Sertraline 100 mg oral tablet once daily in the morning.
Bupropion XL 150 mg PO once daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Sodium", "evidence_text": "Sodium"},
            {"field_type": "lab.value", "value": "139", "value_numeric": 139.0, "evidence_text": "139"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.1", "value_numeric": 4.1, "evidence_text": "4.1"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            # Meds
            {"field_type": "med.name", "value": "Sertraline", "evidence_text": "Sertraline"},
            {"field_type": "med.dose", "value": "100 mg", "value_numeric": 100.0, "unit_raw": "mg", "evidence_text": "100 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Bupropion XL", "evidence_text": "Bupropion XL"},
            {"field_type": "med.dose", "value": "150 mg", "value_numeric": 150.0, "unit_raw": "mg", "evidence_text": "150 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
    {
        "doc_id": "EXP_PSYCH_03",
        "category": "narrative_encounter",
        "specialty": "Psychiatry",
        "title": "Clozapine Monitoring Note - Missing Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
CLOZAPINE REMS MONITORING CLINIC
Date: 2026-06-07    Patient: Synthetic Patient Psych-Exp-03
MRN: PAT-663018    DOB: 1980-09-29    Dr. Preston Burke (NPI: 1649204942)

Weekly ANC registry monitoring.
Absolute Neutrophil Count was 2.8 10*3/uL (NORMAL).
White Blood Cell Count is 5.9 10*3/uL.
Fasting Blood Glucose is 98 mg/dL.
Clozapine 300 mg oral tablet daily at bedtime.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Neutrophil Count", "evidence_text": "Neutrophil Count"},
            {"field_type": "lab.value", "value": "2.8", "value_numeric": 2.8, "evidence_text": "2.8"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "White Blood Cell Count", "evidence_text": "White Blood Cell Count"},
            {"field_type": "lab.value", "value": "5.9", "value_numeric": 5.9, "evidence_text": "5.9"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.test_name", "value": "Glucose", "evidence_text": "Glucose"},
            {"field_type": "lab.value", "value": "98", "value_numeric": 98.0, "evidence_text": "98"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Clozapine", "evidence_text": "Clozapine"},
            {"field_type": "med.dose", "value": "300 mg", "value_numeric": 300.0, "unit_raw": "mg", "evidence_text": "300 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "EXP_PSYCH_04",
        "category": "narrative_encounter",
        "specialty": "Psychiatry",
        "title": "Anxiety Clinic Note - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
ANXIETY DISORDERS CLINIC
Date: 2O26-O6-1O    Patient: Synthetic Patient Psych-Exp-04
MRN: PAT-553018    DOB: 1996-01-18    Dr. Teddy Altman (NPI: 1549204943)

LAB RESULTS:
Serum ALT was 19 U/L (NORMAL).
Serum Creatinine was 0.8 mg/dL.

MEDICATIONS:
Escitalopram 10 mg oral once daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "ALT", "evidence_text": "ALT"},
            {"field_type": "lab.value", "value": "19", "value_numeric": 19.0, "evidence_text": "19"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "0.8", "value_numeric": 0.8, "evidence_text": "0.8"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Escitalopram", "evidence_text": "Escitalopram"},
            {"field_type": "med.dose", "value": "10 mg", "value_numeric": 10.0, "unit_raw": "mg", "evidence_text": "10 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },

    # -------------------------------------------------------------
    # EMERGENCY MEDICINE & URGENT CARE
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_EMERG_01",
        "category": "narrative_encounter",
        "specialty": "Emergency Medicine",
        "title": "Emergency Department Chest Pain Protocol",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
EMERGENCY DEPARTMENT RAPID ASSESSMENT
Date: 2026-06-12    Time: 02:45 EST
Patient: Synthetic Patient Emerg-Exp-01    MRN: PAT-443018
DOB: 1967-11-05    Age: 58    ED Attending: Dr. George O'Malley, MD (NPI: 1449204944)
Vitals: BP 148/92 mmHg, Pulse 88, SpO2 96%, Temp 98.6 F

RAPID STAT LABS:
High-sensitivity Troponin T was 12 ng/L (NORMAL).
Serum Potassium was 4.0 mmol/L (NORMAL).
Serum Glucose was 118 mg/dL (HIGH).

ED MEDICATIONS:
Aspirin 324 mg oral chewable tablet once stat.
Nitroglycerin 0.4 mg sublingual every 5 minutes as needed for chest pain.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Troponin T", "evidence_text": "Troponin T"},
            {"field_type": "lab.value", "value": "12", "value_numeric": 12.0, "evidence_text": "12"},
            {"field_type": "lab.unit", "value": "ng/L", "evidence_text": "ng/L"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.0", "value_numeric": 4.0, "evidence_text": "4.0"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "Glucose", "evidence_text": "Glucose"},
            {"field_type": "lab.value", "value": "118", "value_numeric": 118.0, "evidence_text": "118"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # Meds
            {"field_type": "med.name", "value": "Aspirin", "evidence_text": "Aspirin"},
            {"field_type": "med.dose", "value": "324 mg", "value_numeric": 324.0, "unit_raw": "mg", "evidence_text": "324 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once stat", "evidence_text": "once stat"},
            {"field_type": "med.name", "value": "Nitroglycerin", "evidence_text": "Nitroglycerin"},
            {"field_type": "med.dose", "value": "0.4 mg", "value_numeric": 0.4, "unit_raw": "mg", "evidence_text": "0.4 mg"},
            {"field_type": "med.route", "value": "sublingual", "evidence_text": "sublingual"},
            {"field_type": "med.frequency", "value": "every 5 minutes", "evidence_text": "every 5 minutes"},
        ],
    },
    {
        "doc_id": "EXP_EMERG_02",
        "category": "narrative_encounter",
        "specialty": "Emergency Medicine",
        "title": "ED Syncope & Orthostatic Hypotension",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
EMERGENCY MEDICINE PROGRESS NOTE
Date: 2026-06-14    Patient: Synthetic Patient Emerg-Exp-02
MRN: PAT-333018    DOB: 1948-03-12    Dr. Isobel Stevens (NPI: 1349204945)

LABORATORY FINDINGS:
Hematocrit was 31.2% (LOW).
Blood Urea Nitrogen was 36 mg/dL (HIGH).
Serum Creatinine was 1.4 mg/dL (HIGH).
Serum Sodium was 135 mmol/L.

DISCHARGE PLAN:
Midodrine 5 mg oral tablet three times daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Hematocrit", "evidence_text": "Hematocrit"},
            {"field_type": "lab.value", "value": "31.2", "value_numeric": 31.2, "evidence_text": "31.2"},
            {"field_type": "lab.unit", "value": "%", "evidence_text": "%"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "Blood Urea Nitrogen", "evidence_text": "Blood Urea Nitrogen"},
            {"field_type": "lab.value", "value": "36", "value_numeric": 36.0, "evidence_text": "36"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "1.4", "value_numeric": 1.4, "evidence_text": "1.4"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # Meds
            {"field_type": "med.name", "value": "Midodrine", "evidence_text": "Midodrine"},
            {"field_type": "med.dose", "value": "5 mg", "value_numeric": 5.0, "unit_raw": "mg", "evidence_text": "5 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "three times daily", "evidence_text": "three times daily"},
        ],
    },
    {
        "doc_id": "EXP_EMERG_03",
        "category": "narrative_encounter",
        "specialty": "Emergency Medicine",
        "title": "Suspected Appendicitis Note - Missing Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
EMERGENCY DEPARTMENT NOTE
Date: 2026-06-16    Patient: Synthetic Patient Emerg-Exp-03
MRN: PAT-223018    DOB: 2001-08-20    Dr. Alex Karev (NPI: 1249204946)

24-year-old presenting with RLQ abdominal pain and fever.
Stat labs: White Blood Cell Count 14.8 10*3/uL (HIGH).
Total Bilirubin was 0.9 mg/dL.
Morphine 4 mg IV every 3 hours as needed for severe pain.
Ondansetron 4 mg IV every 8 hours as needed for nausea.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "White Blood Cell Count", "evidence_text": "White Blood Cell Count"},
            {"field_type": "lab.value", "value": "14.8", "value_numeric": 14.8, "evidence_text": "14.8"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Total Bilirubin", "evidence_text": "Total Bilirubin"},
            {"field_type": "lab.value", "value": "0.9", "value_numeric": 0.9, "evidence_text": "0.9"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Morphine", "evidence_text": "Morphine"},
            {"field_type": "med.dose", "value": "4 mg", "value_numeric": 4.0, "unit_raw": "mg", "evidence_text": "4 mg"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "every 3 hours", "evidence_text": "every 3 hours"},
            {"field_type": "med.name", "value": "Ondansetron", "evidence_text": "Ondansetron"},
            {"field_type": "med.dose", "value": "4 mg", "value_numeric": 4.0, "unit_raw": "mg", "evidence_text": "4 mg"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "every 8 hours", "evidence_text": "every 8 hours"},
        ],
    },
    {
        "doc_id": "EXP_EMERG_04",
        "category": "narrative_encounter",
        "specialty": "Emergency Medicine",
        "title": "Acute Asthma Exacerbation ED Note - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
URGENT CARE RAPID TRIAGE
Date: 2O26-O6-18    Patient: Synthetic Patient Emerg-Exp-04
MRN: PAT-113018    DOB: 1990-11-28    Dr. Jackson Avery (NPI: 1149204947)

LABORATORY FINDINGS:
Serum Potassium was 3.8 mmol/L (NORMAL).
Venous Blood Gas pH was 7.38.

TREATMENT:
Methylprednisolone 125 mg IV once stat.
Albuterol 2.5 mg inhalation every 20 minutes for 3 doses.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "3.8", "value_numeric": 3.8, "evidence_text": "3.8"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "pH", "evidence_text": "pH"},
            {"field_type": "lab.value", "value": "7.38", "value_numeric": 7.38, "evidence_text": "7.38"},
            # Meds
            {"field_type": "med.name", "value": "Methylprednisolone", "evidence_text": "Methylprednisolone"},
            {"field_type": "med.dose", "value": "125 mg", "value_numeric": 125.0, "unit_raw": "mg", "evidence_text": "125 mg"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "once stat", "evidence_text": "once stat"},
            {"field_type": "med.name", "value": "Albuterol", "evidence_text": "Albuterol"},
            {"field_type": "med.dose", "value": "2.5 mg", "value_numeric": 2.5, "unit_raw": "mg", "evidence_text": "2.5 mg"},
            {"field_type": "med.route", "value": "inhalation", "evidence_text": "inhalation"},
            {"field_type": "med.frequency", "value": "every 20 minutes", "evidence_text": "every 20 minutes"},
        ],
    },
]
