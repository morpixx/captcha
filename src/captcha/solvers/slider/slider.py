"""Slider captcha solver - classical CV for the gap, humanized path for submit.

One SliderSolver handles slider / Capy / KeyCAPTCHA / Lemin / DataDome-slider at
the type level. No model is involved: gap_detect.py does edge/template matching
and this module only normalises vendor payloads and turns the offset into a
trajectory. All payload mistakes become ERR_UNSOLVABLE, never ValueError.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo
from captcha.solvers.slider.gap_detect import detect_gap
from captcha.solvers.slider.trajectory import build_trajectory


class SliderSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="slider-cv",
        types=(
            CaptchaType.SLIDER,
            CaptchaType.CAPY,
            CaptchaType.KEYCAPTCHA,
            CaptchaType.LEMIN,
            CaptchaType.DATADOME_SLIDER,
        ),
        http=True,
        needs_js_runtime=False,
        needs_gpu=False,
        experimental=False,
        version="0.2.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.monotonic()
        payload = task.payload or {}
        bg_b64, piece_b64, piece_width, start_xy = _extract_images(payload)
        backend = str(payload.get("backend") or "auto").strip().lower()
        min_score = _optional_float(payload.get("min_score"), "min_score", default=None)
        if min_score is not None and not 0.0 <= min_score <= 1.0:
            raise UnsolvableError("min_score must be in [0, 1]", context={"min_score": min_score})
        n_points = _optional_int(
            payload.get("trajectory_points"), "trajectory_points", default=42, minimum=2, maximum=512
        )
        seed = _optional_int(payload.get("seed"), "seed", default=None)

        debug: dict[str, Any] = {}
        x_offset = detect_gap(
            bg_b64,
            piece_b64,
            piece_width_hint=piece_width,
            min_score=0.15 if min_score is None else min_score,
            backend=backend,
            debug=debug,
        )

        # Some vendors return x relative to the slider track's left edge, not the
        # background image. bg_offset_x corrects that; a non-positive result means
        # the payload or the match is wrong, not that the slider is at zero.
        bg_offset_x = _optional_int(payload.get("bg_offset_x"), "bg_offset_x", default=0)
        final_x = int(x_offset) - int(bg_offset_x)
        if final_x <= 0:
            raise UnsolvableError(
                "gap resolved to non-positive offset after bg_offset_x correction",
                context={"raw": int(x_offset), "bg_offset_x": int(bg_offset_x)},
            )

        trajectory = build_trajectory(final_x, start=start_xy, n_points=n_points or 42, seed=seed)
        latency_ms = (time.monotonic() - started) * 1000.0
        return SolverResult(
            offset=float(final_x),
            extra={
                "trajectory": [[x, y, t] for x, y, t in trajectory],
                "match_score": debug.get("score"),
                "piece_bbox": debug.get("piece_bbox"),
                "bg_shape": debug.get("bg_shape"),
                "backend": debug.get("backend"),
                "gap_x": int(x_offset),
            },
            latency_ms=latency_ms,
        )


def _extract_images(payload: dict[str, Any]) -> tuple[str, str, int | None, tuple[float, float]]:
    """Normalise vendor payloads into (bg_b64, piece_b64, piece_width, start_xy)."""
    bg = (
        payload.get("bg")
        or payload.get("background")
        or payload.get("bg_image")
        or payload.get("imgUrl")
        or payload.get("image_b64")
    )
    piece = payload.get("piece") or payload.get("slice") or payload.get("puzzle") or payload.get("pieceImg")
    if bg is None:
        bg = payload.get("capy_bg") or payload.get("fullbg")
    if piece is None:
        piece = payload.get("capy_piece") or payload.get("pz")

    if not bg or not piece:
        raise UnsolvableError(
            "slider payload missing bg or piece image",
            context={"keys": sorted(payload.keys())},
        )
    piece_width = _optional_int(payload.get("piece_width"), "piece_width", default=None, minimum=1, maximum=10000)
    piece_y = _optional_float(payload.get("piece_y"), "piece_y", default=0.0)
    start_x = _optional_float(payload.get("start_x"), "start_x", default=0.0)
    return str(bg), str(piece), piece_width, (float(start_x), float(piece_y))


def _optional_int(
    value: Any,
    field: str,
    *,
    default: int | None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise UnsolvableError(f"{field} must be an integer", context={field: value})
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise UnsolvableError(f"{field} must be an integer", context={field: value}) from exc
    if minimum is not None and parsed < minimum:
        raise UnsolvableError(f"{field} must be >= {minimum}", context={field: parsed})
    if maximum is not None and parsed > maximum:
        raise UnsolvableError(f"{field} must be <= {maximum}", context={field: parsed})
    return parsed


def _optional_float(value: Any, field: str, *, default: float | None) -> float | None:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise UnsolvableError(f"{field} must be a number", context={field: value}) from exc
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise UnsolvableError(f"{field} must be finite", context={field: value})
    return parsed
