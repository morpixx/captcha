"""Helpers for slider tests: synthetic images, no network, no model."""

from __future__ import annotations

import base64
import io
import uuid

from PIL import Image, ImageDraw

from captcha.domain import CaptchaType, Task
from captcha.solvers.base import SolverContext


def pattern(size: int = 40) -> Image.Image:
    image = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([4, 4, size - 5, size - 5], outline=(255, 255, 255), width=3)
    draw.line([8, size - 8, size - 8, 8], fill=(255, 255, 255), width=2)
    return image


def bg_image(width: int = 240, height: int = 120, gap_x: int = 100, gap_y: int = 40) -> Image.Image:
    image = Image.new("RGB", (width, height), (250, 250, 250))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(160, 160, 160), width=1)
    image.paste(pattern(), (gap_x, gap_y))
    return image


def piece_image() -> Image.Image:
    image = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    image.paste(pattern(), (5, 5))
    return image


def png_b64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def solid_piece_b64() -> str:
    # Alpha 200 (not 255) forces the alpha mask path; the crop is then a solid
    # black square with no internal edges, so matching has no template.
    image = Image.new("RGBA", (40, 40), (0, 0, 0, 200))
    return png_b64(image)


def task(**payload: object) -> Task:
    return Task(id=uuid.uuid4(), type=CaptchaType.SLIDER, payload=dict(payload))


def slider_context() -> SolverContext:
    return SolverContext()
