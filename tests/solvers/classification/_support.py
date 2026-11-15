"""Helpers for classification tests: no torch, no network, no weights."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from captcha.adapters.weights.meta import ModelMeta
from captcha.domain.errors import ConfigError
from captcha.solvers.classification.grid import GridHeadContract, GridHeadSpec, GridShape
from captcha.solvers.classification.head_meta import head_spec_from_meta


def make_meta(
    contract: GridHeadContract,
    labels: list[str],
    *,
    threshold: float | None = None,
    shape: GridShape | None = None,
) -> ModelMeta:
    input_schema: dict[str, Any] = {"labels": list(labels)}
    if threshold is not None:
        input_schema["threshold"] = threshold
    if shape is not None:
        input_schema["grid"] = shape.to_dict()
    return ModelMeta(
        name=contract.name,
        version="v1",
        framework="safetensors",
        s3_key=f"{contract.name}/v1/weights.safetensors",
        sha256="0" * 64,
        input_schema=input_schema,
        labels={label: str(index) for index, label in enumerate(labels)},
    )


def make_spec(
    contract: GridHeadContract,
    labels: list[str],
    *,
    threshold: float | None = None,
    shape: GridShape | None = None,
) -> GridHeadSpec:
    return head_spec_from_meta(contract, make_meta(contract, labels, threshold=threshold, shape=shape))


class FakeHead:
    """Drop-in for LoRAHead: contract-driven, deterministic, torch-free."""

    def __init__(self, spec: GridHeadSpec, picked: dict[str, list[int]]) -> None:
        self.spec = spec
        self.name = spec.name
        self.version = "v1"
        self.picked = picked

    @property
    def labels(self) -> tuple[str, ...]:
        return self.spec.labels

    @property
    def threshold(self) -> float:
        return self.spec.threshold

    @property
    def shape(self) -> GridShape:
        return self.spec.shape

    def pick_tiles_for_prompt(
        self,
        features: Any,
        prompt: str,
        *,
        threshold: float | None = None,
        shape: GridShape | None = None,
    ) -> list[int]:
        spec = self.spec if shape is None else self.spec.with_shape(shape)
        spec.resolve(prompt)  # exercise the real prompt-matching path
        probs = [0.05] * spec.shape.tile_count
        for index in self.picked.get(prompt, ()):
            probs[index] = 0.95
        return spec.select(probs, threshold=threshold)


class FakeBackbone:
    feature_dim = 768
    model_name = "fake-vit-b16"

    def encode(self, batch: Any) -> list[int]:
        return list(range(len(batch)))


class FakeModels:
    """Registry seam: returns a ModelMeta, fakes download/artifact failures."""

    def __init__(
        self,
        *,
        meta: ModelMeta | None = None,
        meta_error: Exception | None = None,
        download_error: Exception | None = None,
        artifact: Any = None,
        artifact_error: Exception | None = None,
    ) -> None:
        self.meta = meta
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
        return Path("fake.safetensors")

    def get_artifact(self, name: str, version: str = "latest") -> Any:
        if self.artifact_error is not None:
            raise self.artifact_error
        return self.artifact


def solver_context(models: Any = None) -> Any:
    from captcha.solvers.base import SolverContext

    return SolverContext(
        models=models,
        config=SimpleNamespace(classification_device="cpu"),
        logger=None,
    )

def patch_backbone(monkeypatch: Any) -> None:
    """Keep the solver's real head loader while removing the ViT/tensor work."""
    from captcha.solvers.classification import base_grid

    monkeypatch.setattr(base_grid, "tiles_to_batch", lambda tiles, device="cpu": tiles)
    monkeypatch.setattr(base_grid.ViTBackbone, "get", classmethod(lambda cls, **kwargs: FakeBackbone()))


def install_fake_heads(
    monkeypatch: Any,
    heads: dict[str, tuple[GridHeadSpec, dict[str, list[int]]]],
) -> None:
    """Route LoRAHead.load to one FakeHead per head name (golden-harness runs)."""
    from captcha.solvers.classification import base_grid

    class _Loader:
        @classmethod
        def load(
            cls,
            registry: Any,
            *,
            name: str,
            backbone: Any,
            version: str = "latest",
            device: str = "cpu",
            contract: GridHeadContract | None = None,
        ) -> FakeHead:
            assert contract is not None and contract.name == name
            spec, picked = heads[name]
            return FakeHead(spec, picked)

    monkeypatch.setattr(base_grid, "LoRAHead", _Loader)
    patch_backbone(monkeypatch)

