"""reCAPTCHA v2 grid solver (the "select all images with X" tiles).

The token-returning reCAPTCHA v2 solver lives elsewhere (browser / sensor
driven). This one is the CV path for a pre-extracted 3x3 or 4x4 grid — used
either by a browser flow that hands us tile pixels or by a client who sends
image + prompt directly.

Default head: `recaptcha-v1` in captcha-models. Separate head from hCaptcha —
reCAPTCHA's image distribution (Google Street View crops) is different enough
that a shared head loses accuracy.
"""

from __future__ import annotations

from captcha_common import CaptchaType
from captcha_solver_base import SolverInfo

from .base_grid import GridClassifier


class RecaptchaGridSolver(GridClassifier):
    info = SolverInfo(
        name="recaptcha_grid",
        types=(CaptchaType.CLASSIFICATION_GRID,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        needs_gpu=True,
        experimental=False,
        version="0.1.0",
    )

    default_head_name = "recaptcha-v1"
    default_rows = 3
    default_cols = 3
