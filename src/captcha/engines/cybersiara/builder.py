"""CyberSiara sensor builder.

CyberSiara posts a signed proof-of-work + fingerprint bundle to /v1/token.
Difficulty bumps dynamically based on IP reputation.

# FILL: full schema per CyberSiara release — see runbooks/cybersiara.md
"""

from __future__ import annotations

from typing import Any

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder


@register_builder(AntibotEngine.CYBERSIARA)
class CyberSiaraBuilder(SensorBuilder):
    name = "cybersiara"
    requires_challenge_js = False
    uses_js_runtime = False

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        # FILL: solve PoW via captcha.crypto.pow.cybersiara, sign payload
        raise SensorError(
            "cybersiara token assembly not yet implemented — see runbooks/cybersiara.md",
            context={"site": ctx.site_url},
        )
