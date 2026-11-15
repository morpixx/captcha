"""LoRAHead.load layout contract: single-slot and bundle artifacts.

No torch: the tensor materializer is replaced with a recorder. The point is
which file/config the loader picks and which contract it enforces, not the
matmul.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from _support import FakeBackbone, FakeModels, make_meta

from captcha.adapters.weights.meta import ModelMeta
from captcha.domain.errors import ConfigError
from captcha.solvers.classification import head as head_module
from captcha.solvers.classification.grid import HEADS
from captcha.solvers.classification.head import LoRAHead


def _recorder(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_materialize(*, weights_path: Path, backbone: Any, spec: Any, device: str) -> Any:
        captured.update(path=Path(weights_path), spec=spec, device=device)
        return object()

    monkeypatch.setattr(head_module, "_materialize_head", fake_materialize)
    return captured


def _bundle_meta(contract_name: str) -> ModelMeta:
    return ModelMeta(
        name=contract_name,
        version="v2",
        framework="safetensors",
        s3_key=f"{contract_name}/v2/model.safetensors",
        sha256="0" * 64,
        size_bytes=123,
        layout="bundle",
        artifact=contract_name,
        files={"model.safetensors": "0" * 64, "config.json": "1" * 64},
    )


def test_bundle_layout_uses_primary_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _recorder(monkeypatch)
    contract = HEADS["recaptcha-v1"]
    artifact = SimpleNamespace(
        primary_path=Path("cache/recaptcha-v1/v2/model.safetensors"),
        config={"labels": ["Bus", "Crosswalk"], "threshold": 0.33, "grid": "4x4"},
    )
    models = FakeModels(meta=_bundle_meta(contract.name), artifact=artifact)

    head = LoRAHead.load(models, name=contract.name, backbone=FakeBackbone(), contract=contract)

    assert captured["path"] == Path("cache/recaptcha-v1/v2/model.safetensors")
    assert head.labels == ("bus", "crosswalk")
    assert head.threshold == pytest.approx(0.33)
    assert head.shape == HEADS["recaptcha-v1"].shapes[1]  # 4x4
    assert head.version == "v2"


def test_single_layout_uses_meta_labels_and_download(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _recorder(monkeypatch)
    contract = HEADS["hcaptcha-v1"]
    models = FakeModels(meta=make_meta(contract, ["bus", "traffic light"]))

    head = LoRAHead.load(models, name=contract.name, backbone=FakeBackbone(), contract=contract)

    assert captured["path"] == Path("fake.safetensors")
    assert head.labels == ("bus", "traffic light")
    assert head.shape == HEADS["hcaptcha-v1"].default_shape


def test_bundle_without_artifact_reader_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = HEADS["hcaptcha-v1"]

    class MetaOnly:
        def get_meta(self, name: str, version: str = "latest") -> ModelMeta:
            return _bundle_meta(contract.name)

    with pytest.raises(ConfigError, match="cannot read bundle"):
        LoRAHead.load(MetaOnly(), name=contract.name, backbone=FakeBackbone(), contract=contract)


def test_unusable_meta_is_config_error() -> None:
    contract = HEADS["hcaptcha-v1"]

    class Boom:
        def get_meta(self, name: str, version: str = "latest") -> ModelMeta:
            raise ValueError("labels must be strings")

    with pytest.raises(ConfigError, match="unusable head meta"):
        LoRAHead.load(Boom(), name=contract.name, backbone=FakeBackbone(), contract=contract)


def test_contract_name_mismatch_is_config_error() -> None:
    contract = HEADS["hcaptcha-v1"]
    models = FakeModels(meta=make_meta(contract, ["bus"]))

    with pytest.raises(ConfigError, match="does not match"):
        LoRAHead.load(models, name=contract.name, backbone=FakeBackbone(), contract=HEADS["shein-v1"])
