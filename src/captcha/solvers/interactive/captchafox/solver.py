"""CaptchaFox — HTTP bootstrap + CV when the widget proof is supplied, else browser.

The HTTP lane can go all the way (config -> challenge -> CV -> verify) *if* the
task carries the widget's `cs` proof (docs/protocol.md "payload mode": the client
collects fresh sensor data). Without it the verify call cannot succeed, so the
solver escalates with `NeedsBrowserError` instead of sending a body that will be
rejected — the Turnstile rule: a gated step is not a fake HTTP win.

The browser lane lives in `browser.py`; it renders the page's own widget and
waits for `cf-captcha-response` (the attest/one-click band).

Answer shapes come from the widget's slide/select scripts: `kind=position` for
slide, `kind=taps` for select. Bbox/coords are the CV delegate's.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ChallengeExpiredError, ConfigError, NeedsBrowserError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from .._browser import clean_url, website_url
from . import endpoints as ep
from . import wire
from .browser import solve_browser as browser_solve


@register_solver
class CaptchaFoxSolver(InteractiveBase):
    info = SolverInfo(
        name="captchafox",
        types=(CaptchaType.CAPTCHAFOX,),
        http=True,
        browser=True,
        experimental=True,
        version="0.2.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = task.payload or {}
        sitekey = _sitekey(task)
        site_url = clean_url(_site_url(task))
        proof = wire.proof_from_task(task)
        if ctx.http is None:
            raise ConfigError("captchafox solver requires ctx.http", context={"type": task.type.value})
        started = time.perf_counter()

        config_reply = await ctx.http.get(wire.config_url(sitekey, site_url), headers=wire.headers())
        config = wire.parse_config(wire.json_object(config_reply, "config"))

        if proof is None:
            raise NeedsBrowserError(
                "captchafox: the verify body needs the widget's __cf_wapi proof; the browser lane mints it",
                context={"sitekey": sitekey, "url": site_url, "config_keys": sorted(config.keys())[:20]},
            )

        body = wire.challenge_body(config, proof, host=wire.host_of(site_url), language=_language(task))
        challenge_reply = await ctx.http.post(
            wire.challenge_url(sitekey),
            data=wire.encode_body(body),
            headers={**wire.headers(), "Content-Type": "text/plain"},
        )
        challenge = wire.parse_challenge(wire.json_object(challenge_reply, "challenge"))
        if challenge.token:
            return _result(challenge.token, task=task, started=started)

        answer = await self._answer(task, ctx, challenge.kind, challenge.challenge)
        verify_body = wire.verify_body(
            sitekey,
            answer=answer,
            proof=proof,
            config=config,
            host=wire.host_of(site_url),
            kind=challenge.kind,
            trail=payload.get("trail") if isinstance(payload.get("trail"), list) else None,
            elapsed=time.perf_counter() - started,
        )
        verify_reply = await ctx.http.post(
            ep.VERIFY,
            data=wire.encode_body(verify_body),
            headers={**wire.headers(), "Content-Type": "text/plain"},
        )
        token = wire.parse_verified(wire.json_object(verify_reply, "verify"))
        return _result(token, task=task, started=started)

    async def _answer(
        self,
        task: Task,
        ctx: SolverContext,
        kind: str,
        challenge: dict[str, Any],
    ) -> dict[str, Any]:
        if kind == "attest":
            return {}
        if kind == "slide":
            cv = await self._solve_cv(
                CaptchaType.SLIDER,
                task,
                ctx,
                {
                    "bg": _b64(await _asset(ctx, challenge.get("bg"))),
                    "piece": _b64(await _asset(ctx, _player(challenge))),
                },
            )
        else:
            prompt = str(challenge.get("prompt") or (task.payload or {}).get("target") or "")
            cv = await self._solve_cv(
                CaptchaType.CLICK,
                task,
                ctx,
                {"image": _b64(await _asset(ctx, challenge.get("bg"))), "target": prompt},
            )
        return wire.answer_from_cv(kind, cv, challenge)

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        return await browser_solve(task, ctx, solver=self.info.name)


async def _asset(ctx: SolverContext, value: Any) -> bytes:
    inline = wire.image_bytes(value)
    if inline is not None:
        return inline
    url = wire.asset_url(value)
    if url is None or ctx.http is None:
        raise UnsolvableError("captchafox: challenge asset is neither base64 nor a URL", context={})
    reply = await ctx.http.get(url)
    body = getattr(reply, "body", b"") or b""
    if not body:
        raise ChallengeExpiredError("captchafox: empty challenge asset", context={"url": url})
    return body


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _player(challenge: dict[str, Any]) -> Any:
    player = challenge.get("player")
    if isinstance(player, dict):
        return player.get("url") or player.get("src")
    return player


def _result(token: str, *, task: Task, started: float) -> SolverResult:
    extra: dict[str, Any] = {"userAgent": task.user_agent} if task.user_agent else {}
    return SolverResult(
        token=token,
        extra=extra,
        solve_mode=SolveMode.HTTP,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


def _sitekey(task: Task) -> str:
    payload = task.payload or {}
    for value in (
        task.sitekey,
        payload.get("sitekey"),
        payload.get("websiteKey"),
        payload.get("key"),
        payload.get("siteKey"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise UnsolvableError("captchafox payload missing sitekey/websiteKey", context={"type": task.type.value})


def _site_url(task: Task) -> str:
    try:
        return website_url(task)
    except UnsolvableError as exc:
        raise UnsolvableError("captchafox payload missing websiteURL", context={"type": task.type.value}) from exc


def _language(task: Task) -> str:
    payload = task.payload or {}
    value = payload.get("lang") or payload.get("language") or task.language or "en"
    return str(value).split("-", 1)[0].lower() or "en"
