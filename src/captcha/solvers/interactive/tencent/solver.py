"""Tencent TCaptcha / 防水墙 — slider or 3-point click.

Flow:
  1. GET  /cap_union_prehandle?aid=&accver=1&...                -> sess + echo
  2. GET  /cap_union_new_show?aid=&sess=&...                    -> images, PoW params
     (skipped when prehandle already carried cdnPic1/cdnPic2)
  3. GET  <cdnPic1> + <cdnPic2>                                 -> bg + piece
  4. CV slider / click in-process, plain-Python PoW from the show reply
  5. POST /cap_union_new_verify {sess, ans, collect, pow_answer} -> ticket + randstr

Step 5 needs `collect`, the TDC device-context blob. It is a browser
fingerprint (`window.TDC.getData(true)`), not derivable from the challenge, so
the solver takes a producer from `ctx.extra["tencent_tdc"]` and escalates with
NeedsBrowserError when the worker has none. Sending an empty `collect` would
get the answer rejected as if it were wrong — the failure mode this solver
exists to avoid. The browser lane is `tencent/browser.py`.
"""

from __future__ import annotations

import inspect
import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import (
    ConfigError,
    NeedsBrowserError,
    SensorError,
    UnsolvableError,
)
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import browser, wire
from . import endpoints as ep


@register_solver
class TencentSolver(InteractiveBase):
    info = SolverInfo(
        name="tencent",
        types=(CaptchaType.TENCENT,),
        http=True,
        browser=True,
        needs_js_runtime=True,  # TDC collect() is obfuscated JS
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "aid")
        if ctx.http is None:
            raise ConfigError(
                "tencent solver requires ctx.http",
                context={"type": task.type.value, "lane": "cv"},
            )

        aid = str(task.payload["aid"])
        lang = str(task.payload.get("lang") or task.language or "en")
        site_url = str(task.payload.get("site_url") or task.site_url or "")
        showtype = str(task.payload.get("showtype") or "popup")
        started = time.monotonic()

        prehandle = wire.parse_reply(
            (await ctx.http.get(ep.PREHANDLE, params=self._prehandle_params(task, aid, lang, site_url))).text
        )
        show: dict[str, Any] | None = None
        if not (prehandle.get("cdnPic1") or prehandle.get("bg")):
            show = wire.parse_reply(
                (await ctx.http.get(ep.SHOW_NEW, params=self._show_params(aid, prehandle, showtype, lang))).text
            )
        round_ = wire.parse_round(prehandle, show)
        if not round_.pic1:
            raise UnsolvableError(
                "tencent show returned no challenge image",
                context={"keys": sorted({**prehandle, **(show or {})}), "sess": bool(round_.sess)},
            )

        bg = await ctx.http.get(round_.pic1)
        piece = await ctx.http.get(round_.pic2) if round_.pic2 else None
        kind = CaptchaType.SLIDER if piece is not None else CaptchaType.CLICK
        cv_payload: dict[str, Any] = {"bg": bg.body}
        if piece is not None:
            cv_payload["piece"] = piece.body
        cv = await self._solve_cv(kind, task, ctx, cv_payload)

        collect = await _collect(ctx, round_, aid)
        ans = wire.format_ans(cv, kind, y=int(round_.echo.get("dragY") or task.payload.get("drag_y") or 0))
        body = wire.verify_body(round_, ans, collect, aid=aid, showtype=showtype)
        reply = wire.parse_reply((await ctx.http.post(ep.VERIFY_NEW, data=body)).text)
        ticket, randstr = wire.parse_verify(reply)

        return SolverResult(
            token=ticket,
            text=randstr or None,
            extra={"randstr": randstr, "ticket": ticket, "cv_kind": kind.value},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        return await browser.solve(task, ctx, solver=self)

    # -- request shaping --------------------------------------------------

    def _prehandle_params(self, task: Task, aid: str, lang: str, site_url: str) -> dict[str, Any]:
        """Query the SDK sends before it knows anything about the round."""
        return {
            "aid": aid,
            "accver": 1,
            "showtype": str(task.payload.get("showtype") or "popup"),
            "ua": task.payload.get("ua_b64") or "",
            "noheader": 1,
            "fb": 1,
            "aged": 0,
            "enableAged": 0,
            "enableDarkMode": 0,
            "grayscale": 1,
            "clientype": 2,
            "cap_cd": "",
            "uid": str(task.payload.get("uid") or ""),
            "lang": lang,
            "entry_url": site_url,
            "sess": "",
        }

    def _show_params(self, aid: str, prehandle: dict[str, Any], showtype: str, lang: str) -> dict[str, Any]:
        sess = prehandle.get("sess") or prehandle.get("sid") or ""
        return {"aid": aid, "sess": sess, "showtype": showtype, "accver": 1, "lang": lang}


async def _collect(ctx: SolverContext, round_: wire.Round, aid: str) -> str:
    """Ask the wired TDC producer for the `collect` blob, or escalate.

    The producer is an async callable (or an object with `collect()`) that gets
    the round echo and returns the string `window.TDC.getData(true)` produced in
    that same profile. Anything else is treated as "no fingerprint" rather than
    silently sending an empty one.
    """
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    producer = extra.get("tencent_tdc")
    if producer is None:
        raise NeedsBrowserError(
            "tencent: TDC collect needs the vendor script inside a browser",
            context={
                "artifact": "collect",
                "aid": aid,
                "hint": "wire ctx.extra['tencent_tdc'] or use the browser lane",
            },
        )
    call = getattr(producer, "collect", producer)
    produced = call({"aid": aid, "sess": round_.sess, "pic1": round_.pic1, "pic2": round_.pic2, "echo": round_.echo})
    if inspect.isawaitable(produced):
        produced = await produced
    if isinstance(produced, dict):
        produced = produced.get("collect")
    if not isinstance(produced, str) or not produced.strip():
        raise SensorError(
            "tencent: the TDC producer returned an empty collect",
            context={"aid": aid, "type": type(produced).__name__},
        )
    return produced
