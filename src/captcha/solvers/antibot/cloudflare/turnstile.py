"""Cloudflare Turnstile solver: visible, invisible, enterprise payloads.

The flow is split because the wire is split:

  * HTTP/JS half (`turnstile_wire`): api.js -> versioned bundle -> the
    challenge-platform variant -> the widget document. That is everything the
    public reverse-engineering documents without a page (docs/reverse-2026.md §3).
  * The token-mint POST body is gated, so the HTTP lane cannot return a token.
    It escalates with `NeedsBrowserError` (contract §2), carrying what it learned.
  * Browser half: render the widget on the client's own page through
    `ctx.browser`, wait for `cf-turnstile-response`, and return the token plus the
    UA that minted it.

TURNSTILE and TURNSTILE_INVISIBLE share the widget; the invisible one renders
with `appearance: execute` and is executed at once. Enterprise is a payload flag
(`action` + `cData`), not a separate captcha type.

Failure mapping (contract §5): no token -> `ChallengeExpiredError` (retry/DLQ);
wrong origin, echoed sitekey, malformed token, or a terminal widget error code ->
`UnsolvableError`; missing provider -> `ConfigError`. No path fabricates a token.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ChallengeExpiredError, NeedsBrowserError, UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase

from ._common import host_matches, host_of, navigate, nudge_keys, open_session, page_of, spec_for, timeout_s
from ._cookies import capture_ua
from .turnstile_page import TERMINAL_WIDGET_CODES, click_checkbox, drive_widget, error_code, validate_token
from .turnstile_wire import TurnstileParams, bootstrap, params_from_task

#: First pass: how long the widget gets before the interaction attempts.
GRACE_S = 12.0


class TurnstileSolver(AntibotBase):
    """Cloudflare Turnstile sitekey widget, HTTP bootstrap + browser mint."""

    engine = AntibotEngine.CLOUDFLARE

    info: ClassVar[SolverInfo] = SolverInfo(
        name="turnstile",
        types=(CaptchaType.TURNSTILE, CaptchaType.TURNSTILE_INVISIBLE),
        http=True,
        browser=True,
        needs_js_runtime=False,
        experimental=True,
        version="0.1.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        params = params_from_task(task)
        if getattr(ctx, "browser", None) is not None:
            # A worker that owns both lanes can finish the task in-process; the
            # HTTP-lane worker has no provider and takes the escalation below.
            return await self.solve_browser(task, ctx)
        self.require_ctx(ctx, "http")
        boot = await bootstrap(ctx.http, params)
        raise NeedsBrowserError(
            "turnstile: the token-mint POST has no published schema; the widget needs a real page",
            context={
                "sitekey": params.sitekey,
                "url": params.site_url,
                "enterprise": params.enterprise,
                **boot.summary(),
            },
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        params = params_from_task(task)
        budget = timeout_s(task, ctx)
        expected_host = host_of(params.site_url)
        label = f"cloudflare:{task.type.value}:{task.id}"
        async with open_session(ctx, spec_for(task, ctx, label=label), solver=self.info.name) as session:
            page = await page_of(session)
            await navigate(session, page, params.site_url, int(budget * 1000))
            out = await drive_widget(
                page,
                params=params,
                timeout_ms=int(min(budget, GRACE_S) * 1000),
                expected_host=expected_host,
            )
            if _retryable(out):
                if not params.invisible:
                    await click_checkbox(page)
                await nudge_keys(page)
                remaining_ms = int(max(budget - GRACE_S, 0.0) * 1000)
                if remaining_ms >= 500:
                    out = await drive_widget(page, params=params, timeout_ms=remaining_ms, expected_host=expected_host)
        return _result(out, params=params, task=task, expected_host=expected_host, started=started)


def _retryable(out: dict[str, Any]) -> bool:
    """True when a click/keyboard nudge and a second look can still help."""
    if out.get("token"):
        return False
    if out.get("error") == "wrong-origin":
        return False
    return error_code(out) not in TERMINAL_WIDGET_CODES


def _result(
    out: dict[str, Any],
    *,
    params: TurnstileParams,
    task: Task,
    expected_host: str,
    started: float,
) -> SolverResult:
    host = str(out.get("host") or "")
    if out.get("error") == "wrong-origin" or (host and not host_matches(host, expected_host)):
        raise UnsolvableError(
            "turnstile: the page left the expected origin",
            context={"expected_host": expected_host, "actual_host": host, "url": out.get("href")},
        )

    token = out.get("token")
    code = error_code(out)
    if not token:
        if code in TERMINAL_WIDGET_CODES:
            raise UnsolvableError(
                f"turnstile: the widget rejected the task (error {code})",
                context={"sitekey": params.sitekey, "host": host, "error": code},
            )
        reason = str(out.get("error") or "no_token")
        raise ChallengeExpiredError(
            f"turnstile issued no token ({reason})",
            context={"sitekey": params.sitekey, "host": host, "reason": reason, "error": code},
        )

    ua = capture_ua(out, task)
    return SolverResult(
        token=validate_token(token, sitekey=params.sitekey),
        extra={"userAgent": ua} if ua else {},
        solve_mode=SolveMode.BROWSER,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )
