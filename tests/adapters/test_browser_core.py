"""Browser lane internals: launch options, per-task provider, process supervisor.

Nothing here launches a browser. The provider's launcher is a fake async
context manager, so the assertions are on the exact options dict Camoufox would
receive and on the lifecycle: one fresh profile per task, killed and removed on
exit, no host fallback when the proxy has no exit IP.

Supervisor tests spawn a real short-lived Python process to exercise the kill
path — a local process, not a browser, no network.
"""

from __future__ import annotations

import subprocess
import sys
import types
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from captcha.adapters.browser.geoip import GeoIPCache, resolve_exit_ip
from captcha.adapters.browser.prefs import FIREFOX_PREFS, build_options, default_addons
from captcha.adapters.browser.provider import CamoufoxProvider, CamoufoxSession
from captcha.adapters.browser.proxy import parse_proxy, proxy_fingerprint, proxy_url
from captcha.adapters.browser.supervisor import PROFILE_PREFIX, Supervisor
from captcha.domain.errors import CaptchaError, ConfigError, ProxyError
from captcha.domain.errors import TimeoutError as CaptchaTimeout
from captcha.domain.ports import BrowserSpec

# --------------------------------------------------------------------------- #
# Fakes: a browser that exists only as a dict of options.
# --------------------------------------------------------------------------- #


class FakePage:
    def __init__(self) -> None:
        self.navigations: list[str] = []
        self.closed = False
        self.error: Exception | None = None

    def is_closed(self) -> bool:
        return self.closed

    async def goto(self, url: str, *, wait_until: str = "load", timeout: float | None = None) -> None:
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        self.navigations.append(url)

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, *, pages: int = 0, cookies_fail: bool = False) -> None:
        self.pages: list[FakePage] = [FakePage() for _ in range(pages)]
        self.cookies: list[dict[str, Any]] = []
        self.closed = False
        self.cookies_fail = cookies_fail

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        if self.cookies_fail:
            raise RuntimeError("cookie is missing name/domain")
        self.cookies.extend(cookies)

    async def close(self) -> None:
        self.closed = True


class FakeLauncher:
    """Records every options dict and hands back an empty persistent context."""

    def __init__(
        self,
        *,
        fail: Exception | None = None,
        pages: int = 0,
        cookies_fail: bool = False,
    ) -> None:
        self.options: list[dict[str, Any]] = []
        self.contexts: list[FakeContext] = []
        self.fail = fail
        self.pages = pages
        self.cookies_fail = cookies_fail

    def __call__(self, options: dict[str, Any]):
        self.options.append(dict(options))
        return self._session()

    @asynccontextmanager
    async def _session(self):
        if self.fail is not None:
            raise self.fail
        context = FakeContext(pages=self.pages, cookies_fail=self.cookies_fail)
        self.contexts.append(context)
        try:
            yield context
        finally:
            await context.close()


def make_provider(tmp_path: Path, **kwargs: Any) -> tuple[CamoufoxProvider, FakeLauncher]:
    launcher = kwargs.pop("launcher", None) or FakeLauncher()
    exit_ip = kwargs.pop("exit_ip", lambda _proxy: "198.51.100.7")
    provider = CamoufoxProvider(
        launcher=launcher,
        exit_ip=exit_ip,
        temp_root=tmp_path,
        **kwargs,
    )
    return provider, launcher


def profile_dirs(tmp_path: Path) -> list[Path]:
    return list(tmp_path.glob(PROFILE_PREFIX + "*"))


# --------------------------------------------------------------------------- #
# Launch options (ported from D:/project/src/browser)
# --------------------------------------------------------------------------- #


