"""AWS WAF image-challenge grid classifier.

AWS WAF's image challenge is "pick all tiles containing <X>" on a 3x3 grid of
synthetic renders. The voucher/token exchange is a separate wire step; this
solver only scores the tiles once the grid and prompt are available.

Default head: awswaf-v1 — small vocab, distinctive icon style, cheap to train.
"""

from __future__ import annotations

from captcha.domain import CaptchaType
from captcha.solvers import SolverInfo

from .base_grid import GridClassifier
from .grid import HEADS


class AWSWAFSolver(GridClassifier):
    info = SolverInfo(
        name="awswaf",
        types=(CaptchaType.AWSWAF,),
        http=True,

        needs_js_runtime=False,
        needs_gpu=True,
        experimental=True,
        version="0.1.0",
    )

    head = HEADS["awswaf-v1"]
