from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers.base import SolverContext
from captcha.solvers.recaptcha._common import DEFAULT_VERSION
from captcha.solvers.recaptcha.v2_audio import (
    RecaptchaV2AudioSolver,
    RecaptchaV2EnterpriseSolver,
)

FIXTURES = Path(__file__).with_name("fixtures")
SITEKEY = "test-sitekey"
SITE_URL = "https://example.com/login"
AUDIO_BYTES = (FIXTURES / "audio.bin").read_bytes()


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, body: str | bytes, *, status: int = 200, final_url: str = "") -> None:
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status = status
        self.final_url = final_url
        self.headers: list[tuple[str, str]] = []

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class FakeHTTP:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self._next()

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self._next()

    def _next(self) -> FakeResponse:
        if not self.responses:
            raise AssertionError("unexpected HTTP call; response queue is empty")
        return self.responses.pop(0)


def make_task(
    *,
    type: CaptchaType = CaptchaType.RECAPTCHA_V2,
    payload: dict[str, Any] | None = None,
    sitekey: str | None = SITEKEY,
    site_url: str | None = SITE_URL,
) -> Task:
    return Task(
        id=new_task_id(),
        type=type,
        payload=payload or {},
        sitekey=sitekey,
        site_url=site_url,
        language="en",
    )


def responses(
    *,
    audio_url: bool = True,
    audio_status: int = 200,
    final_token: bool = True,
    enterprise: bool = False,
    extra_audio: tuple[FakeResponse, ...] = (),
) -> list[FakeResponse]:
    audio = "reload_audio_enterprise.json" if enterprise else "reload_audio.json"
    if not audio_url:
        audio = "reload_audio_no_url.json"
    final = "userverify.json" if final_token else "userverify_rejected.json"
    return [
        FakeResponse(fixture("anchor.html"), final_url="https://www.google.com/recaptcha/api2/anchor?k=x"),
        FakeResponse(fixture("reload_swap.json")),
        FakeResponse(fixture(audio)),
        *extra_audio,
        FakeResponse(AUDIO_BYTES, status=audio_status),
        FakeResponse(fixture(final)),
    ]


async def solve_audio(
    http: FakeHTTP,
    *,
    type: CaptchaType = CaptchaType.RECAPTCHA_V2,
    payload: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    sitekey: str | None = SITEKEY,
    site_url: str | None = SITE_URL,
    enterprise: bool = False,
) -> tuple[SolverResult, list[bytes]]:
    seen: list[bytes] = []

    async def transcriber(mp3: bytes, *, task: Task, ctx: SolverContext) -> str:
        seen.append(mp3)
        return "turn left"

    ctx = SolverContext(http=http, extra={"audio_transcriber": transcriber, **(extra or {})})
    solver = RecaptchaV2EnterpriseSolver() if enterprise else RecaptchaV2AudioSolver()
    result = await solver.solve_http(
        make_task(type=type, payload=payload, sitekey=sitekey, site_url=site_url),
        ctx,
    )
    return result, seen


async def test_audio_flow_happy_path() -> None:
    http = FakeHTTP(*responses())
    result, seen = await solve_audio(http)

    assert result.token == "final-token-4"
    assert result.text == "turn left"
    assert result.extra == {"audio_bytes": len(AUDIO_BYTES)}
    assert seen == [AUDIO_BYTES]

    assert [call["method"] for call in http.calls] == ["GET", "POST", "POST", "GET", "POST"]
    assert http.calls[0]["url"].endswith("/recaptcha/api2/anchor")
    assert http.calls[1]["url"].endswith("/recaptcha/api2/reload")
    assert http.calls[2]["url"].endswith("/recaptcha/api2/reload")
    assert http.calls[3]["url"] == "https://www.google.com/recaptcha/api2/payload?p=audio-payload-3"
    assert http.calls[4]["url"].endswith("/recaptcha/api2/userverify")

    anchor_params = http.calls[0]["params"]
    assert anchor_params["k"] == SITEKEY
    assert anchor_params["size"] == "normal"
    assert anchor_params["v"] == DEFAULT_VERSION

    assert http.calls[1]["data"]["reason"] == "q"
    assert http.calls[1]["data"]["c"] == "anchor-token-1"
    assert http.calls[2]["data"]["reason"] == "a"
    assert http.calls[2]["data"]["c"] == "challenge-token-2"

    verify = http.calls[4]["data"]
    assert verify["c"] == "audio-token-3"
    assert verify["response"] == "turn left"
    assert verify["bg"] == ""


