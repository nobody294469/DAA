"""Deterministic synthetic clinical narrative document and ground-truth generator.

Conforms to:
- DEC-014: 20 synthetic narrative clinical notes (15 CV, 5 OOD)
- DEC-015: Group_id entity linkage
- DEC-017: Canonical 10-field vocabulary
- DEC-019: Tier 1 learned verification fields vs Tier 2 deterministic fields
- Genuine clinical narrative prose with HPI, Hospital Course, and contextual sections
"""
from __future__ import annotations

from datetime import date, timedelta
import random
from typing import Any
import uuid

from clindoc.synth.narrative_schema import (
    NarrativeDocumentGroundTruth,
    NarrativeLabEntity,
    NarrativeMedEntity,
    NarrativeSection,
    NarrativeClaimGroundTruth,
)
from clindoc.synth.schema import PatientInfo

# ─── Clinical Candidate Lists & Vocabularies ──────────────────────────────────

FACILITIES = [
    "Memorial General Hospital - Department of Inpatient Medicine",
    "Metro Health Medical Center - Acute Care Pavilion",
    "St. Jude Academic Health Center - Division of Hospital Medicine",
    "Valley Regional Hospital - Department of Clinical Services",
]

PHYSICIANS = [
    "Dr. Lisa Cuddy, MD",
    "Dr. Robert Evans, MD",
    "Dr. Sarah Jenkins, MD",
    "Dr. Marcus Vance, DO",
    "Dr. Emily Chen, MD",
    "Dr. Allison Cameron, MD",
    "Dr. Gregory House, MD",
]

# 20 Unique Patients (15 CV: PAT-60001..60015, 5 OOD: PAT-60016..60020)
PATIENT_ROSTER = [
    # CV Patients (15)
    {"patient_id": "PAT-60001", "name": "James Lopez", "dob": "1965-04-05", "gender": "M"},
    {"patient_id": "PAT-60002", "name": "Mary Johnson", "dob": "1972-11-12", "gender": "F"},
    {"patient_id": "PAT-60003", "name": "Robert Smith", "dob": "1958-08-23", "gender": "M"},
    {"patient_id": "PAT-60004", "name": "Patricia Williams", "dob": "1980-02-19", "gender": "F"},
    {"patient_id": "PAT-60005", "name": "Michael Brown", "dob": "1969-07-30", "gender": "M"},
    {"patient_id": "PAT-60006", "name": "Jennifer Garcia", "dob": "1975-09-14", "gender": "F"},
    {"patient_id": "PAT-60007", "name": "David Miller", "dob": "1962-03-08", "gender": "M"},
    {"patient_id": "PAT-60008", "name": "Linda Davis", "dob": "1984-12-01", "gender": "F"},
    {"patient_id": "PAT-60009", "name": "Richard Martinez", "dob": "1955-05-17", "gender": "M"},
    {"patient_id": "PAT-60010", "name": "Elizabeth Rodriguez", "dob": "1978-10-25", "gender": "F"},
    {"patient_id": "PAT-60011", "name": "Thomas Hernandez", "dob": "1967-06-11", "gender": "M"},
    {"patient_id": "PAT-60012", "name": "Barbara Wilson", "dob": "1982-01-04", "gender": "F"},
    {"patient_id": "PAT-60013", "name": "Charles Anderson", "dob": "1959-09-28", "gender": "M"},
    {"patient_id": "PAT-60014", "name": "Susan Jones", "dob": "1973-04-16", "gender": "F"},
    {"patient_id": "PAT-60015", "name": "Joseph Taylor", "dob": "1964-12-07", "gender": "M"},
    # OOD Patients (5)
    {"patient_id": "PAT-60016", "name": "William Jackson", "dob": "1953-06-22", "gender": "M"},
    {"patient_id": "PAT-60017", "name": "Jessica White", "dob": "1986-08-15", "gender": "F"},
    {"patient_id": "PAT-60018", "name": "Daniel Harris", "dob": "1961-02-03", "gender": "M"},
    {"patient_id": "PAT-60019", "name": "Sarah Martin", "dob": "1979-11-29", "gender": "F"},
    {"patient_id": "PAT-60020", "name": "Matthew Thompson", "dob": "1968-05-14", "gender": "M"},
]

# CV Templates (3 styles)
TEMPLATE_INPATIENT_DISCHARGE = "narrative_inpatient_discharge_summary_v1"
TEMPLATE_EMERGENCY_OBSERVATION = "narrative_emergency_observation_note_v1"
TEMPLATE_SURGICAL_DISCHARGE = "narrative_surgical_discharge_summary_v1"

# OOD Templates (2 distinct styles)
TEMPLATE_SPECIALTY_CONSULT = "narrative_specialty_consult_note_v1"
TEMPLATE_AMBULATORY_PROGRESS = "narrative_ambulatory_progress_note_v1"

CV_TEMPLATES = [
    TEMPLATE_INPATIENT_DISCHARGE,
    TEMPLATE_EMERGENCY_OBSERVATION,
    TEMPLATE_SURGICAL_DISCHARGE,
]

OOD_TEMPLATES = [
    TEMPLATE_SPECIALTY_CONSULT,
    TEMPLATE_AMBULATORY_PROGRESS,
]

