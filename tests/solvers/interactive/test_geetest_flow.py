"""Canned-response flow tests for the GeeTest v3/v4 solvers.

No network: every HTTP call is answered from a fixture or a mock, and the CV
slider delegate is stubbed with a fixed offset. What is asserted here is the
call order, the query/body fields the wire schema requires, and the error
translation (ChallengeExpired / Unsolvable / Config).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext
from captcha.solvers.interactive.geetest import v3 as v3mod
from captcha.solvers.interactive.geetest import v4 as v4mod
from captcha.solvers.interactive.geetest.v3 import GeetestV3Solver
from captcha.solvers.interactive.geetest.v4 import GeetestV4Solver

FIXTURES = Path(__file__).parent / "fixtures" / "geetest"
V3_BASE = "https://api.geetest.com"
V3_GET = V3_BASE + "/get.php"
V3_AJAX = V3_BASE + "/ajax.php"
V3_BG = "https://static.geetest.com/bg/v3-bg.webp"
V3_SLICE = "https://static.geetest.com/slice/v3-slice.png"
V4_BASE = "https://gcaptcha4.geetest.com"
V4_LOAD = V4_BASE + "/load"
V4_VERIFY = V4_BASE + "/verify"
V4_BG = "https://static.geetest.com/bg/v4-bg.webp"
V4_SLICE = "https://static.geetest.com/slice/v4-slice.png"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class _Reply:
    """Minimal stand-in for captcha.adapters.transport.Response."""

    def __init__(self, payload: Any = None, *, body: bytes = b"", status: int = 200) -> None:
        self.status = status
        if isinstance(payload, str):
            self.body = payload.encode()
            self._payload = None
        else:
            self.body = body or (b"" if payload is None else json.dumps(payload).encode())
            self._payload = payload

    @property
    def text(self) -> str:
        return self.body.decode()

    def json(self) -> Any:
        if self._payload is not None:
            return self._payload
        return json.loads(self.body)


class _HTTP:
    """Canned reply per URL; a list value is consumed in call order."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _reply(self, url: str) -> _Reply:
        if url not in self.replies:
            raise AssertionError(f"unexpected HTTP call: {url}")
        value = self.replies[url]
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"no reply left for {url}")
            value = value.pop(0)
        return value if isinstance(value, _Reply) else _Reply(value)

    async def get(self, url: str, **kwargs: Any) -> _Reply:
        self.calls.append(("GET", url, kwargs))
        return self._reply(url)

    async def post(self, url: str, **kwargs: Any) -> _Reply:
        self.calls.append(("POST", url, kwargs))
        return self._reply(url)


def _task(captcha_type: CaptchaType, payload: dict[str, Any], **overrides: Any) -> Task:
    return Task(
        id=new_task_id(),
        type=captcha_type,
        payload=payload,
        preferred_mode=SolveMode.HTTP,
        **overrides,
    )


def _stub_cv(monkeypatch: pytest.MonkeyPatch, solver_cls: type, offset: float = 96.0) -> list[bytes]:
    payloads: list[bytes] = []

    async def fake(self: Any, kind: CaptchaType, task: Task, ctx: SolverContext, payload: dict[str, Any]):
        payloads.append(payload["bg"])
        return SolverResult(offset=offset)

    monkeypatch.setattr(solver_cls, "_solve_cv", fake)
    return payloads


def _v3_payload(**extra: Any) -> dict[str, Any]:
    return {
        "gt": "gt-fixture",
        "challenge": "22ff2f326624b638791dfdef7892700d",
        "pageurl": "https://shop.test/login",
        **extra,
    }


def _v4_payload(**extra: Any) -> dict[str, Any]:
    return {"captcha_id": "captcha-fixture", "pageurl": "https://shop.test/login", **extra}


# ---- v3 flow -----------------------------------------------------------------


def test_v3_flow_builds_both_w_envelopes(monkeypatch: pytest.MonkeyPatch) -> None:
    images = _stub_cv(monkeypatch, GeetestV3Solver)
    envelopes: list[bool] = []

    def fake_build(*args: Any, **kwargs: Any) -> str:
        with_rsa = bool(kwargs.get("with_rsa", True))
        envelopes.append(with_rsa)
        return "w-first" if with_rsa else "w-second"

    monkeypatch.setattr(v3mod, "build_w_v3", fake_build)
    http = _HTTP(
        {
            V3_GET: [_fixture("v3_get_round.txt"), _fixture("v3_get_images.txt")],
            V3_AJAX: [_fixture("v3_ajax_mode.txt"), _fixture("v3_ajax_verify.txt")],
            V3_BG: _Reply(body=b"bg-v3"),
            V3_SLICE: _Reply(body=b"piece-v3"),
        }
    )
    task = _task(CaptchaType.GEETEST_V3, _v3_payload(), site_url="https://shop.test/login")
    result = asyncio.run(GeetestV3Solver().solve_http(task, SolverContext(http=http)))

    assert envelopes == [True, False]
    assert images == [b"bg-v3"]
    assert [call[1] for call in http.calls] == [V3_GET, V3_AJAX, V3_GET, V3_BG, V3_SLICE, V3_AJAX]
    assert http.calls[0][2]["params"]["w"] == "w-first"
    assert http.calls[-1][0] == "POST"
    assert http.calls[-1][2]["data"]["w"] == "w-second"
    assert http.calls[-1][2]["data"]["challenge"] == "22ff2f326624b638791dfdef7892700d9w"
    assert http.calls[0][2]["headers"]["Referer"] == "https://shop.test/login"
    assert result.token == "v3-validate"
    assert result.extra == {
        "challenge": "22ff2f326624b638791dfdef7892700d9w",
        "validate": "v3-validate",
        "seccode": "v3-validate|jordan",
    }


