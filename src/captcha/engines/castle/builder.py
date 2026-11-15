"""Castle.io sensor builder (castle_request_token).

The token is built by `castle.browser.js` from device and behavior signals and
never leaves the browser: there is no server handshake to replay and no public
byte layout for the packed token (the one open reimplementation,
`castleio-gen`, covers v11/2.6.0 only and has been archived). So the builder
has one job -- hand the solver a program that drives the SDK under the capture
harness (:mod:`captcha.engines.capture`) and reports whatever the SDK stored.

What the program does:

1. calls `Castle.configure({pk})` with the publishable key from `ctx.extra`,
2. calls `createRequestToken()` and stashes the result under
   `castle_request_token` in `localStorage` -- a sync `.then` keeps the capture
   inside one evaluation,
3. leaves `__cuid` (the device id) in the same capture.

A bundle that needs a real worker or a DOM event loop will not produce a token
this way; that is what the browser lane is for, and the solver escalates instead
of shipping a placeholder.
"""

from __future__ import annotations

import json

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.capture import setup_js, wrap
from captcha.engines.registry import register_builder

#: Token field the driving snippet writes into storage.
TOKEN_KEY = "castle_request_token"

#: Runs after the SDK script: configure once, mint one token, keep it in storage.
DRIVE_SNIPPET = """
(function () {
  try {
    var sdk = window.Castle;
    if (!sdk || typeof sdk.configure !== "function") { __cap.errors.push("castle: sdk not configured"); return; }
    var instance = sdk.configure({ pk: __castlePk });
    if (!instance || typeof instance.createRequestToken !== "function") { __cap.errors.push("castle: no createRequestToken"); return; }
    var maybe = instance.createRequestToken();
    if (maybe && typeof maybe.then === "function") {
      maybe.then(function (token) { if (token) { localStorage.setItem("castle_request_token", String(token)); } });
    } else if (maybe) {
      localStorage.setItem("castle_request_token", String(maybe));
    }
  } catch (e) { __cap.errors.push("castle: " + String(e)); }
})();
"""


@register_builder(AntibotEngine.CASTLE)
class CastleBuilder(SensorBuilder):
    """Capture program for `castle.browser.js`; the SDK owns the token format."""

    name = "castle"
    requires_challenge_js = True
    uses_js_runtime = True

    async def build(self, ctx: BuildContext) -> str:
        if not ctx.challenge_js:
            raise SensorError(
                "castle builder needs the castle.js source; the token is minted client-side",
                context={"site": ctx.site_url},
            )
        pk = str(ctx.extra.get("publishable_key") or ctx.extra.get("pk") or "").strip()
        if not pk:
            raise SensorError(
                "castle builder needs the publishable key (pk_...) in ctx.extra",
                context={"site": ctx.site_url},
            )
        setup = "\n".join(
            (
                setup_js(
                    href=ctx.site_url,
                    user_agent=str(ctx.extra.get("ua") or ""),
                    cookies=dict(ctx.cookies),
                    storage=dict(ctx.extra.get("storage") or {}),
                ),
                f"var __castlePk = {json.dumps(pk)};",
            )
        )
        return wrap(f"{ctx.challenge_js}\n{DRIVE_SNIPPET}", engine="castle", setup=setup)


__all__ = ["DRIVE_SNIPPET", "TOKEN_KEY", "CastleBuilder"]
