"""Draw-around solver: trace the detected object with a rectangle polygon.

The unified detector returns boxes, not masks (RT-DETR is a box detector). The
polygon is therefore the four corners of the best box, which is exactly what
the pointer animation needs and what the previous bbox fallback did when no
segmentation head was available.
"""

from __future__ import annotations

import time
from typing import ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ModelInferenceError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo
from captcha.solvers.detection import detector as detector_api


class DrawAroundSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="draw-around-detector",
        types=(CaptchaType.DRAW_AROUND,),
        http=True,
        needs_js_runtime=False,
        needs_gpu=False,
        experimental=False,
        version="0.2.0",
    )

    DEFAULT_MODEL = "draw-v1"

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.monotonic()
        payload = task.payload or {}
        img_b64 = payload.get("image") or payload.get("image_b64") or payload.get("img") or payload.get("body")
        target = payload.get("target") if payload.get("target") is not None else payload.get("prompt")

        if not img_b64:
            raise UnsolvableError("draw payload missing 'image'", context={"keys": sorted(payload.keys())})
        if target is None:
            raise UnsolvableError("draw payload missing 'target'", context={"keys": sorted(payload.keys())})

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

        x1, y1, x2, y2 = (float(value) for value in detection.boxes[index])
        polygon = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        latency_ms = (time.monotonic() - started) * 1000.0
        return SolverResult(
            coords=polygon,
            extra={
                "confidence": float(detection.scores[index]),
                "model": detector.name,
                "n_vertices": len(polygon),
                "source": "bbox_rect",
            },
            latency_ms=latency_ms,
        )
