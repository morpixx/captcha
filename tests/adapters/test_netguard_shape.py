"""`ensure_public_url_shape`: the DNS-free half of the SSRF guard.

The gateway create path and the pingback sender use it because they must not
block on a resolver. Everything that can be decided without a DNS answer is
still decided here; the resolved-address check stays in `ensure_public_url`,
which the browser lane calls at navigation time.
"""

from __future__ import annotations

import socket

import pytest

from captcha.adapters.netguard import (
    MAX_URL_LENGTH,
    UrlRejectedError,
    ensure_public_url_shape,
)


def test_shape_check_never_touches_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the shape check must not resolve a hostname")

    monkeypatch.setattr(socket, "getaddrinfo", explode)

    assert ensure_public_url_shape("https://example.com/login?x=1") == "https://example.com/login?x=1"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://localhost/",
        "http://app.localhost/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://",
        "",
    ],
)
def test_shape_check_rejects_what_needs_no_dns(url: str) -> None:
    with pytest.raises(UrlRejectedError) as info:
        ensure_public_url_shape(url, field="websiteURL")
    assert info.value.code == "ERR_URL_REJECTED"


def test_shape_check_rejects_oversize_and_control_characters() -> None:
    with pytest.raises(UrlRejectedError):
        ensure_public_url_shape("https://example.com/" + "a" * MAX_URL_LENGTH)
    with pytest.raises(UrlRejectedError):
        ensure_public_url_shape("https://example.com/\r\nHost: internal")


def test_error_context_names_the_field_and_address_but_not_the_url() -> None:
    secret = "https://10.0.0.1/cb?token=leak"
    with pytest.raises(UrlRejectedError) as info:
        ensure_public_url_shape(secret, field="callbackUrl")
    assert info.value.context["field"] == "callbackUrl"
    assert info.value.context["address"] == "10.0.0.1"
    assert "leak" not in str(info.value.context)
