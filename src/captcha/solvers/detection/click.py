"""Click / coordinates solver: return the center of the best detection."""

from __future__ import annotations

import time
from typing import ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ModelInferenceError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo
from captcha.solvers.detection._yolo import decode_image, load_yolo, predict, resolve_class_ids


class ClickSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="click-yolo",
        types=(CaptchaType.CLICK, CaptchaType.COORDINATES),
        http=True,
        browser=False,
        needs_js_runtime=False,
        needs_gpu=False,
        experimental=False,
        version="0.1.0",
    )

    DEFAULT_MODEL = "click-v1"

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.monotonic()
        model_name = str(task.payload.get("model") or self.DEFAULT_MODEL)
        img_b64 = task.payload.get("image") or task.payload.get("img") or task.payload.get("body")
        target = task.payload.get("target")
        conf = float(task.payload.get("conf", 0.25))

        if not img_b64:
            raise UnsolvableError("click payload missing 'image'", context={"keys": sorted(task.payload.keys())})
        if target is None:
            raise UnsolvableError("click payload missing 'target'", context={"keys": sorted(task.payload.keys())})

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
        # Pick the highest-confidence box among matched classes.
        xyxy = boxes.xyxy.cpu().numpy() if hasattr(boxes.xyxy, "cpu") else boxes.xyxy
        confs = boxes.conf.cpu().numpy() if hasattr(boxes.conf, "cpu") else boxes.conf
        idx = int(confs.argmax())
        x1, y1, x2, y2 = (float(v) for v in xyxy[idx])
        cx = (x1 + x2) * 0.5
        cy = (y1 + y2) * 0.5

        latency_ms = (time.monotonic() - t0) * 1000.0
        return SolverResult(
            coords=[(cx, cy)],
            extra={
                "confidence": float(confs[idx]),
                "bbox": [x1, y1, x2, y2],
                "model": model_name,
                "n_detections": len(confs),
            },
            latency_ms=latency_ms,
        )
