"""atbCaptcha browser lane: read the `captchaVerification` the SDK leaves behind.

After its own challenge the SDK posts `check` from inside the page and hands
`captchaVerification` to the site — usually a hidden input, sometimes a global.
That value is the only thing the site's backend accepts, so this lane watches
for it instead of replaying an HTTP check from outside.

What this lane does not do: drive the slider or forge the seal. A page that
still shows a challenge produces nothing — ChallengeExpiredError with the
reason, not a fabricated success.
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
MIN_TOKEN_LEN = 16

#: Reads the verification the SDK leaves for the host page.
POLL_JS = r"""
(function () {
  function fromInputs() {
    var inputs = document.querySelectorAll('input');
    for (var i = 0; i < inputs.length; i++) {
      var el = inputs[i];
      var name = (el.name || el.id || '').toLowerCase();
      var value = el.value || '';
      if (value && (name.indexOf('captchaverification') !== -1 || name.indexOf('captcha_verification') !== -1)) {
        return value;
      }
    }
    return '';
  }
  var token = fromInputs();
  if (!token) {
    for (var key in window) {
      try {
        var v = window[key];
        if (typeof v === 'string' && v.length > 16 && key.toLowerCase().indexOf('captchaverification') !== -1) {
          token = v;
          break;
        }
        if (v && typeof v === 'object' && typeof v.captchaVerification === 'string' && v.captchaVerification) {
          token = v.captchaVerification;
          break;
        }
      } catch (e) {}
    }
  }
  return {
    token: token,
    challenge_shown: !!document.querySelector('.atb-captcha, .cap-captcha, iframe[src*="aisecurius"]'),
    host: location.host,
    ua: navigator.userAgent,
    error: null
  };
})()
"""


async def solve(task: Task, ctx: SolverContext, *, solver: Any) -> SolverResult:
    if ctx.browser is None:
        raise ConfigError(
            "atbcaptcha browser lane needs ctx.browser (BrowserProvider)",
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
        reason = "challenge_shown" if state.get("challenge_shown") else str(state.get("error") or "no_verification")
        raise ChallengeExpiredError(
            f"atbcaptcha SDK produced no captchaVerification ({reason})",
            context={"reason": reason, "url": url, "host": state.get("host") or ""},
        )
    return SolverResult(
        token=str(token),
        extra={
            "captchaVerification": str(token),
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
        label=f"atbcaptcha:{task.id}",
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
            "atbcaptcha browser lane needs the page the widget sits on",
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
