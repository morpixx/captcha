"""Thin shim around ultralytics.YOLO.

- Lazy import: this module doesn't import ultralytics until load() runs.
- Caches a `(model_name → YOLO)` dict per-process — loading is the expensive bit.
- Handles target resolution: payload passes a string, we match it against the
  model's `names` dict (exact → case-fold → substring, pick the shortest match).
"""

from __future__ import annotations

import base64
import io
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from captcha_common.errors import ModelInferenceError, UnsolvableError


_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()


def load_yolo(models_registry: Any, name: str) -> Any:
    """Download + load YOLO weights by registry name. Thread-safe cached."""
    if models_registry is None:
        raise ModelInferenceError(
            "no model registry in context",
            context={
                "model": name,
                # FILL: wire ctx.models to a captcha_models.Registry pointing at
                #       the project's S3/MinIO bucket before spawning workers.
                "hint": "SolverContext.models is None",
            },
        )

    with _cache_lock:
        cached = _cache.get(name)
        if cached is not None:
            return cached

    try:
        from ultralytics import YOLO  # noqa: PLC0415
    except ImportError as e:
        raise ModelInferenceError(
            "ultralytics not installed; install with the [yolo] extra",
            context={"install": "pip install captcha-solver-detection[yolo]"},
        ) from e

    try:
        weights_path: Path = models_registry.download(name)
    except Exception as e:  # noqa: BLE001 - registry raises its own typed errors
        raise ModelInferenceError(
            f"model weights unavailable: {name}",
            context={"model": name, "error": str(e)},
        ) from e

    try:
        model = YOLO(str(weights_path))
    except Exception as e:  # noqa: BLE001
        raise ModelInferenceError(
            f"YOLO load failed for {name}",
            context={"model": name, "path": str(weights_path), "error": str(e)},
        ) from e

    with _cache_lock:
        _cache[name] = model
    return model


def decode_image(b64: str) -> np.ndarray:
    """Base64 → RGB uint8 (H,W,3)."""
    if b64.startswith("data:"):
        _, _, b64 = b64.partition(",")
    try:
        raw = base64.b64decode(b64, validate=False)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise UnsolvableError("image decode failed", context={"error": str(e)}) from e
    return np.asarray(img, dtype=np.uint8)


def resolve_class_ids(model: Any, target: str | int | list) -> list[int]:
    """Match a target description against the model's class vocabulary.

    `target` may be:
      - int: class id used as-is
      - str: matched against `model.names` (dict[int,str]) case-insensitively
      - list: applied element-wise, deduped
    Raises UnsolvableError if nothing matches.
    """
    if isinstance(target, list):
        out: list[int] = []
        seen: set[int] = set()
        for t in target:
            for cid in resolve_class_ids(model, t):
                if cid not in seen:
                    seen.add(cid)
                    out.append(cid)
        if not out:
            raise UnsolvableError("no class in target list matched model vocabulary",
                                  context={"target": target})
        return out

    names: dict[int, str] = dict(getattr(model, "names", {}) or {})
    if isinstance(target, int):
        if target in names:
            return [target]
        raise UnsolvableError(f"class id {target} unknown to model",
                              context={"target": target, "vocab_size": len(names)})

    t = str(target).strip().lower()
    if not t:
        raise UnsolvableError("empty target string", context={})

    # exact
    exact = [cid for cid, name in names.items() if name.lower() == t]
    if exact:
        return exact
    # substring — pick ones that contain t; tie-break by shortest name
    partial = sorted(
        [(cid, name) for cid, name in names.items() if t in name.lower()],
        key=lambda kv: len(kv[1]),
    )
    if partial:
        return [partial[0][0]]
    raise UnsolvableError(
        f"target '{target}' not found in model vocabulary",
        context={"target": target, "sample_vocab": list(names.values())[:16]},
    )


def predict(model: Any, rgb: np.ndarray, *, conf: float = 0.25, classes: list[int] | None = None) -> Any:
    """Run YOLO .predict in a shape agnostic to version bumps."""
    try:
        results = model.predict(rgb, conf=conf, classes=classes, verbose=False)
    except Exception as e:  # noqa: BLE001
        raise ModelInferenceError(
            "YOLO predict failed",
            context={"error": str(e)},
        ) from e
    if not results:
        return None
    return results[0]
