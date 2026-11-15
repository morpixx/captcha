"""hCaptcha-shaped grid classifier (generic classification_grid only).

The live hCaptcha challenge answer is a token minted by the browser or HTTP
wire solver (captcha.solvers.hcaptcha.*). This solver is the CV half: given a
pre-extracted grid + prompt it returns tile indices, and it is enrolled under
classification_grid only. Claiming hcaptcha / hcaptcha_enterprise here would
project indices into a solution shape that expects a token.

Default head: hcaptcha-v1 (3x3). See solvers/classification/grid.py.
"""

from __future__ import annotations

from captcha.domain import CaptchaType
from captcha.solvers import SolverInfo

from .base_grid import GridClassifier
from .grid import HEADS


class HCaptchaSolver(GridClassifier):
    info = SolverInfo(
        name="hcaptcha",
        types=(CaptchaType.CLASSIFICATION_GRID,),
        http=True,

        needs_js_runtime=False,
        needs_gpu=True,
        experimental=False,
        version="0.1.0",
    )

    head = HEADS["hcaptcha-v1"]
