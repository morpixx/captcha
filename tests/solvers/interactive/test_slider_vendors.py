"""Unit tests for the Capy / KeyCAPTCHA / Lemin interactive solvers.

No network, no DB: payload parsing, required-field validation, answer encoding,
plus one canned-response flow per vendor with the CV delegate stubbed out.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext
from captcha.solvers.interactive.capy import solver as capy
from captcha.solvers.interactive.capy.solver import CapySolver
from captcha.solvers.interactive.keycaptcha import solver as keycaptcha
from captcha.solvers.interactive.keycaptcha.solver import KeyCaptchaSolver, parse_challenge
from captcha.solvers.interactive.lemin import solver as lemin
from captcha.solvers.interactive.lemin.solver import LeminSolver


class _Reply:
    """Minimal stand-in for `captcha.adapters.transport.Response`."""

    def __init__(self, payload: Any = None, *, body: bytes = b"", status: int = 200) -> None:
        self.status = status
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
    """Canned reply per URL; every call is recorded."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _reply(self, url: str) -> _Reply:
        if url not in self.replies:
            raise AssertionError(f"unexpected HTTP call: {url}")
        reply = self.replies[url]
        return reply if isinstance(reply, _Reply) else _Reply(reply)

    async def get(self, url: str, **kwargs: Any) -> _Reply:
        self.calls.append(("GET", url, kwargs))
        return self._reply(url)

    async def post(self, url: str, **kwargs: Any) -> _Reply:
        self.calls.append(("POST", url, kwargs))
        return self._reply(url)


def _task(captcha_type: CaptchaType, payload: dict[str, Any]) -> Task:
    return Task(id=new_task_id(), type=captcha_type, payload=payload, preferred_mode=SolveMode.HTTP)


def _stub_cv(monkeypatch: pytest.MonkeyPatch, solver_cls: type) -> list[tuple[CaptchaType, dict[str, Any]]]:
    """Replace CV delegation with a canned geometric answer."""
    calls: list[tuple[CaptchaType, dict[str, Any]]] = []

    async def fake(self: Any, kind: CaptchaType, task: Task, ctx: SolverContext, payload: dict[str, Any]):
        calls.append((kind, payload))
        if kind is CaptchaType.CLICK:
            return SolverResult(coords=[(10.0, 20.0)])
        return SolverResult(offset=120.0)

    monkeypatch.setattr(solver_cls, "_solve_cv", fake)
    return calls


def _no_http(solver: Any, task: Task):
    return asyncio.run(solver.solve_http(task, SolverContext()))


# ---- metadata ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("solver_cls", "captcha_type"),
    [(CapySolver, CaptchaType.CAPY), (KeyCaptchaSolver, CaptchaType.KEYCAPTCHA), (LeminSolver, CaptchaType.LEMIN)],
)
def test_vendor_metadata(solver_cls: type, captcha_type: CaptchaType) -> None:
    assert solver_cls.info.types == (captcha_type,)
    assert solver_cls.info.http is True
    assert solver_cls.info.experimental is True


# ---- required-field validation ----------------------------------------------


@pytest.mark.parametrize("payload", [{"pageurl": "https://shop.test/f"}, {"sitekey": "PUZZLE_1"}])
def test_capy_requires_sitekey_and_pageurl(payload: dict[str, Any]) -> None:
    with pytest.raises(UnsolvableError, match="missing payload fields"):
        _no_http(CapySolver(), _task(CaptchaType.CAPY, payload))


@pytest.mark.parametrize("payload", [{"pageurl": "https://shop.test/f"}, {"sitekey": "CROPPED_1"}])
def test_lemin_requires_sitekey_and_pageurl(payload: dict[str, Any]) -> None:
    with pytest.raises(UnsolvableError, match="missing payload fields"):
        _no_http(LeminSolver(), _task(CaptchaType.LEMIN, payload))


@pytest.mark.parametrize("solver_cls", [CapySolver, LeminSolver])
def test_solvers_require_http_client(solver_cls: type) -> None:
    captcha_type = solver_cls.info.types[0]
    task = _task(captcha_type, {"sitekey": "KEY", "pageurl": "https://shop.test/f"})
    with pytest.raises(UnsolvableError, match=r"requires ctx\.http"):
        _no_http(solver_cls(), task)


def test_keycaptcha_requires_session_id() -> None:
    payload = {"data": json.dumps({"images": ["https://img.test/a.png"]})}
    with pytest.raises(UnsolvableError, match="no session id"):
        _no_http(KeyCaptchaSolver(), _task(CaptchaType.KEYCAPTCHA, payload))


