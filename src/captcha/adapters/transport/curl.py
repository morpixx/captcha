"""Session-per-task async HTTP client over curl_cffi.

One Client == one curl_cffi.requests.AsyncSession. Do NOT share across tasks:
the session holds a connection pool, TLS state, and cookies; sharing couples
unrelated solver runs and gives antibot stacks a stable fingerprint to pin.

Use via `async with Client(...) as c: await c.get(url)`. Safe at 1000+ parallel
tasks as long as each task owns its own Client."""

from __future__ import annotations

import time
from types import TracebackType
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from captcha.domain.errors import ChallengeExpiredError, ProxyError

from .guard import guarded_request
from .ja_profiles import pick_impersonate
from .response import Response
from .retry import build_retry_policy

if TYPE_CHECKING:
    from captcha.adapters.netguard import Resolver

try:
    from curl_cffi.requests import AsyncSession  # type: ignore[import-not-found]
    from curl_cffi.requests.exceptions import (  # type: ignore[import-not-found]
        ConnectionError as _CurlConnectionError,
    )
    from curl_cffi.requests.exceptions import (
        ProxyError as _CurlProxyError,
    )
    from curl_cffi.requests.exceptions import (
        RequestException as _CurlRequestException,
    )
    from curl_cffi.requests.exceptions import (
        Timeout as _CurlTimeout,
    )
except ImportError:  # pragma: no cover — lets the module import on a dev box without curl_cffi built
    AsyncSession = None  # type: ignore[assignment,misc]
    _CurlConnectionError = ConnectionError  # type: ignore[assignment,misc]
    _CurlProxyError = ConnectionError  # type: ignore[assignment,misc]
    _CurlRequestException = Exception  # type: ignore[assignment,misc]
    _CurlTimeout = TimeoutError  # type: ignore[assignment,misc]


_log = structlog.get_logger(__name__)

# Antibot verdicts. The client never retries these — the proxy/fingerprint
# needs to change before another attempt has any chance.
_CHALLENGE_STATUSES: frozenset[int] = frozenset({403, 429, 451})


