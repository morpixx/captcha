"""VK: legacy image OCR, the explicit kaleidoscope rejection, and the VK ID browser lane.

No network and no browser. The image branch is replayed bytes + a stubbed OCR
delegate; the token branch runs against a fake page whose fetch hook already saw
a `success_token` response — the same shape `wire.TOKEN_HOOK_JS` stores.
"""

from __future__ import annotations

from typing import Any

import msgspec
import pytest
from tests.solvers.interactive._fakes import HTTP, FakePage, FakeProvider, Reply, make_task

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import (
    CaptchaError,
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    UnsolvableError,
)
from captcha.domain.solution import build_solution
from captcha.solvers import SolverContext
from captcha.solvers.interactive.vk import endpoints as ep
from captcha.solvers.interactive.vk.solver import VKSolver

REDIRECT = "https://id.vk.com/not_robot_captcha?domain=vk.com&session_token=abc&variant=popup"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"
TOKEN = "eyJhbGciOiJFUzI1NiJ9.success"


def _task(payload: dict[str, Any] | None = None, **kwargs: Any) -> Task:
    return make_task(CaptchaType.VK, payload, **kwargs)


def _stub_ocr(monkeypatch: pytest.MonkeyPatch, text: str = "a7b9c2") -> list[tuple[CaptchaType, dict[str, Any]]]:
    calls: list[tuple[CaptchaType, dict[str, Any]]] = []

    async def fake(self: Any, kind: CaptchaType, task: Task, ctx: SolverContext, payload: dict[str, Any]):
        calls.append((kind, payload))
        return SolverResult(text=text)

    monkeypatch.setattr(VKSolver, "_solve_cv", fake)
    return calls


def test_metadata() -> None:
    assert VKSolver.info.types == (CaptchaType.VK,)
    assert VKSolver.info.http is True
    assert VKSolver.info.browser is True


# ---- legacy image -----------------------------------------------------------


async def test_image_flow_returns_ocr_text(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_ocr(monkeypatch)
    http = HTTP({ep.CAPTCHA_IMAGE: Reply(body=b"\xff\xd8jpeg")})
    task = _task({"sid": "S-1"}, user_agent=UA)

    result = await VKSolver().solve_http(task, SolverContext(http=http))

    assert result.token == "a7b9c2"
    assert result.text == "a7b9c2"
    assert result.extra == {"userAgent": UA}
    assert http.call("GET", ep.CAPTCHA_IMAGE)["params"] == {"sid": "S-1"}
    assert calls[0][0] is CaptchaType.IMAGE_TO_TEXT
    assert calls[0][1]["image"] == b"\xff\xd8jpeg"
    assert build_solution(CaptchaType.VK, msgspec.structs.asdict(result))["token"] == "a7b9c2"


async def test_captcha_sid_alias_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_ocr(monkeypatch)
    http = HTTP({ep.CAPTCHA_IMAGE: Reply(body=b"\xff\xd8jpeg")})
    result = await VKSolver().solve_http(_task({"captcha_sid": "42"}), SolverContext(http=http))
    assert result.token == "a7b9c2"
    assert http.call("GET", ep.CAPTCHA_IMAGE)["params"] == {"sid": "42"}


async def test_captcha_img_url_is_fetched_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_ocr(monkeypatch)
    image_url = "https://api.vk.com/captcha.php?sid=S-1&img=1"
    http = HTTP({image_url: Reply(body=b"\xff\xd8jpeg")})

    result = await VKSolver().solve_http(_task({"captcha_img": image_url}), SolverContext(http=http))

    assert result.token == "a7b9c2"
    assert http.call("GET", image_url) == {}


async def test_image_error_object_is_retryable() -> None:
    http = HTTP({ep.CAPTCHA_IMAGE: Reply({"error": {"error_code": 14, "error_msg": "Captcha needed"}})})
    with pytest.raises(ChallengeExpiredError, match="error object"):
        await VKSolver().solve_http(_task({"sid": "S-1"}), SolverContext(http=http))


async def test_empty_image_is_retryable() -> None:
    http = HTTP({ep.CAPTCHA_IMAGE: Reply(body=b"")})
    with pytest.raises(ChallengeExpiredError, match="empty body"):
        await VKSolver().solve_http(_task({"sid": "S-1"}), SolverContext(http=http))


async def test_empty_ocr_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_ocr(monkeypatch, text="  ")
    http = HTTP({ep.CAPTCHA_IMAGE: Reply(body=b"\xff\xd8jpeg")})
    with pytest.raises(CaptchaError, match="no text"):
        await VKSolver().solve_http(_task({"sid": "S-1"}), SolverContext(http=http))


async def test_image_mode_without_http_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        await VKSolver().solve_http(_task({"sid": "S-1"}), SolverContext())


async def test_missing_sid_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="sid"):
        await VKSolver().solve_http(_task({"mode": "image"}), SolverContext(http=HTTP({})))


