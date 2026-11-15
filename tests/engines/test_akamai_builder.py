"""Akamai sensor model and builder: framing, seeds, and the JS capture seam.

The model encoder is a labelled approximation of the two documented v3 stages, so
these tests pin its *properties* (deterministic, reversible, seeded from the
script and bm_sz) rather than a byte vector from a live tenant -- nothing here can
claim a live match. The JS path runs a synthetic bmak script through the real V8
runtime: that is the production path, and the point is that the builder takes the
bytes the script posts and never mints its own.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from captcha.adapters.jssandbox.miniracer import LocalRuntime
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ConfigError, SensorError, UnsolvableError
from captcha.engines.akamai import sensor
from captcha.engines.akamai.builder import (
    AkamaiSensorBuilder,
    capture_requests,
    sensor_data_from,
)
from captcha.engines.base import BuildContext

SITE = "https://site.test/login"
BMAK_JS = """
(function () {
  var xhr = new XMLHttpRequest();
  xhr.open('POST', location.origin + '/akam/13/abcdef');
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.send(JSON.stringify({sensor_data: '3:device=ua,lang=en'}));
})();
"""


def profile(**overrides):
    base = {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0",
        "accept_language": "en-US,en;q=0.9",
        "timezone": "Europe/Berlin",
        "canvas_hash": "c0ffee",
        "webgl_vendor": "Google Inc.",
        "webgl_renderer": "ANGLE",
        "audio_hash": "a11d10",
        "fonts": ["Arial", "Verdana"],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def build_ctx(fingerprint=None, behavior=None, extra=None):
    return BuildContext(
        engine=AntibotEngine.AKAMAI,
        site_url=SITE,
        fingerprint=fingerprint if fingerprint is not None else profile(),
        behavior=behavior if behavior is not None else {"mouse": []},
        extra=dict(extra or {}),
    )


# ---- model transform --------------------------------------------------------


def test_encode_decode_roundtrip():
    fields = ["3", "device=ua,lang=en", "mouse=1,2,3;4,5,6"]

    encoded = sensor.encode_elements(fields, seed=1234)

    assert encoded != ":".join(fields)
    assert sensor.decode_elements(encoded, seed=1234) == fields


def test_model_substitution_covers_the_delimiters_too():
    encoded = sensor.encode_elements(["3", "device=x"], seed=7)

    # The encoder substitutes every alphabet character, colon included, so the
    # blob is opaque until it is decoded with the same seed.
    assert ":" not in encoded
    assert sensor.decode_elements(encoded, seed=7) == ["3", "device=x"]


def test_default_bm_sz_seed_is_the_documented_one():
    assert sensor.bm_sz_seed(None) == 8888888
    assert sensor.bm_sz_seed("") == 8888888


def test_bm_sz_seed_is_stable_and_value_dependent():
    assert sensor.bm_sz_seed("abc") == sensor.bm_sz_seed("abc")
    assert sensor.bm_sz_seed("abc") != sensor.bm_sz_seed("abd")


def test_script_seed_pins_the_payload_to_one_script_revision():
    assert sensor.script_seed("a") == sensor.script_seed(b"a")
    assert sensor.script_seed("a") != sensor.script_seed("a ")


def test_encode_sections_is_deterministic_for_the_same_seed_pair():
    rows = [("device", "ua=x"), ("screen", "w=1920")]

    first = sensor.encode_sections(rows, script="bmak", bm_sz="seed")
    second = sensor.encode_sections(rows, script="bmak", bm_sz="seed")

    assert first == second
    assert first != sensor.encode_sections(rows, script="bmak", bm_sz="other")


def test_serialize_drops_empty_sections():
    assert sensor.serialize([("device", "ua=x"), ("screen", "")]) == ["3", "device=ua=x"]


# ---- section collection -----------------------------------------------------


def test_sections_take_profile_fields_only():
    rows = dict(sensor.sections(build_ctx()))

    assert rows["device"].startswith("ua=Mozilla/5.0")
    assert "fonts=2" in rows["device"]
    assert "screen" not in rows  # no screen data was injected: no fabricated size
    assert "hardware" not in rows


def test_sections_serialize_injected_screen_and_hardware():
    ctx = build_ctx(extra={"screen": {"w": 1920, "h": 1080}, "hardware": {"hc": 8}})

    rows = dict(sensor.sections(ctx))

    assert rows["screen"] == "h=1080,w=1920"
    assert rows["hardware"] == "hc=8"


def test_sections_take_behaviour_rows_in_order():
    behavior = {"mouse": [{"t": 1, "x": 10, "y": 20, "type": "move"}, {"t": 5, "x": 11, "y": 21, "type": "move"}]}

    rows = dict(sensor.sections(build_ctx(behavior=behavior)))

    assert rows["mouse"] == "1,10,20,move;5,11,21,move"


def test_sections_cap_event_rows():
    behavior = {"mouse": [{"t": i, "x": i, "y": i} for i in range(sensor.MAX_EVENTS + 20)]}

    rows = dict(sensor.sections(build_ctx(behavior=behavior)))

    assert rows["mouse"].count(";") == sensor.MAX_EVENTS - 1


def test_sections_accept_an_object_behavior_pack():
    class Track:
        def __init__(self) -> None:
            self.mouse = [{"t": 1, "x": 2, "y": 3}]

    rows = dict(sensor.sections(build_ctx(behavior=Track())))

    assert rows["mouse"] == "1,2,3"


# ---- builder ----------------------------------------------------------------


async def test_builder_requires_fingerprint_and_behavior():
    ctx = build_ctx(fingerprint=None)
    ctx.fingerprint = None

    with pytest.raises(SensorError):
        await AkamaiSensorBuilder().build(ctx)


async def test_builder_js_path_returns_what_the_script_posted():
    runtime = LocalRuntime()
    try:
        ctx = build_ctx(extra={"js_runtime": runtime})
        ctx.challenge_js = BMAK_JS

        data = await AkamaiSensorBuilder().build(ctx)
    finally:
        await runtime.close()

    assert data == "3:device=ua,lang=en"


async def test_builder_js_path_needs_the_script():
    with pytest.raises(UnsolvableError):
        await AkamaiSensorBuilder().build(build_ctx(extra={"js_runtime": object()}))


async def test_builder_js_path_needs_a_runtime():
    ctx = build_ctx()
    ctx.challenge_js = BMAK_JS

    with pytest.raises(ConfigError):
        await AkamaiSensorBuilder().build(ctx)


async def test_builder_model_path_needs_no_script():
    data = await AkamaiSensorBuilder().build(build_ctx(extra={"sensor_encoder": "model"}))

    assert data.startswith("3:") is False  # shuffled: the version is an element, not a prefix
    assert data


async def test_builder_model_path_is_seeded_by_bm_sz():
    first = await AkamaiSensorBuilder().build(build_ctx(extra={"sensor_encoder": "model"}))
    ctx = build_ctx(extra={"sensor_encoder": "model"})
    ctx.cookies["bm_sz"] = "seed"
    second = await AkamaiSensorBuilder().build(ctx)

    assert first != second


async def test_builder_rejects_an_unknown_encoder():
    with pytest.raises(ConfigError):
        await AkamaiSensorBuilder().build(build_ctx(extra={"sensor_encoder": "magic"}))


async def test_sensor_data_is_read_out_of_a_form_body():
    captured = {"sent": [{"url": "/akam", "method": "POST", "body": "sensor_data=abc%3Adef"}]}

    assert sensor_data_from(captured) == "abc:def"


def test_sensor_data_from_ignores_bodies_without_the_field():
    captured = {"sent": [{"url": "/akam", "method": "POST", "body": '{"other": 1}'}], "errors": ["boom"]}

    with pytest.raises(SensorError) as exc:
        sensor_data_from(captured)

    assert exc.value.context["script_errors"] == ["boom"]


async def test_capture_without_a_capture_list_is_a_runtime_error():
    class Runtime:
        async def exec_script(self, code, globals=None, timeout_ms=None):
            return "{}"

    with pytest.raises(Exception) as exc:
        await capture_requests(Runtime(), "var a = 1;")

    assert "capture" in str(exc.value)


async def test_script_error_is_reported_not_swallowed():
    runtime = LocalRuntime()
    try:
        captured = await capture_requests(runtime, "throw new Error('nope');")
    finally:
        await runtime.close()

    assert captured["sent"] == []
    assert captured["errors"]


# ---- SBSD envelope ----------------------------------------------------------


def test_sbsd_envelope_is_json_ready():
    ctx = build_ctx()
    ctx.timestamp_ms = 1_700_000_000_000

    body = AkamaiSensorBuilder().build_sbsd(ctx)

    assert body["version"] == sensor.VERSION
    assert body["ts"] == 1_700_000_000_000
    assert body["sensor_data"].startswith("device=ua=")


def test_sbsd_prefers_a_client_supplied_body():
    supplied = {"version": "4", "sensor_data": "client", "ts": 1}

    body = AkamaiSensorBuilder().build_sbsd(build_ctx(extra={"sbsd_body": supplied}))

    assert body == supplied
    assert body is not supplied
