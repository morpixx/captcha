"""Cubic bezier mouse paths with perpendicular jitter.

A real human cursor curves to one side of the straight A→B line and
undershoots slightly near the end. We approximate that with a cubic
bezier whose two control points sit on the A→B axis then get pushed
sideways (same sign both — not S-curve) and jittered along the axis.
"""

from __future__ import annotations

import math

import numpy as np


def bezier_path(
    start: tuple[float, float],
    end: tuple[float, float],
    n_points: int = 40,
    *,
    jitter: float = 0.5,
    curvature: float = 0.25,
    rng: np.random.Generator | None = None,
) -> list[tuple[float, float]]:
    """Return `n_points` points along a jittered cubic bezier from start to end.

    - `jitter`: 0 = flawless curve, 1 = heavy wobble (pixels scale with length).
    - `curvature`: perpendicular offset of control points, in units of A→B length.
      Signed randomly (left or right of the line) per-call.
    - `rng`: pass a seeded `np.random.default_rng(seed)` for determinism.
    """
    if n_points < 2:
        raise ValueError("n_points must be >= 2")

    r = rng if rng is not None else np.random.default_rng()

    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    dx, dy = ex - sx, ey - sy
    length = math.hypot(dx, dy) or 1.0

    # Unit axis and unit perpendicular.
    ax, ay = dx / length, dy / length
    px, py = -ay, ax

    # Pick a side; same side for both control points (not S-shape).
    side = 1.0 if r.random() < 0.5 else -1.0
    off1 = side * curvature * length * (0.6 + 0.8 * r.random())
    off2 = side * curvature * length * (0.6 + 0.8 * r.random())

    # Control points at ~1/3 and ~2/3 along axis, shifted perpendicular.
    c1x = sx + ax * (length * 0.33) + px * off1
    c1y = sy + ay * (length * 0.33) + py * off1
    c2x = sx + ax * (length * 0.66) + px * off2
    c2y = sy + ay * (length * 0.66) + py * off2

    t = np.linspace(0.0, 1.0, n_points)
    # Non-uniform time → faster middle, slower edges (ease-in-out), matches
    # how humans decelerate approaching the target.
    t = 0.5 * (1.0 - np.cos(math.pi * t))

    omt = 1.0 - t
    xs = (omt**3) * sx + 3 * (omt**2) * t * c1x + 3 * omt * (t**2) * c2x + (t**3) * ex
    ys = (omt**3) * sy + 3 * (omt**2) * t * c1y + 3 * omt * (t**2) * c2y + (t**3) * ey

    # Jitter in screen pixels, scaled a touch by length so long drags wobble more.
    j = jitter * max(0.5, math.log1p(length) * 0.35)
    xs = xs + r.normal(0.0, j, size=n_points)
    ys = ys + r.normal(0.0, j, size=n_points)

    # Pin endpoints so caller lands exactly on the target.
    xs[0], ys[0] = sx, sy
    xs[-1], ys[-1] = ex, ey

    return list(zip(xs.tolist(), ys.tolist()))
