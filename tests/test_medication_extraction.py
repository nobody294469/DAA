"""Tests for M0b Medication Regimen Extraction slice."""

import json
from pathlib import Path
import pytest

from clindoc.extract.med_parser import MedicationRegimenParser
from clindoc.ocr.schema import DocumentOCRRecord, LineBox
from clindoc.schema.medication import ExtractedPrescriptionDocument
from clindoc.synth.med_schema import PrescriptionGroundTruth
from eval.eval_medication import evaluate_document_medications, evaluate_all

MED_OCR_DIR = Path("data/med_ocr_cache")
MED_GT_DIR = Path("data/med_ground_truth")
MED_EXT_DIR = Path("data/med_extracted")


@pytest.fixture(scope="module")
def med_parser() -> MedicationRegimenParser:
    """Fixture providing MedicationRegimenParser."""
    return MedicationRegimenParser()


def test_basic_single_medication_extraction(med_parser: MedicationRegimenParser):
    """Test basic single medication extraction on MED_0001 (clinic_rx_pad)."""
    ocr_file = MED_OCR_DIR / "MED_0001.json"
    assert ocr_file.exists()
    with open(ocr_file, "r", encoding="utf-8") as f:
        ocr_record = DocumentOCRRecord.model_validate(json.load(f))

    extracted = med_parser.parse_document(ocr_record)
    assert extracted.doc_id == "MED_0001"
    assert extracted.patient is not None
    assert extracted.patient.name == "James Lopez"
    assert extracted.patient.patient_id == "PAT-30001"
    assert len(extracted.medications) == 1

    med = extracted.medications[0]
    assert med.medication_name == "Atorvastatin"
    assert med.dose == 10.0
    assert med.dose_unit == "mg"
    assert med.dosage_form == "tablet"
    assert med.route == "PO"
    assert med.frequency == "every evening"
    assert med.quantity == 30
    assert med.refills == 2
    assert med.rxcui == "83367"
    assert "Atorvastatin" in med.source_evidence.text
    assert med.source_evidence.ocr_confidence > 0.0


def test_multiple_medications_extraction(med_parser: MedicationRegimenParser):
    """Test multiple medications in discharge summary table layout on MED_0002."""
    ocr_file = MED_OCR_DIR / "MED_0002.json"
    assert ocr_file.exists()
    with open(ocr_file, "r", encoding="utf-8") as f:
        ocr_record = DocumentOCRRecord.model_validate(json.load(f))

    extracted = med_parser.parse_document(ocr_record)
    assert extracted.doc_id == "MED_0002"
    assert len(extracted.medications) == 3

    names = [m.medication_name for m in extracted.medications]
    assert "Omeprazole" in names
    assert "Ciprofloxacin" in names
    assert "Losartan" in names

    # Check Ciprofloxacin specifics
    cipro = next(m for m in extracted.medications if m.medication_name == "Ciprofloxacin")
    assert cipro.dose == 250.0
    assert cipro.dose_unit == "mg"
    assert cipro.dosage_form == "tablet"
    assert cipro.duration == "7 days"
    assert cipro.frequency == "twice daily"


def test_varied_frequency_wording(med_parser: MedicationRegimenParser):
    """Test parsing across multiple realistic frequency variations."""
    cases = [
        ("Take 1 tablet PO TID with meals", "TID"),
        ("1 capsule by mouth three times daily", "three times daily"),
        ("Take 1 tablet oral BID", "BID"),
        ("1 tablet twice daily for 10 days", "twice daily"),
        ("Take 1 tablet every 8 hours", "every 8 hours"),
        ("Take 1 tablet PO once daily at bedtime", "once daily at bedtime"),
        ("Take 1 tablet once daily in the morning with food", "once daily in the morning with food"),
        ("Inhale 2 puffs every 4 to 6 hours as needed", "every 4 to 6 hours as needed"),
        ("Take 1 tablet every 4-6 hours PRN for pain", "every 4-6 hours PRN"),
        ("Take 1 tablet QID", "QID"),
        ("Take 1 tablet QD", "QD"),
    ]
    for text, expected in cases:
        parsed = med_parser._parse_frequency(text)
        assert parsed == expected, f"Failed for '{text}': got {parsed}, expected {expected}"


def test_varied_route_wording(med_parser: MedicationRegimenParser):
    """Test parsing across multiple route expressions."""
    cases = [
        ("Take 1 tablet PO daily", "PO"),
        ("Take 1 capsule by mouth twice daily", "by mouth"),
        ("1 tablet oral daily", "oral"),
        ("Inhale 2 puffs by inhalation as needed", "by inhalation"),
        ("2 puffs inhalation every 4-6 hours", "inhalation"),
        ("Apply thin layer topical daily", "topical"),
        ("Inject 10 units subcutaneous daily", "subcutaneous"),
    ]
    for text, expected in cases:
        parsed = med_parser._parse_route(text)
        assert parsed == expected, f"Failed for '{text}': got {parsed}, expected {expected}"


