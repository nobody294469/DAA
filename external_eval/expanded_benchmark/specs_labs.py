"""Expanded Benchmark - Structured Laboratory Panel Specifications (8 documents).

Authority: User Request / Expanded Synthetic Benchmark
Location: external_eval/expanded_benchmark/specs_labs.py
"""
from __future__ import annotations

LAB_SPECS = [
    {
        "doc_id": "LAB_EXP_01",
        "category": "structured_lab",
        "specialty": "Metabolic Panel",
        "title": "Outpatient Comprehensive Metabolic Panel",
        "format": "tabular_ocr",
        "features": ["tabular", "standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL LABORATORY REPORT - NOT PHI - FOR EVALUATION ONLY]
ACCREDITED DIAGNOSTIC LABORATORIES, INC.
SPECIMEN ID: LAB-904128        ACCESSION: ACC-2026-88192
PATIENT NAME: Synthetic Patient Lab-Exp-01    MRN: PAT-992140
DOB: 1964-03-22    GENDER: M    AGE: 62
ORDERING PHYSICIAN: Dr. Arthur Pendelton, MD (NPI: 1092837465)
COLLECTION DATE: 2026-04-05 08:30 EST    REPORT DATE: 2026-04-05 11:45 EST

COMPREHENSIVE METABOLIC PANEL (SERUM):
Test Name               Result      Reference Range     Units       Flag
------------------------------------------------------------------------
Glucose                 105         70 - 99             mg/dL       HIGH
BUN                     18          7 - 20              mg/dL       NORMAL
Creatinine              1.1         0.7 - 1.3           mg/dL       NORMAL
Sodium                  140         136 - 145           mmol/L      NORMAL
Potassium               4.5         3.5 - 5.1           mmol/L      NORMAL
Chloride                102         98 - 107            mmol/L      NORMAL
Carbon Dioxide          26          22 - 29             mmol/L      NORMAL
Calcium                 9.4         8.6 - 10.2          mg/dL       NORMAL
Total Protein           7.1         6.4 - 8.3           g/dL        NORMAL
Albumin                 4.3         3.5 - 5.0           g/dL        NORMAL
Total Bilirubin         0.8         0.2 - 1.2           mg/dL       NORMAL
Alkaline Phosphatase    78          44 - 121            IU/L        NORMAL
AST                     24          10 - 40             IU/L        NORMAL
ALT                     28          9 - 46              IU/L        NORMAL
""",
        "claims": [
            # Glucose
            {"field_type": "lab.test_name", "value": "Glucose", "evidence_text": "Glucose"},
            {"field_type": "lab.value", "value": "105", "value_numeric": 105.0, "evidence_text": "105"},
            {"field_type": "lab.reference_range", "value": "70 - 99", "evidence_text": "70 - 99"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # BUN
            {"field_type": "lab.test_name", "value": "BUN", "evidence_text": "BUN"},
            {"field_type": "lab.value", "value": "18", "value_numeric": 18.0, "evidence_text": "18"},
            {"field_type": "lab.reference_range", "value": "7 - 20", "evidence_text": "7 - 20"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            # Creatinine
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "1.1", "value_numeric": 1.1, "evidence_text": "1.1"},
            {"field_type": "lab.reference_range", "value": "0.7 - 1.3", "evidence_text": "0.7 - 1.3"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Sodium
            {"field_type": "lab.test_name", "value": "Sodium", "evidence_text": "Sodium"},
            {"field_type": "lab.value", "value": "140", "value_numeric": 140.0, "evidence_text": "140"},
            {"field_type": "lab.reference_range", "value": "136 - 145", "evidence_text": "136 - 145"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            # Potassium
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.5", "value_numeric": 4.5, "evidence_text": "4.5"},
            {"field_type": "lab.reference_range", "value": "3.5 - 5.1", "evidence_text": "3.5 - 5.1"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
        ],
    },
    {
        "doc_id": "LAB_EXP_02",
        "category": "structured_lab",
        "specialty": "Hematology",
        "title": "Complete Blood Count with Differential",
        "format": "tabular_ocr",
        "features": ["tabular", "standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL LABORATORY REPORT - NOT PHI - FOR EVALUATION ONLY]
REGIONAL HEALTH SYSTEM PATHOLOGY LAB
Order Date: 2026-04-08    Time: 07:15
Patient: Synthetic Patient Lab-Exp-02    MRN: PAT-881924
DOB: 1988-10-15    Sex: Female    Age: 37
Provider: Dr. Clara Barton, MD (NPI: 1928374651)

COMPLETE BLOOD COUNT:
Analyte                 Value       Ref Interval        Units       Status
--------------------------------------------------------------------------
White Blood Cell Count  12.4        4.5 - 11.0          10*3/uL     HIGH
Red Blood Cell Count    4.20        4.00 - 5.20         10*6/uL     NORMAL
Hemoglobin              11.8        12.0 - 16.0         g/dL        LOW
Hematocrit              35.5        37.0 - 47.0         %           LOW
Platelet Count          245         150 - 450           10*3/uL     NORMAL
""",
        "claims": [
            # WBC
            {"field_type": "lab.test_name", "value": "White Blood Cell Count", "evidence_text": "White Blood Cell Count"},
            {"field_type": "lab.value", "value": "12.4", "value_numeric": 12.4, "evidence_text": "12.4"},
            {"field_type": "lab.reference_range", "value": "4.5 - 11.0", "evidence_text": "4.5 - 11.0"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # RBC
            {"field_type": "lab.test_name", "value": "Red Blood Cell Count", "evidence_text": "Red Blood Cell Count"},
            {"field_type": "lab.value", "value": "4.20", "value_numeric": 4.20, "evidence_text": "4.20"},
            {"field_type": "lab.reference_range", "value": "4.00 - 5.20", "evidence_text": "4.00 - 5.20"},
            {"field_type": "lab.unit", "value": "10*6/uL", "evidence_text": "10*6/uL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            # Hemoglobin
            {"field_type": "lab.test_name", "value": "Hemoglobin", "evidence_text": "Hemoglobin"},
            {"field_type": "lab.value", "value": "11.8", "value_numeric": 11.8, "evidence_text": "11.8"},
            {"field_type": "lab.reference_range", "value": "12.0 - 16.0", "evidence_text": "12.0 - 16.0"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # Hematocrit
            {"field_type": "lab.test_name", "value": "Hematocrit", "evidence_text": "Hematocrit"},
            {"field_type": "lab.value", "value": "35.5", "value_numeric": 35.5, "evidence_text": "35.5"},
            {"field_type": "lab.reference_range", "value": "37.0 - 47.0", "evidence_text": "37.0 - 47.0"},
            {"field_type": "lab.unit", "value": "%", "evidence_text": "%"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # Platelet
            {"field_type": "lab.test_name", "value": "Platelet Count", "evidence_text": "Platelet Count"},
            {"field_type": "lab.value", "value": "245", "value_numeric": 245.0, "evidence_text": "245"},
            {"field_type": "lab.reference_range", "value": "150 - 450", "evidence_text": "150 - 450"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
        ],
    },
    {
        "doc_id": "LAB_EXP_03",
        "category": "structured_lab",
        "specialty": "Cardiology / Lipids",
        "title": "Lipid & Cardiovascular Risk Assessment",
        "format": "tabular_ocr",
        "features": ["tabular", "standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL LABORATORY REPORT - NOT PHI - FOR EVALUATION ONLY]
CARDIOVASCULAR DIAGNOSTIC CENTER
Sample ID: SPEC-749102        Date: 2026-04-11
Patient: Synthetic Patient Lab-Exp-03    MRN: PAT-773019
DOB: 1959-12-04    Age: 66    Gender: Male
Ordering MD: Dr. Simon Walsh, MD (NPI: 1849201938)

LIPID PROFILE:
Test                    Result      Range               Units       Flag
------------------------------------------------------------------------
Total Cholesterol       215         < 200               mg/dL       HIGH
Triglycerides           180         < 150               mg/dL       HIGH
HDL Cholesterol         42          > 40                mg/dL       NORMAL
LDL Cholesterol         137         < 100               mg/dL       HIGH
hs-CRP                  3.2         < 1.0               mg/L        HIGH
""",
        "claims": [
            # Total Chol
            {"field_type": "lab.test_name", "value": "Total Cholesterol", "evidence_text": "Total Cholesterol"},
            {"field_type": "lab.value", "value": "215", "value_numeric": 215.0, "evidence_text": "215"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # Triglycerides
            {"field_type": "lab.test_name", "value": "Triglycerides", "evidence_text": "Triglycerides"},
            {"field_type": "lab.value", "value": "180", "value_numeric": 180.0, "evidence_text": "180"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # HDL
            {"field_type": "lab.test_name", "value": "HDL Cholesterol", "evidence_text": "HDL Cholesterol"},
            {"field_type": "lab.value", "value": "42", "value_numeric": 42.0, "evidence_text": "42"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            # LDL
            {"field_type": "lab.test_name", "value": "LDL Cholesterol", "evidence_text": "LDL Cholesterol"},
            {"field_type": "lab.value", "value": "137", "value_numeric": 137.0, "evidence_text": "137"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # hs-CRP
            {"field_type": "lab.test_name", "value": "hs-CRP", "evidence_text": "hs-CRP"},
            {"field_type": "lab.value", "value": "3.2", "value_numeric": 3.2, "evidence_text": "3.2"},
            {"field_type": "lab.unit", "value": "mg/L", "evidence_text": "mg/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
        ],
    },
    {
        "doc_id": "LAB_EXP_04",
        "category": "structured_lab",
        "specialty": "Nephrology / Renal",
        "title": "Renal Function Panel - Tabular ASCII",
        "format": "tabular_ocr",
        "features": ["ascii_table", "standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL LABORATORY REPORT - NOT PHI - FOR EVALUATION ONLY]
KIDNEY SPECIALTY LABS
REPORT ID: REN-492019    DATE: 2026-04-14
PATIENT: Synthetic Patient Lab-Exp-04    MRN: PAT-662910
DOB: 1973-06-30    AGE: 52
NEPHROLOGIST: Dr. Hannah Cole, MD (NPI: 1749203819)

RENAL FUNCTION PANEL:
Analyte            Observed    Reference           Units      Interpretation
============================================================================
BUN                32          7 - 20              mg/dL      HIGH
Creatinine         2.4         0.6 - 1.2           mg/dL      HIGH
eGFR               28          > 60                mL/min     LOW
Phosphorus         5.1         2.5 - 4.5           mg/dL      HIGH
Calcium            8.8         8.5 - 10.5          mg/dL      NORMAL
""",
        "claims": [
            # BUN
            {"field_type": "lab.test_name", "value": "BUN", "evidence_text": "BUN"},
            {"field_type": "lab.value", "value": "32", "value_numeric": 32.0, "evidence_text": "32"},
            {"field_type": "lab.reference_range", "value": "7 - 20", "evidence_text": "7 - 20"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # Creatinine
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "2.4", "value_numeric": 2.4, "evidence_text": "2.4"},
            {"field_type": "lab.reference_range", "value": "0.6 - 1.2", "evidence_text": "0.6 - 1.2"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # eGFR
            {"field_type": "lab.test_name", "value": "eGFR", "evidence_text": "eGFR"},
            {"field_type": "lab.value", "value": "28", "value_numeric": 28.0, "evidence_text": "28"},
            {"field_type": "lab.unit", "value": "mL/min", "evidence_text": "mL/min"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # Phosphorus
            {"field_type": "lab.test_name", "value": "Phosphorus", "evidence_text": "Phosphorus"},
            {"field_type": "lab.value", "value": "5.1", "value_numeric": 5.1, "evidence_text": "5.1"},
            {"field_type": "lab.reference_range", "value": "2.5 - 4.5", "evidence_text": "2.5 - 4.5"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
        ],
    },
    {
        "doc_id": "LAB_EXP_05",
        "category": "structured_lab",
        "specialty": "Gastroenterology / Hepatic",
        "title": "Hepatic Function Panel",
        "format": "tabular_ocr",
        "features": ["tabular", "standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL LABORATORY REPORT - NOT PHI - FOR EVALUATION ONLY]
LIVER & DIGESTIVE PATHOLOGY ASSOCIATES
Date: 2026-04-17    Specimen: HEP-194820
Patient: Synthetic Patient Lab-Exp-05    MRN: PAT-551928
DOB: 1980-04-19    Sex: Male    Age: 46
Physician: Dr. Kenneth Reed, MD (NPI: 1649203810)

HEPATIC FUNCTION PANEL:
Test                    Value       Normal Range        Units       Flag
------------------------------------------------------------------------
Total Protein           6.8         6.0 - 8.3           g/dL        NORMAL
Albumin                 3.6         3.4 - 5.0           g/dL        NORMAL
Total Bilirubin         2.4         0.2 - 1.2           mg/dL       HIGH
Direct Bilirubin        1.1         0.0 - 0.3           mg/dL       HIGH
Alkaline Phosphatase    195         40 - 130            U/L         HIGH
ALT                     88          7 - 56              U/L         HIGH
AST                     74          10 - 40             U/L         HIGH
""",
        "claims": [
            # Total Bilirubin
            {"field_type": "lab.test_name", "value": "Total Bilirubin", "evidence_text": "Total Bilirubin"},
            {"field_type": "lab.value", "value": "2.4", "value_numeric": 2.4, "evidence_text": "2.4"},
            {"field_type": "lab.reference_range", "value": "0.2 - 1.2", "evidence_text": "0.2 - 1.2"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # Alk Phos
            {"field_type": "lab.test_name", "value": "Alkaline Phosphatase", "evidence_text": "Alkaline Phosphatase"},
            {"field_type": "lab.value", "value": "195", "value_numeric": 195.0, "evidence_text": "195"},
            {"field_type": "lab.reference_range", "value": "40 - 130", "evidence_text": "40 - 130"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # ALT
            {"field_type": "lab.test_name", "value": "ALT", "evidence_text": "ALT"},
            {"field_type": "lab.value", "value": "88", "value_numeric": 88.0, "evidence_text": "88"},
            {"field_type": "lab.reference_range", "value": "7 - 56", "evidence_text": "7 - 56"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # AST
            {"field_type": "lab.test_name", "value": "AST", "evidence_text": "AST"},
            {"field_type": "lab.value", "value": "74", "value_numeric": 74.0, "evidence_text": "74"},
            {"field_type": "lab.reference_range", "value": "10 - 40", "evidence_text": "10 - 40"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
        ],
    },
    {
        "doc_id": "LAB_EXP_06",
        "category": "structured_lab",
        "specialty": "Hematology / Coagulation",
        "title": "Coagulation & Hemostasis Profile",
        "format": "tabular_ocr",
        "features": ["tabular", "standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL LABORATORY REPORT - NOT PHI - FOR EVALUATION ONLY]
THROMBOSIS & HEMOSTASIS CENTER
Date: 2026-04-20    Accession: COAG-84920
Patient: Synthetic Patient Lab-Exp-06    MRN: PAT-441029
DOB: 1952-11-08    Age: 73    Gender: Female
Ordering Physician: Dr. Diane Foster, MD (NPI: 1549203928)

COAGULATION STUDIES:
Test Name               Result      Ref Range           Units       Flag
------------------------------------------------------------------------
Prothrombin Time        14.2        11.0 - 13.5         seconds     HIGH
INR                     1.3         0.8 - 1.1                       HIGH
aPTT                    32.0        25.0 - 35.0         seconds     NORMAL
Fibrinogen              380         200 - 400           mg/dL       NORMAL
D-Dimer                 0.42        < 0.50              ug/mL       NORMAL
""",
        "claims": [
            # PT
            {"field_type": "lab.test_name", "value": "Prothrombin Time", "evidence_text": "Prothrombin Time"},
            {"field_type": "lab.value", "value": "14.2", "value_numeric": 14.2, "evidence_text": "14.2"},
            {"field_type": "lab.reference_range", "value": "11.0 - 13.5", "evidence_text": "11.0 - 13.5"},
            {"field_type": "lab.unit", "value": "seconds", "evidence_text": "seconds"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # INR
            {"field_type": "lab.test_name", "value": "INR", "evidence_text": "INR"},
            {"field_type": "lab.value", "value": "1.3", "value_numeric": 1.3, "evidence_text": "1.3"},
            {"field_type": "lab.reference_range", "value": "0.8 - 1.1", "evidence_text": "0.8 - 1.1"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # aPTT
            {"field_type": "lab.test_name", "value": "aPTT", "evidence_text": "aPTT"},
            {"field_type": "lab.value", "value": "32.0", "value_numeric": 32.0, "evidence_text": "32.0"},
            {"field_type": "lab.reference_range", "value": "25.0 - 35.0", "evidence_text": "25.0 - 35.0"},
            {"field_type": "lab.unit", "value": "seconds", "evidence_text": "seconds"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            # Fibrinogen
            {"field_type": "lab.test_name", "value": "Fibrinogen", "evidence_text": "Fibrinogen"},
            {"field_type": "lab.value", "value": "380", "value_numeric": 380.0, "evidence_text": "380"},
            {"field_type": "lab.reference_range", "value": "200 - 400", "evidence_text": "200 - 400"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
        ],
    },
    {
        "doc_id": "LAB_EXP_07",
        "category": "structured_lab",
        "specialty": "Endocrinology / Thyroid",
        "title": "Endocrine Panel - Missing Explicit Section Header",
        "format": "tabular_ocr",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL LABORATORY REPORT - NOT PHI - FOR EVALUATION ONLY]
ENDOCRINE PATHOLOGY LABORATORY
SPECIMEN: THY-902148    COLLECTED: 2026-04-23
PATIENT: Synthetic Patient Lab-Exp-07    MRN: PAT-332910
DOB: 1993-01-12    AGE: 33    FEMALE
ENDOCRINOLOGIST: Dr. Laura Croft, MD (NPI: 1449203817)

TSH                     4.85        0.45 - 4.50         uIU/mL      HIGH
Free T4                 1.05        0.82 - 1.77         ng/dL       NORMAL
Free T3                 3.1         2.0 - 4.4           pg/mL       NORMAL
Hemoglobin A1c          5.6         4.0 - 5.6           %           NORMAL
Vitamin D 25-OH         24          30 - 100            ng/mL       LOW
""",
        "claims": [
            # TSH
            {"field_type": "lab.test_name", "value": "TSH", "evidence_text": "TSH"},
            {"field_type": "lab.value", "value": "4.85", "value_numeric": 4.85, "evidence_text": "4.85"},
            {"field_type": "lab.reference_range", "value": "0.45 - 4.50", "evidence_text": "0.45 - 4.50"},
            {"field_type": "lab.unit", "value": "uIU/mL", "evidence_text": "uIU/mL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # Free T4
            {"field_type": "lab.test_name", "value": "Free T4", "evidence_text": "Free T4"},
            {"field_type": "lab.value", "value": "1.05", "value_numeric": 1.05, "evidence_text": "1.05"},
            {"field_type": "lab.reference_range", "value": "0.82 - 1.77", "evidence_text": "0.82 - 1.77"},
            {"field_type": "lab.unit", "value": "ng/dL", "evidence_text": "ng/dL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            # Hemoglobin A1c
            {"field_type": "lab.test_name", "value": "Hemoglobin A1c", "evidence_text": "Hemoglobin A1c"},
            {"field_type": "lab.value", "value": "5.6", "value_numeric": 5.6, "evidence_text": "5.6"},
            {"field_type": "lab.reference_range", "value": "4.0 - 5.6", "evidence_text": "4.0 - 5.6"},
            {"field_type": "lab.unit", "value": "%", "evidence_text": "%"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
        ],
    },
    {
        "doc_id": "LAB_EXP_08",
        "category": "structured_lab",
        "specialty": "Critical Care / Arterial Blood Gas",
        "title": "ABG Panel - Simulated OCR Noise",
        "format": "tabular_ocr",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL LABORATORY REPORT - NOT PHI - FOR EVALUATION ONLY]
INTENSIVE CARE UNIT RAPID LAB
Time: 2O26-O4-26 14:15    Specimen: ABG-94820
Patient: Synthetic Patient Lab-Exp-08    MRN: PAT-221940
DOB: 1960-07-25    Age: 65    ICU Bed: 12
Attending: Dr. Gregory House, MD (NPI: 1349204819)

ARTERIAL BLOOD GAS PANEL:
Analyte                 Value       Ref Interval        Units       Status
--------------------------------------------------------------------------
pH                      7.32        7.35 - 7.45                     LOW
PaCO2                   48          35 - 45             mmHg        HIGH
PaO2                    78          80 - 100            mmHg        LOW
HCO3                    24          22 - 26             mEq/L       NORMAL
Lactate                 2.8         0.5 - 2.0           mmol/L      HIGH
""",
        "claims": [
            # pH
            {"field_type": "lab.test_name", "value": "pH", "evidence_text": "pH"},
            {"field_type": "lab.value", "value": "7.32", "value_numeric": 7.32, "evidence_text": "7.32"},
            {"field_type": "lab.reference_range", "value": "7.35 - 7.45", "evidence_text": "7.35 - 7.45"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # PaCO2
            {"field_type": "lab.test_name", "value": "PaCO2", "evidence_text": "PaCO2"},
            {"field_type": "lab.value", "value": "48", "value_numeric": 48.0, "evidence_text": "48"},
            {"field_type": "lab.reference_range", "value": "35 - 45", "evidence_text": "35 - 45"},
            {"field_type": "lab.unit", "value": "mmHg", "evidence_text": "mmHg"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # PaO2
            {"field_type": "lab.test_name", "value": "PaO2", "evidence_text": "PaO2"},
            {"field_type": "lab.value", "value": "78", "value_numeric": 78.0, "evidence_text": "78"},
            {"field_type": "lab.reference_range", "value": "80 - 100", "evidence_text": "80 - 100"},
            {"field_type": "lab.unit", "value": "mmHg", "evidence_text": "mmHg"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # Lactate
            {"field_type": "lab.test_name", "value": "Lactate", "evidence_text": "Lactate"},
            {"field_type": "lab.value", "value": "2.8", "value_numeric": 2.8, "evidence_text": "2.8"},
            {"field_type": "lab.reference_range", "value": "0.5 - 2.0", "evidence_text": "0.5 - 2.0"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
        ],
    },
]
