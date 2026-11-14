"""Thin loaders. Framework imports are lazy so the base package stays small."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from captcha.domain.errors import ConfigError, ModelInferenceError


def load_torch(path: str | Path, device: str = "cpu", *, eval_: bool = True) -> Any:
    """Load a torch checkpoint. Accepts .pt / .pth / scripted module / state_dict."""
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError as e:
        raise ConfigError("torch not installed — pip install 'captcha-models[torch]'") from e

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"model file missing: {p}", context={"path": str(p)})

    try:
        obj = torch.load(str(p), map_location=device, weights_only=False)
    except Exception as e:
        raise ModelInferenceError(f"torch.load failed: {e}", context={"path": str(p)}) from e

    # Scripted / traced module or already-constructed nn.Module → move + eval.
    if hasattr(obj, "to") and hasattr(obj, "eval"):
        obj = obj.to(device)
        if eval_:
            obj.eval()
        return obj
    # Raw state_dict — caller instantiates the architecture then load_state_dict.
    return obj


def load_onnx(path: str | Path, *, providers: list[str] | None = None) -> Any:
    """Load an ONNX model. Providers default: CUDA if present, else CPU."""
    try:
        import onnxruntime as ort  # type: ignore[import-not-found]
    except ImportError as e:
        raise ConfigError("onnxruntime not installed — pip install 'captcha-models[onnx]'") from e

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"model file missing: {p}", context={"path": str(p)})

    if providers is None:
        available = set(ort.get_available_providers())
        providers = [x for x in ("CUDAExecutionProvider", "CPUExecutionProvider") if x in available]

    try:
        return ort.InferenceSession(str(p), providers=providers)
    except Exception as e:
        raise ModelInferenceError(f"onnx session init failed: {e}", context={"path": str(p)}) from e


def load_hf(path: str | Path, *, device: str = "cpu", task: str | None = None) -> Any:
    """Load a HF model directory (config.json + safetensors/pt)."""
    try:
        from transformers import AutoModel, AutoTokenizer, pipeline  # type: ignore[import-not-found]
    except ImportError as e:
        raise ConfigError("transformers not installed — pip install 'captcha-models[hf]'") from e

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"model dir missing: {p}", context={"path": str(p)})

    try:
        if task:
            return pipeline(task, model=str(p), device=device)
        model = AutoModel.from_pretrained(str(p))
        tokenizer = AutoTokenizer.from_pretrained(str(p))
        if hasattr(model, "to"):
            model = model.to(device)
        return model, tokenizer
    except Exception as e:
        raise ModelInferenceError(f"hf load failed: {e}", context={"path": str(p)}) from e


def load_safetensors(path: str | Path, *, device: str = "cpu") -> dict[str, Any]:
    """Load a bare safetensors file into a {name: tensor} dict."""
    try:
        from safetensors.torch import load_file  # type: ignore[import-not-found]
    except ImportError as e:
        raise ConfigError("safetensors not installed — pip install 'captcha-models[hf]'") from e

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"model file missing: {p}", context={"path": str(p)})
    try:
        return load_file(str(p), device=device)
    except Exception as e:
        raise ModelInferenceError(f"safetensors load failed: {e}", context={"path": str(p)}) from e
