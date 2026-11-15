"""Pure tests for the legacy 2captcha mapping and answer formatting.

No FastAPI client, no DB, no redis: legacy_map is deliberately I/O-free, so the
whole protocol surface is exercised by calling it directly.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from captcha.domain.enums import CaptchaType
from captcha.services.gateway.legacy_map import (
    ERROR_BAD_PARAMETERS,
    ERROR_NO_SUCH_METHOD,
    LegacyError,
    format_solution_string,
    parse_in_request,
)

PAGE = "https://example.com/login"


@pytest.mark.parametrize(
    ("method", "params", "expected_type", "expected_sitekey"),
    [
        ("userrecaptcha", {"googlekey": "gk", "pageurl": PAGE}, CaptchaType.RECAPTCHA_V2, "gk"),
        (
            "userrecaptcha",
            {"googlekey": "gk", "pageurl": PAGE, "invisible": "1"},
            CaptchaType.RECAPTCHA_V2_INVISIBLE,
            "gk",
        ),
        (
            "userrecaptcha",
            {"googlekey": "gk", "pageurl": PAGE, "enterprise": "1"},
            CaptchaType.RECAPTCHA_V2_ENTERPRISE,
            "gk",
        ),
        ("hcaptcha", {"sitekey": "sk", "pageurl": PAGE}, CaptchaType.HCAPTCHA, "sk"),
        ("turnstile", {"sitekey": "ts", "pageurl": PAGE}, CaptchaType.TURNSTILE, "ts"),
        (
            "funcaptcha",
            {"publickey": "pk", "surl": "arkose.example.com", "pageurl": PAGE},
            CaptchaType.FUNCAPTCHA,
            "pk",
        ),
        (
            "geetest",
            {"gt": "gt", "challenge": "ch", "api_server": "api.geetest.com", "pageurl": PAGE},
            CaptchaType.GEETEST_V3,
            "gt",
        ),
        ("textcaptcha", {"textcaptcha": "what day is it?"}, CaptchaType.TEXT, None),
        ("base64", {"body": "aW1n"}, CaptchaType.IMAGE_TO_TEXT, None),
        ("rotatecaptcha", {"body": "aW1n", "angle": "40"}, CaptchaType.ROTATE, None),
        ("clickcaptcha", {"body": "aW1n"}, CaptchaType.CLICK, None),
        ("coordinates", {"body": "aW1n"}, CaptchaType.COORDINATES, None),
        ("grid", {"body": "aW1n", "rows": "3", "cols": "4"}, CaptchaType.CLASSIFICATION_GRID, None),
        (
            "amazon_waf",
            {"pageurl": PAGE, "data": json.dumps({"sitekey": "aw", "iv": "iv", "context": "ctx"})},
            CaptchaType.AWSWAF,
            "aw",
        ),
        ("audio", {"body": "YXVkaW8", "lang": "en"}, CaptchaType.AUDIO, None),
        ("friendly", {"sitekey": "fs", "pageurl": PAGE}, CaptchaType.FRIENDLY, "fs"),
        (
            "altcha",
            {"pageurl": PAGE, "challenge_json": json.dumps({"challenge": "c", "salt": "s", "max": 5})},
            CaptchaType.ALTCHA,
            None,
        ),
    ],
)
def test_method_mapping(
    method: str,
    params: dict[str, str],
    expected_type: CaptchaType,
    expected_sitekey: str | None,
) -> None:
    request = parse_in_request({"method": method, **params})
    assert request.captcha_type is expected_type
    assert request.sitekey == expected_sitekey


def test_userrecaptcha_payload_keeps_legacy_fields() -> None:
    request = parse_in_request(
        {
            "method": "userrecaptcha",
            "googlekey": "gk",
            "pageurl": PAGE,
            "action": "login",
            "cdata": "extra",
            "userAgent": "UA",
            "proxy": "host:1",
            "proxytypeaddr": "HTTP",
            "lang": "en",
        }
    )
    payload = request.payload
    assert request.site_url == PAGE
    assert request.user_agent == "UA"
    assert payload["googlekey"] == "gk"
    assert payload["action"] == "login"
    assert payload["cdata"] == "extra"
    assert payload["pageurl"] == PAGE
    assert payload["proxy"] == "host:1"
    assert payload["proxy_type"] == "HTTP"
    assert payload["lang"] == "en"


def test_amazon_waf_accepts_flat_fields_without_data() -> None:
    request = parse_in_request(
        {
            "method": "amazon_waf",
            "pageurl": PAGE,
            "sitekey": "aw",
            "iv": "iv",
            "context": "ctx",
        }
    )
    assert request.payload["sitekey"] == "aw"
    assert request.payload["iv"] == "iv"
    assert request.payload["context"] == "ctx"


def test_grid_reads_the_legacy_grid_size_names() -> None:
    request = parse_in_request(
        {"method": "grid", "body": "aW1n", "recaptcharows": "4", "recaptchacols": "4"}
    )
    assert request.payload["rows"] == 4
    assert request.payload["cols"] == 4


def test_unknown_method_is_no_such_method() -> None:
    with pytest.raises(LegacyError) as caught:
        parse_in_request({"method": "definitely_not_a_method"})
    assert caught.value.code == ERROR_NO_SUCH_METHOD


def test_missing_method_is_no_such_method() -> None:
    with pytest.raises(LegacyError) as caught:
        parse_in_request({})
    assert caught.value.code == ERROR_NO_SUCH_METHOD


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("userrecaptcha", {"pageurl": PAGE}),
        ("hcaptcha", {"pageurl": PAGE}),
        ("turnstile", {"sitekey": "sk"}),
        ("funcaptcha", {"pageurl": PAGE}),
        ("geetest", {"gt": "gt", "pageurl": PAGE}),
        ("base64", {"body": ""}),
        ("rotatecaptcha", {}),
        ("grid", {}),
        ("audio", {}),
        ("friendly", {"sitekey": "fs"}),
        ("altcha", {"pageurl": PAGE}),
    ],
)
def test_missing_required_field_is_bad_parameters(method: str, params: dict[str, str]) -> None:
    with pytest.raises(LegacyError) as caught:
        parse_in_request({"method": method, **params})
    assert caught.value.code == ERROR_BAD_PARAMETERS


def test_bad_json_and_bad_grid_size_are_bad_parameters() -> None:
    with pytest.raises(LegacyError) as caught:
        parse_in_request({"method": "amazon_waf", "pageurl": PAGE, "data": "{not json"})
    assert caught.value.code == ERROR_BAD_PARAMETERS

    with pytest.raises(LegacyError) as caught:
        parse_in_request({"method": "grid", "body": "aW1n", "rows": "three"})
    assert caught.value.code == ERROR_BAD_PARAMETERS


@pytest.mark.parametrize(
    ("captcha_type", "solution", "expected"),
    [
        (CaptchaType.RECAPTCHA_V2, {"gRecaptchaResponse": "token"}, "token"),
        (CaptchaType.HCAPTCHA, {"gRecaptchaResponse": "token"}, "token"),
        (CaptchaType.TURNSTILE, {"token": "token"}, "token"),
        (CaptchaType.TEXT, {"text": "answer"}, "answer"),
        (CaptchaType.ROTATE, {"angle": 90.0}, "90"),
        (CaptchaType.SLIDER, {"offset": 42}, "42"),
        (CaptchaType.CLICK, {"coordinates": [[83, 22], [187, 39.5]]}, "click:83,22;187,39.5"),
        (
            CaptchaType.COORDINATES,
            {"coordinates": [[39, 59], [252, 72]]},
            "coordinate:x=39,y=59;x=252,y=72",
        ),
        (CaptchaType.CLASSIFICATION_GRID, {"objects": [2, 7, 8]}, "click:3/8/9"),
        (CaptchaType.FRIENDLY, {"data": {"token": "friendly-token"}}, "friendly-token"),
        (CaptchaType.ALTCHA, {"data": {"token": "altcha-token", "number": 3}}, "altcha-token"),
    ],
)
def test_format_solution_string(
    captcha_type: CaptchaType,
    solution: dict[str, Any],
    expected: str,
) -> None:
    assert format_solution_string(captcha_type, solution) == expected


def test_geetest_answer_is_compact_json() -> None:
    answer = format_solution_string(
        CaptchaType.GEETEST_V3,
        {"challenge": "c", "validate": "v", "seccode": "s|jordan"},
    )
    assert json.loads(answer) == {"challenge": "c", "validate": "v", "seccode": "s|jordan"}


def test_awswaf_answer_keeps_both_token_shapes() -> None:
    answer = format_solution_string(
        CaptchaType.AWSWAF,
        {"captcha_voucher": "voucher", "existing_token": "existing"},
    )
    assert json.loads(answer) == {"captcha_voucher": "voucher", "existing_token": "existing"}


def test_empty_solution_formats_to_empty_string() -> None:
    assert format_solution_string(CaptchaType.RECAPTCHA_V2, {}) == ""
    assert format_solution_string(CaptchaType.CLASSIFICATION_GRID, {"objects": []}) == ""