def test_options_carry_the_contract_values() -> None:
    addon = object()
    options = build_options(
        headless=True,
        proxy={"server": "http://1.2.3.4:8000"},
        geoip="203.0.113.9",
        user_data_dir="/tmp/profile",
        humanize=True,
        exclude_addons=[addon],
    )

    assert options["persistent_context"] is True
    assert options["os"] == "windows"
    assert options["block_webrtc"] is True
    assert options["enable_cache"] is False
    assert options["geoip"] == "203.0.113.9"
    assert options["humanize"] is True
    assert options["user_data_dir"] == "/tmp/profile"
    assert options["headless"] is True
    assert options["proxy"] == {"server": "http://1.2.3.4:8000"}
    assert options["exclude_addons"] == [addon]
    assert options["firefox_user_prefs"] == FIREFOX_PREFS


def test_firefox_prefs_keep_the_memory_and_webrtc_choices() -> None:
    prefs = FIREFOX_PREFS
    assert prefs["dom.ipc.processCount"] == 1
    assert prefs["media.peerconnection.enabled"] is False
    assert prefs["browser.cache.disk.enable"] is False
    assert prefs["toolkit.telemetry.enabled"] is False
    # WebGL stays on: it is part of the fingerprint Camoufox generates.
    assert "webgl.disabled" not in prefs


def test_humanize_is_configurable() -> None:
    options = build_options(
        headless=True,
        proxy=None,
        geoip=True,
        user_data_dir="/tmp/profile",
        humanize=False,
        exclude_addons=[],
    )
    assert options["humanize"] is False
    assert options["proxy"] is None


def test_ubo_is_excluded_when_camoufox_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    fake = types.SimpleNamespace(DefaultAddons=types.SimpleNamespace(UBO=sentinel))
    monkeypatch.setitem(sys.modules, "camoufox", fake)

    assert default_addons() == [sentinel]
    options = build_options(headless=True, proxy=None, geoip=True, user_data_dir="/tmp/p")
    assert options["exclude_addons"] == [sentinel]


def test_options_build_without_camoufox_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "camoufox", None)

    assert default_addons() is None
    options = build_options(headless=True, proxy=None, geoip=True, user_data_dir="/tmp/p")
    assert "exclude_addons" not in options


# --------------------------------------------------------------------------- #
# Provider: one fresh session per task
# --------------------------------------------------------------------------- #


async def test_session_is_one_fresh_profile_per_task(tmp_path: Path) -> None:
    provider, launcher = make_provider(tmp_path)

    async with provider.session(BrowserSpec(label="task-a")) as session:
        assert isinstance(session, CamoufoxSession)
        assert launcher.options[0]["geoip"] is True
        profile = Path(launcher.options[0]["user_data_dir"])
        assert profile.is_dir()
        assert profile.name.startswith(PROFILE_PREFIX)
        assert profile.parent == tmp_path

        page = await session.page()
        await session.goto("https://example.com/challenge")
        assert page.navigations == ["https://example.com/challenge"]
        assert launcher.contexts[0].closed is False

    assert launcher.contexts[0].closed is True
    assert not profile.exists()
    assert provider.supervisor.active_jobs == {}


async def test_every_task_gets_its_own_launch(tmp_path: Path) -> None:
    provider, launcher = make_provider(tmp_path)
    profiles: list[Path] = []

    for label in ("first", "second"):
        async with provider.session(BrowserSpec(label=label)):
            profiles.append(Path(launcher.options[-1]["user_data_dir"]))

    assert len(launcher.options) == 2
    assert profiles[0] != profiles[1]
    assert not any(path.exists() for path in profiles)


async def test_proxied_task_uses_the_exit_ip_for_geoip(tmp_path: Path) -> None:
    provider, launcher = make_provider(tmp_path, exit_ip=lambda _proxy: "203.0.113.9")

    async with provider.session(BrowserSpec(proxy="http://user:pass@1.2.3.4:8000")):
        options = launcher.options[0]
        assert options["geoip"] == "203.0.113.9"
        assert options["proxy"] == {
            "server": "http://1.2.3.4:8000",
            "username": "user",
            "password": "pass",
        }


