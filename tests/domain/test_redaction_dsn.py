"""`redact_dsn`: datastore URLs in log lines must not carry the password."""

from __future__ import annotations

import pytest

from captcha.domain.redaction import redact_dsn


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("redis://:s3cret@cache:6379/0", "redis://***@cache:6379/0"),
        ("redis://user:s3cret@cache:6379/1", "redis://***@cache:6379/1"),
        ("postgresql+asyncpg://captcha:captcha@db:5432/captcha", "postgresql+asyncpg://***@db:5432/captcha"),
        ("redis://localhost:6379/0", "redis://localhost:6379/0"),
        ("redis://cache:6379/0?password=s3cret", "redis://cache:6379/0"),
        ("redis://[::1]:6379/0", "redis://[::1]:6379/0"),
        ("", ""),
        ("not-a-dsn", "***"),
    ],
)
def test_redact_dsn(raw: str, expected: str) -> None:
    assert redact_dsn(raw) == expected


def test_redact_dsn_never_returns_the_password() -> None:
    for raw in ("redis://:s3cret@cache:6379/0", "postgresql://u:s3cret@db/db"):
        assert "s3cret" not in redact_dsn(raw)


def test_redact_dsn_accepts_none() -> None:
    assert redact_dsn(None) == ""
