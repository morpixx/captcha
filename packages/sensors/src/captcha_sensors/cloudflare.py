"""Cloudflare challenge builder.

Cloudflare's managed challenge (orchestrate/jsd) runs obfuscated JS in the
browser that produces the `cf_clearance` cookie after /cdn-cgi/challenge-
platform POSTs. We can't realistically re-implement the generator — it
changes weekly and leans on timing/feature probes. Approach:

1. Scrape the challenge HTML/JS from the target (ctx.challenge_js).
2. Hand it to captcha_js_runtime (V8/Node sidecar) with a fingerprint-
   shaped `window` shim.
3. Harvest the `cf_clearance` POST body it tries to submit.

For Turnstile the solve path is different (see solvers/turnstile) — this
builder targets the 5s interstitial / managed challenge only.

# FILL: shim surface (window.*, document.*, WebGL fakes, performance)
"""

from __future__ import annotations

from typing import Any

from captcha_common.enums import AntibotEngine
from captcha_common.errors import ChallengeExpiredError, JSRuntimeError, SensorError

from .base import BuildContext, SensorBuilder
from .registry import register_builder


@register_builder(AntibotEngine.CLOUDFLARE)
class CloudflareChallengeBuilder(SensorBuilder):
    name = "cloudflare"
    requires_challenge_js = True
    uses_js_runtime = True

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        if not ctx.challenge_js:
            raise ChallengeExpiredError(
                "cloudflare builder needs scraped challenge JS",
                context={"site": ctx.site_url},
            )
        if ctx.fingerprint is None:
            raise SensorError(
                "cloudflare builder needs fingerprint for window shim",
                context={"site": ctx.site_url},
            )

        shim = self._build_shim(ctx)
        # FILL: await ctx-provided js_runtime.run(
        #           script=ctx.challenge_js, shim=shim, timeout=15)
        # The runtime hook is injected by SolverContext, not this module —
        # the builder only prepares the inputs.
        raise JSRuntimeError(
            "cloudflare clearance extraction not yet wired — needs js-runtime sidecar",
            context={"has_js": True, "shim_keys": sorted(shim)},
        )

    def _build_shim(self, ctx: BuildContext) -> dict[str, Any]:
        # The shim mimics the subset of DOM/navigator/window that the
        # Cloudflare challenge probes:
        #   navigator: ua, userAgentData, platform, languages, plugins,
        #              hardwareConcurrency, deviceMemory, webdriver=false,
        #              permissions.query -> deterministic map,
        #              storage.estimate -> fp-derived
        #   window: innerWidth, innerHeight, outerWidth, outerHeight,
        #           devicePixelRatio, chrome, cookieStore (noop),
        #           PerformanceObserver (noop), requestIdleCallback
        #   document: cookie (seed from ctx.cookies),
        #             referrer=ctx.extra.get("referrer", ""),
        #             visibilityState="visible", hasFocus=true
        #   screen: w/h/avail/color_depth/pixel_depth/orientation
        #   WebGL: vendor, renderer, extensions, parameters (per card)
        #   Audio: silent oscillator fp
        #   performance: timing + now() clock tied to ctx.timestamp_ms
        # FILL: port fingerprint -> shim; keep defaults deterministic per
        #       session so the challenge hashes land the same
        return {
            "navigator": {},
            "window": {},
            "document": {},
            "screen": {},
            "webgl": {},
            "audio": {},
            "performance": {"now_base_ms": ctx.timestamp_ms or 0},
            "cookies": dict(ctx.cookies),
            "headers": dict(ctx.headers),
        }