# Standard Laboratory Analytes for Prose Embedding
LAB_DEFINITIONS: list[dict[str, Any]] = [
    {"test_name": "Hemoglobin", "loinc_code": "718-7", "unit": "g/dL", "low": 12.0, "high": 16.0, "normal_val": 13.8, "high_val": 17.5, "low_val": 10.4},
    {"test_name": "White Blood Cell Count", "loinc_code": "6690-2", "unit": "10*3/uL", "low": 4.5, "high": 11.0, "normal_val": 7.2, "high_val": 14.6, "low_val": 3.2},
    {"test_name": "Platelet Count", "loinc_code": "777-3", "unit": "10*3/uL", "low": 150.0, "high": 450.0, "normal_val": 245.0, "high_val": 520.0, "low_val": 110.0},
    {"test_name": "Glucose", "loinc_code": "2345-7", "unit": "mg/dL", "low": 70.0, "high": 99.0, "normal_val": 88.0, "high_val": 142.0, "low_val": 58.0},
    {"test_name": "BUN", "loinc_code": "3094-0", "unit": "mg/dL", "low": 7.0, "high": 20.0, "normal_val": 14.0, "high_val": 28.0, "low_val": 5.0},
    {"test_name": "Creatinine", "loinc_code": "2160-0", "unit": "mg/dL", "low": 0.6, "high": 1.3, "normal_val": 0.92, "high_val": 1.85, "low_val": 0.45},
    {"test_name": "Sodium", "loinc_code": "2951-2", "unit": "mmol/L", "low": 135.0, "high": 145.0, "normal_val": 140.0, "high_val": 149.0, "low_val": 130.0},
    {"test_name": "Potassium", "loinc_code": "2823-3", "unit": "mmol/L", "low": 3.5, "high": 5.1, "normal_val": 4.2, "high_val": 5.7, "low_val": 3.1},
    {"test_name": "Calcium", "loinc_code": "17861-6", "unit": "mg/dL", "low": 8.5, "high": 10.2, "normal_val": 9.4, "high_val": 11.2, "low_val": 7.9},
    {"test_name": "Total Cholesterol", "loinc_code": "2093-3", "unit": "mg/dL", "low": 125.0, "high": 200.0, "normal_val": 168.0, "high_val": 235.0, "low_val": 115.0},
    {"test_name": "Alanine Aminotransferase (ALT)", "loinc_code": "1742-6", "unit": "U/L", "low": 7.0, "high": 56.0, "normal_val": 28.0, "high_val": 78.0, "low_val": 5.0},
]

# Standard Medication Profiles for Prose Embedding
MED_DEFINITIONS: list[dict[str, Any]] = [
    {
        "medication_name": "Amoxicillin",
        "doses": [250.0, 500.0, 875.0],
        "dose_unit": "mg",
        "routes": ["orally", "by mouth", "oral", "PO"],
        "frequencies": ["three times daily", "TID", "every 8 hours"],
        "durations": ["for 7 days", "for 10 days"],
    },
    {
        "medication_name": "Lisinopril",
        "doses": [5.0, 10.0, 20.0, 40.0],
        "dose_unit": "mg",
        "routes": ["by mouth", "orally", "oral", "PO"],
        "frequencies": ["once daily", "every morning", "QD"],
        "durations": [None],
    },
    {
        "medication_name": "Metformin",
        "doses": [500.0, 850.0, 1000.0],
        "dose_unit": "mg",
        "routes": ["orally", "by mouth", "oral", "PO"],
        "frequencies": ["twice daily with meals", "twice daily", "BID"],
        "durations": [None],
    },
    {
        "medication_name": "Atorvastatin",
        "doses": [10.0, 20.0, 40.0, 80.0],
        "dose_unit": "mg",
        "routes": ["oral", "by mouth", "orally", "PO"],
        "frequencies": ["once daily at bedtime", "once daily", "every evening"],
        "durations": [None],
    },
    {
        "medication_name": "Amlodipine",
        "doses": [2.5, 5.0, 10.0],
        "dose_unit": "mg",
        "routes": ["by mouth", "orally", "oral", "PO"],
        "frequencies": ["once daily", "every morning"],
        "durations": [None],
    },
    {
        "medication_name": "Metoprolol",
        "doses": [25.0, 50.0, 100.0],
        "dose_unit": "mg",
        "routes": ["orally", "by mouth", "oral", "PO"],
        "frequencies": ["twice daily", "BID"],
        "durations": [None],
    },
    {
        "medication_name": "Omeprazole",
        "doses": [20.0, 40.0],
        "dose_unit": "mg",
        "routes": ["by mouth", "orally", "oral", "PO"],
        "frequencies": ["once daily before breakfast", "once daily in the morning"],
        "durations": [None, "for 14 days", "for 30 days"],
    },
    {
        "medication_name": "Ciprofloxacin",
        "doses": [250.0, 500.0],
        "dose_unit": "mg",
        "routes": ["orally", "by mouth", "oral", "PO"],
        "frequencies": ["twice daily", "every 12 hours"],
        "durations": ["for 7 days", "for 10 days"],
    },
    {
        "medication_name": "Azithromycin",
        "doses": [250.0, 500.0],
        "dose_unit": "mg",
        "routes": ["by mouth", "orally", "oral", "PO"],
        "frequencies": ["once daily", "QD"],
        "durations": ["for 5 days"],
    },
    {
        "medication_name": "Cephalexin",
        "doses": [250.0, 500.0],
        "dose_unit": "mg",
        "routes": ["orally", "by mouth", "oral", "PO"],
        "frequencies": ["four times daily", "every 6 hours", "QID"],
        "durations": ["for 7 days", "for 10 days"],
    },
]


def _format_num(val: float) -> str:
    if val == int(val):
        return str(int(val))
    return f"{val:.2f}".rstrip("0").rstrip(".")


# ─── Prose Builder Functions by Template ──────────────────────────────────────

