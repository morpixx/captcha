"""Unified detection interface shared by every detection solver.

One answer shape for every backend:

    boxes    (N, 4) float32, xyxy in input-image pixels
    scores   (N,)   float32, 0..1
    classes  (N,)   int32, row index into Detector.class_names
    points   (N, 2) float32, box centres

Production backend is RT-DETR exported to ONNX (Apache-2.0). Ultralytics is a
dev-only adapter behind _yolo.py: selecting it requires an explicit payload
opt-in and a dev flag, because importing ultralytics drags AGPL-3.0 into the
service. A production artifact that is not ONNX is an ERR_CONFIG, never a quiet
fallback to YOLO.

Failure shape, same as the rest of the visual solvers:
    ConfigError          no registry/meta, unsupported framework, no runtime
    ModelInferenceError  corrupt model, tensor shape/class-vocabulary mismatch
    UnsolvableError      broken payload, target absent from the vocabulary
"""

from __future__ import annotations

import base64
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from PIL import Image

from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError

from .detector_spec import DetectorSpec, spec_from_meta

_ONNX_KINDS = frozenset({"auto", "", "onnx", "rtdetr", "rt-detr"})
DEV_KINDS = frozenset({"ultralytics", "ultralytics-dev", "yolo", "yolo11"})


# ---- result shape -----------------------------------------------------------


@dataclass(slots=True)
class Detection:
    """Detections in input-image pixels. Built through from_boxes()."""

    boxes: np.ndarray
    scores: np.ndarray
    classes: np.ndarray
    points: np.ndarray | None = None

    def __post_init__(self) -> None:
        boxes = np.asarray(self.boxes, dtype=np.float32)
        if boxes.size == 0:
            boxes = boxes.reshape(0, 4)
        if boxes.ndim != 2 or boxes.shape[1] != 4:
            raise ModelInferenceError(
                "detection boxes must be a (N, 4) array",
                context={"shape": tuple(boxes.shape)},
            )
        count = int(boxes.shape[0])
        scores = np.asarray(self.scores, dtype=np.float32).reshape(-1)
        classes = np.asarray(self.classes, dtype=np.int32).reshape(-1)
        if scores.shape[0] != count:
            raise ModelInferenceError(
                "detection scores do not match boxes",
                context={"boxes": count, "scores": int(scores.shape[0])},
            )
        if classes.shape[0] != count:
            raise ModelInferenceError(
                "detection classes do not match boxes",
                context={"boxes": count, "classes": int(classes.shape[0])},
            )
        if not np.isfinite(boxes).all() or not np.isfinite(scores).all():
            raise ModelInferenceError("detection output contains NaN/Inf", context={})
        if count and (np.any(boxes[:, 2] < boxes[:, 0]) or np.any(boxes[:, 3] < boxes[:, 1])):
            raise ModelInferenceError("detection boxes must satisfy x2 >= x1 and y2 >= y1", context={})
        if self.points is None:
            points = np.stack([(boxes[:, 0] + boxes[:, 2]) * 0.5, (boxes[:, 1] + boxes[:, 3]) * 0.5], axis=1)
        else:
            points = np.asarray(self.points, dtype=np.float32)
            if points.ndim != 2 or points.shape != (count, 2):
                raise ModelInferenceError(
                    "detection points must be a (N, 2) array",
                    context={"boxes": count, "shape": tuple(points.shape)},
                )
        self.boxes = boxes
        self.scores = scores
        self.classes = classes
        self.points = points.astype(np.float32, copy=False)

    @classmethod
    def empty(cls) -> Detection:
        return cls(np.zeros((0, 4), np.float32), np.zeros((0,), np.float32), np.zeros((0,), np.int32))

    @classmethod
    def from_boxes(
        cls,
        boxes: Any,
        scores: Any,
        classes: Any,
        *,
        image_hw: tuple[int, int] | None = None,
    ) -> Detection:
        """Clamp to the image, drop degenerate/out-of-frame rows, derive points."""
        raw = np.asarray(boxes, dtype=np.float32)
        if raw.size == 0:
            return cls.empty()
        if raw.ndim != 2 or raw.shape[1] != 4:
            raise ModelInferenceError(
                "detection boxes must be a (N, 4) array",
                context={"shape": tuple(raw.shape)},
            )
        values = np.array(raw, dtype=np.float32, copy=True)
        score_arr = np.asarray(scores, dtype=np.float32).reshape(-1)
        class_arr = np.asarray(classes, dtype=np.int32).reshape(-1)
        if score_arr.shape[0] != values.shape[0] or class_arr.shape[0] != values.shape[0]:
            raise ModelInferenceError(
                "detector returned mismatched boxes/scores/classes",
                context={
                    "boxes": int(values.shape[0]),
                    "scores": int(score_arr.shape[0]),
                    "classes": int(class_arr.shape[0]),
                },
            )
        if image_hw is not None:
            height, width = int(image_hw[0]), int(image_hw[1])
            values[:, [0, 2]] = np.clip(values[:, [0, 2]], 0.0, float(width))
            values[:, [1, 3]] = np.clip(values[:, [1, 3]], 0.0, float(height))
        keep = (values[:, 2] > values[:, 0]) & (values[:, 3] > values[:, 1])
        return cls(values[keep], score_arr[keep], class_arr[keep])

    def __len__(self) -> int:
        return int(self.boxes.shape[0])

    def filtered(self, *, conf: float = 0.0, class_ids: Sequence[int] | None = None) -> Detection:
        keep = self.scores >= float(conf)
        if class_ids is not None:
            keep &= np.isin(self.classes, np.asarray(list(class_ids), dtype=np.int32))
        if not bool(keep.any()):
            return Detection.empty()
        return Detection(self.boxes[keep], self.scores[keep], self.classes[keep])

    def best(self, *, class_ids: Sequence[int] | None = None) -> int | None:
        """Index of the highest-score row, restricted to classes when given."""
        if not len(self):
            return None
        mask = np.ones(len(self), dtype=bool)
        if class_ids is not None:
            mask = np.isin(self.classes, np.asarray(list(class_ids), dtype=np.int32))
        if not bool(mask.any()):
            return None
        rows = np.where(mask)[0]
        return int(rows[int(self.scores[rows].argmax())])


