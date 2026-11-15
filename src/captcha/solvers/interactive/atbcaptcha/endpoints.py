"""atbCaptcha (aisecurius / 顶象-style) endpoints.

Sold as "AiSecurius" / "atb" slider + 3-point click; the SDK is hosted at
`cap.aisecurius.com` with a tenant-id prefix path. The check body's `data` is
the SDK's AES-GCM sealing of the answer, keyed by material only the page knows,
and the site's backend consumes `captchaVerification` from the reply.
"""

from __future__ import annotations

API_HOST = "cap.aisecurius.com"
GET_CONF = "https://cap.aisecurius.com/cap-api/v1/get"
VERIFY = "https://cap.aisecurius.com/cap-api/v1/check"

#: Challenge types the public SDK asks for (`type` query).
TYPE_SLIDER = 1
TYPE_CLICK = 2

#: Replies that mean the round is gone rather than the answer wrong.
EXPIRED_MARKERS: tuple[str, ...] = ("expire", "timeout", "invalid token", "overdue", "过期", "超时")

#: `data` is sealed by the SDK's JS (AES-GCM over the answer plus a per-session
#: key derived from `token` and the runtime fingerprint). The solver takes a
#: sealer from `ctx.extra["atb_sealer"]` and escalates without one.
SEALER_KEY = "atb_sealer"
