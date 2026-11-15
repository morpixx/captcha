"""Helpers for rotate tests: fake torch/onnxruntime, no weights, no network."""

from __future__ import annotations

import base64
import io
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from PIL import Image

from captcha.adapters.weights.meta import ModelMeta
from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ConfigError


class FakeTensor:
    def __init__(self, array: Any) -> None:
        self.array = np.asarray(array, dtype=np.float32)

    def unsqueeze(self, dim: int) -> FakeTensor:
        return FakeTensor(np.expand_dims(self.array, dim))

    def to(self, device: str) -> FakeTensor:
        return self

    def detach(self) -> FakeTensor:
        return self

    def cpu(self) -> FakeTensor:
        return self

    def numpy(self) -> np.ndarray:
        return self.array


class FakeLinear:
    def __init__(self, in_features: int, out_features: int) -> None:
        self.in_features = in_features
        self.out_features = out_features


class FakeModel:
    def __init__(
        self,
        output: Any = (0.0, 1.0),
        *,
        state_load_error: Exception | None = None,
    ) -> None:
        self.output = FakeTensor(output)
        self.fc = FakeLinear(2048, 1000)
        self.state_load_error = state_load_error
        self.loaded_state: Any = None
        self.eval_called = False
        self.device: str | None = None
        self.input: Any = None

    def load_state_dict(self, state: Any) -> None:
        if self.state_load_error is not None:
            raise self.state_load_error
        self.loaded_state = state

    def eval(self) -> FakeModel:
        self.eval_called = True
        return self

    def to(self, device: str) -> FakeModel:
        self.device = device
        return self

    def __call__(self, tensor: Any) -> FakeTensor:
        self.input = tensor
        return self.output


class _InferenceMode:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: Any) -> None:
        return None


def install_fake_torch(
    monkeypatch: Any,
    *,
    output: Any = (0.0, 1.0),
    checkpoint: Any = None,
    load_error: Exception | None = None,
    state_load_error: Exception | None = None,
) -> FakeModel:
    model = FakeModel(output, state_load_error=state_load_error)
    state = {"fc.weight": object()} if checkpoint is None else checkpoint

    def torch_load(path: str, *, map_location: Any = None, weights_only: bool = True) -> Any:
        if load_error is not None:
            raise load_error
        return state

    torch = SimpleNamespace(
        load=torch_load,
        from_numpy=lambda array: FakeTensor(array),
        inference_mode=lambda: _InferenceMode(),
        nn=SimpleNamespace(Linear=FakeLinear),
    )
    models = SimpleNamespace(
        resnet50=lambda weights=None: model,
        ResNet50_Weights=SimpleNamespace(DEFAULT=None),
    )
    torchvision = SimpleNamespace(models=models)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torchvision", torchvision)
    monkeypatch.setitem(sys.modules, "torchvision.models", models)
    return model


class FakeSession:
    def __init__(self, output: Any, *, input_name: str = "input") -> None:
        self.output = output
        self.input_name = input_name
        self.feeds: list[dict[str, np.ndarray]] = []

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=self.input_name)]

    def run(self, output_names: Any, feeds: dict[str, np.ndarray]) -> list[Any]:
        self.feeds.append(feeds)
        return [self.output]


class FakeModels:
    def __init__(
        self,
        *,
        meta: ModelMeta | None = None,
        weights_path: str | Path = "rotate.onnx",
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


def make_meta(
    *,
    name: str = "rotate-v1",
    version: str = "v1",
    framework: str = "onnx",
    schema: dict[str, Any] | None = None,
    layout: str = "single",
) -> ModelMeta:
    return ModelMeta(
        name=name,
        version=version,
        framework=framework,  # type: ignore[arg-type]
        s3_key=f"{name}/{version}/model.{'onnx' if framework == 'onnx' else 'pt'}",
        sha256="0" * 64,
        layout=layout,  # type: ignore[arg-type]
        input_schema=schema or {},
    )


def rgb(level: int = 30, size: int = 64) -> np.ndarray:
    image = np.zeros((size, size, 3), dtype=np.uint8)
    image[:, :, 0] = level
    image[:, :, 1] = 128
    image[:, :, 2] = 255 - level
    return image


def png_b64(level: int = 30, size: int = 64) -> str:
    image = Image.fromarray(rgb(level, size), mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def task(**payload: object) -> Task:
    return Task(id=uuid.uuid4(), type=CaptchaType.ROTATE, payload=dict(payload))
