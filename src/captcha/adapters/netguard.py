"""SSRF guard for URLs supplied by clients (production contract §8).

Task payloads carry URLs (`pageurl`, `websiteURL`, `challenge_url`, `surl`,
`api_server`) that a solver eventually fetches. Before that happens the URL is
checked here: scheme, length, control characters, then every A/AAAA address the
host resolves to. Any private, loopback, link-local, ULA, multicast, reserved
or metadata address rejects the URL, and the same check applies to every
redirect hop (`ensure_redirect`).

Two entry points: `ensure_public_url` (shape + every resolved address, the
fetch-time check) and `ensure_public_url_shape` (shape + IP literals only, no
DNS, for hot paths that must not block the event loop). The resolver is
injectable, so the tests never touch DNS. Error context carries the host and the
offending address, never the full URL: a `pageurl` can hold a token.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

from captcha.domain.errors import CaptchaError

try:  # router-escalation owns domain/errors.py; the class lands there.
    from captcha.domain.errors import UrlRejectedError
except ImportError:  # pragma: no cover - used until ERR_URL_REJECTED is merged

    class UrlRejectedError(CaptchaError):  # type: ignore[no-redef]
        """URL is not fetchable (SSRF/DNS/scheme)."""

        code = "ERR_URL_REJECTED"


MAX_URL_LENGTH = 2048
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Names that never need a DNS answer to be known-bad.
DENIED_HOSTNAMES = frozenset({"localhost", "metadata.google.internal"})

# host -> resolved addresses (textual). Injectable for tests.
Resolver = Callable[[str], tuple[str, ...]]


def ensure_public_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    field: str | None = None,
) -> str:
    """Validate `url` for outbound fetching and return it unchanged.

    Raises `UrlRejectedError` (`ERR_URL_REJECTED`) on anything that could reach
    the host's own network: wrong scheme, oversize, non-public address, or a
    host that does not resolve.
    """
    candidate, host, literal = _shape_check(url, field=field)
    if literal is not None:
        _reject_private(literal, host=host, field=field)
        return candidate

    addresses = _resolve(host, resolver=resolver, field=field)
    if not addresses:
        raise UrlRejectedError(
            "host did not resolve",
            context={"field": field, "host": host},
        )
    for address in addresses:
        parsed = _as_address(address)
        if parsed is None:
            raise UrlRejectedError(
                "resolver returned a non-address",
                context={"field": field, "host": host},
            )
        _reject_private(parsed, host=host, field=field)
    return candidate


def ensure_public_url_shape(url: str, *, field: str | None = None) -> str:
    """Every check `ensure_public_url` makes except DNS resolution.

    For hot paths that must not block on a resolver (the gateway create path,
    the pingback sender) and for test suites that must not touch the network.
    Shape, scheme, port, denied hostnames and literal private/metadata addresses
    are still rejected; a *hostname* is taken at face value, so the caller is
    responsible for the resolved-address check at fetch time. `UrlRejectedError`
    carries field/host/address, never the URL.
    """
    candidate, host, literal = _shape_check(url, field=field)
    if literal is not None:
        _reject_private(literal, host=host, field=field)
    return candidate


def ensure_redirect(location: str, *, base: str, resolver: Resolver | None = None, field: str | None = None) -> str:
    """Validate one redirect hop: resolve `location` against `base`, then check it.

    Call this for every `Location` header before following it; a public URL
    that redirects to `http://10.0.0.1/` is exactly the hole this closes.
    """
    return ensure_public_url(urljoin(base, location), resolver=resolver, field=field)


def is_public_ip(address: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True when `address` is globally routable and not a metadata endpoint."""
    parsed = _as_address(address) if isinstance(address, str) else address
    if parsed is None:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return is_public_ip(parsed.ipv4_mapped)
    if (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    ):
        return False
    # CGNAT (100.64.0.0/10) and the documentation nets carry none of the flags
    # above on recent Pythons; `is_global` is the reliable last word.
    return bool(parsed.is_global)


def _shape_check(
    url: str,
    *,
    field: str | None,
) -> tuple[str, str, ipaddress.IPv4Address | ipaddress.IPv6Address | None]:
    """Scheme/length/control/port/denied-name checks shared by both entry points.

    Returns (candidate, host, literal) where `literal` is the parsed address when
    the host is an IP literal. No DNS is touched, so this is safe on the event
    loop and in offline tests.
    """
    if not isinstance(url, str):
        raise UrlRejectedError("url must be a string", context={"field": field})
    candidate = url.strip()
    if not candidate:
        raise UrlRejectedError("url is empty", context={"field": field})
    if len(candidate) > MAX_URL_LENGTH:
        raise UrlRejectedError(
            f"url longer than {MAX_URL_LENGTH} characters",
            context={"field": field, "length": len(candidate)},
        )
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in candidate):
        raise UrlRejectedError(
            "url contains control characters",
            context={"field": field},
        )

    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UrlRejectedError(
            "url scheme is not http(s)",
            context={"field": field, "scheme": scheme},
        )
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UrlRejectedError("url has no host", context={"field": field})
    try:
        _ = parts.port  # raises ValueError on a malformed/out-of-range port
    except ValueError as exc:
        raise UrlRejectedError(
            "url port is invalid",
            context={"field": field, "host": host},
        ) from exc
    if host in DENIED_HOSTNAMES or host.endswith(".localhost"):
        raise UrlRejectedError(
            "host is not allowed",
            context={"field": field, "host": host},
        )
    return candidate, host, _as_address(host)


def _reject_private(address: ipaddress.IPv4Address | ipaddress.IPv6Address, *, host: str, field: str | None) -> None:
    if is_public_ip(address):
        return
    raise UrlRejectedError(
        "host resolves to a non-public address",
        context={"field": field, "host": host, "address": str(address)},
    )


def _resolve(host: str, *, resolver: Resolver | None, field: str | None) -> tuple[str, ...]:
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UrlRejectedError(
            "host is not a valid IDN",
            context={"field": field, "host": host},
        ) from exc
    try:
        found = (resolver or default_resolver)(ascii_host)
    except OSError as exc:
        raise UrlRejectedError(
            "host did not resolve",
            context={"field": field, "host": host, "error": type(exc).__name__},
        ) from exc
    return tuple(dict.fromkeys(found))


def default_resolver(host: str) -> tuple[str, ...]:
    """A and AAAA records for `host`, as text. AF_UNSPEC asks for both."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(info[4][0]) for info in infos))


def _as_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


__all__ = [
    "ALLOWED_SCHEMES",
    "MAX_URL_LENGTH",
    "UrlRejectedError",
    "default_resolver",
    "ensure_public_url",
    "ensure_public_url_shape",
    "ensure_redirect",
    "is_public_ip",
]
