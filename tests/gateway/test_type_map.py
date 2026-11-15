"""Pure tests for the official task-name table and the wire-payload folding.

type_map is I/O-free by design, so the whole protocol surface (type aliases,
proxy composition, hCaptcha extras) is exercised by calling it directly. Every
fixture below is shaped like the payloads in the public 2captcha/capsolver docs
and in the clients' issue trackers, not like our own internals.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from captcha.domain.enums import CaptchaType
from captcha.services.gateway.type_map import (
    TYPE_ALIASES,
    ProxyFormatError,
    UnknownCaptchaType,
    build_proxy_url,
    normalize_cookies,
    normalize_task_payload,
    resolve_captcha_type,
)

PAGE = "https://example.com/login"
SITEKEY = "10000000-ffff-ffff-ffff-000000000001"

#: The names the contract requires us to accept verbatim.
REQUIRED_NAMES = (
    "HCaptchaTask",
    "HCaptchaTaskProxyless",
    "HCaptchaTaskEnterprise",
    "HCaptchaTaskEnterpriseProxyless",
    "RecaptchaV2Task",
    "RecaptchaV2TaskProxyless",
    "RecaptchaV2EnterpriseTask",
    "RecaptchaV2EnterpriseTaskProxyless",
    "RecaptchaV3Task",
    "RecaptchaV3TaskProxyless",
    "TurnstileTask",
    "TurnstileTaskProxyless",
    "FunCaptchaTask",
    "FunCaptchaTaskProxyless",
    "GeeTestTask",
    "GeeTestTaskProxyless",
    "ImageToTextTask",
)


# ---------------------------------------------------------------------------
# type names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("HCaptchaTask", CaptchaType.HCAPTCHA),
        ("HCaptchaTaskProxyless", CaptchaType.HCAPTCHA),
        ("HCaptchaTaskProxyLess", CaptchaType.HCAPTCHA),
        ("HCaptchaTaskEnterprise", CaptchaType.HCAPTCHA_ENTERPRISE),
        ("HCaptchaTaskEnterpriseProxyless", CaptchaType.HCAPTCHA_ENTERPRISE),
        ("HCaptchaEnterpriseTaskProxyLess", CaptchaType.HCAPTCHA_ENTERPRISE),
        ("RecaptchaV2Task", CaptchaType.RECAPTCHA_V2),
        ("RecaptchaV2TaskProxyless", CaptchaType.RECAPTCHA_V2),
        ("ReCaptchaV2TaskProxyLess", CaptchaType.RECAPTCHA_V2),
        ("RecaptchaV2EnterpriseTask", CaptchaType.RECAPTCHA_V2_ENTERPRISE),
        ("RecaptchaV2EnterpriseTaskProxyless", CaptchaType.RECAPTCHA_V2_ENTERPRISE),
        ("ReCaptchaV3TaskProxyLess", CaptchaType.RECAPTCHA_V3),
        ("RecaptchaV3EnterpriseTaskProxyless", CaptchaType.RECAPTCHA_V3_ENTERPRISE),
        ("TurnstileTask", CaptchaType.TURNSTILE),
        ("TurnstileTaskProxyless", CaptchaType.TURNSTILE),
        ("AntiTurnstileTaskProxyLess", CaptchaType.TURNSTILE),
        ("FunCaptchaTask", CaptchaType.FUNCAPTCHA),
        ("FunCaptchaTaskProxyless", CaptchaType.FUNCAPTCHA),
        ("GeeTestTask", CaptchaType.GEETEST_V3),
        ("GeeTestTaskProxyless", CaptchaType.GEETEST_V3),
        ("ImageToTextTask", CaptchaType.IMAGE_TO_TEXT),
    ],
)
def test_official_names_resolve(wire: str, expected: CaptchaType) -> None:
    assert resolve_captcha_type(wire) is expected


@pytest.mark.parametrize("member", list(CaptchaType))
def test_every_internal_snake_case_name_resolves(member: CaptchaType) -> None:
    assert resolve_captcha_type(member.value) is member


@pytest.mark.parametrize("wire", ["hcaptcha_task_proxyless", "HCAPTCHA-TASK-PROXYLESS", " hCaptchaTaskProxyLess "])
def test_case_and_separators_are_ignored(wire: str) -> None:
    assert resolve_captcha_type(wire) is CaptchaType.HCAPTCHA


def test_type_instances_pass_through() -> None:
    assert resolve_captcha_type(CaptchaType.TEXT) is CaptchaType.TEXT


def test_alias_table_is_well_formed() -> None:
    assert TYPE_ALIASES
    assert all(isinstance(value, CaptchaType) for value in TYPE_ALIASES.values())
    assert set(REQUIRED_NAMES) <= set(TYPE_ALIASES)


@pytest.mark.parametrize("wire", ["SolvemediaTask", "hcaptcha_typo", ""])
def test_unknown_names_raise(wire: str) -> None:
    with pytest.raises(UnknownCaptchaType):
        resolve_captcha_type(wire)


# ---------------------------------------------------------------------------
# proxy composition
# ---------------------------------------------------------------------------


def test_flat_fields_become_one_url_and_are_removed() -> None:
    task = normalize_task_payload(
        {
            "type": "HCaptchaTask",
            "websiteURL": PAGE,
            "websiteKey": SITEKEY,
            "proxyType": "socks5",
            "proxyAddress": "203.0.113.10",
            "proxyPort": "1080",
            "proxyLogin": "user@example.com",
            "proxyPassword": "p@ss:word",
        }
    )
    assert task["type"] is CaptchaType.HCAPTCHA
    assert task["proxy"] == "socks5://user%40example.com:p%40ss%3Aword@203.0.113.10:1080"
    for gone in ("proxyType", "proxyAddress", "proxyPort", "proxyLogin", "proxyPassword"):
        assert gone not in task


@pytest.mark.parametrize(("wire_type", "scheme"), [("HTTP", "http"), ("https", "https"), ("SOCKS4", "socks4"), ("socks5", "socks5")])
def test_all_documented_schemes(wire_type: str, scheme: str) -> None:
    url = build_proxy_url(proxy_type=wire_type, address="proxy.example", port=3128, login="u", password="p")
    assert url == f"{scheme}://u:p@proxy.example:3128"


def test_address_may_carry_its_own_port() -> None:
    assert build_proxy_url(proxy_type="socks4", address="198.51.100.7:4145") == "socks4://198.51.100.7:4145"


def test_explicit_port_wins_over_embedded_one() -> None:
    assert build_proxy_url(proxy_type="http", address="10.0.0.5:8080", port="3128") == "http://10.0.0.5:3128"


def test_proxyless_is_not_an_error() -> None:
    assert build_proxy_url(proxy_type=None, address=None, port=None) is None
    assert normalize_task_payload({"type": "HCaptchaTaskProxyless"}) == {"type": CaptchaType.HCAPTCHA}


def test_ipv6_address_is_bracketed() -> None:
    assert build_proxy_url(proxy_type="socks5", address="2001:db8::1", port=1080) == "socks5://[2001:db8::1]:1080"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://user:pass@proxy.example:3128", "http://user:pass@proxy.example:3128"),
        ("HTTP://proxy.example:3128", "http://proxy.example:3128"),
        ("user:pass@proxy.example:3128", "http://user:pass@proxy.example:3128"),
        ("socks5://u:p@proxy.example:1080", "socks5://u:p@proxy.example:1080"),
    ],
)
def test_proxy_string_is_canonicalised(raw: str, expected: str) -> None:
    assert normalize_task_payload({"type": "hcaptcha", "proxy": raw})["proxy"] == expected


def test_explicit_proxy_string_wins_over_flat_fields() -> None:
    task = normalize_task_payload(
        {
            "type": "hcaptcha",
            "proxy": "u:p@proxy.example:1080",
            "proxyType": "socks5",
            "proxyAddress": "203.0.113.99",
            "proxyPort": 1,
        }
    )
    assert task["proxy"] == "socks5://u:p@proxy.example:1080"
    assert "proxyAddress" not in task


@pytest.mark.parametrize(
    "proxy_fields",
    [
        {"proxyType": "ftp", "proxyAddress": "10.0.0.5", "proxyPort": 80},
        {"proxyType": "http", "proxyAddress": "10.0.0.5", "proxyPort": "70000"},
        {"proxyType": "http", "proxyAddress": "10.0.0.5", "proxyPort": "eighty"},
        {"proxyType": "http", "proxyAddress": "10.0.0.5"},
        {"proxyType": "http", "proxyPort": 80},
        {"proxyType": "http", "proxyAddress": "10.0.0.5", "proxyPort": 80, "proxyPassword": "p"},
        {"proxy": "http://10.0.0.5"},
        {"proxy": "ftp://10.0.0.5:21"},
    ],
)
def test_malformed_proxy_raises(proxy_fields: dict[str, Any]) -> None:
    with pytest.raises(ProxyFormatError):
        normalize_task_payload({"type": "hcaptcha", **proxy_fields})


def test_proxy_error_never_echoes_the_password() -> None:
    with pytest.raises(ProxyFormatError) as caught:
        normalize_task_payload(
            {"type": "hcaptcha", "proxyAddress": "10.0.0.5", "proxyPort": 80, "proxyPassword": "top-secret"}
        )
    assert "top-secret" not in str(caught.value)


# ---------------------------------------------------------------------------
# hCaptcha extras
# ---------------------------------------------------------------------------


def test_hcaptcha_payload_from_the_2captcha_docs() -> None:
    task = normalize_task_payload(
        {
            "type": "HCaptchaTaskProxyless",
            "websiteURL": PAGE,
            "websiteKey": SITEKEY,
            "isInvisible": True,
            "enterprisePayload": {"rqdata": "rq-123"},
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "cookies": "cf_clearance=abc; session=def",
            "host": "example.com",
        }
    )
    assert task["invisible"] is True
    assert "isInvisible" not in task
    assert task["rqdata"] == "rq-123"
    assert task["enterprise_payload"] == {"rqdata": "rq-123"}
    assert task["cookies"] == [
        {"name": "cf_clearance", "value": "abc"},
        {"name": "session", "value": "def"},
    ]
    assert task["host"] == "example.com"
    assert task["userAgent"] == "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


@pytest.mark.parametrize(("wire", "expected"), [("1", True), ("true", True), (True, True), ("0", False), (False, False)])
def test_invisible_accepts_both_spellings_and_strings(wire: Any, expected: bool) -> None:
    assert normalize_task_payload({"type": "hcaptcha", "invisible": wire})["invisible"] is expected
    assert normalize_task_payload({"type": "hcaptcha", "isInvisible": wire})["invisible"] is expected


def test_enterprise_payload_sent_as_json_text() -> None:
    task = normalize_task_payload({"type": "hcaptcha", "enterprisePayload": '{"rqdata":"rq-json"}'})
    assert task["enterprise_payload"] == {"rqdata": "rq-json"}
    assert task["rqdata"] == "rq-json"


def test_plain_rqdata_stays_a_string() -> None:
    assert normalize_task_payload({"type": "hcaptcha", "rqdata": "  rq-plain  "})["rqdata"] == "rq-plain"


def test_cookies_shapes_converge() -> None:
    assert normalize_cookies([{"name": "a", "value": "b"}]) == [{"name": "a", "value": "b"}]
    assert normalize_cookies({"a": "b"}) == [{"name": "a", "value": "b"}]
    assert normalize_cookies('{"a":"b"}') == [{"name": "a", "value": "b"}]
    assert normalize_cookies("a=b; c=d") == [{"name": "a", "value": "b"}, {"name": "c", "value": "d"}]


def test_normalisation_does_not_mutate_the_input() -> None:
    source: dict[str, Any] = {
        "type": "HCaptchaTask",
        "proxyType": "socks5",
        "proxyAddress": "203.0.113.10",
        "proxyPort": 1080,
        "isInvisible": True,
        "enterprisePayload": {"rqdata": "rq"},
    }
    snapshot = copy.deepcopy(source)
    normalize_task_payload(source)
    assert source == snapshot
    json.dumps(source)  # still serialisable: nothing smuggled in
