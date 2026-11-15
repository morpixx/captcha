from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ChallengeExpiredError
from captcha.domain.task import new_task_id
from captcha.solvers.base import SolverContext
from captcha.solvers.recaptcha._common import (
    audio_payload_url,
    encode_origin,
    endpoints_for,
    extract_anchor_token,
    extract_audio_url,
    extract_reload_token,
    extract_rresp,
    resolve_version,
)

FIXTURES = Path(__file__).with_name("fixtures")
SITEKEY = "test-sitekey"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, body: str, *, status: int = 200, final_url: str = "") -> None:
        self.body = body.encode("utf-8")
        self.status = status
        self.final_url = final_url

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class FakeHTTP:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.responses.pop(0)


def make_task(payload: dict[str, Any] | None = None) -> Task:
    return Task(
        id=new_task_id(),
        type=CaptchaType.RECAPTCHA_V2,
        payload=payload or {},
        sitekey=SITEKEY,
        site_url="https://example.com/login",
        language="en",
    )


def test_encode_origin_round_trips_to_scheme_host_port() -> None:
    co = encode_origin("https://example.com/login")
    assert "=" not in co
    decoded = base64.b64decode(co.replace(".", "=")).decode()
    assert decoded == "https://example.com:443"


def test_encode_origin_keeps_non_default_port() -> None:
    co = encode_origin("http://example.com:8080/x")
    assert base64.b64decode(co.replace(".", "=")).decode() == "http://example.com:8080"


def test_anchor_token_from_recorded_page() -> None:
    assert extract_anchor_token(fixture("anchor.html")) == "anchor-token-1"


def test_anchor_token_handles_reversed_attribute_order() -> None:
    html = '<input value="reversed-token" type="hidden" id="recaptcha-token">'
    assert extract_anchor_token(html) == "reversed-token"


def test_anchor_without_token_is_none() -> None:
    assert extract_anchor_token(fixture("anchor_no_token.html")) is None


def test_rresp_from_canonical_array() -> None:
    assert extract_rresp(fixture("reload_swap.json")) == "challenge-token-2"


def test_rresp_from_nested_array() -> None:
    assert extract_rresp(fixture("v3_reload_nested.json")) == "nested-token-6"


def test_rresp_from_dict() -> None:
    assert extract_rresp(')]}\'{"rresp":"dict-token","ttl":120}') == "dict-token"


def test_rresp_from_malformed_json() -> None:
    assert extract_rresp(fixture("v3_reload_malformed.txt")) == "regex-token-7"


def test_rresp_null_is_none() -> None:
    assert extract_rresp(fixture("userverify_rejected.json")) is None


def test_rresp_from_protobuf_like_body() -> None:
    body = "\n\x05rresp\x12\x20proto-token-abcdefghijklmnop"
    assert extract_rresp(body) == "proto-token-abcdefghijklmnop"


def test_reload_token_falls_back_to_bare_array_index() -> None:
    body = ')]}\'\n["bare-token",null,120]'
    assert extract_reload_token(body) == "bare-token"


def test_audio_url_from_escaped_json() -> None:
    url = extract_audio_url(fixture("reload_audio.json"))
    assert url == "https://www.google.com/recaptcha/api2/payload?p=audio-payload-3"


def test_audio_url_absent_returns_none() -> None:
    assert extract_audio_url(fixture("reload_swap.json")) is None


def test_audio_payload_url_legacy_and_p_variants() -> None:
    api2 = endpoints_for(False)
    c_url = audio_payload_url(api2, "tok", sitekey=SITEKEY)
    p_url = audio_payload_url(api2, "tok", sitekey=SITEKEY, param="p")
    assert c_url == f"https://www.google.com/recaptcha/api2/payload?c=tok&k={SITEKEY}&id=2"
    assert p_url == "https://www.google.com/recaptcha/api2/payload?p=tok"


async def test_resolve_version_reads_release_slug() -> None:
    http = FakeHTTP(FakeResponse(fixture("api.js")))
    task = make_task()
    version = await resolve_version(task, SolverContext(http=http), endpoints_for(False))
    assert version == "TESTVERSION"
    assert http.calls[0]["params"] == {"render": SITEKEY}


async def test_resolve_version_honours_explicit_payload_value() -> None:
    http = FakeHTTP()
    task = make_task({"v": "PINNED"})
    version = await resolve_version(task, SolverContext(http=http), endpoints_for(False))
    assert version == "PINNED"
    assert http.calls == []


async def test_resolve_version_without_slug_is_challenge_expired() -> None:
    http = FakeHTTP(FakeResponse("<html>no release here</html>"))
    with pytest.raises(ChallengeExpiredError):
        await resolve_version(make_task(), SolverContext(http=http), endpoints_for(False))


def test_endpoints_for_enterprise() -> None:
    ent = endpoints_for(True)
    assert ent.anchor == "https://www.google.com/recaptcha/enterprise/anchor"
    assert ent.js.endswith("/recaptcha/enterprise.js")
