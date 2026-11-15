"""MTCaptcha — HTTP text challenge: OCR the image, let the verifier mint the token.

Wire (2026-10-23 widget build, see `wire.py`):

  `getchallenge.json` -> `getimage.json` -> OCR -> `solvechallenge.json`

The last call returns `verifyResult.verifiedToken.vt`, the `v1(h1,h2,sitekey,
hash)` string the site submits. The solver never composes that token: the two
chain hashes are the verifier's, and inventing them was the FILL this replaces.

`getchallenge`/`getimage` failures are a spent challenge (retry with a fresh
one); a rejected answer is the same class because the challenge is single-use.
Missing sitekey/URL is structural (`UnsolvableError`), a missing HTTP client is
misconfiguration (`ConfigError`). No live traffic is needed to exercise any of
this: the tests replay canned vendor replies and stub the OCR delegate.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, ConfigError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep
from . import pulse as pulse_mod
from . import wire

#: Used only when the task carries no UA: the pulse cookie hashes it, and the
#: HTTP transport sends its own UA anyway. `# FILL`: read the transport's UA
#: once curl_cffi exposes it instead of guessing here.
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"


@register_solver
class MTCaptchaSolver(InteractiveBase):
    info = SolverInfo(
        name="mtcaptcha",
        types=(CaptchaType.MTCAPTCHA,),
        http=True,
        experimental=True,
        version="0.2.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = task.payload or {}
        sitekey = _sitekey(task)
        site_url = _site_url(task)
        domain = wire.domain_of(site_url)
        language = _language(task)
        if ctx.http is None:
            raise ConfigError("mtcaptcha solver requires ctx.http", context={"type": task.type.value})
        started = time.perf_counter()

        session = wire.session_id()
        headers = wire.headers_for(sitekey, domain, language=language)
        challenge_reply = await ctx.http.get(
            ep.GET_CHALLENGE,
            params=wire.challenge_params(
                sitekey,
                domain,
                session=session,
                language=language,
                action=str(payload.get("action") or ""),
            ),
            headers=headers,
        )
        challenge = wire.parse_challenge(
            wire.json_object(challenge_reply, "getchallenge"),
            session=session,
            language=language,
            text_length=wire.TEXT_LENGTH,
        )

        pulse_value = pulse_mod.pulse(site_url, user_agent=_ua(task, payload), language=language)
        image_reply = await ctx.http.get(
            ep.GET_IMAGE,
            params=wire.image_params(sitekey, ct=challenge.ct, session=session, fold=challenge.fold),
            headers={**headers, "Cookie": wire.cookie_header(pulse_value)},
        )
        image = wire.parse_image(wire.json_object(image_reply, "getimage"))

        cv = await self._solve_cv(
            CaptchaType.IMAGE_TO_TEXT,
            task,
            ctx,
            {"image": image, "lang": language, "charset": payload.get("charset")},
        )
        answer = (cv.text or "").strip()
        if not answer:
            # The challenge is still fresh; retry with the next one rather than
            # billing a task we could not answer.
            raise CaptchaError("mtcaptcha: OCR returned no text", context={"sitekey": sitekey})

        solve_reply = await ctx.http.get(
            ep.SOLVE_CHALLENGE,
            params=wire.solve_params(
                sitekey,
                domain,
                ct=challenge.ct,
                session=session,
                answer=answer,
                fold=challenge.fold,
                language=language,
            ),
            headers=headers,
        )
        token = wire.parse_token(wire.json_object(solve_reply, "solvechallenge"))
        return SolverResult(
            token=token,
            extra={"userAgent": _ua(task, payload)} if task.user_agent else {},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


def _sitekey(task: Task) -> str:
    payload = task.payload or {}
    for value in (task.sitekey, payload.get("sitekey"), payload.get("websiteKey"), payload.get("key")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise UnsolvableError(
        "mtcaptcha payload missing sitekey/websiteKey",
        context={"type": task.type.value},
    )


def _site_url(task: Task) -> str:
    payload = task.payload or {}
    for value in (
        task.site_url,
        payload.get("site_url"),
        payload.get("websiteURL"),
        payload.get("websiteUrl"),
        payload.get("pageurl"),
        payload.get("url"),
    ):
        if isinstance(value, str) and value.strip():
            url = value.strip()
            if "://" in url:
                return url
            raise UnsolvableError(
                "mtcaptcha websiteURL must be an absolute URL",
                context={"url_len": len(url)},
            )
    raise UnsolvableError("mtcaptcha payload missing websiteURL", context={"type": task.type.value})


def _language(task: Task) -> str:
    payload = task.payload or {}
    value = payload.get("lang") or payload.get("language") or task.language or "en"
    return str(value).split("-", 1)[0].lower() or "en"


def _ua(task: Task, payload: dict[str, Any]) -> str:
    for value in (task.user_agent, payload.get("user_agent"), payload.get("userAgent")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_UA