def test_v3_expired_challenge_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, GeetestV3Solver)
    monkeypatch.setattr(v3mod, "build_w_v3", lambda *a, **k: "w")
    http = _HTTP({V3_GET: _fixture("v3_expired.txt")})
    with pytest.raises(ChallengeExpiredError, match=r"get\.php"):
        asyncio.run(
            GeetestV3Solver().solve_http(_task(CaptchaType.GEETEST_V3, _v3_payload()), SolverContext(http=http))
        )
    assert len(http.calls) == 1


def test_v3_missing_images_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, GeetestV3Solver)
    monkeypatch.setattr(v3mod, "build_w_v3", lambda *a, **k: "w")
    http = _HTTP(
        {
            V3_GET: [
                _fixture("v3_get_round.txt"),
                'geetest_1({"status":"success","data":{"challenge":"22ff2f326624b638791dfdef7892700d9w"}})',
            ],
            V3_AJAX: 'geetest_2({"status":"success","data":{}})',
        }
    )
    with pytest.raises(UnsolvableError, match="slide images"):
        asyncio.run(
            GeetestV3Solver().solve_http(_task(CaptchaType.GEETEST_V3, _v3_payload()), SolverContext(http=http))
        )


def test_v3_short_challenge_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, GeetestV3Solver)
    monkeypatch.setattr(v3mod, "build_w_v3", lambda *a, **k: "w")
    http = _HTTP(
        {
            V3_GET: [
                _fixture("v3_get_round.txt"),
                'geetest_1({"status":"success","data":{"bg":"bg/v3-bg.webp","slice":"slice/v3-slice.png",'
                '"c":[12,58,98,36,43,95,62,15,12],"s":"a1b2c3"}})',
            ],
            V3_AJAX: _fixture("v3_ajax_mode.txt"),
            V3_BG: _Reply(body=b"bg"),
            V3_SLICE: _Reply(body=b"piece"),
        }
    )
    with pytest.raises(UnsolvableError, match="34"):
        asyncio.run(
            GeetestV3Solver().solve_http(_task(CaptchaType.GEETEST_V3, _v3_payload()), SolverContext(http=http))
        )


def test_v3_bad_api_server_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, GeetestV3Solver)
    http = _HTTP({})
    task = _task(CaptchaType.GEETEST_V3, _v3_payload(api_server="::::"))
    with pytest.raises(ConfigError):
        asyncio.run(GeetestV3Solver().solve_http(task, SolverContext(http=http)))
    assert http.calls == []


def test_v3_requires_gt_and_challenge() -> None:
    with pytest.raises(UnsolvableError, match="missing payload fields"):
        asyncio.run(
            GeetestV3Solver().solve_http(
                _task(CaptchaType.GEETEST_V3, {"challenge": "c" * 32}), SolverContext(http=_HTTP({}))
            )
        )


# ---- v4 flow -----------------------------------------------------------------


