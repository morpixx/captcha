"""Imperva (Incapsula) sensor builder.

Imperva's `___utmvc` cookie is produced by main.js on first hit and refreshed
by /reese84 POSTs. Payload carries the standard fingerprint set plus Imperva-
specific XHR/beacon markers. `incap_ses_*` is the session counter that gets
echoed back so the WAF knows we survived challenge.

# FILL: full schema per Imperva release — see runbooks/imperva.md
"""

from __future__ import annotations

from typing import Any

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError

from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder


@register_builder(AntibotEngine.IMPERVA)
class ImpervaBuilder(SensorBuilder):
    name = "imperva"
    requires_challenge_js = False
    uses_js_runtime = False

    # FILL: main.js build id; drives XOR table
    IMPV_VERSION: str = ""

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        if ctx.fingerprint is None:
            raise SensorError(
                "imperva builder needs fingerprint",
                context={"site": ctx.site_url},
            )

        reese_body = {
            "solution": {
                "interrogation": self._collect_signals(ctx),
                "version": "stable",  # FILL: match main.js build channel
            },
            "old_token": ctx.cookies.get("reese84", ""),
            "error": None,
            "performance": {"interrogation": 0},  # FILL: ms spent in collect
        }

        raise SensorError(
            "imperva reese84/___utmvc assembly not yet implemented — see runbooks/imperva.md",
            context={"version": self.IMPV_VERSION, "has_old": bool(reese_body["old_token"])},
        )

    def _collect_signals(self, ctx: BuildContext) -> dict[str, Any]:
        # Imperva interrogation block (order matters, keys are opaque):
        #   p (platform), l (lang), tz (offset), scr (w,h,cd,pd),
        #   plg (plugin fp), wgl (webgl vendor/renderer + extensions),
        #   cnv (canvas hash), aud (audio hash), bat (battery or sentinel),
        #   nav (userAgentData brand list), dev (deviceMemory),
        #   hc (hardwareConcurrency), tp (maxTouchPoints), pr (dpr),
        #   mm (mouse move bursts), kb (keypress cadence),
        #   sc (scroll cadence), fl (feature flags: pdf, service_worker,
        #   clipboard, bluetooth, usb, serial), perm (permissions matrix)
        # Each field goes through XOR with the rolling IMPV table.
        # FILL: port main.js collect() + XOR encoder
        return {}
