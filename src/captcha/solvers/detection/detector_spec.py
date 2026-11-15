"""Detector inference contract parsed from meta.json / bundle config.json.

Kept separate from the runtime interface so a model publisher can read the
contract without importing the ONNX path, and so detector.py stays under the
400-line module cap.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from captcha.domain.errors import ConfigError

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
OUTPUT_LAYOUTS = ("xyxy_score_class", "cxcywh_logits")


@dataclass(frozen=True, slots=True)
class DetectorSpec:
    """Inference contract published in meta.json / bundle config.json."""

    class_names: tuple[str, ...] = ()
    input_size: int = 640
    output_layout: str = "xyxy_score_class"
    score_threshold: float = 0.25
    normalize: bool = True
    mean: tuple[float, float, float] = IMAGENET_MEAN
    std: tuple[float, float, float] = IMAGENET_STD
    boxes_normalized: bool = False


def spec_from_meta(meta: Any, config: Mapping[str, Any] | None = None) -> DetectorSpec:
    """Read detector config from bundle config.json + ModelMeta.input_schema."""
    schema: dict[str, Any] = dict(config) if isinstance(config, Mapping) else {}
    input_schema = getattr(meta, "input_schema", None)
    if isinstance(input_schema, Mapping):
        schema.update(input_schema)

    raw_names = schema.get("class_names", schema.get("classes", schema.get("labels")))
    if raw_names is None:
        raw_names = _indexed_labels(getattr(meta, "labels", None))
    class_names = _coerce_class_names(raw_names)

    layout = str(schema.get("output_layout", schema.get("layout", "xyxy_score_class"))).strip().lower()
    if layout not in OUTPUT_LAYOUTS:
        raise ConfigError(
            f"detector output_layout must be one of {', '.join(OUTPUT_LAYOUTS)}",
            context={"layout": layout},
        )
    input_size = _positive_int(schema.get("input_size", schema.get("imgsz", 640)), "input_size")
    threshold = _ratio(schema.get("score_threshold", schema.get("conf", 0.25)), "score_threshold")
    mean = _triple(schema.get("mean", IMAGENET_MEAN), "mean")
    std = _triple(schema.get("std", IMAGENET_STD), "std")
    if any(value <= 0 for value in std):
        raise ConfigError("detector std values must be positive", context={"std": list(std)})
    return DetectorSpec(
        class_names=class_names,
        input_size=input_size,
        output_layout=layout,
        score_threshold=threshold,
        normalize=bool(schema.get("normalize", True)),
        mean=mean,
        std=std,
        boxes_normalized=bool(schema.get("boxes_normalized", False)),
    )


def _coerce_class_names(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, Mapping):
        indexed = _indexed_labels(raw)
        if indexed is None:
            raise ConfigError("detector class map must be name -> index", context={"labels": list(raw)[:16]})
        return indexed
    if isinstance(raw, (list, tuple)):
        names = tuple(str(item).strip() for item in raw)
        if not names or not all(names):
            raise ConfigError("detector class names must be non-empty strings", context={"labels": list(names[:32])})
        if len(set(names)) != len(names):
            raise ConfigError("detector class names contain duplicates", context={"labels": list(names[:32])})
        return names
    raise ConfigError(
        "detector class names must be a list or a name -> index map", context={"type": type(raw).__name__}
    )


def _indexed_labels(raw: Any) -> tuple[str, ...] | None:
    if not isinstance(raw, Mapping) or not raw:
        return None
    pairs: list[tuple[int, str]] = []
    for name, position in raw.items():
        try:
            index = int(position)
        except (TypeError, ValueError):
            return None
        pairs.append((index, str(name)))
    pairs.sort()
    if [index for index, _ in pairs] != list(range(len(pairs))):
        return None
    return tuple(name for _, name in pairs)


def _positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"detector {field_name} must be an integer", context={field_name: value}) from exc
    if parsed <= 0:
        raise ConfigError(f"detector {field_name} must be positive", context={field_name: parsed})
    return parsed


def _ratio(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"detector {field_name} must be a number", context={field_name: value}) from exc
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ConfigError(f"detector {field_name} must be in [0, 1]", context={field_name: parsed})
    return parsed


def _triple(value: Any, field_name: str) -> tuple[float, float, float]:
    try:
        items = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"detector {field_name} must be three numbers", context={field_name: value}) from exc
    if len(items) != 3:
        raise ConfigError(f"detector {field_name} must have three values", context={field_name: list(items)})
    return items  # type: ignore[return-value]


__all__ = ["IMAGENET_MEAN", "IMAGENET_STD", "OUTPUT_LAYOUTS", "DetectorSpec", "spec_from_meta"]
