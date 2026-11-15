"""PerimeterX (HUMAN) endpoints.

Each tenant has a short appId (e.g. `PXabc1234`); the collector lives at
`https://collector-<appId>.px-cloud.net/api/v2/collector` on current builds and at
`collector-<appId>.perimeterx.net` on older ones. A payload may pin either the
full collector URL or just the appId; both are resolved here.
"""

from __future__ import annotations

COLLECTOR_HOST_FMT: str = "https://collector-{app_id}.px-cloud.net"
#: Pre-rebrand host; still answers for accounts created before the rename.
COLLECTOR_LEGACY_HOST_FMT: str = "https://collector-{app_id}.perimeterx.net"
COLLECTOR_PATH: str = "/api/v2/collector"

#: The challenge interstitial (captcha-type `px3`/`px2`), served off the
#: collector host as well; the page itself is a browser-lane problem.
CAPTCHA_HOST_FMT: str = "https://captcha.px-cloud.net/{app_id}"
CAPTCHA_SUBMIT_FMT: str = "https://captcha.px-cloud.net/{app_id}/api/v2/captcha"

#: Vendors a captured request may legitimately target.
PX_VENDORS: tuple[str, ...] = ("px-cloud.net", "perimeterx.net")

#: Cookies the answer can be reduced to; `_pxhd` is the human verdict, `_px` the
#: sensor token, `_pxvid` the visitor id that ties rounds together.
PX_COOKIES: tuple[str, ...] = ("_px", "_px2", "_px3", "_pxhd", "_pxvid", "_pxde", "pxcts")

#: Prefix a browser-lane poll matches (names are suffixed per deployment).
PX_COOKIE_PREFIXES: tuple[str, ...] = ("_px", "pxcts")

#: The cookie a client replays; `_pxhd` when present, else `_px`.
PX_PRIMARY_COOKIES: tuple[str, ...] = ("_pxhd", "_px", "_px2", "_px3")


def collector_url(app_id: str, *, explicit: str = "") -> str:
    """Collector endpoint for a tenant; an explicit URL always wins."""
    if explicit.lower().startswith(("http://", "https://")):
        return explicit
    return COLLECTOR_HOST_FMT.format(app_id=app_id).rstrip("/") + COLLECTOR_PATH


__all__ = [
    "CAPTCHA_HOST_FMT",
    "CAPTCHA_SUBMIT_FMT",
    "COLLECTOR_HOST_FMT",
    "COLLECTOR_LEGACY_HOST_FMT",
    "COLLECTOR_PATH",
    "PX_COOKIES",
    "PX_COOKIE_PREFIXES",
    "PX_PRIMARY_COOKIES",
    "PX_VENDORS",
    "collector_url",
]
