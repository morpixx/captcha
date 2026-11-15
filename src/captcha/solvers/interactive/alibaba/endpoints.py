"""Alibaba / Aliyun captcha endpoints.

Two generations share this type:

* NoCaptcha v1 (the legacy JSONP API) - get.jsonp hands out a challenge
  (csessionid, sig, token, scene, bg/front) and analyze.jsonp takes the
  geometry back. This is the HTTP path.
* Captcha 2.0 widget - the page SDK produces a signed CaptchaVerifyParam that
  the site backend verifies server-side. No HTTP endpoint accepts that
  parameter directly, so the widget path runs in the browser lane.

UMID (the device token) is collected at ynuf.aliapp.org and rides along with
the get.jsonp call.
"""

from __future__ import annotations

API_HOST = "cf.aliyun.com"
UMID_HOST = "https://ynuf.aliapp.org"
UMID_ENDPOINT = f"{UMID_HOST}/service/um.json"

NC_GET = f"https://{API_HOST}/nocaptcha/get.jsonp"
NC_ANALYZE = f"https://{API_HOST}/nocaptcha/analyze.jsonp"

#: Older names kept for callers that imported them.
GET_JSONP = NC_GET
ANALYZE_JSONP = NC_ANALYZE
