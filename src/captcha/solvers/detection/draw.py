"""Draw-around solver: return a polygon tracing the detected object.

Uses the YOLO segmentation head when available (model.predict returns .masks),
and falls back to a rectangle approximation of the bbox otherwise. Polygon is
Douglas-Peucker simplified to keep the point count reasonable for pointer
animation.
"""

from __future__ import annotations

import time
from typing import ClassVar

import numpy as np

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ModelInferenceError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo
from captcha.solvers.detection._yolo import decode_image, load_yolo, predict, resolve_class_ids


class DrawAroundSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="draw-around-yolo",
        types=(CaptchaType.DRAW_AROUND,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        needs_gpu=False,
        experimental=False,
        version="0.1.0",
    )

    DEFAULT_MODEL = "draw-v1"

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.monotonic()
        model_name = str(task.payload.get("model") or self.DEFAULT_MODEL)
        img_b64 = task.payload.get("image") or task.payload.get("img") or task.payload.get("body")
        target = task.payload.get("target")
        conf = float(task.payload.get("conf", 0.25))
        epsilon_frac = float(task.payload.get("epsilon_frac", 0.01))

        if not img_b64:
            raise UnsolvableError("draw payload missing 'image'", context={"keys": sorted(task.payload.keys())})
        if target is None:
            raise UnsolvableError("draw payload missing 'target'", context={"keys": sorted(task.payload.keys())})

        rgb = decode_image(str(img_b64))
        model = load_yolo(ctx.models, model_name)
        classes = resolve_class_ids(model, target)
        result = predict(model, rgb, conf=conf, classes=classes)

        if result is None or result.boxes is None or len(result.boxes) == 0:
            raise ModelInferenceError(
                "no detections for target",
                context={"target": target, "conf": conf, "model": model_name},
            )

        boxes = result.boxes
        confs = boxes.conf.cpu().numpy() if hasattr(boxes.conf, "cpu") else boxes.conf
        idx = int(confs.argmax())

        polygon = _polygon_for_index(result, idx, epsilon_frac=epsilon_frac)

        latency_ms = (time.monotonic() - t0) * 1000.0
        return SolverResult(
            coords=polygon,
            extra={
                "confidence": float(confs[idx]),
                "model": model_name,
                "n_vertices": len(polygon),
                "source": "mask" if _has_masks(result) else "bbox_rect",
            },
            latency_ms=latency_ms,
        )


def _has_masks(result) -> bool:  # type: ignore[no-untyped-def]
    masks = getattr(result, "masks", None)
    if masks is None:
        return False
    xy = getattr(masks, "xy", None)
    return xy is not None and len(xy) > 0


def _polygon_for_index(result, idx: int, *, epsilon_frac: float) -> list[tuple[float, float]]:  # type: ignore[no-untyped-def]
    if _has_masks(result):
        poly = result.masks.xy[idx]
        poly_np = np.asarray(poly, dtype=np.float32).reshape(-1, 2)
        peri = _polygon_perimeter(poly_np)
        eps = max(1.0, peri * epsilon_frac)
        return _douglas_peucker(poly_np, eps)

    # Fallback: approximate as the bbox rectangle.
    xyxy = result.boxes.xyxy.cpu().numpy() if hasattr(result.boxes.xyxy, "cpu") else result.boxes.xyxy
    x1, y1, x2, y2 = (float(v) for v in xyxy[idx])
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def _polygon_perimeter(poly: np.ndarray) -> float:
    if poly.shape[0] < 2:
        return 0.0
    rolled = np.roll(poly, -1, axis=0)
    seg = np.linalg.norm(rolled - poly, axis=1)
    return float(seg.sum())


def _douglas_peucker(poly: np.ndarray, epsilon: float) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker; cheap pure-numpy implementation."""
    if poly.shape[0] <= 3:
        return [(float(x), float(y)) for x, y in poly]
    # Use OpenCV if present — otherwise implement manually.
    try:
        import cv2

        pts = poly.reshape(-1, 1, 2).astype(np.float32)
        simplified = cv2.approxPolyDP(pts, epsilon, closed=True).reshape(-1, 2)
        return [(float(x), float(y)) for x, y in simplified]
    except ImportError:
        pass

    def _rdp(points: np.ndarray) -> np.ndarray:
        if points.shape[0] < 3:
            return points
        start, end = points[0], points[-1]
        line = end - start
        norm = float(np.linalg.norm(line))
        d = np.linalg.norm(points - start, axis=1) if norm == 0.0 else np.abs(np.cross(line, points - start) / norm)
        i = int(d.argmax())
        if float(d[i]) > epsilon:
            left = _rdp(points[: i + 1])
            right = _rdp(points[i:])
            return np.vstack([left[:-1], right])
        return np.vstack([start, end])

    out = _rdp(poly)
    return [(float(x), float(y)) for x, y in out]
