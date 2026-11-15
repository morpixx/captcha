"""Yandex SmartCaptcha endpoints.

The widget loader is `captcha.js` on `smartcaptcha.yandexcloud.net` or
`captcha-api.yandex.ru`; everything after it is iframe `postMessage` traffic
between hashed `checkbox.<lang>.<hash>.html`, `advanced.<lang>.<hash>.html` and
`backend.<hash>.html` documents. The token is written into the page's hidden
`smart-token` input by `window.smartCaptcha.getResponse()` — there is no public
HTTP call that mints it, and the hashed document names rotate with each build.

That is why the solver only keeps the stable loader/origin constants and does
the work in the browser lane.
"""

from __future__ import annotations

WIDGET = "https://smartcaptcha.yandexcloud.net/captcha.js"
ORIGIN = "https://captcha-api.yandex.ru"
#: Second supported loader origin (the widget regexp accepts either).
ORIGIN_ALT = "https://smartcaptcha.yandexcloud.net"
