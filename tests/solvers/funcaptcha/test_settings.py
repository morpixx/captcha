from __future__ import annotations

import pytest
from helpers import SITEKEY, SURL, FakeHTTP, FakeResponse, fixture_text, make_task

from captcha.domain import CaptchaType
from captcha.domain.errors import SensorError
from captcha.solvers import SolverContext
from captcha.solvers.funcaptcha import settings


def api_js() -> FakeResponse:
    return FakeResponse(fixture_text("api_js.js").encode("utf-8"))


async def test_fetch_settings_reads_every_constant():
    http = FakeHTTP(api_js())

    cfg = await settings.fetch_settings(SolverContext(http=http), surl=SURL, public_key=SITEKEY, user_agent="UA/1")

    assert cfg.capi_version == "4.4.5"
    assert cfg.build_id == "3f8a1c92-1a2b-4c3d-8e4f-0123456789ab"
    assert cfg.enforcement_hash == "2d1c8a89671586563cb793ae2399b954"
    assert cfg.rsa_public_key.startswith("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A")
    assert cfg.bda_field == "c"

    call = http.call(0)
    assert call["method"] == "GET"
    assert call["url"] == f"{SURL}/v2/{SITEKEY}/api.js"
    assert call["headers"]["user-agent"] == "UA/1"


async def test_old_build_uses_bda_field_name():
    http = FakeHTTP(FakeResponse(fixture_text("api_js_old.js").encode("utf-8")))

    cfg = await settings.fetch_settings(SolverContext(http=http), surl=SURL, public_key=SITEKEY)

    assert cfg.capi_version == "3.9.2"
    assert cfg.build_id == ""
    assert cfg.bda_field == "bda"
    assert cfg.rsa_public_key == ""


async def test_loader_without_rsa_needs_a_pin():
    http = FakeHTTP(FakeResponse(fixture_text("api_js_old.js").encode("utf-8")))
    cfg = await settings.fetch_settings(SolverContext(http=http), surl=SURL, public_key=SITEKEY)

    with pytest.raises(SensorError) as exc:
        settings.require_rsa(cfg)

    assert exc.value.context["hint"].startswith("pin it")


async def test_pinned_rsa_wins_over_the_bundle():
    http = FakeHTTP(FakeResponse(fixture_text("api_js_old.js").encode("utf-8")))

    cfg = await settings.fetch_settings(
        SolverContext(http=http),
        surl=SURL,
        public_key=SITEKEY,
        pinned_rsa="  MIIBASE64  ",
    )

    assert cfg.rsa_public_key == "MIIBASE64"


async def test_wrapped_body_is_unwrapped():
    wrapper = '{"body": "var a=1; \\"4.4.5/enforcement.2d1c8a89671586563cb793ae2399b954.html\\""}'
    http = FakeHTTP(FakeResponse(wrapper.encode("utf-8")))

    cfg = await settings.fetch_settings(SolverContext(http=http), surl=SURL, public_key=SITEKEY)

    assert cfg.capi_version == "4.4.5"
    assert cfg.enforcement_hash == "2d1c8a89671586563cb793ae2399b954"


async def test_http_error_is_sensor_error():
    http = FakeHTTP(FakeResponse(b"", status=502))

    with pytest.raises(SensorError) as exc:
        await settings.fetch_settings(SolverContext(http=http), surl=SURL, public_key=SITEKEY)

    assert exc.value.context["status"] == 502


async def test_missing_http_client_is_config_error():
    from captcha.domain.errors import ConfigError

    with pytest.raises(ConfigError):
        await settings.fetch_settings(SolverContext(http=None), surl=SURL, public_key=SITEKEY)


def test_surl_accepts_host_path_and_scheme():
    assert settings.surl_for(make_task(payload={"surl": "client-api.arkoselabs.com/"})) == SURL
    assert settings.surl_for(make_task(payload={"surl": "https://client-api.arkoselabs.com"})) == SURL
    assert (
        settings.surl_for(
            make_task(payload={"surl": None, "funcaptchaApiJSSubdomain": "https://acme-api.arkoselabs.com/"})
        )
        == "https://acme-api.arkoselabs.com"
    )
    assert settings.surl_for(make_task(payload={"surl": "http://127.0.0.1:8080"})) == "http://127.0.0.1:8080"


def test_surl_defaults_to_client_api():
    task = make_task(payload={"surl": None})
    assert settings.surl_for(task) == settings.DEFAULT_SURL


def test_bad_surl_scheme_is_rejected():
    with pytest.raises(SensorError):
        settings.surl_for(make_task(payload={"surl": "ftp://client-api.arkoselabs.com"}))


def test_public_key_comes_from_sitekey_then_payload():
    assert settings.public_key_for(make_task()) == SITEKEY
    task = make_task(sitekey=None, payload={"publickey": None, "websitePublicKey": "PK-2"})
    assert settings.public_key_for(task) == "PK-2"


def test_missing_public_key_is_sensor_error():
    with pytest.raises(SensorError):
        settings.public_key_for(
            make_task(captcha_type=CaptchaType.FUNCAPTCHA, sitekey=None, payload={"publickey": None})
        )
