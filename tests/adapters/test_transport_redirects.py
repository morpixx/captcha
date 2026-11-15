"""Transport redirect policy: curl never follows, every hop goes through netguard.

No curl_cffi session and no network: the session is a fake that hands back
canned responses, and the DNS resolver is patched in the netguard module. This
is the RT-06 regression suite — a public URL answering `302 Location:
http://10.0.0.1/` must raise before a second request is made, and error context
must carry `scheme://host` only, never the query string of the original URL.
"""

from __future__ import annotations

import pytest

from captcha.adapters import netguard
from captcha.adapters.transport import curl
from captcha.adapters.transport.curl import Client
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, ProxyError, UrlRejectedError

PUBLIC_IP = "93.184.216.34"


class FakeRaw:
    """The slice of curl_cffi's Response that `_wrap` reads."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None, *, url: str = "") -> None:
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.content = b""
        self.url = url


class FakeSession:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.closed = False

    async def request(self, method: str, url: str, **kwargs: object) -> FakeRaw:
        self.calls.append({"method": method, "url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, FakeRaw)
        return item

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any hostname resolves to one public address; literal IPs skip the resolver."""
    monkeypatch.setattr(netguard, "default_resolver", lambda _host: (PUBLIC_IP,))


def make_client(monkeypatch: pytest.MonkeyPatch, *responses: object) -> tuple[Client, FakeSession]:
    # The real AsyncSession is never constructed: `_session` is replaced below,
    # and the import guard only looks for None.
    monkeypatch.setattr(curl, "AsyncSession", object)
    client = Client(impersonate="chrome124", retry_attempts=1)
    session = FakeSession(list(responses))
    client._session = session  # type: ignore[assignment]
    return client, session


def redirect(status: int, location: str, *, url: str) -> FakeRaw:
    return FakeRaw(status, {"location": location}, url=url)


async def test_curl_never_follows_on_its_own(monkeypatch: pytest.MonkeyPatch, public_dns: None) -> None:
    client, session = make_client(
        monkeypatch,
        redirect(302, "https://example.com/next", url="https://example.com/start"),
        FakeRaw(200, url="https://example.com/next"),
    )

    response = await client.get("https://example.com/start")

    assert response.status == 200
    assert response.final_url == "https://example.com/next"
    assert [call["url"] for call in session.calls] == ["https://example.com/start", "https://example.com/next"]
    assert all(call["allow_redirects"] is False for call in session.calls)


async def test_public_redirect_chain_is_followed(monkeypatch: pytest.MonkeyPatch, public_dns: None) -> None:
    client, session = make_client(
        monkeypatch,
        redirect(302, "/intermediate", url="https://example.com/start"),
        redirect(303, "https://cdn.example.com/final", url="https://example.com/intermediate"),
        FakeRaw(200, url="https://cdn.example.com/final"),
    )

    response = await client.get("https://example.com/start")

    assert response.status == 200
    assert [call["url"] for call in session.calls] == [
        "https://example.com/start",
        "https://example.com/intermediate",
        "https://cdn.example.com/final",
    ]


async def test_private_literal_redirect_is_rejected_before_the_request(
    monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    client, session = make_client(
        monkeypatch,
        redirect(302, "http://169.254.169.254/latest/meta-data/?token=leak", url="https://example.com/start?token=supersecret"),
    )

    with pytest.raises(UrlRejectedError) as info:
        await client.get("https://example.com/start?token=supersecret")

    assert info.value.code == "ERR_URL_REJECTED"
    assert info.value.context["host"] == "169.254.169.254"
    assert "address" in info.value.context
    assert len(session.calls) == 1
    assert "supersecret" not in repr(info.value.context)
    assert "leak" not in repr(info.value.context)


async def test_hostname_resolving_to_private_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netguard, "default_resolver", lambda host: ("10.1.2.3",) if host == "internal.example" else (PUBLIC_IP,))
    client, session = make_client(
        monkeypatch,
        redirect(302, "http://internal.example/admin", url="https://example.com/start"),
    )

    with pytest.raises(UrlRejectedError) as info:
        await client.get("https://example.com/start")

    assert info.value.context["host"] == "internal.example"
    assert info.value.context["address"] == "10.1.2.3"
    assert len(session.calls) == 1


async def test_metadata_hostname_is_rejected_without_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_dns(_host: str) -> tuple[str, ...]:
        raise AssertionError("denied hostnames must not reach the resolver")

    monkeypatch.setattr(netguard, "default_resolver", no_dns)
    client, session = make_client(
        monkeypatch,
        redirect(302, "http://metadata.google.internal/computeMetadata/v1/", url="https://example.com/start"),
    )

    with pytest.raises(UrlRejectedError) as info:
        await client.get("https://example.com/start")

    assert info.value.context["host"] == "metadata.google.internal"
    assert len(session.calls) == 1