def _build_inpatient_discharge_sections(
    patient: dict,
    doc_id: str,
    labs: list[dict],
    meds: list[dict],
    rng: random.Random,
) -> tuple[list[NarrativeSection], list[NarrativeLabEntity], list[NarrativeMedEntity]]:
    """Build sections and entities for Inpatient Discharge Summary."""
    sections: list[NarrativeSection] = []
    lab_entities: list[NarrativeLabEntity] = []
    med_entities: list[NarrativeMedEntity] = []

    # 1. Chief Complaint
    complaint = rng.choice([
        "progressive shortness of breath and pleuritic chest discomfort",
        "generalized fatigue, productive cough, and persistent fevers",
        "worsening bilateral lower extremity edema and dyspnea on exertion",
        "epigastric abdominal pain, nausea, and poor oral intake",
    ])
    sections.append(NarrativeSection(
        title="CHIEF COMPLAINT",
        text=f"The patient presented with {complaint}."
    ))

    # 2. History of Present Illness (with 2 labs embedded)
    l1, l2 = labs[0], labs[1]
    l1_mention = f"{l1['test_name'].lower()} was {l1['val_str']} {l1['unit']}"
    if l1['flag'] != "NORMAL":
        l1_mention += f" ({l1['flag']})"
    l2_mention = f"{l2['test_name'].lower()} of {l2['val_str']} {l2['unit']}"
    if l2['flag'] != "NORMAL":
        l2_mention += f" ({l2['flag']})"

    hpi_p = (
        f"The patient is a {rng.randint(45, 78)}-year-old {patient['gender'].lower() == 'm' and 'male' or 'female'} "
        f"with a medical history pertinent for hypertension and dyslipidemia who presented for evaluation of {complaint}. "
        f"Symptoms began approximately four days prior to admission and escalated in severity. "
        f"On presentation to the emergency department, initial diagnostic evaluation demonstrated that {l1_mention}. "
        f"Concurrently, serum chemistry was notable for a {l2_mention}. "
        f"Vital signs upon arrival were stable, and the patient was admitted to the inpatient general medicine service."
    )
    sections.append(NarrativeSection(title="HISTORY OF PRESENT ILLNESS", text=hpi_p))

    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_0",
        test_name=l1["test_name"],
        loinc_code=l1["loinc_code"],
        value=l1["value"],
        unit=l1["unit"],
        ref_range_low=l1["low"],
        ref_range_high=l1["high"],
        ref_range_text=f"{l1['low']} - {l1['high']}",
        flag=l1["flag"],
        evidence_text=l1_mention,
    ))
    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_1",
        test_name=l2["test_name"],
        loinc_code=l2["loinc_code"],
        value=l2["value"],
        unit=l2["unit"],
        ref_range_low=l2["low"],
        ref_range_high=l2["high"],
        ref_range_text=f"{l2['low']} - {l2['high']}",
        flag=l2["flag"],
        evidence_text=l2_mention,
    ))

    # 3. Hospital Course (with 1 in-hospital med and 1 lab)
    m_inpatient = meds[0]
    dur_str = f" {m_inpatient['duration']}" if m_inpatient['duration'] else ""
    m1_mention = f"{m_inpatient['medication_name']} {m_inpatient['dose_str']} {m_inpatient['dose_unit']} {m_inpatient['route']} {m_inpatient['frequency']}{dur_str}"

    l3 = labs[2]
    l3_mention = f"{l3['test_name'].lower()} stabilized at {l3['val_str']} {l3['unit']}"
    if l3['flag'] != "NORMAL":
        l3_mention += f" ({l3['flag']})"

    course_p = (
        f"During the hospital course, the inpatient team initiated targeted management. "
        f"The patient received {m1_mention} with good clinical tolerance. "
        f"Serial assessments revealed gradual resolution of presenting symptoms. "
        f"Prior to planned discharge, repeat laboratory testing confirmed that {l3_mention}. "
        f"The patient was ambulating without assistance and tolerating a regular diet."
    )
    sections.append(NarrativeSection(title="HOSPITAL COURSE", text=course_p))

    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_0",
        medication_name=m_inpatient["medication_name"],
        dose=m_inpatient["dose"],
        dose_unit=m_inpatient["dose_unit"],
        route=m_inpatient["route"],
        frequency=m_inpatient["frequency"],
        duration=m_inpatient["duration"],
        sig_text=m1_mention,
        evidence_text=m1_mention,
    ))
    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_2",
        test_name=l3["test_name"],
        loinc_code=l3["loinc_code"],
        value=l3["value"],
        unit=l3["unit"],
        ref_range_low=l3["low"],
        ref_range_high=l3["high"],
        ref_range_text=f"{l3['low']} - {l3['high']}",
        flag=l3["flag"],
        evidence_text=l3_mention,
    ))

    # 4. Discharge Medications (with 2 outpatient meds)
    m_out1, m_out2 = meds[1], meds[2]
    dur1 = f" {m_out1['duration']}" if m_out1['duration'] else ""
    dur2 = f" {m_out2['duration']}" if m_out2['duration'] else ""
    m_out1_mention = f"{m_out1['medication_name']} {m_out1['dose_str']} {m_out1['dose_unit']} {m_out1['route']} {m_out1['frequency']}{dur1}"
    m_out2_mention = f"{m_out2['medication_name']} {m_out2['dose_str']} {m_out2['dose_unit']} {m_out2['route']} {m_out2['frequency']}{dur2}"

    meds_p = (
        f"The patient was discharged home in stable clinical condition with instructions to take "
        f"{m_out1_mention}, and {m_out2_mention}. "
        f"Medication reconciliation was completed and all questions regarding dosing schedules were answered."
    )
    sections.append(NarrativeSection(title="DISCHARGE MEDICATIONS", text=meds_p))

    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_1",
        medication_name=m_out1["medication_name"],
        dose=m_out1["dose"],
        dose_unit=m_out1["dose_unit"],
        route=m_out1["route"],
        frequency=m_out1["frequency"],
        duration=m_out1["duration"],
        sig_text=m_out1_mention,
        evidence_text=m_out1_mention,
    ))
    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_2",
        medication_name=m_out2["medication_name"],
        dose=m_out2["dose"],
        dose_unit=m_out2["dose_unit"],
        route=m_out2["route"],
        frequency=m_out2["frequency"],
        duration=m_out2["duration"],
        sig_text=m_out2_mention,
        evidence_text=m_out2_mention,
    ))

    # 5. Discharge Plan & Instructions
    sections.append(NarrativeSection(
        title="DISCHARGE INSTRUCTIONS & FOLLOW-UP",
        text="The patient was advised to follow up with their primary care physician within 7 to 10 days. "
             "Return precautions for chest pain, recurrent shortness of breath, or fever were provided and understood."
    ))

    return sections, lab_entities, med_entities


