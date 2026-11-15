"""Browser adapters: session provider, pool, process supervisor.

`CamoufoxProvider` is the browser-lane implementation of
`captcha.domain.ports.BrowserProvider`: one fresh Camoufox session per task.
`Pool` is the older shared-context Playwright pool, still used where a task can
reuse a context. `Supervisor` owns the temporary profiles and process trees of
both.
"""

from __future__ import annotations

from .geoip import GeoIPCache, resolve_exit_ip
from .pool import Pool
from .prefs import FIREFOX_PREFS, build_options
from .provider import CamoufoxProvider, CamoufoxSession
from .proxy import parse_proxy, proxy_url
from .session import Session, SessionConfig
from .supervisor import PROFILE_PREFIX, Supervisor, remove_profile

__all__ = [
    "FIREFOX_PREFS",
    "PROFILE_PREFIX",
    "CamoufoxProvider",
    "CamoufoxSession",
    "GeoIPCache",
    "Pool",
    "Session",
    "SessionConfig",
    "Supervisor",
    "build_options",
    "parse_proxy",
    "proxy_url",
    "remove_profile",
    "resolve_exit_ip",
]
