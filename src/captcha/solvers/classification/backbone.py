"""ViT-B/16 backbone wrapper.

Shared across every grid solver — one instance per (model_name, device) tuple
lives in a module-level cache, so hcaptcha + recaptcha solvers in the same
worker don't each own a 350 MB copy of ViT weights.

Loads from HF hub by default; swap to a local path or a captcha-models registry
name via `from_registry` when the worker ships its own baked backbone.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from captcha.domain.errors import ConfigError, ModelInferenceError

if TYPE_CHECKING:
    import torch


_BACKBONE_CACHE: dict[tuple[str, str], "ViTBackbone"] = {}
_BACKBONE_LOCK = threading.Lock()


class ViTBackbone:
    """Image encoder. Takes a NCHW float batch, returns pooled features [N, D]."""

    def __init__(self, model: Any, *, device: str, feature_dim: int, model_name: str) -> None:
        self._model = model
        self.device = device
        self.feature_dim = feature_dim
        self.model_name = model_name

    @classmethod
    def get(
        cls,
        *,
        device: str = "cpu",
        model_name: str = "google/vit-base-patch16-224-in21k",
    ) -> "ViTBackbone":
        """Return a cached backbone, loading it on first call per (model, device)."""
        key = (model_name, device)
        hit = _BACKBONE_CACHE.get(key)
        if hit is not None:
            return hit
        with _BACKBONE_LOCK:
            hit = _BACKBONE_CACHE.get(key)
            if hit is not None:
                return hit
            inst = cls._load_from_hf(model_name=model_name, device=device)
            _BACKBONE_CACHE[key] = inst
            return inst

    @classmethod
    def from_registry(
        cls,
        registry: Any,
        *,
        name: str = "vit-b16-captcha",
        version: str = "latest",
        device: str = "cpu",
    ) -> "ViTBackbone":
        """Load backbone weights from captcha.adapters.weights.Registry instead of HF."""
        key = (f"registry:{name}:{version}", device)
        hit = _BACKBONE_CACHE.get(key)
        if hit is not None:
            return hit
        with _BACKBONE_LOCK:
            hit = _BACKBONE_CACHE.get(key)
            if hit is not None:
                return hit
            try:
                model = registry.load_pytorch(name, version, device=device)
            except AttributeError as e:
                raise ConfigError(
                    "models registry missing load_pytorch",
                    context={"name": name, "version": version},
                ) from e
            model.eval()
            feature_dim = _probe_feature_dim(model, device)
            inst = cls(model, device=device, feature_dim=feature_dim, model_name=f"registry:{name}:{version}")
            _BACKBONE_CACHE[key] = inst
            return inst

    @classmethod
    def _load_from_hf(cls, *, model_name: str, device: str) -> "ViTBackbone":
        try:
            import torch  # noqa: F401 - needed for .to()
            from transformers import ViTModel
        except ImportError as e:
            raise ConfigError(
                "torch+hf extras required to load ViT backbone",
                context={"hint": "uv pip install captcha-solver-classification[torch,hf]"},
            ) from e

        try:
            model = ViTModel.from_pretrained(model_name, add_pooling_layer=False).to(device).eval()
        except Exception as e:  # noqa: BLE001 - HF throws a zoo
            raise ConfigError(
                f"failed to load ViT backbone '{model_name}'",
                context={"error": str(e)},
            ) from e
        feature_dim = int(model.config.hidden_size)
        return cls(model, device=device, feature_dim=feature_dim, model_name=model_name)

    def encode(self, batch: "torch.Tensor") -> "torch.Tensor":
        """Encode NCHW batch → [N, feature_dim] pooled features (CLS token).

        `batch` must already be preprocessed (ImageNet-normalized, 224x224).
        """
        try:
            import torch
        except ImportError as e:  # pragma: no cover
            raise ModelInferenceError("torch missing at encode time") from e

        if batch.device.type != self.device.split(":")[0]:
            batch = batch.to(self.device)

        with torch.no_grad():
            out = self._model(pixel_values=batch)
            # ViTModel w/o pooling: last_hidden_state [N, seq, D]; take CLS token
            if hasattr(out, "last_hidden_state"):
                feats = out.last_hidden_state[:, 0, :]
            elif hasattr(out, "pooler_output") and out.pooler_output is not None:
                feats = out.pooler_output
            else:
                raise ModelInferenceError(
                    "backbone returned no usable features",
                    context={"model": self.model_name, "keys": list(getattr(out, "__dict__", {}).keys())},
                )
        return feats

    @property
    def raw(self) -> Any:
        """Underlying HF model — exposed so LoRA heads can wrap it."""
        return self._model


def _probe_feature_dim(model: Any, device: str) -> int:
    """Best-effort pull of hidden size from a model loaded from the registry."""
    cfg = getattr(model, "config", None)
    if cfg is not None and hasattr(cfg, "hidden_size"):
        return int(cfg.hidden_size)
    # FILL: if the registry serves a torchscript / safetensors-only checkpoint,
    # run a single forward of a zero tensor to probe output shape; registry
    # meta.json should carry feature_dim once the training pipeline is wired.
    raise ConfigError(
        "cannot determine backbone feature_dim",
        context={"hint": "add feature_dim to model meta.json or expose .config.hidden_size"},
    )


def clear_backbone_cache() -> None:
    """Tests only — drop all cached backbones to free GPU memory."""
    with _BACKBONE_LOCK:
        _BACKBONE_CACHE.clear()
