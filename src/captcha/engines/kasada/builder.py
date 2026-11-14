"""Kasada sensor builder (x-kpsdk-cd / x-kpsdk-cr).

Kasada's ips.js produces two headers:

- `x-kpsdk-cd`: base64(json({workTime, id, duration, answers})).
- `x-kpsdk-cr`: a signed challenge-response string.

The payload is gated by a proof-of-work puzzle whose difficulty comes from
`x-kpsdk-ct` on the first 429. Our builder must:

1. Solve PoW (captcha.crypto.pow.kasada).
2. Collect fingerprint fields Kasada samples (narrow set vs Akamai).
3. Sign with the rolling key derived from `kpsdk_version` + nonce.

# FILL: full schema per Kasada release — see runbooks/kasada.md
"""

from __future__ import annotations

from typing import Any

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError

from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder


@register_builder(AntibotEngine.KASADA)
class KasadaBuilder(SensorBuilder):
    name = "kasada"
    requires_challenge_js = False
    uses_js_runtime = False

    # FILL: ips.js version — scrape from challenge response
    KPSDK_VERSION: str = ""

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        if ctx.fingerprint is None:
            raise SensorError(
                "kasada builder needs fingerprint",
                context={"site": ctx.site_url},
            )

        difficulty = ctx.extra.get("kpsdk_difficulty")
        pow_seed = ctx.extra.get("kpsdk_st")
        if difficulty is None or pow_seed is None:
            raise SensorError(
                "kasada needs x-kpsdk-ct bootstrap (difficulty + st)",
                context={"extra_keys": sorted(ctx.extra)},
            )

        # FILL: await captcha.crypto.pow.kasada.solve(seed=pow_seed,
        #                                             difficulty=difficulty)
        pow_answers: list[int] = []

        cd_payload = {
            "workTime": 0,  # FILL: ms elapsed during PoW
            "id": "",  # FILL: ctx.extra["kpsdk_id"]
            "duration": 0,  # FILL: wall clock of this request assembly
            "answers": pow_answers,
            "d": self._collect_signals(ctx),
            "st": pow_seed,
            "rst": "",  # FILL: ctx.extra.get("kpsdk_rst", "")
        }

        raise SensorError(
            "kasada x-kpsdk-cd/cr assembly not yet implemented — see runbooks/kasada.md",
            context={"version": self.KPSDK_VERSION, "difficulty": difficulty},
        )

    def _collect_signals(self, ctx: BuildContext) -> dict[str, Any]:
        # Kasada samples narrower than Akamai:
        #   ua, platform, hw.concurrency, devmem, screen (w,h,avail),
        #   timezone offset, webgl vendor/renderer, canvas fp (truncated),
        #   audio fp (truncated), battery (or sentinel), touch_points,
        #   plugins length, mime count, lang list head
        # FILL: translate fingerprint -> kasada "d" dict
        return {}
