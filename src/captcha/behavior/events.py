"""Shape mouse points + timings into sensor-ready event dicts."""

from __future__ import annotations

from collections.abc import Sequence


def mouse_events_payload(
    path: Sequence[tuple[float, float]],
    timings: Sequence[int],
    *,
    event_type: str = "mousemove",
) -> list[dict]:
    """Zip `path` with `timings` into sensor event dicts.

    Output row: `{"x": float, "y": float, "t": int_ms, "type": str}`.

    Length mismatch is a hard error — misaligned points vs timestamps are
    the single most common cause of rejected Akamai / PX sensor payloads.
    """
    if len(path) != len(timings):
        raise ValueError(f"path len {len(path)} != timings len {len(timings)}")

    out: list[dict] = []
    for (x, y), t in zip(path, timings, strict=False):
        out.append({"x": float(x), "y": float(y), "t": int(t), "type": event_type})
    return out