class Client:
    """Async HTTP client, session-per-task.

    Params:
      impersonate: curl_cffi impersonate string (e.g. "chrome124"). If the
        caller passes a loose hint, run it through pick_impersonate first.
      proxy: full proxy URL — scheme://[user:pass@]host:port. Supports http,
        https, socks5, socks5h. None = direct.
      timeout: per-request timeout in seconds. Covers the whole exchange,
        not just the connect.
      http2: enable HTTP/2. curl-impersonate negotiates ALPN per profile; this
        flag is mostly a kill-switch for debugging HTTP/1.1 edge cases.
      verify: TLS verification. Leave True unless you're pinning a MITM proxy.
      retry_attempts: transport retries (not antibot retries — those bubble).
      allow_private: test-only escape hatch. When True the netguard check is
        skipped and curl follows redirects itself; never set it in a worker.
      resolver: DNS resolver for the netguard check. None = the system resolver;
        tests inject a stub so the suite never touches DNS.
      max_redirects: redirect hops followed before a ConnectionError.
    """

    def __init__(
        self,
        *,
        impersonate: str = "chrome124",
        proxy: str | None = None,
        timeout: float = 30.0,
        http2: bool = True,
        verify: bool = True,
        retry_attempts: int = 3,
        default_headers: dict[str, str] | None = None,
        allow_private: bool = False,
        resolver: Resolver | None = None,
        max_redirects: int = 10,
    ) -> None:
        if AsyncSession is None:
            raise RuntimeError(
                "curl_cffi is not importable. `uv sync` the workspace or `pip install curl_cffi>=0.7.0`."
            )

        self.impersonate = impersonate or pick_impersonate("chrome")
        self.proxy = _normalize_proxy(proxy) if proxy else None
        self.timeout = timeout
        self.http2 = http2
        self.verify = verify
        self.retry_attempts = retry_attempts
        self.default_headers = dict(default_headers or {})
        self.allow_private = allow_private
        self.resolver = resolver
        self.max_redirects = max_redirects

        self._session: AsyncSession | None = None
        self._closed = False

    # --- lifecycle --------------------------------------------------------

    async def __aenter__(self) -> Client:
        self._ensure_session()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _ensure_session(self) -> AsyncSession:
        if self._closed:
            raise RuntimeError("Client is closed; construct a fresh one per task.")
        if self._session is None:
            kwargs: dict[str, Any] = {
                "impersonate": self.impersonate,
                "timeout": self.timeout,
                "verify": self.verify,
                "http_version": 2 if self.http2 else 1,
            }
            if self.proxy:
                kwargs["proxy"] = self.proxy
            if self.default_headers:
                kwargs["headers"] = self.default_headers
            try:
                self._session = AsyncSession(**kwargs)
            except TypeError:
                # curl_cffi <0.7 doesn't take http_version — fall back silently.
                kwargs.pop("http_version", None)
                self._session = AsyncSession(**kwargs)
        return self._session

    async def aclose(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception as exc:
                _log.warning("client.close_failed", error=str(exc))
            finally:
                self._session = None
        self._closed = True

    # --- verbs ------------------------------------------------------------

    async def get(self, url: str, **kwargs: Any) -> Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> Response:
        return await self.request("POST", url, **kwargs)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        data: Any = None,
        json: Any = None,
        cookies: dict[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        impersonate: str | None = None,
    ) -> Response:
        """Send one request. Transport flakes retry per build_retry_policy;
        antibot statuses raise ChallengeExpiredError without retry."""
        session = self._ensure_session()
        policy = build_retry_policy(attempts=self.retry_attempts)

        call_kwargs: dict[str, Any] = {
            "headers": headers,
            "params": params,
            "data": data,
            "json": json,
            "cookies": cookies,
            "timeout": timeout if timeout is not None else self.timeout,
            "allow_redirects": allow_redirects,
            "impersonate": impersonate or self.impersonate,
        }
        # drop Nones so curl_cffi uses its own defaults
        call_kwargs = {k: v for k, v in call_kwargs.items() if v is not None}

        started = time.monotonic()

        async for attempt in policy:
            with attempt:
                try:
                    if self.allow_private:
                        raw = await session.request(method, url, **call_kwargs)
                    else:
                        # Every hop goes through netguard; curl's own redirect
                        # following would open the socket before we could look
                        # at the Location (SSRF through a public redirector).
                        raw = await guarded_request(
                            session,
                            method,
                            url,
                            kwargs=call_kwargs,
                            resolver=self.resolver,
                            max_redirects=self.max_redirects,
                            follow=allow_redirects,
                        )
                except _CurlProxyError as exc:
                    raise ProxyError(
                        f"proxy tunnel failed: {exc}",
                        context={"proxy": _redact_proxy(self.proxy), "url": url},
                    ) from exc
                except _CurlTimeout as exc:
                    # tenacity catches TimeoutError — re-raise as builtin so it retries
                    raise TimeoutError(str(exc)) from exc
                except _CurlConnectionError as exc:
                    raise ConnectionError(str(exc)) from exc
                except _CurlRequestException as exc:
                    # unknown curl-layer failure — surface as transport so it retries
                    raise ConnectionError(str(exc)) from exc

        elapsed_ms = (time.monotonic() - started) * 1000.0
        resp = _wrap(raw, elapsed_ms=elapsed_ms, ja3_hint=self.impersonate)

        if resp.status in _CHALLENGE_STATUSES:
            raise ChallengeExpiredError(
                f"antibot verdict: HTTP {resp.status}",
                context={
                    "status": resp.status,
                    "url": resp.final_url,
                    "method": method,
                    "ja3_hint": self.impersonate,
                    "server": resp.header("server") or "",
                    "cf_ray": resp.header("cf-ray") or "",
                },
            )
        return resp


# ----- helpers ------------------------------------------------------------


def _normalize_proxy(proxy: str) -> str:
    """Accept bare host:port by assuming http://; reject nonsense early."""
    s = proxy.strip()
    if "://" not in s:
        s = "http://" + s
    parsed = urlparse(s)
    if not parsed.hostname or parsed.port is None:
        raise ProxyError(
            "proxy URL must be scheme://[user:pass@]host:port",
            context={"proxy": _redact_proxy(proxy)},
        )
    if parsed.scheme not in {"http", "https", "socks5", "socks5h", "socks4", "socks4a"}:
        raise ProxyError(
            f"unsupported proxy scheme: {parsed.scheme}",
            context={"proxy": _redact_proxy(proxy)},
        )
    return s


def _redact_proxy(proxy: str | None) -> str:
    if not proxy:
        return ""
    try:
        parsed = urlparse(proxy if "://" in proxy else "http://" + proxy)
    except ValueError:
        return "***"
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username:
        netloc = f"***:***@{netloc}"
    return f"{parsed.scheme}://{netloc}" if parsed.scheme else netloc


def _wrap(raw: Any, *, elapsed_ms: float, ja3_hint: str) -> Response:
    """Turn a curl_cffi Response into our msgspec.Response.

    curl_cffi exposes .status_code, .headers (CaseInsensitiveDict with
    .items()), .content (bytes), .url. We flatten headers to a list of pairs
    so repeated Set-Cookie survives."""
    headers: list[tuple[str, str]] = []
    raw_headers = getattr(raw, "headers", None)
    if raw_headers is not None:
        # curl_cffi >=0.7 has multi_items(); fall back to items() otherwise
        items_fn = getattr(raw_headers, "multi_items", None) or raw_headers.items
        for k, v in items_fn():
            headers.append((str(k), str(v)))

    body = getattr(raw, "content", b"") or b""
    return Response(
        status=int(getattr(raw, "status_code", 0)),
        headers=headers,
        body=bytes(body),
        elapsed_ms=elapsed_ms,
        final_url=str(getattr(raw, "url", "")),
        ja3_hint=ja3_hint,
    )
