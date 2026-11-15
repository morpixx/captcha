"""The Turnstile page script and token shape.

The widget has to mint its token on a real page: the bundle probes the browser
and Cloudflare's proof is server-issued (docs/reverse-2026.md §3). This module
owns the script that puts a widget on whatever page the client named, plus the
checks that decide whether what came back is a token or a placeholder -- a
sitekey echo sold as a solution is worse than an obvious failure, because the
client pays for a task that cannot pass.

Argument: {sitekey, action, cData, invisible, timeoutMs, expectedHost}.
Returns: {host, href, ua, token, error, diag} -- never raises, so the solver can
classify "challenge shown" vs "wrong origin" vs "widget rejected the sitekey"
from data instead of a stack trace.
"""

from __future__ import annotations

import re
from typing import Any

from captcha.domain.errors import SensorError, UnsolvableError

from ._common import maybe_await
from .turnstile_wire import TurnstileParams

#: Turnstile responses look like "0.<payload>" / "1.<payload>" (2026-08 capture).
#: Anything else is a placeholder the widget returns when it failed.
TOKEN_RE = re.compile(r"^\d\.[A-Za-z0-9._~+/=-]{10,}$")
MIN_TOKEN_LEN = 20

#: Public Turnstile error codes that mean the task, not the attempt, is wrong:
#: unknown/invalid sitekey, invalid action/cData/parameter, invalid widget.
#: A retry with the same payload cannot succeed, so they are terminal.
TERMINAL_WIDGET_CODES = frozenset(
    {
        "110200",
        "110420",
        "110430",
        "110440",
        "110520",
        "110600",
        "110610",
        "110620",
        "110630",
        "400020",
        "400070",
    }
)

DRIVE_JS = r"""
async ({sitekey, action, cData, invisible, timeoutMs, expectedHost}) => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const FIELD = 'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]';
  const out = {
    host: location.hostname,
    href: location.href,
    ua: navigator.userAgent || '',
    token: null,
    error: null,
    diag: {api: false, injected: false, existing: false, rendered: false, executed: false, callback: false, errorCode: null},
  };
  const sameSite = (actual, expected) => actual === expected
    || actual.endsWith('.' + expected) || expected.endsWith('.' + actual);
  if (expectedHost && !sameSite(location.hostname, expectedHost)) {
    out.error = 'wrong-origin';
    return out;
  }
  const fromField = () => {
    const el = document.querySelector(FIELD);
    return el && typeof el.value === 'string' ? el.value.trim() : '';
  };
  const fromApi = () => {
    try {
      if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
        const value = window.turnstile.getResponse();
        return typeof value === 'string' ? value.trim() : '';
      }
    } catch (e) { out.diag.errorCode = String(e); }
    return '';
  };
  const poll = () => {
    if (out.token) return true;
    const value = fromField() || fromApi();
    if (value) out.token = value;
    return !!out.token;
  };
  if (poll()) { out.diag.existing = true; return out; }
  const state = window.__cfTurnstileState = window.__cfTurnstileState || {widgetId: null};
  out.diag.api = typeof window.turnstile === 'object' && window.turnstile !== null;
  if (!out.diag.api) {
    await new Promise((resolve) => {
      const script = document.createElement('script');
      script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => resolve();
      (document.head || document.documentElement).appendChild(script);
    });
    out.diag.injected = true;
    out.diag.api = typeof window.turnstile === 'object' && window.turnstile !== null;
  }
  const render = () => {
    if (state.widgetId !== null) return true;
    if (!window.turnstile || typeof window.turnstile.render !== 'function') return false;
    let container = document.getElementById('cf-turnstile-solver');
    if (!container) {
      container = document.createElement('div');
      container.id = 'cf-turnstile-solver';
      container.style.cssText = invisible
        ? 'position:fixed;left:-9999px;top:0;'
        : 'position:fixed;right:8px;bottom:8px;z-index:2147483647;';
      (document.body || document.documentElement).appendChild(container);
    }
    const options = {
      sitekey,
      callback: () => { out.diag.callback = true; },
      'error-callback': (code) => { out.diag.errorCode = String(code); },
      'timeout-callback': () => { out.diag.errorCode = 'timeout'; },
      'expired-callback': () => { out.diag.errorCode = 'expired'; },
    };
    if (action) options.action = action;
    if (cData) options.cData = cData;
    if (invisible) { options.appearance = 'execute'; options.execution = 'execute'; }
    try {
      state.widgetId = window.turnstile.render(container, options);
      out.diag.rendered = true;
    } catch (e) {
      out.error = 'render-failed';
      out.diag.errorCode = String(e);
      return false;
    }
    if (invisible && typeof window.turnstile.execute === 'function') {
      try { window.turnstile.execute(state.widgetId); out.diag.executed = true; }
      catch (e) { out.diag.errorCode = String(e); }
    }
    return true;
  };
  render();
  const step = 250;
  for (let waited = 0; waited < timeoutMs && !poll(); waited += step) {
    await sleep(step);
    // An error-callback ends the wait: Python classifies retryable vs terminal
    // from the code, and a terminal one must not burn the whole budget.
    if (out.diag.errorCode) break;
    if (state.widgetId === null && out.diag.api) render();
  }
  poll();
  if (!out.token && !out.error && out.diag.errorCode) out.error = 'widget-error:' + out.diag.errorCode;
  return out;
}
"""


