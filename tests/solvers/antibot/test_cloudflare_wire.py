"""Turnstile wire: payload parsing, bundle discovery, token shape.

The HTTP fixtures under fixtures/turnstile/ are *shape* fixtures derived from
the public 2026-08 flow (docs/reverse-2026.md §3), not captured bytes: this repo
does not commit live dumps and the tests never touch the network. They pin the
parsing rules -- versioned-bundle resolution, variant extraction, the host guard
-- against the documented shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ChallengeExpiredError, ProxyError, UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers.antibot.cloudflare.endpoints import TURNSTILE_API_JS, TURNSTILE_ORIGIN
from captcha.solvers.antibot.cloudflare.turnstile_page import token_problem, validate_token
from captcha.solvers.antibot.cloudflare.turnstile_wire import (
    bootstrap,
    discover_bundle,
    params_from_task,
    parse_variant,
)

FIXTURES = Path(__file__).parent / "fixtures" / "turnstile"
SITE_URL = "https://site.test/login"
SITEKEY = "0x4AAAAAAABkMYinukE8"
BUNDLE_URL = TURNSTILE_ORIGIN + "/turnstile/v0/b/8f1a2b3c/api.js"
WIDGET_PREFIX = TURNSTILE_ORIGIN + "/cdn-cgi/challenge-platform/h/g/turnstile/if/ov2/av0/"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, status: int = 200, body: str = "", final_url: str = "", headers: dict | None = None):
        self.status = status
        self.body = body.encode()
        self.final_url = final_url
        self.headers = list((headers or {}).items())

    @property
    def text(self) -> str:
        return self.body.decode()

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None


class FakeHTTP:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected GET {url}")


def _task(
    payload: dict | None = None, *, type_: CaptchaType = CaptchaType.TURNSTILE, url: str | None = SITE_URL
) -> Task:
    return Task(
        id=new_task_id(),
        type=type_,
        payload=payload or {},
        sitekey=SITEKEY,
        site_url=url,
    )


def _params(**payload):
    return params_from_task(_task(payload))


# ---- payload parsing --------------------------------------------------------


def test_params_defaults_from_task():
    params = _params()

    assert params.sitekey == SITEKEY
    assert params.site_url == SITE_URL
    assert params.action is None
    assert params.cdata is None
    assert params.invisible is False
    assert params.enterprise is False


def test_invisible_type_and_payload_flag():
    assert params_from_task(_task(type_=CaptchaType.TURNSTILE_INVISIBLE)).invisible is True
    assert _params(invisible="true").invisible is True
    assert _params(isInvisible=1).invisible is True


def test_enterprise_action_and_cdata_reach_the_widget():
    params = _params(action="login", cData="challenge-id", enterprise=True)

    assert (params.action, params.cdata, params.enterprise) == ("login", "challenge-id", True)


def test_chl_page_data_is_the_cdata_fallback():
    assert _params(chlPageData="page-data").cdata == "page-data"


def test_sitekey_and_url_fall_back_to_payload_aliases():
    task = Task(id=new_task_id(), type=CaptchaType.TURNSTILE, payload={"siteKey": "key-2", "pageurl": SITE_URL})

    params = params_from_task(task)

    assert params.sitekey == "key-2"
    assert params.site_url == SITE_URL


@pytest.mark.parametrize(
    ("payload", "url", "fragment"),
    [
        ({"sitekey": None}, SITE_URL, "sitekey"),
        ({"sitekey": "bad key"}, SITE_URL, "sitekey"),
        ({"sitekey": SITEKEY}, None, "websiteURL"),
    ],
)
def test_broken_payload_is_unsolvable(payload: dict, url: str | None, fragment: str):
    task = Task(id=new_task_id(), type=CaptchaType.TURNSTILE, payload=payload, sitekey=None, site_url=url)

    with pytest.raises(UnsolvableError) as exc:
        params_from_task(task)

    assert fragment in exc.value.message


def test_invalid_action_is_rejected_before_a_browser_launch():
    with pytest.raises(UnsolvableError):
        _params(action="a" * 33)
    with pytest.raises(UnsolvableError):
        _params(action="bad action")


def test_oversized_cdata_is_rejected():
    with pytest.raises(UnsolvableError):
        _params(cdata="x" * 256)


# ---- bundle discovery -------------------------------------------------------


def test_discover_bundle_prefers_the_redirect_target():
    response = FakeResponse(body="<html></html>", final_url=BUNDLE_URL)

    assert discover_bundle(response) == BUNDLE_URL


def test_discover_bundle_from_the_loader_body():
    response = FakeResponse(body=fixture("api_js.js"), final_url=TURNSTILE_API_JS)

    assert discover_bundle(response) == BUNDLE_URL


def test_discover_bundle_rejects_a_foreign_host():
    body = "s.src = 'https://evil.example/turnstile/v0/b/8f1a2b3c/api.js';"

    assert discover_bundle(FakeResponse(body=body)) is None


def test_parse_variant_reads_the_challenge_platform_segment():
    assert parse_variant(fixture("bundle_api.js")) == "g"
    assert parse_variant("nothing here") is None


# ---- bootstrap --------------------------------------------------------------


async def test_bootstrap_resolves_bundle_variant_and_widget():
    http = FakeHTTP(
        {
            TURNSTILE_API_JS: FakeResponse(200, fixture("api_js.js"), final_url=BUNDLE_URL),
            BUNDLE_URL: FakeResponse(200, fixture("bundle_api.js")),
            WIDGET_PREFIX: FakeResponse(200, fixture("widget.html")),
        }
    )

    boot = await bootstrap(http, _params())

    assert boot.bundle_url == BUNDLE_URL
    assert boot.variant == "g"
    assert boot.widget_url.startswith(WIDGET_PREFIX)
    assert boot.widget_status == 200
    assert boot.error is None
    assert [url for url, _ in http.calls] == [TURNSTILE_API_JS, BUNDLE_URL, boot.widget_url]

    widget_call = http.calls[2][1]
    assert widget_call["headers"]["Referer"] == SITE_URL
    assert widget_call["headers"]["Sec-Fetch-Dest"] == "iframe"


async def test_bootstrap_records_an_antibot_verdict_instead_of_failing():
    http = FakeHTTP(
        {
            TURNSTILE_API_JS: ChallengeExpiredError("antibot verdict: HTTP 403"),
            WIDGET_PREFIX.replace("/g/", "/b/"): FakeResponse(200, fixture("widget.html")),
        }
    )

    boot = await bootstrap(http, _params())

    assert boot.bundle_url is None
    assert boot.variant == "b"
    assert boot.error is not None
    assert "ERR_CHALLENGE_EXPIRED" in boot.error
    assert boot.widget_status == 200


async def test_bootstrap_does_not_swallow_a_proxy_failure():
    http = FakeHTTP({TURNSTILE_API_JS: ProxyError("tunnel down")})

    with pytest.raises(ProxyError):
        await bootstrap(http, _params())


async def test_bootstrap_survives_a_transport_exception():
    http = FakeHTTP(
        {TURNSTILE_API_JS: ConnectionError("reset"), WIDGET_PREFIX.replace("/g/", "/b/"): FakeResponse(200)}
    )

    boot = await bootstrap(http, _params())

    assert boot.bundle_url is None
    assert boot.error is not None and "ConnectionError" in boot.error


# ---- token shape ------------------------------------------------------------


@pytest.mark.parametrize("token", ["0." + "A" * 80, "1." + "b-_." * 20, "0." + "Z" * 20])
def test_validate_token_accepts_widget_shape(token: str):
    assert validate_token(token, sitekey=SITEKEY) == token


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("", "empty token"),
        ("   ", "empty token"),
        (None, "empty token"),
        (SITEKEY, "sitekey"),
        ("0.short", "too short"),
        ("0." + "A" * 30 + " B", "whitespace"),
        ("<html>" + "A" * 40 + "</html>", "shape"),
        ("Y0." + "A" * 40, "shape"),
    ],
)
def test_validate_token_rejects_placeholders(value, fragment: str):
    assert fragment in (token_problem(value, sitekey=SITEKEY) or "")
    with pytest.raises(UnsolvableError):
        validate_token(value, sitekey=SITEKEY)


def test_validate_token_unwraps_a_response_object():
    assert validate_token({"token": "0." + "Q" * 40}, sitekey=SITEKEY) == "0." + "Q" * 40
