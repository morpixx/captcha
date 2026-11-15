"""DataDome endpoint constants.

Hosts and paths come from ``docs/reverse-2026.md`` §5 (live dumps 2026-06 …
2026-11): the tags endpoint, the geo interstitial, the per-check URL and the
versioned challenge bundle served from ``ct.captcha-delivery.com``.
"""

from __future__ import annotations

# Classic full challenge — the JS POSTs here with the probed signals.
DATADOME_JS_ENDPOINT: str = "https://api-js.datadome.co/js/"

# Interstitial / Device Check, served under a per-customer geo host.
DATADOME_CAPTCHA_HOST_FMT: str = "https://geo.captcha-delivery.com"
DATADOME_INTERSTITIAL_URL: str = "https://geo.captcha-delivery.com/interstitial/"
DATADOME_CAPTCHA_CHECK: str = "https://geo.captcha-delivery.com/captcha/check"

# Slider challenge image CDN. Behavior / coords get submitted back to
# /captcha/check with the dd-tc / dd-tr tokens.
DATADOME_SLIDER_IMAGE_FMT: str = "https://dd.prod.captcha-delivery.com/image/2024-02-05/{pin}.jpg"

# Challenge script the interstitial loads; pinned by release, not by us.
DATADOME_CHALLENGE_SCRIPT_FMT: str = "https://ct.captcha-delivery.com/interstitial.{version}.js"

#: Cookie every DataDome answer is built around.
DATADOME_COOKIES: tuple[str, ...] = ("datadome",)
