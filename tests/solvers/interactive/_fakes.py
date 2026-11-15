"""Test doubles for the interactive vendor solvers: canned HTTP, fake page.

No network and no browser. `HTTP` answers by URL and records every call so the
tests can pin the exact vendor parameters; `FakeProvider` hands the solver one
`FakeSession` with a `FakePage` whose `evaluate` answers by substring match.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from captcha.domain import CaptchaType, Task
from captcha.domain.enums import SolveMode
from captcha.domain.task import new_task_id


class Reply:
    """Minimal stand-in for `captcha.adapters.transport.Response`."""

    def __init__(self, payload: Any = None, *, body: bytes = b"", status: int = 200) -> None:
        self.status = status
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
    """A 200 reply that is not JSON at all."""

    status = 200

    def json(self) -> Any:
        raise ValueError("not json")


class HTTP:
    """Canned reply per URL; every call is recorded."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _reply(self, url: str) -> Any:
        if url not in self.replies:
            raise AssertionError(f"unexpected HTTP call: {url}")
        reply = self.replies[url]
        if isinstance(reply, list):
            if not reply:
                raise AssertionError(f"no replies left for: {url}")
            reply = reply.pop(0)
        return reply if isinstance(reply, Reply | Garbage) else Reply(reply)

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("GET", url, kwargs))
        return self._reply(url)

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("POST", url, kwargs))
        return self._reply(url)

    def call(self, method: str, url: str) -> dict[str, Any]:
        for recorded_method, recorded_url, kwargs in self.calls:
            if recorded_method == method and recorded_url == url:
                return kwargs
        raise AssertionError(f"no {method} call to {url}; calls={[c[:2] for c in self.calls]}")


class FakePage:
    """`evaluate` answers the first value whose key is a substring of the script."""

    def __init__(self, values: dict[str, Any] | None = None, *, ua: str | None = None) -> None:
        self.values = dict(values or {})
        self.ua = ua
        self.scripts: list[tuple[str, Any]] = []
        self.clicks: list[str] = []
        self.fail_click = False

    def _lookup(self, script: str) -> Any:
        for key, value in self.values.items():
            if key in script:
                if isinstance(value, list):
                    return value.pop(0) if value else None
                return value
        if "navigator.userAgent" in script:
            return self.ua or ""
        return None

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.scripts.append((script, arg))
        return self._lookup(script)

    async def click(self, selector: str, timeout: int | None = None) -> None:
        self.clicks.append(selector)
        if self.fail_click:
            raise RuntimeError("not clickable")


class FakeContext:
    def __init__(self, jar: list[dict[str, Any]] | None = None) -> None:
        self.jar = jar or []
        self.init_scripts: list[str] = []

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def cookies(self) -> list[dict[str, Any]]:
        return self.jar


class FakeSession:
    def __init__(self, page: FakePage, jar: list[dict[str, Any]] | None = None) -> None:
        self._page = page
        self.context = FakeContext(jar)
        self.gotos: list[tuple[str, str, int]] = []

    async def page(self) -> FakePage:
        return self._page

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 60_000) -> FakePage:
        self.gotos.append((url, wait_until, timeout_ms))
        return self._page


class FakeProvider:
    def __init__(self, page: FakePage, jar: list[dict[str, Any]] | None = None) -> None:
        self.page = page
        self.jar = jar
        self.specs: list[Any] = []
        self.sessions: list[FakeSession] = []
        self.closed = 0

    @asynccontextmanager
    async def session(self, spec: Any):
        self.specs.append(spec)
        session = FakeSession(self.page, self.jar)
        self.sessions.append(session)
        try:
            yield session
        finally:
            self.closed += 1


def make_task(
    captcha_type: CaptchaType,
    payload: dict[str, Any] | None = None,
    *,
    site_url: str | None = None,
    sitekey: str | None = None,
    user_agent: str | None = None,
    proxy: str | None = None,
    mode: SolveMode = SolveMode.HTTP,
) -> Task:
    return Task(
        id=new_task_id(),
        type=captcha_type,
        payload=payload or {},
        site_url=site_url,
        sitekey=sitekey,
        user_agent=user_agent,
        proxy=proxy,
        preferred_mode=mode,
    )
