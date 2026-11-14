"""Tencent TCaptcha (防水墙) vendor endpoints.

Public SDK: `https://turing.captcha.qcloud.com/TCaptcha.js`
Live widget talks to `t.captcha.qq.com` (CN edge) with a mirror on
`turing.captcha.qcloud.com`. Response and verify both use JSONP in the
browser; the plain-JSON variants below work for direct calls.
"""

from __future__ import annotations

API_HOST = "t.captcha.qq.com"

PREHANDLE = "https://t.captcha.qq.com/cap_union_prehandle"
SHOW_NEW = "https://t.captcha.qq.com/cap_union_new_show"
VERIFY_NEW = "https://t.captcha.qq.com/cap_union_new_verify"
GET_IMG = "https://t.captcha.qq.com/cap_union_new_getcapbysig"

# Final token: `{"ret":0,"ticket":"t03...","randstr":"@xyz","errorCode":"0"}`
