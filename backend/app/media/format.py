"""Image processing: WebP conversion, resize, quality control."""

import io

from PIL import Image


def convert_to_webp(image_bytes: bytes, quality: int = 85) -> bytes:
    """Convert image bytes to WebP format."""
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality)
    return buf.getvalue()


def resize_image(img: Image.Image, max_width: int = 1200) -> Image.Image:
    """Resize image if wider than max_width, maintaining aspect ratio."""
    if img.size[0] <= max_width:
        return img
    ratio = max_width / img.size[0]
    new_height = int(img.size[1] * ratio)
    return img.resize((max_width, new_height), Image.LANCZOS)


def process_image(
    image_bytes: bytes,
    max_width: int = 1200,
    quality: int = 85,
) -> bytes:
    """Full image processing pipeline: resize + convert to WebP."""
    img = Image.open(io.BytesIO(image_bytes))
    img = resize_image(img, max_width=max_width)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality)
    return buf.getvalue()
