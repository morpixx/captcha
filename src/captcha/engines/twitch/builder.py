"""Twitch Integrity sensor builder.

Twitch's `Client-Integrity` header is produced by a WASM module loaded by
the main app. Flow: GET /integrity -> JWT with short TTL; builder swaps
in a signed fingerprint bundle.

# FILL: full schema per integrity release — see runbooks/twitch.md
"""

from __future__ import annotations

from typing import Any

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError

from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder


@register_builder(AntibotEngine.TWITCH)
class TwitchIntegrityBuilder(SensorBuilder):
    name = "twitch"
    requires_challenge_js = True
    uses_js_runtime = True

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        # FILL: run WASM integrity module in js-runtime with fingerprint
        #       shim; extract `Client-Integrity` JWT.
        raise SensorError(
            "twitch client-integrity assembly not yet implemented — see runbooks/twitch.md",
            context={"site": ctx.site_url, "has_js": bool(ctx.challenge_js)},
        )
