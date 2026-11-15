"""Manual redirect following with a netguard check on every hop.

curl_cffi's `allow_redirects=True` follows a `Location` header to any host,
including loopback and cloud metadata. The transport therefore sends every
request with redirects off and walks the chain here: each hop is resolved
against the URL that produced it and passed through `ensure_redirect`, so a
public URL answering `302 Location: http://10.0.0.1/` raises
`UrlRejectedError` before the second request leaves the process.

`next_hop` does DNS for the hop's host, so the caller runs it in a thread.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from captcha.adapters.netguard import ensure_redirect
from captcha.domain.errors import CaptchaError

#: Status codes that carry a `Location` worth following.
REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})

#: Chain length cap. curl's own default is 30; ten is plenty for a captcha
#: page and keeps a hostile loop cheap.
DEFAULT_MAX_REDIRECTS = 10

#: Statuses where the method and body survive the hop (RFC 9110 §15.4).
_PRESERVES_METHOD = frozenset({307, 308})

#: Headers that must not travel to another origin. curl strips Authorization
#: on a cross-host redirect itself; following manually means doing the same.
CREDENTIAL_HEADERS: frozenset[str] = frozenset({"authorization", "cookie", "proxy-authorization"})

_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class Hop:
    """The request to send instead of the 3xx response that pointed here."""

    url: str
    method: str
    keep_body: bool
    status: int


def next_hop(
    *,
    status: int,
    location: str | None,
    base: str,
    method: str,
    hops: int,
    max_hops: int = DEFAULT_MAX_REDIRECTS,
    field: str = "redirect",
) -> Hop | None:
    """Return the request for the next hop, or None when this response is final.

    `base` is the URL that produced the response (a relative `Location` is
    resolved against it). Raises `UrlRejectedError` for a non-public target and
    `CaptchaError` when the chain exceeds `max_hops`.
    """
    if status not in REDIRECT_STATUSES or not location:
        return None
    if hops >= max_hops:
        raise CaptchaError(
            "too many redirects",
            context={"max_redirects": max_hops, "status": status},
        )
    target = ensure_redirect(location, base=base, field=field)
    next_method, keep_body = _method_after(status, method)
    return Hop(url=target, method=next_method, keep_body=keep_body, status=status)


def same_origin(first: str, second: str) -> bool:
    """True when both URLs share scheme, host and effective port."""
    return _origin(first) == _origin(second)


def _origin(url: str) -> tuple[str, str, int | None]:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ("", "", None)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return (scheme, (parts.hostname or "").lower(), port if port is not None else _DEFAULT_PORTS.get(scheme))


def without_credentials(headers: Mapping[str, str] | None) -> dict[str, str] | None:
    """Copy of `headers` without Authorization/Cookie/Proxy-Authorization."""
    if not headers:
        return headers
    return {key: value for key, value in headers.items() if key.lower() not in CREDENTIAL_HEADERS}


def _method_after(status: int, method: str) -> tuple[str, bool]:
    """What curl/browsers do: 307/308 keep the method and body, 301/302 turn
    POST into GET (other methods survive), 303 always becomes GET."""
    upper = method.upper()
    if upper in {"GET", "HEAD"} or status in _PRESERVES_METHOD:
        return upper, True
    if upper == "POST" or status == 303:
        return "GET", False
    return upper, True


__all__ = [
    "CREDENTIAL_HEADERS",
    "DEFAULT_MAX_REDIRECTS",
    "REDIRECT_STATUSES",
    "Hop",
    "next_hop",
    "same_origin",
    "without_credentials",
]
