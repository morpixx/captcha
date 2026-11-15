from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers.base import SolverContext
from captcha.solvers.recaptcha._common import DEFAULT_VERSION
from captcha.solvers.recaptcha._proto import encode_reload_body
from captcha.solvers.score_based.recaptcha_v3 import (
    RecaptchaV3EnterpriseSolver,
    RecaptchaV3Solver,
)

FIXTURES = Path(__file__).parents[1] / "recaptcha" / "fixtures"
SITEKEY = "test-sitekey"
SITE_URL = "https://example.com/login"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, body: str | bytes, *, status: int = 200, final_url: str = "") -> None:
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status = status
        self.final_url = final_url
        self.headers: list[tuple[str, str]] = []

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

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.responses.pop(0)


def make_task(
    *,
    type: CaptchaType = CaptchaType.RECAPTCHA_V3,
    payload: dict[str, Any] | None = None,
    sitekey: str | None = SITEKEY,
    site_url: str | None = SITE_URL,
) -> Task:
    return Task(
        id=new_task_id(),
        type=type,
        payload=payload or {},
        sitekey=sitekey,
        site_url=site_url,
        language="en",
    )


def responses(*, final: str = "v3_reload.json") -> list[FakeResponse]:
    return [
        FakeResponse(fixture("anchor.html"), final_url="https://www.google.com/recaptcha/api2/anchor?k=x"),
        FakeResponse(fixture(final)),
    ]


def decode_proto(data: bytes) -> dict[int, list[Any]]:
    fields: dict[int, list[Any]] = {}
    index = 0

    def varint(pos: int) -> tuple[int, int]:
        value = 0
        shift = 0
        while True:
            byte = data[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, pos
            shift += 7

    while index < len(data):
        tag, index = varint(index)
        number = tag >> 3
        wire = tag & 0x07
        if wire == 2:
            length, index = varint(index)
            fields.setdefault(number, []).append(data[index : index + length])
            index += length
        elif wire == 0:
            value, index = varint(index)
            fields.setdefault(number, []).append(value)
        else:  # pragma: no cover - the encoder never emits fixed32/fixed64
            raise AssertionError(f"unexpected wire type {wire}")
    return fields


async def test_v3_happy_path_uses_protobuf_reload_without_sensors() -> None:
    http = FakeHTTP(*responses())
    result = await RecaptchaV3Solver().solve_http(make_task(), SolverContext(http=http))

    assert result.token == "v3-token-5"
    assert result.extra == {"action": "verify", "min_score": 0.3}
    assert [call["method"] for call in http.calls] == ["GET", "POST"]
    assert http.calls[0]["url"].endswith("/recaptcha/api2/anchor")
    assert http.calls[0]["params"]["size"] == "invisible"
    assert http.calls[0]["params"]["v"] == DEFAULT_VERSION
    assert http.calls[1]["url"].endswith("/recaptcha/api2/reload")
    assert http.calls[1]["params"] == {"k": SITEKEY}
    assert http.calls[1]["headers"]["content-type"] == "application/x-protobuffer"

    fields = decode_proto(http.calls[1]["data"])
    assert fields[1] == [DEFAULT_VERSION.encode()]
    assert fields[2] == [b"anchor-token-1"]
    assert fields[6] == [b"q"]
    assert fields[8] == [b"verify"]
    assert fields[14] == [SITEKEY.encode()]


async def test_anchor_retries_with_refreshed_version() -> None:
    http = FakeHTTP(
        FakeResponse(fixture("anchor_no_token.html")),
        FakeResponse(fixture("api.js")),
        *responses(),
    )
    result = await RecaptchaV3Solver().solve_http(make_task(), SolverContext(http=http))

    assert result.token == "v3-token-5"
    assert http.calls[0]["params"]["v"] == DEFAULT_VERSION
    assert http.calls[1]["url"].endswith("/recaptcha/api.js")
    assert http.calls[2]["params"]["v"] == "TESTVERSION"
    assert decode_proto(http.calls[3]["data"])[1] == [b"TESTVERSION"]


async def test_v3_action_and_min_score_are_carried() -> None:
    http = FakeHTTP(*responses())
    result = await RecaptchaV3Solver().solve_http(
        make_task(payload={"action": "login", "min_score": 0.7}),
        SolverContext(http=http),
    )
    fields = decode_proto(http.calls[1]["data"])
    assert fields[8] == [b"login"]
    assert result.extra["action"] == "login"
    assert result.extra["min_score"] == 0.7


async def test_v3_enterprise_uses_enterprise_endpoints() -> None:
    http = FakeHTTP(*responses())
    result = await RecaptchaV3EnterpriseSolver().solve_http(
        make_task(type=CaptchaType.RECAPTCHA_V3_ENTERPRISE),
        SolverContext(http=http),
    )
    assert result.token == "v3-token-5"
    assert http.calls[0]["url"].endswith("/recaptcha/enterprise/anchor")
    assert http.calls[1]["url"].endswith("/recaptcha/enterprise/reload")


async def test_v3_explicit_version_skips_bootstrap() -> None:
    http = FakeHTTP(*responses())
    await RecaptchaV3Solver().solve_http(make_task(payload={"v": "PINNED"}), SolverContext(http=http))
    assert http.calls[0]["url"].endswith("/recaptcha/api2/anchor")
    assert decode_proto(http.calls[1]["data"])[1] == [b"PINNED"]


async def test_v3_bg_and_fingerprint_are_forwarded() -> None:
    http = FakeHTTP(*responses())
    await RecaptchaV3Solver().solve_http(
        make_task(
            payload={
                "bg": "BG-BLOB",
                "fingerprint": {"5": "seed", "28": "20000", "99": "ignored"},
            }
        ),
        SolverContext(http=http),
    )
    fields = decode_proto(http.calls[1]["data"])
    assert fields[4] == [b"BG-BLOB"]
    assert fields[5] == [b"seed"]
    assert fields[28] == [20000]
    assert 99 not in fields


async def test_v3_nested_rresp_is_parsed() -> None:
    http = FakeHTTP(*responses(final="v3_reload_nested.json"))
    result = await RecaptchaV3Solver().solve_http(make_task(), SolverContext(http=http))
    assert result.token == "nested-token-6"


async def test_v3_malformed_json_still_yields_rresp() -> None:
    http = FakeHTTP(*responses(final="v3_reload_malformed.txt"))
    result = await RecaptchaV3Solver().solve_http(make_task(), SolverContext(http=http))
    assert result.token == "regex-token-7"


async def test_v3_missing_http_is_config_error() -> None:
    with pytest.raises(ConfigError):
        await RecaptchaV3Solver().solve_http(make_task(), SolverContext(http=None))


async def test_v3_missing_sitekey_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError):
        await RecaptchaV3Solver().solve_http(make_task(sitekey=None), SolverContext(http=FakeHTTP()))