async def test_proxy_without_exit_ip_fails_before_launch(tmp_path: Path) -> None:
    provider, launcher = make_provider(tmp_path, exit_ip=lambda _proxy: None)

    with pytest.raises(ProxyError) as info:
        async with provider.session(BrowserSpec(proxy="http://1.2.3.4:8000")):
            pytest.fail("session must not open without an exit IP")

    assert info.value.code == "ERR_PROXY"
    assert launcher.options == []
    assert profile_dirs(tmp_path) == []


async def test_launch_failure_is_a_captcha_error_and_cleans_up(tmp_path: Path) -> None:
    provider, _launcher = make_provider(tmp_path, launcher=FakeLauncher(fail=RuntimeError("boom")))

    with pytest.raises(CaptchaError) as info:
        async with provider.session(BrowserSpec()):
            pytest.fail("launch failed, body must not run")

    assert "camoufox launch failed" in info.value.message
    assert profile_dirs(tmp_path) == []
    assert provider.supervisor.active_jobs == {}


async def test_missing_camoufox_raises_config_error(tmp_path: Path) -> None:
    with patch.dict(sys.modules, {"camoufox": None, "camoufox.async_api": None}):
        provider = CamoufoxProvider(exit_ip=lambda _proxy: "203.0.113.9", temp_root=tmp_path)
        with pytest.raises(ConfigError) as info:
            async with provider.session(BrowserSpec()):
                pytest.fail("camoufox is absent, session must not open")

    assert "camoufox is not installed" in info.value.message
    assert profile_dirs(tmp_path) == []


async def test_default_launcher_hands_the_options_to_camoufox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncCamoufox:
        def __init__(self, **options: Any) -> None:
            captured.update(options)

        async def __aenter__(self) -> FakeContext:
            captured["context"] = FakeContext()
            return captured["context"]

        async def __aexit__(self, *_exc: Any) -> bool:
            captured["exited"] = True
            return False

    package = types.ModuleType("camoufox")
    package.__path__ = []  # type: ignore[attr-defined]
    async_api = types.ModuleType("camoufox.async_api")
    async_api.AsyncCamoufox = FakeAsyncCamoufox  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "camoufox", package)
    monkeypatch.setitem(sys.modules, "camoufox.async_api", async_api)

    provider = CamoufoxProvider(exit_ip=None, temp_root=tmp_path, humanize=False)

    async with provider.session(BrowserSpec()) as session:
        assert isinstance(session, CamoufoxSession)
        assert captured["os"] == "windows"
        assert captured["persistent_context"] is True
        assert captured["block_webrtc"] is True
        assert captured["enable_cache"] is False
        assert captured["geoip"] is True
        assert captured["humanize"] is False
        assert captured["firefox_user_prefs"] == FIREFOX_PREFS
        assert Path(captured["user_data_dir"]).is_dir()
        assert await session.page() is not None

    assert captured["exited"] is True


async def test_cookies_from_the_task_reach_the_context(tmp_path: Path) -> None:
    provider, launcher = make_provider(tmp_path)
    cookies = [{"name": "sid", "value": "x", "domain": "example.com", "path": "/"}]

    async with provider.session(BrowserSpec(cookies=cookies)):
        assert launcher.contexts[0].cookies == cookies


async def test_malformed_cookies_are_a_task_error(tmp_path: Path) -> None:
    provider, _launcher = make_provider(tmp_path, launcher=FakeLauncher(cookies_fail=True))

    with pytest.raises(CaptchaError) as info:
        async with provider.session(BrowserSpec(cookies=[{"value": "no-name"}])):
            pytest.fail("cookie load fails, body must not run")

    assert "cookies" in info.value.message
    assert profile_dirs(tmp_path) == []


