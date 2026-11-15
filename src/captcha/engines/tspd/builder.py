"""F5 Shape (TSPD) sensor builder.

Shape has no public payload description at all: the client is a polymorphic VM
(new bytecode per tenant, per deploy), it samples DOM events and posts its own
sensor, and the cookie it leaves behind is renamed per customer (`TSPD_101`,
`TS01abcdef` -- see endpoints). Anything this builder could invent about the
byte layout would be wrong by the next release.

So it builds the one thing that is actually reusable: a capture program for the
tenant script (:mod:`captcha.engines.capture`). The solver runs it, reads the
`TS*` cookie the VM wrote, and replays the sensor POST the VM attempted. A script
that needs real DOM events produces nothing that way -- that is the browser lane's
job, and the solver escalates instead of shipping a guess.
"""

from __future__ import annotations

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ChallengeExpiredError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.capture import setup_js, wrap
from captcha.engines.registry import register_builder


@register_builder(AntibotEngine.TSPD)
class TspdBuilder(SensorBuilder):
    """Capture program for the F5 Shape client script."""

    name = "tspd"
    requires_challenge_js = True
    uses_js_runtime = True

    async def build(self, ctx: BuildContext) -> str:
        if not ctx.challenge_js:
            raise ChallengeExpiredError(
                "tspd builder needs the scraped Shape script; the VM is the generator",
                context={"site": ctx.site_url},
            )
        setup = setup_js(
            href=ctx.site_url,
            user_agent=str(ctx.extra.get("ua") or ""),
            cookies=dict(ctx.cookies),
            storage=dict(ctx.extra.get("storage") or {}),
        )
        return wrap(ctx.challenge_js, engine="tspd", setup=setup)


__all__ = ["TspdBuilder"]