async def drive_widget(page: Any, *, params: TurnstileParams, timeout_ms: int, expected_host: str) -> dict[str, Any]:
    """Run the page script and return its report; the solver does the policy."""
    arg = {
        "sitekey": params.sitekey,
        "action": params.action,
        "cData": params.cdata,
        "invisible": params.invisible,
        "timeoutMs": max(int(timeout_ms), 500),
        "expectedHost": expected_host,
    }
    out = await maybe_await(page.evaluate(DRIVE_JS, arg))
    if not isinstance(out, dict):
        raise SensorError(
            "turnstile: page script returned no result",
            context={"type": type(out).__name__, "sitekey": params.sitekey},
        )
    return out


async def click_checkbox(page: Any) -> bool:
    """Best-effort click on the widget's checkbox inside its cross-origin frame.

    A visible widget may need the interaction; an invisible one never renders
    the checkbox and simply falls through.
    """
    frames = getattr(page, "frames", None)
    frames = frames() if callable(frames) else frames
    for frame in frames or ():
        if "challenges.cloudflare.com" not in str(getattr(frame, "url", "")):
            continue
        locator = getattr(frame, "locator", None)
        if locator is None:
            continue
        try:
            target = locator("input[type='checkbox'], [role='checkbox']")
            if not await maybe_await(target.count()):
                continue
            first = getattr(target, "first", target)
            await maybe_await(first.click(timeout=5000))
            return True
        except Exception:
            continue
    return False


def error_code(out: dict[str, Any]) -> str | None:
    diag = out.get("diag") if isinstance(out.get("diag"), dict) else {}
    raw = diag.get("errorCode") or out.get("error")
    text = str(raw or "")
    match = re.search(r"\d{5,6}", text)
    if match:
        return match.group(0)
    return text or None


def token_problem(value: Any, *, sitekey: str) -> str | None:
    token = _token_text(value)
    if not token:
        return "empty token"
    if token == sitekey:
        return "the widget echoed the sitekey"
    if len(token) < MIN_TOKEN_LEN:
        return "too short for a Turnstile token"
    if any(ch.isspace() for ch in token):
        return "contains whitespace"
    if not TOKEN_RE.match(token):
        return "unexpected token shape"
    return None


def _token_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("token") or value.get("response")
    return value.strip() if isinstance(value, str) else ""


def validate_token(value: Any, *, sitekey: str) -> str:
    """Reject placeholders before they reach a client as a paid success."""
    problem = token_problem(value, sitekey=sitekey)
    if problem is not None:
        raise UnsolvableError(
            f"turnstile: unusable token ({problem})",
            context={"sitekey": sitekey, "problem": problem},
        )
    return _token_text(value)