def _build_emergency_observation_sections(
    patient: dict,
    doc_id: str,
    labs: list[dict],
    meds: list[dict],
    rng: random.Random,
) -> tuple[list[NarrativeSection], list[NarrativeLabEntity], list[NarrativeMedEntity]]:
    """Build sections and entities for Emergency Observation Clinical Note."""
    sections: list[NarrativeSection] = []
    lab_entities: list[NarrativeLabEntity] = []
    med_entities: list[NarrativeMedEntity] = []

    # 1. ED Presentation & Triage
    sections.append(NarrativeSection(
        title="EMERGENCY DEPARTMENT PRESENTATION",
        text=f"Patient {patient['name']} was triaged in the emergency department following acute onset of lightheadedness "
             f"and palpitations earlier this morning. Initial hemodynamic parameters were reviewed and patient was placed on telemetry."
    ))

    # 2. History of Present Illness (with 2 labs)
    l1, l2 = labs[0], labs[1]
    l1_mention = f"initial {l1['test_name'].lower()} was measured at {l1['val_str']} {l1['unit']}"
    if l1['flag'] != "NORMAL":
        l1_mention += f" ({l1['flag']})"
    l2_mention = f"{l2['test_name'].lower()} resulted at {l2['val_str']} {l2['unit']}"
    if l2['flag'] != "NORMAL":
        l2_mention += f" ({l2['flag']})"

    hpi_p = (
        f"The patient describes experiencing recurrent lightheadedness over the preceding 48 hours without syncope. "
        f"Initial emergency workup revealed that {l1_mention}. "
        f"Point-of-care laboratory screening additionally noted {l2_mention}. "
        f"Given these findings, the patient was transitioned to the Clinical Decision Unit for 24-hour observation."
    )
    sections.append(NarrativeSection(title="HISTORY OF PRESENT ILLNESS", text=hpi_p))

    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_0",
        test_name=l1["test_name"],
        loinc_code=l1["loinc_code"],
        value=l1["value"],
        unit=l1["unit"],
        ref_range_low=l1["low"],
        ref_range_high=l1["high"],
        ref_range_text=f"{l1['low']} - {l1['high']}",
        flag=l1["flag"],
        evidence_text=l1_mention,
    ))
    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_1",
        test_name=l2["test_name"],
        loinc_code=l2["loinc_code"],
        value=l2["value"],
        unit=l2["unit"],
        ref_range_low=l2["low"],
        ref_range_high=l2["high"],
        ref_range_text=f"{l2['low']} - {l2['high']}",
        flag=l2["flag"],
        evidence_text=l2_mention,
    ))

    # 3. Observation Unit Clinical Course (with 1 med and 1 lab)
    m1 = meds[0]
    dur1 = f" {m1['duration']}" if m1['duration'] else ""
    m1_mention = f"{m1['medication_name']} at a dose of {m1['dose_str']} {m1['dose_unit']} {m1['route']} {m1['frequency']}{dur1}"

    l3 = labs[2]
    l3_mention = f"monitoring of {l3['test_name'].lower()} demonstrated {l3['val_str']} {l3['unit']}"
    if l3['flag'] != "NORMAL":
        l3_mention += f" ({l3['flag']})"

    course_p = (
        f"Throughout the observation unit stay, continuous telemetry showed normal sinus rhythm without pause or tachyarrhythmia. "
        f"The patient was administered {m1_mention}, which promptly alleviated acute symptoms. "
        f"Follow-up protocol {l3_mention}. "
        f"Orthostatic blood pressures were negative upon ambulation."
    )
    sections.append(NarrativeSection(title="OBSERVATION UNIT CLINICAL COURSE", text=course_p))

    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_0",
        medication_name=m1["medication_name"],
        dose=m1["dose"],
        dose_unit=m1["dose_unit"],
        route=m1["route"],
        frequency=m1["frequency"],
        duration=m1["duration"],
        sig_text=m1_mention,
        evidence_text=m1_mention,
    ))
    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_2",
        test_name=l3["test_name"],
        loinc_code=l3["loinc_code"],
        value=l3["value"],
        unit=l3["unit"],
        ref_range_low=l3["low"],
        ref_range_high=l3["high"],
        ref_range_text=f"{l3['low']} - {l3['high']}",
        flag=l3["flag"],
        evidence_text=l3_mention,
    ))

    # 4. Discharge Regimen (with 1 med)
    m2 = meds[1]
    dur2 = f" {m2['duration']}" if m2['duration'] else ""
    m2_mention = f"{m2['medication_name']} {m2['dose_str']} {m2['dose_unit']} {m2['route']} {m2['frequency']}{dur2}"

    regimen_p = (
        f"Patient was deemed safe for outpatient discharge. "
        f"The ongoing regimen includes {m2_mention} as prescribed. "
        f"Prescription counseling was performed by nursing prior to release."
    )
    sections.append(NarrativeSection(title="DISCHARGE REGIMEN", text=regimen_p))

    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_1",
        medication_name=m2["medication_name"],
        dose=m2["dose"],
        dose_unit=m2["dose_unit"],
        route=m2["route"],
        frequency=m2["frequency"],
        duration=m2["duration"],
        sig_text=m2_mention,
        evidence_text=m2_mention,
    ))

    # 5. Plan & Precautions
    sections.append(NarrativeSection(
        title="PLAN & PRECAUTIONS",
        text="Resume normal daily activities as tolerated. Avoid strenuous exercise until cardiology follow-up in 1 to 2 weeks. "
             "Immediate emergency precautions detailed."
    ))

    return sections, lab_entities, med_entities


