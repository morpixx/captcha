"""Shared doubles for the interactive-vendor tests.

No network, no browser: `_HTTP` answers from a URL -> reply table and records
every call, `FakeProvider` hands out a session whose `evaluate` replays a
scripted list of page states. The same shapes are used by every vendor test in
this directory, so a flow test reads as the wire conversation it asserts.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext


class Reply:
    """Minimal stand-in for `captcha.adapters.transport.Response`."""

    def __init__(self, payload: Any = None, *, body: bytes = b"", status: int = 200, text: str | None = None) -> None:
        self.status = status
        if text is not None:
            self.body = text.encode()
            self._payload = None
        else:
            self.body = body or (b"" if payload is None else json.dumps(payload).encode())
            self._payload = payload

    @property
    def text(self) -> str:
        return self.body.decode()

    def json(self) -> Any:
        if self._payload is not None:
            return self._payload
        return json.loads(self.body)


class Garbage:
    """A reply that is not JSON at all (bot wall, HTML error page)."""

    status = 502

    @property
    def text(self) -> str:
        return "<html>blocked</html>"

    def json(self) -> Any:
        raise ValueError("not json")


class HTTP:
    """Canned reply per URL; every call is recorded as (method, url, kwargs)."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _reply(self, url: str) -> Any:
        if url not in self.replies:
            raise AssertionError(f"unexpected HTTP call: {url}")
        reply = self.replies[url]
        return reply if isinstance(reply, (Reply, Garbage)) else Reply(reply)

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("GET", url, kwargs))
        return self._reply(url)

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("POST", url, kwargs))
        return self._reply(url)

    def by_method(self, method: str) -> list[tuple[str, str, dict[str, Any]]]:
        return [call for call in self.calls if call[0] == method]


def task(captcha_type: CaptchaType, payload: dict[str, Any], **fields: Any) -> Task:
    return Task(id=new_task_id(), type=captcha_type, payload=payload, preferred_mode=SolveMode.HTTP, **fields)


def stub_cv(
    monkeypatch: pytest.MonkeyPatch,
    solver_cls: type,
    *,
    offset: float = 120.0,
    coords: list[tuple[float, float]] | None = None,
) -> list[tuple[CaptchaType, dict[str, Any]]]:
    """Replace CV delegation with a canned geometric answer."""
    calls: list[tuple[CaptchaType, dict[str, Any]]] = []
    points = coords if coords is not None else [(10.0, 20.0)]

    async def fake(self: Any, kind: CaptchaType, parent: Task, ctx: SolverContext, payload: dict[str, Any]):
        calls.append((kind, payload))
        if kind is CaptchaType.CLICK:
            return SolverResult(coords=list(points))
        return SolverResult(offset=offset)

    monkeypatch.setattr(solver_cls, "_solve_cv", fake)
    return calls


def run_http(solver: Any, t: Task, ctx: SolverContext) -> SolverResult:
    return asyncio.run(solver.solve_http(t, ctx))


# ---- browser lane doubles ---------------------------------------------------


class FakePage:
    """Replays a scripted list of `evaluate` results; the last one repeats."""

    def __init__(self, states: list[dict[str, Any]]) -> None:
        if not states:
            raise ValueError("FakePage needs at least one state")
        self.states = list(states)
        self.evaluate_calls = 0

    def evaluate(self, script: str) -> dict[str, Any]:
        self.evaluate_calls += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return state


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self.page_obj = page
        self.navigated: list[str] = []

    async def goto(self, url: str, **kwargs: Any) -> FakePage:
        self.navigated.append(url)
        return self.page_obj

    async def page(self) -> FakePage:
        return self.page_obj


class _SessionCM:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakeProvider:
    """Hands out one `FakeSession` per `session(spec)` call and records the specs."""

    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.specs: list[Any] = []
        self.sessions: list[FakeSession] = []

    def session(self, spec: Any) -> _SessionCM:
        self.specs.append(spec)
        session = FakeSession(self.page)
        self.sessions.append(session)
        return _SessionCM(session)
