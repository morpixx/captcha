"""SSRF guard: what gets rejected, and what the resolver is never asked.

No test here touches DNS: the resolver is injected, so the suite runs offline
and the assertions are about the decision, not the network.
"""

from __future__ import annotations

import pytest

from captcha.adapters.netguard import (
    MAX_URL_LENGTH,
    UrlRejectedError,
    ensure_public_url,
    ensure_redirect,
    is_public_ip,
)

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700::1111"


def resolver_returning(*addresses: str):
    calls: list[str] = []

    def resolve(host: str) -> tuple[str, ...]:
        calls.append(host)
        return addresses

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


def test_public_http_and_https_pass_unchanged() -> None:
    resolve = resolver_returning(PUBLIC_V4, PUBLIC_V6)

    assert ensure_public_url("https://example.com/challenge?x=1", resolver=resolve) == (
        "https://example.com/challenge?x=1"
    )
    assert ensure_public_url("http://example.com/", resolver=resolve) == "http://example.com/"
    assert resolve.calls == ["example.com", "example.com"]


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "gopher://example.com:70/_x",
        "",
        "   ",
        "not a url",
        "http://",
        "http:///nohost",
    ],
)
def test_bad_scheme_or_shape_is_rejected(url: str) -> None:
    with pytest.raises(UrlRejectedError) as info:
        ensure_public_url(url, resolver=resolver_returning(PUBLIC_V4))
    assert info.value.code == "ERR_URL_REJECTED"


def test_oversize_url_is_rejected() -> None:
    too_long = "https://example.com/" + "a" * MAX_URL_LENGTH
    with pytest.raises(UrlRejectedError) as info:
        ensure_public_url(too_long, resolver=resolver_returning(PUBLIC_V4))
    assert info.value.context["length"] == len(too_long)


def test_control_characters_are_rejected() -> None:
    with pytest.raises(UrlRejectedError):
        ensure_public_url("https://example.com/\r\nHost: internal", resolver=resolver_returning(PUBLIC_V4))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "https://127.0.0.1:8443/x",
        "http://10.1.2.3/",
        "http://172.16.5.5/",
        "http://192.168.0.10/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",
        "http://198.18.0.1/",
        "http://224.0.0.1/",
        "http://255.255.255.255/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://[fd12:3456::1]/",
        "http://[ff02::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[2001:db8::1]/",
    ],
)
def test_private_literals_are_rejected_without_dns(url: str) -> None:
    resolve = resolver_returning(PUBLIC_V4)

    with pytest.raises(UrlRejectedError) as info:
        ensure_public_url(url, resolver=resolve)

    assert info.value.context["address"] is not None
    assert resolve.calls == [], "a literal must never hit the resolver"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://localhost:8080/x",
        "http://app.localhost/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_loopback_names_are_rejected_without_dns(url: str) -> None:
    resolve = resolver_returning(PUBLIC_V4)

    with pytest.raises(UrlRejectedError) as info:
        ensure_public_url(url, resolver=resolve)

    assert info.value.context["host"]
    assert resolve.calls == []


def test_a_and_aaaa_are_both_checked() -> None:
    # Public A, private AAAA: the host is not allowed to be reachable at all.
    resolve = resolver_returning(PUBLIC_V4, "fd00::1")
    with pytest.raises(UrlRejectedError) as info:
        ensure_public_url("https://example.com/", resolver=resolve)
    assert info.value.context["address"] == "fd00::1"


def test_purely_public_dns_answer_passes() -> None:
    resolve = resolver_returning(PUBLIC_V4, PUBLIC_V6)
    assert ensure_public_url("https://example.com/", resolver=resolve) == "https://example.com/"


def test_unresolvable_host_is_rejected() -> None:
    with pytest.raises(UrlRejectedError) as info:
        ensure_public_url("https://nope.example/", resolver=resolver_returning())
    assert "did not resolve" in info.value.message


def test_resolver_failure_is_translated_to_a_captcha_error() -> None:
    def broken(_host: str) -> tuple[str, ...]:
        raise OSError("dns is down")

    with pytest.raises(UrlRejectedError) as info:
        ensure_public_url("https://example.com/", resolver=broken)
    assert "did not resolve" in info.value.message


def test_non_address_answer_is_rejected() -> None:
    with pytest.raises(UrlRejectedError):
        ensure_public_url("https://example.com/", resolver=resolver_returning("not-an-address"))


def test_field_lands_in_the_error_context() -> None:
    with pytest.raises(UrlRejectedError) as info:
        ensure_public_url("http://10.0.0.1/", field="websiteURL")
    assert info.value.context["field"] == "websiteURL"


def test_redirect_hop_is_checked_against_the_same_rules() -> None:
    resolve = resolver_returning(PUBLIC_V4)

    assert ensure_redirect("/step-2", base="https://example.com/a", resolver=resolve) == ("https://example.com/step-2")
    with pytest.raises(UrlRejectedError):
        ensure_redirect("http://10.0.0.1/admin", base="https://example.com/a", resolver=resolve)
    with pytest.raises(UrlRejectedError):
        ensure_redirect("http://169.254.169.254/latest", base="https://example.com/a", resolver=resolve)


def test_idn_host_is_encoded_before_resolution() -> None:
    resolve = resolver_returning(PUBLIC_V4)
    ensure_public_url("https://пример.рф/", resolver=resolve)
    assert resolve.calls == ["xn--e1afmkfd.xn--p1ai"]


def test_is_public_ip_truth_table() -> None:
    for address in (PUBLIC_V4, PUBLIC_V6, "8.8.8.8", "1.1.1.1"):
        assert is_public_ip(address) is True
    for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "fe80::1", "fc00::1", "100.64.0.1", "junk"):
        assert is_public_ip(address) is False
