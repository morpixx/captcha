"""FunCAPTCHA image-selection variant (not the rotate/3d variants).

Arkose ships multiple variants. The solvers in this package handle only the
grid-selection ones ("pick all images with a <prompt>"). Rotation and 3D-dice
variants go to the geometry solver package.

Default head: `funcap-v1` — tuned against Arkose's stylized tile set
(upscaled rendered 3D plus a smaller photo subset), which doesn't transfer
cleanly from hCaptcha's photorealistic tiles.
"""

from __future__ import annotations

from captcha_common import CaptchaType
from captcha_solver_base import SolverInfo

from .base_grid import GridClassifier


class FunCaptchaGridSolver(GridClassifier):
    info = SolverInfo(
        name="funcaptcha_grid",
        types=(
            CaptchaType.FUNCAPTCHA,
            CaptchaType.FUNCAPTCHA_ENTERPRISE,
        ),
        http=True,
        browser=False,
        needs_js_runtime=False,
        needs_gpu=True,
        experimental=True,
        version="0.1.0",
    )

    default_head_name = "funcap-v1"
    default_rows = 2
    default_cols = 3
