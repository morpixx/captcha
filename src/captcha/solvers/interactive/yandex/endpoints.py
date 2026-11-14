"""Yandex SmartCaptcha endpoints.

Public docs: https://yandex.cloud/en/docs/smartcaptcha
Cloud ingress: `smartcaptcha.yandexcloud.net/captcha.js`.
API: `captcha-api.yandex.ru`.

Frontend tokens:
  invisible  -> site POST body `smart-token`
  checkbox   -> same, lower complexity
  advanced   -> image grid click, returns `dR001:...` or similar prefix.
"""

from __future__ import annotations

API_HOST = "captcha-api.yandex.ru"
CHALLENGE = "https://captcha-api.yandex.ru/captcha.js"  # loader
PICTURE = "https://captcha-api.yandex.ru/image"  # image bytes
CHECK = "https://captcha-api.yandex.ru/check"
VALIDATE = "https://captcha-api.yandex.ru/validate"  # 2nd stage