async def test_v3_missing_site_url_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError):
        await RecaptchaV3Solver().solve_http(make_task(site_url=None), SolverContext(http=FakeHTTP()))


async def test_v3_invalid_action_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError):
        await RecaptchaV3Solver().solve_http(
            make_task(payload={"action": "   "}),
            SolverContext(http=FakeHTTP()),
        )


@pytest.mark.parametrize("min_score", ["high", -0.1, 1.1, None])
async def test_v3_invalid_min_score_is_unsolvable(min_score: Any) -> None:
    with pytest.raises(UnsolvableError):
        await RecaptchaV3Solver().solve_http(
            make_task(payload={"min_score": min_score}),
            SolverContext(http=FakeHTTP()),
        )


async def test_v3_no_release_slug_is_challenge_expired() -> None:
    http = FakeHTTP(
        FakeResponse(fixture("anchor_no_token.html")),
        FakeResponse("<html>no release here</html>"),
    )
    with pytest.raises(ChallengeExpiredError):
        await RecaptchaV3Solver().solve_http(make_task(), SolverContext(http=http))


async def test_v3_anchor_without_token_is_challenge_expired() -> None:
    http = FakeHTTP(
        FakeResponse(fixture("anchor_no_token.html")),
        FakeResponse(fixture("api.js")),
        FakeResponse(fixture("anchor_no_token.html")),
    )
    with pytest.raises(ChallengeExpiredError):
        await RecaptchaV3Solver().solve_http(make_task(), SolverContext(http=http))


async def test_v3_reload_without_rresp_is_unsolvable() -> None:
    http = FakeHTTP(*responses(final="v3_reload_empty.json"))
    with pytest.raises(UnsolvableError):
        await RecaptchaV3Solver().solve_http(make_task(), SolverContext(http=http))


def test_encode_reload_body_minimal_is_canonical() -> None:
    body = encode_reload_body(version="v", token="c", sitekey="k", action="a")
    assert body == b"\x0a\x01v\x12\x01c\x32\x01q\x42\x01a\x72\x01k"


def test_encode_reload_body_without_action_omits_field_8() -> None:
    fields = decode_proto(encode_reload_body(version="v", token="c", sitekey="k"))
    assert 8 not in fields
    assert fields[1] == [b"v"]
    assert fields[2] == [b"c"]
    assert fields[6] == [b"q"]
    assert fields[14] == [b"k"]


def test_encode_reload_body_fingerprint_varint() -> None:
    body = encode_reload_body(
        version="v",
        token="c",
        sitekey="k",
        fingerprint={28: 300, 29: 30000, "bad": "x", 7: "prior"},
    )
    fields = decode_proto(body)
    assert fields[28] == [300]
    assert fields[29] == [30000]
    assert fields[7] == [b"prior"]
