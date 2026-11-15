"""Detector artifact resolution: registry -> ONNX session or dev adapter.

ONNX RT-DETR is the only production family. Ultralytics is reachable only with
an explicit kind plus allow_dev=True, which keeps AGPL-3.0 code out of the
production dependency graph. The detector cache is per (name, version, family,
device); clear_detector_cache() is the test seam.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from captcha.domain.errors import CaptchaError, ConfigError

from .detector import _ONNX_KINDS, DEV_KINDS, Detector
from .detector_spec import spec_from_meta

_DETECTOR_CACHE: dict[tuple[str, str, str, str], Detector] = {}
_DETECTOR_LOCK = threading.Lock()


def load_detector(
    registry: Any,
    name: str,
    *,
    version: str = "latest",
    device: str = "cpu",
    kind: str = "auto",
    allow_dev: bool = False,
) -> Detector:
    """Resolve a detector artifact. ONNX is the only production path."""
    if registry is None:
        raise ConfigError(
            "detection solver requires ctx.models (captcha.adapters.weights.Registry)",
            context={"model": name},
        )
    getter = getattr(registry, "get_meta", None)
    if getter is None:
        raise ConfigError("models registry missing get_meta", context={"name": name, "version": version})
    try:
        meta = getter(name, version)
    except CaptchaError:
        raise
    except Exception as exc:
        raise ConfigError(
            "models registry returned unusable detector meta",
            context={"name": name, "version": version, "error": str(exc)},
        ) from exc

    resolved_version = getattr(meta, "version", None) or version
    family = _resolve_family(meta, kind=kind, allow_dev=allow_dev)

    cache_key = (name, str(resolved_version), family, device)
    with _DETECTOR_LOCK:
        cached = _DETECTOR_CACHE.get(cache_key)
        if cached is not None:
            return cached

    weights_path, config = _load_source(registry, name, resolved_version, meta)
    spec = spec_from_meta(meta, config)

    if family == "onnx":
        from captcha.solvers.detection.onnx_detector import OnnxDetector

        detector: Detector = OnnxDetector(
            weights_path,
            spec,
            name=f"{name}@{resolved_version}",
            device=device,
        )
    else:
        from captcha.solvers.detection._yolo import UltralyticsDetector

        detector = UltralyticsDetector.load(weights_path, spec=spec, name=f"{name}@{resolved_version}")

    with _DETECTOR_LOCK:
        _DETECTOR_CACHE[cache_key] = detector
    return detector


def clear_detector_cache() -> None:
    """Tests only - drop cached detector sessions."""
    with _DETECTOR_LOCK:
        _DETECTOR_CACHE.clear()


def _resolve_family(meta: Any, *, kind: str, allow_dev: bool) -> str:
    normalized = str(kind or "auto").strip().lower()
    if normalized in DEV_KINDS:
        if not allow_dev:
            raise ConfigError(
                "ultralytics detector is a dev-only path (AGPL-3.0); production artifacts must be ONNX RT-DETR",
                context={"kind": normalized, "hint": "pass detector='ultralytics' and allow_dev=True explicitly"},
            )
        return "ultralytics"
    if normalized not in _ONNX_KINDS:
        raise ConfigError(
            "unknown detector kind",
            context={"kind": normalized, "allowed": sorted(_ONNX_KINDS | DEV_KINDS)},
        )
    if normalized in {"onnx", "rtdetr", "rt-detr"}:
        return "onnx"

    framework = str(getattr(meta, "framework", "") or "").lower()
    if framework == "onnx":
        return "onnx"
    if framework == "raw" and str(getattr(meta, "s3_key", "")).lower().endswith(".onnx"):
        return "onnx"
    raise ConfigError(
        "production detectors must be ONNX RT-DETR artifacts",
        context={"framework": framework, "model": getattr(meta, "name", "")},
    )


def _load_source(registry: Any, name: str, version: str, meta: Any) -> tuple[Path, dict[str, Any]]:
    layout = getattr(meta, "layout", "single") or "single"
    try:
        if layout == "bundle":
            getter = getattr(registry, "get_artifact", None)
            if getter is None:
                raise ConfigError("models registry cannot read bundle artifacts", context={"name": name})
            artifact = getter(name, version)
            primary = getattr(artifact, "primary_path", None)
            if primary is None:
                raise ConfigError("detector bundle has no primary weights file", context={"name": name})
            config = getattr(artifact, "config", None)
            return Path(primary), dict(config) if isinstance(config, Mapping) else {}
        download = getattr(registry, "download", None)
        if download is None:
            raise ConfigError("models registry missing download", context={"name": name})
        return Path(download(name, version)), {}
    except CaptchaError:
        raise
    except Exception as exc:
        raise ConfigError(
            "detector weights unavailable",
            context={"name": name, "version": version, "error": str(exc)},
        ) from exc


__all__ = ["clear_detector_cache", "load_detector"]
