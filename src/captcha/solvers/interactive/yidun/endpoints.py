"""NetEase Yidun (易盾) — captcha / dun.163.com.

Public SDK host: `cstaticdun.126.net`.
API host: `c.dun.163.com`.

Round: `/api/v2/get` allocates the challenge (token + image URLs), `/api/v2/check`
takes the AES-sealed answer together with the SDK's `cb` tag and returns
`validate`. The site's backend then re-signs `validate` with `zoneId` into the
value it submits — that part is the site's business, not ours.
"""

from __future__ import annotations

API_HOST = "c.dun.163.com"
GET_CONF = "https://c.dun.163.com/api/v2/get"  # challenge metadata
IMAGE_HOST = "https://necaptcha.nosdn.127.net"  # OSS bucket for puzzle PNGs
CHECK = "https://c.dun.163.com/api/v2/check"  # verify

#: `type` the SDK asks for, and the challenge kind it produces.
TYPE_SLIDER = 2
TYPE_CLICK = 3

#: How the widget is embedded: 10 is the plain web page (runEnv).
RUN_ENV_WEB = 10

#: Vendor replies that mean the round is gone rather than the answer wrong.
EXPIRED_MARKERS: tuple[str, ...] = ("expire", "timeout", "invalid token", "overdue", "过期", "超时")

#: `cb` is AES over the answer, keyed by material only the SDK knows; the solver
#: takes a sealer from `ctx.extra["yidun_signer"]` and escalates without one.
SEALER_KEY = "yidun_signer"
