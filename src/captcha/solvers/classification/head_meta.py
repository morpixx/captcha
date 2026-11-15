"""Parse a grid head's meta.json input_schema and bundle config.json.

ModelMeta drops unknown top-level meta.json keys, so the fields a head needs
(labels, threshold, grid, prompt aliases) live in ModelMeta.input_schema; a
multi-file bundle may carry the same fields in config.json. Labels can also
come from ModelMeta.labels, a name -> row-index map used by publish-model.

Everything here is pure: no torch, no registry, no download. A bad document is
ERR_CONFIG, because a worker with a mislabelled head must not guess clicks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from captcha.domain.errors import ConfigError

from .grid import GridHeadContract, GridHeadSpec, GridShape, coerce_threshold
from .prompt import normalize_text


def _coerce_labels(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, Mapping):
        indexed: list[tuple[int, str]] = []
        for name, position in raw.items():
            try:
                index = int(position)
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    "head label positions must be integers",
                    context={"label": str(name), "position": position},
                ) from exc
            indexed.append((index, str(name)))
        indexed.sort()
        if [index for index, _ in indexed] != list(range(len(indexed))):
            raise ConfigError(
                "head labels must be indexed 0..N-1 without gaps",
                context={"positions": [index for index, _ in indexed]},
            )
        labels = [normalize_text(name) for _, name in indexed]
    elif isinstance(raw, (list, tuple)):
        labels = [normalize_text(str(name)) for name in raw]
    else:
        raise ConfigError(
            "head meta is missing a 'labels' list or map",
            context={"type": type(raw).__name__},
        )

    if not labels or not all(labels):
        raise ConfigError("head labels must be non-empty strings", context={"labels": labels[:32]})
    if len(set(labels)) != len(labels):
        raise ConfigError("head labels contain duplicates", context={"labels": labels[:32]})
    return tuple(labels)


def _coerce_aliases(raw: Any, labels: Sequence[str]) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError("head prompt aliases must be a mapping", context={"type": type(raw).__name__})
    aliases: dict[str, str] = {}
    known = set(labels)
    for alias, target in raw.items():
        alias_norm = normalize_text(str(alias))
        target_norm = normalize_text(str(target))
        if not alias_norm or not target_norm:
            raise ConfigError("head prompt alias is empty", context={"alias": alias, "target": target})
        if target_norm not in known:
            raise ConfigError(
                "head prompt alias targets an unknown label",
                context={"alias": alias_norm, "target": target_norm, "known_labels": list(labels[:32])},
            )
        aliases[alias_norm] = target_norm
    return aliases


def _shape_from_schema(schema: Mapping[str, Any], contract: GridHeadContract) -> GridShape:
    raw = schema.get("grid")
    if raw is None and "rows" in schema and "cols" in schema:
        raw = {"rows": schema["rows"], "cols": schema["cols"]}
    if raw is None:
        return contract.default_shape

    rows: Any
    cols: Any
    if isinstance(raw, str):
        match = re.fullmatch(r"\s*(\d+)\s*[x\u00d7]\s*(\d+)\s*", raw)
        if match is None:
            raise ConfigError("head grid string must look like '3x3'", context={"grid": raw})
        rows, cols = match.group(1), match.group(2)
    elif isinstance(raw, Mapping):
        rows, cols = raw.get("rows"), raw.get("cols")
    else:
        raise ConfigError("head grid must be a mapping or 'RxC' string", context={"grid": raw})

    try:
        shape = GridShape(int(rows), int(cols))
    except (TypeError, ValueError) as exc:
        raise ConfigError("head grid rows/cols must be integers", context={"grid": raw}) from exc
    if shape not in contract.shapes:
        raise ConfigError(
            f"head '{contract.name}' meta declares an unsupported grid",
            context={"grid": shape.to_dict(), "allowed": [str(item) for item in contract.shapes]},
        )
    return shape


def head_spec_from_meta(
    contract: GridHeadContract,
    meta: Any,
    *,
    config: Mapping[str, Any] | None = None,
) -> GridHeadSpec:
    """Build the runtime spec from ModelMeta.input_schema, meta.labels and bundle config.

    bundle config.json provides defaults; meta.input_schema overrides it.
    Labels fall back to ModelMeta.labels (name -> row index) only when neither
    carries a label list. A multi-file bundle can carry the same fields in
    config.json because ModelMeta has no top-level threshold/grid fields.
    """
    schema: dict[str, Any] = {}
    if isinstance(config, Mapping):
        schema.update(config)
    input_schema = getattr(meta, "input_schema", None)
    if isinstance(input_schema, Mapping):
        schema.update(input_schema)

    raw_labels = schema.get("labels")
    if raw_labels is None:
        raw_labels = getattr(meta, "labels", None)
    labels = _coerce_labels(raw_labels)
    aliases = _coerce_aliases(schema.get("prompt_aliases", schema.get("aliases")), labels)
    threshold = coerce_threshold(schema.get("threshold", contract.default_threshold), source="meta")
    shape = _shape_from_schema(schema, contract)
    return GridHeadSpec(contract=contract, labels=labels, threshold=threshold, aliases=aliases, shape=shape)


__all__ = ["head_spec_from_meta"]
