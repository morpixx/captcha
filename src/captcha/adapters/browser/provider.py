"""Camoufox provider: one fresh browser per task.

`CamoufoxProvider.session(spec)` is the browser-lane contract from
`docs/production-contract.md` §4: an async context manager, one new Camoufox
launch plus one temporary profile per task, GeoIP from the proxy exit IP, no
host fallback for a proxied task, no shared context between clients.

The launcher is injectable. Tests pass a fake async context manager and assert
on the options dict, so `camoufox` does not have to be installed to run the
suite — and is not imported at module level here.

Every page handed out by `CamoufoxSession.page()` gets a `page.route("**/*")`
hook: the main document, each redirect hop and every subresource is checked by
`netguard.ensure_public_url` before the socket opens, and a private or metadata
target is aborted. DNS rebinding between that check and connect is still open
(RT-05 residual, closed only by an egress policy).

The fingerprint (UA, screen, WebGL, timezone, locale) is Camoufox's job, driven
by the exit IP. `BrowserSpec.user_agent` / `locale` / `timezone` are therefore
accepted but ignored: overriding one of them at the context level produces a
fingerprint that disagrees with itself, which is worse than a generic one.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

from captcha.adapters import netguard
from captcha.adapters.browser.geoip import GeoIPCache, resolve_exit_ip
from captcha.adapters.browser.prefs import build_options
from captcha.adapters.browser.proxy import parse_proxy
from captcha.adapters.browser.supervisor import PROFILE_PREFIX, Supervisor, remove_profile
from captcha.adapters.netguard import ALLOWED_SCHEMES, UrlRejectedError
from captcha.domain.errors import CaptchaError, ConfigError, ProxyError
from captcha.domain.errors import TimeoutError as CaptchaTimeout
from captcha.domain.ports import BrowserSpec
from captcha.domain.redaction import safe_url

log = structlog.get_logger(__name__)

# options dict -> async context manager yielding a persistent BrowserContext
Launcher = Callable[[dict[str, Any]], AbstractAsyncContextManager[Any]]
# proxy dict -> exit IP (None when unreachable). Sync: called in a worker thread.
ExitIPResolver = Callable[[dict[str, str] | None], str | None]

#: Schemes that never leave the browser process, so netguard's http(s)-only
#: rule would reject them for no security gain: `about:blank` is every popup's
#: first document, `data:`/`blob:` are inline images. Everything else that is
#: not http(s) (`file:`, `ftp:`, `chrome:`, `ws:`) is blocked.
_NETWORK_FREE_SCHEMES = frozenset({"about", "data", "blob"})

#: Playwright abort code; the page sees a failed request, the socket is never
#: opened.
_BLOCKED_ERROR_CODE = "blockedbyclient"

#: Called when the guard rejects a request: (error, is_main_navigation).
BlockCallback = Callable[[UrlRejectedError, bool], None]


async def install_route_guard(
    page: Any,
    *,
    job_id: str = "",
    on_block: BlockCallback | None = None,
) -> bool:
    """Route every request of `page` through netguard; False if it has no router.

    `page.route("**/*")` fires for the main document, each redirect hop and
    every subresource (image, script, XHR, iframe). A page that points an
    `<img>` at `http://169.254.169.254/` is stopped before the socket opens,
    which `goto`-time checks cannot do. `ensure_public_url` (shape + DNS) runs
    in a worker thread, and a host that does not resolve counts as rejected:
    fail closed.

    A page object without `route` is a test double, not a Playwright page, so
    routing is a no-op there rather than a crash. Real pages always expose it.
    """
    route = getattr(page, "route", None)
    if not callable(route):
        log.debug("browser.route_guard.unavailable", job_id=job_id)
        return False
    main_frame = getattr(page, "main_frame", None)
    await route("**/*", _make_route_handler(job_id=job_id, main_frame=main_frame, on_block=on_block))
    return True


def _make_route_handler(
    *,
    job_id: str,
    main_frame: Any,
    on_block: BlockCallback | None,
) -> Callable[[Any], Any]:
    async def handler(route: Any) -> None:
        request = getattr(route, "request", None)
        url = str(getattr(request, "url", "") or "")
        scheme = _scheme(url)
        if scheme in _NETWORK_FREE_SCHEMES:
            await route.continue_()
            return
        navigation = _is_main_navigation(request, main_frame)
        if scheme not in ALLOWED_SCHEMES:
            await _block(
                route,
                UrlRejectedError(
                    "url scheme is not http(s)",
                    context={"field": "page_url", "scheme": scheme},
                ),
                job_id=job_id,
                navigation=navigation,
                on_block=on_block,
            )
            return
        try:
            await asyncio.to_thread(netguard.ensure_public_url, url, field="page_url")
        except UrlRejectedError as exc:
            await _block(route, exc, job_id=job_id, navigation=navigation, on_block=on_block)
            return
        await route.continue_()

    return handler


async def _block(
    route: Any,
    exc: UrlRejectedError,
    *,
    job_id: str,
    navigation: bool,
    on_block: BlockCallback | None,
) -> None:
    """Log host-only context, tell the session, abort the request."""
    log.warning(
        "browser.request_blocked",
        job_id=job_id,
        host=exc.context.get("host"),
        address=exc.context.get("address"),
        scheme=exc.context.get("scheme"),
        navigation=navigation,
        error=exc.message,
    )
    if on_block is not None:
        on_block(exc, navigation)
    try:
        await route.abort(_BLOCKED_ERROR_CODE)
    except Exception:
        # Either the page is closing or this build ignores error codes; one
        # bare retry is the last thing available, and it must not leak out of
        # the handler.
        with contextlib.suppress(Exception):
            await route.abort()


def _scheme(url: str) -> str:
    try:
        return urlsplit(url).scheme.lower()
    except ValueError:
        return ""


def _is_main_navigation(request: Any, main_frame: Any) -> bool:
    """True when this request is the top document (not an iframe, not a subresource)."""
    probe = getattr(request, "is_navigation_request", None)
    try:
        navigation = bool(probe()) if callable(probe) else str(getattr(request, "resource_type", "") or "") == "document"
    except Exception:
        navigation = False
    if not navigation:
        return False
    frame = getattr(request, "frame", None)
    return main_frame is None or frame is None or frame is main_frame


class CamoufoxProvider:
    """Browser session provider for the `browser` lane.

    Costs ~0.5–1.1 GB RSS and 1–3 s launch per task; that is the price of one
    clean fingerprint per task and it is what `WORKER_BROWSER_SESSIONS` caps.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        humanize: bool = True,
        geoip_ttl_sec: float = 120.0,
        supervisor: Supervisor | None = None,
        launcher: Launcher | None = None,
        exit_ip: ExitIPResolver | None = None,
        temp_root: str | Path | None = None,
        profile_prefix: str = PROFILE_PREFIX,
    ) -> None:
        self._headless = headless
        self._humanize = humanize
        self._temp_root = Path(temp_root) if temp_root is not None else None
        self._profile_prefix = profile_prefix
        self._supervisor = supervisor or Supervisor(temp_root=self._temp_root, prefix=profile_prefix)
        self._launcher: Launcher = launcher or _camoufox_launcher
        self._geoip = GeoIPCache(ttl_sec=geoip_ttl_sec)
        self._resolve: ExitIPResolver = exit_ip or (lambda proxy: resolve_exit_ip(proxy, cache=self._geoip))
        self._closed = False
        self._reaped = False

    # ---- lifecycle -----------------------------------------------------

    @property
    def supervisor(self) -> Supervisor:
        return self._supervisor

    async def __aenter__(self) -> CamoufoxProvider:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Kill every live job and reap leftover profiles. Idempotent."""
        if self._closed:
            return
        self._closed = True
        killed = await asyncio.to_thread(self._supervisor.kill_all)
        removed = await asyncio.to_thread(self._supervisor.reap_stale)
        log.info("browser.provider.closed", killed=killed, profiles_removed=len(removed))

    # ---- sessions ------------------------------------------------------

    @asynccontextmanager
    async def session(self, spec: BrowserSpec) -> AsyncIterator[CamoufoxSession]:
        """Launch one fresh browser for one task; everything dies on exit."""
        if self._closed:
            raise ConfigError("browser provider is closed")
        await self._reap_once()

        job_id = uuid.uuid4().hex
        proxy = parse_proxy(spec.proxy) if spec.proxy else None
        geoip: bool | str = True
        if proxy is not None:
            exit_ip = await asyncio.to_thread(self._resolve, proxy)
            if not exit_ip:
                raise ProxyError(
                    "could not resolve the proxy exit IP for GeoIP; refusing the host fallback",
                    context={"job_id": job_id},
                )
            geoip = exit_ip

        profile = Path(tempfile.mkdtemp(prefix=self._profile_prefix, dir=self._dir_arg()))
        self._supervisor.register(job_id, profile)
        options = build_options(
            headless=self._headless,
            proxy=proxy,
            geoip=geoip,
            user_data_dir=str(profile),
            humanize=self._humanize,
        )
        context_cm = self._launcher(options)
        session: CamoufoxSession | None = None
        try:
            try:
                context = await context_cm.__aenter__()
            except CaptchaError:
                raise
            except Exception as exc:
                raise CaptchaError(
                    "camoufox launch failed",
                    context={"job_id": job_id, "error": str(exc)},
                ) from exc
            session = CamoufoxSession(context=context, spec=spec, job_id=job_id)
            if spec.cookies:
                await session.load_cookies(spec.cookies)
            log.debug("browser.session.start", job_id=job_id, label=spec.label, geoip=bool(proxy))
            yield session
        finally:
            if session is not None:
                with contextlib.suppress(Exception):
                    await session.close()
            with contextlib.suppress(Exception):
                await context_cm.__aexit__(None, None, None)
            # Close() can return while the browser tree is still dying; a leaked
            # content process keeps the profile locked, so kill before removing.
            await asyncio.to_thread(self._supervisor.kill, job_id)
            self._supervisor.unregister(job_id)
            await asyncio.to_thread(remove_profile, profile)
            log.debug("browser.session.end", job_id=job_id, label=spec.label)

    async def _reap_once(self) -> None:
        if self._reaped:
            return
        self._reaped = True
        removed = await asyncio.to_thread(self._supervisor.reap_stale)
        if removed:
            log.info("browser.reaped_stale", profiles=len(removed))

    def _dir_arg(self) -> str | None:
        return str(self._temp_root) if self._temp_root is not None else None


class CamoufoxSession:
    """One live Camoufox persistent context, owned by exactly one task."""

    def __init__(self, *, context: Any, spec: BrowserSpec, job_id: str) -> None:
        self._context = context
        self.spec = spec
        self.job_id = job_id
        self._page: Any = None
        self._closed = False
        self._guarded_pages: set[int] = set()
        self._last_rejection: UrlRejectedError | None = None

    @property
    def context(self) -> Any:
        return self._context

    async def page(self) -> Any:
        """Primary page: the persistent context's first tab, or a new one.

        The page gets the netguard route hook before the caller can navigate it.
        """
        if self._page is None or _page_closed(self._page):
            pages = list(getattr(self._context, "pages", ()) or ())
            self._page = pages[0] if pages else await self._context.new_page()
        await self._guard_page(self._page)
        return self._page

    async def _guard_page(self, page: Any) -> None:
        if id(page) in self._guarded_pages:
            return
        if await install_route_guard(page, job_id=self.job_id, on_block=self._on_blocked):
            self._guarded_pages.add(id(page))

    def _on_blocked(self, exc: UrlRejectedError, navigation: bool) -> None:
        # Only the top document decides the task's error; a blocked image is
        # logged but must not turn a later timeout into ERR_URL_REJECTED.
        if navigation:
            self._last_rejection = exc

    async def goto(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 60_000,
    ) -> Any:
        netguard.ensure_public_url(url, field="websiteURL")
        page = await self.page()
        self._last_rejection = None
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        except Exception as exc:
            rejection = self._last_rejection
            self._last_rejection = None
            if rejection is not None:
                # A redirect hop or the document itself was blocked by the
                # route guard; that is terminal, not a retryable page failure.
                raise rejection from exc
            if "timeout" in str(exc).lower():
                raise CaptchaTimeout(
                    "page navigation timed out",
                    context={"url": safe_url(url), "timeout_ms": timeout_ms},
                ) from exc
            raise CaptchaError("page navigation failed", context={"url": safe_url(url)}) from exc
        final_url = getattr(page, "url", None)
        if final_url:
            netguard.ensure_public_url(str(final_url), field="final_url")
        return page

    async def load_cookies(self, cookies: list[dict[str, Any]]) -> None:
        try:
            await self._context.add_cookies([dict(cookie) for cookie in cookies])
        except Exception as exc:
            # Cookie dicts come from the client payload; a malformed one is a
            # task problem, not a browser problem.
            raise CaptchaError(
                "could not load task cookies into the browser",
                context={"count": len(cookies), "error": str(exc)},
            ) from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            if self._page is not None and not _page_closed(self._page):
                await self._page.close()
        with contextlib.suppress(Exception):
            await self._context.close()


class _CamoufoxLaunch:
    """Async context manager around `camoufox.async_api.AsyncCamoufox`.

    With `persistent_context=True` camoufox yields a BrowserContext, which is
    what a per-task profile needs.
    """

    def __init__(self, options: dict[str, Any]) -> None:
        self._options = options
        self._cm: Any = None

    async def __aenter__(self) -> Any:
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError as exc:
            raise ConfigError(
                "camoufox is not installed",
                context={"hint": "pip install camoufox (the browser downloads on first launch)"},
            ) from exc
        self._cm = AsyncCamoufox(**self._options)
        return await self._cm.__aenter__()

    async def __aexit__(self, *exc: Any) -> Any:
        if self._cm is None:
            return False
        return await self._cm.__aexit__(*exc)


def _camoufox_launcher(options: dict[str, Any]) -> AbstractAsyncContextManager[Any]:
    return _CamoufoxLaunch(options)


def _page_closed(page: Any) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:
        return True


__all__ = ["CamoufoxProvider", "CamoufoxSession"]
