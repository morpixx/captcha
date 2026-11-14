"""Per-engine payload builders — the reverse-engineering surface.

Every engine folder owns its schema constants (builder.py) and, once implemented,
a fixtures/ directory with live dumps for replay tests. Importing this package
registers all builders with the registry.

Builders are pure-ish: they assemble a payload from a BuildContext and do not
perform network calls. Driving HTTP is the solver's job.
"""

from __future__ import annotations

from captcha.domain.enums import AntibotEngine

from .base import BuildContext, SensorBuilder
from .akamai.builder import AkamaiSensorBuilder
from .botdeflector.builder import BotDeflectorBuilder
from .botguard.builder import BotGuardBuilder
from .castle.builder import CastleBuilder
from .cloudflare.builder import CloudflareChallengeBuilder
from .cybersiara.builder import CyberSiaraBuilder
from .datadome.builder import DataDomeBuilder
from .imperva.builder import ImpervaBuilder
from .kasada.builder import KasadaBuilder
from .perimeterx.builder import PerimeterXBuilder
from .tspd.builder import TspdBuilder
from .twitch.builder import TwitchIntegrityBuilder
from .registry import (
    SensorRegistry,
    get_builder,
    global_registry,
    register_builder,
)


def build_akamai() -> AkamaiSensorBuilder:
    return get_builder(AntibotEngine.AKAMAI)  # type: ignore[return-value]


def build_botdeflector() -> BotDeflectorBuilder:
    return get_builder(AntibotEngine.BOTDEFLECTOR)  # type: ignore[return-value]


def build_botguard() -> BotGuardBuilder:
    return get_builder(AntibotEngine.BOTGUARD)  # type: ignore[return-value]


def build_castle() -> CastleBuilder:
    return get_builder(AntibotEngine.CASTLE)  # type: ignore[return-value]


def build_cloudflare() -> CloudflareChallengeBuilder:
    return get_builder(AntibotEngine.CLOUDFLARE)  # type: ignore[return-value]


def build_cybersiara() -> CyberSiaraBuilder:
    return get_builder(AntibotEngine.CYBERSIARA)  # type: ignore[return-value]


def build_datadome() -> DataDomeBuilder:
    return get_builder(AntibotEngine.DATADOME)  # type: ignore[return-value]


def build_imperva() -> ImpervaBuilder:
    return get_builder(AntibotEngine.IMPERVA)  # type: ignore[return-value]


def build_kasada() -> KasadaBuilder:
    return get_builder(AntibotEngine.KASADA)  # type: ignore[return-value]


def build_perimeterx() -> PerimeterXBuilder:
    return get_builder(AntibotEngine.PERIMETERX)  # type: ignore[return-value]


def build_tspd() -> TspdBuilder:
    return get_builder(AntibotEngine.TSPD)  # type: ignore[return-value]


def build_twitch() -> TwitchIntegrityBuilder:
    return get_builder(AntibotEngine.TWITCH)  # type: ignore[return-value]


__all__ = [
    "AkamaiSensorBuilder",
    "BotDeflectorBuilder",
    "BotGuardBuilder",
    "BuildContext",
    "CastleBuilder",
    "CloudflareChallengeBuilder",
    "CyberSiaraBuilder",
    "DataDomeBuilder",
    "ImpervaBuilder",
    "KasadaBuilder",
    "PerimeterXBuilder",
    "SensorBuilder",
    "SensorRegistry",
    "TspdBuilder",
    "TwitchIntegrityBuilder",
    "build_akamai",
    "build_botdeflector",
    "build_botguard",
    "build_castle",
    "build_cloudflare",
    "build_cybersiara",
    "build_datadome",
    "build_imperva",
    "build_kasada",
    "build_perimeterx",
    "build_tspd",
    "build_twitch",
    "get_builder",
    "global_registry",
    "register_builder",
]
