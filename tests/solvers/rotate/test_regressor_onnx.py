"""Rotate ONNX regressor: sin/cos decoding, shapes, loading errors."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from _rotate_support import FakeModels, FakeSession, make_meta, rgb

from captcha.domain.errors import ConfigError, ModelInferenceError
from captcha.solvers.rotate.regressor import AngleRegressor, RegressorSpec


def _regressor(output: Any) -> AngleRegressor:
    return AngleRegressor("weights.onnx", session=FakeSession(output))


@pytest.mark.parametrize(
    ("sin_value", "cos_value", "expected"),
    ((0.0, 1.0, 0.0), (0.5, 0.8660254, 30.0), (0.0, -1.0, 180.0), (-1.0, 0.0, 270.0)),
)
def test_sin_cos_decoding(sin_value: float, cos_value: float, expected: float) -> None:
    angle, confidence = _regressor([sin_value, cos_value]).predict(rgb())
    assert angle == pytest.approx(expected, abs=1e-3)
    assert confidence == pytest.approx(1.0, abs=1e-6)


def test_batched_output_shape_is_accepted() -> None:
    angle, _ = _regressor([[0.0, 1.0]]).predict(rgb())
    assert angle == pytest.approx(0.0)


def test_preprocess_shape_and_normalization() -> None:
    session = FakeSession([0.0, 1.0])
    regressor = AngleRegressor("weights.onnx", spec=RegressorSpec(input_size=32), session=session)
    regressor.predict(rgb())

    feed = session.feeds[0]["input"]
    assert feed.shape == (1, 3, 32, 32)
    assert feed.dtype == np.float32


def test_wrong_output_shape_is_model_error() -> None:
    with pytest.raises(ModelInferenceError, match="exactly 2"):
        _regressor([0.0, 1.0, 0.0]).predict(rgb())


def test_non_finite_output_is_model_error() -> None:
    with pytest.raises(ModelInferenceError, match="NaN"):
        _regressor([float("nan"), 1.0]).predict(rgb())


def test_degenerate_vector_is_model_error() -> None:
    with pytest.raises(ModelInferenceError, match="degenerate"):
        _regressor([0.0, 0.0]).predict(rgb())


def test_non_rgb_input_is_model_error() -> None:
    with pytest.raises(ModelInferenceError, match="RGB"):
        _regressor([0.0, 1.0]).predict(np.zeros((10, 10), dtype=np.uint8))


def test_missing_runtime_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    with pytest.raises(ConfigError, match="onnxruntime"):
        AngleRegressor("weights.onnx")._load()


def test_missing_weights_file_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(InferenceSession=lambda *a, **k: None))
    regressor = AngleRegressor("does-not-exist.onnx")
    with pytest.raises(ConfigError, match="weights file missing"):
        regressor._load()


def test_session_init_failure_is_model_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    weights = tmp_path / "rotate.onnx"
    weights.write_bytes(b"not-onnx")

    class Boom:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("bad model")

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(InferenceSession=Boom))
    with pytest.raises(ModelInferenceError, match="session init"):
        AngleRegressor(weights)._load()


def test_from_registry_reads_onnx_meta(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    weights = tmp_path / "rotate.onnx"
    weights.write_bytes(b"fake")
    models = FakeModels(
        meta=make_meta(framework="onnx", schema={"input_size": 32}),
        weights_path=weights,
    )
    sessions: list[FakeSession] = []

    def factory(path: str, providers: Any = None) -> FakeSession:
        session = FakeSession([0.5, 0.8660254], input_name="pixel_values")
        sessions.append(session)
        return session

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(InferenceSession=factory))
    regressor = AngleRegressor.from_registry(models)
    angle, _ = regressor.predict(rgb())

    assert angle == pytest.approx(30.0)
    assert regressor.spec.input_size == 32
    assert sessions[0].feeds[0]["pixel_values"].shape == (1, 3, 32, 32)


def test_from_registry_rejects_bad_meta_and_framework() -> None:
    class Boom:
        def get_meta(self, name: str, version: str = "latest") -> Any:
            raise ValueError("bad meta")

    with pytest.raises(ConfigError, match="unusable rotate meta"):
        AngleRegressor.from_registry(Boom())

    class NoGetter:
        pass

    with pytest.raises(ConfigError, match="get_meta"):
        AngleRegressor.from_registry(NoGetter())

    with pytest.raises(ConfigError, match="must be onnx or torch"):
        AngleRegressor.from_registry(FakeModels(meta=make_meta(framework="safetensors")))


def test_spec_validation() -> None:
    with pytest.raises(ConfigError, match="input_size"):
        RegressorSpec.from_meta(make_meta(schema={"input_size": 0}))
    with pytest.raises(ConfigError, match="resize_size"):
        RegressorSpec.from_meta(make_meta(schema={"input_size": 256, "resize_size": 128}))
