"""VK — legacy image OCR over HTTP, VK ID token flow in the browser lane.

Two different protocols share the type:

  * `sid` (API error 14, old integrations): `captcha.php?sid=...` returns an
    image; OCR it and return the `captcha_key` text. HTTP+CV, no browser.
  * `redirectUri` (`id.vk.com/not_robot_captcha`): the 2026 widget. The page
    runs a PoW + FingerprintJS, binds `success_token` to the session/IP, and
    keeps `remixstlid` in its own cookies. Nothing in that token is derivable
    from HTTP inputs (docs/reverse-2026.md has the same verdict for the family),
    so `solve_http` escalates and `solve_browser` drives the page.
  * the `image+steps` variant from `captchaNotRobot.getContent` is a
    kaleidoscope path, not an OCR image; it is rejected explicitly.

The token hook is installed before navigation and only stores token-shaped
JSON; success is never assumed from a 200 alone.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import (
    CaptchaError,
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    UnsolvableError,
)
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from .._browser import (
    clean_url,
    click_first,
    host_of,
    install_init_script,
    navigate,
    open_session,
    page_of,
    read_ua,
    spec_for,
    timeout_s,
    wait_js,
)
from . import endpoints as ep
from . import wire
from .payload import image_url_of, mode_of, sid_of, site_url


@register_solver
class VKSolver(InteractiveBase):
    info = SolverInfo(
        name="vk",
        types=(CaptchaType.VK,),
        http=True,
        browser=True,
        experimental=True,
        version="0.2.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        mode = mode_of(task)
        if mode == "token":
            url = clean_url(site_url(task))
            if getattr(ctx, "browser", None) is not None:
                return await self.solve_browser(task, ctx)
            raise NeedsBrowserError(
                "vk: success_token is minted by the VK ID widget and bound to the browser session",
                context={"url": url, "mode": mode},
            )
        if mode == "steps":
            raise UnsolvableError(
                "vk: captchaNotRobot image+steps is a kaleidoscope path, not an OCR image",
                context={"type": task.type.value},
            )
        if ctx.http is None:
            raise ConfigError("vk image solver requires ctx.http", context={"type": task.type.value})
        sid = sid_of(task)
        started = time.perf_counter()
        image_url = image_url_of(task)
        if image_url is not None:
            reply = await ctx.http.get(image_url)
        else:
            reply = await ctx.http.get(ep.CAPTCHA_IMAGE, params={"sid": sid})
        image = wire.parse_image(reply)
        cv = await self._solve_cv(
            CaptchaType.IMAGE_TO_TEXT,
            task,
            ctx,
            {"image": image, "lang": "en", "charset": "latin"},
        )
        text = (cv.text or "").strip()
        if not text:
            raise CaptchaError("vk: OCR returned no text", context={"captcha_sid": sid})
        extra: dict[str, Any] = {"userAgent": task.user_agent} if task.user_agent else {}
        return SolverResult(
            token=text,
            text=text,
            extra=extra,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        url = clean_url(site_url(task))
        expected_host = host_of(url)
        budget_ms = int(timeout_s(task, ctx) * 1000)
        label = f"vk:{task.type.value}:{task.id}"
        async with open_session(ctx, spec_for(task, ctx, label=label), solver=self.info.name) as session:
            page = await page_of(session)
            await install_init_script(session, wire.TOKEN_HOOK_JS)
            await navigate(session, page, url, budget_ms)
            token = await wait_js(page, wire.TOKEN_JS, timeout_ms=max(budget_ms // 2, 1))
            if not token:
                await click_first(
                    page,
                    [
                        "#not-robot-captcha-checkbox",
                        '[data-testid="not-robot-captcha-checkbox"]',
                        ".vk-captcha__checkbox",
                        "[role=checkbox]",
                    ],
                )
                token = await wait_js(page, wire.TOKEN_JS, timeout_ms=max(budget_ms // 2 - 500, 1))
            ua = await read_ua(page)
            host = await _host_of_page(page)
        if host and expected_host and not _same_vk_family(host, expected_host):
            raise UnsolvableError(
                "vk: the page left the redirectUri host",
                context={"expected_host": expected_host, "actual_host": host, "url": url},
            )
        if not token:
            raise ChallengeExpiredError(
                "vk issued no success_token",
                context={"url": url, "host": host or expected_host},
            )
        return SolverResult(
            token=str(token),
            extra={"userAgent": ua} if ua else {},
            solve_mode=SolveMode.BROWSER,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


def _same_vk_family(actual: str, expected: str) -> bool:
    """VK may land on id.vk.com or id.vk.ru; anything else is a wrong origin."""
    actual, expected = actual.lower().strip("."), expected.lower().strip(".")
    if actual == expected:
        return True
    return any(actual.endswith("." + family) and expected.endswith("." + family) for family in ("vk.com", "vk.ru"))


async def _host_of_page(page: Any) -> str:
    try:
        value = await page.evaluate("() => location.hostname || ''")
    except Exception:
        return ""
    return str(value).strip().lower() if isinstance(value, str) else ""