def test_keycaptcha_requires_images() -> None:
    payload = {"data": json.dumps({"sess_id": "S1"})}
    with pytest.raises(UnsolvableError, match="no puzzle images"):
        _no_http(KeyCaptchaSolver(), _task(CaptchaType.KEYCAPTCHA, payload))


def test_keycaptcha_slider_needs_two_images() -> None:
    payload = {"data": json.dumps({"sess_id": "S1", "type": "slide", "images": ["https://img.test/a.png"]})}
    http = _HTTP({})
    with pytest.raises(UnsolvableError, match="one image"):
        asyncio.run(KeyCaptchaSolver().solve_http(_task(CaptchaType.KEYCAPTCHA, payload), SolverContext(http=http)))
    assert http.calls == []


# ---- payload parsing --------------------------------------------------------


def test_parse_challenge_json_string() -> None:
    blob = {"sess_id": "S1", "images": ["https://img.test/a.png", "https://img.test/b.png"]}
    assert parse_challenge({"data": json.dumps(blob)}) == blob


def test_parse_challenge_object() -> None:
    blob = {"sess_id": "S1"}
    assert parse_challenge({"challenge": blob}) is blob


def test_parse_challenge_flat_payload() -> None:
    payload = {"s_s_c_session_id": "S1", "s_s_c_user_id": "42"}
    assert parse_challenge(payload) == payload


def test_parse_challenge_rejects_broken_json() -> None:
    with pytest.raises(UnsolvableError, match="not JSON"):
        parse_challenge({"data": "{nope"})


def test_parse_challenge_rejects_non_object_json() -> None:
    with pytest.raises(UnsolvableError, match="JSON object"):
        parse_challenge({"data": "[1, 2]"})


# ---- answer encoding --------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [(0, "0"), (5, "5"), (31, "v"), (32, "10"), (120, "3o"), (405, "cl")])
def test_base32_matches_js_to_string(value: int, expected: str) -> None:
    assert capy._b32(value) == expected
    assert lemin._b32(value) == expected


def test_capy_drag_track_samples() -> None:
    assert capy.encode_drag(120, 0, points=4) == "ux0x1sx0x2qx0x3ox0x"


def test_keycaptcha_encodes_click_points() -> None:
    cv = SolverResult(coords=[(10.4, 20.6), (30.0, 40.0)])
    assert keycaptcha.encode_answer(cv, CaptchaType.CLICK) == "10,20;30,40"


def test_keycaptcha_encodes_slider_offset() -> None:
    assert keycaptcha.encode_answer(SolverResult(offset=87.6), CaptchaType.SLIDER) == "87"


@pytest.mark.parametrize(
    ("raw", "expected"), [("click", CaptchaType.CLICK), ("slide", CaptchaType.SLIDER), (None, CaptchaType.SLIDER)]
)
def test_keycaptcha_task_kind(raw: str | None, expected: CaptchaType) -> None:
    assert keycaptcha._cv_kind({"type": raw} if raw else {}) is expected


def test_keycaptcha_token_attempt_suffix() -> None:
    assert keycaptcha.assemble_token("capkey", 1) == "capkey|1"
    assert keycaptcha.assemble_token("capkey", None) == "capkey"


def test_lemin_track_shape() -> None:
    answer = lemin.encode_answer(123)
    tokens = answer.split("x")
    assert tokens[-1] == ""
    assert tokens[-3] == "3o"  # 123 px snaps to the 10 px grid at 120
    assert tokens[-2] == "cq"  # the 405 px home snaps to 410, as JS Math.round does
    assert answer.count("x") % 3 == 0


# ---- task passthrough -------------------------------------------------------


def test_headers_carry_ua_language_and_page() -> None:
    task = _task(CaptchaType.CAPY, {"pageurl": "https://shop.test/f", "user_agent": "UA/1", "lang": "ja"})
    headers = capy._headers(task, "https://shop.test/f")
    assert headers["User-Agent"] == "UA/1"
    assert headers["Accept-Language"] == "ja"
    assert headers["Referer"] == "https://shop.test/f"
    assert headers["Origin"] == "https://shop.test"


def test_keycaptcha_headers_fall_back_to_task_fields() -> None:
    task = Task(
        id=new_task_id(),
        type=CaptchaType.KEYCAPTCHA,
        payload={"pageurl": "https://shop.test/f"},
        user_agent="UA/2",
    )
    assert keycaptcha._headers(task)["User-Agent"] == "UA/2"


# ---- non-JSON responses -----------------------------------------------------


class _Garbage:
    status = 502

    def json(self) -> Any:
        raise ValueError("not json")


