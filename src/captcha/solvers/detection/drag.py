"""Drag-and-drop solver: detect source and destination centres, return a path.

Payload must provide both source_target and dest_target. One detector call
returns every box; the solver picks the best box per class set. The trajectory
is the same humanized bezier used before - detection supplies coordinates only.
"""

from __future__ import annotations

import time
from typing import ClassVar

import numpy as np

from captcha.behavior import bezier_path, humanize_timing
from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ModelInferenceError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo
from captcha.solvers.detection import detector as detector_api


class DragDropSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="drag-drop-detector",
        types=(CaptchaType.DRAG_DROP,),
        http=True,
        needs_js_runtime=False,
        needs_gpu=False,
        experimental=False,
        version="0.2.0",
    )

    DEFAULT_MODEL = "drag-v1"

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.monotonic()
        payload = task.payload or {}
        img_b64 = payload.get("image") or payload.get("image_b64") or payload.get("img") or payload.get("body")
        src_target = payload.get("source_target") or payload.get("source")
        dst_target = payload.get("dest_target") or payload.get("dest")

        if not img_b64:
            raise UnsolvableError("drag payload missing 'image'", context={"keys": sorted(payload.keys())})
        if src_target is None or dst_target is None:
            raise UnsolvableError(
                "drag payload missing source_target or dest_target",
                context={"keys": sorted(payload.keys())},
            )

        conf = detector_api.coerce_confidence(payload.get("conf"))
        n_points = _trajectory_points(payload.get("trajectory_points", 48))
        rgb = detector_api.decode_image(str(img_b64))
        detector = detector_api.detector_for_task(ctx, payload, self.DEFAULT_MODEL)
        src_classes = detector_api.resolve_class_ids(detector.class_names, src_target)
        dst_classes = detector_api.resolve_class_ids(detector.class_names, dst_target)
        detection = detector.predict(rgb, conf=conf, classes=sorted(set(src_classes) | set(dst_classes)))

        src_index = detection.best(class_ids=src_classes)
        dst_index = detection.best(class_ids=dst_classes)
        if src_index is None:
            raise ModelInferenceError(
                "no source detection",
                context={"src": src_target, "model": detector.name},
            )
        if dst_index is None:
            raise ModelInferenceError(
                "no destination detection",
                context={"dst": dst_target, "model": detector.name},
            )

        src_xy = tuple(float(value) for value in detection.points[src_index])
        dst_xy = tuple(float(value) for value in detection.points[dst_index])
        if float(np.hypot(dst_xy[0] - src_xy[0], dst_xy[1] - src_xy[1])) < 1.0:
            raise ModelInferenceError(
                "source and destination detections coincide",
                context={"src": src_xy, "dst": dst_xy, "model": detector.name},
            )

        seed = payload.get("seed")
        rng = np.random.default_rng(None if seed is None else _seed(seed))
        path = bezier_path(src_xy, dst_xy, n_points=n_points, jitter=0.5, curvature=0.18, rng=rng)
        timing = humanize_timing(n_points, mean_ms=16.0, std_ms=5.0, rng=rng)
        trajectory = [[float(x), float(y), int(t)] for (x, y), t in zip(path, timing, strict=False)]

        latency_ms = (time.monotonic() - started) * 1000.0
        return SolverResult(
            coords=[src_xy, dst_xy],
            extra={
                "trajectory": trajectory,
                "src_conf": float(detection.scores[src_index]),
                "dst_conf": float(detection.scores[dst_index]),
                "src_bbox": [float(value) for value in detection.boxes[src_index]],
                "dst_bbox": [float(value) for value in detection.boxes[dst_index]],
                "model": detector.name,
            },
            latency_ms=latency_ms,
        )


def _trajectory_points(value: object) -> int:
    try:
        points = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise UnsolvableError("trajectory_points must be an integer", context={"value": value}) from exc
    if not 2 <= points <= 512:
        raise UnsolvableError("trajectory_points must be in [2, 512]", context={"value": points})
    return points


def _seed(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise UnsolvableError("seed must be an integer", context={"value": value}) from exc
