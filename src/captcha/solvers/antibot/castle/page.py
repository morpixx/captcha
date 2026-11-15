"""The Castle page script and token check.

The SDK has to run on a real page: `createRequestToken()` is asynchronous, reads
DOM state and behavior collected from the moment `configure()` ran, and (in the
current build) spins up workers. mini-racer has no event loop, so the browser
lane is where a Castle token actually comes from.

`DRIVE_JS` is deliberately defensive: it never throws, it reports why it failed
(`sdk-missing`/`configure-failed`/`create-failed`), and it returns the device id
(`__cuid`) even when no token was minted -- a token without its device id is not
worth shipping, and a failure with a reason is worth logging.
"""

from __future__ import annotations

import re
from typing import Any

from captcha.domain.errors import SensorError, UnsolvableError

#: A real token is one long opaque string; whitespace or a short value means the
#: SDK handed back a placeholder (or the page echoed something else).
MIN_TOKEN_LEN = 20
_MAX_TOKEN_LEN = 16_384
_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/=_.:|~-]+$")


DRIVE_JS = r"""
async ({pk, sdkUrl, timeoutMs}) => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const out = {token: "", cuid: "", error: null, diag: {sdk: false, injected: false, configured: false}};
  const readCuid = () => { try { return localStorage.getItem("__cuid") || ""; } catch (e) { return ""; } };
  const sdkReady = () => typeof window.Castle === "object" && window.Castle !== null
    && typeof window.Castle.configure === "function";
  out.diag.sdk = sdkReady();
  if (!out.diag.sdk && sdkUrl) {
    await new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = sdkUrl;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => resolve();
      (document.head || document.documentElement).appendChild(script);
    });
    out.diag.injected = true;
    out.diag.sdk = sdkReady();
  }
  out.cuid = readCuid();
  if (!out.diag.sdk) { out.error = "sdk-missing"; return out; }
  let instance = null;
  try {
    instance = window.Castle.configure({ pk });
    out.diag.configured = true;
  } catch (e) { out.error = "configure-failed: " + String(e); return out; }
  if (!instance || typeof instance.createRequestToken !== "function") {
    out.error = "create-unsupported";
    return out;
  }
  const step = 250;
  for (let waited = 0; waited < timeoutMs && !out.token; waited += step) {
    try {
      const token = await instance.createRequestToken();
      if (token) { out.token = String(token); break; }
    } catch (e) { out.error = "create-failed: " + String(e); }
    await sleep(step);
  }
  out.cuid = readCuid() || out.cuid;
  return out;
}
"""


async def drive(page: Any, *, pk: str, sdk_url: str, timeout_ms: int) -> dict[str, Any]:
    """Run :data:`DRIVE_JS` and return its report; the solver does the policy."""
    out = await page.evaluate(
        DRIVE_JS,
        {"pk": pk, "sdkUrl": sdk_url, "timeoutMs": max(int(timeout_ms), 500)},
    )
    if not isinstance(out, dict):
        raise SensorError(
            "castle: page script returned no result",
            context={"type": type(out).__name__},
        )
    return out


def validate_token(value: Any, *, pk: str) -> str:
    """Reject a placeholder before it reaches a client as a paid success."""
    token = value.strip() if isinstance(value, str) else ""
    if not token:
        raise UnsolvableError("castle: the SDK returned no request token", context={"pk": pk})
    if token == pk:
        raise UnsolvableError("castle: the page echoed the publishable key", context={"pk": pk})
    if len(token) < MIN_TOKEN_LEN:
        raise UnsolvableError("castle: token too short", context={"length": len(token)})
    if len(token) > _MAX_TOKEN_LEN:
        raise UnsolvableError("castle: token too long", context={"length": len(token)})
    if any(ch.isspace() for ch in token):
        raise UnsolvableError("castle: token contains whitespace", context={"length": len(token)})
    if not _TOKEN_RE.match(token):
        raise UnsolvableError("castle: unexpected token shape", context={"head": token[:16]})
    return token


def token_from_request(body: Any) -> str:
    """Pull `castle_request_token` out of a captured request body.

    Integrations put the token in a JSON body field; some put it in a form field.
    Both shapes reach here as one string, so both are tried and nothing else is
    invented from the body.
    """
    if not isinstance(body, str) or "castle_request_token" not in body:
        return ""
    import re as _re

    match = _re.search(r'"castle_request_token"\s*:\s*"([^"]+)"', body)
    if match:
        return match.group(1)
    for part in body.split("&"):
        name, sep, value = part.partition("=")
        if sep and name.strip() == "castle_request_token":
            return value.strip()
    return ""


def error_of(out: dict[str, Any]) -> str:
    return str(out.get("error") or "")


__all__ = ["DRIVE_JS", "MIN_TOKEN_LEN", "drive", "error_of", "token_from_request", "validate_token"]
