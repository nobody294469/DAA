"""Visual document degradation engine using PyMuPDF rasterization and Pillow."""

import io
from pathlib import Path
import random
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
import pymupdf


def rasterize_pdf(pdf_path: str | Path, dpi: int = 200) -> list[Image.Image]:
    """Rasterize a PDF document into a list of PIL Images (one per page)."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found at: {pdf_path}")

    doc = pymupdf.open(str(pdf_path))
    pages: list[Image.Image] = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        pages.append(img)
    return pages


def save_images_as_pdf(
    images: list[Image.Image],
    output_pdf_path: str | Path,
    dpi: int = 150,
) -> None:
    """Save one or more PIL images into a purely raster image-backed PDF without text streams."""
    output_pdf_path = Path(output_pdf_path)
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not images:
        raise ValueError("Cannot save empty image list as PDF")

    rgb_images = [img.convert("RGB") if img.mode != "RGB" else img for img in images]
    first = rgb_images[0]
    rest = rgb_images[1:] if len(rgb_images) > 1 else []

    first.save(
        str(output_pdf_path),
        "PDF",
        resolution=float(dpi),
        save_all=True,
        append_images=rest,
    )


def apply_gaussian_blur(image: Image.Image, radius: float) -> Image.Image:
    """Degradation A: Apply Gaussian blur smoothing."""
    return image.filter(ImageFilter.GaussianBlur(radius=radius))


def apply_jpeg_compression(image: Image.Image, quality: int) -> Image.Image:
    """Degradation B: Apply lossy JPEG compression artifacts."""
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).copy()


def apply_resolution_reduction(
    image: Image.Image, target_dpi: int, base_dpi: int = 200
) -> Image.Image:
    """Degradation C: Downscale resolution and re-upscale inducing pixelation."""
    orig_w, orig_h = image.size
    scale = max(0.05, min(1.0, target_dpi / base_dpi))
    down_w = max(1, int(orig_w * scale))
    down_h = max(1, int(orig_h * scale))

    downscaled = image.resize((down_w, down_h), resample=Image.Resampling.BILINEAR)
    upscaled = downscaled.resize((orig_w, orig_h), resample=Image.Resampling.NEAREST)
    return upscaled


def apply_rotation_skew(
    image: Image.Image, angle_degrees: float, seed: int = 42
) -> Image.Image:
    """Degradation D: Apply affine rotational skew with white background fill."""
    rng = random.Random(seed)
    sign = rng.choice([-1.0, 1.0])
    effective_angle = sign * abs(angle_degrees)
    return image.rotate(
        effective_angle,
        resample=Image.Resampling.BICUBIC,
        expand=False,
        fillcolor=(255, 255, 255),
    )


def apply_contrast_brightness(
    image: Image.Image, contrast_factor: float, brightness_factor: float
) -> Image.Image:
    """Degradation E: Simulate faded toner, low contrast, or overexposed photocopy."""
    enh_c = ImageEnhance.Contrast(image)
    img_c = enh_c.enhance(contrast_factor)
    enh_b = ImageEnhance.Brightness(img_c)
    return enh_b.enhance(brightness_factor)


def apply_partial_local(
    image: Image.Image,
    coverage_ratio: float,
    opacity: float,
    blur_radius: float,
    seed: int = 42,
) -> Image.Image:
    """Degradation G: Apply localized smudge, coffee-stain shadow, or scanner crease."""
    rng = random.Random(seed)
    w, h = image.size

    # Create RGBA overlay
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Deterministic position for localized stain/shadow
    box_w = int(w * min(0.9, max(0.1, coverage_ratio * 1.5)))
    box_h = int(h * min(0.9, max(0.1, coverage_ratio)))

    x0 = rng.randint(int(w * 0.1), max(int(w * 0.1) + 1, int(w * 0.9) - box_w))
    y0 = rng.randint(int(h * 0.2), max(int(h * 0.2) + 1, int(h * 0.8) - box_h))
    x1 = x0 + box_w
    y1 = y0 + box_h

    alpha = int(255 * max(0.0, min(1.0, opacity)))
    # Subtle dark grey / brownish shadow color
    draw.ellipse([x0, y0, x1, y1], fill=(45, 40, 35, alpha))

    # Add a diagonal smudge streak
    streak_y = rng.randint(int(h * 0.2), int(h * 0.7))
    draw.line([(0, streak_y), (w, streak_y + int(h * 0.05))], fill=(60, 55, 50, alpha // 2), width=15)

    # Blur overlay to create realistic smudge gradient
    blurred_overlay = overlay.filter(ImageFilter.GaussianBlur(radius=blur_radius * 2.0))

    base_rgba = image.convert("RGBA")
    composite = Image.alpha_composite(base_rgba, blurred_overlay)
    return composite.convert("RGB")


def apply_combined(
    image: Image.Image,
    params: dict[str, Any],
    seed: int = 42,
) -> Image.Image:
    """Degradation H: Combined realistic real-world scan/fax degradation."""
    skew_angle = params.get("skew_angle", 1.5)
    blur_radius = params.get("blur_radius", 1.0)
    target_dpi = params.get("target_dpi", 80)
    jpeg_quality = params.get("jpeg_quality", 25)
    contrast_factor = params.get("contrast_factor", 0.75)

    # 1. Rotational skew
    img = apply_rotation_skew(image, angle_degrees=skew_angle, seed=seed)
    # 2. Resolution reduction
    img = apply_resolution_reduction(img, target_dpi=target_dpi, base_dpi=200)
    # 3. Gaussian blur
    img = apply_gaussian_blur(img, radius=blur_radius)
    # 4. Contrast adjustment
    img = apply_contrast_brightness(img, contrast_factor=contrast_factor, brightness_factor=1.1)
    # 5. JPEG compression
    img = apply_jpeg_compression(img, quality=jpeg_quality)
    return img
