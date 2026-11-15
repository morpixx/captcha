"""Prompt-conditioned grid-head contract shared by every classification solver.

This is the weights-free half of the grid stack: which grid shapes a head
accepts, how a per-tile probability vector becomes tile indices, and what the
runtime spec must satisfy. prompt.py owns prompt-to-label matching, head_meta.py
parses meta.json/config.json; head.py adds ViT + safetensors plumbing and
base_grid.py the payload/IO path.

Heads (one per family, see HEADS):

    hcaptcha-v1   3x3        classification_grid
    recaptcha-v1  3x3, 4x4   classification_grid
    funcap-v1     2x3        classification_grid
    awswaf-v1     3x3        classification_grid
    shein-v1      3x3        classification_grid

These heads answer with tile indices. Token types (hcaptcha,
hcaptcha_enterprise, funcaptcha, ...) are served by their browser/HTTP wire
solvers; a grid solver that claims them would project a token into an index
answer, so GridClassifier.__init_subclass__ rejects hCaptcha there.

Nothing here imports torch/PIL: the contract is unit-testable without the cv
extra and cannot download a model by accident.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError

from .prompt import resolve_label

DEFAULT_THRESHOLD = 0.5

#: Shapes the service can slice and the five trained heads can score. 5x5 grids
#: are not in the contract; a task asking for one is unsolvable, not a retrain.
GRID_2X3 = (2, 3)
GRID_3X3 = (3, 3)
GRID_4X4 = (4, 4)
SUPPORTED_GRIDS: frozenset[tuple[int, int]] = frozenset({GRID_2X3, GRID_3X3, GRID_4X4})


@dataclass(frozen=True, slots=True)
class GridShape:
    """A validated row/column grid. Construction is for static configs."""

    rows: int
    cols: int

    def __post_init__(self) -> None:
        if self.rows < 1 or self.cols < 1:
            raise ConfigError("grid rows/cols must be >= 1", context={"rows": self.rows, "cols": self.cols})
        if (self.rows, self.cols) not in SUPPORTED_GRIDS:
            raise ConfigError(
                "unsupported grid shape",
                context={
                    "rows": self.rows,
                    "cols": self.cols,
                    "supported": sorted(SUPPORTED_GRIDS),
                },
            )

    @property
    def tile_count(self) -> int:
        return self.rows * self.cols

    def to_dict(self) -> dict[str, int]:
        return {"rows": self.rows, "cols": self.cols}

    def __str__(self) -> str:
        return f"{self.rows}x{self.cols}"


SHAPE_2X3 = GridShape(*GRID_2X3)
SHAPE_3X3 = GridShape(*GRID_3X3)
SHAPE_4X4 = GridShape(*GRID_4X4)


def parse_grid_shape(rows: Any, cols: Any) -> GridShape:
    """Coerce a task payload's rows/cols. Bad client input is ERR_UNSOLVABLE."""
    if isinstance(rows, bool) or isinstance(cols, bool):
        raise UnsolvableError("grid rows/cols must be integers", context={"rows": rows, "cols": cols})
    try:
        r, c = int(rows), int(cols)
    except (TypeError, ValueError) as exc:
        raise UnsolvableError(
            "grid rows/cols must be integers",
            context={"rows": rows, "cols": cols},
        ) from exc
    if (r, c) not in SUPPORTED_GRIDS:
        raise UnsolvableError(
            "grid shape is not supported",
            context={"rows": r, "cols": c, "supported": sorted(SUPPORTED_GRIDS)},
        )
    return GridShape(r, c)


@dataclass(frozen=True, slots=True)
class GridHeadContract:
    """Static part of a head: its name and the grids it may be asked to score."""

    name: str
    shapes: tuple[GridShape, ...]
    default_shape: GridShape
    default_threshold: float = DEFAULT_THRESHOLD

    def __post_init__(self) -> None:
        if not self.shapes:
            raise ConfigError(f"head '{self.name}' declares no grid shapes")
        if self.default_shape not in self.shapes:
            raise ConfigError(
                f"head '{self.name}' default shape is not in its allowed shapes",
                context={"default": self.default_shape.to_dict(), "allowed": [str(s) for s in self.shapes]},
            )
        if not 0.0 <= self.default_threshold <= 1.0:
            raise ConfigError(
                f"head '{self.name}' default threshold must be in [0, 1]",
                context={"threshold": self.default_threshold},
            )

    def resolve(self, rows: Any, cols: Any) -> GridShape:
        """Validate a requested shape against this head; unsupported = unsolvable."""
        shape = parse_grid_shape(rows, cols)
        if shape not in self.shapes:
            raise UnsolvableError(
                f"head '{self.name}' does not serve {shape} grids",
                context={"head": self.name, "requested": shape.to_dict(), "allowed": [str(s) for s in self.shapes]},
            )
        return shape