async def test_navigation_timeout_maps_to_the_domain_error(tmp_path: Path) -> None:
    provider, _launcher = make_provider(tmp_path, launcher=FakeLauncher(pages=1))

    async with provider.session(BrowserSpec()) as session:
        page = await session.page()
        page.error = RuntimeError("Timeout 30000ms exceeded")
        with pytest.raises(CaptchaTimeout):
            await session.goto("https://example.com/")
        page.error = RuntimeError("net::ERR_CONNECTION_RESET")
        with pytest.raises(CaptchaError):
            await session.goto("https://example.com/")


async def test_provider_cleans_up_as_an_async_context_manager(tmp_path: Path) -> None:
    provider = CamoufoxProvider(
        launcher=FakeLauncher(),
        exit_ip=lambda _proxy: "203.0.113.9",
        temp_root=tmp_path,
    )
    async with AsyncExitStack() as stack:
        assert await stack.enter_async_context(provider) is provider
        session = await stack.enter_async_context(provider.session(BrowserSpec()))
        assert isinstance(session, CamoufoxSession)
        assert session.job_id

    with pytest.raises(ConfigError):
        async with provider.session(BrowserSpec()):
            pytest.fail("provider is closed")


async def test_stale_profiles_are_reaped_and_close_kills(tmp_path: Path) -> None:
    stale = tmp_path / (PROFILE_PREFIX + "from-a-crashed-worker")
    stale.mkdir()
    provider, _launcher = make_provider(tmp_path)

    # Reaper runs once, before the first session of this process.
    async with provider.session(BrowserSpec()):
        assert not stale.exists()

    leftover = tmp_path / (PROFILE_PREFIX + "leftover")
    leftover.mkdir()
    await provider.close()
    assert not leftover.exists()


# --------------------------------------------------------------------------- #
# Supervisor: registry, tree kill, reaper
# --------------------------------------------------------------------------- #


