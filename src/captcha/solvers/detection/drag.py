"""Drag-and-drop solver: detect source and destination centers, return trajectory.

Payload MUST provide both `source_target` and `dest_target`. We run YOLO once,
then pick the best-confidence box for each class. The trajectory is a humanized
cubic bezier via captcha.behavior, suitable for the vendor submit.
"""

from __future__ import annotations

import time
from typing import ClassVar

import numpy as np

from captcha.behavior import bezier_path, humanize_timing
from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ModelInferenceError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo
from captcha.solvers.detection._yolo import decode_image, load_yolo, predict, resolve_class_ids


class DragDropSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="drag-drop-yolo",
        types=(CaptchaType.DRAG_DROP,),
        http=True,

        needs_js_runtime=False,
        needs_gpu=False,
        experimental=False,
        version="0.1.0",
    )

    DEFAULT_MODEL = "drag-v1"

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.monotonic()
        model_name = str(task.payload.get("model") or self.DEFAULT_MODEL)
        img_b64 = task.payload.get("image") or task.payload.get("img") or task.payload.get("body")
        src_target = task.payload.get("source_target") or task.payload.get("source")
        dst_target = task.payload.get("dest_target") or task.payload.get("dest")
        conf = float(task.payload.get("conf", 0.25))
        n_points = int(task.payload.get("trajectory_points", 48))

        if not img_b64:
            raise UnsolvableError("drag payload missing 'image'", context={"keys": sorted(task.payload.keys())})
        if src_target is None or dst_target is None:
            raise UnsolvableError(
                "drag payload missing source_target or dest_target",
                context={"keys": sorted(task.payload.keys())},
            )

        rgb = decode_image(str(img_b64))
        model = load_yolo(ctx.models, model_name)

        src_classes = resolve_class_ids(model, src_target)
        dst_classes = resolve_class_ids(model, dst_target)
        allowed = sorted(set(src_classes) | set(dst_classes))
        result = predict(model, rgb, conf=conf, classes=allowed)

        if result is None or result.boxes is None or len(result.boxes) == 0:
            raise ModelInferenceError(
                "no detections for src/dst targets",
                context={"src": src_target, "dst": dst_target, "model": model_name},
            )

        boxes = result.boxes
        xyxy = boxes.xyxy.cpu().numpy() if hasattr(boxes.xyxy, "cpu") else boxes.xyxy
        confs = boxes.conf.cpu().numpy() if hasattr(boxes.conf, "cpu") else boxes.conf
        cls_ids = boxes.cls.cpu().numpy().astype(int) if hasattr(boxes.cls, "cpu") else np.asarray(boxes.cls, dtype=int)

        src_i = _best(cls_ids, confs, src_classes)
        dst_i = _best(cls_ids, confs, dst_classes)
        if src_i is None:
            raise ModelInferenceError("no source detection", context={"src": src_target, "model": model_name})
        if dst_i is None:
            raise ModelInferenceError("no destination detection", context={"dst": dst_target, "model": model_name})

        src_xy = _center(xyxy[src_i])
        dst_xy = _center(xyxy[dst_i])

        rng = np.random.default_rng(task.payload.get("seed"))
        path = bezier_path(src_xy, dst_xy, n_points=n_points, jitter=0.5, curvature=0.18, rng=rng)
        ts = humanize_timing(n_points, mean_ms=16.0, std_ms=5.0, rng=rng)
        trajectory = [[float(x), float(y), int(t)] for (x, y), t in zip(path, ts, strict=False)]

        latency_ms = (time.monotonic() - t0) * 1000.0
        return SolverResult(
            coords=[src_xy, dst_xy],
            extra={
                "trajectory": trajectory,
                "src_conf": float(confs[src_i]),
                "dst_conf": float(confs[dst_i]),
                "src_bbox": [float(v) for v in xyxy[src_i]],
                "dst_bbox": [float(v) for v in xyxy[dst_i]],
                "model": model_name,
            },
            latency_ms=latency_ms,
        )


def _best(cls_ids: np.ndarray, confs: np.ndarray, allowed: list[int]) -> int | None:
    mask = np.isin(cls_ids, allowed)
    if not mask.any():
        return None
    idxs = np.where(mask)[0]
    best = int(idxs[int(confs[idxs].argmax())])
    return best


def _center(xyxy_row: np.ndarray) -> tuple[float, float]:
    x1, y1, x2, y2 = (float(v) for v in xyxy_row)
    return ((x1 + x2) * 0.5, (y1 + y2) * 0.5)