def _build_surgical_discharge_sections(
    patient: dict,
    doc_id: str,
    labs: list[dict],
    meds: list[dict],
    rng: random.Random,
) -> tuple[list[NarrativeSection], list[NarrativeLabEntity], list[NarrativeMedEntity]]:
    """Build sections and entities for Surgical Discharge Summary."""
    sections: list[NarrativeSection] = []
    lab_entities: list[NarrativeLabEntity] = []
    med_entities: list[NarrativeMedEntity] = []

    procedure = rng.choice([
        "laparoscopic cholecystectomy for symptomatic cholelithiasis",
        "open inguinal hernia repair with polypropylene mesh",
        "arthroscopic knee meniscectomy and chondroplasty",
    ])
    sections.append(NarrativeSection(
        title="PREOPERATIVE DIAGNOSIS & PROCEDURE",
        text=f"The patient underwent uncomplicated {procedure} under general endotracheal anesthesia."
    ))

    # History of Present Illness (with 2 labs)
    l1, l2 = labs[0], labs[1]
    l1_mention = f"preoperative {l1['test_name'].lower()} was {l1['val_str']} {l1['unit']}"
    if l1['flag'] != "NORMAL":
        l1_mention += f" ({l1['flag']})"
    l2_mention = f"baseline {l2['test_name'].lower()} of {l2['val_str']} {l2['unit']}"
    if l2['flag'] != "NORMAL":
        l2_mention += f" ({l2['flag']})"

    hpi_p = (
        f"The patient is an adult who presented electively for surgical intervention following conservative management failure. "
        f"Pre-admission testing confirmed that {l1_mention}. "
        f"Preoperative metabolic screening showed a {l2_mention}. "
        f"The patient was cleared from a cardiopulmonary standpoint and taken to the operative suite."
    )
    sections.append(NarrativeSection(title="HISTORY OF PRESENT ILLNESS", text=hpi_p))

    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_0",
        test_name=l1["test_name"],
        loinc_code=l1["loinc_code"],
        value=l1["value"],
        unit=l1["unit"],
        ref_range_low=l1["low"],
        ref_range_high=l1["high"],
        ref_range_text=f"{l1['low']} - {l1['high']}",
        flag=l1["flag"],
        evidence_text=l1_mention,
    ))
    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_1",
        test_name=l2["test_name"],
        loinc_code=l2["loinc_code"],
        value=l2["value"],
        unit=l2["unit"],
        ref_range_low=l2["low"],
        ref_range_high=l2["high"],
        ref_range_text=f"{l2['low']} - {l2['high']}",
        flag=l2["flag"],
        evidence_text=l2_mention,
    ))

    # Postoperative Hospital Course (with 1 med and 1 lab)
    m1 = meds[0]
    dur1 = f" {m1['duration']}" if m1['duration'] else ""
    m1_mention = f"prescribed {m1['medication_name']} {m1['dose_str']} {m1['dose_unit']} {m1['route']} {m1['frequency']}{dur1}"

    l3 = labs[2]
    l3_mention = f"postoperative {l3['test_name'].lower()} remained stable at {l3['val_str']} {l3['unit']}"
    if l3['flag'] != "NORMAL":
        l3_mention += f" ({l3['flag']})"

    course_p = (
        f"The patient was extubated in the operating room and transferred to the post-anesthesia care unit in stable condition. "
        f"Postoperative recovery was uneventful. For pain and infection prophylaxis, the patient was {m1_mention}. "
        f"On postoperative day one, {l3_mention}. "
        f"Surgical incisions were clean, intact, and without erythema or purulent drainage."
    )
    sections.append(NarrativeSection(title="POSTOPERATIVE HOSPITAL COURSE", text=course_p))

    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_0",
        medication_name=m1["medication_name"],
        dose=m1["dose"],
        dose_unit=m1["dose_unit"],
        route=m1["route"],
        frequency=m1["frequency"],
        duration=m1["duration"],
        sig_text=m1_mention,
        evidence_text=m1_mention,
    ))
    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_2",
        test_name=l3["test_name"],
        loinc_code=l3["loinc_code"],
        value=l3["value"],
        unit=l3["unit"],
        ref_range_low=l3["low"],
        ref_range_high=l3["high"],
        ref_range_text=f"{l3['low']} - {l3['high']}",
        flag=l3["flag"],
        evidence_text=l3_mention,
    ))

    # Post-Discharge Medications (with 2 meds)
    m2, m3 = meds[1], meds[2]
    dur2 = f" {m2['duration']}" if m2['duration'] else ""
    dur3 = f" {m3['duration']}" if m3['duration'] else ""
    m2_mention = f"{m2['medication_name']} {m2['dose_str']} {m2['dose_unit']} {m2['route']} {m2['frequency']}{dur2}"
    m3_mention = f"{m3['medication_name']} {m3['dose_str']} {m3['dose_unit']} {m3['route']} {m3['frequency']}{dur3}"

    meds_p = (
        f"Discharge medications: The patient will take {m2_mention}, and continue baseline {m3_mention}. "
        f"All prescriptions were transmitted electronically to the patient's pharmacy."
    )
    sections.append(NarrativeSection(title="POST-DISCHARGE MEDICATIONS", text=meds_p))

    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_1",
        medication_name=m2["medication_name"],
        dose=m2["dose"],
        dose_unit=m2["dose_unit"],
        route=m2["route"],
        frequency=m2["frequency"],
        duration=m2["duration"],
        sig_text=m2_mention,
        evidence_text=m2_mention,
    ))
    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_2",
        medication_name=m3["medication_name"],
        dose=m3["dose"],
        dose_unit=m3["dose_unit"],
        route=m3["route"],
        frequency=m3["frequency"],
        duration=m3["duration"],
        sig_text=m3_mention,
        evidence_text=m3_mention,
    ))

    # Activity & Wound Care
    sections.append(NarrativeSection(
        title="ACTIVITY & WOUND CARE PLAN",
        text="Keep dressing clean and dry. No heavy lifting greater than 10 pounds for two weeks. "
             "Surgical clinic follow-up scheduled in 10 days."
    ))

    return sections, lab_entities, med_entities


# ─── OOD Prose Builders (Distinct Clinical Genres) ───────────────────────────

