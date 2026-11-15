"""Enrollment table: every built-in solver, in one place.

A solver becomes available by being imported and registered here. With one
distribution there are no per-solver entry points to enumerate, so this tuple is
the list — 53 solvers today, greppable, diffable, no import-time magic.

Import or registration failure is per-module and non-fatal: slider and rotate need
opencv (--extra cv), classification and detection need torch. A worker started
without those extras logs a warning and answers ERR_NO_SOLVER for the affected
types instead of crashing at boot.

Adding a solver = write the module + append one line here.
"""

from __future__ import annotations

import importlib
import logging

log = logging.getLogger(__name__)

#: "module.path:ClassName" for every solver shipped in this distribution.
SOLVERS: tuple[str, ...] = (
    # antibot
    "captcha.solvers.antibot.akamai.sbsd:AkamaiSBSDSolver",
    "captcha.solvers.antibot.akamai.solver:AkamaiSensorSolver",
    "captcha.solvers.antibot.botdeflector.solver:BotDeflectorSolver",
    "captcha.solvers.antibot.botguard.solver:BotGuardSolver",
    "captcha.solvers.antibot.castle.solver:CastleSolver",
    "captcha.solvers.antibot.cloudflare.solver:CloudflareChallengeSolver",
    "captcha.solvers.antibot.cloudflare.turnstile:TurnstileSolver",
    "captcha.solvers.antibot.cybersiara.solver:CyberSiARASolver",
    "captcha.solvers.antibot.datadome.solver:DataDomeSolver",
    "captcha.solvers.antibot.imperva.solver:ImpervaSolver",
    "captcha.solvers.antibot.kasada.solver:KasadaSolver",
    "captcha.solvers.antibot.perimeterx.solver:PerimeterXSolver",
    "captcha.solvers.antibot.tspd.solver:TSPDSolver",
    "captcha.solvers.antibot.twitch_integrity.solver:TwitchIntegritySolver",
    # audio
    "captcha.solvers.audio.audio_captcha:AudioCaptchaSolver",
    # classification
    "captcha.solvers.classification.awswaf:AWSWAFSolver",
    "captcha.solvers.classification.funcaptcha_grid:FunCaptchaGridSolver",
    "captcha.solvers.classification.hcaptcha:HCaptchaSolver",
    "captcha.solvers.classification.recaptcha_grid:RecaptchaGridSolver",
    "captcha.solvers.classification.shein:SheinSolver",
    # detection
    "captcha.solvers.detection.bbox:BBoxSolver",
    "captcha.solvers.detection.click:ClickSolver",
    "captcha.solvers.detection.drag:DragDropSolver",
    "captcha.solvers.detection.draw:DrawAroundSolver",
    # funcaptcha
    "captcha.solvers.funcaptcha.bda:FunCaptchaBDASolver",
    "captcha.solvers.funcaptcha.funcaptcha:FunCaptchaEnterpriseSolver",
    "captcha.solvers.funcaptcha.funcaptcha:FunCaptchaSolver",
    # hcaptcha token: browser first, HTTP research path (Riot profile stays browser)
    "captcha.solvers.hcaptcha.browser:HCaptchaBrowserSolver",
    "captcha.solvers.hcaptcha.http:HCaptchaHTTPSolver",
    # interactive
    "captcha.solvers.interactive.alibaba.solver:AlibabaSolver",
    "captcha.solvers.interactive.atbcaptcha.solver:AtbCaptchaSolver",
    "captcha.solvers.interactive.basilisk.solver:BasiliskSolver",
    "captcha.solvers.interactive.binance.solver:BinanceSolver",
    "captcha.solvers.interactive.capy.solver:CapySolver",
    "captcha.solvers.interactive.captchafox.solver:CaptchaFoxSolver",
    "captcha.solvers.interactive.cutcaptcha.solver:CutCaptchaSolver",
    "captcha.solvers.interactive.geetest.v3:GeetestV3Solver",
    "captcha.solvers.interactive.geetest.v4:GeetestV4Solver",
    "captcha.solvers.interactive.hunt.solver:HuntSolver",
    "captcha.solvers.interactive.keycaptcha.solver:KeyCaptchaSolver",
    "captcha.solvers.interactive.lemin.solver:LeminSolver",
    "captcha.solvers.interactive.mtcaptcha.solver:MTCaptchaSolver",
    "captcha.solvers.interactive.prosopo.solver:ProsopoSolver",
    "captcha.solvers.interactive.temu.solver:TemuSolver",
    "captcha.solvers.interactive.tencent.solver:TencentSolver",
    "captcha.solvers.interactive.vk.solver:VKSolver",
    "captcha.solvers.interactive.yandex.solver:YandexSolver",
    "captcha.solvers.interactive.yidun.solver:YidunSolver",
    # ocr
    "captcha.solvers.ocr.image_to_text:ImageToTextSolver",
    "captcha.solvers.ocr.math_image:MathImageSolver",
    "captcha.solvers.ocr.text_captcha:TextCaptchaSolver",
    # pow
    "captcha.solvers.pow.altcha:AltchaSolver",
    "captcha.solvers.pow.friendly:FriendlySolver",
    # recaptcha
    "captcha.solvers.recaptcha.v2_audio:RecaptchaV2AudioSolver",
    "captcha.solvers.recaptcha.v2_audio:RecaptchaV2EnterpriseSolver",
    # rotate
    "captcha.solvers.rotate.rotate:RotateSolver",
    # score_based
    "captcha.solvers.score_based.recaptcha_v3:RecaptchaV3EnterpriseSolver",
    "captcha.solvers.score_based.recaptcha_v3:RecaptchaV3Solver",
    "captcha.solvers.score_based.turnstile:TurnstileInvisibleSolver",
    # slider
    "captcha.solvers.slider.slider:SliderSolver",
)


def _register_one(spec: str) -> bool:
    module_path, _, class_name = spec.partition(":")
    try:
        module = importlib.import_module(module_path)
        solver_cls = getattr(module, class_name)
    except Exception as exc:
        log.warning("solver skipped: %s (%s: %s)", spec, type(exc).__name__, exc)
        return False

    # Importing the module already fired @register_solver for most solvers; the
    # registry is idempotent, so this call is the authoritative registration.
    from captcha.solvers.registry import global_registry

    global_registry().register(solver_cls)
    return True


def load_builtin_solvers() -> tuple[int, int]:
    """Register every built-in solver. Returns (loaded, skipped) counts."""
    loaded = sum(1 for spec in SOLVERS if _register_one(spec))
    return loaded, len(SOLVERS) - loaded
