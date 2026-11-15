"""Rotate state_dict regressor: resnet50 + fc(2) loading and shape checks."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from _rotate_support import FakeModel, FakeModels, install_fake_torch, make_meta, rgb

from captcha.domain.errors import ConfigError, ModelInferenceError
from captcha.solvers.rotate.regressor import AngleRegressor


def _weights(tmp_path: Any, name: str = "rotate.pt") -> Any:
    path = tmp_path / name
    path.write_bytes(b"fake-state-dict")
    return path


def test_state_dict_path_builds_fc2_and_decodes_angle(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    model = install_fake_torch(monkeypatch, output=(0.5, 0.8660254))
    regressor = AngleRegressor(_weights(tmp_path))

    angle, confidence = regressor.predict(rgb())

    assert angle == pytest.approx(30.0, abs=1e-3)
    assert confidence == pytest.approx(1.0)
    assert model.fc.in_features == 2048
    assert model.fc.out_features == 2
    assert "fc.weight" in model.loaded_state
    assert model.eval_called is True
    assert model.device == "cpu"


def test_full_module_checkpoint_is_used_as_is(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    module = FakeModel(output=(0.0, 1.0))
    install_fake_torch(monkeypatch, checkpoint=module)
    regressor = AngleRegressor(_weights(tmp_path))

    angle, _ = regressor.predict(rgb())
    assert angle == pytest.approx(0.0)
    assert module.eval_called is True


def test_state_dict_mismatch_is_model_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    install_fake_torch(monkeypatch, state_load_error=RuntimeError("unexpected key"))
    regressor = AngleRegressor(_weights(tmp_path))

    with pytest.raises(ModelInferenceError, match="does not match"):
        regressor._load()


def test_torch_load_failure_is_model_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    install_fake_torch(monkeypatch, load_error=RuntimeError("corrupt zip"))
    regressor = AngleRegressor(_weights(tmp_path))

    with pytest.raises(ModelInferenceError, match=r"torch\.load failed"):
        regressor._load()


def test_missing_torch_is_config_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "torchvision", None)
    regressor = AngleRegressor(_weights(tmp_path))

    with pytest.raises(ConfigError, match="torch/torchvision"):
        regressor._load()


def test_missing_weights_file_is_config_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    install_fake_torch(monkeypatch)
    regressor = AngleRegressor(tmp_path / "missing.pt")

    with pytest.raises(ConfigError, match="weights file missing"):
        regressor._load()


def test_unsupported_suffix_is_config_error() -> None:
    regressor = AngleRegressor("rotate.safetensors")
    with pytest.raises(ConfigError, match="suffix"):
        regressor._load()


def test_invalid_checkpoint_type_is_model_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    install_fake_torch(monkeypatch, checkpoint=42)
    regressor = AngleRegressor(_weights(tmp_path))

    with pytest.raises(ModelInferenceError, match="neither a state_dict"):
        regressor._load()


def test_state_dict_output_shape_is_checked(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    install_fake_torch(monkeypatch, output=(0.0, 1.0, 0.0))
    regressor = AngleRegressor(_weights(tmp_path))

    with pytest.raises(ModelInferenceError, match="exactly 2"):
        regressor.predict(rgb())


def test_from_registry_torch_framework(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    model = install_fake_torch(monkeypatch, output=(0.0, -1.0))
    weights = _weights(tmp_path)
    models = FakeModels(
        meta=make_meta(framework="torch", schema={"input_size": 32, "resize_size": 64}),
        weights_path=weights,
    )

    regressor = AngleRegressor.from_registry(models)
    angle, _ = regressor.predict(rgb())

    assert angle == pytest.approx(180.0)
    assert regressor.spec.input_size == 32
    assert model.input is not None
