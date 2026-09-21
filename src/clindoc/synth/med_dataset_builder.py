"""Batch script to build 50 synthetic medication documents, render PDFs, and extract OCR records."""

from pathlib import Path
import time

from clindoc.ocr.pipeline import process_document
from clindoc.synth.med_generator import generate_synthetic_medication_dataset
from clindoc.synth.med_renderer import render_prescription_pdf


def build_synthetic_medication_dataset(
    count: int = 50,
    seed: int = 42,
    gt_dir: Path = Path("data/med_ground_truth"),
    rendered_dir: Path = Path("data/med_rendered"),
    ocr_cache_dir: Path = Path("data/med_ocr_cache"),
) -> dict:
    """Generate ground-truth records, render PDFs, and run OCR caching for synthetic medication documents."""
    gt_dir.mkdir(parents=True, exist_ok=True)
    rendered_dir.mkdir(parents=True, exist_ok=True)
    ocr_cache_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    # 1. Generate ground truth
    records = generate_synthetic_medication_dataset(count=count, seed=seed, output_gt_dir=gt_dir)

    # 2. Render PDFs & 3. Extract OCR cache with exact bounding boxes & confidences
    pdf_paths: list[Path] = []
    for rec in records:
        pdf_path = rendered_dir / f"{rec.doc_id}.pdf"
        render_prescription_pdf(rec, pdf_path)
        pdf_paths.append(pdf_path)
        process_document(
            input_path=pdf_path,
            output_dir=ocr_cache_dir,
            force=True,
            use_born_digital=True,
        )

    elapsed = time.time() - t0
    total_meds = sum(len(r.medications) for r in records)

    return {
        "documents_generated": len(records),
        "total_medications": total_meds,
        "elapsed_seconds": round(elapsed, 3),
        "gt_dir": str(gt_dir),
        "rendered_dir": str(rendered_dir),
        "ocr_cache_dir": str(ocr_cache_dir),
    }


if __name__ == "__main__":
    res = build_synthetic_medication_dataset()
    print("=" * 60)
    print("SYNTHETIC MEDICATION DATASET GENERATED")
    print(f"Documents: {res['documents_generated']}")
    print(f"Total medication regimens: {res['total_medications']}")
    print(f"Time: {res['elapsed_seconds']} s")
    print(f"PDFs: {res['rendered_dir']}")
    print(f"Ground truth: {res['gt_dir']}")
    print(f"OCR Cache: {res['ocr_cache_dir']}")
    print("=" * 60)
