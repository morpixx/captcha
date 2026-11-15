"""Answer comparison for the golden harness.

Only three shapes matter for the visual types: an index (exact), a coordinate or
bbox (per-axis tolerance in pixels), and a label/token (exact). Numbers inside a
list are compared with tolerance; a scalar index is compared exactly, because
"close enough" on a target index is a wrong click.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_TOLERANCE_PX = 8.0


def mismatched_fields(
    expected: Mapping[str, Any],
    predicted: Any,
    *,
    tolerance_px: float = DEFAULT_TOLERANCE_PX,
) -> tuple[str, ...]:
    """Which expected keys the prediction got wrong (empty tuple = match)."""
    if not isinstance(predicted, Mapping):
        return tuple(expected)
    bad: list[str] = []
    for key, want in expected.items():
        if key not in predicted:
            bad.append(key)
        elif is_number_list(want):
            if not within_tolerance(predicted[key], want, tolerance_px):
                bad.append(key)
        elif predicted[key] != want:
            bad.append(key)
    return tuple(bad)


def within_tolerance(predicted: Any, expected: Any, tolerance_px: float) -> bool:
    """Per-axis comparison for [x, y] and [x1, y1, x2, y2] style answers."""
    want = floats(expected)
    got = floats(predicted)
    if want is None or got is None or len(want) != len(got):
        return False
    return all(abs(a - b) <= tolerance_px for a, b in zip(want, got, strict=True))


def is_number_list(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) >= 2 and floats(value) is not None


def floats(value: Any) -> list[float] | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        out: list[float] = []
        for item in value:
            sub = floats(item)
            if sub is None:
                return None
            out.extend(sub)
        return out
    return None
