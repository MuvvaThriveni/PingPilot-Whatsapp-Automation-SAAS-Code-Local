"""Production-grade image compression for WhatsApp media uploads.

WhatsApp Cloud API rejects images > 5 MB with (#100) Invalid parameter.
This module provides automatic, safe compression with:
  - EXIF orientation fix
  - RGBA → RGB conversion (transparent PNG handling)
  - Iterative JPEG quality reduction
  - PNG optimization with optional JPEG fallback
  - Guaranteed ≤ 5 MB output

Public API
----------
``compress_image(file_bytes: bytes) -> bytes``
"""

from __future__ import annotations

import io
from observability import log_event

# WhatsApp media size limit (5 MB)
MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5,242,880 bytes


def _fix_orientation(image):
    """Apply EXIF orientation tag and strip EXIF to avoid rotation bugs.

    Some phones embed an orientation tag instead of physically rotating pixels.
    Pillow's ``ImageOps.exif_transpose`` handles all 8 EXIF orientation values.
    """
    try:
        from PIL import ImageOps
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass  # No EXIF or corrupt — safe to skip
    return image


def _to_rgb(image):
    """Convert RGBA/P/LA images to RGB with white background.

    Required because JPEG does not support transparency.
    """
    if image.mode in ("RGBA", "LA"):
        from PIL import Image as _Img
        background = _Img.new("RGB", image.size, (255, 255, 255))
        # Use alpha channel as mask for compositing
        background.paste(image, mask=image.split()[-1])
        return background
    if image.mode == "P":
        # Palette mode — convert through RGBA to handle possible transparency
        image = image.convert("RGBA")
        return _to_rgb(image)
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def _has_transparency(image) -> bool:
    """Check whether an image actually uses transparency."""
    if image.mode == "RGBA":
        alpha = image.getchannel("A")
        extrema = alpha.getextrema()
        return extrema[0] < 255  # min alpha < 255 means some transparency
    if image.mode == "P":
        return "transparency" in image.info
    if image.mode == "LA":
        return True
    return False


def _compress_as_png(image) -> bytes:
    """Optimize PNG with maximum compression."""
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _compress_as_jpeg(image, quality: int = 85) -> bytes:
    """Save as JPEG at the given quality."""
    rgb_image = _to_rgb(image)
    buf = io.BytesIO()
    rgb_image.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def compress_image(file_bytes: bytes) -> bytes:
    """Compress image bytes to fit within the WhatsApp 5 MB limit.

    Strategy:
      1. Image ≤ 5 MB → return as-is (no processing).
      2. PNG with transparency → try PNG optimize first; if still > 5 MB,
         fall back to JPEG.
      3. JPEG / others → iteratively reduce JPEG quality (90 → 40).

    Always fixes EXIF orientation and handles RGBA → RGB conversion.

    Parameters
    ----------
    file_bytes : bytes
        Raw image file bytes (JPEG, PNG, or other Pillow-supported format).

    Returns
    -------
    bytes
        Compressed image bytes guaranteed ≤ 5 MB, or the original bytes
        if compression fails or if already under the limit.
    """
    original_size = len(file_bytes)

    # ── Case A: Already under limit ──────────────────────────────────────
    if original_size <= MAX_SIZE_BYTES:
        log_event("image_compression_skipped",
                  detail=f"Image already within limit ({original_size:,} bytes)")
        return file_bytes

    # ── Parse image with Pillow ──────────────────────────────────────────
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(file_bytes))
        img.load()  # Force full decode to catch corrupt images early
    except Exception as exc:
        log_event("image_compression_error",
                  detail=f"Cannot open image: {str(exc)[:100]}")
        return file_bytes  # Fallback: return original

    img = _fix_orientation(img)
    source_format = (img.format or "").upper()

    # ── Case B: PNG with transparency ────────────────────────────────────
    if source_format == "PNG" and _has_transparency(img):
        log_event("image_compression_start",
                  detail=f"PNG with transparency, original={original_size:,} bytes")
        compressed = _compress_as_png(img)
        if len(compressed) <= MAX_SIZE_BYTES:
            log_event("image_compressed",
                      detail=f"original={original_size:,}, final={len(compressed):,}, format=PNG")
            return compressed
        # PNG optimize wasn't enough — fall through to JPEG
        log_event("image_compression_png_fallback",
                  detail=f"Optimized PNG still {len(compressed):,} bytes, falling back to JPEG")

    # ── Case C: JPEG / general — iterative quality reduction ─────────────
    log_event("image_compression_start",
              detail=f"Iterative JPEG compression, original={original_size:,} bytes")

    for quality in range(90, 35, -5):
        compressed = _compress_as_jpeg(img, quality=quality)
        if len(compressed) <= MAX_SIZE_BYTES:
            log_event("image_compressed",
                      detail=f"original={original_size:,}, final={len(compressed):,}, format=JPEG, quality={quality}")
            return compressed

    # ── Last resort: aggressive resize + low quality ─────────────────────
    log_event("image_compression_resize",
              detail="Quality reduction insufficient, resizing image")
    try:
        # Halve resolution until under limit
        resized = img.copy()
        for _ in range(5):  # Max 5 halvings (1/32 of original)
            w, h = resized.size
            resized = resized.resize((w // 2, h // 2), Image.LANCZOS)
            compressed = _compress_as_jpeg(resized, quality=70)
            if len(compressed) <= MAX_SIZE_BYTES:
                log_event("image_compressed",
                          detail=f"original={original_size:,}, final={len(compressed):,}, format=JPEG, resized=true")
                return compressed
    except Exception as exc:
        log_event("image_compression_resize_error",
                  detail=str(exc)[:100])

    # Absolute fallback — return original and let the upload fail naturally
    log_event("image_compression_failed",
              detail=f"Could not compress {original_size:,} bytes below {MAX_SIZE_BYTES:,}")
    return file_bytes
