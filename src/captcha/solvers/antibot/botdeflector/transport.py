"""Request glue for the BotDeflector challenge host.

Headers, the host's verdict and its Set-Cookie jar. Nothing here knows what a
JWT means -- that is wire.py -- and nothing here imports the transport adapter;
a response is duck-typed on status/body/headers_all.

Header set and option order come from the 2026-10 browser capture: a JSON
content type even on GET, origin/referer of the page, sec-fetch-* hints, and the
flow token as x-tt-flow-token on the two /pow calls. /verify-flow swaps in
x-tt-ch-flow-token, the comma-joined x-tt-solution-tokens and the 32-hex
x-tt-client-fingerprint. /pow/verify carries no token header at all: both JWTs
ride in the body.

FILL: whether the host scores sec-fetch-* or Accept-Encoding. The capture has
them; replay sends the same values and lets the transport pick the encoding.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from captcha.domain.errors import ChallengeExpiredError, SensorError

#: Engine error code for a spent flow token. Only P033 is documented in public
#: captures; the I0xx family belongs to /icon/verify, which the HTTP lane skips.
FLOW_TOKEN_EXPIRED_CODE: str = "P033"

_ERROR_CODE_RE = re.compile(r"\b[PI]\d{3}\b")


def api_headers(
    flow_token: str,
    *,
    origin: str = "",
    referer: str = "",
    user_agent: str = "",
    same_site: bool = True,
) -> dict[str, str]:
    """Headers on /pow/get and /pow/verify; only /pow/get carries the flow token."""
    headers = _browser_headers(origin=origin, referer=referer, user_agent=user_agent, same_site=same_site)
    if flow_token:
        headers["x-tt-flow-token"] = flow_token
    return headers


def verify_flow_headers(
    flow_token: str,
    step_tokens: list[str],
    fingerprint: str,
    *,
    origin: str = "",
    referer: str = "",
    user_agent: str = "",
    same_site: bool = True,
) -> dict[str, str]:
    """Headers on /verify-flow: flow token, comma-joined step tokens, visitor id."""
    headers = _browser_headers(origin=origin, referer=referer, user_agent=user_agent, same_site=same_site)
    headers["x-tt-ch-flow-token"] = flow_token
    headers["x-tt-solution-tokens"] = ",".join(step_tokens)
    headers["x-tt-client-fingerprint"] = fingerprint
    return headers


def origin(url: str) -> str:
    """Scheme and host of a page URL; empty when the caller sent none."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


def same_site(page_url: str, api_base: str) -> bool:
    """Whether the widget's page and the challenge host share a registrable domain.

    Only decides the sec-fetch-site hint; a wrong value breaks nothing for a
    plain HTTP client, but the capture carries it and replay should look alike.
    """
    page_host = (urlparse(page_url).hostname or "").lower()
    api_host = (urlparse(api_base).hostname or "").lower()
    if not page_host or not api_host:
        return True
    return _registrable(page_host) == _registrable(api_host)


def check_status(resp: Any, *, what: str) -> None:
    """Map the challenge host's verdict onto our errors.

    4xx means the challenge or token is spent or was rejected (single use, so a
    fresh flow token is the retry); 5xx and anything else is the host failing.
    """
    status = int(getattr(resp, "status", 0) or 0)
    if not status or 200 <= status < 300:
        return
    text = _body_text(resp)
    code = _error_code(text)
    context: dict[str, Any] = {"status": status}
    if code:
        context["code"] = code
    if status in (400, 401, 403, 409, 410, 422):
        hint = " (flow token expired or already used)" if code == FLOW_TOKEN_EXPIRED_CODE else ""
        raise ChallengeExpiredError(f"botdeflector {what} rejected the challenge{hint}", context=context)
    raise SensorError(f"botdeflector {what} failed", context=context)


def set_cookie(resp: Any) -> list[str]:
    """All Set-Cookie values of a response, without importing the transport."""
    getter = getattr(resp, "headers_all", None)
    if callable(getter):
        return list(getter("set-cookie"))
    header = getattr(resp, "header", None)
    value = header("set-cookie") if callable(header) else None
    return [value] if value else []


def _browser_headers(*, origin: str, referer: str, user_agent: str, same_site: bool) -> dict[str, str]:
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site" if same_site else "cross-site",
    }
    if origin:
        headers["origin"] = origin
    if referer:
        headers["referer"] = referer
    if user_agent:
        headers["user-agent"] = user_agent
    return headers


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _error_code(text: str) -> str:
    match = _ERROR_CODE_RE.search(text or "")
    return match.group(0) if match else ""


def _body_text(raw: Any) -> str:
    """Best-effort error text; a response we cannot read is not an error subject."""
    data = _body(raw)
    if isinstance(data, dict):
        return " ".join(str(value) for value in data.values())
    return str(data)[:400]


def _body(raw: Any) -> Any:
    """The response body as text/dict, without importing the transport type."""
    if isinstance(raw, (str, dict)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "replace")
    body = getattr(raw, "body", None)
    if body is None:
        return ""
    return bytes(body).decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else body
