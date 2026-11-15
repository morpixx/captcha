"""Click / coordinates solver: centre of the best detection for a target class.

The backend is whatever the unified detector interface resolved: ONNX RT-DETR
in production, Ultralytics only behind an explicit dev opt-in. This module
knows nothing about either.
"""

from __future__ import annotations

import time
from typing import ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ModelInferenceError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo
from captcha.solvers.detection import detector as detector_api


class ClickSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="click-detector",
        types=(CaptchaType.CLICK, CaptchaType.COORDINATES),
        http=True,
        needs_js_runtime=False,
        needs_gpu=False,
        experimental=False,
        version="0.2.0",
    )

    DEFAULT_MODEL = "click-v1"

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.monotonic()
        payload = task.payload or {}
        img_b64 = payload.get("image") or payload.get("image_b64") or payload.get("img") or payload.get("body")
        target = payload.get("target") if payload.get("target") is not None else payload.get("prompt")

        if not img_b64:
            raise UnsolvableError("click payload missing 'image'", context={"keys": sorted(payload.keys())})
        if target is None:
            raise UnsolvableError("click payload missing 'target'", context={"keys": sorted(payload.keys())})

        conf = detector_api.coerce_confidence(payload.get("conf"))
        rgb = detector_api.decode_image(str(img_b64))
        detector = detector_api.detector_for_task(ctx, payload, self.DEFAULT_MODEL)
        class_ids = detector_api.resolve_class_ids(detector.class_names, target)
        detection = detector.predict(rgb, conf=conf, classes=class_ids)
        index = detection.best(class_ids=class_ids)
        if index is None:
            raise ModelInferenceError(
                "no detections for target",
                context={"target": target, "conf": conf, "model": detector.name},
            )

        cx, cy = (float(value) for value in detection.points[index])
        latency_ms = (time.monotonic() - started) * 1000.0
        return SolverResult(
            coords=[(cx, cy)],
            extra={
                "confidence": float(detection.scores[index]),
                "bbox": [float(value) for value in detection.boxes[index]],
                "model": detector.name,
                "n_detections": len(detection),
            },
            latency_ms=latency_ms,
        )