async def test_relative_redirect_resolves_against_the_hop_and_keeps_its_query(
    monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    client, session = make_client(
        monkeypatch,
        redirect(302, "/next?x=1", url="https://example.com/start"),
        FakeRaw(200, url="https://example.com/next?x=1"),
    )

    await client.get("https://example.com/start")

    assert session.calls[1]["url"] == "https://example.com/next?x=1"
    # params belong to the first request only; they are not re-applied per hop
    assert "params" not in session.calls[1]


async def test_cross_origin_redirect_drops_credentials(monkeypatch: pytest.MonkeyPatch, public_dns: None) -> None:
    client, session = make_client(
        monkeypatch,
        redirect(302, "https://other.example/final", url="https://example.com/start"),
        FakeRaw(200, url="https://other.example/final"),
    )

    await client.get(
        "https://example.com/start",
        headers={"Authorization": "Bearer secret", "X-Trace": "1"},
        cookies={"sid": "secret"},
    )

    assert session.calls[0]["headers"] == {"Authorization": "Bearer secret", "X-Trace": "1"}
    assert session.calls[1]["headers"] == {"X-Trace": "1"}
    assert "cookies" not in session.calls[1]


async def test_same_origin_redirect_keeps_credentials(monkeypatch: pytest.MonkeyPatch, public_dns: None) -> None:
    client, session = make_client(
        monkeypatch,
        redirect(302, "https://example.com:443/final", url="https://example.com/start"),
        FakeRaw(200, url="https://example.com/final"),
    )

    await client.get(
        "https://example.com/start",
        headers={"Authorization": "Bearer secret"},
        cookies={"sid": "secret"},
    )

    assert session.calls[1]["headers"] == {"Authorization": "Bearer secret"}
    assert session.calls[1]["cookies"] == {"sid": "secret"}


@pytest.mark.parametrize("status", [307, 308])
async def test_307_308_keep_method_and_body(
    monkeypatch: pytest.MonkeyPatch, public_dns: None, status: int
) -> None:
    client, session = make_client(
        monkeypatch,
        redirect(status, "https://example.com/final", url="https://example.com/start"),
        FakeRaw(200, url="https://example.com/final"),
    )

    await client.post("https://example.com/start", json={"answer": 1})

    assert session.calls[1]["method"] == "POST"
    assert session.calls[1]["json"] == {"answer": 1}


async def test_put_survives_a_302(monkeypatch: pytest.MonkeyPatch, public_dns: None) -> None:
    client, session = make_client(
        monkeypatch,
        redirect(302, "https://example.com/final", url="https://example.com/start"),
        FakeRaw(200, url="https://example.com/final"),
    )

    await client.request("PUT", "https://example.com/start", data=b"payload")

    assert session.calls[1]["method"] == "PUT"
    assert session.calls[1]["data"] == b"payload"


@pytest.mark.parametrize("status", [301, 302, 303])
async def test_post_is_downgraded_to_get_on_301_302_303(
    monkeypatch: pytest.MonkeyPatch, public_dns: None, status: int
) -> None:
    client, session = make_client(
        monkeypatch,
        redirect(status, "https://example.com/final", url="https://example.com/start"),
        FakeRaw(200, url="https://example.com/final"),
    )

    await client.post("https://example.com/start", data=b"payload")

    assert session.calls[1]["method"] == "GET"
    assert "data" not in session.calls[1]
    assert "json" not in session.calls[1]


async def test_allow_redirects_false_returns_the_3xx(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = make_client(
        monkeypatch,
        redirect(302, "http://169.254.169.254/", url="https://example.com/start"),
    )

    response = await client.get("https://example.com/start", allow_redirects=False)

    assert response.status == 302
    assert len(session.calls) == 1


async def test_redirect_without_location_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = make_client(monkeypatch, FakeRaw(302, url="https://example.com/start"))

    response = await client.get("https://example.com/start")

    assert response.status == 302
    assert len(session.calls) == 1


async def test_too_many_redirects_is_a_captcha_error(monkeypatch: pytest.MonkeyPatch, public_dns: None) -> None:
    responses = [redirect(302, f"https://example.com/hop-{index}", url="https://example.com/start") for index in range(5)]
    client, session = make_client(monkeypatch, *responses)

    with pytest.raises(CaptchaError) as info:
        await client.get("https://example.com/start", max_redirects=2)

    assert "too many redirects" in info.value.message
    assert info.value.context["max_redirects"] == 2
    assert len(session.calls) == 3


async def test_no_dns_lookup_when_nothing_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_dns(_host: str) -> tuple[str, ...]:
        raise AssertionError("a plain request must not resolve the URL")

    monkeypatch.setattr(netguard, "default_resolver", no_dns)
    client, _session = make_client(monkeypatch, FakeRaw(200, url="https://example.com/start"))

    assert (await client.get("https://example.com/start")).status == 200


async def test_challenge_error_context_is_host_only(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _session = make_client(
        monkeypatch,
        FakeRaw(403, url="https://example.com/challenge?token=supersecret&session=abc"),
    )

    with pytest.raises(ChallengeExpiredError) as info:
        await client.get("https://example.com/challenge?token=supersecret&session=abc")

    assert info.value.context["url"] == "https://example.com"
    assert info.value.context["status"] == 403
    assert "supersecret" not in repr(info.value.context)


async def test_proxy_error_context_is_host_only(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProxyError(Exception):
        pass

    monkeypatch.setattr(curl, "_CurlProxyError", FakeProxyError)
    client, session = make_client(monkeypatch, FakeProxyError("tunnel refused"))

    with pytest.raises(ProxyError) as info:
        await client.get("https://example.com/start?token=supersecret")

    assert info.value.context["url"] == "https://example.com"
    assert "supersecret" not in repr(info.value.context)
    assert len(session.calls) == 1
