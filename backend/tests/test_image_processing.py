"""Tests for image processing / format conversion (Task 3.2)."""

import io

import pytest
from PIL import Image

from app.media.format import convert_to_webp, resize_image, process_image


class TestConvertToWebp:
    def test_converts_png_to_webp(self):
        img = Image.new("RGB", (100, 100), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        webp_bytes = convert_to_webp(png_bytes)
        result = Image.open(io.BytesIO(webp_bytes))
        assert result.format == "WEBP"

    def test_converts_jpeg_to_webp(self):
        img = Image.new("RGB", (100, 100), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        jpeg_bytes = buf.getvalue()

        webp_bytes = convert_to_webp(jpeg_bytes)
        result = Image.open(io.BytesIO(webp_bytes))
        assert result.format == "WEBP"

    def test_preserves_rgba(self):
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        webp_bytes = convert_to_webp(buf.getvalue())
        result = Image.open(io.BytesIO(webp_bytes))
        assert result.format == "WEBP"


class TestResizeImage:
    def test_no_resize_if_within_limit(self):
        img = Image.new("RGB", (800, 600))
        resized = resize_image(img, max_width=1200)
        assert resized.size == (800, 600)

    def test_resizes_wider_image(self):
        img = Image.new("RGB", (2400, 1600))
        resized = resize_image(img, max_width=1200)
        assert resized.size[0] == 1200
        assert resized.size[1] == 800  # maintains aspect ratio

    def test_exact_max_width(self):
        img = Image.new("RGB", (1200, 900))
        resized = resize_image(img, max_width=1200)
        assert resized.size == (1200, 900)

    def test_maintains_aspect_ratio(self):
        img = Image.new("RGB", (3000, 1000))
        resized = resize_image(img, max_width=1200)
        assert resized.size == (1200, 400)


class TestProcessImage:
    def test_full_pipeline(self):
        """Create image → process → verify WebP + dimensions."""
        img = Image.new("RGB", (2400, 1600), color="green")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        result_bytes = process_image(png_bytes, max_width=1200, quality=85)

        result = Image.open(io.BytesIO(result_bytes))
        assert result.format == "WEBP"
        assert result.size[0] <= 1200

    def test_small_image_not_upscaled(self):
        img = Image.new("RGB", (400, 300), color="yellow")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        result_bytes = process_image(buf.getvalue())
        result = Image.open(io.BytesIO(result_bytes))
        assert result.size == (400, 300)

    def test_quality_setting(self):
        import random
        random.seed(42)
        # Use a noisy image so quality differences are meaningful
        img = Image.new("RGB", (500, 500))
        pixels = img.load()
        for x in range(500):
            for y in range(500):
                pixels[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        high_q = process_image(buf.getvalue(), quality=95)
        low_q = process_image(buf.getvalue(), quality=30)
        # Lower quality should produce smaller file for noisy images
        assert len(low_q) < len(high_q)
