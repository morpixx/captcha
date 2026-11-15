from __future__ import annotations

import pytest
from helpers import (
    SITEKEY,
    SURL,
    FakeHTTP,
    FakeResponse,
    StubRegistry,
    fixture,
    fixture_text,
    make_task,
    stub_class,
)

from captcha.domain import CaptchaType, SolverResult
from captcha.domain.errors import (
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
)
from captcha.solvers import SolverContext
from captcha.solvers.funcaptcha import settings
from captcha.solvers.funcaptcha.funcaptcha import FunCaptchaEnterpriseSolver, FunCaptchaSolver


def api_js(status: int = 200) -> FakeResponse:
    if status != 200:
        return FakeResponse(b"", status=status)
    return FakeResponse(fixture_text("api_js.js").encode("utf-8"))


def grid_registry(indices: list[int] | None = None) -> StubRegistry:
    return StubRegistry([stub_class("funcaptcha_grid", SolverResult(indices=indices or [0, 2]))])


def rotate_registry() -> StubRegistry:
    return StubRegistry([stub_class("rotate", SolverResult(angle=90.0))])


async def solve(http: FakeHTTP, *, task=None, registry: StubRegistry | None = None, solver_cls=FunCaptchaSolver):
    extra = {"solver_registry": registry} if registry is not None else {}
    ctx = SolverContext(http=http, extra=extra)
    return await solver_cls().solve_http(task or make_task(), ctx)


# --- suppressed sessions -----------------------------------------------------


async def test_suppressed_token_is_the_answer():
    http = FakeHTTP(api_js(), FakeResponse(fixture("gt2_suppressed.json")))

    result = await solve(http, registry=grid_registry())

    assert result.token == fixture("gt2_suppressed.json")["token"]
    assert result.extra["path"] == "suppressed"
    assert result.extra["bda_source"] == "sensor"
    assert result.extra["userAgent"] == "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Test/1.0"
    assert len(http.calls) == 2  # api.js + token endpoint, no challenge fetch


async def test_token_endpoint_without_a_token_is_challenge_expired():
    http = FakeHTTP(api_js(), FakeResponse({"error": "bad bda"}))

    with pytest.raises(ChallengeExpiredError) as exc:
        await solve(http, registry=grid_registry())

    assert exc.value.context["surl"] == SURL
    assert exc.value.context["public_key"] == SITEKEY


async def test_token_without_a_game_returns_the_token():
    http = FakeHTTP(api_js(), FakeResponse(fixture("gt2_session.json")), FakeResponse({}))

    result = await solve(http, registry=grid_registry())

    assert result.token == fixture("gt2_session.json")["token"]
    assert result.extra["path"] == "token-only"
    assert len(http.calls) == 3  # api.js + token + challenge


# --- game rounds -------------------------------------------------------------


async def test_grid_round_is_solved_and_the_answer_is_submitted():
    ca = fixture("ca_answered.json")
    http = FakeHTTP(api_js(), FakeResponse(fixture("gt2_session.json")), FakeResponse(fixture("gfct_grid.json")), FakeResponse(ca))

    result = await solve(http, registry=grid_registry())

    assert result.token == ca["token"]
    assert result.extra["variant"] == "grid"
    assert result.extra["rounds"] == 1
    assert result.extra["game_type"] == "image_label_binary"

    answer_call = http.call(3)
    assert answer_call["url"] == f"{SURL}/fc/ca/"
    assert "&guess=%5B0%2C2%5D" in answer_call["data"]
    assert "&game_token=713184db7d62a8982.4535671902" in answer_call["data"]


async def test_rotate_round_is_solved_with_a_geometry_answer():
    ca = fixture("ca_answered.json")
    http = FakeHTTP(
        api_js(),
        FakeResponse(fixture("gt2_session.json")),
        FakeResponse(fixture("gfct_rotate.json")),
        FakeResponse(ca),
    )

    result = await solve(http, registry=rotate_registry())

    assert result.extra["variant"] == "rotate"
    assert "&guess=%5B90%5D" in http.call(3)["data"]


async def test_match_round_goes_through_the_classification_head():
    ca = fixture("ca_answered.json")
    challenge = {**fixture("gfct_grid.json"), "game_type": "match_key"}
    http = FakeHTTP(api_js(), FakeResponse(fixture("gt2_session.json")), FakeResponse(challenge), FakeResponse(ca))

    result = await solve(http, registry=grid_registry(indices=[4]))

    assert result.extra["variant"] == "match"
    assert result.extra["game_type"] == "match_key"
    assert "&guess=%5B4%5D" in http.call(3)["data"]


