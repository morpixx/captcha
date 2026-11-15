"""CaptchaFox endpoints (api.js build `66bfc668cc8dc345`).

The widget's public API is `api.captchafox.com/captcha/<sitekey>/config` and
`.../challenge`, plus a shared `/captcha/verify`. Request bodies are gzip JSON
with a 2-byte header and a per-byte XOR — `wire.encode_body`.

The build hash in the path and the `X-Pulse`/`X-Ember` header values rotate
with the CDN release. They are pinned here because a mismatch is a protocol
drift that must be loud, not silently worked around; `# FILL` on the next
capture is "read them from the current `cdn.captchafox.com/api.js`".
"""

from __future__ import annotations

API = "https://api.captchafox.com"
CONFIG = API + "/captcha/{sitekey}/config"
CHALLENGE = API + "/captcha/{sitekey}/challenge"
VERIFY = API + "/captcha/verify"

#: Request headers the widget attaches; values from the 2026-11 build.
X_PULSE = "66bfc668cc8dc345"
X_EMBER = "1db87116"

#: Challenge kinds the widget serves.
KINDS = ("slide", "select", "attest", "audio")
