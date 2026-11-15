"""RT-DETR ONNX detector: the production detection path.

Why ONNX and not the Ultralytics loader: RT-DETR is Apache-2.0 and exports
cleanly; Ultralytics YOLO is AGPL-3.0, so it cannot be a production
dependency. This module never imports ultralytics or torch - only
onnxruntime plus numpy/PIL preprocessing.

Accepted output layouts (set in meta.json input_schema / bundle config.json):

    xyxy_score_class  (1, N, 6): x1, y1, x2, y2, score, class
    cxcywh_logits     (1, N, 4+C): cx, cy, w, h (normalized), class logits

Boxes of the xyxy layout are input-image pixels unless boxes_normalized=true.
Every shape/vocabulary mismatch is ERR_MODEL_INFERENCE: a wrong decoder must
not silently emit shifted clicks.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from captcha.domain.errors import ConfigError, ModelInferenceError

from .detector import Detection
from .detector_spec import DetectorSpec


class OnnxDetector:
    """Lazy onnxruntime session + RT-DETR postprocessing."""

    def __init__(
        self,
        weights_path: str | Path,
        spec: DetectorSpec,
        *,
        name: str = "",
        device: str = "cpu",
        providers: Sequence[str] | None = None,
        session: Any | None = None,
    ) -> None:
        self.weights_path = Path(weights_path)
        self.spec = spec
        self.name = name or self.weights_path.stem
        self.device = device
        self._providers = list(providers) if providers is not None else None
        self._session = session
        self._input_name: str | None = None
        self._lock = threading.Lock()

    @property
    def class_names(self) -> tuple[str, ...]:
        return self.spec.class_names

    def predict(
        self,
        rgb: np.ndarray,
        *,
        conf: float | None = None,
        classes: Sequence[int] | None = None,
    ) -> Detection:
        image = np.asarray(rgb)
        if image.ndim != 3 or image.shape[2] != 3:
            raise ModelInferenceError(
                "detector input must be RGB HxWx3",
                context={"shape": tuple(image.shape)},
            )
        threshold = self.spec.score_threshold if conf is None else float(conf)
        session = self._get_session()
        tensor = self._preprocess(image)
        input_name = self._input_name or self._input_name_from(session)
        try:
            outputs = session.run(None, {input_name: tensor})
        except Exception as exc:
            raise ModelInferenceError(
                "RT-DETR onnx inference failed",
                context={"model": self.name, "error": str(exc)},
            ) from exc
        detection = self.decode(_first_output(outputs), image_hw=image.shape[:2], conf=threshold)
        return detection.filtered(conf=threshold, class_ids=classes)

    # ---- session ------------------------------------------------------------

    def _get_session(self) -> Any:
        if self._session is not None:
            return self._session
        with self._lock:
            if self._session is not None:
                return self._session
            try:
                import onnxruntime as ort
            except ImportError as exc:
                raise ConfigError(
                    "onnxruntime not installed; the RT-DETR detector requires it",
                    context={"install": "pip install captcha[onnx]"},
                ) from exc
            if not self.weights_path.exists():
                raise ConfigError(
                    "detector weights file missing",
                    context={"path": str(self.weights_path)},
                )
            providers = self._providers
            if providers is None and self.device.startswith("cuda") and hasattr(ort, "get_available_providers"):
                available = set(ort.get_available_providers())
                providers = [item for item in ("CUDAExecutionProvider", "CPUExecutionProvider") if item in available]
            try:
                self._session = ort.InferenceSession(str(self.weights_path), providers=providers)
            except Exception as exc:
                raise ModelInferenceError(
                    "RT-DETR onnx session init failed",
                    context={"path": str(self.weights_path), "error": str(exc)},
                ) from exc
            return self._session

    @staticmethod
    def _input_name_from(session: Any) -> str:
        try:
            inputs = session.get_inputs()
        except Exception as exc:
            raise ModelInferenceError("onnx session exposes no inputs", context={"error": str(exc)}) from exc
        if not inputs:
            raise ModelInferenceError("onnx session exposes no inputs", context={})
        return str(getattr(inputs[0], "name", "") or "images")

    # ---- preprocessing ------------------------------------------------------

    def _preprocess(self, rgb: np.ndarray) -> np.ndarray:
        size = self.spec.input_size
        try:
            resized = Image.fromarray(np.asarray(rgb, dtype=np.uint8), mode="RGB").resize(
                (size, size), Image.Resampling.BILINEAR
            )
        except Exception as exc:
            raise ModelInferenceError("detector preprocessing failed", context={"error": str(exc)}) from exc
        array = np.asarray(resized, dtype=np.float32) / 255.0
        if self.spec.normalize:
            mean = np.asarray(self.spec.mean, dtype=np.float32)
            std = np.asarray(self.spec.std, dtype=np.float32)
            array = (array - mean) / std
        return np.ascontiguousarray(array.transpose(2, 0, 1)[None, ...], dtype=np.float32)

    # ---- postprocessing -----------------------------------------------------

    def decode(self, raw: Any, *, image_hw: tuple[int, int], conf: float) -> Detection:
        """ONNX output -> Detection in original-image pixels."""
        array = np.asarray(raw, dtype=np.float32)
        if array.ndim == 3 and array.shape[0] == 1:
            array = array[0]
        if array.ndim != 2:
            raise ModelInferenceError(
                "RT-DETR output must be (1, N, K) or (N, K)",
                context={"shape": tuple(np.asarray(raw).shape), "layout": self.spec.output_layout},
            )
        if not np.isfinite(array).all():
            raise ModelInferenceError("RT-DETR output contains NaN/Inf", context={"model": self.name})

        if self.spec.output_layout == "xyxy_score_class":
            boxes, scores, classes = self._decode_xyxy(array, image_hw=image_hw)
        elif self.spec.output_layout == "cxcywh_logits":
            boxes, scores, classes = self._decode_cxcywh_logits(array, image_hw=image_hw)
        else:  # spec_from_meta rejects unknown layouts; keep a hard stop here too
            raise ConfigError("unsupported detector output layout", context={"layout": self.spec.output_layout})

        self._check_classes(classes)
        keep = scores >= float(conf)
        return Detection.from_boxes(boxes[keep], scores[keep], classes[keep], image_hw=image_hw)

    def _decode_xyxy(
        self, array: np.ndarray, *, image_hw: tuple[int, int]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if array.shape[1] != 6:
            raise ModelInferenceError(
                "xyxy_score_class output must have 6 columns",
                context={"shape": tuple(array.shape)},
            )
        height, width = int(image_hw[0]), int(image_hw[1])
        scale_x = float(width) if self.spec.boxes_normalized else float(width) / self.spec.input_size
        scale_y = float(height) if self.spec.boxes_normalized else float(height) / self.spec.input_size
        boxes = np.array(array[:, :4], dtype=np.float32, copy=True)
        boxes[:, [0, 2]] *= scale_x
        boxes[:, [1, 3]] *= scale_y
        scores = array[:, 4]
        classes = array[:, 5].astype(np.int32)
        return boxes, scores, classes

    def _decode_cxcywh_logits(
        self, array: np.ndarray, *, image_hw: tuple[int, int]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if array.shape[1] < 5:
            raise ModelInferenceError(
                "cxcywh_logits output must have 4 box columns plus class logits",
                context={"shape": tuple(array.shape)},
            )
        logits = array[:, 4:]
        if self.spec.class_names and logits.shape[1] != len(self.spec.class_names):
            raise ModelInferenceError(
                "class logit count does not match the detector vocabulary",
                context={"logits": int(logits.shape[1]), "classes": len(self.spec.class_names)},
            )
        classes = logits.argmax(axis=1).astype(np.int32)
        scores = _sigmoid(logits.max(axis=1))
        height, width = int(image_hw[0]), int(image_hw[1])
        # cxcywh_logits is a DETR-style export: box coordinates are normalized
        # to the input; the class scores are raw logits.
        cx = array[:, 0] * float(width)
        cy = array[:, 1] * float(height)
        bw = array[:, 2] * float(width)
        bh = array[:, 3] * float(height)
        boxes = np.stack([cx - bw * 0.5, cy - bh * 0.5, cx + bw * 0.5, cy + bh * 0.5], axis=1)
        return boxes.astype(np.float32), scores.astype(np.float32), classes

    def _check_classes(self, classes: np.ndarray) -> None:
        if not self.spec.class_names:
            return
        vocab = len(self.spec.class_names)
        if classes.size and (int(classes.min()) < 0 or int(classes.max()) >= vocab):
            raise ModelInferenceError(
                "detector produced a class id outside the published vocabulary",
                context={"vocab_size": vocab, "min": int(classes.min()), "max": int(classes.max())},
            )


def _first_output(outputs: Any) -> Any:
    if isinstance(outputs, (list, tuple)):
        if not outputs:
            raise ModelInferenceError("RT-DETR onnx session returned no outputs", context={})
        return outputs[0]
    if isinstance(outputs, np.ndarray):
        return outputs
    raise ModelInferenceError(
        "RT-DETR onnx session returned an unsupported output container",
        context={"type": type(outputs).__name__},
    )


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(values, dtype=np.float32), -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-clipped))


__all__ = ["OnnxDetector"]
