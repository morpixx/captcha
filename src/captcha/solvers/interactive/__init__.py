"""Vendor-specific interactive captcha plugins.

Solvers register themselves when imported. `INTERACTIVE_SOLVERS` is the enrollment
list consumed by `captcha.solvers.loader` — importing this package stays cheap and
does not pull any vendor code.
"""

from __future__ import annotations

from ._base import InteractiveBase

INTERACTIVE_SOLVERS: tuple[str, ...] = (
    "captcha.solvers.interactive.alibaba.solver:AlibabaSolver",
    "captcha.solvers.interactive.atbcaptcha.solver:AtbCaptchaSolver",
    "captcha.solvers.interactive.basilisk.solver:BasiliskSolver",
    "captcha.solvers.interactive.binance.solver:BinanceSolver",
    "captcha.solvers.interactive.captchafox.solver:CaptchaFoxSolver",
    "captcha.solvers.interactive.cutcaptcha.solver:CutCaptchaSolver",
    "captcha.solvers.interactive.hunt.solver:HuntSolver",
    "captcha.solvers.interactive.mtcaptcha.solver:MTCaptchaSolver",
    "captcha.solvers.interactive.prosopo.solver:ProsopoSolver",
    "captcha.solvers.interactive.temu.solver:TemuSolver",
    "captcha.solvers.interactive.tencent.solver:TencentSolver",
    "captcha.solvers.interactive.vk.solver:VKSolver",
    "captcha.solvers.interactive.yandex.solver:YandexSolver",
    "captcha.solvers.interactive.yidun.solver:YidunSolver",
)

__all__ = ["INTERACTIVE_SOLVERS", "InteractiveBase"]
