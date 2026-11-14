"""PerimeterX (HUMAN) sensor builder.

PX wants three cookies:

- `_px`: collector token (AES-CBC blob, key derived from app id + vid).
- `_pxhd`: hash-digest, returned by /collector POST response.
- `_pxvid`: visitor id — new on first contact, echoed thereafter.

The /collector POST body is a signed JSON blob with the usual
fingerprint+behavior mix plus PX-specific markers (PX_APP_ID, PX_CT).

# FILL: full schema per PerimeterX release — see runbooks/perimeterx.md
"""

from __future__ import annotations

from typing import Any

from captcha_common.enums import AntibotEngine
from captcha_common.errors import SensorError

from .base import BuildContext, SensorBuilder
from .registry import register_builder


@register_builder(AntibotEngine.PERIMETERX)
class PerimeterXBuilder(SensorBuilder):
    name = "perimeterx"
    requires_challenge_js = False
    uses_js_runtime = False

    # FILL: px.js build hash
    PX_VERSION: str = ""

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        app_id = ctx.extra.get("px_app_id")
        if not app_id:
            raise SensorError(
                "perimeterx needs px_app_id in ctx.extra (PX0-prefixed or PXzz-style)",
                context={"site": ctx.site_url},
            )

        vid = ctx.cookies.get("_pxvid") or ctx.extra.get("px_vid")
        signals = self._collect_signals(ctx)

        collector_body = {
            "appId": app_id,
            "tag": "mobile" if ctx.fingerprint and getattr(ctx.fingerprint, "is_mobile", False) else "web",
            "uuid": vid,
            "ft": ctx.timestamp_ms or 0,
            "seq": ctx.extra.get("px_seq", 0),
            "en": "NTO",  # FILL: real encoding marker varies per release
            "pc": "",  # FILL: challenge-response hash
            "payload": signals,
        }

        raise SensorError(
            "perimeterx _px/_pxhd assembly not yet implemented — see runbooks/perimeterx.md",
            context={"app_id": app_id, "version": self.PX_VERSION},
        )

    def _collect_signals(self, ctx: BuildContext) -> dict[str, Any]:
        # PX sensor fields (subset; keys are two-char codes):
        #   PX101 ua, PX102 lang, PX103 plugins, PX104 timezone, PX105 scr,
        #   PX106 webgl, PX107 canvas, PX108 audio, PX109 touch,
        #   PX1200 mouse_track, PX1201 kbd_track, PX1202 scroll_track,
        #   PX1203 device_motion, PX1204 orientation, PX1300 perms,
        #   PX1301 battery, PX1400 webrtc_ips (empty if proxy)
        # FILL: translate fingerprint + behavior -> PX code map + AES-CBC
        return {}
