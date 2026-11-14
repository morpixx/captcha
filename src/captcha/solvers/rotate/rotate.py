"""Rotate captcha solver.

Classical first, ML second. If `rotate-v1` is present in the model registry
and the payload doesn't force classical, the regressor runs. On regressor
failure (missing weights, bad state_dict) we fall through to the classical
path rather than raising — a working angle is better than an error.
"""

from __future__ import annotations

import base64
import io
import time
from typing import ClassVar

import numpy as np
from PIL import Image

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ModelInferenceError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo
from captcha.solvers.rotate.classical import estimate_angle
from captcha.solvers.rotate.regressor import AngleRegressor

_MODEL_NAME = "rotate-v1"


class RotateSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="rotate",
        types=(CaptchaType.ROTATE,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        needs_gpu=False,
        experimental=False,
        version="0.1.0",
    )

    def __init__(self) -> None:
        super().__init__()
        self._regressor: AngleRegressor | None = None
        self._regressor_tried: bool = False

    async def prepare(self, ctx: SolverContext) -> None:
        # Try to resolve the ML model once, but never fail prepare — regressor is optional.
        if ctx.models is None:
            return
        try:
            weights_path = ctx.models.download(_MODEL_NAME)
            self._regressor = AngleRegressor(weights_path, device="cpu")
        except Exception as e:
            if ctx.logger is not None:
                ctx.logger.info("rotate.regressor.unavailable", error=str(e))
            self._regressor = None
        finally:
            self._regressor_tried = True

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.monotonic()
        img_b64 = task.payload.get("image") or task.payload.get("img") or task.payload.get("body")
        if not img_b64:
            raise UnsolvableError("rotate payload missing 'image'", context={"keys": sorted(task.payload.keys())})

        rgb = _decode_rgb(str(img_b64))
        prefer = (task.payload.get("prefer") or "").lower() or None

        angle: float
        path: str
        confidence: float

        if prefer == "classical" or self._regressor is None:
            angle, confidence = estimate_angle(rgb, full_rotation=True)
            path = "classical"
        else:
            try:
                angle, confidence = self._regressor.predict(rgb)
                path = "ml"
            except ModelInferenceError as e:
                if ctx.logger is not None:
                    ctx.logger.warning("rotate.regressor.failed_fallback", error=str(e))
                angle, confidence = estimate_angle(rgb, full_rotation=True)
                path = "classical_fallback"

        latency_ms = (time.monotonic() - t0) * 1000.0
        return SolverResult(
            angle=float(angle),
            extra={"path": path, "confidence": float(confidence), "model": _MODEL_NAME if path == "ml" else None},
            latency_ms=latency_ms,
        )


def _decode_rgb(b64: str) -> np.ndarray:
    if b64.startswith("data:"):
        _, _, b64 = b64.partition(",")
    try:
        raw = base64.b64decode(b64, validate=False)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:
        raise UnsolvableError("rotate image decode failed", context={"error": str(e)}) from e
    return np.asarray(img, dtype=np.uint8)
