"""Tencent TCaptcha (防水墙) vendor endpoints.

Public SDK: `https://turing.captcha.qcloud.com/TCaptcha.js`
Live widget talks to `t.captcha.qq.com` (CN edge) with a mirror on
`turing.captcha.qcloud.com`. Response and verify both use JSONP in the
browser; the plain-JSON variants below work for direct calls.

Round order: `prehandle` allocates `sess` + the challenge type, `new_show`
returns the images and the PoW parameters, `new_verify` takes the answer plus
the TDC `collect` blob and mints `ticket`/`randstr`.
"""

from __future__ import annotations

API_HOST = "t.captcha.qq.com"

PREHANDLE = "https://t.captcha.qq.com/cap_union_prehandle"
SHOW_NEW = "https://t.captcha.qq.com/cap_union_new_show"
VERIFY_NEW = "https://t.captcha.qq.com/cap_union_new_verify"
GET_IMG = "https://t.captcha.qq.com/cap_union_new_getcapbysig"

#: Final token: `{"ret":0,"ticket":"t03...","randstr":"@xyz","errorCode":"0"}`

#: `errorCode` values that mean the round is gone, not that the answer was
#: wrong: the session expired or was never opened for this aid.
EXPIRED_CODES: frozenset[str] = frozenset({"9", "12"})

#: `errorCode` values the vendor returns when `collect` (the TDC fingerprint)
#: is missing or rejected. Retryable on a fresh profile, not on a fresh answer.
SENSOR_CODES: frozenset[str] = frozenset({"7"})

#: How the `ans` field is shaped per challenge type (see `wire.format_ans`).
SLIDER_ANS = "x,y"
CLICK_ANS = "x1,y1|x2,y2|..."
