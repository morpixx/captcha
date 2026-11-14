"""Per-antibot-engine sensor payload builders.

Importing this package registers every bundled builder with
`SensorRegistry`. Solvers resolve a builder via `get_builder(engine)`.
"""

from __future__ import annotations

from captcha_common.enums import AntibotEngine

from .akamai import AkamaiSensorBuilder
from .base import BuildContext, SensorBuilder
from .botdeflector import BotDeflectorBuilder
from .botguard import BotGuardBuilder
from .castle import CastleBuilder
from .cloudflare import CloudflareChallengeBuilder
from .cybersiara import CyberSiaraBuilder
from .datadome import DataDomeBuilder
from .imperva import ImpervaBuilder
from .kasada import KasadaBuilder
from .perimeterx import PerimeterXBuilder
from .registry import (
    SensorRegistry,
    get_builder,
    global_registry,
    register_builder,
)
from .tspd import TspdBuilder
from .twitch import TwitchIntegrityBuilder


def build_akamai() -> AkamaiSensorBuilder:
    return get_builder(AntibotEngine.AKAMAI)  # type: ignore[return-value]


def build_datadome() -> DataDomeBuilder:
    return get_builder(AntibotEngine.DATADOME)  # type: ignore[return-value]


def build_kasada() -> KasadaBuilder:
    return get_builder(AntibotEngine.KASADA)  # type: ignore[return-value]


def build_perimeterx() -> PerimeterXBuilder:
    return get_builder(AntibotEngine.PERIMETERX)  # type: ignore[return-value]


def build_imperva() -> ImpervaBuilder:
    return get_builder(AntibotEngine.IMPERVA)  # type: ignore[return-value]


def build_cloudflare() -> CloudflareChallengeBuilder:
    return get_builder(AntibotEngine.CLOUDFLARE)  # type: ignore[return-value]


def build_castle() -> CastleBuilder:
    return get_builder(AntibotEngine.CASTLE)  # type: ignore[return-value]


def build_tspd() -> TspdBuilder:
    return get_builder(AntibotEngine.TSPD)  # type: ignore[return-value]


def build_cybersiara() -> CyberSiaraBuilder:
    return get_builder(AntibotEngine.CYBERSIARA)  # type: ignore[return-value]


def build_twitch() -> TwitchIntegrityBuilder:
    return get_builder(AntibotEngine.TWITCH)  # type: ignore[return-value]


def build_botguard() -> BotGuardBuilder:
    return get_builder(AntibotEngine.BOTGUARD)  # type: ignore[return-value]


def build_botdeflector() -> BotDeflectorBuilder:
    return get_builder(AntibotEngine.BOTDEFLECTOR)  # type: ignore[return-value]


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
