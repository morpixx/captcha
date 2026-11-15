"""Canned HTTP/browser doubles shared by the interactive vendor tests.

No sockets. Every reply is keyed by URL and every browser state by evaluate
call order, so a test reads as a script of the vendor conversation.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import msgspec

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.task import new_task_id


class Reply:
    """Minimal stand-in for captcha.adapters.transport.Response."""

    def __init__(
        self,
        payload: Any = None,
        *,
        body: bytes = b"",
        text: str | None = None,
        status: int = 200,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        if text is not None:
            self.body = text.encode()
        else:
            self.body = body or (b"" if payload is None else json.dumps(payload).encode())
        self.status = status
        self.headers = headers or []
        self._payload = payload

    @property
    def text(self) -> str:
        return self.body.decode()

    def json(self) -> Any:
        if self._payload is not None:
            return self._payload
        return json.loads(self.body)


class FakeHTTP:
    """Canned reply per URL; every call is recorded with its kwargs."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _reply(self, url: str) -> Reply:
        if url not in self.replies:
            raise AssertionError(f"unexpected HTTP call: {url}")
        reply = self.replies[url]
        return reply if isinstance(reply, Reply) else Reply(reply)

    async def get(self, url: str, **kwargs: Any) -> Reply:
        self.calls.append(("GET", url, kwargs))
        return self._reply(url)

    async def post(self, url: str, **kwargs: Any) -> Reply:
        self.calls.append(("POST", url, kwargs))
        return self._reply(url)


def make_task(
    captcha_type: CaptchaType,
    payload: dict[str, Any],
    *,
    site_url: str | None = None,
    user_agent: str | None = None,
) -> Task:
    return Task(
        id=new_task_id(),
        type=captcha_type,
        payload=payload,
        preferred_mode=SolveMode.HTTP,
        site_url=site_url,
        user_agent=user_agent,
    )


def stub_cv(monkeypatch: Any, solver_cls: type, result: SolverResult) -> list[tuple[CaptchaType, dict[str, Any]]]:
    """Replace CV delegation with a canned geometric answer."""
    calls: list[tuple[CaptchaType, dict[str, Any]]] = []

    async def fake(self: Any, kind: CaptchaType, task: Task, ctx: Any, payload: dict[str, Any]):
        calls.append((kind, payload))
        return result

    monkeypatch.setattr(solver_cls, "_solve_cv", fake)
    return calls


def builtins(result: SolverResult) -> dict[str, Any]:
    return msgspec.to_builtins(result)


class FakePage:
    """Returns scripted evaluate() states; the last state repeats."""

    def __init__(self, states: list[dict[str, Any]]) -> None:
        self.states = states
        self.calls = 0

    async def evaluate(self, script: str) -> dict[str, Any]:
        index = min(self.calls, len(self.states) - 1)
        self.calls += 1
        return self.states[index]


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.url = ""

    async def goto(self, url: str, **kwargs: Any) -> FakePage:
        self.url = url
        return self._page

    async def page(self) -> FakePage:
        return self._page


class FakeBrowser:
    """BrowserProvider protocol: session(spec) is an async context manager."""

    def __init__(self, session: FakeSession) -> None:
        self._session = session
        self.specs: list[Any] = []

    def session(self, spec: Any) -> Any:
        self.specs.append(spec)

        @asynccontextmanager
        async def _open():
            yield self._session

        return _open()
