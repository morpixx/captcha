"""Alibaba / Aliyun captcha (AliyunCaptchaCallback / NoCaptcha / `nc`).

SDK is served under `AlibabaChina` namespace. Primary API:
  `https://cf.aliyun.com/nocaptcha/*.jsonp`
UMID (device fingerprint) is signed at `https://ynuf.aliapp.org/service/um.json`.
"""

from __future__ import annotations

UMID_HOST = "https://ynuf.aliapp.org"
UMID_ENDPOINT = "https://ynuf.aliapp.org/service/um.json"
NC_GET = "https://cf.aliyun.com/nocaptcha/get.jsonp"
NC_ANALYZE = "https://cf.aliyun.com/nocaptcha/analyze.jsonp"