def _build_specialty_consult_sections(
    patient: dict,
    doc_id: str,
    labs: list[dict],
    meds: list[dict],
    rng: random.Random,
) -> tuple[list[NarrativeSection], list[NarrativeLabEntity], list[NarrativeMedEntity]]:
    """Build sections and entities for OOD Inpatient Specialty Consult & Transfer Note."""
    sections: list[NarrativeSection] = []
    lab_entities: list[NarrativeLabEntity] = []
    med_entities: list[NarrativeMedEntity] = []

    subspecialty = rng.choice([
        "Cardiovascular Medicine Consult Service",
        "Nephrology & Renal Metabolic Consultation Service",
    ])
    sections.append(NarrativeSection(
        title="REASON FOR CONSULTATION",
        text=f"Consult requested by primary medical team regarding refractory clinical optimization and therapeutic titration. "
             f"Consultant team: {subspecialty}."
    ))

    # Chronological Clinical Narrative (Acts as HPI, embeds 2 labs)
    l1, l2 = labs[0], labs[1]
    l1_mention = f"initial biomarker profiling revealed {l1['test_name'].lower()} of {l1['val_str']} {l1['unit']}"
    if l1['flag'] != "NORMAL":
        l1_mention += f" ({l1['flag']})"
    l2_mention = f"serum {l2['test_name'].lower()} was {l2['val_str']} {l2['unit']}"
    if l2['flag'] != "NORMAL":
        l2_mention += f" ({l2['flag']})"

    hpi_p = (
        f"CHRONOLOGICAL CLINICAL NARRATIVE: We were asked to evaluate patient {patient['name']}, who was admitted "
        f"three days ago with complex multisystem decompensation. On initial presentation, {l1_mention}. "
        f"Subsequent evaluation demonstrated that {l2_mention}. "
        f"Prior outpatient regimens had proven insufficient to achieve therapeutic stability."
    )
    sections.append(NarrativeSection(title="CHRONOLOGICAL CLINICAL NARRATIVE", text=hpi_p))

    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_0",
        test_name=l1["test_name"],
        loinc_code=l1["loinc_code"],
        value=l1["value"],
        unit=l1["unit"],
        ref_range_low=l1["low"],
        ref_range_high=l1["high"],
        ref_range_text=f"{l1['low']} - {l1['high']}",
        flag=l1["flag"],
        evidence_text=l1_mention,
    ))
    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_1",
        test_name=l2["test_name"],
        loinc_code=l2["loinc_code"],
        value=l2["value"],
        unit=l2["unit"],
        ref_range_low=l2["low"],
        ref_range_high=l2["high"],
        ref_range_text=f"{l2['low']} - {l2['high']}",
        flag=l2["flag"],
        evidence_text=l2_mention,
    ))

    # Hospital Course & Trajectory (embeds 1 lab and 1 med)
    m1 = meds[0]
    dur1 = f" {m1['duration']}" if m1['duration'] else ""
    m1_mention = f"initiated on {m1['medication_name']} {m1['dose_str']} {m1['dose_unit']} {m1['route']} {m1['frequency']}{dur1}"

    l3 = labs[2]
    l3_mention = f"trajectory assessment showed {l3['test_name'].lower()} of {l3['val_str']} {l3['unit']}"
    if l3['flag'] != "NORMAL":
        l3_mention += f" ({l3['flag']})"

    course_p = (
        f"HOSPITAL COURSE & TRAJECTORY: During inpatient specialty evaluation, the consultant team adjusted pharmacological therapy. "
        f"The patient was {m1_mention}. "
        f"Following this intervention, {l3_mention}. "
        f"Clinical response has been favorable with marked improvement in objective metabolic parameters."
    )
    sections.append(NarrativeSection(title="HOSPITAL COURSE & TRAJECTORY", text=course_p))

    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_0",
        medication_name=m1["medication_name"],
        dose=m1["dose"],
        dose_unit=m1["dose_unit"],
        route=m1["route"],
        frequency=m1["frequency"],
        duration=m1["duration"],
        sig_text=m1_mention,
        evidence_text=m1_mention,
    ))
    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_2",
        test_name=l3["test_name"],
        loinc_code=l3["loinc_code"],
        value=l3["value"],
        unit=l3["unit"],
        ref_range_low=l3["low"],
        ref_range_high=l3["high"],
        ref_range_text=f"{l3['low']} - {l3['high']}",
        flag=l3["flag"],
        evidence_text=l3_mention,
    ))

    # Focused Pharmacotherapy Recommendations (embeds 2 meds)
    m2, m3 = meds[1], meds[2]
    dur2 = f" {m2['duration']}" if m2['duration'] else ""
    dur3 = f" {m3['duration']}" if m3['duration'] else ""
    m2_mention = f"{m2['medication_name']} {m2['dose_str']} {m2['dose_unit']} {m2['route']} {m2['frequency']}{dur2}"
    m3_mention = f"{m3['medication_name']} {m3['dose_str']} {m3['dose_unit']} {m3['route']} {m3['frequency']}{dur3}"

    pharm_p = (
        f"FOCUSED PHARMACOTHERAPY RECOMMENDATIONS: The specialty team recommends the following maintenance regimen: "
        f"continue {m2_mention}; and titrate {m3_mention}. "
        f"Avoid NSAIDs and nephrotoxic exposures."
    )
    sections.append(NarrativeSection(title="FOCUSED PHARMACOTHERAPY RECOMMENDATIONS", text=pharm_p))

    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_1",
        medication_name=m2["medication_name"],
        dose=m2["dose"],
        dose_unit=m2["dose_unit"],
        route=m2["route"],
        frequency=m2["frequency"],
        duration=m2["duration"],
        sig_text=m2_mention,
        evidence_text=m2_mention,
    ))
    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_2",
        medication_name=m3["medication_name"],
        dose=m3["dose"],
        dose_unit=m3["dose_unit"],
        route=m3["route"],
        frequency=m3["frequency"],
        duration=m3["duration"],
        sig_text=m3_mention,
        evidence_text=m3_mention,
    ))

    # Specialty Assessment & Plan
    sections.append(NarrativeSection(
        title="CONSULTANT ASSESSMENT & SPECIALTY PLAN",
        text="The patient is stabilized and appropriate for transfer to the regular step-down floor. "
             "Re-evaluate laboratory markers in 48 hours. Thank you for this interesting consultation."
    ))

    return sections, lab_entities, med_entities


