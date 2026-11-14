"""Castle.io sensor builder (castle_request_token).

Castle collects a compact fingerprint client-side and signs it with a
public key fetched from /v1/pub. Payload lands in the
`X-Castle-Request-Token` header on protected routes.

# FILL: full schema per Castle release — see runbooks/castle.md
"""

from __future__ import annotations

from typing import Any

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError

from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder


@register_builder(AntibotEngine.CASTLE)
class CastleBuilder(SensorBuilder):
    name = "castle"
    requires_challenge_js = False
    uses_js_runtime = False

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        # FILL: port castle.js collect() + ECDSA sign of the payload
        raise SensorError(
            "castle request-token assembly not yet implemented — see runbooks/castle.md",
            context={"site": ctx.site_url},
        )
