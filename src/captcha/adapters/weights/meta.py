"""Model metadata struct. Persisted as meta.json next to the weights blob."""

from __future__ import annotations

from typing import Any, Literal

import msgspec

Framework = Literal["torch", "onnx", "hf", "safetensors", "raw"]


class ModelMeta(msgspec.Struct, frozen=True, kw_only=True):
    """What the registry knows about a single (name, version) tuple."""

    name: str
    version: str
    framework: Framework
    s3_key: str                  # object key of the weights blob inside the bucket
    sha256: str                  # hex digest of the weights blob
    size_bytes: int = 0
    input_schema: dict[str, Any] = msgspec.field(default_factory=dict)
    output_schema: dict[str, Any] = msgspec.field(default_factory=dict)
    canary_rollout: float = 0.0  # 0.0 = disabled, 1.0 = full
    labels: dict[str, str] = msgspec.field(default_factory=dict)
    created_at: str = ""         # ISO-8601, informational


def decode_meta(blob: bytes) -> ModelMeta:
    return msgspec.json.decode(blob, type=ModelMeta)


def encode_meta(meta: ModelMeta) -> bytes:
    return msgspec.json.encode(meta)
