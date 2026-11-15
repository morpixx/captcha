"""Builder replay: what each script-driven engine hands its solver.

The five engines covered here cannot be assembled from a published byte layout, so
their builders return either

* a capture *program* (`str`) that runs the vendor script in
  :mod:`captcha.engines.capture` and reports what it wrote -- Castle, CyberSiARA,
  F5 Shape, and the script variant of Imperva/PerimeterX; or
* a request body (`dict`) for the one engine whose envelope *is* public: the
  Imperva reese84 POST and the PerimeterX sensorless collector.

Both shapes are pinned here, and the programs are executed against a stub SDK
rather than a live site: the vendor script is replaced by a fixture that writes the
same artefacts the real one does (a `castle_request_token` in storage, a `TS*`
cookie, a `master_url_token`), then the capture is read back the way the solver
reads it.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from captcha.adapters.jssandbox import LocalRuntime
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ChallengeExpiredError, SensorError
from captcha.engines import capture, get_builder
from captcha.engines.base import BuildContext
from captcha.engines.castle.builder import DRIVE_SNIPPET as CASTLE_DRIVE
from captcha.engines.castle.builder import TOKEN_KEY as CASTLE_TOKEN_KEY
from captcha.engines.imperva.builder import DEFAULT_VERSION, INTERROGATION_KEYS
from captcha.engines.perimeterx import codec as px_codec
from captcha.engines.perimeterx.builder import DEFAULT_TAG, SIGNAL_KEYS

PAGE_URL = "https://acme.example/login"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"
TIMESTAMP = 1_700_000_000_000

CASTLE_TOKEN = "tok_" + "a" * 24
CYBERSIARA_TOKEN = "mfp_" + "b" * 20
SHAPE_COOKIE = "TS01abcdef"
SHAPE_VALUE = "shape-cookie-value"

#: Stand-in for castle.browser.js: same API surface, no worker, no event loop.
CASTLE_SDK = f"""
window.Castle = {{
  configure: function (options) {{
    return {{
      createRequestToken: function () {{
        return {{ then: function (cb) {{ cb("{CASTLE_TOKEN}"); }} }};
      }}
    }};
  }}
}};
"""

#: Stand-in for the CyberSiARA widget: leaves the token where the page reads it.
CYBERSIARA_WIDGET = f'localStorage.setItem("master_url_token", "{CYBERSIARA_TOKEN}");'

#: Stand-in for the Shape VM: writes its cookie, then posts its sensor.
SHAPE_VM = f"""
document.cookie = "{SHAPE_COOKIE}={SHAPE_VALUE}; path=/";
var xhr = new XMLHttpRequest();
xhr.open("POST", "/TSPD/08abc");
xhr.send("sensor-body");
"""


class FakeFingerprint:
    user_agent = UA
    accept_language = "en-US,en;q=0.9"
    timezone = "Europe/Berlin"
    canvas_hash = "canvas-hash"
    webgl_vendor = "Google Inc. (NVIDIA)"
    webgl_renderer = "ANGLE (NVIDIA)"
    audio_hash = "audio-hash"
    tags: ClassVar[dict[str, str]] = {"platform": "Win32"}


def ctx(
    engine: AntibotEngine,
    *,
    challenge_js: str | None = None,
    fingerprint: Any = None,
    extra: dict[str, Any] | None = None,
    cookies: dict[str, str] | None = None,
    behavior: Any = None,
) -> BuildContext:
    return BuildContext(
        engine=engine,
        site_url=PAGE_URL,
        fingerprint=fingerprint,
        behavior=behavior,
        challenge_js=challenge_js,
        timestamp_ms=TIMESTAMP,
        cookies=dict(cookies or {}),
        extra=dict(extra or {}),
    )


@pytest.fixture
async def runtime() -> Any:
    rt = LocalRuntime(timeout_ms=5000)
    try:
        yield rt
    finally:
        await rt.close()


async def capture_of(runtime: Any, program: str, engine: str) -> capture.Capture:
    return capture.parse(await runtime.exec_script(program, {}, timeout_ms=5000), engine=engine)


def test_all_five_engines_have_a_registered_builder() -> None:
    expected = {
        AntibotEngine.CASTLE: "castle",
        AntibotEngine.CYBERSIARA: "cybersiara",
        AntibotEngine.IMPERVA: "imperva",
        AntibotEngine.PERIMETERX: "perimeterx",
        AntibotEngine.TSPD: "tspd",
    }
    for engine, name in expected.items():
        builder = get_builder(engine)
        assert builder.name == name


# --- Castle ------------------------------------------------------------------


async def test_castle_builder_needs_the_sdk_source_and_the_publishable_key() -> None:
    builder = get_builder(AntibotEngine.CASTLE)
    with pytest.raises(SensorError):
        await builder.build(ctx(AntibotEngine.CASTLE, extra={"publishable_key": "pk_test"}))
    with pytest.raises(SensorError):
        await builder.build(ctx(AntibotEngine.CASTLE, challenge_js=CASTLE_SDK))


async def test_castle_program_drives_the_sdk_and_leaves_the_token_in_storage(runtime: Any) -> None:
    program = await get_builder(AntibotEngine.CASTLE).build(
        ctx(
            AntibotEngine.CASTLE,
            challenge_js=CASTLE_SDK,
            extra={"publishable_key": "pk_test", "ua": UA, "storage": {"__cuid": "cuid-1"}},
            cookies={"_cid": "seed"},
        )
    )
    assert isinstance(program, str)
    assert CASTLE_DRIVE.strip() in program
    assert 'var __castlePk = "pk_test";' in program
    cap = await capture_of(runtime, program, "castle")
    assert cap.stored(CASTLE_TOKEN_KEY) == CASTLE_TOKEN
    assert cap.storage["__cuid"] == "cuid-1"
    assert cap.cookies["_cid"] == "seed"


async def test_castle_program_reports_a_missing_sdk_instead_of_throwing(runtime: Any) -> None:
    program = await get_builder(AntibotEngine.CASTLE).build(
        ctx(AntibotEngine.CASTLE, challenge_js="var unrelated = 1;", extra={"publishable_key": "pk_test"})
    )
    cap = await capture_of(runtime, program, "castle")
    assert cap.stored(CASTLE_TOKEN_KEY) == ""
    assert "sdk not configured" in cap.error_tail()


# --- CyberSiARA -------------------------------------------------------------


async def test_cybersiara_builder_needs_the_widget_script() -> None:
    with pytest.raises(SensorError):
        await get_builder(AntibotEngine.CYBERSIARA).build(ctx(AntibotEngine.CYBERSIARA, extra={"sitekey": "sk"}))


async def test_cybersiara_program_seeds_the_site_key_and_runs_the_widget(runtime: Any) -> None:
    program = await get_builder(AntibotEngine.CYBERSIARA).build(
        ctx(AntibotEngine.CYBERSIARA, challenge_js=CYBERSIARA_WIDGET, extra={"sitekey": "sk-42"})
    )
    assert isinstance(program, str)
    assert 'var SlideMasterUrlId = "sk-42";' in program
    assert 'var MasterUrlId = "sk-42";' in program
    cap = await capture_of(runtime, program, "cybersiara")
    assert cap.stored("master_url_token") == CYBERSIARA_TOKEN


# --- F5 Shape ---------------------------------------------------------------


async def test_tspd_builder_requires_the_tenant_script() -> None:
    with pytest.raises(ChallengeExpiredError):
        await get_builder(AntibotEngine.TSPD).build(ctx(AntibotEngine.TSPD))


async def test_tspd_program_reports_the_cookie_and_the_sensor_post(runtime: Any) -> None:
    program = await get_builder(AntibotEngine.TSPD).build(
        ctx(AntibotEngine.TSPD, challenge_js=SHAPE_VM, extra={"ua": UA, "storage": {"sid": "s"}})
    )
    cap = await capture_of(runtime, program, "tspd")
    assert cap.cookie_with_prefix(("TSPD", "TS")) == (SHAPE_COOKIE, SHAPE_VALUE)
    request = cap.request(needle="/TSPD/")
    assert request is not None and request.is_post and request.body == "sensor-body"


# --- Imperva ----------------------------------------------------------------


async def test_imperva_builder_needs_a_fingerprint_for_the_envelope() -> None:
    with pytest.raises(SensorError):
        await get_builder(AntibotEngine.IMPERVA).build(ctx(AntibotEngine.IMPERVA))


async def test_imperva_envelope_is_the_public_reese84_body() -> None:
    body = await get_builder(AntibotEngine.IMPERVA).build(
        ctx(
            AntibotEngine.IMPERVA,
            fingerprint=FakeFingerprint(),
            cookies={"reese84": "old-token"},
            extra={"interrogation_ms": 42},
            behavior={"mouse": [{"x": 1, "y": 2}]},
        )
    )
    assert isinstance(body, dict)
    assert set(body) == {"solution", "old_token", "error", "performance"}
    assert body["old_token"] == "old-token"
    assert body["error"] is None
    assert body["performance"] == {"interrogation": 42}
    solution = body["solution"]
    assert solution["version"] == DEFAULT_VERSION
    interrogation = solution["interrogation"]
    assert interrogation["p"] == "Win32"
    assert interrogation["l"] == "en-US"
    assert interrogation["tz"] == "Europe/Berlin"
    assert interrogation["cnv"] == "canvas-hash"
    assert interrogation["aud"] == "audio-hash"
    assert interrogation["wgl"] == {"vendor": FakeFingerprint.webgl_vendor, "renderer": FakeFingerprint.webgl_renderer}
    assert interrogation["mm"] == [{"x": 1, "y": 2}]
    # wire order is the engine's, not ours
    assert [key for key, _ in INTERROGATION_KEYS][:4] == ["p", "l", "tz", "scr"]


async def test_imperva_interrogation_override_wins_and_absent_values_are_omitted() -> None:
    body = await get_builder(AntibotEngine.IMPERVA).build(
        ctx(
            AntibotEngine.IMPERVA,
            fingerprint=FakeFingerprint(),
            extra={"interrogation": {"zz": "custom"}, "version": "beta"},
        )
    )
    assert body["solution"]["version"] == "beta"
    assert body["solution"]["interrogation"]["zz"] == "custom"
    assert "scr" not in body["solution"]["interrogation"]  # no screen in ctx.extra
    assert body["old_token"] == ""


async def test_imperva_script_variant_returns_a_capture_program(runtime: Any) -> None:
    program = await get_builder(AntibotEngine.IMPERVA).build(
        ctx(AntibotEngine.IMPERVA, challenge_js='document.cookie = "___utmvc=utmvc-value; path=/";')
    )
    assert isinstance(program, str)
    cap = await capture_of(runtime, program, "imperva")
    assert cap.cookie("___utmvc") == "utmvc-value"


# --- PerimeterX -------------------------------------------------------------


async def test_perimeterx_builder_needs_the_app_id() -> None:
    with pytest.raises(SensorError):
        await get_builder(AntibotEngine.PERIMETERX).build(ctx(AntibotEngine.PERIMETERX, fingerprint=FakeFingerprint()))


async def test_perimeterx_envelope_carries_the_codec_fields() -> None:
    body = await get_builder(AntibotEngine.PERIMETERX).build(
        ctx(
            AntibotEngine.PERIMETERX,
            fingerprint=FakeFingerprint(),
            cookies={"_pxvid": "visitor-1"},
            behavior={"mouse": [{"x": 1}], "keyboard": [{"k": "a"}]},
            extra={"app_id": "PXabc123", "pc_len": 12},
        )
    )
    assert isinstance(body, dict)
    assert set(body) == {"appId", "tag", "ft", "seq", "en", "uuid", "vid", "cts", "sid", "pc", "payload"}
    assert body["appId"] == "PXabc123"
    assert body["tag"] == DEFAULT_TAG
    assert body["ft"] == TIMESTAMP and body["cts"] == TIMESTAMP
    assert body["en"] == px_codec.en_field()
    assert body["uuid"] == "visitor-1" and body["vid"] == "visitor-1"
    assert len(body["pc"]) == 12
    assert px_codec.sid_decode(body["sid"]) == "visitor-1"
    assert all(ord(ch) >= 0xE0000 for ch in body["sid"])
    signals = px_codec.decode_payload(body["payload"])
    assert signals["PX101"] == UA
    assert signals["PX102"] == "en-US"
    assert signals["PX104"] == "Europe/Berlin"
    assert signals["PX107"] == "canvas-hash"
    assert signals["PX1200"] == [{"x": 1}]
    assert signals["PX1201"] == [{"k": "a"}]
    assert next(key for key, _ in SIGNAL_KEYS) == "PX101"


async def test_perimeterx_press_variant_returns_a_capture_program(runtime: Any) -> None:
    program = await get_builder(AntibotEngine.PERIMETERX).build(
        ctx(
            AntibotEngine.PERIMETERX,
            challenge_js='document.cookie = "_pxhd=verdict; path=/";',
            extra={"app_id": "PXabc123"},
        )
    )
    assert isinstance(program, str)
    cap = await capture_of(runtime, program, "perimeterx")
    assert cap.cookie("_pxhd") == "verdict"
