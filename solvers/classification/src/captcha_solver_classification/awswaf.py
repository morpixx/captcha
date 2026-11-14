"""AWS WAF captcha grid solver.

AWS WAF's image challenge is "pick all tiles containing <X>" on a 3x3 grid of
synthetic renders (toys, icons, photos). The token exchange is handled by the
antibot engine solver; this one only does the per-tile classification once
tiles + prompt have been extracted.

Default head: `awswaf-v1` — small vocab, distinctive icon style, cheap to train.
"""

from __future__ import annotations

from captcha_common import CaptchaType
from captcha_solver_base import SolverInfo

from .base_grid import GridClassifier


class AWSWAFSolver(GridClassifier):
    info = SolverInfo(
        name="awswaf",
        types=(CaptchaType.AWSWAF,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        needs_gpu=True,
        experimental=True,
        version="0.1.0",
    )

    default_head_name = "awswaf-v1"
    default_rows = 3
    default_cols = 3
