"""captcha-solver-antibot — one plugin per antibot engine.

Solvers register through the `captcha.solvers` entry-points group; importing
this module is cheap and does not pull engine code. Enumerate via
``importlib.metadata.entry_points(group="captcha.solvers")`` instead of touching
the list below.
"""

from __future__ import annotations

ANTIBOT_SOLVERS: tuple[str, ...] = (
    "captcha.solvers.antibot.cloudflare.solver:CloudflareChallengeSolver",
    "captcha.solvers.antibot.cloudflare.turnstile:TurnstileSolver",
    "captcha.solvers.antibot.akamai.solver:AkamaiSensorSolver",
    "captcha.solvers.antibot.akamai.sbsd:AkamaiSBSDSolver",
    "captcha.solvers.antibot.datadome.solver:DataDomeSolver",
    "captcha.solvers.antibot.kasada.solver:KasadaSolver",
    "captcha.solvers.antibot.perimeterx.solver:PerimeterXSolver",
    "captcha.solvers.antibot.imperva.solver:ImpervaSolver",
    "captcha.solvers.antibot.castle.solver:CastleSolver",
    "captcha.solvers.antibot.tspd.solver:TSPDSolver",
    "captcha.solvers.antibot.cybersiara.solver:CyberSiARASolver",
    "captcha.solvers.antibot.twitch_integrity.solver:TwitchIntegritySolver",
    "captcha.solvers.antibot.botguard.solver:BotGuardSolver",
    "captcha.solvers.antibot.botdeflector.solver:BotDeflectorSolver",
)

__all__ = ["ANTIBOT_SOLVERS"]
