"""MTCaptcha vendor endpoints (public JS SDK surface).

The JS client is loaded from `service.mtcaptcha.com/mtcv1/client/mtcaptcha.min.js`
which drives the two endpoints below. The host may be rotated through
`service{1..4}.mtcaptcha.com` by the client at runtime; replies carry the
canonical host in the `vd` (verifier domain) field — reuse it for submit.
"""

from __future__ import annotations

API_HOST = "service.mtcaptcha.com"
GET_CHALLENGE = "https://service.mtcaptcha.com/mtcv1/api/getchallengev3.json"
SUBMIT_CHALLENGE = "https://service.mtcaptcha.com/mtcv1/api/submitchallenge.json"

# Final token shape (observed): `v1(<md5-upper>,<md5-upper>,<sitekey>,hash:<hex>*<nonce>)`
TOKEN_TEMPLATE = "v1({ch1},{ch2},{sk},hash:{hash}*{nonce})"
