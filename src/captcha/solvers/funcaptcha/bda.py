"""Arkose BDA (Browser Data) builder.

Arkose's token endpoint requires a `bda` field — a base64 blob of browser
telemetry emitted by the obfuscated `v2/<pk>/api.js` loader. The loader
rotates per public_key and is pointer-chase heavy, so we do not re-implement
it in Python. Instead:

    1. Fetch the loader JS (ctx.http).
    2. Feed it to the JS runtime (ctx.js_runtime) alongside a fingerprint-
       shaped `window`/`navigator`/`document` shim.
    3. The loader builds a `bda` string internally; we hook its submit
       function and capture the argument.

This file exposes two surfaces:

  * `build_bda_payload(task, ctx, host, pk)` — the free function used by the
    regular FunCaptcha solver.
  * `FunCaptchaBDASolver` — a dedicated solver registered under
    `CaptchaType.FUNCAPTCHA_BDA` that returns the BDA string in `extra`
    (used by gateways that want to roll their own transport).
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import AntibotEngine, SolveMode
from captcha.domain.errors import (
    CaptchaError,
    JSRuntimeError,
    SensorError,
)
from captcha.solvers import Solver, SolverContext, SolverInfo, register_solver

_log = structlog.get_logger(__name__)

API_JS_URL_FMT = "https://{host_bare}/v2/{pk}/api.js"


async def build_bda_payload(*, task: Task, ctx: SolverContext, host: str, pk: str) -> str:
    """Produce the `bda` field for `/fc/gt2/public_key/<pk>`.

    Strategy:
      1. GET `<host>/v2/<pk>/api.js` to pull the current loader. Arkose
         rotates it per public_key.
      2. Pick a fingerprint from `ctx.fingerprint_store`.
      3. Hand both to `ctx.js_runtime` wrapped in a shim that captures the
         `bda` string the loader assembles.
      4. Return the captured string.

    Everything up to step 3 is wired; step 3 needs the BDA-specific shim,
    which belongs in `captcha.engines` once the live-RE lands.
    """
    if ctx.http is None:
        raise CaptchaError("bda builder requires ctx.http", context={"task": str(task.id)})
    if ctx.js_runtime is None:
        raise CaptchaError("bda builder requires ctx.js_runtime", context={"task": str(task.id)})
    if ctx.fingerprint_store is None:
        raise CaptchaError("bda builder requires ctx.fingerprint_store", context={"task": str(task.id)})

    # 1. fetch loader
    host_bare = host.replace("https://", "").replace("http://", "").strip("/")
    api_js_url = API_JS_URL_FMT.format(host_bare=host_bare, pk=pk)
    resp = await ctx.http.get(
        api_js_url,
        headers={"referer": task.site_url or "", "accept": "application/javascript,*/*;q=0.8"},
    )
    if resp.status != 200 or not resp.text:
        raise SensorError(
            "arkose /v2/<pk>/api.js fetch failed",
            context={"status": resp.status, "url": api_js_url, "bytes": len(resp.body)},
        )
    loader_js = resp.text

    # 2. pick fingerprint
    try:
        fingerprint = ctx.fingerprint_store.pick({"browser": "chrome"})
    except Exception as exc:
        raise CaptchaError(
            "no chrome fingerprint available for arkose shim",
            context={"err": str(exc)},
        ) from exc

    # 3. shim — the surface Arkose probes.
    # FILL: finalize the shim list — dump a live `bda`-emitting page, diff
    # the `navigator.*`/`window.*`/`document.*` touches, and move the full
    # list into `captcha.engines.arkose` when the builder lands. For now
    # we hand the loader a sketch; it will fail in V8 but the failure is
    # loud and names the missing symbol, which is the next FILL in queue.
    shim = _build_shim(fingerprint=fingerprint, task=task, pk=pk)

    # 4. execute — the loader is designed to POST its own bda; we instead
    # overwrite its XHR/fetch to capture the field. The capture snippet is
    # prepended before the loader source, so by the time the loader runs,
    # fetch is already hooked.
    capture_prefix = _capture_prefix()
    full_script = capture_prefix + "\n" + loader_js + "\n" + _capture_suffix()
    try:
        bda = await ctx.js_runtime.exec_script(full_script, shim, timeout_ms=15_000)
    except JSRuntimeError:
        # the loader hit a missing shim symbol or a timing probe. The
        # runbook captures what to patch next.
        raise
    except Exception as exc:
        raise JSRuntimeError(
            "arkose loader exec failed",
            context={"url": api_js_url, "err": str(exc)},
        ) from exc

    if not isinstance(bda, str) or not bda:
        raise JSRuntimeError(
            "arkose loader did not emit a bda string",
            context={"got_type": type(bda).__name__},
        )
    return bda


def _build_shim(*, fingerprint: Any, task: Task, pk: str) -> dict[str, Any]:
    """Minimal window/navigator sketch. Expand as the loader complains."""
    ua = getattr(fingerprint, "user_agent", task.user_agent or "")
    lang = getattr(fingerprint, "accept_language", task.language or "en-US")
    renderer = getattr(fingerprint, "webgl_renderer", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics)")
    vendor = getattr(fingerprint, "webgl_vendor", "Google Inc. (Intel)")
    timezone = getattr(fingerprint, "timezone", "America/New_York")

    return {
        "navigator": {
            "userAgent": ua,
            "language": lang.split(",")[0] if lang else "en-US",
            "languages": [p.split(";")[0] for p in (lang or "en-US").split(",")],
            "platform": "Win32",
            "hardwareConcurrency": 8,
            "deviceMemory": 8,
            "webdriver": False,
            "plugins": [],
        },
        "window": {
            "innerWidth": 1280,
            "innerHeight": 720,
            "outerWidth": 1280,
            "outerHeight": 800,
            "devicePixelRatio": 1,
        },
        "screen": {"width": 1920, "height": 1080, "colorDepth": 24, "pixelDepth": 24},
        "document": {
            "cookie": "",
            "referrer": task.site_url or "",
            "visibilityState": "visible",
        },
        "webgl": {"vendor": vendor, "renderer": renderer},
        "timezone": timezone,
        "arkose": {"public_key": pk, "site": task.site_url or ""},
    }


def _capture_prefix() -> str:
    """Install a fetch/XHR hook that captures the `bda` form-field from the
    first call the loader makes to `/fc/gt2/public_key/<pk>`."""
    return r"""