def test_duration_extraction(med_parser: MedicationRegimenParser):
    """Test duration parsing for short-course and maintenance regimens."""
    cases = [
        ("Amoxicillin 500 mg PO TID for 10 days", "10 days"),
        ("Ciprofloxacin 250 mg tablet oral twice daily x 7 days", "7 days"),
        ("Prednisone 5 mg tablet for 5 days", "5 days"),
        ("Duration: 30 days | Dispense: #30", "30 days"),
        ("Duration: Ongoing | Dispense: #90", None),
        ("Duration: as needed | Dispense: #1", "as needed"),
    ]
    for text, expected in cases:
        parsed = med_parser._parse_duration(text)
        assert parsed == expected, f"Failed for '{text}': got {parsed}, expected {expected}"


def test_evidence_preservation(med_parser: MedicationRegimenParser):
    """Test that all extracted medications preserve complete source evidence."""
    ocr_file = MED_OCR_DIR / "MED_0003.json"
    with open(ocr_file, "r", encoding="utf-8") as f:
        ocr_record = DocumentOCRRecord.model_validate(json.load(f))

    extracted = med_parser.parse_document(ocr_record)
    assert len(extracted.medications) >= 1
    for med in extracted.medications:
        ev = med.source_evidence
        assert ev is not None
        assert isinstance(ev.text, str) and len(ev.text) > 0
        assert ev.page >= 1
        assert len(ev.bbox) == 4
        assert ev.bbox[2] > ev.bbox[0]
        assert ev.bbox[3] > ev.bbox[1]
        assert ev.ocr_confidence > 0.0


def test_terminology_normalization_and_rxcui(med_parser: MedicationRegimenParser):
    """Test medication catalog lookup and RxCUI mapping."""
    cases = [
        ("amoxicillin", "Amoxicillin", "723"),
        ("METOPROLOL", "Metoprolol", "6918"),
        ("Atorvastatin Calcium", "Atorvastatin", "83367"),
        ("Lisinopril 10mg", "Lisinopril", "29046"),
        ("Levothyroxine Sodium", "Levothyroxine", "10582"),
    ]
    for raw, exp_name, exp_rxcui in cases:
        canon_name, rxcui = med_parser._normalize_med_name(raw)
        assert canon_name == exp_name, f"Failed name for {raw}: got {canon_name}, expected {exp_name}"
        assert rxcui == exp_rxcui, f"Failed rxcui for {raw}: got {rxcui}, expected {exp_rxcui}"


def test_batch_processing(med_parser: MedicationRegimenParser):
    """Test batch processing multiple OCR records."""
    test_files = [MED_OCR_DIR / f"MED_{i:04d}.json" for i in range(1, 6)]
    records = []
    for f in test_files:
        with open(f, "r", encoding="utf-8") as fp:
            records.append(DocumentOCRRecord.model_validate(json.load(fp)))

    batch_docs = med_parser.parse_documents(records)
    assert len(batch_docs) == 5
    for doc in batch_docs:
        assert isinstance(doc, ExtractedPrescriptionDocument)
        assert len(doc.medications) >= 1
        assert doc.metadata["total_extracted_medications"] == len(doc.medications)


def test_evaluation_against_ground_truth():
    """Test evaluation logic on single document and full dataset."""
    gt_file = MED_GT_DIR / "MED_0001.json"
    ext_file = MED_EXT_DIR / "MED_0001.json"
    assert gt_file.exists() and ext_file.exists()

    with open(gt_file, "r", encoding="utf-8") as f:
        gt = PrescriptionGroundTruth.model_validate(json.load(f))
    with open(ext_file, "r", encoding="utf-8") as f:
        ext = ExtractedPrescriptionDocument.model_validate(json.load(f))

    res = evaluate_document_medications(gt, ext)
    assert res["tp"] == 1
    assert res["fp"] == 0
    assert res["fn"] == 0
    assert res["complete_regimen_matches"] == 1

    # Full report verification
    report = evaluate_all()
    assert report["total_documents"] == 50
    assert report["entity_metrics"]["precision"] == 1.0
    assert report["entity_metrics"]["recall"] == 1.0
    assert report["entity_metrics"]["f1"] == 1.0
    assert report["complete_regimen_exact_match"]["rate"] == 1.0
