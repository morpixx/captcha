"""Capy (capy.me) puzzle — drag the slice into the gap, return the answer triple.

Flow:
  1. GET  <origin>/puzzle/get/?k=<public key>          -> puzzle JSON
     Carries the puzzle ids (`captchakey`, `challengekey`) and the image URLs.
  2. GET  <bg> + <piece>                               -> scene + draggable slice
  3. `_solve_cv(SLIDER, ...)`                          -> horizontal offset
  4. POST <origin>/puzzle/answer/                      -> signed `answer` string
  5. token = json({captchakey, challengekey, answer})  -> the `capy_*` form fields
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep

_B32 = "0123456789abcdefghijklmnopqrstuv"
_DRAG_POINTS = 24


@register_solver
class CapySolver(InteractiveBase):
    """Capy puzzle captcha. `sitekey` is the public `PUZZLE_...` widget key."""

    info = SolverInfo(
        name="capy",
        types=(CaptchaType.CAPY,),
        http=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "sitekey", "pageurl")
        if ctx.http is None:
            raise UnsolvableError("capy solver requires ctx.http", context={})

        sitekey = str(task.payload["sitekey"])
        pageurl = _pageurl(task)
        lang = str(task.payload.get("lang") or task.language or "en")
        headers = _headers(task, pageurl)
        start = time.monotonic()

        # FILL: `get` query. Network -> `puzzle/get/` -> Request URL; confirm the
        # key parameter name (`k`) and whether the embedding host travels along as
        # `domain`. The widget bootstrap itself is `puzzle/get_js/?k=<key>`.
        params = {"k": sitekey, "lang": lang, "domain": _hostname(pageurl)}
        resp = await ctx.http.get(ep.GET_CHALLENGE, params=params, headers=headers)
        data = _json_object(resp, "capy get")

        # FILL: `get` reply keys. Network -> `puzzle/get/` -> Response; copy the
        # JSON. The puzzle ids are `captchakey` + `challengekey`, the images `bg`
        # + `piece`. Some builds nest them under `puzzle`/`image`, hence the unwrap.
        root = data.get("puzzle") or data.get("data") or data
        captchakey = _first(root, "captchakey", "captcha_key", "captchaKey")
        challengekey = _first(root, "challengekey", "challenge_key", "challengeKey")
        bg_url = _first(root, "bg", "background", "bg_url", "background_image")
        piece_url = _first(root, "piece", "puzzle", "piece_url", "piece_image")
        if not (captchakey and challengekey and bg_url and piece_url):
            raise CaptchaError(
                "capy get missing puzzle fields — see runbooks/capy.md",
                context={"keys": sorted(root.keys())},
            )

        bg = await ctx.http.get(str(bg_url), headers=headers)
        piece = await ctx.http.get(str(piece_url), headers=headers)
        cv = await self._solve_cv(
            CaptchaType.SLIDER,
            task,
            ctx,
            {"bg": bg.body, "piece": piece.body},
        )

        # FILL: `answer` request body. Network -> `puzzle/answer/` -> Form Data;
        # confirm the two id field names, the drag field (`answer`) and its units
        # (image pixels vs 0..1 of the track width).
        answer_body = {
            "captchakey": str(captchakey),
            "challengekey": str(challengekey),
            "answer": encode_drag(cv.offset or 0.0, float(task.payload.get("piece_y") or 0.0)),
            "lang": lang,
        }
        aresp = await ctx.http.post(ep.POST_ANSWER, data=answer_body, headers=headers)
        adata = _json_object(aresp, "capy answer")
        answer = _first(adata, "answer", "token")
        if not answer:
            raise CaptchaError(
                "capy answer missing token — see runbooks/capy.md",
                context={"keys": sorted(adata.keys())},
            )

        token = _assemble_token(str(captchakey), str(challengekey), str(answer))
        return SolverResult(
            token=token,
            extra={"captchakey": str(captchakey), "challengekey": str(challengekey), "answer": str(answer)},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )


def encode_drag(offset: float, y: float = 0.0, points: int = _DRAG_POINTS) -> str:
    """Encode the drag as pointer samples, one `<x>x<y>x` token per sample.

    FILL: capy's answer track. A published capy answer looks like
    `0xaxakx0xaxaax0xkxx3ox...gAAAAABk8bgz...` — base-32 samples split by `x`
    plus a base64 AES-GCM tail. Solve one puzzle by hand, then read
    `puzzle/answer/` -> Form Data in devtools: confirm the separator, the sample
    count, and whether the `gAAAA...` tail is appended client-side. If the field
    is a plain integer offset, drop this encoder and post the offset.
    """
    x_end, y_end = round(offset), round(y)
    samples = [(round(x_end * step / points), round(y_end * step / points)) for step in range(1, points + 1)]
    return "".join(f"{_b32(x)}x{_b32(yy)}x" for x, yy in samples)


def _assemble_token(captchakey: str, challengekey: str, answer: str) -> str:
    """Build what the page puts into `capy_captchakey`/`capy_challengekey`/`capy_answer`.

    FILL: 2captcha reports capy as `answer` + `challenge_key` only, while the
    vendor's own docs name the form fields `capy_captchakey`, `capy_challengekey`,
    `capy_answer`. Capture the three hidden inputs the widget fills after a
    manual solve and trim this JSON to the keys the client actually reads.
    """
    return json.dumps(
        {"captchakey": captchakey, "challengekey": challengekey, "answer": answer},
        separators=(",", ":"),
    )


def _b32(value: int) -> str:
    """Base-32 the way `Number.prototype.toString(32)` renders it (digits 0-9, a-v)."""
    if value <= 0:
        return "0"
    out = ""
    while value:
        value, digit = divmod(value, 32)
        out = _B32[digit] + out
    return out


def _pageurl(task: Task) -> str:
    return str(task.payload.get("pageurl") or task.payload.get("site_url") or task.site_url or "")


def _hostname(pageurl: str) -> str:
    return urlparse(pageurl if "//" in pageurl else "https://" + pageurl).hostname or ""


def _origin(pageurl: str) -> str:
    parsed = urlparse(pageurl if "//" in pageurl else "https://" + pageurl)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _headers(task: Task, pageurl: str) -> dict[str, str]:
    """UA and language from the task; the proxy rides on `ctx.http`, not here."""
    headers = {"Accept": "application/json, text/plain, */*"}
    ua = str(task.payload.get("user_agent") or task.user_agent or "")
    if ua:
        headers["User-Agent"] = ua
    lang = str(task.payload.get("lang") or task.language or "")
    if lang:
        headers["Accept-Language"] = lang
    if pageurl:
        headers["Referer"] = pageurl
        origin = _origin(pageurl)
        if origin:
            headers["Origin"] = origin
    return headers


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _json_object(resp: Any, what: str) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception as exc:
        raise CaptchaError(f"{what} is not JSON", context={"status": getattr(resp, "status", None)}) from exc
    if not isinstance(data, dict):
        raise CaptchaError(f"{what} is not a JSON object", context={"type": type(data).__name__})
    return data
