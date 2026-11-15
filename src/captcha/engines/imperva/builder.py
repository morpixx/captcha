"""Imperva (Incapsula) sensor builder.

Imperva has two client artifacts and one builder per artifact, because the two
are not the same kind of thing:

* **reese84** -- the sensor script collects an *interrogation* and POSTs
  `{"solution": {"interrogation": {...}, "version": ...}, "old_token": <previous
  reese84>, "error": null, "performance": {"interrogation": <ms>}}` to
  `/_Incapsula_Resource?SWKMTFSR=1&e=<nonce>`. The envelope is public and stable;
  the interrogation keys are opaque and rotate per build, so this builder fills
  the names the public writeups carry and lets `ctx.extra["interrogation"]` patch
  the rest. This variant returns a `dict`.
* **___utmvc** -- the older cookie, written by the inline `main.js` in the
  interruption page. There is no public byte layout for it (docs/reverse-2026.md
  §8: "no published payload schema"), and the script *is* the generator, so this
  variant returns a capture *program* (`str`) built by
  :mod:`captcha.engines.capture`: the solver runs it and reads
  `document.cookie` plus any request the script tried to send.

Sources: docs/reverse-2026.md §8; hyper-sdk 2026-11 (reese84 / ___utmvc /
dynamic reese script), Capsolver Imperva (reeseScriptURL required).
"""

from __future__ import annotations

from typing import Any

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.capture import setup_js, wrap
from captcha.engines.registry import register_builder

#: Channel string the bundle puts in the solution; deployments on the "beta"
#: channel send that instead. Not a secret -- the page names it.
DEFAULT_VERSION = "stable"

#: Interrogation keys this builder fills, in wire order. The set comes from the
#: stub's field table (p, l, tz, scr, plg, wgl, cnv, aud, bat, nav, dev, hc, tp,
#: pr, mm, kb, sc, fl, perm); names are the engine's, values are ours.
INTERROGATION_KEYS: tuple[tuple[str, str], ...] = (
    ("p", "platform"),
    ("l", "language"),
    ("tz", "timezone"),
    ("scr", "screen"),
    ("plg", "plugins"),
    ("wgl", "webgl"),
    ("cnv", "canvas"),
    ("aud", "audio"),
    ("bat", "battery"),
    ("nav", "user_agent_data"),
    ("dev", "device_memory"),
    ("hc", "hardware_concurrency"),
    ("tp", "touch_points"),
    ("pr", "pixel_ratio"),
    ("mm", "mouse"),
    ("kb", "keyboard"),
    ("sc", "scroll"),
    ("fl", "features"),
    ("perm", "permissions"),
)


@register_builder(AntibotEngine.IMPERVA)
class ImpervaBuilder(SensorBuilder):
    """reese84 envelope as a dict; `___utmvc` script as a capture program."""

    name = "imperva"
    requires_challenge_js = False
    uses_js_runtime = False

    async def build(self, ctx: BuildContext) -> dict[str, Any] | str:
        if ctx.challenge_js:
            return self.capture(ctx, ctx.challenge_js)
        if ctx.fingerprint is None:
            raise SensorError(
                "imperva builder needs a fingerprint for the interrogation",
                context={"site": ctx.site_url},
            )
        return {
            "solution": {
                "interrogation": self.interrogate(ctx),
                "version": _text(ctx.extra.get("version")) or DEFAULT_VERSION,
            },
            "old_token": self.old_token(ctx),
            "error": None,
            "performance": {"interrogation": _int(ctx.extra.get("interrogation_ms"), 0)},
        }

    def capture(self, ctx: BuildContext, script: str) -> str:
        """Program that runs `main.js` / the reese script and reports its output."""
        return wrap(
            script,
            engine="imperva",
            setup=setup_js(
                href=ctx.site_url,
                user_agent=_text(ctx.extra.get("ua")),
                cookies=dict(ctx.cookies),
                referrer=_text(ctx.extra.get("referrer")),
            ),
        )

    def old_token(self, ctx: BuildContext) -> str:
        """The client's previous `reese84`; the server rotates it on success."""
        for value in (ctx.cookies.get("reese84"), ctx.extra.get("old_token"), ctx.extra.get("reese84")):
            text = _text(value)
            if text:
                return text
        return ""

    def interrogate(self, ctx: BuildContext) -> dict[str, Any]:
        """Interrogation map in :data:`INTERROGATION_KEYS` order.

        Absent fingerprint fields are omitted; an explicit
        `ctx.extra["interrogation"]` wins over everything, which is how a
        site-specific key that is not in this table gets in.
        """
        values = {
            "platform": _fingerprint(ctx, "tags", "platform") or _text(ctx.extra.get("platform")),
            "language": _first_language(_fingerprint(ctx, "accept_language")),
            "timezone": _fingerprint(ctx, "timezone"),
            "screen": ctx.extra.get("screen"),
            "plugins": ctx.extra.get("plugins"),
            "webgl": _webgl(ctx),
            "canvas": _fingerprint(ctx, "canvas_hash"),
            "audio": _fingerprint(ctx, "audio_hash"),
            "battery": ctx.extra.get("battery"),
            "user_agent_data": ctx.extra.get("user_agent_data"),
            "device_memory": ctx.extra.get("device_memory"),
            "hardware_concurrency": ctx.extra.get("hardware_concurrency"),
            "touch_points": ctx.extra.get("touch_points"),
            "pixel_ratio": ctx.extra.get("pixel_ratio"),
            "mouse": _behavior(ctx, "mouse", "mouse_track", "mouse_events"),
            "keyboard": _behavior(ctx, "keyboard", "kbd", "keyboard_track"),
            "scroll": _behavior(ctx, "scroll", "scroll_track"),
            "features": ctx.extra.get("features"),
            "permissions": ctx.extra.get("permissions"),
        }
        interrogation: dict[str, Any] = {}
        for key, name in INTERROGATION_KEYS:
            value = values.get(name)
            if value not in (None, "", [], {}):
                interrogation[key] = value
        override = ctx.extra.get("interrogation")
        if isinstance(override, dict):
            interrogation.update(override)
        return interrogation


def _webgl(ctx: BuildContext) -> dict[str, str] | None:
    vendor = _fingerprint(ctx, "webgl_vendor")
    renderer = _fingerprint(ctx, "webgl_renderer")
    if not vendor and not renderer:
        return None
    return {"vendor": vendor or "", "renderer": renderer or ""}


def _fingerprint(ctx: BuildContext, name: str, *nested: str) -> str:
    profile = ctx.fingerprint
    value: Any = getattr(profile, name, None) if profile is not None else None
    for key in nested:
        value = value.get(key) if isinstance(value, dict) else None
    return _text(value)


def _behavior(ctx: BuildContext, *names: str) -> Any:
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


__all__ = ["DEFAULT_VERSION", "INTERROGATION_KEYS", "ImpervaBuilder"]