HEADS: dict[str, GridHeadContract] = {
    "hcaptcha-v1": GridHeadContract("hcaptcha-v1", (SHAPE_3X3,), SHAPE_3X3),
    "recaptcha-v1": GridHeadContract("recaptcha-v1", (SHAPE_3X3, SHAPE_4X4), SHAPE_3X3),
    "funcap-v1": GridHeadContract("funcap-v1", (SHAPE_2X3,), SHAPE_2X3),
    "awswaf-v1": GridHeadContract("awswaf-v1", (SHAPE_3X3,), SHAPE_3X3),
    "shein-v1": GridHeadContract("shein-v1", (SHAPE_3X3,), SHAPE_3X3),
}


def head_contract(name: str) -> GridHeadContract:
    try:
        return HEADS[name]
    except KeyError as exc:
        raise ConfigError(
            f"unknown classification head '{name}'",
            context={"head": name, "known": sorted(HEADS)},
        ) from exc


# ---- thresholds and selection ---------------------------------------------


def coerce_threshold(value: Any, *, source: str) -> float:
    """Validate a threshold from head meta or a task payload."""
    error = UnsolvableError if source == "payload" else ConfigError
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise error(
            f"{source} threshold must be a number",
            context={"threshold": value, "source": source},
        ) from exc
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise error(
            f"{source} threshold must be in [0, 1]",
            context={"threshold": value, "source": source},
        )
    return threshold


def resolve_grid(payload: Mapping[str, Any], contract: GridHeadContract, *, fallback: GridShape) -> GridShape:
    """Pick the grid from the payload, or the head's published default.

    rows and cols travel together: a payload with only one of them is a
    broken task, not a request for the default on the other axis.
    """
    has_rows = payload.get("rows") is not None
    has_cols = payload.get("cols") is not None
    if not has_rows and not has_cols:
        if fallback not in contract.shapes:
            raise ConfigError(
                f"head '{contract.name}' default {fallback} is not in its allowed shapes",
                context={"head": contract.name, "allowed": [str(shape) for shape in contract.shapes]},
            )
        return fallback
    if has_rows != has_cols:
        raise UnsolvableError(
            "grid rows/cols must be sent together",
            context={"rows": payload.get("rows"), "cols": payload.get("cols")},
        )
    return contract.resolve(payload["rows"], payload["cols"])


# ---- runtime spec ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GridHeadSpec:
    """A head's runtime contract: contract + labels + threshold + grid."""

    contract: GridHeadContract
    labels: tuple[str, ...]
    threshold: float = DEFAULT_THRESHOLD
    aliases: Mapping[str, str] = field(default_factory=dict)
    shape: GridShape = SHAPE_3X3

    def __post_init__(self) -> None:
        if not self.labels:
            raise ConfigError(f"head '{self.contract.name}' has no labels")
        if len(set(self.labels)) != len(self.labels):
            raise ConfigError(
                f"head '{self.contract.name}' has duplicate labels",
                context={"labels": list(self.labels)},
            )
        unknown = sorted(set(self.aliases.values()) - set(self.labels))
        if unknown:
            raise ConfigError(
                f"head '{self.contract.name}' has aliases for unknown labels",
                context={"labels": unknown, "head": self.contract.name},
            )
        if not 0.0 <= self.threshold <= 1.0:
            raise ConfigError(
                f"head '{self.contract.name}' threshold must be in [0, 1]",
                context={"threshold": self.threshold},
            )
        if self.shape not in self.contract.shapes:
            raise ConfigError(
                f"head '{self.contract.name}' was published for {self.shape}, contract allows "
                + ", ".join(str(shape) for shape in self.contract.shapes),
                context={"head": self.contract.name, "shape": self.shape.to_dict()},
            )

    @property
    def name(self) -> str:
        return self.contract.name

    @property
    def label_to_idx(self) -> dict[str, int]:
        return {label: index for index, label in enumerate(self.labels)}

    def with_shape(self, shape: GridShape) -> GridHeadSpec:
        if shape not in self.contract.shapes:
            raise UnsolvableError(
                f"head '{self.name}' does not serve {shape} grids",
                context={"head": self.name, "requested": shape.to_dict()},
            )
        return replace(self, shape=shape)

    def resolve(self, prompt: str) -> int:
        return resolve_label(prompt, self.labels, self.aliases, head=self.name)

    def select(self, probs: Sequence[float], *, threshold: float | None = None) -> list[int]:
        """Tiles at/above the threshold, row-major.

        Empty is a valid answer ("none of these tiles"). There is deliberately
        no argmax fallback: a missing detection must not become a guessed click.
        """
        cutoff = self.threshold if threshold is None else threshold
        if len(probs) != self.shape.tile_count:
            raise ModelInferenceError(
                "tile count does not match the grid",
                context={
                    "head": self.name,
                    "shape": self.shape.to_dict(),
                    "expected": self.shape.tile_count,
                    "got": len(probs),
                },
            )
        return [index for index, prob in enumerate(probs) if float(prob) >= cutoff]


__all__ = [
    "DEFAULT_THRESHOLD",
    "GRID_2X3",
    "GRID_3X3",
    "GRID_4X4",
    "HEADS",
    "SHAPE_2X3",
    "SHAPE_3X3",
    "SHAPE_4X4",
    "SUPPORTED_GRIDS",
    "GridHeadContract",
    "GridHeadSpec",
    "GridShape",
    "coerce_threshold",
    "head_contract",
    "parse_grid_shape",
    "resolve_grid",
]
