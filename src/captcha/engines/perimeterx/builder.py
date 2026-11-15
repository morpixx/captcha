"""PerimeterX (HUMAN Security) sensor builder.

Two PX deployments exist and only one of them is ours to assemble:

* **sensorless collector** (the majority of sessions, docs/reverse-2026.md §7):
  a single form POST to `collector-<appId>.px-cloud.net/api/v2/collector`
  carrying `appId, tag, ft, seq, en, uuid, vid, cts, sid, pc, payload`. This
  builder returns that body as a dict; the codec in :mod:`.codec` owns the
  obfuscation, `pc` fold and `sid` tag-block shift.
* **press / bundle** (`/assets/js/bundle` with WASM and a PoW): the payload is
  produced by the bundle itself, with its own constants. Handing that script to
  the JS runtime and capturing what it writes is the only honest option, so for
  that variant :meth:`build` returns a capture *program* (a `str`) built with
  :mod:`captcha.engines.capture` -- the solver executes it instead of POSTing
  a dict.

Signal keys are the EV2 codes. Only codes the public writeups name are filled;
a value that is absent is omitted rather than zero-filled, because PX scores a
synthetic `screen: 0x0` harder than a missing field.
"""

from __future__ import annotations

import json
import time
import uuid as uuidlib
from typing import Any

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.capture import setup_js, wrap
from captcha.engines.registry import register_builder

from . import codec

#: Collector `tag` for a desktop session; mobile deployments send `mobile`.
DEFAULT_TAG = "web"

#: EV2 keys this builder fills, in wire order. Kept as a tuple so the payload
#: byte stream is reproducible: PX's `pc` is computed over these bytes.
SIGNAL_KEYS: tuple[tuple[str, str], ...] = (
    ("PX101", "ua"),
    ("PX102", "lang"),
    ("PX104", "timezone"),
    ("PX105", "screen"),
    ("PX106", "webgl"),
    ("PX107", "canvas"),
    ("PX108", "audio"),
    ("PX109", "touch"),
    ("PX1200", "mouse"),
    ("PX1201", "keyboard"),
    ("PX1202", "scroll"),
    ("PX1203", "motion"),
    ("PX1204", "orientation"),
    ("PX1300", "permissions"),
    ("PX1301", "battery"),
    ("PX1400", "webrtc"),
)


@register_builder(AntibotEngine.PERIMETERX)
class PerimeterXBuilder(SensorBuilder):
    """Collector body for sensorless PX; capture program for the press bundle."""

    name = "perimeterx"
    requires_challenge_js = False
    uses_js_runtime = False

    async def build(self, ctx: BuildContext) -> dict[str, Any] | str:
        if ctx.challenge_js:
            return self.capture(ctx, ctx.challenge_js)

        app_id = _text(ctx.extra.get("app_id") or ctx.extra.get("px_app_id"))
        if not app_id:
            raise SensorError(
                "perimeterx needs the site's appId (P-prefixed) in ctx.extra",
                context={"site": ctx.site_url},
            )

        tag = _text(ctx.extra.get("tag")) or DEFAULT_TAG
        uuid = self.visitor_id(ctx)
        now_ms = int(time.time() * 1000)
        ft = _int(ctx.extra.get("ft"), ctx.timestamp_ms or now_ms)
        cts = _int(ctx.extra.get("cts"), ctx.timestamp_ms or now_ms)
        seq = _int(ctx.extra.get("seq"), 0)
        sid_source = _text(ctx.extra.get("sid")) or uuid
        signals = self.collect_signals(ctx)
        payload_json = json.dumps(signals, separators=(",", ":"), ensure_ascii=True)

        return {
            "appId": app_id,
            "tag": tag,
            "ft": ft,
            "seq": seq,
            "en": codec.en_field(),
            "uuid": uuid,
            "vid": _text(ctx.extra.get("vid")) or ctx.cookies.get("_pxvid") or uuid,
            "cts": cts,
            "sid": codec.sid_encode(sid_source),
            "pc": codec.pc_token(
                uuid=uuid,
                tag=tag,
                ft=ft,
                payload_json=payload_json,
                length=_int(ctx.extra.get("pc_len"), codec.DEFAULT_PC_LEN),
            ),
            "payload": codec.encode_payload(signals),
        }

    def capture(self, ctx: BuildContext, script: str) -> str:
        """Program that runs the press bundle and reports what it produced."""
        return wrap(
            script,
            engine="perimeterx",
            setup=setup_js(
                href=ctx.site_url,
                user_agent=_text(ctx.extra.get("ua")),
                cookies=dict(ctx.cookies),
                storage=dict(ctx.extra.get("storage") or {}),
            ),
        )

    def visitor_id(self, ctx: BuildContext) -> str:
        """`uuid`: an existing `_pxvid` if the client has one, else a fresh v4.

        Reusing the client's visitor id is what makes a second solve land in the
        same PX session instead of minting a stranger the collector distrusts.
        """
        for value in (ctx.extra.get("uuid"), ctx.extra.get("vid"), ctx.cookies.get("_pxvid")):
            text = _text(value)
            if text:
                return text
        return str(uuidlib.uuid4())

    def collect_signals(self, ctx: BuildContext) -> dict[str, Any]:
        """EV2 signal map, in :data:`SIGNAL_KEYS` order, absent values skipped."""
        values = {
            "ua": _fingerprint(ctx, "user_agent"),
            "lang": _first_language(_fingerprint(ctx, "accept_language")),
            "timezone": _fingerprint(ctx, "timezone"),
            "screen": ctx.extra.get("screen"),
            "webgl": _webgl(ctx),
            "canvas": _fingerprint(ctx, "canvas_hash"),
            "audio": _fingerprint(ctx, "audio_hash"),
            "touch": ctx.extra.get("touch"),
            "mouse": _behavior(ctx, "mouse", "mouse_track", "mouse_events"),
            "keyboard": _behavior(ctx, "keyboard", "kbd", "kbd_track", "key_events"),
            "scroll": _behavior(ctx, "scroll", "scroll_track"),
            "motion": _behavior(ctx, "motion", "device_motion"),
            "orientation": _behavior(ctx, "orientation"),
            "permissions": ctx.extra.get("permissions"),
            "battery": ctx.extra.get("battery"),
            "webrtc": ctx.extra.get("webrtc_ips"),
        }
        signals: dict[str, Any] = {}
        for key, name in SIGNAL_KEYS:
            value = values.get(name)
            if value not in (None, "", [], {}):
                signals[key] = value
        return signals


def _webgl(ctx: BuildContext) -> dict[str, str] | None:
    vendor = _fingerprint(ctx, "webgl_vendor")
    renderer = _fingerprint(ctx, "webgl_renderer")
    if not vendor and not renderer:
        return None
    return {"vendor": vendor or "", "renderer": renderer or ""}


def _fingerprint(ctx: BuildContext, name: str) -> str:
    profile = ctx.fingerprint
    value = getattr(profile, name, None) if profile is not None else None
    return _text(value)


def _behavior(ctx: BuildContext, *names: str) -> Any:
    """Behavior track field, whether the track is a mapping or an object."""
    track = ctx.behavior
    if track is None:
        return None
    for name in names:
        value = track.get(name) if isinstance(track, dict) else getattr(track, name, None)
        if value not in (None, "", [], {}):
            return value
    return None


def _first_language(value: str) -> str:
    return value.split(",")[0].split(";")[0].strip() if value else ""


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


__all__ = ["DEFAULT_TAG", "SIGNAL_KEYS", "PerimeterXBuilder"]
