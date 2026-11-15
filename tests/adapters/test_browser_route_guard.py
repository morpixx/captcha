"""Browser route guard: every request in the page goes through netguard.

No Camoufox, no browser, no network. The launcher yields a fake persistent
context whose pages record `page.route("**/*")` handlers; the test feeds a
request through the real handler and asserts abort-vs-continue. The DNS
resolver is patched in the netguard module, so only literals skip it.

RT-05 regression: a page whose subresource or redirect points at loopback /
metadata must be blocked before the socket opens, and the task's error must be
`UrlRejectedError` without the query string in its context.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from captcha.adapters import netguard
from captcha.adapters.browser.provider import CamoufoxProvider
from captcha.adapters.netguard import UrlRejectedError
from captcha.domain.errors import CaptchaError
from captcha.domain.errors import TimeoutError as CaptchaTimeout
from captcha.domain.ports import BrowserSpec

PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netguard, "default_resolver", lambda _host: (PUBLIC_IP,))


class FakeRequest:
    def __init__(self, url: str, resource_type: str) -> None:
        self.url = url
        self.resource_type = resource_type
        self.frame: Any = None

    def is_navigation_request(self) -> bool:
        return self.resource_type == "document"


class FakeRoute:
    def __init__(self, url: str, resource_type: str) -> None:
        self.request = FakeRequest(url, resource_type)
        self.aborted: str | None = None
        self.continued = False

    async def abort(self, error_code: str | None = None) -> None:
        self.aborted = error_code or "aborted"

    async def continue_(self) -> None:
        self.continued = True


class RoutePage:
    """Page double that runs the registered `**/*` handlers for each request."""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.handlers: list[tuple[str, Any]] = []
        self.visited: list[FakeRoute] = []
        self.redirect_to: str | None = None
        self.fail: Exception | None = None
        self.closed = False
        self.main_frame = object()

    def is_closed(self) -> bool:
        return self.closed

    async def route(self, pattern: str, handler: Any) -> None:
        self.handlers.append((pattern, handler))

    async def visit(self, url: str, resource_type: str = "document") -> FakeRoute:
        route = FakeRoute(url, resource_type)
        for _pattern, handler in self.handlers:
            await handler(route)
        self.visited.append(route)
        return route

    async def goto(self, url: str, *, wait_until: str = "load", timeout: float | None = None) -> None:
        route = await self.visit(url)
        if route.aborted:
            raise RuntimeError("net::ERR_BLOCKED_BY_CLIENT")
        if self.redirect_to is not None:
            route = await self.visit(self.redirect_to)
            if route.aborted:
                raise RuntimeError("net::ERR_BLOCKED_BY_CLIENT")
            url = self.redirect_to
        if self.fail is not None:
            error, self.fail = self.fail, None
            raise error
        self.url = url

    async def close(self) -> None:
        self.closed = True


class RouteContext:
    def __init__(self, pages: int = 0) -> None:
        self.pages: list[Any] = [RoutePage() for _ in range(pages)]
        self.cookies: list[dict[str, Any]] = []
        self.closed = False

    async def new_page(self) -> RoutePage:
        page = RoutePage()
        self.pages.append(page)
        return page

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        self.cookies.extend(cookies)

    async def close(self) -> None:
        self.closed = True


class RouteLauncher:
    def __init__(self, *, context: Any | None = None) -> None:
        self.context = context
        self.contexts: list[Any] = []

    def __call__(self, _options: dict[str, Any]):
        return self._session()

    @asynccontextmanager
    async def _session(self):
        context = self.context if self.context is not None else RouteContext()
        self.contexts.append(context)
        try:
            yield context
        finally:
            await context.close()


def make_provider(tmp_path: Path, *, launcher: RouteLauncher | None = None) -> tuple[CamoufoxProvider, RouteLauncher]:
    launcher = launcher or RouteLauncher()
    provider = CamoufoxProvider(
        launcher=launcher,
        exit_ip=lambda _proxy: "198.51.100.7",
        temp_root=tmp_path,
    )
    return provider, launcher


async def test_route_hook_is_installed_before_the_caller_can_navigate(tmp_path: Path) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()

    assert page.handlers and page.handlers[0][0] == "**/*"


async def test_page_is_guarded_once(tmp_path: Path) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        assert await session.page() is page

    assert len(page.handlers) == 1


async def test_metadata_subresource_is_aborted(tmp_path: Path) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        route = await page.visit("http://169.254.169.254/latest/meta-data/iam/", "image")

    assert route.aborted == "blockedbyclient"
    assert route.continued is False


async def test_loopback_navigation_is_aborted(tmp_path: Path) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        route = await page.visit("http://127.0.0.1:8080/admin", "document")

    assert route.aborted == "blockedbyclient"


async def test_hostname_resolving_to_private_is_aborted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netguard, "default_resolver", lambda host: ("10.0.0.5",) if host == "cdn.example" else (PUBLIC_IP,))
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        route = await page.visit("https://cdn.example/tracker.js", "script")

    assert route.aborted == "blockedbyclient"


async def test_public_subresource_continues(tmp_path: Path, public_dns: None) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        route = await page.visit("https://cdn.example.com/app.js", "script")

    assert route.continued is True
    assert route.aborted is None


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/file", "ws://10.0.0.1/socket"])
async def test_non_http_schemes_are_aborted(tmp_path: Path, url: str) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        route = await page.visit(url, "other")

    assert route.aborted == "blockedbyclient"


@pytest.mark.parametrize("url", ["about:blank", "data:image/png;base64,AAAA", "blob:https://example.com/uuid"])
async def test_network_free_schemes_continue(tmp_path: Path, url: str) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        route = await page.visit(url, "image")

    assert route.continued is True


async def test_goto_private_redirect_raises_url_rejected(tmp_path: Path, public_dns: None) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        page.redirect_to = "http://10.0.0.1/admin"
        with pytest.raises(UrlRejectedError) as info:
            await session.goto("https://example.com/")

    assert info.value.code == "ERR_URL_REJECTED"
    assert info.value.context["host"] == "10.0.0.1"
    # the navigation never committed
    assert page.url == "about:blank"


async def test_blocked_initial_navigation_raises_before_the_page_exists(tmp_path: Path) -> None:
    provider, launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        with pytest.raises(UrlRejectedError):
            await session.goto("http://169.254.169.254/computeMetadata/v1/")

    assert launcher.contexts[0].pages == []


async def test_goto_error_context_is_host_only(tmp_path: Path, public_dns: None) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        page.fail = RuntimeError("Timeout 30000ms exceeded")
        with pytest.raises(CaptchaTimeout) as info:
            await session.goto("https://example.com/challenge?token=supersecret")

    assert info.value.context["url"] == "https://example.com"
    assert "supersecret" not in repr(info.value.context)


async def test_blocked_subresource_does_not_poison_a_later_timeout(tmp_path: Path, public_dns: None) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        await page.visit("http://169.254.169.254/", "image")
        page.fail = RuntimeError("Timeout 30000ms exceeded")
        with pytest.raises(CaptchaTimeout):
            await session.goto("https://example.com/")


async def test_page_without_a_route_hook_still_navigates(tmp_path: Path, public_dns: None) -> None:
    """The fakes in test_browser_core have no `route`; the guard must not crash."""

    class NoRoutePage:
        def __init__(self) -> None:
            self.url = "about:blank"
            self.navigations: list[str] = []
            self.closed = False

        def is_closed(self) -> bool:
            return self.closed

        async def goto(self, url: str, *, wait_until: str = "load", timeout: float | None = None) -> None:
            self.navigations.append(url)
            self.url = url

        async def close(self) -> None:
            self.closed = True

    page = NoRoutePage()
    context = RouteContext()
    context.pages = [page]
    provider, _launcher = make_provider(tmp_path, launcher=RouteLauncher(context=context))

    async with provider.session(BrowserSpec()) as session:
        assert await session.goto("https://example.com/challenge") is page

    assert page.navigations == ["https://example.com/challenge"]


async def test_generic_navigation_failure_keeps_its_captcha_error(tmp_path: Path, public_dns: None) -> None:
    provider, _launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        page.fail = RuntimeError("net::ERR_CONNECTION_RESET")
        with pytest.raises(CaptchaError) as info:
            await session.goto("https://example.com/")

    assert info.value.code == "ERR_GENERIC"
    assert "page navigation failed" in info.value.message