def spawn_sleeper(*marker: str) -> subprocess.Popen:
    """A stand-in process tree; no browser, no network."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", *marker],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_register_and_unregister(tmp_path: Path) -> None:
    supervisor = Supervisor(temp_root=tmp_path)
    profile = tmp_path / (PROFILE_PREFIX + "job")

    supervisor.register("job-1", profile)
    assert supervisor.active_jobs == {"job-1": profile}
    assert supervisor.profile_for("job-1") == profile
    assert supervisor.unregister("job-1") == profile
    assert supervisor.active_jobs == {}
    assert supervisor.unregister("job-1") is None


def test_find_processes_by_profile_command_line(tmp_path: Path) -> None:
    profile = tmp_path / (PROFILE_PREFIX + "scanned")
    profile.mkdir()
    proc = spawn_sleeper(str(profile))
    try:
        found = Supervisor.find_processes(profile, names=("python",))
        assert any(candidate.pid == proc.pid for candidate in found)
        # A different profile must not match this process.
        assert Supervisor.find_processes(profile.with_name(profile.name + "-other"), names=("python",)) == []
    finally:
        with suppress(Exception):
            proc.kill()
            proc.wait(timeout=10)


def test_kill_terminates_the_tree_found_by_profile(tmp_path: Path) -> None:
    profile = tmp_path / (PROFILE_PREFIX + "killed")
    profile.mkdir()
    proc = spawn_sleeper(str(profile))
    try:
        supervisor = Supervisor(temp_root=tmp_path, process_names=("python",))
        supervisor.register("job", profile)

        assert supervisor.kill("job") >= 1
        assert proc.wait(timeout=10) is not None
        supervisor.unregister("job")
    finally:
        with suppress(Exception):
            proc.kill()
            proc.wait(timeout=10)


def test_kill_uses_the_registered_pid(tmp_path: Path) -> None:
    proc = spawn_sleeper()
    try:
        supervisor = Supervisor(temp_root=tmp_path)
        supervisor.register("job", tmp_path / "profile", pid=proc.pid)

        # A venv python on Windows is a redirector: the registered pid is the
        # root of a two-process tree, and the tree is what gets killed.
        assert supervisor.kill("job") >= 1
        assert proc.wait(timeout=10) is not None
    finally:
        with suppress(Exception):
            proc.kill()
            proc.wait(timeout=10)


def test_kill_all_clears_the_registry(tmp_path: Path) -> None:
    supervisor = Supervisor(temp_root=tmp_path)
    for job_id in ("a", "b"):
        supervisor.register(job_id, tmp_path / (PROFILE_PREFIX + job_id))

    assert supervisor.kill_all() == 0
    assert supervisor.active_jobs == {}


def test_reap_stale_removes_only_unowned_profiles(tmp_path: Path) -> None:
    live = tmp_path / (PROFILE_PREFIX + "live")
    dead = tmp_path / (PROFILE_PREFIX + "dead")
    unrelated = tmp_path / "not-a-browser-profile"
    for path in (live, dead, unrelated):
        path.mkdir()

    supervisor = Supervisor(temp_root=tmp_path)
    supervisor.register("live", live)

    removed = supervisor.reap_stale()

    assert removed == [dead]
    assert not dead.exists()
    assert live.exists()
    assert unrelated.exists()


def test_reap_stale_respects_max_age(tmp_path: Path) -> None:
    fresh = tmp_path / (PROFILE_PREFIX + "fresh")
    fresh.mkdir()
    supervisor = Supervisor(temp_root=tmp_path)

    assert supervisor.reap_stale(max_age_sec=3600) == []
    assert fresh.exists()


# --------------------------------------------------------------------------- #
# GeoIP: exit IP per proxy, cached
# --------------------------------------------------------------------------- #


def test_exit_ip_is_resolved_once_per_proxy() -> None:
    calls: list[str] = []

    def fetch(service: str, _budget: float) -> str:
        calls.append(service)
        return "198.51.100.7\n"

    cache = GeoIPCache(ttl_sec=60.0)
    proxy = {"server": "http://1.2.3.4:8000", "username": "u", "password": "p"}

    assert resolve_exit_ip(proxy, cache=cache, fetch=fetch) == "198.51.100.7"
    assert resolve_exit_ip(proxy, cache=cache, fetch=fetch) == "198.51.100.7"
    assert len(calls) == 1


def test_exit_ip_cache_expires() -> None:
    now = [1000.0]
    cache = GeoIPCache(ttl_sec=10.0, clock=lambda: now[0])
    proxy = {"server": "http://1.2.3.4:8000"}

    resolve_exit_ip(proxy, cache=cache, fetch=lambda _service, _budget: "198.51.100.7")
    assert cache.get(proxy_fingerprint(proxy)) == "198.51.100.7"

    now[0] += 11.0
    assert cache.get(proxy_fingerprint(proxy)) is None


def test_unresolvable_exit_ip_returns_none() -> None:
    def dead(_service: str, _budget: float) -> str:
        raise OSError("proxy is dead")

    proxy = {"server": "http://1.2.3.4:8000"}
    assert resolve_exit_ip(proxy, fetch=dead) is None
    # First answer is not an address, second one is.
    answers = iter(["<html>nope</html>", "198.51.100.7"])
    assert resolve_exit_ip(proxy, fetch=lambda _service, _budget: next(answers)) == "198.51.100.7"
    assert resolve_exit_ip(proxy, fetch=lambda _service, _budget: "not-an-ip") is None


def test_proxy_url_encodes_credentials() -> None:
    proxy = parse_proxy("http://user:p@ss:word@10.0.0.1:8000")

    assert proxy["username"] == "user"
    assert proxy["password"] == "p@ss:word"
    assert proxy_url(proxy) == "http://user:p%40ss%3Aword@10.0.0.1:8000"
    assert parse_proxy("1.2.3.4:8000") == {"server": "http://1.2.3.4:8000"}
    assert proxy_url(None) is None
