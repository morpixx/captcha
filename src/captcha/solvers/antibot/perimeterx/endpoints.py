"""PerimeterX (HUMAN) endpoints.

Each tenant has a short `appId` (e.g. ``PXabc1234``); the collector lives
at ``https://collector-{appId}.perimeterx.net``.
"""

from __future__ import annotations

COLLECTOR_HOST_FMT: str = "https://collector-{app_id}.perimeterx.net"
COLLECTOR_PATH: str = "/api/v2/collector"

# The challenge interstitial (captcha-type `px3`/`px2`).
CAPTCHA_HOST_FMT: str = "https://captcha.px-cloud.net/{app_id}"
CAPTCHA_SUBMIT_FMT: str = "https://captcha.px-cloud.net/{app_id}/api/v2/captcha"

PX_COOKIES: tuple[str, ...] = ("_px", "_px2", "_px3", "_pxhd", "_pxvid", "_pxde")
