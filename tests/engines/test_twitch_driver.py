"""Twitch KPSDK driver: the patched fetch headers are captured for real.

The builder's whole job is to load p.js into the shared JS runtime, fire the
KPSDK lifecycle, and report the headers the patched fetch attached. The stub
bootstrap below patches window.fetch the way p.js does, so the test exercises
the shim, the event dispatch and the header capture without a network call.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ChallengeExpiredError, SensorError
from captcha.engines.base import BuildContext
from captcha.engines.twitch.builder import INTEGRITY_URL, KPSDK_TARGET, TwitchIntegrityBuilder

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

KPSDK = """
(function () {
  var originalFetch = window.fetch;
  window.fetch = function (url, options) {
    var headers = options.headers || {};
    headers["x-kpsdk-ct"] = "ct-1";
    headers["x-kpsdk-cd"] = "cd-1";
    headers["x-kpsdk-v"] = "j-1.1.0";
    headers["x-configured"] = JSON.stringify(window.__target || null);
    options.headers = headers;
    return originalFetch(url, options);
  };
  window.KPSDK = { configure: function (target) { window.__target = target; } };
})();
"""


def make_ctx(**kwargs: Any) -> BuildContext:
    headers = kwargs.pop("headers", {"x-device-id": "dev-1", "client-version": "v-1"})
    return BuildContext(
        engine=AntibotEngine.TWITCH,
        site_url="https://www.twitch.tv/somechannel",
        fingerprint=None,
        behavior=None,
        challenge_js=kwargs.pop("challenge_js", KPSDK),
        timestamp_ms=1_700_000_000_000,
        headers=headers,
        extra={"ua": UA, **kwargs},
    )


async def run_plan(plan: dict[str, Any]) -> Any:
    pytest.importorskip("py_mini_racer")
    from captcha.adapters.jssandbox.miniracer import LocalRuntime

    runtime = LocalRuntime(timeout_ms=5_000)
    try:
        return await runtime.exec_script(plan["script"], plan["globals"], timeout_ms=5_000)
    finally:
        await runtime.close()


async def test_the_patched_fetch_headers_are_captured() -> None:
    plan = await TwitchIntegrityBuilder().build(make_ctx())

    raw = json.loads(await run_plan(plan))

    assert raw["error"] is None
    assert raw["request"]["url"] == INTEGRITY_URL
    assert raw["request"]["method"] == "POST"
    assert raw["request"]["headers"]["x-kpsdk-ct"] == "ct-1"
    assert raw["request"]["headers"]["x-kpsdk-cd"] == "cd-1"
    assert raw["request"]["headers"]["x-device-id"] == "dev-1"
    assert raw["request"]["headers"]["client-id"] == "kimne78kx3ncx6brgo4mv6wki5h1ko"


async def test_kpsdk_is_configured_for_the_integrity_endpoint() -> None:
    plan = await TwitchIntegrityBuilder().build(make_ctx())

    raw = json.loads(await run_plan(plan))

    configured = json.loads(raw["request"]["headers"]["x-configured"])
    assert configured == [KPSDK_TARGET]


async def test_the_builder_refuses_a_payload_without_a_device_id() -> None:
    with pytest.raises(SensorError):
        await TwitchIntegrityBuilder().build(make_ctx(headers={"client-version": "v-1"}))


async def test_the_builder_refuses_a_missing_bootstrap() -> None:
    with pytest.raises(ChallengeExpiredError):
        await TwitchIntegrityBuilder().build(make_ctx(challenge_js=None))
