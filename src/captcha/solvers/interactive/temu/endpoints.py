"""Temu risk-control captcha endpoints.

Temu serves a bespoke in-page challenge at api/bg/aegis/* backed by PDD
Holdings' anti_content fingerprint. Requests carry the fingerprint in the
anti-content header; the signature covers the pointer trace and device hash.
There is no sitekey and no third-party iframe: the challenge belongs to the
page, which is why the HTTP path needs the client-captured anti_content.

The paths below are the ones the web client uses; they are undocumented and
per-build, so parsing keeps aliases and the solver fails loudly on drift.
"""

from __future__ import annotations

PAGE_HOST = "www.temu.com"
PAGE_BASE = f"https://{PAGE_HOST}"

CAPTCHA_INIT = f"{PAGE_BASE}/api/bg/aegis/captcha/init"
CAPTCHA_VERIFY = f"{PAGE_BASE}/api/bg/aegis/captcha/verify"

#: Names used by the older solver stub and by callers that imported them.
VERIFY_INIT = CAPTCHA_INIT
VERIFY_SUBMIT = CAPTCHA_VERIFY