async def test_a_follow_up_round_is_played_before_answering():
    ca = fixture("ca_answered.json")
    http = FakeHTTP(
        api_js(),
        FakeResponse(fixture("gt2_session.json")),
        FakeResponse(fixture("gfct_grid.json")),
        FakeResponse(fixture("ca_round2.json")),
        FakeResponse(ca),
    )

    result = await solve(http, registry=grid_registry())

    assert result.extra["rounds"] == 2
    assert result.token == ca["token"]
    assert "&game_token=813184db7d62a8982.5535671902" in http.call(4)["data"]


async def test_rejected_answer_without_a_follow_up_is_challenge_expired():
    http = FakeHTTP(
        api_js(),
        FakeResponse(fixture("gt2_session.json")),
        FakeResponse(fixture("gfct_grid.json")),
        FakeResponse(fixture("ca_rejected.json")),
    )

    with pytest.raises(ChallengeExpiredError) as exc:
        await solve(http, registry=grid_registry())

    assert exc.value.context["round"] == 1


async def test_unknown_game_escalates_to_the_browser_lane():
    challenge = {"challengeID": "abc", "_challenge_imgs": ["aGVsbG8="], "game_type": "mystery_game"}
    http = FakeHTTP(api_js(), FakeResponse(fixture("gt2_session.json")), FakeResponse(challenge))

    with pytest.raises(NeedsBrowserError) as exc:
        await solve(http, registry=grid_registry())

    assert exc.value.context["game_type"] == "mystery_game"


async def test_missing_variant_solver_escalates_to_the_browser_lane():
    challenge = {**fixture("gfct_grid.json"), "game_type": "frontier_game"}
    challenge.pop("instruction")
    http = FakeHTTP(api_js(), FakeResponse(fixture("gt2_session.json")), FakeResponse(challenge))

    with pytest.raises(NeedsBrowserError):
        await solve(http, registry=StubRegistry([]))


# --- payload plumbing --------------------------------------------------------


async def test_recorded_bda_is_used_and_no_sensor_is_sampled():
    http = FakeHTTP(api_js(), FakeResponse(fixture("gt2_suppressed.json")))
    task = make_task(payload={"bda_plaintext": '{"api_type":"js","recorded":1}'})

    result = await solve(http, task=task, registry=grid_registry())

    assert result.extra["bda_source"] == "recorded"
    body = http.call(1)["data"]
    assert body.startswith("c=")


async def test_user_agent_from_the_payload_reaches_the_wire():
    http = FakeHTTP(api_js(), FakeResponse(fixture("gt2_suppressed.json")))
    task = make_task(payload={"userAgent": "Mozilla/5.0 PayloadUA"}, user_agent=None)

    result = await solve(http, task=task, registry=grid_registry())

    assert result.extra["userAgent"] == "Mozilla/5.0 PayloadUA"
    assert http.call(0)["headers"]["user-agent"] == "Mozilla/5.0 PayloadUA"
    assert "userbrowser=Mozilla/5.0%20PayloadUA" in http.call(1)["data"]


async def test_enterprise_solver_uses_the_customer_subdomain():
    http = FakeHTTP(api_js(), FakeResponse(fixture("gt2_suppressed.json")))
    task = make_task(
        captcha_type=CaptchaType.FUNCAPTCHA_ENTERPRISE,
        payload={"surl": "https://acme-api.arkoselabs.com"},
    )

    result = await solve(http, task=task, registry=grid_registry(), solver_cls=FunCaptchaEnterpriseSolver)

    assert result.token
    assert http.call(0)["url"] == f"https://acme-api.arkoselabs.com/v2/{SITEKEY}/api.js"
    assert http.call(1)["url"] == f"https://acme-api.arkoselabs.com/fc/gt2/public_key/{SITEKEY}"


async def test_missing_http_client_is_config_error():
    with pytest.raises(ConfigError):
        await FunCaptchaSolver().solve_http(make_task(), SolverContext(http=None))


async def test_api_js_failure_is_sensor_error():
    from captcha.domain.errors import SensorError

    http = FakeHTTP(api_js(status=502))

    with pytest.raises(SensorError):
        await solve(http, registry=grid_registry())


def test_both_solver_classes_are_registered_and_browser_capable():
    assert FunCaptchaSolver.info.browser is True
    assert FunCaptchaEnterpriseSolver.info.browser is True
    assert FunCaptchaSolver.info.types == (CaptchaType.FUNCAPTCHA,)
    assert FunCaptchaEnterpriseSolver.info.types == (CaptchaType.FUNCAPTCHA_ENTERPRISE,)


def test_enterprise_defaults_to_the_shared_host():
    assert settings.surl_for(make_task(captcha_type=CaptchaType.FUNCAPTCHA_ENTERPRISE, payload={"surl": None})) == (
        settings.DEFAULT_SURL
    )
