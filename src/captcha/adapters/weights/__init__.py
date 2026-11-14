"""Model weights registry backed by S3/MinIO with a disk cache."""

from __future__ import annotations

from .loaders import load_hf, load_onnx, load_safetensors, load_torch
from .meta import ModelMeta, decode_meta, encode_meta
from .s3_registry import Registry


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
