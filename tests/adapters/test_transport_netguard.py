"""The HTTP transport must not reach private targets, directly or by redirect.

curl_cffi follows redirects inside the library, after the socket is open, so the
guard has to drive the chain itself. These tests use a fake session: the point is
which URL is attempted and which is refused, not the bytes on the wire.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("curl_cffi")

from captcha.adapters.netguard import UrlRejectedError
from captcha.adapters.transport import Client
from captcha.domain.errors import CaptchaError

PUBLIC = "93.184.216.34"


def resolver_map(
    mapping: dict[str, tuple[str, ...]] | None = None,
    default: tuple[str, ...] = (PUBLIC,),
) -> Any:
    calls: list[str] = []

    def resolve(host: str) -> tuple[str, ...]:
        calls.append(host)
        if mapping is not None and host in mapping:
            return mapping[host]
        return default

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.content = content
        self.url = ""


class FakeSession:
    """Hands out queued responses and records (method, url, kwargs) per call."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request to {url}")
        response = self.responses.pop(0)
        response.url = url
        return response

    async def close(self) -> None:
        return None


def make_client(session: FakeSession, **kwargs: Any) -> Client:
    kwargs.setdefault("retry_attempts", 1)
    client = Client(**kwargs)
    client._session = session  # type: ignore[assignment]  # no real curl_cffi socket in tests
    return client


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.169.254/latest/meta-data/"])
async def test_private_target_is_refused_before_the_socket(url: str) -> None:
    session = FakeSession([])
    resolve = resolver_map()
    client = make_client(session, resolver=resolve)

    with pytest.raises(UrlRejectedError):
        await client.get(url)

    assert session.calls == [], "a rejected target must never be requested"
    assert resolve.calls == [], "a literal address needs no DNS"


async def test_private_hostname_is_refused_after_resolution() -> None:
    session = FakeSession([])
    resolve = resolver_map({"internal.test": ("10.0.0.1",)})
    client = make_client(session, resolver=resolve)

    with pytest.raises(UrlRejectedError):
        await client.get("https://internal.test/admin")

    assert session.calls == []
    assert resolve.calls == ["internal.test"]


async def test_public_target_is_sent() -> None:
    session = FakeSession([FakeResponse()])
    resolve = resolver_map()
    client = make_client(session, resolver=resolve)

    response = await client.get("https://example.com/login")

    assert response.status == 200
    assert resolve.calls == ["example.com"]
    assert len(session.calls) == 1
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", "https://example.com/login")
    assert kwargs["allow_redirects"] is False, "curl must not follow on its own"


@pytest.mark.parametrize("target", ["http://127.0.0.1/", "http://10.0.0.1/admin", "http://169.254.169.254/latest/"])
async def test_redirect_to_a_private_target_is_refused(target: str) -> None:
    session = FakeSession([FakeResponse(302, {"location": target})])
    client = make_client(session, resolver=resolver_map())

    with pytest.raises(UrlRejectedError):
        await client.get("https://example.com/start")

    assert len(session.calls) == 1, "the private hop must not be fetched"


async def test_public_redirect_is_followed() -> None:
    session = FakeSession(
        [
            FakeResponse(302, {"location": "/next"}),
            FakeResponse(200, content=b"done"),
        ]
    )
    client = make_client(session, resolver=resolver_map())

    response = await client.get("https://example.com/start")

    assert response.status == 200
    assert response.body == b"done"
    assert response.final_url == "https://example.com/next"
    assert [call[1] for call in session.calls] == ["https://example.com/start", "https://example.com/next"]
    assert all(call[2]["allow_redirects"] is False for call in session.calls)


async def test_redirect_disabled_by_the_caller_is_not_followed() -> None:
    session = FakeSession([FakeResponse(302, {"location": "https://example.com/next"})])
    client = make_client(session, resolver=resolver_map())

    response = await client.get("https://example.com/start", allow_redirects=False)

    assert response.status == 302
    assert len(session.calls) == 1


async def test_post_becomes_get_on_303_without_the_body() -> None:
    session = FakeSession(
        [
            FakeResponse(303, {"location": "https://example.com/next"}),
            FakeResponse(200),
        ]
    )
    client = make_client(session, resolver=resolver_map())

    await client.post("https://example.com/start", data=b"payload")

    second = session.calls[1]
    assert second[0] == "GET"
    assert "data" not in second[2]
    assert "json" not in second[2]


async def test_308_keeps_the_method_and_the_body() -> None:
    session = FakeSession(
        [
            FakeResponse(308, {"location": "https://example.com/next"}),
            FakeResponse(200),
        ]
    )
    client = make_client(session, resolver=resolver_map())

    await client.post("https://example.com/start", data=b"payload")

    second = session.calls[1]
    assert second[0] == "POST"
    assert second[2]["data"] == b"payload"


async def test_cross_host_redirect_drops_caller_credentials() -> None:
    session = FakeSession(
        [
            FakeResponse(302, {"location": "https://other.test/next"}),
            FakeResponse(200),
        ]
    )
    client = make_client(session, resolver=resolver_map())

    await client.get(
        "https://example.com/start",
        cookies={"sid": "secret"},
        headers={"authorization": "Bearer t", "user-agent": "UA"},
    )

    first, second = session.calls
    assert first[2]["cookies"] == {"sid": "secret"}
    assert second[2].get("cookies") is None
    assert second[2]["headers"] == {"user-agent": "UA", "Referer": "https://example.com/start"}


async def test_same_host_redirect_keeps_the_cookies() -> None:
    session = FakeSession(
        [
            FakeResponse(302, {"location": "/next"}),
            FakeResponse(200),
        ]
    )
    client = make_client(session, resolver=resolver_map())

    await client.get("https://example.com/start", cookies={"sid": "secret"})

    assert session.calls[1][2]["cookies"] == {"sid": "secret"}


async def test_too_many_redirects_is_a_transport_error() -> None:
    session = FakeSession(
        [
            FakeResponse(302, {"location": "/a"}),
            FakeResponse(302, {"location": "/b"}),
        ]
    )
    client = make_client(session, resolver=resolver_map(), max_redirects=1)

    with pytest.raises(CaptchaError):
        await client.get("https://example.com/start")

    assert len(session.calls) == 2


async def test_allow_private_flag_is_the_only_way_to_a_private_target() -> None:
    session = FakeSession([FakeResponse(200)])
    resolve = resolver_map()
    client = make_client(session, resolver=resolve, allow_private=True)

    response = await client.get("http://127.0.0.1:8080/")

    assert response.status == 200
    assert len(session.calls) == 1
    assert resolve.calls == [], "the test flag skips the guard entirely"
