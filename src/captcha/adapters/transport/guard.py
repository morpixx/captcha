"""Outbound URL guard for the HTTP transport.

`Client` promises two things the solver layer must be able to trust: the
request never lands on the host's own network, and a public URL that redirects
into it is not followed. curl_cffi follows redirects internally, i.e. after the
socket is already open, so the only place to check a hop is between hops: this
module drives the redirect chain one request at a time and runs every `Location`
through `adapters.netguard`.

Checks run through `asyncio.to_thread` because `getaddrinfo` blocks; the
transport is shared by hundreds of tasks on one loop.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from captcha.adapters.netguard import Resolver, ensure_public_url_async, ensure_redirect_async

#: Statuses curl follows when `allow_redirects` is on.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: These turn a non-GET into a body-less GET, per RFC 9110 §15.4.
_METHOD_SWITCH = frozenset({301, 302, 303})

#: Caller-supplied credentials that must not ride a redirect to another host.
_CROSS_HOST_SECRET_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})


async def guarded_request(
    session: Any,
    method: str,
    url: str,
    *,
    kwargs: dict[str, Any],
    resolver: Resolver | None = None,
    max_redirects: int = 10,
    follow: bool = True,
) -> Any:
    """Send one request with every URL and redirect hop checked by netguard.

    Raises `UrlRejectedError` before the socket is opened for a rejected target;
    the exception is a `CaptchaError`, so the retry policy leaves it alone.
    """
    current = await ensure_public_url_async(url, resolver=resolver, field="url")
    request_kwargs = dict(kwargs)
    remaining = max_redirects

    while True:
        hop_kwargs = {**request_kwargs, "allow_redirects": False}
        raw = await session.request(method, current, **hop_kwargs)

        location = _location(raw)
        status = int(getattr(raw, "status_code", 0))
        if not follow or status not in REDIRECT_STATUSES or location is None:
            return raw
        if remaining <= 0:
            raise ConnectionError(f"redirect chain longer than {max_redirects} hops")
        remaining -= 1

        target = await ensure_redirect_async(location, base=current, resolver=resolver, field="redirect")
        if status in _METHOD_SWITCH and method.upper() not in {"GET", "HEAD"}:
            method = "GET"
            request_kwargs.pop("data", None)
            request_kwargs.pop("json", None)
        if _host(current) != _host(target):
            _drop_cross_host_secrets(request_kwargs)
        _carry_referer(request_kwargs, current)
        current = target


def _location(raw: Any) -> str | None:
    headers = getattr(raw, "headers", None)
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    for name in ("location", "Location"):
        value = getter(name)
        if value:
            return str(value)
    return None


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _carry_referer(kwargs: dict[str, Any], referer: str) -> None:
    """curl sets Referer on the hop it follows; manual hops have to do it here."""
    headers = kwargs.get("headers")
    if not isinstance(headers, dict):
        headers = {}
    if any(key.lower() == "referer" for key in headers):
        return
    kwargs["headers"] = {**headers, "Referer": referer}


def _drop_cross_host_secrets(kwargs: dict[str, Any]) -> None:
    kwargs.pop("cookies", None)
    headers = kwargs.get("headers")
    if not isinstance(headers, dict):
        return
    kept = {key: value for key, value in headers.items() if key.lower() not in _CROSS_HOST_SECRET_HEADERS}
    kwargs["headers"] = kept


__all__ = ["REDIRECT_STATUSES", "guarded_request"]
