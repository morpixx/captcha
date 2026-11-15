"""Alibaba / Aliyun captcha: NoCaptcha v1 over HTTP, Captcha 2.0 in a browser.

v1 (the JSONP API):
  1. best-effort UMID token from ynuf.aliapp.org;
  2. GET nocaptcha/get.jsonp -> csessionid, sig, token, scene, bg/front;
  3. solve the slider (or rotate/click variant) through the CV registry;
  4. POST nocaptcha/analyze.jsonp -> nc_sid + nc_token, the pair the site
     backend verifies.

v2 (the modern widget) never exposes an HTTP endpoint: the page SDK signs a
CaptchaVerifyParam that only the site backend can verify. When a task carries
a page but no appkey/scene, solve_http raises NeedsBrowserError and browser.py
runs the widget in the browser lane. Returning a made-up nc_token there would
be the exact "HTTP success" lie the contract forbids.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ConfigError, NeedsBrowserError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import browser as browser_lane
from . import endpoints as ep
from . import wire


@register_solver
class AlibabaSolver(InteractiveBase):
    """Aliyun NoCaptcha v1 over HTTP; Captcha 2.0 through the browser lane."""

    info = SolverInfo(
        name="alibaba",
        types=(CaptchaType.ALIBABA,),
        http=True,
        browser=True,
        needs_js_runtime=True,  # UMID collect and the v2 widget are JS
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = task.payload
        appkey = _first(payload, "appkey", "appKey", "app_key")
        scene = _first(payload, "scene", "sceneId", "SceneId", "scene_id")
        if not appkey or not scene:
            if _page_available(task):
                # Same contract signal as Hunt: worker_http knows this hop,
                # worker_cv does not yet; preferred_mode=BROWSER reaches the lane.
                raise NeedsBrowserError(
                    "aliyun Captcha 2.0 signs CaptchaVerifyParam in the page; the JSONP path needs appkey + scene",
                    context={"need": ["appkey", "scene"], "browser": True},
                )
            raise UnsolvableError(
                "alibaba needs appkey + scene (v1), or pageurl for the browser lane",
                context={"keys": sorted(payload)},
            )
        if ctx.http is None:
            raise ConfigError("alibaba solver needs ctx.http", context={"solver": self.info.name})

        start = time.monotonic()
        headers = _headers(task)
        ua = str(_first(payload, "user_agent", "userAgent") or task.user_agent or "")
        umid = str(_first(payload, "umid_token", "umidToken", "umid") or "")
        if not umid:
            umid = await _umid(ctx, task, headers, str(appkey), str(scene))

        params = {
            "callback": "cb",
            "ua": ua,
            "umidToken": umid,
            "scene": str(scene),
            "appkey": str(appkey),
            "lang": str(_first(payload, "lang", "language") or task.language or "cn"),
            "_": int(time.time() * 1000),
        }
        resp = await ctx.http.get(ep.NC_GET, params=params, headers=headers)
        wire.raise_for_status(resp, "alibaba get")
        root = wire.jsonp_object(resp.text, "alibaba get")
        challenge = wire.parse_challenge(root)

        kind = wire.variant_kind(challenge["variant"], bool(challenge["front"]), challenge["prompt"])
        cv_kind, cv_payload = await _cv_inputs(ctx, headers, challenge, kind, payload)
        cv = await self._solve_cv(cv_kind, task, ctx, cv_payload)

        body = wire.analyze_body(
            challenge,
            cv,
            kind,
            appkey=str(appkey),
            ua=ua,
            seed=payload.get("seed"),
        )
        vresp = await ctx.http.post(ep.NC_ANALYZE, data=body, headers=headers, params={"callback": "cb"})
        wire.raise_for_status(vresp, "alibaba analyze")
        vroot = wire.jsonp_object(vresp.text, "alibaba analyze")
        result = wire.find_result(vroot)

        token = str(result.get("nc_token") or result.get("CaptchaVerifyParam") or "") or None
        return SolverResult(
            token=token,
            extra={"data": result},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        return await browser_lane.solve(task, ctx, solver=self)


async def _cv_inputs(
    ctx: SolverContext,
    headers: dict[str, str],
    challenge: dict[str, Any],
    kind: str,
    payload: dict[str, Any],
) -> tuple[CaptchaType, dict[str, Any]]:
    """Fetch the images and shape the payload the CV solver expects."""
    bg = await _image(ctx, challenge["bg"], headers)
    seed = payload.get("seed")
    if kind == "slider":
        if not challenge["front"]:
            raise UnsolvableError("alibaba slider challenge has no front image", context={})
        front = await _image(ctx, challenge["front"], headers)
        return CaptchaType.SLIDER, {"bg": wire.b64(bg), "piece": wire.b64(front), "seed": seed}
    if kind == "rotate":
        return CaptchaType.ROTATE, {"image": wire.b64(bg), "seed": seed}
    if not challenge["prompt"]:
        raise UnsolvableError("alibaba click challenge has no target text", context={})
    return CaptchaType.CLICK, {"image": wire.b64(bg), "target": challenge["prompt"], "seed": seed}


async def _umid(
    ctx: SolverContext,
    task: Task,
    headers: dict[str, str],
    appkey: str,
    scene: str,
) -> str:
    """Best-effort UMID token; the v1 endpoints tolerate an empty one."""
    collect = task.payload.get("umid_collect")
    body = collect if isinstance(collect, dict) else {"ua": str(task.user_agent or ""), "scene": scene, "appkey": appkey}
    try:
        resp = await ctx.http.post(ep.UMID_ENDPOINT, json=body, headers=headers)
        wire.raise_for_status(resp, "alibaba umid")
        root = wire.jsonp_object(resp.text, "alibaba umid")
    except Exception:
        # UMID is a fingerprint, not the challenge: a registry failure must not
        # turn a solvable task into ERR_UNSOLVABLE before we even asked for one.
        return ""
    data = root.get("data") if isinstance(root.get("data"), dict) else root
    return str(_first(data, "tn", "umidToken", "token", "umid") or "")


async def _image(ctx: SolverContext, url: str, headers: dict[str, str]) -> bytes:
    if url.startswith("data:"):
        try:
            return base64.b64decode(url.split(",", 1)[1])
        except Exception as exc:
            raise UnsolvableError("alibaba image is a broken data URI", context={"head": url[:48]}) from exc
    resp = await ctx.http.get(url, headers=headers)
    wire.raise_for_status(resp, "alibaba image")
    if not resp.body:
        raise UnsolvableError("alibaba image body is empty", context={"url": url})
    return resp.body


def _headers(task: Task) -> dict[str, str]:
    payload = task.payload
    headers = {"accept": "*/*", "accept-language": str(_first(payload, "lang") or task.language or "cn")}
    ua = _first(payload, "user_agent", "userAgent") or task.user_agent
    if ua:
        headers["user-agent"] = str(ua)
    page = str(_first(payload, "pageurl", "websiteURL", "site_url") or task.site_url or "")
    if page:
        headers["referer"] = page
    return headers


def _page_available(task: Task) -> bool:
    payload = task.payload
    return any(payload.get(key) for key in ("pageurl", "websiteURL", "websiteUrl", "site_url")) or bool(
        task.site_url
    )


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
