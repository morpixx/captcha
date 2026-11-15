"""Real-browser smoke test for the browser lane.

Runs where Camoufox is installed — the `browser` job in
`.github/workflows/ci.yml`, or locally via:

    uv run --with camoufox pytest -m browser tests/integration/browser

It serves one local HTML page and drives it through the real
`CamoufoxProvider`, so the assertions cover the provider contract, not just
Playwright: a fresh profile per task, the contract launch options, navigation
through the session wrapper, and cleanup of the profile and the supervisor when
the task ends.

No live captcha site is contacted; the only host touched is 127.0.0.1.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("camoufox.async_api", reason="camoufox is not installed")

import captcha.adapters.netguard as netguard
from captcha.adapters.browser.provider import CamoufoxProvider, CamoufoxSession
from captcha.adapters.browser.supervisor import PROFILE_PREFIX
from captcha.domain.ports import BrowserSpec

pytestmark = pytest.mark.browser

PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>captcha-browser-fixture</title>
  </head>
  <body>
    <div id="probe">browser-lane-ok</div>
  </body>
</html>
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: Any) -> None:
        return


@pytest.fixture(scope="module")
def local_site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Serve one static page on 127.0.0.1; no external network involved."""
    root = tmp_path_factory.mktemp("browser-site")
    (root / "index.html").write_text(PAGE, encoding="utf-8")
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@contextlib.asynccontextmanager
async def _local_launcher(options: dict[str, Any]) -> AsyncIterator[Any]:
    """Real Camoufox, with the one option CI cannot honour.

    The provider asks for host-based GeoIP when the task carries no proxy. That
    performs a public-IP lookup, which is exactly the kind of network dependency
    a browser smoke test must not have; everything else is passed through
    untouched so the provider's option assembly is still what runs.
    """
    from camoufox.async_api import AsyncCamoufox

    local_options = dict(options)
    local_options["geoip"] = False
    async with AsyncCamoufox(**local_options) as context:
        yield context


async def test_provider_opens_a_local_page_and_cleans_up(
    tmp_path: Path, local_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def recording_launcher(options: dict[str, Any]) -> Any:
        seen.append(dict(options))
        return _local_launcher(options)

    provider = CamoufoxProvider(
        launcher=recording_launcher,
        temp_root=tmp_path,
        humanize=False,
    )

    async with provider.session(BrowserSpec(label="local-html", headless=True)) as session:
        assert isinstance(session, CamoufoxSession)
        assert len(seen) == 1

        options = seen[0]
        assert options["persistent_context"] is True
        assert options["os"] == "windows"
        assert options["block_webrtc"] is True
        assert options["enable_cache"] is False
        assert options["headless"] is True
        profile = Path(options["user_data_dir"])
        assert profile.is_dir()
        assert profile.name.startswith(PROFILE_PREFIX)
        assert profile.parent == tmp_path

        # 127.0.0.1 is exactly what the SSRF guard must reject, so prove the
        # merged contract first, then bypass it for the navigation itself. The
        # guard has its own tests (tests/adapters/test_netguard.py); this file
        # is about the provider's launch, navigation and cleanup.
        with pytest.raises(netguard.UrlRejectedError):
            await session.goto(local_site, timeout_ms=60_000)
        monkeypatch.setattr(netguard, "ensure_public_url", lambda url, **_kwargs: url)

        page = await session.goto(local_site, timeout_ms=60_000)
        assert await page.title() == "captcha-browser-fixture"
        assert await page.evaluate("() => document.querySelector('#probe').textContent") == "browser-lane-ok"
        assert await page.evaluate("() => navigator.userAgent")
        assert not await page.evaluate("() => navigator.webdriver")
        assert provider.supervisor.active_jobs

    assert not profile.exists(), "the per-task profile must be removed on exit"
    assert provider.supervisor.active_jobs == {}
    assert list(tmp_path.glob(PROFILE_PREFIX + "*")) == []
