# captcha-behavior

Human-shaped mouse paths and timing for:

- Browser-fallback solves (Playwright driver sends these points)
- HTTP sensor payload emulation (Akamai `bmak.sensor_data`, PerimeterX
  `_pxhd`, DataDome `cid`, Kasada `x-kpsdk-ct` — all need plausible
  `mousemove` event streams)

## API

```python
from captcha_behavior import bezier_path, humanize_timing, mouse_events_payload

pts = bezier_path((40, 320), (780, 410), n_points=42, jitter=0.6)
ts  = humanize_timing(len(pts), mean_ms=14, std_ms=4)
events = mouse_events_payload(pts, ts, event_type="mousemove")
# -> [{"x": 40.0, "y": 320.0, "t": 0, "type": "mousemove"}, ...]
```

Deterministic given a seeded `numpy.random.Generator`:

```python
import numpy as np
rng = np.random.default_rng(1337)
pts = bezier_path(a, b, n_points=50, rng=rng)
```

## Notes

- Bezier is cubic with two mid control points jittered perpendicular to
  the A→B axis. Keeps the curve biased in one direction (how humans
  actually move, not S-shaped).
- Timings are per-step dt drawn from a truncated normal, cumulatively
  summed so the series is strictly monotonic.