@pytest.mark.parametrize("module", [capy, keycaptcha, lemin])
def test_non_json_response_raises_captcha_error(module: Any) -> None:
    with pytest.raises(CaptchaError) as exc:
        module._json_object(_Garbage(), "vendor call")
    assert "not JSON" in str(exc.value)
    assert exc.value.context["status"] == 502


# ---- canned flows -----------------------------------------------------------


def test_capy_flow_builds_answer_triple(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_cv(monkeypatch, CapySolver)
    http = _HTTP(
        {
            capy.ep.GET_CHALLENGE: {
                "puzzle": {
                    "captchakey": "CAPY1",
                    "challengekey": "CHAL1",
                    "bg": "https://img.test/bg.jpg",
                    "piece": "https://img.test/piece.png",
                }
            },
            "https://img.test/bg.jpg": _Reply(body=b"bg"),
            "https://img.test/piece.png": _Reply(body=b"piece"),
            capy.ep.POST_ANSWER: {"answer": "SIGNED"},
        }
    )
    task = _task(CaptchaType.CAPY, {"sitekey": "PUZZLE_1", "pageurl": "https://shop.test/f"})
    result = asyncio.run(CapySolver().solve_http(task, SolverContext(http=http)))

    assert json.loads(result.token) == {"captchakey": "CAPY1", "challengekey": "CHAL1", "answer": "SIGNED"}
    assert result.extra["answer"] == "SIGNED"
    assert calls[0][0] is CaptchaType.SLIDER
    assert calls[0][1]["bg"] == b"bg"
    posted = next(call for call in http.calls if call[0] == "POST")[2]
    assert posted["data"]["captchakey"] == "CAPY1"
    assert posted["headers"]["Referer"] == "https://shop.test/f"


def test_keycaptcha_flow_assembles_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, KeyCaptchaSolver)
    blob = {
        "s_s_c_session_id": "SESS1",
        "s_s_c_web_server_sign": "SIGN1",
        "s_s_c_user_id": "42",
        "type": "click",
        "target": "select the cat",
        "images": ["https://img.test/a.png"],
    }
    http = _HTTP({"https://img.test/a.png": _Reply(body=b"img"), keycaptcha.ep.CHECK: {"cap_key": "f" * 40}})
    task = _task(CaptchaType.KEYCAPTCHA, {"data": json.dumps(blob), "pageurl": "https://shop.test/f"})
    result = asyncio.run(KeyCaptchaSolver().solve_http(task, SolverContext(http=http)))

    assert result.token == "f" * 40
    assert result.extra["session_id"] == "SESS1"
    posted = next(call for call in http.calls if call[0] == "POST")[2]["data"]
    assert posted["sess_id"] == "SESS1"
    assert posted["answer"] == "10,20"
    assert posted["type"] == "click"


def test_lemin_flow_returns_track_and_pv_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_cv(monkeypatch, LeminSolver)
    http = _HTTP(
        {
            lemin.ep.CHALLENGE: {
                "challenge_id": "C1",
                "background": "https://img.test/bg.png",
                "piece": "https://img.test/piece.png",
            },
            "https://img.test/bg.png": _Reply(body=b"bg"),
            "https://img.test/piece.png": _Reply(body=b"piece"),
            lemin.ep.PRE_VALIDATE: {"success": True, "data": {"pv_token": "PV"}},
        }
    )
    task = _task(CaptchaType.LEMIN, {"sitekey": "CROPPED_1", "pageurl": "https://shop.test/f"})
    result = asyncio.run(LeminSolver().solve_http(task, SolverContext(http=http)))

    body = json.loads(result.token)
    assert body["challenge_id"] == "C1"
    assert body["pv_token"] == "PV"
    assert body["answer"].endswith("0x3oxcqx")
    assert calls[0][0] is CaptchaType.SLIDER


def test_lemin_rejects_failed_pre_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, LeminSolver)
    http = _HTTP(
        {
            lemin.ep.CHALLENGE: {
                "challenge_id": "C1",
                "background": "https://img.test/bg.png",
                "piece": "https://img.test/piece.png",
            },
            "https://img.test/bg.png": _Reply(body=b"bg"),
            "https://img.test/piece.png": _Reply(body=b"piece"),
            lemin.ep.PRE_VALIDATE: {"success": False, "code": "expired"},
        }
    )
    task = _task(CaptchaType.LEMIN, {"sitekey": "CROPPED_1", "pageurl": "https://shop.test/f"})
    with pytest.raises(CaptchaError, match="rejected"):
        asyncio.run(LeminSolver().solve_http(task, SolverContext(http=http)))
