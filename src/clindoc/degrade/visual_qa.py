"""Generate visual QA samples for each degradation type and severity level."""

from pathlib import Path
from clindoc.degrade.engine import DegradationEngine
from clindoc.degrade.schema import DegradationType, SeverityLevel

DEFAULT_SAMPLE_SOURCE = Path("data/rendered/LAB_0001.pdf")
DEFAULT_OUTPUT_SAMPLES_DIR = Path("data/degraded/samples")


def generate_visual_qa_samples(
    source_pdf: Path = DEFAULT_SAMPLE_SOURCE,
    output_dir: Path = DEFAULT_OUTPUT_SAMPLES_DIR,
    seed: int = 42,
) -> list[Path]:
    """Generate representative degraded sample images across all visual degradation types and severities."""
    if not source_pdf.exists():
        # Fallback to MED_0001 if LAB_0001 is missing
        source_pdf = Path("data/med_rendered/MED_0001.pdf")
    if not source_pdf.exists():
        raise FileNotFoundError(f"Source PDF for visual QA not found: {source_pdf}")

    output_dir.mkdir(parents=True, exist_ok=True)
    engine = DegradationEngine()

    visual_types = [
        DegradationType.GAUSSIAN_BLUR,
        DegradationType.JPEG_COMPRESSION,
        DegradationType.RESOLUTION_REDUCTION,
        DegradationType.ROTATION_SKEW,
        DegradationType.CONTRAST_BRIGHTNESS,
        DegradationType.PARTIAL_LOCAL,
        DegradationType.COMBINED,
    ]

    severities = [SeverityLevel.LOW, SeverityLevel.MEDIUM, SeverityLevel.HIGH]
    generated_paths: list[Path] = []

    print(f"Generating Visual QA samples from {source_pdf} into {output_dir}...")
    for deg_type in visual_types:
        for sev in severities:
            rec = engine.apply_visual_degradation(
                clean_pdf_path=source_pdf,
                degradation_type=deg_type,
                severity=sev,
                output_dir=output_dir,
                seed=seed,
            )
            if rec.output_image:
                generated_paths.append(Path(rec.output_image))

    print(f"Successfully generated {len(generated_paths)} visual QA samples.")
    return generated_paths


if __name__ == "__main__":
    generate_visual_qa_samples()
