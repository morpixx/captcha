"""VK endpoints: the legacy image captcha and the VK ID "not a robot" page.

VK API error 14 historically returned `captcha_sid` + `captcha_img` pointing at
`captcha.php`; that image is still an OCR task and is served by this module.
The 2026 flow replaces it with `id.vk.com/not_robot_captcha?...` (the
`redirectUri` 2captcha asks for), where the page runs a PoW + FingerprintJS and
returns `success_token`. There is no public HTTP endpoint that mints that token:
the script binds it to the browser session, fingerprint and IP.

`captchaNotRobot.getContent` is the image/steps variant of the widget; its
"steps" answer is a kaleidoscope path (2captcha's own custom task), not a plain
OCR result, so it is rejected explicitly instead of being half-implemented.
"""

from __future__ import annotations

CAPTCHA_IMAGE = "https://api.vk.com/captcha.php"  # ?sid=<captcha_sid>
NOT_ROBOT_CONTENT = "https://api.vk.com/method/captchaNotRobot.getContent"
#: The page host the `redirectUri` points at; the browser lane opens the URI.
ID_HOST = "https://id.vk.com"
