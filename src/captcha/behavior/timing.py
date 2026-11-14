"""Monotonic humanized timestamps for pointer event streams."""

from __future__ import annotations

import numpy as np


def humanize_timing(
    n_events: int,
    *,
    mean_ms: float = 16.0,
    std_ms: float = 4.0,
    start_ms: int = 0,
    rng: np.random.Generator | None = None,
) -> list[int]:
    """Cumulative millisecond timestamps for `n_events` events.

    Per-step dt is drawn from a truncated normal (min 1ms, max mean*4) so
    the sequence never regresses. Returned timestamps are non-decreasing
    integers starting at `start_ms`.
    """
    if n_events <= 0:
        return []

    r = rng if rng is not None else np.random.default_rng()

    lo = 1.0
    hi = max(mean_ms * 4.0, mean_ms + std_ms * 3.0)
    # Resample tail so the distribution stays shaped, not clipped-flat.
    dts = r.normal(mean_ms, std_ms, size=n_events)
    bad = (dts < lo) | (dts > hi)
    while bad.any():
        dts[bad] = r.normal(mean_ms, std_ms, size=int(bad.sum()))
        bad = (dts < lo) | (dts > hi)

    # First event lands at start_ms; subsequent at cumulative sum.
    ts = np.empty(n_events, dtype=np.int64)
    ts[0] = int(start_ms)
    if n_events > 1:
        acc = np.cumsum(dts[1:]).astype(np.int64) + int(start_ms)
        # Enforce strictly non-decreasing after int-cast rounding.
        for i in range(1, n_events):
            cand = int(acc[i - 1])
            if cand <= ts[i - 1]:
                cand = ts[i - 1] + 1
            ts[i] = cand
    return ts.tolist()
