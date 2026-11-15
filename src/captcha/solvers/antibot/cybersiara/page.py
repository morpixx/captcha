"""The CyberSiARA token: where the page leaves it, and how to check it.

The widget stores the master URL token where the page can read it back; the
concrete place differs per integration (a storage key, a cookie, or an object on
`window`). `HARVEST_JS` looks in all three and reports what it found plus what
it checked, so a failure says *why* instead of "no token".

`token_from_payload` does the same for a replayed verify response: it walks the
JSON two levels deep for a token-shaped value under a documented key name.
"""

from __future__ import annotations

import json
import re
from typing import Any

from captcha.domain.errors import UnsolvableError

MIN_TOKEN_LEN = 16
_MAX_TOKEN_LEN = 8192
_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/=_.:|~-]+$")

STORAGE_KEYS: tuple[str, ...] = ("master_url_token", "MasterUrlToken", "masterUrlToken", "master_url")


HARVEST_JS = r"""
({keys, cookiePrefixes}) => {
  const out = {token: "", source: null, error: null, diag: {storage: [], cookie: [], globals: []}};
  const wanted = keys || [];
  const read = (bucket, source) => {
    for (const key of wanted) {
      try {
        const value = bucket ? bucket.getItem(key) : null;
        if (value) { out.token = String(value); out.source = source + ":" + key; return true; }
      } catch (e) { /* storage disabled */ }
    }
    return false;
  };
  if (read(window.localStorage, "localStorage")) return out;
  if (read(window.sessionStorage, "sessionStorage")) return out;
  try {
    const jar = String(document.cookie || "");
    for (const part of jar.split(";")) {
      const i = part.indexOf("=");
      if (i <= 0) continue;
      const name = part.slice(0, i).trim();
      const value = part.slice(i + 1).trim();
      out.diag.cookie.push(name);
      if (value && (cookiePrefixes || []).some((prefix) => name.startsWith(prefix))) {
        out.token = value; out.source = "cookie:" + name; return out;
      }
    }
  } catch (e) { out.error = "cookie-read: " + String(e); }
  const globals = [window.MasterUrlToken, window.masterUrlToken, window.MasterController && window.MasterController.token];
  for (const value of globals) {
    if (typeof value === "string" && value) { out.token = value; out.source = "window"; return out; }
  }
  out.diag.globals = globals.map((value) => typeof value);
  out.error = out.error || "token-missing";
  return out;
}
"""


async def harvest(page: Any, *, sitekey: str, cookie_prefixes: tuple[str, ...]) -> dict[str, Any]:
    """Run :data:`HARVEST_JS` on the page and return its report."""
    out = await page.evaluate(HARVEST_JS, {"keys": list(STORAGE_KEYS), "cookiePrefixes": list(cookie_prefixes)})
    return out if isinstance(out, dict) else {"token": "", "error": "harvest-returned-non-object"}


def validate_token(value: Any, *, sitekey: str) -> str:
    """Reject a placeholder before it is sold as a token."""
    token = value.strip() if isinstance(value, str) else ""
    if not token:
        raise UnsolvableError("cybersiara: empty master URL token", context={"sitekey": sitekey})
    if token == sitekey:
        raise UnsolvableError("cybersiara: the widget echoed the site key", context={"sitekey": sitekey})
    if len(token) < MIN_TOKEN_LEN or len(token) > _MAX_TOKEN_LEN:
        raise UnsolvableError("cybersiara: token length out of range", context={"length": len(token)})
    if any(ch.isspace() for ch in token) or not _TOKEN_RE.match(token):
        raise UnsolvableError("cybersiara: unexpected token shape", context={"head": token[:16]})
    return token


def token_from_payload(raw: Any, keys: tuple[str, ...] = STORAGE_KEYS) -> str:
    """Walk a JSON answer two levels deep for a token-shaped string."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ""
        try:
            raw = json.loads(text)
        except ValueError:
            return text if _TOKEN_RE.match(text) and len(text) >= MIN_TOKEN_LEN else ""
    if not isinstance(raw, dict):
        return ""
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value and _TOKEN_RE.match(value):
            return value
    for value in raw.values():
        if isinstance(value, dict):
            for key in keys:
                nested = value.get(key)
                if isinstance(nested, str) and nested and _TOKEN_RE.match(nested):
                    return nested
    return ""


__all__ = [
    "HARVEST_JS",
    "MIN_TOKEN_LEN",
    "STORAGE_KEYS",
    "harvest",
    "token_from_payload",
    "validate_token",
]
