"""ResNet50 sin/cos angle regressor with ONNX and torch state_dict loading.

The model contract is unchanged from docs/ml-tz.md: resnet50 with fc -> 2,
trained with a cosine loss, so the two outputs are (sin theta, cos theta).
Two artifact layouts are accepted:

    .onnx                     onnxruntime session, NCHW float32 input
    .pt / .pth / .bin         state_dict for resnet50 + Linear(2048, 2),
                              or a full nn.Module

Errors are deliberately split:
    ConfigError          no registry/meta, unsupported framework, missing file,
                         missing torch/onnxruntime
    ModelInferenceError  corrupt checkpoint, state_dict mismatch, wrong output
                         shape, NaN/Inf, degenerate (0, 0) vector

A (0, 0) output is not "angle 0": atan2 would silently answer up. It raises.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from captcha.domain.errors import CaptchaError, ConfigError, ModelInferenceError

from .regressor_spec import RegressorSpec

ANGLE_MODEL_NAME = "rotate-v1"
_ONNX_SUFFIXES = frozenset({".onnx"})
_TORCH_SUFFIXES = frozenset({".pt", ".pth", ".bin", ".ckpt"})


class AngleRegressor:
    """Lazy regressor: first predict() imports the runtime and loads weights."""

    def __init__(
        self,
        source: str | Path | None,
        *,
        device: str = "cpu",
        backend: str = "auto",
        spec: RegressorSpec | None = None,
        session: Any | None = None,
        model: Any | None = None,
    ) -> None:
        self.source = Path(source) if source is not None else None
        self.device = device
        self.backend = str(backend or "auto").strip().lower()
        self.spec = spec or RegressorSpec()
        self._session = session
        self._model = model
        self._torch: Any = None
        self._input_name: str | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_registry(
        cls,
        registry: Any,
        *,
        name: str = ANGLE_MODEL_NAME,
        version: str = "latest",
        device: str = "cpu",
    ) -> AngleRegressor:
        if registry is None:
            raise ConfigError(
                "rotate ML path requires ctx.models (captcha.adapters.weights.Registry)",
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
                "models registry returned unusable rotate meta",
                context={"name": name, "version": version, "error": str(exc)},
            ) from exc
        resolved_version = getattr(meta, "version", None) or version
        source, config = _load_source(registry, name, resolved_version, meta)
        return cls(
            source,
            device=device,
            backend=_backend_from_meta(meta),
            spec=RegressorSpec.from_meta(meta, config),
        )

    def predict(self, rgb: np.ndarray) -> tuple[float, float]:
        """Return (angle_deg 0..360, confidence 0..1)."""
        self._load()
        tensor = self._preprocess(rgb)
        raw = self._run_onnx(tensor) if self._session is not None else self._run_torch(tensor)
        return _angle_from_output(raw)

    # ---- loading ------------------------------------------------------------

    def _load(self) -> None:
        if self._session is not None or self._model is not None:
            return
        with self._lock:
            if self._session is not None or self._model is not None:
                return
            if self.source is None:
                raise ConfigError("rotate regressor has no weights source", context={})
            backend = self.backend if self.backend != "auto" else _backend_from_path(self.source)
            if backend == "onnx":
                self._load_onnx()
            elif backend == "torch":
                self._load_torch()
            else:
                raise ConfigError("unknown rotate regressor backend", context={"backend": backend})

    def _load_onnx(self) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ConfigError(
                "onnxruntime not installed; rotate ONNX artifact cannot be loaded",
                context={"install": "pip install captcha[onnx]"},
            ) from exc
        assert self.source is not None
        if not self.source.exists():
            raise ConfigError("rotate regressor weights file missing", context={"path": str(self.source)})
        providers = None
        if self.device.startswith("cuda") and hasattr(ort, "get_available_providers"):
            available = set(ort.get_available_providers())
            providers = [item for item in ("CUDAExecutionProvider", "CPUExecutionProvider") if item in available]
        try:
            self._session = ort.InferenceSession(str(self.source), providers=providers)
        except Exception as exc:
            raise ModelInferenceError(
                "rotate onnx session init failed",
                context={"path": str(self.source), "error": str(exc)},
            ) from exc
        self._input_name = _onnx_input_name(self._session)

    def _load_torch(self) -> None:
        try:
            import torch
            from torchvision.models import resnet50
        except ImportError as exc:
            raise ConfigError(
                "torch/torchvision not installed; rotate ML path unavailable",
                context={"install": "pip install captcha[cv]"},
            ) from exc
        assert self.source is not None
        if not self.source.exists():
            raise ConfigError("rotate regressor weights file missing", context={"path": str(self.source)})
        try:
            checkpoint = torch.load(str(self.source), map_location=self.device, weights_only=True)
        except Exception as exc:
            raise ModelInferenceError(
                "rotate regressor torch.load failed",
                context={"path": str(self.source), "error": str(exc)},
            ) from exc

        if isinstance(checkpoint, dict):
            try:
                model = resnet50(weights=None)
                model.fc = torch.nn.Linear(int(model.fc.in_features), 2)
            except Exception as exc:
                raise ModelInferenceError(
                    "cannot build resnet50 + fc(2) for rotate weights",
                    context={"error": str(exc)},
                ) from exc
            try:
                model.load_state_dict(checkpoint)
            except Exception as exc:
                raise ModelInferenceError(
                    "rotate state_dict does not match resnet50 + fc(2)",
                    context={"path": str(self.source), "error": str(exc)},
                ) from exc
        elif callable(checkpoint) and hasattr(checkpoint, "eval"):
            model = checkpoint
        else:
            raise ModelInferenceError(
                "rotate checkpoint is neither a state_dict nor an nn.Module",
                context={"path": str(self.source), "type": type(checkpoint).__name__},
            )
        try:
            model.eval()
            model.to(self.device)
        except Exception as exc:
            raise ModelInferenceError("rotate module init failed", context={"error": str(exc)}) from exc
        self._model = model
        self._torch = torch

    # ---- inference ----------------------------------------------------------

    def _preprocess(self, rgb: np.ndarray) -> np.ndarray:
        image_array = np.asarray(rgb)
        if image_array.ndim != 3 or image_array.shape[2] != 3:
            raise ModelInferenceError(
                "rotate regressor input must be RGB HxWx3",
                context={"shape": tuple(image_array.shape)},
            )
        try:
            image = Image.fromarray(image_array.astype(np.uint8, copy=False), mode="RGB")
            width, height = image.size
            scale = float(self.spec.resize_size) / float(min(width, height))
            resized = image.resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.Resampling.BILINEAR,
            )
            left = max(0, (resized.width - self.spec.input_size) // 2)
            top = max(0, (resized.height - self.spec.input_size) // 2)
            cropped = resized.crop((left, top, left + self.spec.input_size, top + self.spec.input_size))
        except Exception as exc:
            raise ModelInferenceError("rotate preprocessing failed", context={"error": str(exc)}) from exc
        array = np.asarray(cropped, dtype=np.float32) / 255.0
        mean = np.asarray(self.spec.mean, dtype=np.float32)
        std = np.asarray(self.spec.std, dtype=np.float32)
        array = (array - mean) / std
        return np.ascontiguousarray(array.transpose(2, 0, 1), dtype=np.float32)

    def _run_onnx(self, tensor: np.ndarray) -> Any:
        session = self._session
        input_name = self._input_name or _onnx_input_name(session)
        try:
            outputs = session.run(None, {input_name: tensor[None, ...]})
        except Exception as exc:
            raise ModelInferenceError(
                "rotate onnx inference failed",
                context={"path": str(self.source), "error": str(exc)},
            ) from exc
        return _first_output(outputs)

    def _run_torch(self, tensor: np.ndarray) -> Any:
        torch = self._torch
        if torch is None:
            raise ConfigError("rotate torch runtime missing after load", context={})
        try:
            batch = torch.from_numpy(tensor).unsqueeze(0).to(self.device)
            with torch.inference_mode():
                output = self._model(batch)
        except Exception as exc:
            raise ModelInferenceError(
                "rotate torch inference failed",
                context={"path": str(self.source), "error": str(exc)},
            ) from exc
        if isinstance(output, (list, tuple)):
            output = output[0]
        if hasattr(output, "detach"):
            output = output.detach()
        if hasattr(output, "cpu"):
            output = output.cpu()
        if hasattr(output, "numpy"):
            output = output.numpy()
        return output


# ---- module helpers ---------------------------------------------------------


def _backend_from_meta(meta: Any) -> str:
    framework = str(getattr(meta, "framework", "") or "").lower()
    if framework == "onnx":
        return "onnx"
    if framework == "torch":
        return "torch"
    if framework in ("", "raw"):
        return "auto"
    raise ConfigError(
        "rotate artifact framework must be onnx or torch",
        context={"framework": framework},
    )


def _backend_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _ONNX_SUFFIXES:
        return "onnx"
    if suffix in _TORCH_SUFFIXES:
        return "torch"
    raise ConfigError(
        "rotate weights suffix must be .onnx or a torch checkpoint (.pt/.pth/.bin/.ckpt)",
        context={"path": str(path), "suffix": suffix},
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
                raise ConfigError("rotate bundle has no primary weights file", context={"name": name})
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
            "rotate weights unavailable",
            context={"name": name, "version": version, "error": str(exc)},
        ) from exc


def _onnx_input_name(session: Any) -> str:
    try:
        inputs = session.get_inputs()
    except Exception as exc:
        raise ModelInferenceError("onnx session exposes no inputs", context={"error": str(exc)}) from exc
    if not inputs:
        raise ModelInferenceError("onnx session exposes no inputs", context={})
    return str(getattr(inputs[0], "name", "") or "input")


def _first_output(outputs: Any) -> Any:
    if isinstance(outputs, (list, tuple)):
        if not outputs:
            raise ModelInferenceError("rotate onnx session returned no outputs", context={})
        return outputs[0]
    return outputs


def _angle_from_output(raw: Any) -> tuple[float, float]:
    values = np.asarray(raw, dtype=np.float64)
    if values.ndim == 2 and values.shape[0] == 1:
        values = values[0]
    values = values.reshape(-1)
    if values.size != 2:
        raise ModelInferenceError(
            "rotate regressor must output exactly 2 values (sin, cos)",
            context={"shape": tuple(np.asarray(raw).shape)},
        )
    if not np.isfinite(values).all():
        raise ModelInferenceError("rotate regressor output contains NaN/Inf", context={"values": values.tolist()})
    sin_value, cos_value = float(values[0]), float(values[1])
    norm = math.hypot(sin_value, cos_value)
    if norm < 1e-6:
        raise ModelInferenceError(
            "rotate regressor returned a degenerate (0, 0) vector",
            context={"values": [sin_value, cos_value]},
        )
    angle = (math.degrees(math.atan2(sin_value, cos_value)) + 360.0) % 360.0
    return (angle, float(min(1.0, norm)))


__all__ = ["ANGLE_MODEL_NAME", "AngleRegressor", "RegressorSpec"]
