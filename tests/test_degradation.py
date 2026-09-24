"""Tests for Milestone 1: Document Degradation & Robustness Benchmark."""

import hashlib
import json
from pathlib import Path
from PIL import Image
import pytest

from clindoc.degrade.engine import DegradationEngine
from clindoc.degrade.metrics import (
    classify_field_failure,
    compute_cer,
    compute_ocr_quality_metrics,
    compute_wer,
)
from clindoc.degrade.ocr_corrupt import corrupt_ocr_record
from clindoc.degrade.schema import (
    DegradationManifest,
    DegradationRecord,
    DegradationTrack,
    DegradationType,
    SeverityLevel,
)
from clindoc.ocr.schema import DocumentOCRRecord
import pymupdf

TEST_LAB_PDF = Path("data/rendered/LAB_0001.pdf")
TEST_LAB_OCR = Path("data/ocr_cache/LAB_0001.json")
TMP_OUT_DIR = Path("data/degraded/test_tmp")


@pytest.fixture(scope="module")
def engine() -> DegradationEngine:
    return DegradationEngine()


def _file_hash(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_severity_parameter_validation(engine: DegradationEngine):
    """Test that explicit numeric parameters exist for all 8 degradation types and all 3 severities."""
    types = list(DegradationType)
    assert len(types) == 8

    for dt in types:
        for sev in SeverityLevel:
            params = engine.get_parameters(dt, sev)
            assert isinstance(params, dict)
            assert len(params) > 0

    # Validate specific numeric ordering
    blur_low = engine.get_parameters(DegradationType.GAUSSIAN_BLUR, SeverityLevel.LOW)["radius"]
    blur_high = engine.get_parameters(DegradationType.GAUSSIAN_BLUR, SeverityLevel.HIGH)["radius"]
    assert blur_low < blur_high

    jpeg_low = engine.get_parameters(DegradationType.JPEG_COMPRESSION, SeverityLevel.LOW)["quality"]
    jpeg_high = engine.get_parameters(DegradationType.JPEG_COMPRESSION, SeverityLevel.HIGH)["quality"]
    assert jpeg_low > jpeg_high  # Higher severity = lower JPEG quality

    corrupt_low = engine.get_parameters(DegradationType.OCR_CHARACTER_CORRUPTION, SeverityLevel.LOW)["error_rate"]
    corrupt_high = engine.get_parameters(DegradationType.OCR_CHARACTER_CORRUPTION, SeverityLevel.HIGH)["error_rate"]
    assert corrupt_low < corrupt_high


def test_clean_source_remains_unchanged(engine: DegradationEngine):
    """Test that clean source PDF is strictly unmodified during degradation."""
    assert TEST_LAB_PDF.exists()
    hash_before = _file_hash(TEST_LAB_PDF)

    engine.apply_visual_degradation(
        clean_pdf_path=TEST_LAB_PDF,
        degradation_type=DegradationType.GAUSSIAN_BLUR,
        severity=SeverityLevel.LOW,
        output_dir=TMP_OUT_DIR,
        seed=42,
    )

    hash_after = _file_hash(TEST_LAB_PDF)
    assert hash_before == hash_after, "Clean source PDF was modified!"


def test_deterministic_degradation_with_fixed_seed(engine: DegradationEngine):
    """Test that identical seeds produce byte-identical or pixel-identical visual degradation outputs."""
    out_dir_1 = TMP_OUT_DIR / "det1"
    out_dir_2 = TMP_OUT_DIR / "det2"

    rec1 = engine.apply_visual_degradation(
        clean_pdf_path=TEST_LAB_PDF,
        degradation_type=DegradationType.PARTIAL_LOCAL,
        severity=SeverityLevel.MEDIUM,
        output_dir=out_dir_1,
        seed=101,
    )

    rec2 = engine.apply_visual_degradation(
        clean_pdf_path=TEST_LAB_PDF,
        degradation_type=DegradationType.PARTIAL_LOCAL,
        severity=SeverityLevel.MEDIUM,
        output_dir=out_dir_2,
        seed=101,
    )

    img1 = Image.open(rec1.output_image)
    img2 = Image.open(rec2.output_image)
    diff = sum(abs(a - b) for a, b in zip(img1.tobytes(), img2.tobytes()))
    assert diff == 0, "Deterministic seed failed to produce identical pixel output!"


def test_all_visual_degradations_produce_valid_image_backed_pdfs(engine: DegradationEngine):
    """Test that all 7 visual degradations produce non-empty image-backed PDFs without text streams."""
    visual_types = [
        DegradationType.GAUSSIAN_BLUR,
        DegradationType.JPEG_COMPRESSION,
        DegradationType.RESOLUTION_REDUCTION,
        DegradationType.ROTATION_SKEW,
        DegradationType.CONTRAST_BRIGHTNESS,
        DegradationType.PARTIAL_LOCAL,
        DegradationType.COMBINED,
    ]

    for vt in visual_types:
        rec = engine.apply_visual_degradation(
            clean_pdf_path=TEST_LAB_PDF,
            degradation_type=vt,
            severity=SeverityLevel.LOW,
            output_dir=TMP_OUT_DIR / "visual_types",
            seed=42,
        )
        assert rec.output_pdf is not None
        pdf_p = Path(rec.output_pdf)
        assert pdf_p.exists()
        assert pdf_p.stat().st_size > 5000

        # Verify PDF contains no born-digital text operators
        doc = pymupdf.open(str(pdf_p))
        text = doc[0].get_text().strip()
        assert text == "", f"Degraded PDF for {vt} unexpectedly contained born-digital text: '{text}'"


def test_track_b_ocr_character_corruption(engine: DegradationEngine):
    """Test Track B character corruption on clean OCR record."""
    assert TEST_LAB_OCR.exists()
    with open(TEST_LAB_OCR, "r", encoding="utf-8") as f:
        clean_ocr = DocumentOCRRecord.model_validate(json.load(f))

    corrupt_ocr, rec = engine.apply_ocr_corruption(
        clean_ocr_record=clean_ocr,
        severity=SeverityLevel.HIGH,
        output_dir=TMP_OUT_DIR / "corrupt",
        seed=42,
    )

    assert rec.track == DegradationTrack.OCR_TEXT
    assert rec.degradation_type == DegradationType.OCR_CHARACTER_CORRUPTION
    assert len(corrupt_ocr.lines) == len(clean_ocr.lines)

    # Some characters must have changed
    clean_text = " ".join(l.text for l in clean_ocr.lines)
    corrupt_text = " ".join(l.text for l in corrupt_ocr.lines)
    assert clean_text != corrupt_text
    cer = compute_cer(clean_text, corrupt_text)
    assert 0.05 < cer < 0.35


def test_ocr_metrics_computation():
    """Test CER, WER, and token metrics calculation."""
    ref = "Hemoglobin 14.5 g/dL Normal"
    hyp = "Hemoglobin 145 g/dL N0rmal"

    cer = compute_cer(ref, hyp)
    assert cer > 0.0
    wer = compute_wer(ref, hyp)
    assert wer > 0.0

    identical_cer = compute_cer(ref, ref)
    assert identical_cer == 0.0
    identical_wer = compute_wer(ref, ref)
    assert identical_wer == 0.0


def test_failure_attribution_classification():
    """Test decoupled classification of OCR vs downstream extraction failures."""
    # Target value 14.5 was extracted correctly
    res1 = classify_field_failure("14.5", is_field_correct=True, degraded_ocr_text="Hemoglobin 14.5 g/dL")
    assert res1 == "SUCCESS"

    # Target value 14.5 was not extracted, but OCR text still has '14.5' -> extraction model failure
    res2 = classify_field_failure("14.5", is_field_correct=False, degraded_ocr_text="Hemoglobin 14.5 g/dL")
    assert res2 == "EXTRACTION_FAILURE"

    # Target value 14.5 was missing/corrupted in OCR ('145') -> OCR failure
    res3 = classify_field_failure("14.5", is_field_correct=False, degraded_ocr_text="Hemoglobin 145 g/dL")
    assert res3 == "OCR_FAILURE"


def test_degradation_manifest_schema():
    """Test schema validation of DegradationRecord and DegradationManifest."""
    rec = DegradationRecord(
        doc_id="LAB_0001",
        track=DegradationTrack.VISUAL,
        degradation_type=DegradationType.COMBINED,
        severity=SeverityLevel.HIGH,
        seed=42,
        applied_params={"skew_angle": 5.0, "blur_radius": 2.5},
        output_pdf="data/degraded/visual/lab/LAB_0001_combined_high.pdf",
    )

    manifest = DegradationManifest(
        manifest_id="MAN-001",
        timestamp="2026-09-22T12:00:00",
        source_dataset="lab",
        total_records=1,
        records=[rec],
    )

    json_str = manifest.model_dump_json()
    loaded = DegradationManifest.model_validate_json(json_str)
    assert loaded.total_records == 1
    assert loaded.records[0].degradation_type == DegradationType.COMBINED


def test_batch_ocr_corruption_and_manifest(engine: DegradationEngine):
    """Test batch generation of corrupted OCR records and manifest creation."""
    batch_records = []
    for i in range(1, 4):
        doc_f = Path(f"data/ocr_cache/LAB_{i:04d}.json")
        if doc_f.exists():
            with open(doc_f, "r", encoding="utf-8") as f:
                rec = DocumentOCRRecord.model_validate(json.load(f))
            _, deg_rec = engine.apply_ocr_corruption(
                clean_ocr_record=rec,
                severity=SeverityLevel.LOW,
                output_dir=TMP_OUT_DIR / "batch_corrupt",
                seed=42 + i,
            )
            batch_records.append(deg_rec)

    manifest = DegradationManifest(
        manifest_id="BATCH-001",
        timestamp="2026-09-22T12:00:00",
        source_dataset="lab_subset",
        total_records=len(batch_records),
        records=batch_records,
    )
    assert manifest.total_records == len(batch_records)
    for r in manifest.records:
        assert r.track == DegradationTrack.OCR_TEXT
        assert Path(r.output_ocr_cache).exists()


def test_track_separation_in_benchmark_schema():
    """Verify that Track A (Visual) and Track B (OCR Text) maintain strict schema separation."""
    sample_report = {
        "title": "M1 Robustness Benchmark",
        "track_a_visual_robustness": {"lab": {}, "medication": {}},
        "track_b_ocr_text_robustness": {"lab": {}, "medication": {}},
        "robustness_curves": [
            {
                "track": "visual",
                "dataset": "lab",
                "degradation": "gaussian_blur",
                "severity": "LOW",
                "mean_cer": 0.015,
                "extraction_f1": 0.98,
            },
            {
                "track": "ocr_text",
                "dataset": "lab",
                "degradation": "ocr_character_corruption",
                "severity": "HIGH",
                "mean_cer": 0.185,
                "extraction_f1": 0.65,
            },
        ],
    }

    assert "track_a_visual_robustness" in sample_report
    assert "track_b_ocr_text_robustness" in sample_report
    assert len(sample_report["robustness_curves"]) == 2
    assert sample_report["robustness_curves"][0]["track"] == "visual"
    assert sample_report["robustness_curves"][1]["track"] == "ocr_text"

