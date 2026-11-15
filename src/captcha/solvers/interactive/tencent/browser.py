"""Tencent browser lane: read back the ticket the widget minted in-page.

`cap_union_new_verify` is called by the widget itself from inside the page, so
the only thing a solver can do from outside is watch for the result the page
kept: the `ticket`/`randstr` pair in a form field, or the object the SDK handed
to the site's callback (many hosts stash it on `window`).

What this lane does not do: drive the slider or forge a `collect`. If the page
needs an interaction it will show a challenge and never produce a ticket — that
is a ChallengeExpiredError with the reason, not a fabricated success.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from captcha.domain import SolveMode, SolverResult, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.domain.ports import BrowserSpec
from captcha.solvers.base import SolverContext

DEFAULT_TIMEOUT_S = 90.0
MIN_TIMEOUT_S = 10.0
POLL_INTERVAL_S = 1.0
MAX_URL_LEN = 2048

#: Reads the ticket/randstr the widget leaves for the host page.
POLL_JS = r"""
(function () {
  function value(selector) {
    var el = document.querySelector(selector);
    return el ? (el.value || el.getAttribute('value') || el.getAttribute('content') || '') : '';
  }
  var ticket = value('input[name="ticket"], input#ticket, [data-tcaptcha-ticket]');
  var randstr = value('input[name="randstr"], input#randstr, [data-tcaptcha-randstr]');
  if (!ticket) {
    for (var key in window) {
      try {
        var v = window[key];
        if (v && typeof v === 'object' && typeof v.ticket === 'string' && v.ticket) {
          ticket = v.ticket;
          randstr = randstr || (typeof v.randstr === 'string' ? v.randstr : '');
          break;
        }
      } catch (e) {}
    }
  }
  return {
    ticket: ticket,
    randstr: randstr,
    challenge_shown: !!document.querySelector('iframe[src*="captcha"], .tcaptcha-transform, #tcaptcha_iframe'),
    host: location.host,
    ua: navigator.userAgent,
    error: null
  };
})()
"""


async def solve(task: Task, ctx: SolverContext, *, solver: Any) -> SolverResult:
    if ctx.browser is None:
        raise ConfigError(
            "tencent browser lane needs ctx.browser (BrowserProvider)",
            context={"solver": getattr(getattr(solver, "info", None), "name", "")},
        )
    url = _page_url(task)
    timeout_s = _timeout_s(task)
    started = time.perf_counter()

    async with ctx.browser.session(_spec(task)) as session:
        page = await _goto(session, url, timeout_s)
        state = await _poll(page, timeout_s)

    ticket = state.get("ticket")
    if not ticket:
        reason = "challenge_shown" if state.get("challenge_shown") else str(state.get("error") or "no_ticket")
        raise ChallengeExpiredError(
            f"tencent widget produced no ticket ({reason})",
            context={"reason": reason, "url": url, "host": state.get("host") or ""},
        )
    return SolverResult(
        token=str(ticket),
        text=str(state.get("randstr") or "") or None,
        extra={
            "ticket": str(ticket),
            "randstr": str(state.get("randstr") or ""),
            "userAgent": str(state.get("ua") or task.user_agent or ""),
            "path": "browser",
        },
        solve_mode=SolveMode.BROWSER,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


def _spec(task: Task) -> BrowserSpec:
    return BrowserSpec(
        proxy=task.proxy,
        cookies=_cookies((task.payload or {}).get("cookies")),
        headless=True,
        label=f"tencent:{task.id}",
    )


def _cookies(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [{"name": str(name), "value": str(value)} for name, value in raw.items()]
    return []


def _page_url(task: Task) -> str:
    payload = task.payload or {}
    url = task.site_url or payload.get("pageurl") or payload.get("websiteURL")
    if not isinstance(url, str) or not url.strip():
        raise UnsolvableError(
            "tencent browser lane needs the page the widget sits on",
            context={"type": task.type.value},
        )
    cleaned = url.strip()
    scheme = cleaned.split("://", 1)[0].lower() if "://" in cleaned else ""
    if scheme not in ("http", "https") or len(cleaned) > MAX_URL_LEN:
        raise UnsolvableError(
            "pageurl must be a short http(s) URL",
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
    """Poll the page until the widget leaves a ticket or the budget runs out."""
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {"error": "no_answer"}
    while time.monotonic() < deadline:
        outcome = page.evaluate(POLL_JS)
        if asyncio.iscoroutine(outcome):
            outcome = await outcome
        if isinstance(outcome, dict):
            last = outcome
            if outcome.get("ticket"):
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
