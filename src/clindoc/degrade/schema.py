"""Pydantic schemas and enums for M1 document degradation and manifests."""

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class SeverityLevel(str, Enum):
    """Explicit severity levels for document degradation."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class DegradationTrack(str, Enum):
    """Separation of experimental benchmark tracks."""
    VISUAL = "visual"
    OCR_TEXT = "ocr_text"


class DegradationType(str, Enum):
    """Catalog of 8 controlled degradation types."""
    GAUSSIAN_BLUR = "gaussian_blur"
    JPEG_COMPRESSION = "jpeg_compression"
    RESOLUTION_REDUCTION = "resolution_reduction"
    ROTATION_SKEW = "rotation_skew"
    CONTRAST_BRIGHTNESS = "contrast_brightness"
    OCR_CHARACTER_CORRUPTION = "ocr_character_corruption"
    PARTIAL_LOCAL = "partial_local"
    COMBINED = "combined"


class DegradationRecord(BaseModel):
    """Metadata record for a single generated degraded document."""
    doc_id: str = Field(..., description="Source document ID")
    track: DegradationTrack = Field(..., description="Benchmark track (visual or ocr_text)")
    degradation_type: DegradationType = Field(..., description="Type of applied degradation")
    severity: SeverityLevel = Field(..., description="Severity level applied")
    seed: int = Field(42, description="Random seed used for deterministic transformation")
    applied_params: dict[str, Any] = Field(default_factory=dict, description="Explicit numeric parameters applied")
    output_pdf: str | None = Field(None, description="Path to generated degraded PDF")
    output_image: str | None = Field(None, description="Path to generated raster image (PNG) for visual QA")
    output_ocr_cache: str | None = Field(None, description="Path to cached OCR record JSON")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional provenance or runtime metadata")


class DegradationManifest(BaseModel):
    """Machine-readable manifest describing a batch degradation run."""
    manifest_id: str
    timestamp: str
    source_dataset: str
    total_records: int
    records: list[DegradationRecord]
    metadata: dict[str, Any] = Field(default_factory=dict)
