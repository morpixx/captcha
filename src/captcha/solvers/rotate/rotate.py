"""Rotate captcha solver: ResNet50 sin/cos regressor first, classical second.

The regressor is resolved lazily from ctx.models (rotate-v1, ONNX or torch
state_dict). A worker with an empty bucket still boots: resolution failures are
remembered and surfaced as ERR_CONFIG only when the ML path is actually
required. prefer="auto" falls back to the classical estimator on inference
failure; prefer="ml" and prefer="classical" are strict and never silently swap
the path the caller asked for.
"""

from __future__ import annotations

import base64
import io
import math
import time
from typing import ClassVar

import numpy as np
from PIL import Image

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import CaptchaError, ConfigError, ModelInferenceError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo
from captcha.solvers.rotate.classical import estimate_angle
from captcha.solvers.rotate.regressor import ANGLE_MODEL_NAME, AngleRegressor

_PREFERENCES = frozenset({"auto", "classical", "ml"})


class RotateSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="rotate",
        types=(CaptchaType.ROTATE,),
        http=True,
        needs_js_runtime=False,
        needs_gpu=False,
        experimental=False,
        version="0.2.0",
    )

    def __init__(self) -> None:
        super().__init__()
        self._regressor: AngleRegressor | None = None
        self._resolve_attempted = False
        self._resolve_error: CaptchaError | None = None

    async def prepare(self, ctx: SolverContext) -> None:
        # Optional warmup: never fail worker boot because the bucket is empty.
        self._resolve(ctx)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.monotonic()
        payload = task.payload or {}
        img_b64 = payload.get("image") or payload.get("image_b64") or payload.get("img") or payload.get("body")
        if not img_b64:
            raise UnsolvableError("rotate payload missing 'image'", context={"keys": sorted(payload.keys())})
        prefer = str(payload.get("prefer") or "auto").strip().lower()
        if prefer not in _PREFERENCES:
            raise UnsolvableError(
                "rotate prefer must be auto, classical, or ml",
                context={"prefer": payload.get("prefer")},
            )
        rgb = _decode_rgb(str(img_b64))

        angle: float
        confidence: float
        path: str
        fallback_reason: str | None = None

        if prefer == "classical":
            angle, confidence = _classical(rgb)
            path = "classical"
        else:
            self._resolve(ctx)
            if prefer == "ml":
                if self._regressor is None:
                    raise self._resolve_error or ConfigError(
                        "rotate ML path requested but no regressor is available",
                        context={"model": ANGLE_MODEL_NAME},
                    )
                angle, confidence = self._regressor.predict(rgb)
                path = "ml"
            elif self._regressor is not None:
                try:
                    angle, confidence = self._regressor.predict(rgb)
                    path = "ml"
                except ModelInferenceError as exc:
                    fallback_reason = str(exc)
                    if ctx.logger is not None:
                        ctx.logger.warning("rotate.regressor.failed_fallback", error=fallback_reason)
                    angle, confidence = _classical(rgb)
                    path = "classical_fallback"
            else:
                fallback_reason = str(self._resolve_error) if self._resolve_error else "regressor unavailable"
                angle, confidence = _classical(rgb)
                path = "classical_fallback"

        angle = float(angle) % 360.0
        if not math.isfinite(angle):
            raise ModelInferenceError("rotate solver produced a non-finite angle", context={"path": path})
        confidence = min(1.0, max(0.0, float(confidence)))

        latency_ms = (time.monotonic() - started) * 1000.0
        extra = {
            "path": path,
            "confidence": confidence,
            "model": ANGLE_MODEL_NAME if path == "ml" else None,
        }
        if fallback_reason is not None:
            extra["fallback_reason"] = fallback_reason
        return SolverResult(angle=angle, extra=extra, latency_ms=latency_ms)

    # ---- regressor resolution ----------------------------------------------

    def _resolve(self, ctx: SolverContext) -> None:
        if self._resolve_attempted:
            return
        self._resolve_attempted = True
        if ctx.models is None:
            self._resolve_error = ConfigError(
                "rotate ML path requires ctx.models (captcha.adapters.weights.Registry)",
                context={"model": ANGLE_MODEL_NAME},
            )
            return
        try:
            self._regressor = AngleRegressor.from_registry(ctx.models, name=ANGLE_MODEL_NAME, device=_device(ctx))
        except CaptchaError as exc:
            self._resolve_error = exc
            if ctx.logger is not None:
                ctx.logger.info("rotate.regressor.unavailable", error=str(exc))
        except Exception as exc:  # registry bug is config, not a per-task failure
            self._resolve_error = ConfigError(
                "rotate regressor resolution failed",
                context={"model": ANGLE_MODEL_NAME, "error": str(exc)},
            )
            if ctx.logger is not None:
                ctx.logger.info("rotate.regressor.unavailable", error=str(exc))


def _classical(rgb: np.ndarray) -> tuple[float, float]:
    try:
        angle, confidence = estimate_angle(rgb, full_rotation=True)
    except CaptchaError:
        raise
    except Exception as exc:
        raise ModelInferenceError(
            "classical rotate estimation failed",
            context={"error": str(exc)},
        ) from exc
    return float(angle), float(confidence)


def _device(ctx: SolverContext) -> str:
    config = getattr(ctx, "config", None)
    if config is not None:
        preferred = getattr(config, "rotate_device", None) or getattr(config, "device", None)
        if preferred:
            return str(preferred)
    return "cpu"


def _decode_rgb(b64: str) -> np.ndarray:
    if b64.startswith("data:"):
        _, _, b64 = b64.partition(",")
    try:
        raw = base64.b64decode(b64, validate=False)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image.load()
    except Exception as exc:
        raise UnsolvableError("rotate image decode failed", context={"error": str(exc)}) from exc
    return np.asarray(image, dtype=np.uint8)


__all__ = ["RotateSolver"]
