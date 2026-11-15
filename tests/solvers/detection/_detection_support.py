"""Helpers for detection tests: no torch, no onnxruntime, no network."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from PIL import Image

from captcha.adapters.weights.meta import ModelMeta
from captcha.domain.errors import ConfigError
from captcha.solvers.base import SolverContext
from captcha.solvers.detection import detector as detector_api


def make_meta(
    *,
    name: str = "click-v1",
    version: str = "v1",
    framework: str = "onnx",
    schema: dict[str, Any] | None = None,
    labels: dict[str, str] | None = None,
    layout: str = "single",
) -> ModelMeta:
    return ModelMeta(
        name=name,
        version=version,
        framework=framework,  # type: ignore[arg-type]
        s3_key=f"{name}/{version}/model.onnx",
        sha256="0" * 64,
        layout=layout,  # type: ignore[arg-type]
        input_schema=schema if schema is not None else {"class_names": ["bus", "crosswalk", "traffic light"]},
        labels=labels or {},
    )


class FakeModels:
    """Registry seam: meta + download, with configurable failures."""

    def __init__(
        self,
        *,
        meta: ModelMeta | None = None,
        weights_path: str | Path = "fake.onnx",
        meta_error: Exception | None = None,
        download_error: Exception | None = None,
        artifact: Any = None,
        artifact_error: Exception | None = None,
    ) -> None:
        self.meta = meta
        self.weights_path = Path(weights_path)
        self.meta_error = meta_error
        self.download_error = download_error
        self.artifact = artifact
        self.artifact_error = artifact_error

    def get_meta(self, name: str, version: str = "latest") -> ModelMeta:
        if self.meta_error is not None:
            raise self.meta_error
        if self.meta is None:
            raise ConfigError("meta.json missing", context={"name": name, "version": version})
        return self.meta

    def download(self, name: str, version: str = "latest") -> Path:
        if self.download_error is not None:
            raise self.download_error
        return self.weights_path

    def get_artifact(self, name: str, version: str = "latest") -> Any:
        if self.artifact_error is not None:
            raise self.artifact_error
        return self.artifact


class FakeSession:
    """Minimal onnxruntime.InferenceSession stand-in."""

    def __init__(self, output: Any, *, input_name: str = "images") -> None:
        self.output = output
        self.input_name = input_name
        self.feeds: list[dict[str, np.ndarray]] = []

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=self.input_name)]

    def run(self, output_names: Any, feeds: dict[str, np.ndarray]) -> list[Any]:
        self.feeds.append(feeds)
        return [self.output]


class FakeDetector:
    """Contract-driven detector: deterministic boxes, same interface as ONNX."""

    def __init__(
        self,
        *,
        class_names: tuple[str, ...] = ("bus", "crosswalk", "traffic light"),
        boxes: Any = (),
        scores: Any = (),
        classes: Any = (),
    ) -> None:
        self.name = "fake-detector"
        self.class_names = tuple(class_names)
        self._boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 4)
        self._scores = np.asarray(scores, dtype=np.float32).reshape(-1)
        self._classes = np.asarray(classes, dtype=np.int32).reshape(-1)
        self.calls: list[dict[str, Any]] = []

    def predict(
        self,
        rgb: np.ndarray,
        *,
        conf: float | None = None,
        classes: Any = None,
    ) -> detector_api.Detection:
        self.calls.append(
            {"shape": tuple(rgb.shape), "conf": conf, "classes": None if classes is None else list(classes)}
        )
        detection = detector_api.Detection.from_boxes(
            self._boxes,
            self._scores,
            self._classes,
            image_hw=np.asarray(rgb).shape[:2],
        )
        return detection.filtered(conf=0.0 if conf is None else float(conf), class_ids=classes)


def install_fake_detector(monkeypatch: Any, detector: FakeDetector) -> None:
    monkeypatch.setattr(detector_api, "detector_for_task", lambda *args, **kwargs: detector)


def png_b64(width: int = 320, height: int = 240, color: tuple[int, int, int] = (240, 240, 240)) -> str:
    image = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def solver_context(models: Any = None) -> SolverContext:
    return SolverContext(
        models=models,
        config=SimpleNamespace(detection_device="cpu", device="cpu"),
        logger=None,
    )
