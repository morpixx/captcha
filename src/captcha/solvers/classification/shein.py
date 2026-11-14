"""Shein's bespoke image-selection captcha.

Simple grid, narrow vocab (consumer-goods photos — shoes, bags, clothing),
decent lighting and no occluders. Easiest head of the five.

Default head: `shein-v1`.
"""

from __future__ import annotations

from captcha.domain import CaptchaType
from captcha.solvers import SolverInfo

from .base_grid import GridClassifier


class SheinSolver(GridClassifier):
    info = SolverInfo(
        name="shein",
        types=(CaptchaType.SHEIN,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        needs_gpu=True,
        experimental=True,
        version="0.1.0",
    )

    default_head_name = "shein-v1"
    default_rows = 3
    default_cols = 3
