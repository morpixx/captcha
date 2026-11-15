"""Exit-IP resolution through the task proxy.

Camoufox aligns timezone, locale and geolocation with the IP passed as
`geoip=`. That IP has to be the proxy exit, not the host: probing it here means
a dead proxy fails before a browser starts (the provider turns a `None` into
`ProxyError`), and no task silently lands on the datacenter's own location.

Sticky proxies keep one exit IP for minutes, so the answer is cached per proxy
credential fingerprint. The IP itself never goes into a log line by this
module.
"""

from __future__ import annotations

import ipaddress
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from captcha.adapters.browser.proxy import proxy_fingerprint, proxy_url

IP_SERVICES: tuple[str, ...] = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
)

DEFAULT_TTL_SEC = 120.0
MAX_PROBE_BYTES = 64
MAX_SINGLE_PROBE_SEC = 4.0

# service url -> probe budget in seconds -> body (or None)
Fetch = Callable[[str, float], str | None]


@dataclass
class GeoIPCache:
    """TTL cache keyed by proxy fingerprint. `ttl_sec <= 0` disables it."""

    ttl_sec: float = DEFAULT_TTL_SEC
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, tuple[float, str]] = field(default_factory=dict, repr=False)

    def get(self, key: str) -> str | None:
        if self.ttl_sec <= 0 or not key:
            return None
        entry = self._entries.get(key)
        if entry is None:
            return None
        stamp, ip = entry
        if self.clock() - stamp >= self.ttl_sec:
            self._entries.pop(key, None)
            return None
        return ip

    def put(self, key: str, ip: str) -> None:
        if self.ttl_sec > 0 and key:
            self._entries[key] = (self.clock(), ip)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


def resolve_exit_ip(
    proxy: dict[str, str] | None,
    *,
    cache: GeoIPCache | None = None,
    timeout: float = 10.0,
    fetch: Fetch | None = None,
) -> str | None:
    """Return the IP the proxy exits from, or `None` when it cannot be resolved.

    The whole probe is bounded by `timeout`, not every attempt: a dead proxy
    used to cost four HTTPS round trips before the job failed.
    """
    key = proxy_fingerprint(proxy)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached

    credential_url = proxy_url(proxy) if proxy else None
    probe = fetch or (lambda service, budget: _fetch_text(service, budget, credential_url))
    deadline = time.monotonic() + max(1.0, timeout)
    for service in IP_SERVICES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            answer = probe(service, min(MAX_SINGLE_PROBE_SEC, remaining))
        except Exception:  # an injected probe must never take the worker down
            continue
        candidate = _as_address(answer)
        if candidate is None:
            continue
        if cache is not None:
            cache.put(key, candidate)
        return candidate
    return None


def _as_address(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return str(ipaddress.ip_address(text.strip()))
    except ValueError:
        return None


def _fetch_text(service: str, timeout: float, proxy: str | None) -> str | None:
    """One HTTPS GET, through the proxy when there is one.

    `ProxyHandler({})` disables `HTTP_PROXY`/`HTTPS_PROXY` from the host env —
    the exit IP must never be decided by the machine the worker runs on.
    """
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    ]
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(service, headers={"User-Agent": "captcha-browser-core/1"})
    try:
        with opener.open(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            return response.read(MAX_PROBE_BYTES).decode("ascii", "replace")
    except (OSError, ValueError):
        return None


__all__ = ["DEFAULT_TTL_SEC", "IP_SERVICES", "GeoIPCache", "resolve_exit_ip"]
