"""Kasada builder: the /tl capture, the cd envelope, and the refusals.

The PoW answers cannot be computed from anything a client holds (the seed mixes a
value that never reaches the wire), so the honest behaviour is to carry a VM
capture and to raise when it is absent. These tests pin both: the capture path
through real V8, and every place where "no answers" must not become a payload.
"""

from __future__ import annotations

import base64
import json

import pytest

from captcha.adapters.jssandbox.miniracer import LocalRuntime
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ConfigError, SensorError, UnsolvableError
from captcha.engines.base import BuildContext
from captcha.engines.kasada import pow
from captcha.engines.kasada.builder import KasadaBuilder, capture_requests, tl_request

SITE = "https://site.test/login"
TL_URL = "/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/tl"
PJS = (
    """
(function () {
  var payload = new Uint8Array([1, 2, 3, 4, 250, 0]);
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '"""
    + TL_URL
    + """');
  xhr.setRequestHeader('x-kpsdk-ct', 'pre-ct');
  xhr.setRequestHeader('x-kpsdk-dt', 'pre-dt');
  xhr.send(payload);
})();
"""
)


def ctx(extra=None):
    return BuildContext(
        engine=AntibotEngine.KASADA,
        site_url=SITE,
        fingerprint=object(),
        behavior={},
        extra=dict(extra or {}),
    )


# ---- capture parsing --------------------------------------------------------


def test_tl_request_decodes_the_bytes_and_lowercases_headers():
    captured = {
        "sent": [
            {"url": "/x/fp", "method": "GET", "body_b64": "", "headers": {}},
            {
                "url": TL_URL,
                "method": "POST",
                "body_b64": base64.b64encode(b"\x01\x02").decode(),
                "headers": {"X-KPSDK-CT": "a", "X-Kpsdk-DT": "b", "x-kpsdk-im": ""},
            },
        ]
    }

    request = tl_request(captured)

    assert request.body == b"\x01\x02"
    assert request.headers == {"x-kpsdk-ct": "a", "x-kpsdk-dt": "b"}
    assert request.url == TL_URL
    assert request.payload_b64 == base64.b64encode(b"\x01\x02").decode()


def test_no_tl_post_is_a_script_failure():
    captured = {"sent": [{"url": "/x/fp", "method": "POST", "body_b64": "AA=="}], "errors": ["boom"]}

    with pytest.raises(SensorError) as exc:
        tl_request(captured)

    assert exc.value.context["requests"] == 1
    assert exc.value.context["script_errors"] == ["boom"]


def test_empty_tl_body_is_rejected():
    captured = {"sent": [{"url": TL_URL, "method": "POST", "body_b64": "", "headers": {}}]}

    with pytest.raises(SensorError):
        tl_request(captured)


# ---- cd envelope ------------------------------------------------------------


def test_envelope_uses_the_documented_field_names_and_order():
    payload = pow.envelope(st=1700000000, answers=[1, 2, 3], ident="abc", work_time=12, duration=4)

    assert list(payload) == list(pow.CD_FIELDS)
    assert payload["answers"] == [1, 2, 3]
    assert payload["st"] == 1700000000


def test_cd_header_is_base64_of_compact_json():
    payload = pow.envelope(st=5, answers="1,2")

    decoded = json.loads(base64.b64decode(pow.encode(payload)))

    assert decoded == payload
    assert pow.encode_json(payload).startswith("{")


def test_envelope_from_payload_extras():
    extra = {
        pow.EXTRA_ST: "1700000000",
        pow.EXTRA_ANSWERS: [9, 8],
        pow.EXTRA_ID: "id-1",
        pow.EXTRA_WORK_TIME: "3",
    }

    payload = pow.envelope_from(extra)

    assert payload is not None
    assert payload["st"] == 1700000000
    assert payload["answers"] == [9, 8]
    assert payload["id"] == "id-1"
    assert payload["workTime"] == 3


def test_missing_answers_are_never_invented():
    assert pow.envelope_from({pow.EXTRA_ST: 1}) is None

    with pytest.raises(SensorError):
        pow.require_envelope({pow.EXTRA_ST: 1})


def test_too_many_answers_is_a_sensor_error():
    with pytest.raises(SensorError):
        pow.envelope(st=1, answers=list(range(pow.MAX_ANSWERS + 1)))


# ---- builder ----------------------------------------------------------------


async def test_builder_returns_the_script_own_tl_body():
    runtime = LocalRuntime()
    try:
        build_ctx = ctx({"js_runtime": runtime})
        build_ctx.challenge_js = PJS

        built = await KasadaBuilder().build(build_ctx)
    finally:
        await runtime.close()

    assert base64.b64decode(built["payload_b64"]) == b"\x01\x02\x03\x04\xfa\x00"
    assert built["bytes"] == 6
    assert built["headers"]["x-kpsdk-ct"] == "pre-ct"
    assert built["target"].endswith("/tl")


async def test_builder_needs_the_script():
    with pytest.raises(UnsolvableError):
        await KasadaBuilder().build(ctx({"js_runtime": object()}))


async def test_builder_needs_a_runtime():
    build_ctx = ctx()
    build_ctx.challenge_js = PJS

    with pytest.raises(ConfigError):
        await KasadaBuilder().build(build_ctx)


async def test_capture_reports_a_throwing_script():
    runtime = LocalRuntime()
    try:
        captured = await capture_requests(runtime, "throw new Error('kaboom');")
    finally:
        await runtime.close()

    assert captured["sent"] == []
    assert "kaboom" in captured["errors"][0]


async def test_build_cd_encodes_payload_answers():
    build_ctx = ctx({pow.EXTRA_ST: 1700000000, pow.EXTRA_ANSWERS: [4, 5]})

    header = KasadaBuilder().build_cd(build_ctx)

    decoded = json.loads(base64.b64decode(header))
    assert decoded["answers"] == [4, 5]
    assert decoded["st"] == 1700000000


async def test_build_cd_without_answers_raises():
    with pytest.raises(SensorError):
        KasadaBuilder().build_cd(ctx())