# ---- steps / token ----------------------------------------------------------


async def test_steps_variant_is_rejected_explicitly() -> None:
    payload = {"image": "AAAA", "steps": [1, 2, 3]}
    with pytest.raises(UnsolvableError, match="kaleidoscope"):
        await VKSolver().solve_http(_task(payload), SolverContext())


async def test_token_mode_without_browser_escalates() -> None:
    with pytest.raises(NeedsBrowserError, match="success_token") as exc:
        await VKSolver().solve_http(_task({"redirectUri": REDIRECT}), SolverContext())
    assert exc.value.context["url"] == REDIRECT
    assert exc.value.context["mode"] == "token"


async def test_token_mode_missing_url_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="redirectUri"):
        await VKSolver().solve_http(_task({}), SolverContext())


# ---- browser lane -----------------------------------------------------------


async def _solve_browser(page: FakePage, task: Task | None = None, provider: FakeProvider | None = None):
    return await VKSolver().solve_browser(
        task or _task({"redirectUri": REDIRECT}),
        SolverContext(browser=provider or FakeProvider(page), extra={"timeout_s": 0.1}),
    )


async def test_browser_reads_the_hooked_success_token() -> None:
    page = FakePage(
        {
            "__vkCaptchaTokens": TOKEN,
            "location.hostname": "id.vk.com",
        },
        ua=UA,
    )
    provider = FakeProvider(page)

    result = await _solve_browser(page, provider=provider)

    assert result.token == TOKEN
    assert result.extra == {"userAgent": UA}
    assert result.solve_mode.value == "browser"
    assert provider.sessions[0].gotos[0][0] == REDIRECT
    assert any("XMLHttpRequest" in script for script in provider.sessions[0].context.init_scripts)
    assert page.clicks == []  # the hook already had the answer


async def test_browser_falls_back_to_the_hidden_field() -> None:
    page = FakePage({'input[name="success_token"]': TOKEN, "location.hostname": "id.vk.com"}, ua=UA)
    result = await _solve_browser(page)
    assert result.token == TOKEN


async def test_browser_clicks_the_checkbox_before_giving_up() -> None:
    page = FakePage({}, ua=UA)
    with pytest.raises(ChallengeExpiredError, match="no success_token"):
        await _solve_browser(page)
    assert page.clicks


async def test_browser_wrong_origin_is_unsolvable() -> None:
    page = FakePage(
        {"__vkCaptchaTokens": TOKEN, "location.hostname": "evil.test"},
        ua=UA,
    )
    with pytest.raises(UnsolvableError, match="redirectUri host"):
        await _solve_browser(page)


async def test_solution_projects_to_token_and_ua() -> None:
    page = FakePage(
        {"__vkCaptchaTokens": TOKEN, "location.hostname": "id.vk.com"},
        ua=UA,
    )
    result = await _solve_browser(page)
    assert build_solution(CaptchaType.VK, msgspec.structs.asdict(result)) == {
        "token": TOKEN,
        "userAgent": UA,
    }
