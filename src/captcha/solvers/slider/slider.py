"""Slider captcha solver — classical CV for gap, humanized trajectory for submit.

One `SliderSolver` handles slider / Capy / KeyCAPTCHA / Lemin / Cutcaptcha-simple
/ DataDome-slider at the type level. Vendor-specific payload quirks live in the
per-vendor branch at the top of `solve_http`.
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
        version="0.1.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.monotonic()
        bg_b64, piece_b64, piece_width, start_xy = _extract_images(task)

        debug: dict[str, Any] = {}
        x_off = detect_gap(bg_b64, piece_b64, piece_width_hint=piece_width, debug=debug)

        # Some vendors return x relative to the slider track's left edge, not
        # the bg image. If caller passed `bg_offset_x` in payload, apply it.
        bg_offset_x = int(task.payload.get("bg_offset_x", 0))
        final_x = x_off - bg_offset_x
        if final_x <= 0:
            raise UnsolvableError(
                "gap resolved to non-positive offset after bg_offset_x correction",
                context={"raw": x_off, "bg_offset_x": bg_offset_x},
            )

        trajectory = build_trajectory(
            final_x,
            start=start_xy,
            seed=task.payload.get("seed"),
        )

        latency_ms = (time.monotonic() - t0) * 1000.0
        return SolverResult(
            offset=float(final_x),
            extra={
                "trajectory": [[x, y, t] for x, y, t in trajectory],
                "match_score": debug.get("score"),
                "piece_bbox": debug.get("piece_bbox"),
                "bg_shape": debug.get("bg_shape"),
            },
            latency_ms=latency_ms,
        )


def _extract_images(task: Task) -> tuple[str, str, int | None, tuple[float, float]]:
    """Normalise payload across vendors into (bg_b64, piece_b64, piece_width, start_xy)."""
    p = task.payload
    # Canonical.
    bg = p.get("bg") or p.get("background") or p.get("bg_image") or p.get("imgUrl")
    piece = p.get("piece") or p.get("slice") or p.get("puzzle") or p.get("pieceImg")
    # Capy puts them under different names; KeyCAPTCHA calls the piece "pz".
    if bg is None:
        bg = p.get("capy_bg") or p.get("fullbg")
    if piece is None:
        piece = p.get("capy_piece") or p.get("pz")

    if not bg or not piece:
        raise UnsolvableError(
            "slider payload missing bg or piece image",
            context={"keys": sorted(p.keys())},
        )
    piece_width = p.get("piece_width")
    piece_y = float(p.get("piece_y", 0.0))
    start_xy = (float(p.get("start_x", 0.0)), piece_y)
    return str(bg), str(piece), int(piece_width) if piece_width is not None else None, start_xy
