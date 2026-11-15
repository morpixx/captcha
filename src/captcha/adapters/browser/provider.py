"""Camoufox provider: one fresh browser per task.

`CamoufoxProvider.session(spec)` is the browser-lane contract from
`docs/production-contract.md` §4: an async context manager, one new Camoufox
launch plus one temporary profile per task, GeoIP from the proxy exit IP, no
host fallback for a proxied task, no shared context between clients.

The launcher is injectable. Tests pass a fake async context manager and assert
on the options dict, so `camoufox` does not have to be installed to run the
suite — and is not imported at module level here.

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

import structlog

from captcha.adapters.browser.geoip import GeoIPCache, resolve_exit_ip
from captcha.adapters.browser.prefs import build_options
from captcha.adapters.browser.proxy import parse_proxy
from captcha.adapters.browser.supervisor import PROFILE_PREFIX, Supervisor, remove_profile
from captcha.domain.errors import CaptchaError, ConfigError, ProxyError
from captcha.domain.errors import TimeoutError as CaptchaTimeout
from captcha.domain.ports import BrowserSpec

log = structlog.get_logger(__name__)

# options dict -> async context manager yielding a persistent BrowserContext
Launcher = Callable[[dict[str, Any]], AbstractAsyncContextManager[Any]]
# proxy dict -> exit IP (None when unreachable). Sync: called in a worker thread.
ExitIPResolver = Callable[[dict[str, str] | None], str | None]


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

    @property
    def context(self) -> Any:
        return self._context

    async def page(self) -> Any:
        """Primary page: the persistent context's first tab, or a new one."""
        if self._page is None or _page_closed(self._page):
            pages = list(getattr(self._context, "pages", ()) or ())
            self._page = pages[0] if pages else await self._context.new_page()
        return self._page

    async def goto(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 60_000,
    ) -> Any:
        page = await self.page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        except Exception as exc:
            if "timeout" in str(exc).lower():
                raise CaptchaTimeout(
                    "page navigation timed out",
                    context={"url": url, "timeout_ms": timeout_ms},
                ) from exc
            raise CaptchaError("page navigation failed", context={"url": url}) from exc
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
