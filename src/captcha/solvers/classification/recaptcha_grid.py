"""reCAPTCHA v2 grid classifier (the "select all images with X" tiles).

The token-returning reCAPTCHA v2 solver lives elsewhere (audio/browser flow).
This is the CV path for a pre-extracted grid: 3x3 by default, 4x4 when the
payload says so. Both shapes are part of the recaptcha-v1 contract.

Default head: recaptcha-v1 in captcha-models. Separate head from hCaptcha:
Google Street View crops are a different distribution.
"""

from __future__ import annotations

from captcha.domain import CaptchaType
from captcha.solvers import SolverInfo

from .base_grid import GridClassifier
from .grid import HEADS


class RecaptchaGridSolver(GridClassifier):
    info = SolverInfo(
        name="recaptcha_grid",
        types=(CaptchaType.CLASSIFICATION_GRID,),
        http=True,

        needs_js_runtime=False,
        needs_gpu=True,
        experimental=False,
        version="0.1.0",
    )

    head = HEADS["recaptcha-v1"]
