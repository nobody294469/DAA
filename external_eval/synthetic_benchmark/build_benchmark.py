"""Builder and verification script for the frozen synthetic benchmark.

Authority: User Request / Functional Extraction Benchmark
Location: external_eval/synthetic_benchmark/build_benchmark.py

Generates:
1. 4 Multi-Specialty Synthetic Clinical Encounter Notes (Cardiology, Endocrinology, Pulmonology, Nephrology).
2. Verifiable Ground Truth Claim sets with exact character offsets verified against source strings.
3. Benchmark manifest referencing 12 frozen documents:
   - 4 Structured Lab Panels (LAB_0001 - LAB_0004)
   - 4 Structured Prescriptions (MED_0001 - MED_0004)
   - 4 Multi-Specialty Synthetic Clinical Encounters (SYNTH_CARDIO_01, SYNTH_ENDO_01, SYNTH_PULM_01, SYNTH_NEPHRO_01)
"""
from __future__ import annotations

import json
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parent.parent
NOTES_DIR = BENCHMARK_DIR / "notes"
GT_DIR = BENCHMARK_DIR / "ground_truth"


NOTE_SPECS = [
    {
        "doc_id": "SYNTH_CARDIO_01",
        "specialty": "Cardiology",
        "title": "Cardiology Outpatient Consultation",
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR FUNCTIONAL EVALUATION ONLY]
PATIENT: Synthetic Patient Cardio-01
ENCOUNTER: Cardiology Outpatient Consultation
DATE: 2026-03-15

CHIEF COMPLAINT:
Follow-up for congestive heart failure and paroxysmal atrial fibrillation.

HISTORY OF PRESENT ILLNESS:
The patient is a 68-year-old male presenting for routine cardiology follow-up. He reports mild exertional dyspnea when climbing stairs but denies chest pain, palpitations, or orthopnea.

LABORATORY RESULTS:
Recent serum chemistry reveals Potassium 4.2 mmol/L (NORMAL). Digoxin level is therapeutic at 1.1 ng/mL. Serum BNP was measured at 145 pg/mL.

ASSESSMENT AND PLAN:
1. Congestive heart failure, NYHA Class II - compensated.
2. Paroxysmal atrial fibrillation - rate and rhythm controlled.

MEDICATIONS:
Continue Carvedilol 25 mg PO twice daily.
Continue Furosemide 40 mg oral once daily in the morning.
Continue Apixaban 5 mg by mouth BID for stroke prevention.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.2", "value_numeric": 4.2, "evidence_text": "4.2"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "Digoxin", "evidence_text": "Digoxin"},
            {"field_type": "lab.value", "value": "1.1", "value_numeric": 1.1, "evidence_text": "1.1"},
            {"field_type": "lab.unit", "value": "ng/mL", "evidence_text": "ng/mL"},
            {"field_type": "lab.test_name", "value": "BNP", "evidence_text": "BNP"},
            {"field_type": "lab.value", "value": "145", "value_numeric": 145.0, "evidence_text": "145"},
            {"field_type": "lab.unit", "value": "pg/mL", "evidence_text": "pg/mL"},
            # Meds
            {"field_type": "med.name", "value": "Carvedilol", "evidence_text": "Carvedilol"},
            {"field_type": "med.dose", "value": "25 mg", "value_numeric": 25.0, "unit_raw": "mg", "evidence_text": "25 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
            {"field_type": "med.name", "value": "Furosemide", "evidence_text": "Furosemide"},
            {"field_type": "med.dose", "value": "40 mg", "value_numeric": 40.0, "unit_raw": "mg", "evidence_text": "40 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily in the morning"},
            {"field_type": "med.name", "value": "Apixaban", "evidence_text": "Apixaban"},
            {"field_type": "med.dose", "value": "5 mg", "value_numeric": 5.0, "unit_raw": "mg", "evidence_text": "5 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "BID", "evidence_text": "BID"},
        ],
    },
    {
        "doc_id": "SYNTH_ENDO_01",
        "specialty": "Endocrinology",
        "title": "Endocrinology Follow-up Visit",
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR FUNCTIONAL EVALUATION ONLY]
PATIENT: Synthetic Patient Endo-02
ENCOUNTER: Endocrinology Follow-up Visit
DATE: 2026-03-20

CHIEF COMPLAINT:
Evaluation of type 2 diabetes mellitus glycemic control.

HISTORY OF PRESENT ILLNESS:
The patient is a 54-year-old female with long-standing type 2 diabetes mellitus. Blood glucose self-monitoring logs show fasting values ranging from 110 to 140 mg/dL without symptomatic hypoglycemia.

LABORATORY FINDINGS:
Laboratory evaluation today demonstrates Hemoglobin A1c of 7.4% (HIGH). Spot urine Microalbumin is 45 mg/g (HIGH). Serum Creatinine is stable at 0.9 mg/dL.

MEDICATION PLAN:
Continue Metformin 1000 mg PO BID with meals.
Continue Empagliflozin 10 mg oral once daily.
Add Glipizide 5 mg by mouth daily before breakfast for 30 days.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Hemoglobin A1c", "evidence_text": "Hemoglobin A1c"},
            {"field_type": "lab.value", "value": "7.4", "value_numeric": 7.4, "evidence_text": "7.4"},
            {"field_type": "lab.unit", "value": "%", "evidence_text": "%"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Microalbumin", "evidence_text": "Microalbumin"},
            {"field_type": "lab.value", "value": "45", "value_numeric": 45.0, "evidence_text": "45"},
            {"field_type": "lab.unit", "value": "mg/g", "evidence_text": "mg/g"},
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "0.9", "value_numeric": 0.9, "evidence_text": "0.9"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            # Meds
            {"field_type": "med.name", "value": "Metformin", "evidence_text": "Metformin"},
            {"field_type": "med.dose", "value": "1000 mg", "value_numeric": 1000.0, "unit_raw": "mg", "evidence_text": "1000 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "BID", "evidence_text": "BID"},
            {"field_type": "med.name", "value": "Empagliflozin", "evidence_text": "Empagliflozin"},
            {"field_type": "med.dose", "value": "10 mg", "value_numeric": 10.0, "unit_raw": "mg", "evidence_text": "10 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Glipizide", "evidence_text": "Glipizide"},
            {"field_type": "med.dose", "value": "5 mg", "value_numeric": 5.0, "unit_raw": "mg", "evidence_text": "5 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
            {"field_type": "med.duration", "value": "30 days", "evidence_text": "30 days"},
        ],
    },
    {
        "doc_id": "SYNTH_PULM_01",
        "specialty": "Pulmonology",
        "title": "Pulmonary Consultation & Inpatient Discharge Note",
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR FUNCTIONAL EVALUATION ONLY]
PATIENT: Synthetic Patient Pulm-03
ENCOUNTER: Pulmonary Consultation & Inpatient Discharge Note
DATE: 2026-03-22

CHIEF COMPLAINT:
Resolving community-acquired pneumonia.

HOSPITAL COURSE:
The patient is a 42-year-old male admitted with right lower lobe pneumonia. Treatment was initiated with intravenous Ceftriaxone 1 g IV once daily for 3 days. Patient showed marked clinical improvement with defervescence.

DISCHARGE LABS:
WBC count improved to 7.8 10*3/uL (NORMAL). Serum CRP decreased to 8.2 mg/L.

DISCHARGE MEDICATIONS:
Switch to Azithromycin 500 mg PO daily for 5 days.
Albuterol inhaler 90 mcg by inhalation every 4 hours as needed for wheezing.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "WBC", "evidence_text": "WBC"},
            {"field_type": "lab.value", "value": "7.8", "value_numeric": 7.8, "evidence_text": "7.8"},
            {"field_type": "lab.unit", "value": "10*3/uL", "evidence_text": "10*3/uL"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            {"field_type": "lab.test_name", "value": "CRP", "evidence_text": "CRP"},
            {"field_type": "lab.value", "value": "8.2", "value_numeric": 8.2, "evidence_text": "8.2"},
            {"field_type": "lab.unit", "value": "mg/L", "evidence_text": "mg/L"},
            # Meds
            {"field_type": "med.name", "value": "Ceftriaxone", "evidence_text": "Ceftriaxone"},
            {"field_type": "med.dose", "value": "1 g", "value_numeric": 1.0, "unit_raw": "g", "evidence_text": "1 g"},
            {"field_type": "med.route", "value": "IV", "evidence_text": "IV"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.duration", "value": "3 days", "evidence_text": "for 3 days"},
            {"field_type": "med.name", "value": "Azithromycin", "evidence_text": "Azithromycin"},
            {"field_type": "med.dose", "value": "500 mg", "value_numeric": 500.0, "unit_raw": "mg", "evidence_text": "500 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
            {"field_type": "med.duration", "value": "5 days", "evidence_text": "for 5 days"},
            {"field_type": "med.name", "value": "Albuterol", "evidence_text": "Albuterol"},
            {"field_type": "med.dose", "value": "90 mcg", "value_numeric": 90.0, "unit_raw": "mcg", "evidence_text": "90 mcg"},
            {"field_type": "med.route", "value": "by inhalation", "evidence_text": "by inhalation"},
            {"field_type": "med.frequency", "value": "every 4 hours as needed", "evidence_text": "every 4 hours as needed"},
        ],
    },
    {
        "doc_id": "SYNTH_NEPHRO_01",
        "specialty": "Nephrology",
        "title": "Nephrology Subspecialty Consultation",
        "text": """[SYNTHETIC CLINICAL ENCOUNTER NOTE - NOT PHI - FOR FUNCTIONAL EVALUATION ONLY]
PATIENT: Synthetic Patient Nephro-04
ENCOUNTER: Nephrology Subspecialty Consultation
DATE: 2026-03-25

CHIEF COMPLAINT:
Management of chronic kidney disease stage 3b and secondary hypertension.

HISTORY OF PRESENT ILLNESS:
The patient is a 71-year-old male with chronic kidney disease secondary to hypertensive nephrosclerosis. Blood pressure in clinic is 138/82 mmHg.

DIAGNOSTIC TESTING:
Renal panel reveals serum Creatinine 1.8 mg/dL (HIGH). Blood Urea Nitrogen is 28 mg/dL (HIGH). Serum Potassium is 4.7 mmol/L (NORMAL).

CURRENT THERAPY:
Continue Losartan 50 mg PO once daily.
Continue Amlodipine 5 mg oral daily.
Take Sodium Bicarbonate 650 mg by mouth twice daily for metabolic acidosis.
""",
        "claims": [
            # Labs
            {"field_type": "lab.test_name", "value": "Creatinine", "evidence_text": "Creatinine"},
            {"field_type": "lab.value", "value": "1.8", "value_numeric": 1.8, "evidence_text": "1.8"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.flag", "value": "HIGH", "evidence_text": "HIGH"},
            {"field_type": "lab.test_name", "value": "Blood Urea Nitrogen", "evidence_text": "Blood Urea Nitrogen"},
            {"field_type": "lab.value", "value": "28", "value_numeric": 28.0, "evidence_text": "28"},
            {"field_type": "lab.unit", "value": "mg/dL", "evidence_text": "mg/dL"},
            {"field_type": "lab.test_name", "value": "Potassium", "evidence_text": "Potassium"},
            {"field_type": "lab.value", "value": "4.7", "value_numeric": 4.7, "evidence_text": "4.7"},
            {"field_type": "lab.unit", "value": "mmol/L", "evidence_text": "mmol/L"},
            {"field_type": "lab.flag", "value": "NORMAL", "evidence_text": "NORMAL"},
            # Meds
            {"field_type": "med.name", "value": "Losartan", "evidence_text": "Losartan"},
            {"field_type": "med.dose", "value": "50 mg", "value_numeric": 50.0, "unit_raw": "mg", "evidence_text": "50 mg"},
            {"field_type": "med.route", "value": "PO", "evidence_text": "PO"},
            {"field_type": "med.frequency", "value": "once daily", "evidence_text": "once daily"},
            {"field_type": "med.name", "value": "Amlodipine", "evidence_text": "Amlodipine"},
            {"field_type": "med.dose", "value": "5 mg", "value_numeric": 5.0, "unit_raw": "mg", "evidence_text": "5 mg"},
            {"field_type": "med.route", "value": "oral", "evidence_text": "oral"},
            {"field_type": "med.frequency", "value": "daily", "evidence_text": "daily"},
            {"field_type": "med.name", "value": "Sodium Bicarbonate", "evidence_text": "Sodium Bicarbonate"},
            {"field_type": "med.dose", "value": "650 mg", "value_numeric": 650.0, "unit_raw": "mg", "evidence_text": "650 mg"},
            {"field_type": "med.route", "value": "by mouth", "evidence_text": "by mouth"},
            {"field_type": "med.frequency", "value": "twice daily", "evidence_text": "twice daily"},
        ],
    },
]


def build_and_verify():
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    GT_DIR.mkdir(parents=True, exist_ok=True)

    manifest_docs = []

    # 1. Build and verify the 4 synthetic narrative notes
    for spec in NOTE_SPECS:
        doc_id = spec["doc_id"]
        raw_text = spec["text"]

        # Write text note
        note_path = NOTES_DIR / f"{doc_id}.txt"
        note_path.write_text(raw_text, encoding="utf-8")

        # Resolve and verify character offsets
        claims_with_offsets = []
        search_start = 0

        for c in spec["claims"]:
            ev = c["evidence_text"]
            idx = raw_text.find(ev, search_start)
            if idx == -1:
                # Wrap-around search if monotonic search didn't find it
                idx = raw_text.find(ev)
            assert idx != -1, f"Evidence '{ev}' not found in {doc_id} text!"

            start_char = idx
            end_char = idx + len(ev)

            # Slicing verification check
            slice_text = raw_text[start_char:end_char]
            assert slice_text == ev, f"Slice mismatch: '{slice_text}' != '{ev}'"

            # Advance search_start slightly
            search_start = end_char

            claims_with_offsets.append({
                "document_id": doc_id,
                "field_type": c["field_type"],
                "value": c["value"],
                "value_numeric": c.get("value_numeric"),
                "unit_raw": c.get("unit_raw"),
                "evidence_text": ev,
                "char_start": start_char,
                "char_end": end_char,
                "page_number": 1,
            })

        # Save ground truth JSON
        gt_path = GT_DIR / f"{doc_id}.json"
        gt_data = {
            "doc_id": doc_id,
            "specialty": spec["specialty"],
            "title": spec["title"],
            "is_synthetic": True,
            "total_claims": len(claims_with_offsets),
            "claims": claims_with_offsets,
        }
        gt_path.write_text(json.dumps(gt_data, indent=2), encoding="utf-8")

        manifest_docs.append({
            "doc_id": doc_id,
            "category": "narrative_encounter",
            "specialty": spec["specialty"],
            "format": "clinical_narrative_text",
            "text_file": str(note_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "gt_file": str(gt_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "claim_count": len(claims_with_offsets),
        })

    # 2. Add 4 canonical structured labs and 4 canonical structured meds
    structured_labs = [
        {"doc_id": "LAB_0001", "specialty": "Metabolic Panel", "panel": "Comprehensive Metabolic Panel"},
        {"doc_id": "LAB_0002", "specialty": "Cardiology / Lipids", "panel": "Lipid Panel"},
        {"doc_id": "LAB_0003", "specialty": "Nephrology / Renal", "panel": "Renal Function Panel"},
        {"doc_id": "LAB_0004", "specialty": "Hematology", "panel": "Complete Blood Count"},
    ]
    for lab in structured_labs:
        manifest_docs.append({
            "doc_id": lab["doc_id"],
            "category": "structured_lab",
            "specialty": lab["specialty"],
            "format": "tabular_pdf_ocr",
            "ocr_cache": f"data/ocr_cache/{lab['doc_id']}.json",
            "gt_file": f"data/ground_truth/{lab['doc_id']}.json",
        })

    structured_meds = [
        {"doc_id": "MED_0001", "specialty": "Cardiovascular", "med": "Atorvastatin"},
        {"doc_id": "MED_0002", "specialty": "Cardiovascular", "med": "Lisinopril"},
        {"doc_id": "MED_0003", "specialty": "Endocrinology", "med": "Metformin"},
        {"doc_id": "MED_0004", "specialty": "Infectious Disease", "med": "Amoxicillin"},
    ]
    for med in structured_meds:
        manifest_docs.append({
            "doc_id": med["doc_id"],
            "category": "structured_medication",
            "specialty": med["specialty"],
            "format": "prescription_pad_ocr",
            "ocr_cache": f"data/med_ocr_cache/{med['doc_id']}.json",
            "gt_file": f"data/med_ground_truth/{med['doc_id']}.json",
        })

    manifest = {
        "benchmark_name": "ClinicalDoc Smallest Reproducible Multi-Specialty Synthetic Benchmark",
        "benchmark_version": "1.0-frozen",
        "created_date": "2026-10-29",
        "license": "Apache 2.0 (Synthea-derived core + original synthetic test fixtures)",
        "total_documents": len(manifest_docs),
        "document_breakdown": {
            "structured_lab": 4,
            "structured_medication": 4,
            "narrative_encounter": 4,
        },
        "specialties_covered": [
            "Cardiology",
            "Endocrinology",
            "Infectious Disease / Pulmonary",
            "Nephrology",
            "Hematology",
        ],
        "is_synthetic_evaluation": True,
        "phi_free_guarantee": True,
        "documents": manifest_docs,
    }

    manifest_path = BENCHMARK_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Successfully generated synthetic benchmark with {len(manifest_docs)} documents.")
    print(f"Manifest written to: {manifest_path}")


if __name__ == "__main__":
    build_and_verify()
