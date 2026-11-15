"""hCaptcha wire facts shared by the browser solver and (later) the HTTP one.

Nothing here touches a browser or the network. It is token shape, result
bookkeeping, and the page script that drives the widget. Both paths must agree
on what a valid response token looks like, otherwise one of them will sell a
sitekey echo as a solution — the worst class of bug, because the client sees a
"successful" task whose token the site rejects.

The script below is adapted from the Riot reverse-engineering work
(D:/riot-research/riot_hcaptcha_minter.py, read-only): fetch hCaptcha's own
api.js, eval it inside the page, render a hidden widget bound to the sitekey,
execute it, wait for the callback. A real page executes the real bundle, so the
"n" token it builds is the page's fingerprint, not our guess.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from captcha.domain.errors import CaptchaError, ConfigError, UnsolvableError

#: hCaptcha response tokens are JWE-style strings: P1_ for a normal pass,
#: E0_/E1_ for an enterprise pass. Anything else is a placeholder the widget
#: hands back when it failed (the sitekey itself is the common one).
TOKEN_PREFIXES: tuple[str, ...] = ("P1_", "E0_", "E1_")

#: Shorter than this and it cannot be a real pass token (the JWE is ~1-4 KB).
MIN_TOKEN_LEN = 100

#: Page-context script driven by HCaptchaBrowserSolver.solve_browser.
#:
#: Argument: {mintUrl, sitekey, expectedHost, rqdata, invisible, timeoutMs}.
#: Returns: {host, href, token, respKey, ua, error, diag} — never raises, so the
#: solver can classify "challenge shown" vs "widget never answered" vs "page left
#: the origin" from diag/error instead of a stack trace.
EXECUTE_JS = r"""
async ({mintUrl, sitekey, expectedHost, rqdata, invisible, timeoutMs}) => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const field = () => document.querySelector(
    'textarea[name="h-captcha-response"], input[name="h-captcha-response"], textarea[name="g-recaptcha-response"]');
  const tokenOf = (value) => (value && typeof value === 'object' && typeof value.response === 'string')
    ? value.response : value;
  const out = {
    host: location.hostname,
    href: location.href,
    token: null,
    respKey: null,
    ua: navigator.userAgent,
    error: null,
    diag: {api: false, injected: false, existing: false, challengeOpened: false, callback: false},
  };
  const read = () => {
    if (out.token) return out.token;
    const el = field();
    if (el && el.value) out.token = el.value;
    return out.token;
  };
  // Subdomain on either side is the same site: the profile keeps the apex, the
  // page may load on www./login./accounts.
  const sameSite = (actual, expected) => actual === expected
    || actual.endsWith('.' + expected) || expected.endsWith('.' + actual);
  if (expectedHost && !sameSite(location.hostname, expectedHost)) {
    out.error = 'wrong-origin';
    return out;
  }
  let widget = null;
  try {
    out.diag.api = typeof window.hcaptcha === 'object' && window.hcaptcha !== null;
    if (read()) {
      out.diag.existing = true;
      return out;
    }
    // Checkbox deployments: the page's own widget is the one the site trusts,
    // and hCaptcha exposes no "click the checkbox" API. Drive it programmatically
    // and give the page a moment to answer before minting an off-page widget.
    if (!invisible && out.diag.api && typeof window.hcaptcha.execute === 'function') {
      out.diag.existing = true;
      try { window.hcaptcha.execute(); } catch (e) { out.diag.existingError = String(e); }
      for (let i = 0; i < 40 && !read(); i++) await sleep(250);
      if (read()) return out;
    }
    // No usable widget on the page (or an invisible sitekey): load hCaptcha's own
    // bundle and render a hidden widget bound to the requested sitekey.
    if (!out.diag.api) {
      const src = await (await fetch(mintUrl, {credentials: 'omit'})).text();
      (0, eval)(src);
      out.diag.injected = true;
      out.diag.api = typeof window.hcaptcha === 'object' && window.hcaptcha !== null;
    }
    if (!out.diag.api) {
      out.error = 'hcaptcha api did not load';
      return out;
    }
    const box = document.createElement('div');
    box.id = 'dsh-hc-' + Date.now();
    box.style.display = 'none';
    document.body.appendChild(box);
    const options = {
      sitekey: sitekey,
      size: 'invisible',
      callback: (value) => { out.token = tokenOf(value); out.diag.callback = true; },
      'error-callback': (value) => { out.error = String(value); },
      'open-callback': () => { out.diag.challengeOpened = true; },
      'chalexpired-callback': () => { out.diag.challengeOpened = true; },
    };
    if (rqdata) options.rqdata = rqdata;
    widget = window.hcaptcha.render(box, options);
    try {
      const pending = window.hcaptcha.execute(widget, {async: true});
      if (pending && typeof pending.then === 'function') {
        pending.then((value) => {
          out.token = tokenOf(value) || out.token;
          out.diag.callback = true;
        }).catch(() => {});
      }
    } catch (e) {
      out.error = 'execute: ' + String(e);
      try { window.hcaptcha.execute(widget); } catch (e2) { out.error += ' | ' + String(e2); }
    }
    const deadline = Date.now() + Math.max(10000, Number(timeoutMs) || 90000);
    while (Date.now() < deadline && !read() && !out.error) await sleep(250);
    read();
    if (widget !== null && typeof window.hcaptcha.getRespKey === 'function') {
      out.respKey = window.hcaptcha.getRespKey(widget);
    }
    out.diag.challengeShown = !!document.querySelector(
      'iframe[src*="hcaptcha.com"][src*="challenge"], iframe[title*="hCaptcha challenge" i]');
  } catch (e) {
    out.error = out.error || String(e);
  }
  return out;
}
"""


# ---- token shape ------------------------------------------------------------


def normalize_token(value: Any) -> str:
    """Accept the callback shapes hCaptcha hands back: a string or {response}."""
    if isinstance(value, dict):
        value = value.get("response") or value.get("token") or value.get("value")
    if value is None:
        return ""
    return str(value).strip()


def token_problem(token: str | None, *, sitekey: str | None = None) -> str | None:
    """Why token cannot be a response token, or None when it can."""
    if not token:
        return "empty token"
    if sitekey and token == sitekey:
        return "hCaptcha echoed the sitekey instead of a token"
    if not token.startswith(TOKEN_PREFIXES):
        return f"unexpected token prefix {token[:4]!r}"
    if len(token) <= MIN_TOKEN_LEN:
        return f"token too short ({len(token)} chars)"
    return None


def validate_token(value: Any, *, sitekey: str | None = None) -> str:
    """Normalize and validate a response token, or raise UnsolvableError.

    Structural junk is terminal: a prefix change means hCaptcha changed the
    wire format, and retrying the same page will not fix it.
    """
    token = normalize_token(value)
    problem = token_problem(token, sitekey=sitekey)
    if problem:
        raise UnsolvableError(
            f"hCaptcha issued an unusable token: {problem}",
            context={"problem": problem, "token_prefix": token[:4], "length": len(token)},
        )
    return token


# ---- result capture ---------------------------------------------------------


def capture_user_agent(out: dict[str, Any], *, fallback: str | None = None) -> str | None:
    """The UA that actually minted the token — the client must submit with it."""
    for key in ("ua", "userAgent", "user_agent"):
        value = out.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def capture_resp_key(out: dict[str, Any]) -> str | None:
    """Enterprise respKey: 2captcha's hCaptcha solution carries it when present."""
    for key in ("respKey", "resp_key"):
        value = out.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def challenge_shown(out: dict[str, Any]) -> bool:
    """True when hCaptcha opened an interactive challenge instead of passing."""
    diag = out.get("diag")
    if isinstance(diag, dict) and (diag.get("challengeOpened") or diag.get("challengeShown")):
        return True
    error = str(out.get("error") or "").lower()
    return "challenge" in error


