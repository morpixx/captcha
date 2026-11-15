"""Prompt-conditioned grid head: ViT features in, tile indices out.

Each registered head name (hcaptcha-v1, recaptcha-v1, funcap-v1, awswaf-v1,
shein-v1) resolves to a model in captcha-models. Two layouts are supported
because ml-platform publishes both:

    single   weights.safetensors + meta.json (labels in input_schema or labels)
    bundle   model.safetensors + config.json + release.json + meta.json

At inference we do not rebuild a PEFT model: the head's classifier projects
pooled ViT features directly. Only classifier.weight / classifier.bias are
read, which is the contract docs/ml.md gives the training side. LoRA deltas are
rejected loudly rather than silently ignored: merging them is not implemented.

Failure shape, no fallback predictor:
    ConfigError          no registry/meta/labels, unknown head, LoRA payload
    ModelInferenceError  unreadable/corrupt weights, shape mismatch, bad prompt
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from captcha.domain.errors import CaptchaError, ConfigError, ModelInferenceError

from .backbone import ViTBackbone
from .grid import GridHeadContract, GridHeadSpec, GridShape, head_contract
from .head_meta import head_spec_from_meta

if TYPE_CHECKING:
    import torch


_HEAD_CACHE: dict[tuple[str, str, str], LoRAHead] = {}
_HEAD_LOCK = threading.Lock()


class LoRAHead:
    """Linear classifier on top of a frozen ViT backbone.

    The name is historical: the shipped heads are frozen-backbone logistic
    rows, not LoRA adapters. The public surface is the grid contract: labels,
    prompt matching, threshold, grid shape.
    """

    def __init__(
        self,
        *,
        name: str,
        version: str,
        spec: GridHeadSpec,
        classifier: Any,
    ) -> None:
        self.name = name
        self.version = version
        self.spec = spec
        self._classifier = classifier

    @property
    def contract(self) -> GridHeadContract:
        return self.spec.contract

    @property
    def labels(self) -> tuple[str, ...]:
        return self.spec.labels

    @property
    def label_to_idx(self) -> dict[str, int]:
        return self.spec.label_to_idx

    @property
    def threshold(self) -> float:
        return self.spec.threshold

    @property
    def shape(self) -> GridShape:
        return self.spec.shape

    @classmethod
    def load(
        cls,
        registry: Any,
        *,
        name: str,
        backbone: ViTBackbone,
        version: str = "latest",
        device: str = "cpu",
        contract: GridHeadContract | None = None,
    ) -> LoRAHead:
        """Resolve a head by registry name; cached per (name, version, device)."""
        getter = getattr(registry, "get_meta", None)
        if getter is None:
            raise ConfigError(
                "models registry missing get_meta",
                context={"name": name, "version": version},
            )
        try:
            meta = getter(name, version)
        except CaptchaError:
            raise
        except Exception as exc:
            raise ConfigError(
                "models registry returned unusable head meta",
                context={"name": name, "version": version, "error": str(exc)},
            ) from exc
        resolved_version = getattr(meta, "version", None) or version

        resolved_contract = contract if contract is not None else head_contract(name)
        if resolved_contract.name != name:
            raise ConfigError(
                "head contract does not match the requested head",
                context={"head": name, "contract": resolved_contract.name},
            )

        key = (name, resolved_version, device)
        hit = _HEAD_CACHE.get(key)
        if hit is not None:
            return hit

        with _HEAD_LOCK:
            hit = _HEAD_CACHE.get(key)
            if hit is not None:
                return hit

            weights_path, config = _load_source(registry, name, resolved_version, meta)
            spec = head_spec_from_meta(resolved_contract, meta, config=config)
            classifier = _materialize_head(
                weights_path=weights_path,
                backbone=backbone,
                spec=spec,
                device=device,
            )
            inst = cls(name=name, version=resolved_version, spec=spec, classifier=classifier)
            _HEAD_CACHE[key] = inst
            return inst

    def predict(self, features: torch.Tensor) -> torch.Tensor:
        """Features [N, D] -> class logits [N, C]."""
        try:
            import torch  # noqa: F401
        except ImportError as exc:  # pragma: no cover - env check
            raise ModelInferenceError("torch missing at predict time") from exc

        if features.ndim != 2:
            raise ModelInferenceError(
                "features must be 2D [N, D]",
                context={"shape": tuple(features.shape)},
            )
        logits = self._classifier(features)
        if logits.shape[1] != len(self.spec.labels):
            raise ModelInferenceError(
                "classifier width does not match the label list",
                context={"classes": int(logits.shape[1]), "labels": len(self.spec.labels), "head": self.name},
            )
        return logits

    def pick_tiles_for_prompt(
        self,
        features: torch.Tensor,
        prompt: str,
        *,
        threshold: float | None = None,
        shape: GridShape | None = None,
    ) -> list[int]:
        """Return tile indices whose score for the prompt crosses the threshold.

        Per-tile binary decision: does this tile contain the prompt's object?
        The grid shape is taken from the published head unless the payload
        explicitly asked for another shape the contract allows (reCAPTCHA 4x4).
        """
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - env check
            raise ModelInferenceError("torch missing at pick time") from exc

        spec = self.spec if shape is None else self.spec.with_shape(shape)
        label_idx = spec.resolve(prompt)

        if features.ndim != 2:
            raise ModelInferenceError(
                "features must be 2D [N, D]",
                context={"shape": tuple(features.shape)},
            )
        if int(features.shape[0]) != spec.shape.tile_count:
            raise ModelInferenceError(
                "tile count does not match the head grid",
                context={
                    "head": self.name,
                    "shape": spec.shape.to_dict(),
                    "expected": spec.shape.tile_count,
                    "got": int(features.shape[0]),
                },
            )

        logits = self.predict(features)
        probs = torch.sigmoid(logits[:, label_idx]).detach().cpu().tolist()
        return spec.select(probs, threshold=threshold)


# ---- internal --------------------------------------------------------------


def _load_source(registry: Any, name: str, version: str, meta: Any) -> tuple[Path, dict[str, Any]]:
    """Return (primary weights path, bundle config)."""
    layout = getattr(meta, "layout", "single") or "single"
    if layout == "bundle":
        getter = getattr(registry, "get_artifact", None)
        if getter is None:
            raise ConfigError(
                "models registry cannot read bundle artifacts",
                context={"name": name, "version": version},
            )
        artifact = getter(name, version)
        primary = getattr(artifact, "primary_path", None)
        if primary is None:
            raise ConfigError(
                "bundle artifact has no primary weights file",
                context={"name": name, "version": version},
            )
        config = getattr(artifact, "config", None)
        return Path(primary), dict(config) if isinstance(config, Mapping) else {}

    download = getattr(registry, "download", None)
    if download is None:
        raise ConfigError(
            "models registry missing download",
            context={"name": name, "version": version},
        )
    return Path(download(name, version)), {}


def _materialize_head(
    *,
    weights_path: Path,
    backbone: ViTBackbone,
    spec: GridHeadSpec,
    device: str,
) -> Any:
    """Load classifier.weight/bias from safetensors and build the linear layer."""
    try:
        import torch
        from torch import nn
    except ImportError as exc:
        raise ConfigError(
            "torch extra required to load classification heads",
            context={"hint": "uv pip install captcha-solver-classification[torch]"},
        ) from exc

    try:
        from safetensors.torch import load_file
    except ImportError as exc:
        raise ConfigError(
            "peft extra (safetensors) required to load classification heads",
            context={"hint": "uv pip install captcha-solver-classification[peft]"},
        ) from exc

    try:
        tensors = load_file(str(weights_path), device=device)
    except Exception as exc:
        raise ModelInferenceError(
            "failed to load head safetensors",
            context={"path": str(weights_path), "error": str(exc)},
        ) from exc

    lora_keys = [key for key in tensors if ".lora_" in key]
    if lora_keys:
        raise ConfigError(
            "head weights carry LoRA deltas but merging is not implemented; "
            "publish a frozen-backbone classifier head instead",
            context={"path": str(weights_path), "lora_keys": lora_keys[:8]},
        )

    clf_w = tensors.get("classifier.weight")
    clf_b = tensors.get("classifier.bias")
    if clf_w is None:
        raise ModelInferenceError(
            "head weights missing 'classifier.weight'",
            context={"path": str(weights_path), "keys": list(tensors.keys())[:32]},
        )

    if getattr(clf_w, "ndim", 0) != 2:
        raise ModelInferenceError(
            "classifier.weight must be 2D [C, D]",
            context={"path": str(weights_path), "shape": tuple(clf_w.shape)},
        )
    num_classes, feat_dim = clf_w.shape
    if clf_b is not None and (getattr(clf_b, "ndim", 0) != 1 or int(clf_b.shape[0]) != int(num_classes)):
        raise ModelInferenceError(
            "classifier.bias shape does not match classifier.weight",
            context={"classes": int(num_classes), "bias_shape": tuple(clf_b.shape)},
        )
    if feat_dim != backbone.feature_dim:
        raise ModelInferenceError(
            "head/backbone feature_dim mismatch",
            context={"head_dim": int(feat_dim), "backbone_dim": backbone.feature_dim},
        )
    if num_classes != len(spec.labels):
        raise ModelInferenceError(
            "head classifier width != label count",
            context={"classes": int(num_classes), "labels": len(spec.labels), "head": spec.name},
        )

    classifier = nn.Linear(int(feat_dim), int(num_classes), bias=clf_b is not None).to(device).eval()
    with torch.no_grad():
        classifier.weight.copy_(clf_w.to(device))
        if clf_b is not None:
            classifier.bias.copy_(clf_b.to(device))
    for param in classifier.parameters():
        param.requires_grad_(False)
    return classifier


def clear_head_cache() -> None:
    """Tests only — drop all cached heads."""
    with _HEAD_LOCK:
        _HEAD_CACHE.clear()


__all__ = ["LoRAHead", "clear_head_cache"]
