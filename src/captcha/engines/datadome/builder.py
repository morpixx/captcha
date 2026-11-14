"""DataDome sensor builder.

DataDome's /js/ endpoint expects a JSON body with a (per-release) field
table — most values are collected from navigator/screen/webgl/audio/canvas
and signed with a rolling key derived from the `cid` cookie + `hsh` nonce.

# FILL: full schema per DataDome release — see runbooks/datadome.md
"""

from __future__ import annotations

from typing import Any

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError

from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder


@register_builder(AntibotEngine.DATADOME)
class DataDomeBuilder(SensorBuilder):
    name = "datadome"
    requires_challenge_js = False
    uses_js_runtime = False

    # FILL: dd.js build hash — scrape `dd.m` on challenge load
    DDJS_HASH: str = ""

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        if ctx.fingerprint is None:
            raise SensorError(
                "datadome builder needs fingerprint",
                context={"site": ctx.site_url},
            )

        payload: dict[str, Any] = {
            "ddk": "",  # FILL: public site key from ctx.extra["ddk"]
            "Referer": ctx.site_url,
            "responsePage": "",  # FILL: challenge type (captcha|interstitial)
            "ua": "",  # FILL: ctx.fingerprint.user_agent
            "cid": ctx.cookies.get("datadome", ""),
            "hsh": "",  # FILL: nonce from challenge page
            "jsType": "ch",  # "ch" for challenge, "cs" for captcha solve
            "parsed_body": self._collect_signals(ctx),
        }
        raise SensorError(
            "datadome JSON assembly not yet implemented — see runbooks/datadome.md",
            context={"ddjs": self.DDJS_HASH, "keys": sorted(payload)},
        )

    def _collect_signals(self, ctx: BuildContext) -> dict[str, Any]:
        # Fields observed in the wild (subset; order matters for the signer):
        #   jsr, jsf, wh, jsd, ua, ifr, ifrh, lang, langh, nav, navh, scr,
        #   scrh, pxr, dpr, dprh, hc, dm, plg, plgh, mem, cok, tzo, tzoh,
        #   wdc, pr, prh, aud, audh, canv, canvh, webgl, webglh, perm, permh,
        #   bat, bath, dmot, dmoth, dori, dorih, hw, hwh, keyb, keybh
        # Each `*h` is a hash of the value with the rolling key.
        # FILL: port fingerprint -> datadome dict + sign each pair
        return {}
