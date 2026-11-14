"""VK captcha endpoints.

Three variants:
  - classic image captcha served at `api.vk.com/captcha.php?sid=<sid>`
    answered by resubmitting the parent request with `captcha_sid` + `captcha_key`.
  - slider captcha on `id.vk.com` login flow, token `smart_token`.
  - account verification 3-point click on `login.vk.com/?act=check` — bespoke.
"""

from __future__ import annotations

CAPTCHA_IMAGE = "https://api.vk.com/captcha.php"  # ?sid=...
ID_HOST = "https://id.vk.com"
ID_SLIDER_INIT = "https://id.vk.com/account/api/v1/captcha/init"
ID_SLIDER_VERIFY = "https://id.vk.com/account/api/v1/captcha/verify"
