"""Model registry + lazy framework loaders for captcha solvers."""

from captcha_models.loader import load_hf, load_onnx, load_safetensors, load_torch
from captcha_models.meta import ModelMeta, decode_meta, encode_meta
from captcha_models.registry import Registry


def load_model(
    registry: Registry,
    name: str,
    version: str = "latest",
    *,
    device: str = "cpu",
):
    """Framework-dispatching convenience. Picks the loader off ModelMeta.framework."""
    meta = registry.get_meta(name, version)
    fw = meta.framework
    if fw == "torch":
        return registry.load_pytorch(name, meta.version, device=device)
    if fw == "onnx":
        return registry.load_onnx(name, meta.version)
    if fw == "hf":
        return registry.load_hf(name, meta.version, device=device)
    if fw == "safetensors":
        return registry.load_safetensors(name, meta.version, device=device)
    if fw == "raw":
        return registry.download(name, meta.version)
    raise ValueError(f"unknown framework: {fw}")


__all__ = [
    "ModelMeta",
    "Registry",
    "decode_meta",
    "encode_meta",
    "load_hf",
    "load_model",
    "load_onnx",
    "load_safetensors",
    "load_torch",
]
