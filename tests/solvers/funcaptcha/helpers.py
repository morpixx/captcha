"""Shared fakes for the funcaptcha solver tests.

No network, no browser, no real Arkose: a recording HTTP client that replays
queued bodies, a stub solver registry, and a browser provider whose page answers
with a scripted sequence of JS results.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.task import new_task_id

FIXTURES = Path(__file__).with_name("fixtures")
SITEKEY = "3EE79F8D-13A6-474B-9278-448EA19F79B3"
PAGE_URL = "https://www.example.com/login"
SURL = "https://client-api.arkoselabs.com"
TOKEN = "98718c6b2e1d4f8a9b3c5d7e|r=eu-west-1|sup=1|1712000000"
CHALLENGE_TOKEN = "98718c6b2e1d4f8a9b3c5d7e|sid=us-west-2|r=eu-west-1|1712000000"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    """Duck-typed stand-in for captcha.adapters.transport.Response."""

    def __init__(self, body: Any, *, status: int = 200) -> None:
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.headers: list[tuple[str, str]] = [("content-type", "application/json")]
        self.elapsed_ms = 0.0
        self.final_url = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)


class FakeHTTP:
    """Records every request and replays queued responses in order."""

    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self._next(url)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self._next(url)

    def _next(self, url: str) -> FakeResponse:
        if not self.responses:
            raise AssertionError(f"unexpected request {url}")
        return self.responses.pop(0)

    def call(self, index: int) -> dict[str, Any]:
        return self.calls[index]


def make_task(*, captcha_type: CaptchaType = CaptchaType.FUNCAPTCHA, payload: dict[str, Any] | None = None, **overrides: Any) -> Task:
    base = {"publickey": SITEKEY, "pageurl": PAGE_URL, "surl": SURL}
    base.update(payload or {})
    fields: dict[str, Any] = {
        "id": new_task_id(),
        "type": captcha_type,
        "payload": base,
        "sitekey": SITEKEY,
        "site_url": PAGE_URL,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Test/1.0",
    }
    fields.update(overrides)
    return Task(**fields)


def gt2_ok() -> FakeResponse:
    return FakeResponse(fixture("gt2_session.json"))


def gfct_grid() -> FakeResponse:
    return FakeResponse(fixture("gfct_grid.json"))


def ca_solved() -> FakeResponse:
    return FakeResponse(fixture("ca_answered.json"))


class StubSolver:
    """Sibling solver with a canned result, used for the variant dispatch."""

    info = None

    def __init__(self, result: SolverResult) -> None:
        self._result = result
        self.prepared = False
        self.shut_down = False
        self.tasks: list[Task] = []

    async def prepare(self, ctx: Any) -> None:
        self.prepared = True

    async def shutdown(self) -> None:
        self.shut_down = True

    async def solve_http(self, task: Task, ctx: Any) -> SolverResult:
        self.tasks.append(task)
        return self._result


def stub_class(name: str, result: SolverResult, types: tuple[CaptchaType, ...] = ()) -> type:
    """Build a solver class exposing info.name plus a shared instance slot."""
    instance = StubSolver(result)
    meta = type("Info", (), {"name": name, "experimental": True, "types": types})()

    class _Stub(StubSolver):
        info = meta

        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            return instance

    return _Stub


class StubRegistry:
    """Minimal SolverRegistry face: by-name lookup plus pick()."""

    def __init__(self, solvers: list[type]) -> None:
        self._solvers = solvers

    def all_solvers(self) -> list[type]:
        return list(self._solvers)

    def pick(self, captcha_type: CaptchaType, *, allow_experimental: bool = True) -> type | None:
        for solver in self._solvers:
            if captcha_type in getattr(solver.info, "types", ()):
                return solver
        return None


class StubSession:
    """Browser session stand-in: one page that yields scripted JS results."""

    def __init__(self, states: list[dict[str, Any]], *, host: str = "www.example.com") -> None:
        self.spec: Any = None
        self.job_id = "job-1"
        self._states = list(states)
        self.navigated: list[str] = []
        self.evaluate_calls = 0
        self.page = _StubPage(self)
        self.host = host

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 60_000) -> Any:
        self.navigated.append(url)
        return self.page

    async def close(self) -> None:
        return None


class _StubPage:
    def __init__(self, session: StubSession) -> None:
        self._session = session

    async def evaluate(self, script: str) -> dict[str, Any]:
        self._session.evaluate_calls += 1
        if not self._session._states:
            return {"token": None, "host": self._session.host, "challenge_shown": True, "ua": "", "error": None}
        state = self._session._states.pop(0)
        state.setdefault("host", self._session.host)
        state.setdefault("ua", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Test/1.0")
        return state


class FakeProvider:
    """BrowserProvider fake: hands out one scripted session per call."""

    def __init__(self, states: list[dict[str, Any]]) -> None:
        self._states = states
        self.specs: list[Any] = []
        self.sessions: list[StubSession] = []

    def session(self, spec: Any) -> Any:
        provider = self

        class _CM:
            async def __aenter__(self) -> StubSession:
                provider.specs.append(spec)
                session = StubSession(provider._states)
                session.spec = spec
                provider.sessions.append(session)
                return session

            async def __aexit__(self, *exc: Any) -> None:
                return None

        return _CM()
