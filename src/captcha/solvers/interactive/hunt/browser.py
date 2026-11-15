"""Hunt widget browser lane: let the page SDK mint the token.

CapMonster's Hunt integration documents two server modes plus a widget mode:
when the page exposes the widget URL, the SDK runs in the page and the result
is the token in solution.data.token. The HTTP solver cannot reproduce that
because the SDK signs its requests with an in-page fingerprint (X-HD); this
module opens the widget once, per task, and reads the token the SDK writes.

What it does not do: fake a token, click through a challenge, or keep a
session alive across tasks. If the widget never produces a token, that is a
ChallengeExpiredError with the observed state, not a success.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from captcha.domain import SolveMode, SolverResult, Task
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.domain.ports import BrowserSpec
from captcha.solvers.base import SolverContext

DEFAULT_TIMEOUT_S = 90.0
MIN_TIMEOUT_S = 10.0
POLL_INTERVAL_S = 1.0
MAX_URL_LEN = 2048

#: Reads the token the widget writes after its own challenge. The field names
#: differ per integrator, so this checks the common carriers instead of one
#: selector; FILL: pin the exact one after a capture on the target site.
POLL_JS = r"""
(function () {
  function read(sel) {
    var el = document.querySelector(sel);
    if (!el) return '';
    return el.value || el.getAttribute('content') || el.getAttribute('data-token') ||
           el.getAttribute('data-hunt-token') || el.textContent || '';
  }
  var token = read('input[name="token"]') || read('input[name*="hunt" i]') ||
              read('meta[name="token"]') || read('[data-hunt-token]') ||
              read('#hunt-token') || read('#token');
  if (!token && window.__HUNT__ && window.__HUNT__.token) token = window.__HUNT__.token;
  if (!token && window.meta && window.meta.token) token = window.meta.token;
  var iframe = document.querySelector('iframe[src*="hunt"], iframe[src*="hd-api"]');
  return {
    token: token,
    host: location.host,
    ua: navigator.userAgent,
    challenge_shown: !!iframe
  };
})()
"""


async def solve(task: Task, ctx: SolverContext, *, solver: Any) -> SolverResult:
    """Render the widget page and return solution.data.token."""
    if ctx.browser is None:
        raise ConfigError(
            "hunt widget mode needs ctx.browser (BrowserProvider)",
            context={"solver": getattr(getattr(solver, "info", None), "name", "")},
        )
    url = widget_url(task)
    timeout = _timeout_s(task)
    started = time.perf_counter()
    async with ctx.browser.session(_spec(task)) as session:
        page = await _goto(session, url, timeout)
        state = await _poll(page, timeout)

    token = state.get("token")
    if not token:
        reason = "challenge_shown" if state.get("challenge_shown") else str(state.get("error") or "no_token")
        raise ChallengeExpiredError(
            f"hunt widget issued no token ({reason})",
            context={"reason": reason, "url": url, "host": str(state.get("host") or "")},
        )
    if not isinstance(token, str) or len(token) < 8:
        raise UnsolvableError(
            "hunt widget token shape is not a token",
            context={"length": len(str(token)), "url": url},
        )
    return SolverResult(
        token=token,
        extra={
            "data": {"token": token},
            "userAgent": str(state.get("ua") or task.user_agent or ""),
        },
        solve_mode=SolveMode.BROWSER,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


def widget_url(task: Task) -> str:
    """Full widget URL when given, else the page that hosts the widget."""
    payload = task.payload
    raw = payload.get("widget_url") or payload.get("widgetUrl") or payload.get("pageurl")
    url = str(raw or task.site_url or "").strip()
    if not url:
        raise UnsolvableError(
            "hunt widget mode needs widgetUrl or the page URL",
            context={"key": task.sitekey},
        )
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme not in ("http", "https") or len(url) > MAX_URL_LEN:
        raise UnsolvableError(
            "hunt widget URL must be a short http(s) URL",
            context={"scheme": scheme, "length": len(url)},
        )
    return url


def _spec(task: Task) -> BrowserSpec:
    return BrowserSpec(
        proxy=task.proxy,
        cookies=_cookies((task.payload or {}).get("cookies")),
        headless=True,
        label=f"hunt:{task.id}",
    )


def _cookies(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [{"name": str(name), "value": str(value)} for name, value in raw.items()]
    return []


async def _goto(session: Any, url: str, timeout_s: float) -> Any:
    goto = getattr(session, "goto", None)
    timeout_ms = int(timeout_s * 1000)
    if goto is not None:
        result = goto(url, wait_until="domcontentloaded", timeout_ms=timeout_ms)
        return await result if asyncio.iscoroutine(result) else result
    page = await session.page()
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    return page


async def _poll(page: Any, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {"error": "no_answer"}
    while time.monotonic() < deadline:
        try:
            outcome = page.evaluate(POLL_JS)
            if asyncio.iscoroutine(outcome):
                outcome = await outcome
        except CaptchaError:
            raise
        except Exception as exc:
            last = {"error": f"evaluate: {str(exc)[:120]}"}
        else:
            if isinstance(outcome, dict):
                last = outcome
                if outcome.get("token"):
                    return outcome
        if POLL_INTERVAL_S > 0:
            await asyncio.sleep(POLL_INTERVAL_S)
    return last


def _timeout_s(task: Task) -> float:
    timeout = DEFAULT_TIMEOUT_S
    if task.deadline_ts:
        remaining = task.deadline_ts - time.time()
        if 0 < remaining < timeout:
            timeout = remaining
    return max(timeout, MIN_TIMEOUT_S)
