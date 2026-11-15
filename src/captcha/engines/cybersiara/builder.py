"""CyberSiARA sensor builder.

CyberSiARA's widget is a slide-to-verify challenge: the script draws the puzzle,
collects the drag track and posts the verification itself, then hands the page a
master URL token. The verification body is not published (the vendor's own
`MasterController` builds it), so this builder does not pretend to assemble one.

Two artefacts:

* with the widget script in the payload -- a capture program
  (:mod:`captcha.engines.capture`) seeded with the site key and the page, so the
  script can run and the token can be read where it lands;
* without it -- nothing. A slide whose gesture has to be human-shaped belongs in
  the browser lane, and the solver escalates.
"""

from __future__ import annotations

import json

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.capture import setup_js, wrap
from captcha.engines.registry import register_builder


@register_builder(AntibotEngine.CYBERSIARA)
class CyberSiaraBuilder(SensorBuilder):
    """Capture program for the CyberSiARA widget script."""

    name = "cybersiara"
    requires_challenge_js = True
    uses_js_runtime = True

    async def build(self, ctx: BuildContext) -> str:
        if not ctx.challenge_js:
            raise SensorError(
                "cybersiara builder needs the widget script; a slide is minted client-side",
                context={"site": ctx.site_url},
            )
        sitekey = str(ctx.extra.get("sitekey") or "").strip()
        setup = "\n".join(
            (
                setup_js(
                    href=ctx.site_url,
                    user_agent=str(ctx.extra.get("ua") or ""),
                    cookies=dict(ctx.cookies),
                    storage=dict(ctx.extra.get("storage") or {}),
                ),
                f"var SlideMasterUrlId = {json.dumps(sitekey)};",
                f"var MasterUrlId = {json.dumps(sitekey)};",
                f"var __cybersiaraKey = {json.dumps(sitekey)};",
            )
        )
        return wrap(ctx.challenge_js, engine="cybersiara", setup=setup)


__all__ = ["CyberSiaraBuilder"]