def _build_ambulatory_progress_sections(
    patient: dict,
    doc_id: str,
    labs: list[dict],
    meds: list[dict],
    rng: random.Random,
) -> tuple[list[NarrativeSection], list[NarrativeLabEntity], list[NarrativeMedEntity]]:
    """Build sections and entities for OOD Ambulatory Longitudinal Clinic Note."""
    sections: list[NarrativeSection] = []
    lab_entities: list[NarrativeLabEntity] = []
    med_entities: list[NarrativeMedEntity] = []

    # Subjective Interval History (acts as HPI, embeds 2 labs)
    l1, l2 = labs[0], labs[1]
    l1_mention = f"interval laboratory check showed {l1['test_name'].lower()} of {l1['val_str']} {l1['unit']}"
    if l1['flag'] != "NORMAL":
        l1_mention += f" ({l1['flag']})"
    l2_mention = f"fasting {l2['test_name'].lower()} was {l2['val_str']} {l2['unit']}"
    if l2['flag'] != "NORMAL":
        l2_mention += f" ({l2['flag']})"

    hpi_p = (
        f"SUBJECTIVE INTERVAL HISTORY: Patient {patient['name']} presents for longitudinal chronic disease management. "
        f"Since the last comprehensive visit three months ago, the patient reports adherence to diet and exercise recommendations. "
        f"Recent outpatient send-out {l1_mention}. "
        f"Concurrent testing noted that {l2_mention}. "
        f"The patient denies any acute side effects or hypoglycemic episodes."
    )
    sections.append(NarrativeSection(title="SUBJECTIVE INTERVAL HISTORY", text=hpi_p))

    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_0",
        test_name=l1["test_name"],
        loinc_code=l1["loinc_code"],
        value=l1["value"],
        unit=l1["unit"],
        ref_range_low=l1["low"],
        ref_range_high=l1["high"],
        ref_range_text=f"{l1['low']} - {l1['high']}",
        flag=l1["flag"],
        evidence_text=l1_mention,
    ))
    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_1",
        test_name=l2["test_name"],
        loinc_code=l2["loinc_code"],
        value=l2["value"],
        unit=l2["unit"],
        ref_range_low=l2["low"],
        ref_range_high=l2["high"],
        ref_range_text=f"{l2['low']} - {l2['high']}",
        flag=l2["flag"],
        evidence_text=l2_mention,
    ))

    # Clinical Course & Progression (acts as Hospital Course equivalent, embeds 1 med and 1 lab)
    m1 = meds[0]
    dur1 = f" {m1['duration']}" if m1['duration'] else ""
    m1_mention = f"{m1['medication_name']} at {m1['dose_str']} {m1['dose_unit']} {m1['route']} {m1['frequency']}{dur1}"

    l3 = labs[2]
    l3_mention = f"point-of-care {l3['test_name'].lower()} was {l3['val_str']} {l3['unit']}"
    if l3['flag'] != "NORMAL":
        l3_mention += f" ({l3['flag']})"

    course_p = (
        f"CLINICAL COURSE & PROGRESSION: Over the preceding quarter, therapy with {m1_mention} was maintained. "
        f"In-clinic {l3_mention}, confirming steady metabolic control. "
        f"Vascular and sensory examinations of bilateral feet were normal without neuropathy."
    )
    sections.append(NarrativeSection(title="CLINICAL COURSE & PROGRESSION", text=course_p))

    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_0",
        medication_name=m1["medication_name"],
        dose=m1["dose"],
        dose_unit=m1["dose_unit"],
        route=m1["route"],
        frequency=m1["frequency"],
        duration=m1["duration"],
        sig_text=m1_mention,
        evidence_text=m1_mention,
    ))
    lab_entities.append(NarrativeLabEntity(
        group_id=f"{doc_id}_narr_lab_2",
        test_name=l3["test_name"],
        loinc_code=l3["loinc_code"],
        value=l3["value"],
        unit=l3["unit"],
        ref_range_low=l3["low"],
        ref_range_high=l3["high"],
        ref_range_text=f"{l3['low']} - {l3['high']}",
        flag=l3["flag"],
        evidence_text=l3_mention,
    ))

    # Updated Chronic Medication Plan (embeds 2 meds)
    m2, m3 = meds[1], meds[2]
    dur2 = f" {m2['duration']}" if m2['duration'] else ""
    dur3 = f" {m3['duration']}" if m3['duration'] else ""
    m2_mention = f"{m2['medication_name']} {m2['dose_str']} {m2['dose_unit']} {m2['route']} {m2['frequency']}{dur2}"
    m3_mention = f"{m3['medication_name']} {m3['dose_str']} {m3['dose_unit']} {m3['route']} {m3['frequency']}{dur3}"

    meds_p = (
        f"UPDATED CHRONIC MEDICATION PLAN: Refills were renewed for {m2_mention}, as well as {m3_mention}. "
        f"Patient demonstrated proper technique and confirmed understanding of morning versus evening administration."
    )
    sections.append(NarrativeSection(title="UPDATED CHRONIC MEDICATION PLAN", text=meds_p))

    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_1",
        medication_name=m2["medication_name"],
        dose=m2["dose"],
        dose_unit=m2["dose_unit"],
        route=m2["route"],
        frequency=m2["frequency"],
        duration=m2["duration"],
        sig_text=m2_mention,
        evidence_text=m2_mention,
    ))
    med_entities.append(NarrativeMedEntity(
        group_id=f"{doc_id}_narr_med_2",
        medication_name=m3["medication_name"],
        dose=m3["dose"],
        dose_unit=m3["dose_unit"],
        route=m3["route"],
        frequency=m3["frequency"],
        duration=m3["duration"],
        sig_text=m3_mention,
        evidence_text=m3_mention,
    ))

    # Follow-Up & Coordination
    sections.append(NarrativeSection(
        title="FOLLOW-UP & COORDINATION",
        text="Return to clinic in six months for repeat comprehensive metabolic panel and lipid panel. "
             "Routine health maintenance screenings were reviewed and scheduled."
    ))

    return sections, lab_entities, med_entities