class Detector(Protocol):
    """Everything a detection solver is allowed to assume about a backend."""

    name: str
    class_names: tuple[str, ...]

    def predict(
        self,
        rgb: np.ndarray,
        *,
        conf: float | None = None,
        classes: Sequence[int] | None = None,
    ) -> Detection: ...


# ---- payload helpers --------------------------------------------------------


def decode_image(b64: str) -> np.ndarray:
    """Base64 (optionally a data: URL) -> RGB uint8 (H, W, 3)."""
    if not isinstance(b64, str) or not b64:
        raise UnsolvableError("image payload is empty", context={})
    if b64.startswith("data:"):
        _, _, b64 = b64.partition(",")
    try:
        raw = base64.b64decode(b64, validate=False)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image.load()
    except Exception as exc:
        raise UnsolvableError("image decode failed", context={"error": str(exc)}) from exc
    return np.asarray(image, dtype=np.uint8)


def resolve_class_ids(class_names: Sequence[str], target: Any) -> list[int]:
    """Match a payload target (id, name, or list) against a detector vocabulary.

    A missing vocabulary is ERR_CONFIG for string targets: the published meta is
    unusable, not the task. A target that is absent is ERR_UNSOLVABLE: the task
    asks for an object this detector cannot answer for.
    """
    if isinstance(target, (list, tuple)):
        out: list[int] = []
        seen: set[int] = set()
        for item in target:
            for class_id in resolve_class_ids(class_names, item):
                if class_id not in seen:
                    seen.add(class_id)
                    out.append(class_id)
        if not out:
            raise UnsolvableError("no class in target list matched detector vocabulary", context={"target": target})
        return out

    names = tuple(class_names)
    if isinstance(target, bool) or not isinstance(target, (int, str)):
        raise UnsolvableError(
            "detector target must be a class id, a name, or a list of either",
            context={"target": target, "type": type(target).__name__},
        )
    if isinstance(target, int):
        if names:
            if 0 <= target < len(names):
                return [target]
            raise UnsolvableError(
                f"class id {target} outside detector vocabulary",
                context={"target": target, "vocab_size": len(names)},
            )
        if target < 0:
            raise UnsolvableError("class id must be non-negative", context={"target": target})
        return [target]

    text = target.strip().lower()
    if not text:
        raise UnsolvableError("empty target string", context={})
    if not names:
        raise ConfigError(
            "detector has no class vocabulary; publish class_names in meta.json",
            context={"target": target},
        )
    exact = [index for index, name in enumerate(names) if name.lower() == text]
    if exact:
        return exact
    partial = sorted(
        ((index, name) for index, name in enumerate(names) if text in name.lower()),
        key=lambda pair: len(pair[1]),
    )
    if partial:
        return [partial[0][0]]
    raise UnsolvableError(
        f"target '{target}' not found in detector vocabulary",
        context={"target": target, "sample_vocab": list(names[:16])},
    )


def coerce_confidence(value: Any, *, default: float = 0.25) -> float:
    """Payload conf -> [0, 1]; a bad value is ERR_UNSOLVABLE, never ValueError."""
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise UnsolvableError("conf must be a number", context={"conf": value}) from exc
    if not np.isfinite(parsed) or not 0.0 < parsed <= 1.0:
        raise UnsolvableError("conf must be in (0, 1]", context={"conf": parsed})
    return parsed


def detector_for_task(ctx: Any, payload: Mapping[str, Any], default_model: str) -> Detector:
    """Build the detector a solver needs from its payload and context."""
    from .detector_loader import load_detector

    model_name = str(payload.get("model") or default_model)
    version = str(payload.get("model_version") or "latest")
    kind = str(payload.get("detector") or "auto").strip().lower()
    device = str(payload.get("device") or device_for_context(ctx))
    return load_detector(
        getattr(ctx, "models", None),
        model_name,
        version=version,
        device=device,
        kind=kind,
        allow_dev=kind in DEV_KINDS,
    )


def device_for_context(ctx: Any, *, needs_gpu: bool = False) -> str:
    config = getattr(ctx, "config", None)
    if config is not None:
        preferred = getattr(config, "detection_device", None) or getattr(config, "device", None)
        if preferred:
            return str(preferred)
    if needs_gpu:
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except ImportError:
            pass
    return "cpu"


__all__ = [
    "DEV_KINDS",
    "Detection",
    "Detector",
    "DetectorSpec",
    "coerce_confidence",
    "decode_image",
    "detector_for_task",
    "device_for_context",
    "resolve_class_ids",
    "spec_from_meta",
]
