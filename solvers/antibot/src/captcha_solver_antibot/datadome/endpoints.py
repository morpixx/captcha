"""DataDome endpoint constants."""

from __future__ import annotations

# Classic full challenge — the JS POSTs here with the probed signals.
DATADOME_JS_ENDPOINT: str = "https://api-js.datadome.co/js/"

# Interstitial geo endpoint (served under a per-customer subdomain).
DATADOME_CAPTCHA_HOST_FMT: str = "https://geo.captcha-delivery.com"
DATADOME_CAPTCHA_CHECK: str = "https://geo.captcha-delivery.com/captcha/check"

# Slider challenge image CDN. Behavior / coords get submitted back to
# /captcha/check with the dd-tc / dd-tr tokens.
DATADOME_SLIDER_IMAGE_FMT: str = "https://dd.prod.captcha-delivery.com/image/2024-02-05/{pin}.jpg"

DATADOME_COOKIES: tuple[str, ...] = ("datadome",)
