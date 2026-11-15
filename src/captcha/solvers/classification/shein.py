"""Shein's bespoke image-selection captcha.

Simple grid, narrow vocab (consumer-goods photos — shoes, bags, clothing),
decent lighting and no occluders. Easiest head of the five.

Default head: shein-v1 (3x3).
"""

from __future__ import annotations

from typing import Any

from msgspec.structs import replace

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.solvers import SolverContext, SolverInfo

from ._tiles import decode_base64_image
from .base_grid import GridClassifier
from .grid import HEADS


class SheinSolver(GridClassifier):
    info = SolverInfo(
        name="shein",
        types=(CaptchaType.SHEIN,),
        http=True,

        needs_js_runtime=False,
        needs_gpu=True,
        experimental=True,
        version="0.1.0",
    )

    head = HEADS["shein-v1"]

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        result = await super().solve_http(task, ctx)
        payload = task.payload or {}
        image_b64 = payload.get("image_b64") or payload.get("image") or payload.get("body")
        if not isinstance(image_b64, str) or not image_b64:
            return result
        image = decode_base64_image(image_b64)
        extra: dict[str, Any] = result.extra or {}
        coords = tile_centres(
            image.width,
            image.height,
            rows=int(extra.get("rows", 3)),
            cols=int(extra.get("cols", 3)),
            indices=result.indices or [],
        )
        return replace(result, coords=coords) if coords else result


def tile_centres(
    width: int,
    height: int,
    *,
    rows: int,
    cols: int,
    indices: list[int],
) -> list[tuple[int, int]]:
    """Project grid indices to the centre of each selected tile, in pixels."""
    if width <= 0 or height <= 0 or rows <= 0 or cols <= 0:
        return []
    out: list[tuple[int, int]] = []
    for index in indices:
        if index < 0 or index >= rows * cols:
            continue
        row, col = divmod(index, cols)
        out.append((round((col + 0.5) * width / cols), round((row + 0.5) * height / rows)))
    return out
