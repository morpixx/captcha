"""FunCAPTCHA image-selection variant (not the rotate/3d variants).

Arkose ships multiple variants. The solvers in this package handle only the
grid-selection ones ("pick all images with a <prompt>"). Rotation, 3D dice and
the token exchange live in captcha.solvers.funcaptcha, which owns the
funcaptcha / funcaptcha_enterprise types.

This solver is a grid head, so it is enrolled under classification_grid only;
a 2x3 payload with head="funcap-v1" reaches it. Arkose's stylized tile set does
not transfer cleanly from hCaptcha's photorealistic tiles, hence a separate
head.
"""

from __future__ import annotations

from captcha.domain import CaptchaType
from captcha.solvers import SolverInfo

from .base_grid import GridClassifier
from .grid import HEADS


class FunCaptchaGridSolver(GridClassifier):
    info = SolverInfo(
        name="funcaptcha_grid",
        types=(CaptchaType.CLASSIFICATION_GRID,),
        http=True,

        needs_js_runtime=False,
        needs_gpu=True,
        experimental=True,
        version="0.1.0",
    )

    head = HEADS["funcap-v1"]