var __captured_bda = null;
var __orig_fetch = typeof fetch === 'function' ? fetch : null;
fetch = function(url, opts) {
    try {
        if (opts && typeof opts.body === 'string' && opts.body.indexOf('bda=') !== -1) {
            var m = opts.body.match(/(^|&)bda=([^&]*)/);
            if (m) __captured_bda = decodeURIComponent(m[2]);
        }
    } catch (e) {}
    if (__orig_fetch) return __orig_fetch(url, opts);
    return Promise.resolve({ok: true, status: 200, json: function(){return {};}, text: function(){return '';}});
};
var __OrigXHR = typeof XMLHttpRequest === 'function' ? XMLHttpRequest : null;
function __FakeXHR() {
    this.open = function(){};
    this.setRequestHeader = function(){};
    this.send = function(body) {
        try {
            if (typeof body === 'string' && body.indexOf('bda=') !== -1) {
                var m = body.match(/(^|&)bda=([^&]*)/);
                if (m) __captured_bda = decodeURIComponent(m[2]);
            }
        } catch (e) {}
    };
    this.readyState = 4;
    this.status = 200;
    this.responseText = '{}';
    this.getAllResponseHeaders = function(){return '';};
    this.onreadystatechange = null;
    this.onload = null;
    this.onerror = null;
}
XMLHttpRequest = __FakeXHR;
"""


def _capture_suffix() -> str:
    """After the loader runs, hand back whatever it captured."""
    return "__captured_bda"


@register_solver
class FunCaptchaBDASolver(Solver):
    """BDA-only solver — returns the BDA string without talking to Arkose.

    Useful when the gateway wants to drive the HTTP transport itself and
    only needs us to run the obfuscated loader. The token stays empty; the
    BDA lands in `extra['bda']`.
    """

    info: ClassVar[SolverInfo] = SolverInfo(
        name="funcaptcha_bda",
        types=(CaptchaType.FUNCAPTCHA_BDA,),
        http=True,
        browser=False,
        needs_js_runtime=True,
        experimental=True,
        version="0.1.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        pk = task.sitekey or (task.payload or {}).get("public_key")
        if not pk:
            raise CaptchaError(
                "funcaptcha_bda missing public_key",
                context={"task": str(task.id)},
            )
        host = (task.payload or {}).get("host") or "https://client-api.arkoselabs.com"
        # FILL: pull a live BDA loader from the target and persist a captured
        # sample under `fixtures/arkose/<pk>.js` so the harness can replay.
        # Until the shim set is complete, this will raise JSRuntimeError on
        # the first missing symbol — the message names it, patch and rerun.
        bda = await build_bda_payload(task=task, ctx=ctx, host=host, pk=str(pk))
        return SolverResult(
            token="",
            solve_mode=SolveMode.HTTP,
            extra={"bda": bda, "public_key": pk, "host": host, "engine": AntibotEngine.BOTGUARD.value},
        )