def truthy(value: Any) -> bool:
    """2captcha clients send booleans as "true"/"false" strings as often as bools."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def parse_cookies(raw: Any) -> list[dict[str, str]]:
    """Normalize a task's cookies field into cookie dicts for the provider.

    Accepts the string form ("a=b; c=d") and the JSON list form
    ([{name, value, domain?, path?}]). Domain/path stay unset unless the client
    sent them; the provider decides the jar.
    """
    if isinstance(raw, str):
        cookies: list[dict[str, str]] = []
        for part in raw.split(";"):
            name, sep, value = part.partition("=")
            if sep and name.strip():
                cookies.append({"name": name.strip(), "value": value.strip()})
        return cookies
    if isinstance(raw, list | tuple):
        cookies = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("name") or item.get("value") is None:
                continue
            cookie = {"name": str(item["name"]), "value": str(item["value"])}
            for key in ("domain", "path"):
                if item.get(key):
                    cookie[key] = str(item[key])
            cookies.append(cookie)
        return cookies
    return []


# ---- browser plumbing -------------------------------------------------------
#
# The mechanics of putting hCaptcha's widget onto a page: open one session,
# get its page, click the checkbox frame, run the script above. Kept here so
# the solver file stays policy + flow (repo rule: solver <= 200 lines) and so
# the click/script details live next to the script they drive.

_CHECKBOX_FRAME = "hcaptcha.com"
_CHECKBOX_SELECTOR = "#checkbox"


async def maybe_await(value: Any) -> Any:
    """playwright returns awaitables; duck-typed fakes may return plain values."""
    if hasattr(value, "__await__"):
        return await value
    return value


@asynccontextmanager
async def open_session(ctx: Any, spec: Any, *, solver: str) -> AsyncIterator[Any]:
    """Open one per-task session through the provider, never a shared context."""
    provider = getattr(ctx, "browser", None)
    if provider is None:
        raise ConfigError(
            "SolverContext.browser is None: the browser lane needs a provider",
            context={"solver": solver},
        )
    opener = getattr(provider, "session", None)
    if opener is not None:
        async with opener(spec) as session:
            yield session
        return
    # Compatibility with the pre-provider baseline pool. It has no per-task
    # spec, so proxy/UA/cookies from the task do not reach it — the contract
    # provider above is the path that carries them.
    acquire = getattr(provider, "acquire", None)
    if acquire is None:
        raise ConfigError(
            "browser provider exposes neither session() nor acquire()",
            context={"provider": type(provider).__name__},
        )
    async with acquire() as session:
        yield session


async def page_of(session: Any) -> Any:
    getter = getattr(session, "page", None) or getattr(session, "new_page", None)
    if getter is None:
        raise ConfigError(
            "browser session exposes no page()",
            context={"session": type(session).__name__},
        )
    return await maybe_await(getter())


async def click_checkbox(page: Any) -> bool:
    """Click the visible hCaptcha checkbox, when the page rendered one.

    The checkbox lives in a cross-origin hCaptcha frame: page JS cannot reach
    it, playwright can. Best-effort — a missing frame, an invisible widget, or a
    frame that is not ready all fall through to the scripted path.
    """
    frames = getattr(page, "frames", None)
    frames = frames() if callable(frames) else frames
    for frame in frames or ():
        try:
            if _CHECKBOX_FRAME not in str(getattr(frame, "url", "")):
                continue
            locator = frame.locator(_CHECKBOX_SELECTOR)
            if not await maybe_await(locator.count()):
                continue
            await maybe_await(locator.click(timeout=5_000))
            return True
        except CaptchaError:
            raise
        except Exception:
            continue
    return False


async def drive_widget(
    page: Any,
    *,
    sitekey: str,
    mint_url: str,
    enterprise: bool,
    rqdata: str | None,
    invisible: bool,
    expected_host: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Run EXECUTE_JS on the page and return its diagnostics."""
    evaluate = getattr(page, "evaluate", None)
    if evaluate is None:
        raise ConfigError(
            "browser page exposes no evaluate()",
            context={"page": type(page).__name__},
        )
    args = {
        "sitekey": sitekey,
        "mintUrl": mint_url,
        "enterprise": enterprise,
        "rqdata": rqdata,
        "invisible": invisible,
        "expectedHost": expected_host,
        "timeoutMs": timeout_ms,
    }
    out = await maybe_await(evaluate(EXECUTE_JS, args))
    if not isinstance(out, dict):
        raise CaptchaError(
            "hCaptcha page script returned no diagnostics",
            context={"result_type": type(out).__name__},
        )
    return out
