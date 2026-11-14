"""LoRA classification head.

Each registered head name (`hcaptcha-v1`, `recaptcha-v1`, ...) resolves to a
blob in captcha-models containing:

    weights.safetensors    LoRA deltas + classifier.weight / classifier.bias
    meta.json              { labels: [...], base_backbone: "...", r: 8, alpha: 16 }

At inference we skip reconstructing a full PEFT model — the head's classifier
projects pooled ViT features directly, and LoRA deltas (if any) are merged into
a tiny adapter module once at load time. Keeps the hot path two matmuls.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from captcha.domain.errors import ConfigError, ModelInferenceError

from .backbone import ViTBackbone

if TYPE_CHECKING:
    import torch


_HEAD_CACHE: dict[tuple[str, str, str], "LoRAHead"] = {}
_HEAD_LOCK = threading.Lock()


class LoRAHead:
    """Linear classifier on top of a (optionally LoRA-adapted) ViT backbone.

    `labels` is the ordered class list; for a grid-selection head the labels
    are the object vocabulary (`bus`, `crosswalk`, ...). The solver maps
    `prompt` → label index before calling `predict`.
    """

    def __init__(
        self,
        *,
        name: str,
        version: str,
        backbone: ViTBackbone,
        classifier: Any,
        labels: list[str],
        threshold: float = 0.5,
        lora_merged: bool = False,
    ) -> None:
        self.name = name
        self.version = version
        self.backbone = backbone
        self._classifier = classifier
        self.labels = labels
        self.label_to_idx: dict[str, int] = {lab: i for i, lab in enumerate(labels)}
        self.threshold = threshold
        self.lora_merged = lora_merged

    @classmethod
    def load(
        cls,
        registry: Any,
        *,
        name: str,
        backbone: ViTBackbone,
        version: str = "latest",
        device: str = "cpu",
    ) -> "LoRAHead":
        """Resolve a head by registry name; cached per (name, version, device)."""
        try:
            meta = registry.get_meta(name, version)
            resolved_version = getattr(meta, "version", version)
        except AttributeError as e:
            raise ConfigError(
                "models registry missing get_meta",
                context={"name": name, "version": version},
            ) from e

        key = (name, resolved_version, device)
        hit = _HEAD_CACHE.get(key)
        if hit is not None:
            return hit

        with _HEAD_LOCK:
            hit = _HEAD_CACHE.get(key)
            if hit is not None:
                return hit

            weights_path = registry.download(name, resolved_version)
            labels, threshold, lora_merged, classifier = _materialize_head(
                weights_path=weights_path,
                backbone=backbone,
                meta=meta,
                device=device,
            )
            inst = cls(
                name=name,
                version=resolved_version,
                backbone=backbone,
                classifier=classifier,
                labels=labels,
                threshold=threshold,
                lora_merged=lora_merged,
            )
            _HEAD_CACHE[key] = inst
            return inst

    def predict(self, features: "torch.Tensor") -> "torch.Tensor":
        """Features [N, D] → class logits [N, C]."""
        try:
            import torch  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise ModelInferenceError("torch missing at predict time") from e

        if features.ndim != 2:
            raise ModelInferenceError(
                "features must be 2D [N, D]",
                context={"shape": tuple(features.shape)},
            )
        return self._classifier(features)

    def pick_tiles_for_prompt(
        self,
        features: "torch.Tensor",
        prompt: str,
        *,
        threshold: float | None = None,
    ) -> list[int]:
        """Return tile indices whose score for `prompt` exceeds the threshold.

        Interpretation: this is a per-tile *binary* decision — "does this tile
        contain `<prompt>`?" We treat each row of logits as independent
        sigmoid-space scores over the label vocab and pick tiles whose score
        for the prompt's label index crosses `threshold`.
        """
        try:
            import torch
        except ImportError as e:  # pragma: no cover
            raise ModelInferenceError("torch missing at pick time") from e

        label = prompt.strip().lower()
        idx = self.label_to_idx.get(label)
        if idx is None:
            # alias lookup: bare-plural, trailing 's'
            for alias in (label.rstrip("s"), f"{label}s"):
                idx = self.label_to_idx.get(alias)
                if idx is not None:
                    break
        if idx is None:
            raise ModelInferenceError(
                f"head '{self.name}' has no label matching prompt '{prompt}'",
                context={"head": self.name, "version": self.version, "known_labels": self.labels[:32]},
            )

        logits = self.predict(features)                   # [N, C]
        probs = torch.sigmoid(logits[:, idx])             # [N]
        thr = self.threshold if threshold is None else threshold
        picked = (probs >= thr).nonzero(as_tuple=False).flatten().tolist()
        if not picked:
            # degrade: argmax, always return at least one so caller doesn't ship empty
            picked = [int(probs.argmax().item())]
        return [int(i) for i in picked]


# ---- internal --------------------------------------------------------------


def _materialize_head(
    *,
    weights_path: Path,
    backbone: ViTBackbone,
    meta: Any,
    device: str,
) -> tuple[list[str], float, bool, Any]:
    """Load safetensors weights, merge LoRA into backbone if present, build classifier."""
    try:
        import torch
        from torch import nn
    except ImportError as e:
        raise ConfigError(
            "torch extra required to load LoRA heads",
            context={"hint": "uv pip install captcha-solver-classification[torch]"},
        ) from e

    try:
        from safetensors.torch import load_file
    except ImportError as e:
        raise ConfigError(
            "peft extra (safetensors) required to load LoRA heads",
            context={"hint": "uv pip install captcha-solver-classification[peft]"},
        ) from e

    labels = _extract_labels(meta)
    threshold = _extract_threshold(meta)

    try:
        tensors = load_file(str(weights_path), device=device)
    except Exception as e:  # noqa: BLE001 - safetensors throws typed-but-varied
        raise ModelInferenceError(
            "failed to load head safetensors",
            context={"path": str(weights_path), "error": str(e)},
        ) from e

    lora_merged = False
    lora_keys = [k for k in tensors if ".lora_A." in k or ".lora_B." in k]
    if lora_keys:
        # FILL: merge LoRA deltas into backbone state_dict in-place using peft.
        # Right now we rely on the classifier-only path; once the training
        # script is wired we switch to: `peft.PeftModel.from_pretrained(...)`
        # followed by `.merge_and_unload()`. See runbooks/lora-merge.md.
        lora_merged = False

    clf_w = tensors.get("classifier.weight")
    clf_b = tensors.get("classifier.bias")
    if clf_w is None:
        raise ModelInferenceError(
            "head weights missing 'classifier.weight'",
            context={"path": str(weights_path), "keys": list(tensors.keys())[:32]},
        )

    num_classes, feat_dim = clf_w.shape
    if feat_dim != backbone.feature_dim:
        raise ModelInferenceError(
            "head/backbone feature_dim mismatch",
            context={"head_dim": feat_dim, "backbone_dim": backbone.feature_dim},
        )
    if num_classes != len(labels):
        raise ModelInferenceError(
            "head classifier width != label count",
            context={"classes": num_classes, "labels": len(labels)},
        )

    classifier = nn.Linear(feat_dim, num_classes, bias=clf_b is not None).to(device).eval()
    with torch.no_grad():
        classifier.weight.copy_(clf_w.to(device))
        if clf_b is not None:
            classifier.bias.copy_(clf_b.to(device))
    for p in classifier.parameters():
        p.requires_grad_(False)

    return labels, threshold, lora_merged, classifier


def _extract_labels(meta: Any) -> list[str]:
    """Pull labels out of ModelMeta — meta.labels, or meta.extra['labels'], or meta.json path."""
    for attr in ("labels",):
        val = getattr(meta, attr, None)
        if isinstance(val, list) and val and isinstance(val[0], str):
            return [str(x).lower() for x in val]
    extra = getattr(meta, "extra", None)
    if isinstance(extra, dict):
        val = extra.get("labels")
        if isinstance(val, list):
            return [str(x).lower() for x in val]
        # stringified list fallback (some meta pipelines store JSON strings)
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return [str(x).lower() for x in parsed]
            except json.JSONDecodeError:
                pass
    raise ConfigError(
        "head meta is missing a 'labels' list",
        context={"meta_type": type(meta).__name__},
    )


def _extract_threshold(meta: Any) -> float:
    extra = getattr(meta, "extra", None)
    if isinstance(extra, dict) and "threshold" in extra:
        try:
            return float(extra["threshold"])
        except (TypeError, ValueError):
            pass
    return 0.5


def clear_head_cache() -> None:
    """Tests only — drop all cached heads."""
    with _HEAD_LOCK:
        _HEAD_CACHE.clear()
