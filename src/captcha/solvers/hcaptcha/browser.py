"""hCaptcha browser-lane solver: drive the real widget in a real page.

Why a browser: Riot's sitekeys refuse the pure-HTTP path — getcaptcha answers
with a fresh "hsw" challenge instead of a P1_ pass token, and the "n" token it
wants is a serialized fingerprint, not a ciphertext. A real page executes
hCaptcha's own bundle, so the widget builds a legitimate token. Steam and Epic
accept the same flow; their HTTP agent is separate work, so they stay on auto.

Contract: one fresh session per task via ctx.browser.session(spec); the solver
never creates a browser, page, or HTTP client itself; returns
SolverResult(token, extra={userAgent, respKey?}, solve_mode=BROWSER). No token
is retryable (ChallengeExpiredError carries the reason); a changed token shape
or a page that left the expected origin is terminal (UnsolvableError).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, ConfigError, UnsolvableError

try:  # browser-core's port; the W0 baseline tree predates it
    from captcha.domain.ports import BrowserSpec
except ImportError:  # pragma: no cover - gone once this branch sits on browser-core
    BrowserSpec = SimpleNamespace  # type: ignore[assignment,misc]

from captcha.solvers import Solver, SolverContext, SolverInfo

from .common import (
    capture_resp_key,
    capture_user_agent,
    challenge_shown,
    click_checkbox,
    drive_widget,
    maybe_await,
    open_session,
    page_of,
    parse_cookies,
    validate_token,
)
from .sites import Site, host_matches, host_of, resolve_site

#: Whole-task budget for the widget to answer; the page script polls for this.
DEFAULT_TIMEOUT_S = 90.0
_MIN_TIMEOUT_S = 10.0
_MAX_URL_LEN = 2048


class HCaptchaBrowserSolver(Solver):
    """Drive the live hCaptcha widget in the browser lane."""

    info = SolverInfo(
        name="hcaptcha_browser",
        types=(CaptchaType.HCAPTCHA, CaptchaType.HCAPTCHA_ENTERPRISE),
        http=False,
        browser=True,
        # Protocol written against the live widget; success rate not measured
        # yet. Flip to False only after the L3 canary (production-contract §10).
        experimental=True,
        version="0.1.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        raise ConfigError(
            "hcaptcha_browser is browser-only; HTTP tasks belong to the HTTP/grid solver",
            context={"solver": self.info.name},
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        url = _clean_url(_website_url(task))
        site = resolve_site(site_url=url, sitekey=task.sitekey, payload=task.payload, captcha_type=task.type)
        if not site.allows_browser:
            raise ConfigError(
                "site profile forbids the browser lane",
                context={"site": site.name, "mode": str(site.mode)},
            )
        if not site.sitekey:
            raise UnsolvableError(
                "hCaptcha sitekey unknown: pass websiteKey or add a site profile",
                context={"site": site.name, "host": site.host, "url": url},
            )

        timeout_s = _timeout_s(task)
        timeout_ms = int(max(timeout_s, _MIN_TIMEOUT_S) * 1000)
        async with open_session(ctx, _spec(task, site, ctx), solver=self.info.name) as session:
            page = await page_of(session)
            await _navigate(session, page, url, timeout_s)
            # A real click on the checkbox is the gesture hCaptcha expects from
            # a visible widget; invisible widgets ignore it and answer in JS.
            await click_checkbox(page)
            out = await drive_widget(
                page,
                sitekey=site.sitekey,
                mint_url=site.mint_url,
                enterprise=site.enterprise,
                rqdata=site.rqdata,
                invisible=site.invisible,
                expected_host=site.host,
                timeout_ms=timeout_ms,
            )
        return _result(out, site=site, url=url, started=started)


def _spec(task: Task, site: Site, ctx: SolverContext) -> BrowserSpec:
    """Per-task spec; UA/locale/timezone stay with the provider on purpose.

    Camoufox derives them from the proxy exit IP, and forcing a client-supplied
    UA produces a fingerprint that disagrees with itself.
    """
    config = getattr(ctx, "config", None)
    return BrowserSpec(
        proxy=task.proxy,
        cookies=parse_cookies((task.payload or {}).get("cookies")),
        headless=bool(getattr(config, "worker_browser_headless", True)),
        label=f"hcaptcha:{site.name}:{task.id}",
    )


async def _navigate(session: Any, page: Any, url: str, timeout_s: float) -> None:
    """Use the provider's goto when it has one; it maps failures to CaptchaError."""
    goto = getattr(session, "goto", None)
    timeout_ms = int(timeout_s * 1000)
    if goto is not None:
        await maybe_await(goto(url, wait_until="domcontentloaded", timeout_ms=timeout_ms))
        return
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except CaptchaError:
        raise
    except Exception as exc:
        raise CaptchaError("page navigation failed", context={"url": url, "error": str(exc)[:300]}) from exc


def _result(out: dict[str, Any], *, site: Site, url: str, started: float) -> SolverResult:
    actual = str(out.get("host") or "")
    expected = site.host or host_of(url)
    if out.get("error") == "wrong-origin" or (actual and not host_matches(actual, expected)):
        raise UnsolvableError(
            "hCaptcha page left the expected origin",
            context={"expected_host": expected, "actual_host": actual, "url": url},
        )

    token = out.get("token")
    if not token:
        reason = "challenge_shown" if challenge_shown(out) else str(out.get("error") or "no_token")
        raise ChallengeExpiredError(
            f"hCaptcha issued no token ({reason})",
            context={"reason": reason, "host": actual, "sitekey": site.sitekey},
        )

    # The UA that actually minted the token: the client must submit with it.
    extra: dict[str, Any] = {"userAgent": capture_user_agent(out)}
    resp_key = capture_resp_key(out)
    if resp_key:
        extra["respKey"] = resp_key

    return SolverResult(
        token=validate_token(token, sitekey=site.sitekey),
        extra=extra,
        solve_mode=SolveMode.BROWSER,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


def _website_url(task: Task) -> str:
    payload = task.payload or {}
    url = task.site_url or payload.get("websiteURL") or payload.get("websiteUrl") or payload.get("pageurl")
    if not isinstance(url, str) or not url.strip():
        raise UnsolvableError(
            "payload missing websiteURL: the browser lane needs a page to render the widget",
            context={"type": task.type.value},
        )
    return url.strip()


def _clean_url(url: str) -> str:
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if len(url) > _MAX_URL_LEN or scheme not in ("http", "https"):
        raise UnsolvableError(
            "websiteURL must be a short http(s) URL",
            context={"scheme": scheme, "length": len(url)},
        )
    return url


def _timeout_s(task: Task) -> float:
    timeout = DEFAULT_TIMEOUT_S
    if task.deadline_ts:
        remaining = task.deadline_ts - time.time()
        if 0 < remaining < timeout:
            timeout = remaining
    return max(timeout, _MIN_TIMEOUT_S)
