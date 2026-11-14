"""Google BotGuard sensor builder.

BotGuard (reCAPTCHA / YouTube) is VM-driven: a minified interpreter runs
opcodes streamed from /recaptcha/api2/bframe. Our builder scrapes the
bytecode and runs it through js-runtime with a navigator shim.

# FILL: VM opcode table per release — see runbooks/botguard.md
"""

from __future__ import annotations

from typing import Any

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError

from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder


@register_builder(AntibotEngine.BOTGUARD)
class BotGuardBuilder(SensorBuilder):
    name = "botguard"
    requires_challenge_js = True
    uses_js_runtime = True

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        # FILL: feed ctx.challenge_js (bframe) into js-runtime with
        #       Google-flavoured shim; harvest the resulting `bg` token.
        raise SensorError(
            "botguard token assembly not yet implemented — see runbooks/botguard.md",
            context={"site": ctx.site_url, "has_js": bool(ctx.challenge_js)},
        )
