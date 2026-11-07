"""Free-text extractor: vital signs in a table row, and BP split into systolic / diastolic."""
from __future__ import annotations

from clindoc.extract.text_provider import extract_text

ROWS = ("Blood pressure 118/76 mmHg\nHeart rate 82 beats/min\nRespiratory rate 16 breaths/min\n"
        "Temperature 37.1 °C\nSpOI 98 % on room air")


def test_vitals_table_rows():
    ex = extract_text(ROWS)
    pairs = {}
    for e in ex:
        pairs.setdefault(e["group"], {})[e["field_type"]] = e["value"]
    assert sorted((p["lab.test_name"], p["lab.value"]) for p in pairs.values()) == [
        ("Body temperature", "37.1"), ("Diastolic blood pressure", "76"), ("Heart rate", "82"),
        ("Respiratory rate", "16"), ("SpO2", "98"), ("Systolic blood pressure", "118")]
    assert all(e["evidence_text"] in ROWS for e in ex)  # spans stay verbatim


def test_lab_value_is_the_number_next_to_its_unit(tmp_path):
    import pymupdf
    from clindoc.extract.router import ExtractionRouter
    from clindoc.ocr.ingest import ingest_document

    pdf = pymupdf.open()
    pdf.new_page().insert_textbox((50, 60, 545, 300), "Continue aspirin 81 mg daily. Fasting glucose came back at 142 mg/dL, which is raised.")
    pdf.save(tmp_path / "LAB_t.pdf")
    out = ExtractionRouter().extract_document(ingest_document(tmp_path / "LAB_t.pdf", document_id="LAB_t"))
    values = [c.value for r in out.regions for c in r.claims if c.field_type == "lab.value"]
    assert "81" not in values  # the dose of aspirin is not a lab result
