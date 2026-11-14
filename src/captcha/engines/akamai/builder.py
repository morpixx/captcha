"""Akamai Bot Manager sensor builder.

Produces the `sensor_data` string posted to /_bm/ endpoints. Modern AKAM-BM
layout is roughly:

    sensor_data = "<version>,<rand>,<encoded sections>"

where each section is a comma/semicolon/caret-delimited blob covering:

    device_info, perf, mouse, kbd, orientation, touch, battery,
    challenge_response, timing, abck_seed

Exact separators, field order and obfuscation (XOR mask, char substitution,
rolling CRC) mutate per release. Capture a fresh `_abck` challenge, diff
against the previous runbook, update `_pack_*` helpers.

# FILL: full schema per Akamai release — see runbooks/akamai.md
"""

from __future__ import annotations

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder


@register_builder(AntibotEngine.AKAMAI)
class AkamaiSensorBuilder(SensorBuilder):
    name = "akamai"
    requires_challenge_js = False
    uses_js_runtime = False

    #: AKAM-BM version marker baked into the payload. Bump per release.
    # FILL: scrape `bmak.ver` from the live challenge JS
    VERSION: str = "1.70"

    async def build(self, ctx: BuildContext) -> str:
        if ctx.fingerprint is None or ctx.behavior is None:
            raise SensorError(
                "akamai builder needs fingerprint + behavior",
                context={"site": ctx.site_url},
            )

        sections = [
            self._pack_device_info(ctx),
            self._pack_perf(ctx),
            self._pack_mouse(ctx),
            self._pack_kbd(ctx),
            self._pack_orientation(ctx),
            self._pack_touch(ctx),
            self._pack_battery(ctx),
            self._pack_challenge_response(ctx),
            self._pack_timing(ctx),
            self._pack_abck_seed(ctx),
        ]

        # FILL: the live joiner is not a plain comma — recover the real
        # concatenation + obfuscation (XOR mask, rolling CRC) from bmak.js
        raise SensorError(
            "akamai sensor_data assembly not yet implemented — see runbooks/akamai.md",
            context={"version": self.VERSION, "sections": len(sections)},
        )

    # --- section packers ------------------------------------------------

    def _pack_device_info(self, ctx: BuildContext) -> str:
        # fields: ua, platform, vendor, languages, cpu_class, device_memory,
        # hardware_concurrency, max_touch_points, screen w/h/avail/pixel_ratio,
        # inner w/h, color_depth, pixel_depth, timezone_offset, do_not_track,
        # cookie_enabled, plugins_hash, mime_types_hash
        # FILL: translate fingerprint fields -> Akamai field ordering
        return ""

    def _pack_perf(self, ctx: BuildContext) -> str:
        # fields: nav timing (fetchStart, domainLookup*, connect*, request*,
        # response*, domLoading, domInteractive, domComplete, loadEvent*)
        # relative to navigationStart, plus bmak init delta
        # FILL: pull from behavior.perf_timeline
        return ""

    def _pack_mouse(self, ctx: BuildContext) -> str:
        # fields: list of (t, x, y, kind) where kind is press/release/move.
        # bmak stores diffs, not absolutes; first point anchors.
        # FILL: feed behavior.mouse_events through diff encoder
        return ""

    def _pack_kbd(self, ctx: BuildContext) -> str:
        # fields: list of (t, keycode, kind) — bmak wraps codes with a
        # rotating offset derived from the challenge nonce.
        # FILL: port the rotation from bmak.ke
        return ""

    def _pack_orientation(self, ctx: BuildContext) -> str:
        # fields: alpha, beta, gamma samples at ~16ms; absolute flag.
        # Mobile only; desktop sends "0".
        # FILL: emit from fingerprint.device_motion when touch profile
        return ""

    def _pack_touch(self, ctx: BuildContext) -> str:
        # fields: touchstart/touchmove/touchend events per pointer id.
        # FILL: from behavior.touch_events
        return ""

    def _pack_battery(self, ctx: BuildContext) -> str:
        # fields: level, charging, chargingTime, dischargingTime. Chrome
        # removed the API — bmak now expects the "unsupported" sentinel.
        # FILL: emit sentinel unless fingerprint.has_battery_api
        return ""

    def _pack_challenge_response(self, ctx: BuildContext) -> str:
        # fields: hash of (nonce || sensor_prefix) via bmak's internal
        # roller — recomputed each post.
        # FILL: port bmak.cf (challenge function); needs crypto helpers
        return ""

    def _pack_timing(self, ctx: BuildContext) -> str:
        # fields: ts_init, ts_submit, delta, and the "typing cadence" buckets
        # bmak computes from kbd events.
        # FILL: pull init/submit timestamps from BuildContext.timestamp_ms
        return ""

    def _pack_abck_seed(self, ctx: BuildContext) -> str:
        # fields: parsed from existing _abck cookie — bmak echoes shard,
        # version marker and challenge counter back.
        # FILL: parse ctx.cookies["_abck"] -> (shard, ver, counter)
        return ""
