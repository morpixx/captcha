"""Make the repo-root `tools/` package importable in tests.

`pyproject.toml` puts only `src` on pytest's pythonpath; ml_probe lives at
`tools/ml_probe` and is imported as `tools.ml_probe.metrics`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
