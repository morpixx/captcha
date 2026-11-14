"""Humanized drag trajectory for slider submits.

Combines `captcha.behavior.bezier` (curved path with perpendicular jitter) with
`captcha.behavior.timing` (non-decreasing cumulative ms timestamps). Returns a
list of `(x, y, t_ms)` tuples ready to be fed into a vendor submit.
"""

from __future__ import annotations

import numpy as np

from captcha.behavior import bezier_path, humanize_timing


def build_trajectory(
    offset: int,
    *,
    start: tuple[float, float] = (0.0, 0.0),
    y_drift: float = 2.5,
    n_points: int = 42,
    mean_dt_ms: float = 16.0,
    seed: int | None = None,
) -> list[tuple[float, float, int]]:
    """Return a humanized path of length `n_points` from `start` to `start + (offset, y_drift)`.

    - `offset`: horizontal distance to drag, in pixels.
    - `y_drift`: tiny vertical wobble a real finger adds. Random per-call sign.
    - `n_points`: number of samples along the curve.
    - `mean_dt_ms`: mean inter-event delay.
    - `seed`: optional for test determinism.
    """
    if offset <= 0:
        raise ValueError("offset must be > 0")

    rng = np.random.default_rng(seed)
    y_sign = 1.0 if rng.random() < 0.5 else -1.0
    end = (start[0] + float(offset), start[1] + y_sign * y_drift)
    pts = bezier_path(start, end, n_points=n_points, jitter=0.6, curvature=0.12, rng=rng)
    ts = humanize_timing(n_points, mean_ms=mean_dt_ms, std_ms=mean_dt_ms * 0.35, rng=rng)
    return [(float(x), float(y), int(t)) for (x, y), t in zip(pts, ts, strict=False)]