async def test_anchor_retries_with_refreshed_version() -> None:
    http = FakeHTTP(
        FakeResponse(fixture("anchor_no_token.html")),
        FakeResponse(fixture("api.js")),
        *responses(),
    )
    result, _ = await solve_audio(http)

    assert result.token == "final-token-4"
    assert http.calls[0]["url"].endswith("/recaptcha/api2/anchor")
    assert http.calls[0]["params"]["v"] == DEFAULT_VERSION
    assert http.calls[1]["url"].endswith("/recaptcha/api.js")
    assert http.calls[2]["url"].endswith("/recaptcha/api2/anchor")
    assert http.calls[2]["params"]["v"] == "TESTVERSION"


async def test_pinned_version_skips_bootstrap() -> None:
    http = FakeHTTP(*responses())
    await solve_audio(http, payload={"v": "PINNED"})
    assert http.calls[0]["params"]["v"] == "PINNED"
    assert not any(call["url"].endswith("/recaptcha/api.js") for call in http.calls)


@pytest.mark.parametrize(
    "type",
    [CaptchaType.RECAPTCHA_V2_INVISIBLE, CaptchaType.RECAPTCHA_V2_CALLBACK],
)
async def test_invisible_and_callback_use_invisible_anchor(type: CaptchaType) -> None:
    http = FakeHTTP(*responses())
    result, _ = await solve_audio(http, type=type)
    assert result.token == "final-token-4"
    assert http.calls[0]["params"]["size"] == "invisible"


async def test_payload_size_overrides_type_default() -> None:
    http = FakeHTTP(*responses())
    await solve_audio(http, payload={"size": "compact"})
    assert http.calls[0]["params"]["size"] == "compact"


async def test_enterprise_uses_enterprise_endpoints() -> None:
    http = FakeHTTP(*responses(enterprise=True))
    result, _ = await solve_audio(http, type=CaptchaType.RECAPTCHA_V2_ENTERPRISE, enterprise=True)

    assert result.token == "final-token-4"
    assert http.calls[0]["url"].endswith("/recaptcha/enterprise/anchor")
    assert http.calls[1]["url"].endswith("/recaptcha/enterprise/reload")
    assert http.calls[2]["url"].endswith("/recaptcha/enterprise/reload")
    assert http.calls[3]["url"] == "https://www.google.com/recaptcha/enterprise/payload?p=audio-payload-ent"
    assert http.calls[4]["url"].endswith("/recaptcha/enterprise/userverify")


async def test_audio_url_falls_back_to_legacy_payload_endpoint() -> None:
    http = FakeHTTP(*responses(audio_url=False))
    await solve_audio(http)
    assert http.calls[3]["url"] == "https://www.google.com/recaptcha/api2/payload"
    assert http.calls[3]["params"] == {"c": "audio-token-3", "k": SITEKEY, "id": "2"}


async def test_audio_url_retries_with_p_param() -> None:
    http = FakeHTTP(
        *responses(
            audio_url=False,
            extra_audio=(FakeResponse(b"", status=403),),
        )
    )
    result, _ = await solve_audio(http)
    assert result.token == "final-token-4"
    assert http.calls[3]["params"] == {"c": "audio-token-3", "k": SITEKEY, "id": "2"}
    assert http.calls[4]["params"] == {"p": "audio-token-3"}


async def test_missing_http_is_config_error() -> None:
    with pytest.raises(ConfigError):
        await RecaptchaV2AudioSolver().solve_http(
            make_task(), SolverContext(http=None, extra={"audio_transcriber": lambda mp3, **kw: "x"})
        )


async def test_missing_sitekey_is_unsolvable() -> None:
    http = FakeHTTP()
    with pytest.raises(UnsolvableError):
        await solve_audio(http, sitekey=None)
    assert http.calls == []


async def test_missing_site_url_is_unsolvable() -> None:
    http = FakeHTTP()
    with pytest.raises(UnsolvableError):
        await solve_audio(http, site_url=None)
    assert http.calls == []


