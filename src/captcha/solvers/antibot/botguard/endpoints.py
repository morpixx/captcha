"""Google BotGuard endpoints.

BotGuard is the VM behind reCAPTCHA / YouTube / Play signals. The
challenge program (``f.txt``) is served at a product-specific URL and
the result posts to its paired verify endpoint. We keep hints only —
real URLs are product-scoped (reCAPTCHA v3 vs Play Integrity vs account
sign-in).
"""

from __future__ import annotations

# reCAPTCHA v3 / v2 challenge program.
BOTGUARD_RECAPTCHA_FTX: str = "https://www.google.com/recaptcha/api2/bframe"
BOTGUARD_RECAPTCHA_PAYLOAD: str = "https://www.google.com/recaptcha/api2/userverify"

# Account / "I'm not a robot" bot-guard challenges attach their f.txt
# inline on the page; the response POSTs to `/_/gm/t` variants.
# FILL: capture live f.txt + verify URL for the specific product target.
