"""MTCaptcha vendor endpoints (mtcv1 API, 2026-10-23 widget build).

Captured from `mtcv1/client/iframe.html` (the bundle the widget actually
runs) and cross-checked against the MIT `kalkih/mtcaptcha-client` port. The
three calls are plain JSON GETs; the image call additionally carries the
`mtv1Pulse` risk cookie built by `pulse.py`.

The host may be rotated through `service{1..4}.mtcaptcha.com`; the challenge
reply names the canonical verifier domain, so a solver should reuse the host it
started with rather than invent one.
"""

from __future__ import annotations

BASE = "https://service.mtcaptcha.com/mtcv1"
API = BASE + "/api"
GET_CHALLENGE = API + "/getchallenge.json"
GET_IMAGE = API + "/getimage.json"
SOLVE_CHALLENGE = API + "/solvechallenge.json"

#: The iframe document the widget requests come from; used as Referer.
IFRAME = BASE + "/client/iframe.html"
#: `v=` of the current widget build; also the `jsV` cookie value.
JS_VERSION = "2026-10-23.19.20.13"
#: Fixed `mtv1ConfSum` cookie the widget writes before the first image fetch.
CONF_SUM = "{v:01|wdsz:std|thm:highcontrast|lan:sv|chlg:std|clan:1|cstyl:1|afv:0|afot:0|}"
#: Salt in the per-sitekey transaction signature: `TH[md5(salt+sitekey)]`.
TRANSACTION_SALT = "mtcap@mtcaptcha.com"
