"""FunCaptcha browser lane: let the real widget mint its own token.

The HTTP path answers the games we model; this path answers everything else by
rendering the page the widget lives on and reading the fc-token field the
widget fills in after its own challenge. One fresh session per task through
ctx.browser, exactly like hcaptcha/browser.py.

What this solver does not do: type into the widget, patch its JS, or replay a
captured token. If the page never produces a token, that is a
ChallengeExpiredError with the reason, not a fabricated success.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from captcha.domain import SolveMode, SolverResult, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.solvers import SolverContext

try:  # browser-core's port; older trees do not have it
    from captcha.domain.ports import BrowserSpec
except ImportError:  # pragma: no cover - only on the pre-browser baseline
    BrowserSpec = SimpleNamespace  # type: ignore[assignment,misc]

DEFAULT_TIMEOUT_S = 90.0
MIN_TIMEOUT_S = 10.0
POLL_INTERVAL_S = 1.0
MAX_URL_LEN = 2048

#: Reads the field the widget fills after it finishes its own challenge.
POLL_JS = r"""
(function () {
  function readToken() {
    var el = document.querySelector('input[name="fc-token"], input#fc-token, [id^="fc-token"]');
    if (el) {
      var value = el.value || el.getAttribute('value');
      if (value) return value;
    }
    var meta = document.querySelector('meta[name="fc-token"]');
    if (meta) return meta.getAttribute('content');
    return null;
  }
  var iframe = document.querySelector('iframe[src*="arkoselabs"], iframe[src*="funcaptcha"]');
  return {
    token: readToken(),
    host: location.host,
    challenge_shown: !!iframe,
    ua: navigator.userAgent,
    error: null
  };
})()
"""


async def solve(task: Task, ctx: SolverContext, *, solver: Any) -> SolverResult:
    if ctx.browser is None:
        raise ConfigError(
            "funCaptcha browser lane needs ctx.browser (BrowserProvider)",
            context={"solver": getattr(getattr(solver, "info", None), "name", "")},
        )
    url = _page_url(task)
    timeout_s = _timeout_s(task)
    started = time.perf_counter()

    async with ctx.browser.session(_spec(task)) as session:
        page = await _goto(session, url, timeout_s)
        state = await _poll(page, timeout_s)

    token = state.get("token")
    if not token:
        reason = "challenge_shown" if state.get("challenge_shown") else str(state.get("error") or "no_token")
        raise ChallengeExpiredError(
            f"funCaptcha issued no token ({reason})",
            context={"reason": reason, "url": url, "host": state.get("host") or ""},
        )
    if not isinstance(token, str) or len(token) < 16:
        raise UnsolvableError(
            "funCaptcha token shape is not a token",
            context={"length": len(str(token)), "url": url},
        )
    return SolverResult(
        token=token,
        solve_mode=SolveMode.BROWSER,
        extra={"userAgent": str(state.get("ua") or task.user_agent or ""), "path": "browser"},
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


def _spec(task: Task) -> Any:
    return BrowserSpec(
        proxy=task.proxy,
        cookies=_cookies((task.payload or {}).get("cookies")),
        headless=True,
        label=f"funcaptcha:{task.id}",
    )


def _cookies(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [{"name": str(name), "value": str(value)} for name, value in raw.items()]
    return []


def _page_url(task: Task) -> str:
    payload = task.payload or {}
    url = task.site_url or payload.get("websiteURL") or payload.get("pageurl")
    if not isinstance(url, str) or not url.strip():
        raise UnsolvableError(
            "funcaptcha browser lane needs the page the widget sits on",
            context={"type": task.type.value},
        )
    cleaned = url.strip()
    scheme = cleaned.split("://", 1)[0].lower() if "://" in cleaned else ""
    if scheme not in ("http", "https") or len(cleaned) > MAX_URL_LEN:
        raise UnsolvableError(
            "websiteURL must be a short http(s) URL",
            context={"scheme": scheme, "length": len(cleaned)},
        )
    return cleaned


async def _goto(session: Any, url: str, timeout_s: float) -> Any:
    goto = getattr(session, "goto", None)
    timeout_ms = int(timeout_s * 1000)
    if goto is not None:
        result = goto(url, wait_until="domcontentloaded", timeout_ms=timeout_ms)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    page = await session.page()
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    return page


async def _poll(page: Any, timeout_s: float) -> dict[str, Any]:
    """Poll the page until the widget writes its token or the budget runs out."""
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {"error": "no_answer"}
    while time.monotonic() < deadline:
        outcome = page.evaluate(POLL_JS)
        if asyncio.iscoroutine(outcome):
            outcome = await outcome
        if isinstance(outcome, dict):
            last = outcome
            if outcome.get("token"):
                return outcome
        await asyncio.sleep(POLL_INTERVAL_S)
    return last


def _timeout_s(task: Task) -> float:
    timeout = DEFAULT_TIMEOUT_S
    if task.deadline_ts:
        remaining = task.deadline_ts - time.time()
        if 0 < remaining < timeout:
            timeout = remaining
    return max(timeout, MIN_TIMEOUT_S)
