"""image_to_text - OCR a captcha image to a text string.

Engines are optional and lazy: PaddleOCR by default, TrOCR on request, 'auto'
picks whichever runtime the image actually has. Missing runtime is ERR_CONFIG;
broken weights or a failed inference is ERR_MODEL_INFERENCE. Empty OCR output is
a failed task, never an empty answer.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import time
from typing import Any, ClassVar

from PIL import Image, UnidentifiedImageError

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.solvers.base import Solver, SolverContext, SolverInfo
from captcha.solvers.ocr._engines import available_engines, paddle_read, trocr_read

_MAX_B64_CHARS = 40_000_000  # ~30 MB decoded; a captcha image is orders below this
_MAX_PIXELS = 20_000_000
_MAX_SIDE = 12_000
_ENGINES = ("auto", "paddle", "trocr")


def _b64decode(body: Any) -> bytes:
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    if not isinstance(body, str):
        raise UnsolvableError("image_to_text: body must be a base64 string", context={"got": type(body).__name__})
    text = body.strip()
    if text.startswith("data:"):
        _, _, text = text.partition(",")
    text = "".join(text.split())
    if not text:
        raise UnsolvableError("image_to_text: empty image body")
    if len(text) > _MAX_B64_CHARS:
        raise UnsolvableError(
            "image_to_text: image too large",
            context={"base64_chars": len(text), "cap": _MAX_B64_CHARS},
        )
    padded = text + "=" * ((-len(text)) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        pass
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise UnsolvableError("image_to_text: bad base64", context={"error": str(exc)}) from exc


def _decode_image(payload: dict[str, Any]) -> Image.Image:
    body = payload.get("body") or payload.get("image") or payload.get("base64")
    if not body:
        raise UnsolvableError("image_to_text: payload missing 'body' (base64 image)")
    raw = _b64decode(body)
    if not raw:
        raise UnsolvableError("image_to_text: empty image body")
    try:
        img = Image.open(io.BytesIO(raw))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnsolvableError(f"image_to_text: unreadable image ({exc})") from exc

    width, height = img.size
    if width <= 0 or height <= 0 or width * height > _MAX_PIXELS or max(width, height) > _MAX_SIDE:
        raise UnsolvableError(
            "image_to_text: image dimensions out of range",
            context={"width": width, "height": height, "max_pixels": _MAX_PIXELS},
        )
    try:
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnsolvableError(f"image_to_text: unreadable image ({exc})") from exc
    return img


def _requested_engine(payload: dict[str, Any]) -> str:
    requested = str(payload.get("engine") or "auto").lower()
    if requested not in _ENGINES:
        raise ConfigError(
            f"image_to_text: unknown engine '{requested}' (expected one of {', '.join(_ENGINES)})",
            context={"engine": requested},
        )
    if requested != "auto":
        return requested
    found = available_engines()
    if not found:
        raise ConfigError(
            "image_to_text: no OCR engine installed (paddleocr or torch+transformers); "
            "run the cv lane on a worker image that has one",
            context={"engines": list(_ENGINES)},
        )
    return found[0]


class ImageToTextSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="image_to_text",
        types=(CaptchaType.IMAGE_TO_TEXT,),
        http=True,
        version="0.2.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.perf_counter()
        payload = task.payload or {}
        engine = _requested_engine(payload)
        lang = str(payload.get("lang") or task.language or "en")
        case_sensitive = bool(payload.get("case", False))

        dirs = {
            key: str(payload[key])
            for key in ("det_model_dir", "rec_model_dir", "cls_model_dir")
            if isinstance(payload.get(key), str) and payload[key]
        }
        model_path = payload.get("model_path") or payload.get("model_dir")
        model_path = str(model_path) if isinstance(model_path, str) and model_path else None
        device = getattr(getattr(ctx, "config", None), "worker_cv_device", None) or "cpu"

        image = _decode_image(payload)
        try:
            if engine == "paddle":
                text = await asyncio.to_thread(paddle_read, image, lang, dirs=dirs)
            else:
                text = await asyncio.to_thread(trocr_read, image, device, model_path=model_path)
        except (ConfigError, ModelInferenceError):
            raise
        except Exception as exc:  # an engine bug must not look like an empty answer
            raise ModelInferenceError(
                f"image_to_text: {engine} crashed: {exc}",
                context={"engine": engine, "error_type": type(exc).__name__},
            ) from exc

        text = text.strip()
        if not text:
            raise ModelInferenceError(
                "image_to_text: OCR returned no text",
                context={"engine": engine, "lang": lang},
            )
        if not case_sensitive:
            text = text.lower()

        return SolverResult(
            text=text,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            extra={"engine": engine, "lang": lang},
        )