# ─── Master Document Generator ────────────────────────────────────────────────

def generate_narrative_document(
    doc_index: int,  # 0 to 19 (doc_index < 15 is CV, >= 15 is OOD)
    seed: int = 42,
) -> NarrativeDocumentGroundTruth:
    """Generate a single deterministic synthetic narrative document with ground truth."""
    doc_seed = seed + doc_index * 1337
    rng = random.Random(doc_seed)

    doc_id = f"NARRATIVE_{doc_index + 1:04d}"
    is_cv = doc_index < 15
    split = "CV" if is_cv else "OOD"

    patient_raw = PATIENT_ROSTER[doc_index]
    patient = PatientInfo(
        patient_id=patient_raw["patient_id"],
        name=patient_raw["name"],
        dob=patient_raw["dob"],
        gender=patient_raw["gender"],
    )

    facility = FACILITIES[doc_index % len(FACILITIES)]
    physician = PHYSICIANS[doc_index % len(PHYSICIANS)]

    # Assign template
    if is_cv:
        if doc_index < 5:
            template_id = TEMPLATE_INPATIENT_DISCHARGE
        elif doc_index < 10:
            template_id = TEMPLATE_EMERGENCY_OBSERVATION
        else:
            template_id = TEMPLATE_SURGICAL_DISCHARGE
    else:
        if doc_index < 18:
            template_id = TEMPLATE_SPECIALTY_CONSULT
        else:
            template_id = TEMPLATE_AMBULATORY_PROGRESS

    # Dates
    base_date = date(2025, 2, 1) + timedelta(days=doc_index * 8)
    adm_date = base_date.isoformat()
    dis_date = (base_date + timedelta(days=rng.randint(2, 6))).isoformat()

    # Sample Labs (3 analytes per document)
    sampled_lab_defs = rng.sample(LAB_DEFINITIONS, 3)
    labs_data = []
    for ldef in sampled_lab_defs:
        # Determine flag: 60% normal, 20% high, 20% low
        flag_roll = rng.random()
        if flag_roll < 0.60:
            val = ldef["normal_val"] + rng.uniform(-0.5, 0.5)
            flag = "NORMAL"
        elif flag_roll < 0.80:
            val = ldef["high_val"] + rng.uniform(0.1, 1.5)
            flag = "HIGH"
        else:
            val = ldef["low_val"] - rng.uniform(0.1, 0.8)
            flag = "LOW"
        val = round(val, 2 if ldef["unit"] in ("g/dL", "mg/dL", "mmol/L") and val < 10 else 1)
        labs_data.append({
            "test_name": ldef["test_name"],
            "loinc_code": ldef["loinc_code"],
            "unit": ldef["unit"],
            "low": ldef["low"],
            "high": ldef["high"],
            "value": val,
            "val_str": _format_num(val),
            "flag": flag,
        })

    # Sample Meds (3 regimens per document)
    sampled_med_defs = rng.sample(MED_DEFINITIONS, 3)
    meds_data = []
    for mdef in sampled_med_defs:
        dose = rng.choice(mdef["doses"])
        route = rng.choice(mdef["routes"])
        freq = rng.choice(mdef["frequencies"])
        dur = rng.choice(mdef["durations"])
        meds_data.append({
            "medication_name": mdef["medication_name"],
            "dose": dose,
            "dose_str": _format_num(dose),
            "dose_unit": mdef["dose_unit"],
            "route": route,
            "frequency": freq,
            "duration": dur,
        })

    # Dispatch to template-specific section builder
    if template_id == TEMPLATE_INPATIENT_DISCHARGE:
        sections, lab_entities, med_entities = _build_inpatient_discharge_sections(
            patient_raw, doc_id, labs_data, meds_data, rng
        )
    elif template_id == TEMPLATE_EMERGENCY_OBSERVATION:
        sections, lab_entities, med_entities = _build_emergency_observation_sections(
            patient_raw, doc_id, labs_data, meds_data, rng
        )
    elif template_id == TEMPLATE_SURGICAL_DISCHARGE:
        sections, lab_entities, med_entities = _build_surgical_discharge_sections(
            patient_raw, doc_id, labs_data, meds_data, rng
        )
    elif template_id == TEMPLATE_SPECIALTY_CONSULT:
        sections, lab_entities, med_entities = _build_specialty_consult_sections(
            patient_raw, doc_id, labs_data, meds_data, rng
        )
    elif template_id == TEMPLATE_AMBULATORY_PROGRESS:
        sections, lab_entities, med_entities = _build_ambulatory_progress_sections(
            patient_raw, doc_id, labs_data, meds_data, rng
        )
    else:
        raise ValueError(f"Unknown narrative template: {template_id}")

    return NarrativeDocumentGroundTruth(
        doc_id=doc_id,
        template_id=template_id,
        template_version="1.0",
        split=split,
        patient=patient,
        facility_name=facility,
        attending_physician=physician,
        admission_date=adm_date,
        discharge_date=dis_date,
        sections=sections,
        lab_entities=lab_entities,
        med_entities=med_entities,
        claims=[],  # Populated after character offset localization
    )


def generate_all_narrative_documents(seed: int = 42) -> list[NarrativeDocumentGroundTruth]:
    """Generate all 20 narrative clinical documents (15 CV, 5 OOD)."""
    docs = []
    for idx in range(20):
        docs.append(generate_narrative_document(idx, seed=seed))
    return docs
