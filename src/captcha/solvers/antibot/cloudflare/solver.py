"""Cloudflare managed challenge (cf_clearance), browser lane.

`cf_clearance` is server-issued and bound to UA + IP: there is nothing to port
into an HTTP client (docs/reverse-2026.md §4), so `solve_http` is the escalation
point and the browser lane does the work -- load the page, let Cloudflare's own
JS run, wait for the final redirect *and* the clearance cookie, then return both
plus the UA.

A clearance that appears while the interstitial is still up is not a solution:
Cloudflare rejects it until the challenge page has gone (2026-10 capture), so the
wait loop requires the challenge markers and the `/cdn-cgi/challenge-platform/`
path to be gone. Timeout without all three -> `ChallengeExpiredError` (retry/DLQ);
a page that answered with something else entirely -> the same, never a fake win.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ChallengeExpiredError, ConfigError, NeedsBrowserError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase

from ._common import (
    clean_url,
    host_of,
    maybe_await,
    navigate,
    nudge_keys,
    open_session,
    page_of,
    spec_for,
    timeout_s,
    website_url,
)
from ._cookies import CLEARANCE_COOKIE, cookie_jar, page_ua

#: Wait this long for Cloudflare's own script before trying Tab+Space.
NUDGE_AFTER_S = 8.0
#: Cookie/redirect poll interval; never longer than a sixth of the budget.
POLL_S = 0.5

STATE_JS = r"""
() => {
  const body = document.body ? document.body.innerHTML : '';
  const title = document.title || '';
  const marker = !!document.querySelector(
    '#challenge-stage, #challenge-running, #challenge-form, #cf-please-wait, #cf-challenge-running');
  const probe = title + ' ' + body.slice(0, 6000);
  return {
    url: location.href,
    host: location.hostname,
    ua: navigator.userAgent || '',
    title,
    challenge: marker || /Just a moment|Checking your browser|cf-please-wait|cf-challenge-running/i.test(probe),
  };
}
"""


class CloudflareChallengeSolver(AntibotBase):
    """Cloudflare managed / IUAM challenge, solved on a real page."""

    engine = AntibotEngine.CLOUDFLARE

    info: ClassVar[SolverInfo] = SolverInfo(
        name="cloudflare_challenge",
        types=(CaptchaType.CF_CHALLENGE,),
        http=True,
        browser=True,
        needs_js_runtime=False,
        experimental=True,
        version="0.1.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        url = clean_url(website_url(task))
        if getattr(ctx, "browser", None) is not None:
            return await self.solve_browser(task, ctx)
        raise NeedsBrowserError(
            "cf_clearance is server-issued; the managed challenge needs the browser lane",
            context={"url": url, "engine": self.engine.value},
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        url = clean_url(website_url(task))
        host = host_of(url)
        budget = timeout_s(task, ctx)
        label = f"cloudflare:cf_challenge:{task.id}"
        async with open_session(ctx, spec_for(task, ctx, label=label), solver=self.info.name) as session:
            page = await page_of(session)
            await navigate(session, page, url, int(budget * 1000))
            got = await _wait_clearance(page, session, host=host, budget=budget)
        return _result(got, task=task, started=started)


async def _wait_clearance(page: Any, session: Any, *, host: str, budget: float) -> dict[str, Any]:
    """Poll until the clearance is present, the challenge UI is gone, and the
    browser is off the challenge path; then return the jar and the page state."""
    deadline = time.monotonic() + budget
    started = time.monotonic()
    nudge_after = min(NUDGE_AFTER_S, max(budget / 3.0, 0.01))
    poll = min(POLL_S, max(budget / 6.0, 0.01))
    nudged = False
    state: dict[str, Any] = {}
    cookies: dict[str, str] = {}
    while True:
        state = await _state(page)
        cookies = await cookie_jar(session, host=host)
        clearance = cookies.get(CLEARANCE_COOKIE)
        if clearance and not state.get("challenge") and not _on_challenge_url(str(state.get("url") or "")):
            return {"cookies": cookies, "state": state}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            reason = "clearance_before_redirect" if clearance else "no_clearance"
            raise ChallengeExpiredError(
                f"cloudflare: no usable cf_clearance ({reason})",
                context={
                    "url": state.get("url"),
                    "reason": reason,
                    "challenge": bool(state.get("challenge")),
                    # Names/count only: cookie values never leave the worker
                    # (contract §5, redaction).
                    "cookie_count": len(cookies),
                },
            )
        if not nudged and time.monotonic() - started >= nudge_after:
            nudged = True
            await nudge_keys(page)
        await asyncio.sleep(min(poll, remaining))


async def _state(page: Any) -> dict[str, Any]:
    out = await maybe_await(page.evaluate(STATE_JS))
    if not isinstance(out, dict):
        raise ChallengeExpiredError(
            "cloudflare: page script returned no state",
            context={"type": type(out).__name__},
        )
    return out


def _on_challenge_url(url: str) -> bool:
    return "/cdn-cgi/challenge-platform/" in url or "/cdn-cgi/l/chk_" in url


def _result(got: dict[str, Any], *, task: Task, started: float) -> SolverResult:
    cookies = got.get("cookies") or {}
    state = got.get("state") or {}
    clearance = cookies.get(CLEARANCE_COOKIE)
    if not clearance:
        # The wait loop only returns a jar with the cookie; keep the guard so a
        # refactor cannot turn an empty jar into a paid success.
        raise ChallengeExpiredError("cloudflare: clearance cookie disappeared", context={})
    ua = page_ua(state)
    if not ua:
        # A client-supplied UA is not a substitute: the browser that holds the
        # cookie has its own, and Cloudflare checks the pair.
        raise ConfigError(
            "cloudflare: the page reported no user-agent; cf_clearance is UA-bound and unusable without it",
            context={"url": state.get("url")},
        )
    return SolverResult(
        token=clearance.strip(),
        extra={"cookies": dict(cookies), "userAgent": ua},
        solve_mode=SolveMode.BROWSER,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )
