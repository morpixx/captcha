"""Aliyun Captcha 2.0 browser lane: read the signed CaptchaVerifyParam.

In Captcha 2.0 the widget does not expose a token over an HTTP API. It renders
in the page, collects device signals, and writes a signed CaptchaVerifyParam
into a hidden field (V2 JSON string or V3 base64). The site backend verifies
that parameter with Aliyun. The only honest way to produce it is to run the
widget's own page, which is what this module does: one fresh session per task,
navigate, wait for the field, return its value.

A widget that shows an interactive challenge and never writes the parameter is
reported as ChallengeExpiredError - not as an empty success.
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

#: The SDK writes the parameter into a hidden input/textarea named
#: CaptchaVerifyParam. FILL: confirm the container on the target site; some
#: builds expose it only through the callback, not the DOM.
POLL_JS = r"""
(function () {
  function read(sel) {
    var el = document.querySelector(sel);
    if (!el) return '';
    return el.value || el.getAttribute('value') || el.textContent || '';
  }
  var param = read('input[name="CaptchaVerifyParam"]') ||
              read('textarea[name="CaptchaVerifyParam"]') ||
              read('#CaptchaVerifyParam') ||
              read('input[name*="CaptchaVerifyParam" i]');
  if (!param && window.__ALIYUN_CAPTCHA_RESULT__) param = String(window.__ALIYUN_CAPTCHA_RESULT__);
  if (!param && window.CaptchaVerifyParam) param = String(window.CaptchaVerifyParam);
  var widget = document.querySelector('[class*="aliyunCaptcha" i], #aliyunCaptcha, [id*="aliyun" i]');
  return {
    param: param,
    sceneId: String(window.__ALIYUN_CAPTCHA_SCENE_ID__ || ''),
    host: location.host,
    ua: navigator.userAgent,
    widget: !!widget
  };
})()
"""


async def solve(task: Task, ctx: SolverContext, *, solver: Any) -> SolverResult:
    if ctx.browser is None:
        raise ConfigError(
            "alibaba 2.0 widget needs ctx.browser (BrowserProvider)",
            context={"solver": getattr(getattr(solver, "info", None), "name", "")},
        )
    url = page_url(task)
    timeout = _timeout_s(task)
    started = time.perf_counter()
    async with ctx.browser.session(_spec(task)) as session:
        page = await _goto(session, url, timeout)
        state = await _poll(page, timeout)

    param = state.get("param")
    if not param:
        reason = "widget_present" if state.get("widget") else str(state.get("error") or "no_param")
        raise ChallengeExpiredError(
            f"aliyun widget issued no CaptchaVerifyParam ({reason})",
            context={"reason": reason, "url": url, "host": str(state.get("host") or "")},
        )
    if not isinstance(param, str) or len(param) < 16:
        raise UnsolvableError(
            "aliyun CaptchaVerifyParam shape is not a parameter",
            context={"length": len(str(param)), "url": url},
        )
    data: dict[str, Any] = {"CaptchaVerifyParam": param, "tokens": param}
    scene = state.get("sceneId")
    if scene:
        data["sceneId"] = str(scene)
    return SolverResult(
        token=param,
        extra={"data": data, "userAgent": str(state.get("ua") or task.user_agent or "")},
        solve_mode=SolveMode.BROWSER,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


def page_url(task: Task) -> str:
    payload = task.payload
    raw = _first(payload, "pageurl", "websiteURL", "websiteUrl", "site_url")
    url = str(raw or task.site_url or "").strip()
    if not url:
        raise UnsolvableError(
            "alibaba 2.0 needs the page that renders the widget",
            context={"keys": sorted(payload)},
        )
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme not in ("http", "https") or len(url) > MAX_URL_LEN:
        raise UnsolvableError(
            "websiteURL must be a short http(s) URL",
            context={"scheme": scheme, "length": len(url)},
        )
    return url


def _spec(task: Task) -> BrowserSpec:
    return BrowserSpec(
        proxy=task.proxy,
        cookies=_cookies((task.payload or {}).get("cookies")),
        headless=True,
        label=f"alibaba:{task.id}",
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
                if outcome.get("param"):
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


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
