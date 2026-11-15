"""Dev-only Ultralytics adapter. AGPL-3.0 - never a production dependency.

The production detector is ONNX RT-DETR (see onnx_detector.py). This module
exists for local experiments and for comparing a YOLO baseline against the
shipped model; load_detector reaches it only when the payload explicitly asks
for detector="ultralytics" and passes the dev flag. Ultralytics is imported
lazily so a worker without the dev extra never touches it, and the service never
requires AGPL code to solve a task.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from captcha.domain.errors import ConfigError, ModelInferenceError

from .detector import Detection
from .detector_spec import DetectorSpec


class UltralyticsDetector:
    """Thin adapter that maps a YOLO result onto the unified Detection shape."""

    def __init__(self, model: Any, *, name: str, spec: DetectorSpec | None = None) -> None:
        self._model = model
        self.name = name
        self.spec = spec or DetectorSpec()
        names = dict(getattr(model, "names", {}) or {})
        self._names = {int(key): str(value) for key, value in names.items()}

    @classmethod
    def load(cls, weights_path: str | Path, *, spec: DetectorSpec | None = None, name: str = "") -> UltralyticsDetector:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise ConfigError(
                "ultralytics is not installed; it is a dev-only backend",
                context={"install": "pip install ultralytics  # AGPL-3.0, dev only"},
            ) from exc
        path = Path(weights_path)
        if not path.exists():
            raise ConfigError("detector weights file missing", context={"path": str(path)})
        try:
            model = YOLO(str(path))
        except Exception as exc:
            raise ModelInferenceError(
                "ultralytics model load failed",
                context={"path": str(path), "error": str(exc)},
            ) from exc
        return cls(model, name=name or path.stem, spec=spec)

    @property
    def class_names(self) -> tuple[str, ...]:
        if self.spec.class_names:
            return self.spec.class_names
        if not self._names:
            return ()
        highest = max(self._names)
        return tuple(self._names.get(index, "") for index in range(highest + 1))

    def predict(
        self,
        rgb: np.ndarray,
        *,
        conf: float | None = None,
        classes: Sequence[int] | None = None,
    ) -> Detection:
        threshold = self.spec.score_threshold if conf is None else float(conf)
        try:
            results = self._model.predict(
                np.asarray(rgb),
                conf=threshold,
                classes=list(classes) if classes is not None else None,
                verbose=False,
            )
        except Exception as exc:
            raise ModelInferenceError(
                "ultralytics predict failed",
                context={"model": self.name, "error": str(exc)},
            ) from exc
        if not results:
            return Detection.empty()
        boxes = getattr(results[0], "boxes", None)
        if boxes is None or len(boxes) == 0:
            return Detection.empty()
        xyxy = _to_numpy(boxes.xyxy)
        scores = _to_numpy(boxes.conf)
        classes_arr = _to_numpy(boxes.cls).astype(np.int32)
        detection = Detection.from_boxes(xyxy, scores, classes_arr, image_hw=np.asarray(rgb).shape[:2])
        return detection.filtered(conf=threshold, class_ids=classes)


def _to_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


__all__ = ["UltralyticsDetector"]
