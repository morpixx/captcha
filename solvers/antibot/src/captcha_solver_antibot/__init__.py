"""captcha-solver-antibot — one plugin per antibot engine.

Solvers register through the `captcha.solvers` entry-points group; importing
this module is cheap and does not pull engine code. Enumerate via
``importlib.metadata.entry_points(group="captcha.solvers")`` instead of touching
the list below.
"""

from __future__ import annotations

ANTIBOT_SOLVERS: tuple[str, ...] = (
    "captcha_solver_antibot.cloudflare.solver:CloudflareChallengeSolver",
    "captcha_solver_antibot.cloudflare.turnstile:TurnstileSolver",
    "captcha_solver_antibot.akamai.solver:AkamaiSensorSolver",
    "captcha_solver_antibot.akamai.sbsd:AkamaiSBSDSolver",
    "captcha_solver_antibot.datadome.solver:DataDomeSolver",
    "captcha_solver_antibot.kasada.solver:KasadaSolver",
    "captcha_solver_antibot.perimeterx.solver:PerimeterXSolver",
    "captcha_solver_antibot.imperva.solver:ImpervaSolver",
    "captcha_solver_antibot.castle.solver:CastleSolver",
    "captcha_solver_antibot.tspd.solver:TSPDSolver",
    "captcha_solver_antibot.cybersiara.solver:CyberSiARASolver",
    "captcha_solver_antibot.twitch_integrity.solver:TwitchIntegritySolver",
    "captcha_solver_antibot.botguard.solver:BotGuardSolver",
    "captcha_solver_antibot.botdeflector.solver:BotDeflectorSolver",
)

__all__ = ["ANTIBOT_SOLVERS"]