async def test_anchor_without_token_is_challenge_expired() -> None:
    http = FakeHTTP(
        FakeResponse(fixture("anchor_no_token.html")),
        FakeResponse(fixture("api.js")),
        FakeResponse(fixture("anchor_no_token.html")),
    )
    with pytest.raises(ChallengeExpiredError):
        await solve_audio(http)


async def test_challenge_reload_without_token_is_challenge_expired() -> None:
    http = FakeHTTP(
        FakeResponse(fixture("anchor.html")),
        FakeResponse(fixture("reload_no_token.json")),
    )
    with pytest.raises(ChallengeExpiredError):
        await solve_audio(http)


async def test_audio_reload_without_token_is_challenge_expired() -> None:
    http = FakeHTTP(
        FakeResponse(fixture("anchor.html")),
        FakeResponse(fixture("reload_swap.json")),
        FakeResponse(fixture("reload_no_token.json")),
    )
    with pytest.raises(ChallengeExpiredError):
        await solve_audio(http)


async def test_audio_payload_unavailable_is_challenge_expired() -> None:
    http = FakeHTTP(
        FakeResponse(fixture("anchor.html")),
        FakeResponse(fixture("reload_swap.json")),
        FakeResponse(fixture("reload_audio_no_url.json")),
        FakeResponse(b"", status=403),
        FakeResponse(b"", status=403),
    )
    with pytest.raises(ChallengeExpiredError):
        await solve_audio(http)


async def test_empty_transcript_is_unsolvable() -> None:
    http = FakeHTTP(*responses())

    async def empty(mp3: bytes, *, task: Task, ctx: SolverContext) -> str:
        return "   "

    ctx = SolverContext(http=http, extra={"audio_transcriber": empty})
    with pytest.raises(UnsolvableError):
        await RecaptchaV2AudioSolver().solve_http(make_task(), ctx)


async def test_userverify_rejection_is_unsolvable() -> None:
    http = FakeHTTP(*responses(final_token=False))
    with pytest.raises(UnsolvableError):
        await solve_audio(http)


async def test_missing_transcriber_is_config_error() -> None:
    http = FakeHTTP(
        FakeResponse(fixture("anchor.html")),
        FakeResponse(fixture("reload_swap.json")),
        FakeResponse(fixture("reload_audio.json")),
        FakeResponse(AUDIO_BYTES),
    )
    with pytest.raises(ConfigError):
        await RecaptchaV2AudioSolver().solve_http(make_task(), SolverContext(http=http))


async def test_registry_injected_audio_solver_is_used() -> None:
    calls: list[CaptchaType] = []

    class FakeAudioSolver:
        async def prepare(self, ctx: SolverContext) -> None:
            calls.append(CaptchaType.AUDIO)

        async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
            assert task.type is CaptchaType.AUDIO
            assert task.payload["audio"] == AUDIO_BYTES
            return SolverResult(text="registry answer")

        async def shutdown(self) -> None:
            pass

    class FakeRegistry:
        def pick(self, captcha_type: CaptchaType) -> type[FakeAudioSolver]:
            assert captcha_type is CaptchaType.AUDIO
            return FakeAudioSolver

    http = FakeHTTP(*responses())
    ctx = SolverContext(http=http, extra={"solver_registry": FakeRegistry()})
    result = await RecaptchaV2AudioSolver().solve_http(make_task(), ctx)
    assert result.text == "registry answer"
    assert calls == [CaptchaType.AUDIO]


async def test_sync_transcriber_is_supported() -> None:
    http = FakeHTTP(*responses())
    ctx = SolverContext(http=http, extra={"audio_transcriber": lambda mp3: "sync answer"})
    result = await RecaptchaV2AudioSolver().solve_http(make_task(), ctx)
    assert result.text == "sync answer"


async def test_whisper_style_object_transcriber_is_supported() -> None:
    class WhisperStub:
        def transcribe(self, mp3: bytes, *, task: Task, ctx: SolverContext) -> str:
            assert mp3 == AUDIO_BYTES
            return "object answer"

    http = FakeHTTP(*responses())
    ctx = SolverContext(http=http, extra={"whisper": WhisperStub()})
    result = await RecaptchaV2AudioSolver().solve_http(make_task(), ctx)
    assert result.text == "object answer"
