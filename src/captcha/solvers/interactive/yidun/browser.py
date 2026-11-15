"""Yidun browser lane: read the `validate` the widget leaves on the page.

The SDK mints `validate` inside the page after its own challenge; the host site
then folds it with `zoneId` into `NECaptchaValidate` for its backend. From
outside we can only observe what the page kept: a `*Validate` form field, or a
validate-looking string the site stashed on `window`.

What this lane does not do: drive the slider or forge `data`/`cb`. A page that
still shows a challenge produces no validate — ChallengeExpiredError with the
reason, not a fabricated token.
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
#: A real validate is a long opaque string; short ones are fingerprints/labels.
MIN_TOKEN_LEN = 16

#: Reads the validate the widget leaves for the host page.
POLL_JS = r"""
(function () {
  function fromInputs() {
    var inputs = document.querySelectorAll('input');
    for (var i = 0; i < inputs.length; i++) {
      var el = inputs[i];
      var name = (el.name || el.id || '').toLowerCase();
      var value = el.value || '';
      if (value && (name.indexOf('validate') !== -1 || name.indexOf('necaptcha') !== -1)) return value;
    }
    return '';
  }
  var token = fromInputs();
  if (!token) {
    for (var key in window) {
      try {
        var v = window[key];
        if (typeof v === 'string' && v.length > 16 && key.toLowerCase().indexOf('validate') !== -1) {
          token = v;
          break;
        }
        if (v && typeof v === 'object' && typeof v.validate === 'string' && v.validate) {
          token = v.validate;
          break;
        }
      } catch (e) {}
    }
  }
  return {
    token: token,
    challenge_shown: !!document.querySelector('.yidun, .yidun_popup, iframe[src*="dun.163.com"]'),
    host: location.host,
    ua: navigator.userAgent,
    error: null
  };
})()
"""


async def solve(task: Task, ctx: SolverContext, *, solver: Any) -> SolverResult:
    if ctx.browser is None:
        raise ConfigError(
            "yidun browser lane needs ctx.browser (BrowserProvider)",
            context={"solver": getattr(getattr(solver, "info", None), "name", "")},
        )
    url = _page_url(task)
    timeout_s = _timeout_s(task)
    started = time.perf_counter()

    async with ctx.browser.session(_spec(task)) as session:
        page = await _goto(session, url, timeout_s)
        state = await _poll(page, timeout_s)

    token = state.get("token")
    if not token or len(str(token)) < MIN_TOKEN_LEN:
        reason = "challenge_shown" if state.get("challenge_shown") else str(state.get("error") or "no_validate")
        raise ChallengeExpiredError(
            f"yidun widget produced no validate ({reason})",
            context={"reason": reason, "url": url, "host": state.get("host") or ""},
        )
    return SolverResult(
        token=str(token),
        extra={
            "validate": str(token),
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
        label=f"yidun:{task.id}",
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
            "yidun browser lane needs the page the widget sits on",
            context={"type": task.type.value},
        )
    cleaned = url.strip()
    scheme = cleaned.split("://", 1)[0].lower() if "://" in cleaned else ""
    if scheme not in ("http", "https") or len(cleaned) > MAX_URL_LEN:
        raise UnsolvableError("pageurl must be a short http(s) URL", context={"scheme": scheme})
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
