from __future__ import annotations

import asyncio
import base64
import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from captcha.domain import CaptchaType
from captcha.domain.task import Task, new_task_id
from captcha.solvers.base import SolverContext

paddleocr = pytest.importorskip("paddleocr", reason="paddleocr not installed; skip live OCR test")  # noqa: F841

from captcha.solvers.ocr.image_to_text import ImageToTextSolver  # noqa: E402


def _synth_image(text: str = "ABC123") -> str:
    img = Image.new("RGB", (240, 80), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 10), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_image_to_text_runs() -> None:
    solver = ImageToTextSolver()
    task = Task(
        id=new_task_id(),
        type=CaptchaType.IMAGE_TO_TEXT,
        payload={"body": _synth_image("hello"), "engine": "paddle", "case": False},
    )
    ctx = SolverContext()
    result = asyncio.run(solver.solve_http(task, ctx))
    assert result.text is not None
    # loose assertion: OCR may mis-read synthetic font, just verify pipeline ran
    assert isinstance(result.text, str)
    assert result.latency_ms > 0
