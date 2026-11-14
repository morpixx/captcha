"""hCaptcha + hCaptcha Enterprise grid solver.

Also registered as the generic CLASSIFICATION_GRID handler — the live hCaptcha
challenge is just `image_b64 + prompt + 3x3 grid`, same shape as the generic
grid type, so the same solver serves both. Enterprise variant is pure
re-skinning, no payload change.

Default head: `hcaptcha-v1` in captcha-models.

Example payload:
    {
      "image_b64": "<png bytes b64>",
      "prompt": "motorcycle",
      "rows": 3,
      "cols": 3
    }
"""

from __future__ import annotations

from captcha.domain import CaptchaType
from captcha.solvers import SolverInfo

from .base_grid import GridClassifier


class HCaptchaSolver(GridClassifier):
    info = SolverInfo(
        name="hcaptcha",
        types=(
            CaptchaType.HCAPTCHA,
            CaptchaType.HCAPTCHA_ENTERPRISE,
            CaptchaType.CLASSIFICATION_GRID,
        ),
        http=True,
        browser=False,
        needs_js_runtime=False,
        needs_gpu=True,
        experimental=False,
        version="0.1.0",
    )

    default_head_name = "hcaptcha-v1"
    default_rows = 3
    default_cols = 3
