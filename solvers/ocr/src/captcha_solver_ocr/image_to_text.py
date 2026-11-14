"""image_to_text — OCR a captcha image to a text string."""

from __future__ import annotations

import base64
import binascii
import io
import time
from typing import ClassVar

from PIL import Image, UnidentifiedImageError

from captcha_common import CaptchaType, SolverResult, Task
from captcha_common.enums import SolveMode
from captcha_common.errors import ModelInferenceError, UnsolvableError
from captcha_solver_base.solver import Solver, SolverContext, SolverInfo

from captcha_solver_ocr._engines import paddle_read, trocr_read


def _decode_image(payload: dict) -> Image.Image:
    body = payload.get("body") or payload.get("image") or payload.get("base64")
    if not body:
        raise UnsolvableError("image_to_text: payload missing 'body' (base64 image)")
    if isinstance(body, str) and body.startswith("data:"):
        _, _, body = body.partition(",")
    try:
        raw = base64.b64decode(body, validate=False)
    except (binascii.Error, ValueError) as e:
        raise UnsolvableError(f"image_to_text: bad base64 ({e})") from e
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise UnsolvableError(f"image_to_text: unreadable image ({e})") from e
    return img


class ImageToTextSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="image_to_text",
        types=(CaptchaType.IMAGE_TO_TEXT,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        needs_gpu=False,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.perf_counter()
        payload = task.payload or {}
        engine = str(payload.get("engine", "paddle")).lower()
        lang = str(payload.get("lang") or task.language or "en")
        case_sensitive = bool(payload.get("case", False))

        image = _decode_image(payload)

        if engine == "paddle":
            text = paddle_read(image, lang=lang)
        elif engine == "trocr":
            device = getattr(getattr(ctx, "config", None), "worker_cv_device", "cpu") or "cpu"
            text = trocr_read(image, device=device)
        else:
            raise ModelInferenceError(
                f"image_to_text: unknown engine '{engine}' (expected 'paddle' or 'trocr')",
                context={"engine": engine},
            )

        if not case_sensitive:
            text = text.lower()

        return SolverResult(
            text=text,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            extra={"engine": engine, "lang": lang},
        )
