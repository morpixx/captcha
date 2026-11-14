"""Temu riskcontrol captcha endpoints.

Temu serves a bespoke slider at `www.temu.com/api/bg/aegis/*`, backed by
PDD Holdings' `anti_content` (`a_c`) fingerprint. Payloads are gzipped and
the signature covers mouse trajectory + device hash.
"""

from __future__ import annotations

API_HOST = "www.temu.com"
VERIFY_INIT = "https://www.temu.com/api/bg/aegis/captcha/init"
VERIFY_SUBMIT = "https://www.temu.com/api/bg/aegis/captcha/verify"
