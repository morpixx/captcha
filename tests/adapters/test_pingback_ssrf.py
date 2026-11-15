"""Pingback SSRF guard: a client callback URL never reaches private space.

`callbackUrl` is client-supplied and the worker POSTs to it, so the sender
re-checks it before every delivery. No network: the http client is a fake, and
the guard itself is DNS-free by design.
"""

from __future__ import annotations

from typing import Any

import pytest

from captcha.adapters.pingback import PingbackSender, safe_url


class FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class FakeClient:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses) or [FakeResponse()]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/cb",
        "http://10.0.0.5/cb",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/cb",
        "http://localhost/cb",
        "http://metadata.google.internal/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
async def test_private_callback_urls_are_not_called(url: str) -> None:
    client = FakeClient()
    sender = PingbackSender(client=client, max_attempts=3, backoff_sec=0)

    assert await sender.send(url, {"status": "ready"}) is False
    assert client.calls == [], "a rejected callback URL must never be fetched"


async def test_a_public_callback_url_is_delivered_once() -> None:
    client = FakeClient(FakeResponse(200))
    sender = PingbackSender(client=client, max_attempts=3, backoff_sec=0)

    assert await sender.send("https://client.example/cb?task=1", {"status": "ready"}) is True
    assert len(client.calls) == 1
    assert client.calls[0][0] == "https://client.example/cb?task=1"


async def test_non_2xx_is_retried_then_reported_as_failure() -> None:
    client = FakeClient(FakeResponse(500), FakeResponse(500), FakeResponse(500))
    sender = PingbackSender(client=client, max_attempts=3, backoff_sec=0)

    assert await sender.send("https://client.example/cb", {}) is False
    assert len(client.calls) == 3


def test_safe_url_drops_path_query_and_userinfo() -> None:
    assert safe_url("https://user:pass@client.example:8443/cb?token=secret#frag") == "https://client.example:8443"
    assert safe_url("http://client.example/cb") == "http://client.example"
    assert safe_url("not a url") == "<unparseable>"
    assert safe_url("ftp://host/x") == "ftp://host"
