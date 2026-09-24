"""Central DegradationEngine coordinating visual transformations and OCR corruptions."""

import json
from pathlib import Path
import time
from typing import Any

from PIL import Image

from clindoc.degrade.ocr_corrupt import corrupt_ocr_record
from clindoc.degrade.schema import (
    DegradationManifest,
    DegradationRecord,
    DegradationTrack,
    DegradationType,
    SeverityLevel,
)
from clindoc.degrade.visual import (
    apply_combined,
    apply_contrast_brightness,
    apply_gaussian_blur,
    apply_jpeg_compression,
    apply_partial_local,
    apply_resolution_reduction,
    apply_rotation_skew,
    rasterize_pdf,
    save_images_as_pdf,
)
from clindoc.ocr.schema import DocumentOCRRecord

DEFAULT_CONFIG_PATH = Path("configs/degradation_config.json")


def load_degradation_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load degradation parameters configuration from JSON."""
    if not config_path.exists():
        proj_path = Path(__file__).resolve().parents[3] / config_path
        if proj_path.exists():
            config_path = proj_path
    if not config_path.exists():
        raise FileNotFoundError(f"Degradation configuration not found at {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


class DegradationEngine:
    """Orchestrates document degradation generation across Track A (Visual) and Track B (OCR Text)."""

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH):
        self.config = load_degradation_config(config_path)
        self.base_raster_dpi = self.config.get("rasterize_dpi", 200)

    def get_parameters(
        self, degradation_type: DegradationType, severity: SeverityLevel
    ) -> dict[str, Any]:
        """Fetch explicit numeric parameters for a degradation type and severity level."""
        type_key = degradation_type.value
        deg_cfg = self.config.get("degradations", {}).get(type_key)
        if not deg_cfg:
            raise ValueError(f"Unknown degradation type in config: {type_key}")
        levels = deg_cfg.get("levels", {})
        sev_key = severity.value
        if sev_key not in levels:
            raise ValueError(f"Unknown severity level '{sev_key}' for {type_key}")
        return levels[sev_key]

    def apply_visual_degradation(
        self,
        clean_pdf_path: str | Path,
        degradation_type: DegradationType,
        severity: SeverityLevel,
        output_dir: str | Path,
        seed: int = 42,
    ) -> DegradationRecord:
        """Apply Track A visual degradation to a clean PDF and emit an image-backed PDF."""
        clean_pdf_path = Path(clean_pdf_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        doc_id = clean_pdf_path.stem
        degraded_id = f"{doc_id}_{degradation_type.value}_{severity.value.lower()}"
        out_pdf_path = output_dir / f"{degraded_id}.pdf"
        out_png_path = output_dir / f"{degraded_id}_p1.png"

        params = self.get_parameters(degradation_type, severity)
        pages = rasterize_pdf(clean_pdf_path, dpi=self.base_raster_dpi)

        degraded_pages: list[Image.Image] = []
        for p_idx, page_img in enumerate(pages):
            page_seed = seed + p_idx * 17

            if degradation_type == DegradationType.GAUSSIAN_BLUR:
                deg_img = apply_gaussian_blur(page_img, radius=params["radius"])
            elif degradation_type == DegradationType.JPEG_COMPRESSION:
                deg_img = apply_jpeg_compression(page_img, quality=params["quality"])
            elif degradation_type == DegradationType.RESOLUTION_REDUCTION:
                deg_img = apply_resolution_reduction(
                    page_img, target_dpi=params["target_dpi"], base_dpi=self.base_raster_dpi
                )
            elif degradation_type == DegradationType.ROTATION_SKEW:
                deg_img = apply_rotation_skew(
                    page_img, angle_degrees=params["angle_degrees"], seed=page_seed
                )
            elif degradation_type == DegradationType.CONTRAST_BRIGHTNESS:
                deg_img = apply_contrast_brightness(
                    page_img,
                    contrast_factor=params["contrast_factor"],
                    brightness_factor=params["brightness_factor"],
                )
            elif degradation_type == DegradationType.PARTIAL_LOCAL:
                deg_img = apply_partial_local(
                    page_img,
                    coverage_ratio=params["coverage_ratio"],
                    opacity=params["opacity"],
                    blur_radius=params["blur_radius"],
                    seed=page_seed,
                )
            elif degradation_type == DegradationType.COMBINED:
                deg_img = apply_combined(page_img, params=params, seed=page_seed)
            else:
                raise ValueError(f"Visual engine does not support non-visual type: {degradation_type}")

            degraded_pages.append(deg_img)

        # Save purely image-backed PDF without text layer
        save_images_as_pdf(degraded_pages, out_pdf_path, dpi=self.base_raster_dpi)
        # Save first page as PNG for visual inspection
        degraded_pages[0].save(str(out_png_path), "PNG")

        return DegradationRecord(
            doc_id=doc_id,
            track=DegradationTrack.VISUAL,
            degradation_type=degradation_type,
            severity=severity,
            seed=seed,
            applied_params=params,
            output_pdf=str(out_pdf_path),
            output_image=str(out_png_path),
            metadata={"num_pages": len(degraded_pages), "degraded_doc_id": degraded_id},
        )

    def apply_ocr_corruption(
        self,
        clean_ocr_record: DocumentOCRRecord,
        severity: SeverityLevel,
        output_dir: str | Path,
        seed: int = 42,
    ) -> tuple[DocumentOCRRecord, DegradationRecord]:
        """Apply Track B text/OCR-level character corruption to a clean DocumentOCRRecord."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        params = self.get_parameters(DegradationType.OCR_CHARACTER_CORRUPTION, severity)
        error_rate = float(params["error_rate"])

        degraded_id = f"{clean_ocr_record.doc_id}_ocr_corrupt_{severity.value.lower()}"
        corrupted_record = corrupt_ocr_record(
            clean_record=clean_ocr_record,
            error_rate=error_rate,
            seed=seed,
            degraded_doc_id=degraded_id,
        )

        out_cache_path = output_dir / f"{degraded_id}.json"
        with open(out_cache_path, "w", encoding="utf-8") as f:
            f.write(corrupted_record.model_dump_json(indent=2))

        record = DegradationRecord(
            doc_id=clean_ocr_record.doc_id,
            track=DegradationTrack.OCR_TEXT,
            degradation_type=DegradationType.OCR_CHARACTER_CORRUPTION,
            severity=severity,
            seed=seed,
            applied_params=params,
            output_ocr_cache=str(out_cache_path),
            metadata={"total_lines": len(corrupted_record.lines), "degraded_doc_id": degraded_id},
        )
        return corrupted_record, record
