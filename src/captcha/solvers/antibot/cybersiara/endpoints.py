"""CyberSiARA endpoints and wire names.

CyberSiARA serves a slide-to-verify widget from `mfpapi.cybersiara.com`; the
site key is the widget's `SlideMasterUrlId` / `MasterUrlId`, and the value the
page finally submits is a master URL token. The verification call and its body
are produced by their widget script, not published, so this module pins only what
is documented: the host, the verify path, and the token's names in storage and
cookies.
"""

from __future__ import annotations

#: Widget API host.
CYBERSIARA_API: str = "https://mfpapi.cybersiara.com"

#: Verify path the widget posts a solved slide to (site key in the path).
CYBERSIARA_VERIFY_FMT: str = "https://mfpapi.cybersiara.com/api/v1/verify/{sitekey}"

#: Vendors a captured widget request may legitimately target.
CYBERSIARA_VENDORS: tuple[str, ...] = ("cybersiara.com",)

#: Names the token shows up under, in storage, cookies or a JSON answer.
CYBERSIARA_TOKEN_KEYS: tuple[str, ...] = (
    "master_url_token",
    "MasterUrlToken",
    "masterUrlToken",
    "master_url",
    "token",
)

#: Cookies the widget sets on the protected page.
CYBERSIARA_COOKIE_PREFIXES: tuple[str, ...] = ("master_url", "cybersiara", "MasterUrl")


def verify_url(sitekey: str) -> str:
    return CYBERSIARA_VERIFY_FMT.format(sitekey=sitekey)


__all__ = [
    "CYBERSIARA_API",
    "CYBERSIARA_COOKIE_PREFIXES",
    "CYBERSIARA_TOKEN_KEYS",
    "CYBERSIARA_VENDORS",
    "CYBERSIARA_VERIFY_FMT",
    "verify_url",
]
