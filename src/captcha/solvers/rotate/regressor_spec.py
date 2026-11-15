"""Preprocessing contract for rotate artifacts, parsed from model meta."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from captcha.domain.errors import ConfigError

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


@dataclass(frozen=True, slots=True)
class RegressorSpec:
    """Preprocessing contract published next to the weights."""

    input_size: int = 224
    resize_size: int = 256
    mean: tuple[float, float, float] = IMAGENET_MEAN
    std: tuple[float, float, float] = IMAGENET_STD

    @classmethod
    def from_meta(cls, meta: Any, config: Mapping[str, Any] | None = None) -> RegressorSpec:
        schema: dict[str, Any] = dict(config) if isinstance(config, Mapping) else {}
        input_schema = getattr(meta, "input_schema", None)
        if isinstance(input_schema, Mapping):
            schema.update(input_schema)
        input_size = _positive_int(schema.get("input_size", 224), "input_size")
        resize_size = _positive_int(schema.get("resize_size", max(256, input_size)), "resize_size")
        if resize_size < input_size:
            raise ConfigError(
                "rotate resize_size must be >= input_size",
                context={"input_size": input_size, "resize_size": resize_size},
            )
        mean = _triple(schema.get("mean", IMAGENET_MEAN), "mean")
        std = _triple(schema.get("std", IMAGENET_STD), "std")
        if any(value <= 0 for value in std):
            raise ConfigError("rotate std values must be positive", context={"std": list(std)})
        return cls(input_size=input_size, resize_size=resize_size, mean=mean, std=std)


def _positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"rotate {field_name} must be an integer", context={field_name: value}) from exc
    if parsed <= 0:
        raise ConfigError(f"rotate {field_name} must be positive", context={field_name: parsed})
    return parsed


def _triple(value: Any, field_name: str) -> tuple[float, float, float]:
    try:
        items = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"rotate {field_name} must be three numbers", context={field_name: value}) from exc
    if len(items) != 3:
        raise ConfigError(f"rotate {field_name} must have three values", context={field_name: list(items)})
    return items  # type: ignore[return-value]


__all__ = ["IMAGENET_MEAN", "IMAGENET_STD", "RegressorSpec"]
