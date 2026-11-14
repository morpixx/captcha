"""BotDeflector sensor builder.

BotDeflector ships a slim collector that produces the `bd_token` cookie
plus a header signature. Lighter than Akamai/PX but same shape.

# FILL: full schema per BotDeflector release — see runbooks/botdeflector.md
"""

from __future__ import annotations

from typing import Any

from captcha_common.enums import AntibotEngine
from captcha_common.errors import SensorError

from .base import BuildContext, SensorBuilder
from .registry import register_builder


@register_builder(AntibotEngine.BOTDEFLECTOR)
class BotDeflectorBuilder(SensorBuilder):
    name = "botdeflector"
    requires_challenge_js = False
    uses_js_runtime = False

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        # FILL: port collector.js -> fingerprint dict + HMAC signature
        raise SensorError(
            "botdeflector token assembly not yet implemented — see runbooks/botdeflector.md",
            context={"site": ctx.site_url},
        )