def test_v4_flow_sends_the_2026_verify_query(monkeypatch: pytest.MonkeyPatch) -> None:
    images = _stub_cv(monkeypatch, GeetestV4Solver)
    captured: dict[str, Any] = {}

    def fake_build(*args: Any, **kwargs: Any) -> str:
        captured["args"] = args
        captured.update(kwargs)
        return "w-v4"

    monkeypatch.setattr(v4mod, "build_w_v4", fake_build)
    http = _HTTP(
        {
            V4_LOAD: _fixture("v4_load.txt"),
            V4_BG: _Reply(body=b"bg-v4"),
            V4_SLICE: _Reply(body=b"piece-v4"),
            V4_VERIFY: _fixture("v4_verify.txt"),
        }
    )
    task = _task(CaptchaType.GEETEST_V4, _v4_payload(), site_url="https://shop.test/login")
    result = asyncio.run(GeetestV4Solver().solve_http(task, SolverContext(http=http)))

    assert images == [b"bg-v4"]
    assert captured["pt"] == "1"
    assert captured["risk_type"] == "slide"
    assert captured["args"][2] == 96.0  # float left, not the rounded offset
    assert 600 <= captured["args"][3] <= 1200
    assert captured["args"][0] == "f4744c44df4541b3be48c5c270ced20b"
    assert captured["args"][1] == "captcha-fixture"

    load_params = http.calls[0][2]["params"]
    assert load_params["risk_type"] == "slide"
    assert load_params["client_type"] == "web"
    assert load_params["challenge"]

    verify_params = http.calls[-1][2]["params"]
    assert http.calls[-1][1] == V4_VERIFY
    assert verify_params["w"] == "w-v4"
    assert verify_params["captcha_id"] == "captcha-fixture"
    assert verify_params["lot_number"] == "f4744c44df4541b3be48c5c270ced20b"
    assert verify_params["risk_type"] == "slide"
    assert verify_params["payload"] == "payload-fixture"
    assert verify_params["process_token"] == "process-fixture"
    assert verify_params["payload_protocol"] == "1"
    assert verify_params["pt"] == "1"

    assert result.token == "v4-output"
    assert result.extra == {
        "captcha_id": "captcha-fixture",
        "lot_number": "f4744c44df4541b3be48c5c270ced20b",
        "pass_token": "v4-pass",
        "gen_time": "1700000000012",
        "captcha_output": "v4-output",
    }


def test_v4_pt0_uses_the_urlencoded_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, GeetestV4Solver)
    captured: dict[str, Any] = {}

    def fake_build(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "w-pt0"

    monkeypatch.setattr(v4mod, "build_w_v4", fake_build)
    http = _HTTP(
        {
            V4_LOAD: _fixture("v4_load_pt0.txt"),
            V4_BG: _Reply(body=b"bg"),
            V4_SLICE: _Reply(body=b"piece"),
            V4_VERIFY: _fixture("v4_verify.txt"),
        }
    )
    result = asyncio.run(
        GeetestV4Solver().solve_http(_task(CaptchaType.GEETEST_V4, _v4_payload()), SolverContext(http=http))
    )
    assert captured["pt"] == "0"
    assert http.calls[-1][2]["params"]["pt"] == "0"
    assert http.calls[-1][2]["params"]["payload"] == "payload-pt0"
    assert result.token == "v4-output"


def test_v4_pt2_is_reported_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, GeetestV4Solver)
    http = _HTTP(
        {
            V4_LOAD: _fixture("v4_load_pt2.txt"),
            V4_BG: _Reply(body=b"bg"),
            V4_SLICE: _Reply(body=b"piece"),
        }
    )
    with pytest.raises(UnsolvableError, match="pt"):
        asyncio.run(
            GeetestV4Solver().solve_http(_task(CaptchaType.GEETEST_V4, _v4_payload()), SolverContext(http=http))
        )


def test_v4_expired_load_is_terminal() -> None:
    http = _HTTP({V4_LOAD: _fixture("v4_load_expired.txt")})
    with pytest.raises(ChallengeExpiredError, match="load"):
        asyncio.run(
            GeetestV4Solver().solve_http(_task(CaptchaType.GEETEST_V4, _v4_payload()), SolverContext(http=http))
        )
    assert len(http.calls) == 1


def test_v4_rejected_verify_is_a_captcha_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, GeetestV4Solver)
    http = _HTTP(
        {
            V4_LOAD: _fixture("v4_load.txt"),
            V4_BG: _Reply(body=b"bg"),
            V4_SLICE: _Reply(body=b"piece"),
            V4_VERIFY: _fixture("v4_verify_fail.txt"),
        }
    )
    with pytest.raises(CaptchaError, match="verify failed"):
        asyncio.run(
            GeetestV4Solver().solve_http(_task(CaptchaType.GEETEST_V4, _v4_payload()), SolverContext(http=http))
        )


def test_v4_rejects_non_slide_risk_types_before_http() -> None:
    http = _HTTP({})
    task = _task(CaptchaType.GEETEST_V4, _v4_payload(risk_type="icon"))
    with pytest.raises(UnsolvableError, match="risk_type"):
        asyncio.run(GeetestV4Solver().solve_http(task, SolverContext(http=http)))
    assert http.calls == []


def test_v4_accepts_camel_case_captcha_id() -> None:
    with pytest.raises(UnsolvableError, match=r"ctx\.http"):
        asyncio.run(
            GeetestV4Solver().solve_http(
                _task(CaptchaType.GEETEST_V4, {"captchaId": "captcha-fixture"}), SolverContext()
            )
        )


def test_v4_requires_captcha_id() -> None:
    with pytest.raises(UnsolvableError, match="captcha_id"):
        asyncio.run(GeetestV4Solver().solve_http(_task(CaptchaType.GEETEST_V4, {}), SolverContext(http=_HTTP({}))))
