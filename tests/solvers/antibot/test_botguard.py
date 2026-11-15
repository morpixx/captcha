"""BotGuard solver tests -- fakes for transport/runtime, no network.

Pins the contract between the solver and the builder: the program is parsed
(bare f.txt or a WAA JSON wrapper), the interpreter comes inline or is fetched
from the product-scoped URL, the driver runs in ctx.js_runtime and its JSON is
the only way a token reaches the client. Error mapping matters more than the
happy path here: a rotated program is ChallengeExpiredError, a VM that refuses
to initialise is SensorError, a worker without a JS runtime is ConfigError.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, SensorError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.domain.task import new_task_id
from captcha.engines.botguard.builder import BotGuardBuilder
from captcha.solvers import SolverContext
from captcha.solvers.antibot.botguard import wire
from captcha.solvers.antibot.botguard.solver import BotGuardSolver

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
PROGRAM = "PROG-1"
TOKEN = "TOKEN-" + "A" * 40
INTERPRETER = "var botguard = { a: function () {} };"


# --- fakes -------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body: Any = b"", *, status: int = 200) -> None:
        self.status = status
        self.body = body if isinstance(body, (bytes, str)) else json.dumps(body).encode("utf-8")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace") if isinstance(self.body, bytes) else self.body


class FakeHTTP:
    def __init__(self, *gets: FakeResponse) -> None:
        self.gets = list(gets)
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        if not self.gets:
            raise AssertionError(f"unexpected GET {url}")
        return self.gets.pop(0)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:  # pragma: no cover - guard
        raise AssertionError(f"botguard must not POST, got {url}")


class FakeRuntime:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def exec_script(
        self, code: str, context_globals: dict[str, Any] | None = None, *, timeout_ms: int | None = None
    ) -> Any:
        self.calls.append({"code": code, "globals": context_globals, "timeout_ms": timeout_ms})
        if not self.results:
            raise AssertionError("unexpected exec_script")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeSensors:
    def __init__(self, builder: Any) -> None:
        self.builder = builder

    def get(self, engine: Any) -> Any:
        return self.builder


def answer(token: str | None = TOKEN, *, error: str | None = None) -> str:
    return json.dumps({"token": token, "error": error, "stage": "snapshot", "warnings": []})


def make_task(**payload: Any) -> Task:
    base: dict[str, Any] = {
        "ftx": PROGRAM,
        "interpreter_js": INTERPRETER,
        "product": "recaptcha",
        "userAgent": UA,
    }
    base.update(payload)
    return Task(id=new_task_id(), type=CaptchaType.BOTGUARD, payload=base)


def make_ctx(http: Any, runtime: Any = None) -> SolverContext:
    return SolverContext(http=http, js_runtime=runtime, sensors=FakeSensors(BotGuardBuilder()))


# --- flow --------------------------------------------------------------------


async def test_inline_interpreter_is_run_and_the_token_returned() -> None:
    http = FakeHTTP()
    runtime = FakeRuntime(answer())

    result = await BotGuardSolver().solve_http(make_task(), make_ctx(http, runtime))

    assert http.calls == []
    assert result.token == TOKEN
    assert result.extra == {"product": "recaptcha", "userAgent": UA}
    assert result.latency_ms >= 0.0
    assert runtime.calls[0]["globals"]["BG_PROGRAM"] == PROGRAM
    assert INTERPRETER in runtime.calls[0]["code"]
    assert runtime.calls[0]["timeout_ms"] == 30_000

    solution = build_solution(CaptchaType.BOTGUARD, {"token": result.token, "extra": result.extra})
    assert solution is not None
    assert solution["token"] == TOKEN
    assert solution["userAgent"] == UA


async def test_driver_timeout_honours_the_payload() -> None:
    runtime = FakeRuntime(answer())
    await BotGuardSolver().solve_http(make_task(js_timeout_ms=1234), make_ctx(FakeHTTP(), runtime))
    assert runtime.calls[0]["timeout_ms"] == 1234


async def test_interpreter_is_fetched_from_a_protocol_relative_url() -> None:
    http = FakeHTTP(FakeResponse(b"var botguard = { fetched: 1 };"))
    runtime = FakeRuntime(answer())

    task = make_task(interpreter_js=None, interpreter_url="//www.google.com/recaptcha/api2/x.js")
    await BotGuardSolver().solve_http(task, make_ctx(http, runtime))

    assert http.calls[0]["url"] == "https://www.google.com/recaptcha/api2/x.js"
    assert "var botguard = { fetched: 1 };" in runtime.calls[0]["code"]


async def test_json_challenge_wrapper_wins_over_payload_fields() -> None:
    program = json.dumps({"program": "PROG-2", "globalName": "vm1", "interpreterUrl": "https://host/i.js"})
    http = FakeHTTP(FakeResponse(b"INTERP-2"))
    runtime = FakeRuntime(answer("PROG-2-" + "B" * 40))

    task = make_task(ftx=program, interpreter_js=None)
    result = await BotGuardSolver().solve_http(task, make_ctx(http, runtime))

    assert result.token == "PROG-2-" + "B" * 40
    assert runtime.calls[0]["globals"]["BG_PROGRAM"] == "PROG-2"
    assert runtime.calls[0]["globals"]["BG_GLOBAL"] == "vm1"
    assert "INTERP-2" in runtime.calls[0]["code"]


# --- error mapping -----------------------------------------------------------


async def test_missing_program_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError):
        await BotGuardSolver().solve_http(make_task(ftx=None), make_ctx(FakeHTTP(), FakeRuntime()))


async def test_missing_interpreter_is_unsolvable() -> None:
    task = make_task(interpreter_js=None, challenge_js=None)
    with pytest.raises(UnsolvableError):
        await BotGuardSolver().solve_http(task, make_ctx(FakeHTTP(), FakeRuntime()))


async def test_rotated_json_challenge_is_an_expired_challenge() -> None:
    task = make_task(ftx=json.dumps({"globalName": "vm1"}))
    with pytest.raises(ChallengeExpiredError):
        await BotGuardSolver().solve_http(task, make_ctx(FakeHTTP(), FakeRuntime()))


async def test_vm_refusal_is_a_sensor_error() -> None:
    runtime = FakeRuntime(answer(None, error="vm-throw:boom"))
    with pytest.raises(SensorError) as exc:
        await BotGuardSolver().solve_http(make_task(), make_ctx(FakeHTTP(), runtime))
    assert exc.value.context["error"] == "vm-throw:boom"


async def test_empty_answer_is_an_expired_challenge() -> None:
    runtime = FakeRuntime(json.dumps({"token": None, "error": None, "stage": "lookup"}))
    with pytest.raises(ChallengeExpiredError):
        await BotGuardSolver().solve_http(make_task(), make_ctx(FakeHTTP(), runtime))


async def test_missing_runtime_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        await BotGuardSolver().solve_http(make_task(), make_ctx(FakeHTTP()))


async def test_interpreter_fetch_failure_is_a_sensor_error() -> None:
    http = FakeHTTP(FakeResponse(b"", status=404))
    task = make_task(interpreter_js=None, interpreter_url="https://www.google.com/x.js")
    with pytest.raises(SensorError):
        await BotGuardSolver().solve_http(task, make_ctx(http, FakeRuntime()))


# --- wire helpers ------------------------------------------------------------


def test_parse_program_accepts_a_bare_blob() -> None:
    program = wire.parse_program("  PROG  ", global_name="vm", interpreter_url="https://i.js")
    assert program.program == "PROG"
    assert program.global_name == "vm"
    assert program.interpreter_url == "https://i.js"


def test_parse_program_rejects_a_json_blob_without_bytecode() -> None:
    with pytest.raises(ChallengeExpiredError):
        wire.parse_program('{"globalName": "vm"}')


def test_token_from_runtime_accepts_the_driver_shapes() -> None:
    assert wire.token_from_runtime(answer()) == TOKEN
    assert wire.token_from_runtime({"bgresponse": "R" * 40}) == "R" * 40
    assert wire.token_from_runtime("T" * 40) == "T" * 40


def test_token_from_runtime_rejects_short_and_non_printable_tokens() -> None:
    with pytest.raises(SensorError):
        wire.token_from_runtime({"token": "short"})
    with pytest.raises(SensorError):
        wire.token_from_runtime("T" * 39 + "\u0000")


def test_solver_info_is_http_and_needs_the_runtime() -> None:
    info = BotGuardSolver.info
    assert info.types == (CaptchaType.BOTGUARD,)
    assert info.http is True
    assert info.needs_js_runtime is True
