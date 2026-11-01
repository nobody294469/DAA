"""Expanded Benchmark - Clinical Encounter Specs Part 1 (16 documents).
Cardiology, Endocrinology, Pulmonology, Nephrology (4 archetypes each).

Authority: User Request / Expanded Synthetic Benchmark
Location: external_eval/expanded_benchmark/specs_encounters_part1.py
"""
from __future__ import annotations

ENCOUNTERS_PART1 = [
    # -------------------------------------------------------------
    # CARDIOLOGY
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_CARDIO_01",
        "category": "narrative_encounter",
        "specialty": "Cardiology",
        "title": "Heart Failure Clinic Progress Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
PATIENT: Synthetic Patient Cardio-Exp-01    MRN: PAT-819201
ENCOUNTER: Cardiology Outpatient Visit    DATE: 2026-03-10
DOB: 1958-09-12    AGE: 67    SEX: M
PROVIDER: Dr. Sarah Vance, MD (NPI: 1092837461)    TEL: 555-019-2831
VITALS: BP 126/78 mmHg, Pulse 68 bpm, Temp 98.4 F, SpO2 97% on room air

CHIEF COMPLAINT:
Follow-up chronic systolic heart failure (HFrEF, EF 30%).

HISTORY OF PRESENT ILLNESS:
The patient is a 67-year-old male presenting for routine follow-up. He reports stable exercise tolerance and denies orthopnea, paroxysmal nocturnal dyspnea, or chest pain. No peripheral edema.

LABORATORY FINDINGS:
Serum chemistries obtained today show Potassium 4.4 mmol/L (NORMAL). Serum BNP is stable at 110 pg/mL. BUN was 19 mg/dL and Creatinine was 1.0 mg/dL.

ASSESSMENT AND PLAN:
1. Heart failure with reduced ejection fraction - clinically stable.
2. Continue guideline-directed medical therapy.

MEDICATIONS:
Continue Sacubitril-Valsartan 49-51 mg oral tablet twice daily.
Continue Metoprolol Succinate 100 mg PO daily in the morning.
Continue Spironolactone 25 mg by mouth once daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.4", "value_numeric": 4.4, "evidence_text": "4.4"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "BNP", "evidence_text": "BNP"},
            {"field_type": "lab.value", "value": "110", "value_numeric": 110.0, "evidence_text": "110"},
            {"field_type": "lab.unit", "value": "pg/mL", "evidence_text": "pg/mL"},
            {"field_type": "lab.test_name", "value": "BUN", "evidence_text": "BUN"},
            {"field_type": "lab.value", "value": "19", "value_numeric": 19.0, "evidence_text": "19"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "1.0", "value_numeric": 1.0, "evidence_text": "1.0"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Sacubitril-Valsartan", "evidence_text": "Sacubitril-Valsartan"},
            {"field_type": "med.dose", "value": "49-51 mg", "evidence_text": "49-51 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Metoprolol Succinate", "evidence_text": "Metoprolol Succinate"},
            {"field_type": "med.dose", "value": "100 mg", "value_numeric": 100.0, "unit_raw": "mg", "evidence_text": "100 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
            {"field_type": "med.name", "value": "Spironolactone", "evidence_text": "Spironolactone"},
            {"field_type": "med.dose", "value": "25 mg", "value_numeric": 25.0, "unit_raw": "mg", "evidence_text": "25 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
    {
        "doc_id": "EXP_CARDIO_02",
        "category": "narrative_encounter",
        "specialty": "Cardiology",
        "title": "Inpatient NSTEMI Discharge Summary",
        "format": "clinical_narrative_text",
        "features": ["discharge_summary", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
ST. LUKE'S MEMORIAL HOSPITAL
DISCHARGE SUMMARY
PATIENT: Synthetic Patient Cardio-Exp-02    MRN: PAT-719302
ADMISSION DATE: 2026-03-12    DISCHARGE DATE: 2026-03-15
ATTENDING PHYSICIAN: Dr. Michael Torres, MD (NPI: 1749201839)

HOSPITAL COURSE:
The patient was admitted with chest pressure and diagnosed with NSTEMI. Cardiac catheterization revealed single-vessel LAD disease treated with drug-eluting stent. Peak Troponin I was 8.4 ng/mL and discharge Hemoglobin was 13.2 g/dL. Total Cholesterol on admission was 195 mg/dL.

DISCHARGE MEDICATIONS:
Aspirin 81 mg oral tablet once daily.
Ticagrelor 90 mg PO BID for 12 months.
Atorvastatin 80 mg by mouth daily at bedtime.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Troponin I", "evidence_text": "Troponin I"},
            {"field_type": "lab.value", "value": "8.4", "value_numeric": 8.4, "evidence_text": "8.4"},
            {"field_type": "lab.unit", "value": "ng/mL", "evidence_text": "ng/mL"},
            {"field_type": "lab.test_name", "value": "Hemoglobin", "evidence_text": "Hemoglobin"},
            {"field_type": "lab.value", "value": "13.2", "value_numeric": 13.2, "evidence_text": "13.2"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            {"field_type": "lab.test_name", "value": "Total Cholesterol", "evidence_text": "Total Cholesterol"},
            {"field_type": "lab.value", "value": "195", "value_numeric": 195.0, "evidence_text": "195"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Aspirin", "evidence_text": "Aspirin"},
            {"field_type": "med.dose", "value": "81 mg", "value_numeric": 81.0, "unit_raw": "mg", "evidence_text": "81 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Ticagrelor", "evidence_text": "Ticagrelor"},
            {"field_type": "med.dose", "value": "90 mg", "value_numeric": 90.0, "unit_raw": "mg", "evidence_text": "90 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "BID", "evidence_text": "BID"},
            {"field_type": "med.duration", "value": "for 12 months", "evidence_text": "for 12 months"},
            {"field_type": "med.name", "value": "Atorvastatin", "evidence_text": "Atorvastatin"},
            {"field_type": "med.dose", "value": "80 mg", "value_numeric": 80.0, "unit_raw": "mg", "evidence_text": "80 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "EXP_CARDIO_03",
        "category": "narrative_encounter",
        "specialty": "Cardiology",
        "title": "Afib Clinic Note - Missing Section Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
CARDIOLOGY CONSULTATION
Date: 2026-03-18    Patient: Synthetic Patient Cardio-Exp-03
MRN: PAT-619283    DOB: 1961-04-20    Provider: Dr. Alan Stern (NPI: 1849203719)

Patient seen for management of paroxysmal atrial fibrillation on anticoagulation. He is feeling well without dizzy spells or palpitations.
Morning blood work shows Potassium 4.1 mmol/L and INR 2.3 (in therapeutic range). Serum Creatinine is 1.1 mg/dL.
We will maintain the current antiarrhythmic and anticoagulant regimen.
Continue Diltiazem 180 mg PO once daily for rate control.
Continue Warfarin 5 mg by mouth daily in the evening.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.1", "value_numeric": 4.1, "evidence_text": "4.1"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.test_name", "value": "INR", "evidence_text": "INR"},
            {"field_type": "lab.value", "value": "2.3", "value_numeric": 2.3, "evidence_text": "2.3"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "1.1", "value_numeric": 1.1, "evidence_text": "1.1"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Diltiazem", "evidence_text": "Diltiazem"},
            {"field_type": "med.dose", "value": "180 mg", "value_numeric": 180.0, "unit_raw": "mg", "evidence_text": "180 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Warfarin", "evidence_text": "Warfarin"},
            {"field_type": "med.dose", "value": "5 mg", "value_numeric": 5.0, "unit_raw": "mg", "evidence_text": "5 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "EXP_CARDIO_04",
        "category": "narrative_encounter",
        "specialty": "Cardiology",
        "title": "Post-CABG Note - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "irregular_spacing", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
CARDIOTHORACIC CLINIC
Date: 2O26-O3-22    Patient: Synthetic Patient Cardio-Exp-04
MRN: PAT-519284    DOB: 1955-11-19    Dr. David Miller (NPI: 1948203719)

LABORATORY RESULTS:
Hemoglobin was 10.8 g/dL (LOW). Platelet Count was 210 10*3/uL.
Serum Sodium was 138 mmol/L and Potassium was 4.3 mmol/L.

DISCHARGE REGIMEN:
Metoprolol Tartrate 25 mg oral twice daily.
Aspirin 81 mg PO daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Hemoglobin", "evidence_text": "Hemoglobin"},
            {"field_type": "lab.value", "value": "10.8", "value_numeric": 10.8, "evidence_text": "10.8"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "Platelet Count", "evidence_text": "Platelet Count"},
            {"field_type": "lab.value", "value": "210", "value_numeric": 210.0, "evidence_text": "210"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.test_name", "value": "Sodium", "evidence_text": "Sodium"},
            {"field_type": "lab.value", "value": "138", "value_numeric": 138.0, "evidence_text": "138"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.3", "value_numeric": 4.3, "evidence_text": "4.3"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            # Meds
            {"field_type": "med.name", "value": "Metoprolol Tartrate", "evidence_text": "Metoprolol Tartrate"},
            {"field_type": "med.dose", "value": "25 mg", "value_numeric": 25.0, "unit_raw": "mg", "evidence_text": "25 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Aspirin", "evidence_text": "Aspirin"},
            {"field_type": "med.dose", "value": "81 mg", "value_numeric": 81.0, "unit_raw": "mg", "evidence_text": "81 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },

    # -------------------------------------------------------------
    # ENDOCRINOLOGY
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_ENDO_01",
        "category": "narrative_encounter",
        "specialty": "Endocrinology",
        "title": "Type 2 Diabetes Management Consultation",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
PATIENT: Synthetic Patient Endo-Exp-01    MRN: PAT-492019
DATE: 2026-03-25    CLINIC: Center for Endocrinology & Metabolism
DOB: 1969-07-14    AGE: 56    PROVIDER: Dr. Linda Chang, MD (NPI: 1492038192)
VITALS: BP 130/82, HR 72, Weight 88 kg, BMI 29.4

CHIEF COMPLAINT:
Follow-up for type 2 diabetes mellitus and dyslipidemia.

LABORATORY DATA:
Fasting Glucose was 142 mg/dL (HIGH). Hemoglobin A1c was measured at 7.8% (HIGH).
Urine Albumin/Creatinine ratio was 45 mg/g (HIGH). Serum Creatinine was 0.9 mg/dL.

PLAN:
Increase antidiabetic therapy.

MEDICATIONS:
Continue Metformin 1000 mg oral tablet twice daily with food.
Start Empagliflozin 10 mg PO once daily in the morning.
Continue Atorvastatin 20 mg by mouth daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Glucose", "evidence_text": "Glucose"},
            {"field_type": "lab.value", "value": "142", "value_numeric": 142.0, "evidence_text": "142"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Hemoglobin A1c", "evidence_text": "Hemoglobin A1c"},
            {"field_type": "lab.value", "value": "7.8", "value_numeric": 7.8, "evidence_text": "7.8"},
            {"field_type": "lab.unit", "value": "%", "evidence_text": "%"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "0.9", "value_numeric": 0.9, "evidence_text": "0.9"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            {"field_type": "med.dose", "value": "1000 mg", "value_numeric": 1000.0, "unit_raw": "mg", "evidence_text": "1000 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Empagliflozin", "evidence_text": "Empagliflozin"},
            {"field_type": "med.dose", "value": "10 mg", "value_numeric": 10.0, "unit_raw": "mg", "evidence_text": "10 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Atorvastatin", "evidence_text": "Atorvastatin"},
            {"field_type": "med.dose", "value": "20 mg", "value_numeric": 20.0, "unit_raw": "mg", "evidence_text": "20 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "EXP_ENDO_02",
        "category": "narrative_encounter",
        "specialty": "Endocrinology",
        "title": "Thyroid Clinic Progress Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
PATIENT: Synthetic Patient Endo-Exp-02    MRN: PAT-382910
DATE: 2026-03-28    PHYSICIAN: Dr. Robert Taylor, MD (NPI: 1293847193)
DIAGNOSIS: Primary hypothyroidism post-radioiodine therapy.

LABORATORY EVALUATION:
Serum TSH was 6.2 uIU/mL (HIGH). Free T4 was 0.95 ng/dL (NORMAL).

RECOMMENDATIONS:
Adjust thyroid hormone replacement.

ACTIVE MEDICATIONS:
Levothyroxine 112 mcg oral tablet once daily in the morning on an empty stomach.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "TSH", "evidence_text": "TSH"},
            {"field_type": "lab.value", "value": "6.2", "value_numeric": 6.2, "evidence_text": "6.2"},
            {"field_type": "lab.unit", "value": "uIU/mL", "evidence_text": "uIU/mL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Free T4", "evidence_text": "Free T4"},
            {"field_type": "lab.value", "value": "0.95", "value_numeric": 0.95, "evidence_text": "0.95"},
            {"field_type": "lab.unit", "value": "ng/dL", "evidence_text": "ng/dL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            # Meds
            {"field_type": "med.name", "value": "Levothyroxine", "evidence_text": "Levothyroxine"},
            {"field_type": "med.dose", "value": "112 mcg", "value_numeric": 112.0, "unit_raw": "mcg", "evidence_text": "112 mcg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
    {
        "doc_id": "EXP_ENDO_03",
        "category": "narrative_encounter",
        "specialty": "Endocrinology",
        "title": "Adrenal Clinic Note - Missing Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
ADRENAL FUNCTION REVIEW
Date: 2026-03-31    Patient: Synthetic Patient Endo-Exp-03
MRN: PAT-291038    DOB: 1984-06-22    Dr. Karen White (NPI: 1849204820)

Follow-up primary adrenal insufficiency (Addison disease).
Blood work shows morning Cortisol 14.5 ug/dL and ACTH 35 pg/mL.
Serum Sodium is 139 mmol/L and Potassium is 4.6 mmol/L.
Patient will continue maintenance steroid replacement therapy.
Hydrocortisone 20 mg oral once daily in divided doses.
Fludrocortisone 0.1 mg PO daily in the morning.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Cortisol", "evidence_text": "Cortisol"},
            {"field_type": "lab.value", "value": "14.5", "value_numeric": 14.5, "evidence_text": "14.5"},
            {"field_type": "lab.unit", "value": "ug/dL", "evidence_text": "ug/dL"},
            {"field_type": "lab.test_name", "value": "ACTH", "evidence_text": "ACTH"},
            {"field_type": "lab.value", "value": "35", "value_numeric": 35.0, "evidence_text": "35"},
            {"field_type": "lab.unit", "value": "pg/mL", "evidence_text": "pg/mL"},
            {"field_type": "lab.test_name", "value": "Sodium", "evidence_text": "Sodium"},
            {"field_type": "lab.value", "value": "139", "value_numeric": 139.0, "evidence_text": "139"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.6", "value_numeric": 4.6, "evidence_text": "4.6"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            # Meds
            {"field_type": "med.name", "value": "Hydrocortisone", "evidence_text": "Hydrocortisone"},
            {"field_type": "med.dose", "value": "20 mg", "value_numeric": 20.0, "unit_raw": "mg", "evidence_text": "20 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Fludrocortisone", "evidence_text": "Fludrocortisone"},
            {"field_type": "med.dose", "value": "0.1 mg", "value_numeric": 0.1, "unit_raw": "mg", "evidence_text": "0.1 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "EXP_ENDO_04",
        "category": "narrative_encounter",
        "specialty": "Endocrinology",
        "title": "DKA ICU Note - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
ICU CRITICAL CARE NOTE
Date: 2O26-O4-O2    Patient: Synthetic Patient Endo-Exp-04
MRN: PAT-194820    DOB: 1995-12-05    Dr. Steven King (NPI: 1749203811)

LABORATORY FINDINGS:
Serum Glucose was 340 mg/dL (HIGH).
Anion Gap was 22 mmol/L (HIGH).
Potassium was 5.2 mmol/L.

MEDICATIONS:
Insulin Glargine 24 units subcutaneous once daily at bedtime.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Glucose", "evidence_text": "Glucose"},
            {"field_type": "lab.value", "value": "340", "value_numeric": 340.0, "evidence_text": "340"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Anion Gap", "evidence_text": "Anion Gap"},
            {"field_type": "lab.value", "value": "22", "value_numeric": 22.0, "evidence_text": "22"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "5.2", "value_numeric": 5.2, "evidence_text": "5.2"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            # Meds
            {"field_type": "med.name", "value": "Insulin Glargine", "evidence_text": "Insulin Glargine"},
            {"field_type": "med.dose", "value": "24 units", "evidence_text": "24 units"},
            {"field_type": "med.route", "value": "subcutaneous", "evidence_text": "subcutaneous"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },

    # -------------------------------------------------------------
    # PULMONOLOGY
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_PULM_01",
        "category": "narrative_encounter",
        "specialty": "Pulmonology",
        "title": "COPD Exacerbation Clinic Follow-up",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
PULMONARY DISEASE CLINIC
Date: 2026-04-04    Patient: Synthetic Patient Pulm-Exp-01
MRN: PAT-904128    DOB: 1957-03-10    Age: 69
Physician: Dr. Nancy Wheeler, MD (NPI: 1649203812)

CHIEF COMPLAINT:
Follow-up severe COPD with chronic bronchitis.

LABORATORY FINDINGS:
Recent CBC shows White Blood Cell Count 8.2 10*3/uL (NORMAL).
Serum Bicarbonate was 31 mmol/L (HIGH).

CURRENT MEDICATIONS:
Continue Tiotropium 18 mcg inhalation once daily.
Continue Budesonide-Formoterol 160-4.5 mcg inhalation twice daily.
Continue Prednisone 10 mg oral once daily for 5 days.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "White Blood Cell Count", "evidence_text": "White Blood Cell Count"},
            {"field_type": "lab.value", "value": "8.2", "value_numeric": 8.2, "evidence_text": "8.2"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "Bicarbonate", "evidence_text": "Bicarbonate"},
            {"field_type": "lab.value", "value": "31", "value_numeric": 31.0, "evidence_text": "31"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # Meds
            {"field_type": "med.name", "value": "Tiotropium", "evidence_text": "Tiotropium"},
            {"field_type": "med.dose", "value": "18 mcg", "value_numeric": 18.0, "unit_raw": "mcg", "evidence_text": "18 mcg"},
            {"field_type": "med.route", "value": "inhalation", "evidence_text": "inhalation"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Budesonide-Formoterol", "evidence_text": "Budesonide-Formoterol"},
            {"field_type": "med.dose", "value": "160-4.5 mcg", "evidence_text": "160-4.5 mcg"},
            {"field_type": "med.route", "value": "inhalation", "evidence_text": "inhalation"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Prednisone", "evidence_text": "Prednisone"},
            {"field_type": "med.dose", "value": "10 mg", "value_numeric": 10.0, "unit_raw": "mg", "evidence_text": "10 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.duration", "value": "for 5 days", "evidence_text": "for 5 days"},
        ],
    },
    {
        "doc_id": "EXP_PULM_02",
        "category": "narrative_encounter",
        "specialty": "Pulmonology",
        "title": "Severe Asthma Assessment",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
ASTHMA CLINIC PROGRESS NOTE
Date: 2026-04-06    Patient: Synthetic Patient Pulm-Exp-02
MRN: PAT-801928    DOB: 1989-08-14    Dr. Gregory Stone (NPI: 1549203813)

LAB RESULTS:
Absolute Eosinophil Count was 480 cells/uL (HIGH). Total Serum IgE was 280 IU/mL (HIGH).

TREATMENT PLAN:
Dupilumab 300 mg subcutaneous every 2 weeks.
Montelukast 10 mg oral once daily at bedtime.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Eosinophil Count", "evidence_text": "Eosinophil Count"},
            {"field_type": "lab.value", "value": "480", "value_numeric": 480.0, "evidence_text": "480"},
            {"field_type": "lab.unit", "value": "cells/uL", "evidence_text": "cells/uL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "IgE", "evidence_text": "IgE"},
            {"field_type": "lab.value", "value": "280", "value_numeric": 280.0, "evidence_text": "280"},
            {"field_type": "lab.unit", "value": "IU/mL", "evidence_text": "IU/mL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            # Meds
            {"field_type": "med.name", "value": "Dupilumab", "evidence_text": "Dupilumab"},
            {"field_type": "med.dose", "value": "300 mg", "value_numeric": 300.0, "unit_raw": "mg", "evidence_text": "300 mg"},
            {"field_type": "med.route", "value": "subcutaneous", "evidence_text": "subcutaneous"},
            {"field_type": "med.frequency", "value": "every 2 weeks", "evidence_text": "every 2 weeks"},
            {"field_type": "med.name", "value": "Montelukast", "evidence_text": "Montelukast"},
            {"field_type": "med.dose", "value": "10 mg", "value_numeric": 10.0, "unit_raw": "mg", "evidence_text": "10 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
    {
        "doc_id": "EXP_PULM_03",
        "category": "narrative_encounter",
        "specialty": "Pulmonology",
        "title": "Interstitial Lung Disease Note - Missing Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
PULMONARY FIBROSIS CLINIC
Date: 2026-04-09    Patient: Synthetic Patient Pulm-Exp-03
MRN: PAT-701928    DOB: 1963-02-18    Dr. Sarah Jenkins (NPI: 1449203814)

Patient with idiopathic pulmonary fibrosis on antifibrotic therapy.
Monthly surveillance labs reveal ALT 34 U/L and AST 28 U/L (both in normal range).
Serum Total Bilirubin is 0.7 mg/dL.
Pirfenidone 801 mg PO three times daily with meals.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "ALT", "evidence_text": "ALT"},
            {"field_type": "lab.value", "value": "34", "value_numeric": 34.0, "evidence_text": "34"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            {"field_type": "lab.test_name", "value": "AST", "evidence_text": "AST"},
            {"field_type": "lab.value", "value": "28", "value_numeric": 28.0, "evidence_text": "28"},
            {"field_type": "lab.unit", "value": "U/L", "evidence_text": "U/L"},
            {"field_type": "lab.test_name", "value": "Total Bilirubin", "evidence_text": "Total Bilirubin"},
            {"field_type": "lab.value", "value": "0.7", "value_numeric": 0.7, "evidence_text": "0.7"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Pirfenidone", "evidence_text": "Pirfenidone"},
            {"field_type": "med.dose", "value": "801 mg", "value_numeric": 801.0, "unit_raw": "mg", "evidence_text": "801 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "three times daily", "evidence_text": "three times daily"},
        ],
    },
    {
        "doc_id": "EXP_PULM_04",
        "category": "narrative_encounter",
        "specialty": "Pulmonology",
        "title": "Pneumonia Discharge - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
INPATIENT DISCHARGE NOTE
Date: 2O26-O4-12    Patient: Synthetic Patient Pulm-Exp-04
MRN: PAT-601928    DOB: 1978-05-12    Dr. Kevin Hart (NPI: 1349203815)

LABS:
Procalcitonin was 0.08 ng/mL (NORMAL).
White Blood Cell Count was 7.4 10*3/uL.

DISCHARGE PRESCRIPTION:
Azithromycin 250 mg oral once daily for 3 days.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Procalcitonin", "evidence_text": "Procalcitonin"},
            {"field_type": "lab.value", "value": "0.08", "value_numeric": 0.08, "evidence_text": "0.08"},
            {"field_type": "lab.unit", "value": "ng/mL", "evidence_text": "ng/mL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "White Blood Cell Count", "evidence_text": "White Blood Cell Count"},
            {"field_type": "lab.value", "value": "7.4", "value_numeric": 7.4, "evidence_text": "7.4"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            # Meds
            {"field_type": "med.name", "value": "Azithromycin", "evidence_text": "Azithromycin"},
            {"field_type": "med.dose", "value": "250 mg", "value_numeric": 250.0, "unit_raw": "mg", "evidence_text": "250 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.duration", "value": "for 3 days", "evidence_text": "for 3 days"},
        ],
    },

    # -------------------------------------------------------------
    # NEPHROLOGY
    # -------------------------------------------------------------
    {
        "doc_id": "EXP_NEPHRO_01",
        "category": "narrative_encounter",
        "specialty": "Nephrology",
        "title": "CKD Stage 4 Outpatient Clinic Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
NEPHROLOGY CONSULTATIVE SERVICES
Date: 2026-04-14    Patient: Synthetic Patient Nephro-Exp-01
MRN: PAT-501928    DOB: 1954-10-30    Age: 71
Nephrologist: Dr. Melissa Scott, MD (NPI: 1249203816)

CHIEF COMPLAINT:
Follow-up chronic kidney disease, stage 4.

LABORATORY FINDINGS:
Serum Creatinine was 2.8 mg/dL (HIGH). Estimated GFR was 22 mL/min (LOW).
BUN was 42 mg/dL (HIGH). Serum Potassium was 4.9 mmol/L (NORMAL).
Serum Bicarbonate was 18 mmol/L (LOW).

MEDICATION PLAN:
Sodium Bicarbonate 650 mg oral tablet twice daily.
Furosemide 40 mg PO once daily in the morning.
Amlodipine 5 mg by mouth daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "2.8", "value_numeric": 2.8, "evidence_text": "2.8"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "GFR", "evidence_text": "GFR"},
            {"field_type": "lab.value", "value": "22", "value_numeric": 22.0, "evidence_text": "22"},
            {"field_type": "lab.unit", "value": "mL/min", "evidence_text": "mL/min"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "BUN", "evidence_text": "BUN"},
            {"field_type": "lab.value", "value": "42", "value_numeric": 42.0, "evidence_text": "42"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.9", "value_numeric": 4.9, "evidence_text": "4.9"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "Bicarbonate", "evidence_text": "Bicarbonate"},
            {"field_type": "lab.value", "value": "18", "value_numeric": 18.0, "evidence_text": "18"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            # Meds
            {"field_type": "med.name", "value": "Sodium Bicarbonate", "evidence_text": "Sodium Bicarbonate"},
            {"field_type": "med.dose", "value": "650 mg", "value_numeric": 650.0, "unit_raw": "mg", "evidence_text": "650 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Furosemide", "evidence_text": "Furosemide"},
            {"field_type": "med.dose", "value": "40 mg", "value_numeric": 40.0, "unit_raw": "mg", "evidence_text": "40 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Amlodipine", "evidence_text": "Amlodipine"},
            {"field_type": "med.dose", "value": "5 mg", "value_numeric": 5.0, "unit_raw": "mg", "evidence_text": "5 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
        ],
    },
    {
        "doc_id": "EXP_NEPHRO_02",
        "category": "narrative_encounter",
        "specialty": "Nephrology",
        "title": "Hemodialysis Flowsheet Note",
        "format": "clinical_narrative_text",
        "features": ["standard_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
DIALYSIS CENTER PROGRESS NOTE
Date: 2026-04-16    Patient: Synthetic Patient Nephro-Exp-02
MRN: PAT-401928    DOB: 1965-01-24    Dr. Brian O'Connor (NPI: 1149203817)

PRE-DIALYSIS LABS:
Pre-dialysis Potassium was 5.4 mmol/L (HIGH).
Serum Phosphorus was 5.8 mg/dL (HIGH).
Serum Calcium was 9.1 mg/dL.

MEDICATIONS:
Sevelamer Carbonate 800 mg oral tablet three times daily with meals.
Calcitriol 0.25 mcg PO once daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "5.4", "value_numeric": 5.4, "evidence_text": "5.4"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Phosphorus", "evidence_text": "Phosphorus"},
            {"field_type": "lab.value", "value": "5.8", "value_numeric": 5.8, "evidence_text": "5.8"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Calcium", "evidence_text": "Calcium"},
            {"field_type": "lab.value", "value": "9.1", "value_numeric": 9.1, "evidence_text": "9.1"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Sevelamer Carbonate", "evidence_text": "Sevelamer Carbonate"},
            {"field_type": "med.dose", "value": "800 mg", "value_numeric": 800.0, "unit_raw": "mg", "evidence_text": "800 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "three times daily", "evidence_text": "three times daily"},
            {"field_type": "med.name", "value": "Calcitriol", "evidence_text": "Calcitriol"},
            {"field_type": "med.dose", "value": "0.25 mcg", "value_numeric": 0.25, "unit_raw": "mcg", "evidence_text": "0.25 mcg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
    {
        "doc_id": "EXP_NEPHRO_03",
        "category": "narrative_encounter",
        "specialty": "Nephrology",
        "title": "Kidney Transplant Follow-up - Missing Headings",
        "format": "clinical_narrative_text",
        "features": ["missing_headings", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
TRANSPLANT NEPHROLOGY CLINIC
Date: 2026-04-18    Patient: Synthetic Patient Nephro-Exp-03
MRN: PAT-301928    DOB: 1982-12-11    Dr. Emily Clark (NPI: 1049203818)

Renal allograft recipient 2 years post-deceased donor transplant.
Graft function is stable. Tacrolimus trough level is 6.8 ng/mL.
Serum Creatinine is 1.3 mg/dL and BUN is 21 mg/dL.
Tacrolimus 2 mg oral twice daily.
Mycophenolate Mofetil 500 mg PO twice daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Tacrolimus", "evidence_text": "Tacrolimus"},
            {"field_type": "lab.value", "value": "6.8", "value_numeric": 6.8, "evidence_text": "6.8"},
            {"field_type": "lab.unit", "value": "ng/mL", "evidence_text": "ng/mL"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "1.3", "value_numeric": 1.3, "evidence_text": "1.3"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.test_name", "value": "BUN", "evidence_text": "BUN"},
            {"field_type": "lab.value", "value": "21", "value_numeric": 21.0, "evidence_text": "21"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Tacrolimus", "evidence_text": "Tacrolimus"},
            {"field_type": "med.dose", "value": "2 mg", "value_numeric": 2.0, "unit_raw": "mg", "evidence_text": "2 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Mycophenolate Mofetil", "evidence_text": "Mycophenolate Mofetil"},
            {"field_type": "med.dose", "value": "500 mg", "value_numeric": 500.0, "unit_raw": "mg", "evidence_text": "500 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
        ],
    },
    {
        "doc_id": "EXP_NEPHRO_04",
        "category": "narrative_encounter",
        "specialty": "Nephrology",
        "title": "Glomerulonephritis Note - Simulated OCR Noise",
        "format": "clinical_narrative_text",
        "features": ["simulated_ocr_noise", "header_noise"],
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR EVALUATION ONLY]
RENAL GLOMERULAR CLINIC
Date: 2O26-O4-2O    Patient: Synthetic Patient Nephro-Exp-04
MRN: PAT-201928    DOB: 1990-04-03    Dr. William Davis (NPI: 1948203819)

LABORATORY FINDINGS:
Serum Albumin was 2.9 g/dL (LOW).
24-hour urine protein was 3.2 g/24hr (HIGH).
Serum Creatinine was 1.2 mg/dL.

MEDICATIONS:
Lisinopril 20 mg oral once daily.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Albumin", "evidence_text": "Albumin"},
            {"field_type": "lab.value", "value": "2.9", "value_numeric": 2.9, "evidence_text": "2.9"},
            {"field_type": "lab.unit", "value": "g/dL", "evidence_text": "g/dL"},
            {"field_type": "lab.flag", "value": "LOW", "evidence_text": "LOW"},
            {"field_type": "lab.test_name", "value": "protein", "evidence_text": "protein"},
            {"field_type": "lab.value", "value": "3.2", "value_numeric": 3.2, "evidence_text": "3.2"},
            {"field_type": "lab.unit", "value": "g/24hr", "evidence_text": "g/24hr"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "1.2", "value_numeric": 1.2, "evidence_text": "1.2"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Lisinopril", "evidence_text": "Lisinopril"},
            {"field_type": "med.dose", "value": "20 mg", "value_numeric": 20.0, "unit_raw": "mg", "evidence_text": "20 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
        ],
    },
]
