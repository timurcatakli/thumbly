import io

from PIL import Image


def encode(image: Image.Image, format: str, quality: int = 90) -> bytes:
    """``format`` is "png" (fast, lossless) or "jpeg" (RGB at ``quality``)."""
    buffer = io.BytesIO()
    if format == "jpeg":
        image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    else:
        image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()
