"""BotDeflector sensor builder.

BotDeflector is Queue-it's challenge wall (botdeflector.eu). The invisible
step is a proof of work; the widget also fingerprints the browser with
FingerprintJS and sends that blob on every pow/icon call. This builder owns
the fingerprint half and the wire names around it; the PoW lives in pow.py
and the HTTP flow lives in the solver.

The flow token (botDeflectorJwtToken) is captured by the client from the
page: standalone sites carry a siteKey/accountId pair, Queue-it softblocks
carry the same JWT in the HTML. Either way the token says which steps run,
so the builder does not decide the flow.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder

#: Standalone deployment. Queue-it serves the same API on its own host.
DEFAULT_DOMAIN: str = "https://botdeflector.eu"
QUEUE_IT_HOST: str = "https://ipv4.challenge.queue-it.net"

#: Invisible proof of work. Each challengeFlow step hits both.
POW_GET_PATH: str = "/pow/get"
POW_VERIFY_PATH: str = "/pow/verify"

#: Finalises a multi-step flow with the comma-joined step tokens.
VERIFY_FLOW_PATH: str = "/verify-flow"

#: Flow token: request header on pow/icon, sibling header on verify-flow.
HEADER_FLOW_TOKEN: str = "x-tt-flow-token"
HEADER_CHALLENGE_FLOW_TOKEN: str = "x-tt-ch-flow-token"
HEADER_SOLUTION_TOKENS: str = "x-tt-solution-tokens"
HEADER_FINGERPRINT: str = "x-tt-client-fingerprint"

#: The signed result a standalone site spends as a query parameter.
RESULT_QUERY_PARAM: str = "tt_solutions"

#: The flow token is valid for 120 s (600 s on botdeflector.eu); the token
#: itself carries exp, this is only the default used when it does not.
DEFAULT_FLOW_TTL_S: int = 120


@register_builder(AntibotEngine.BOTDEFLECTOR)
class BotDeflectorBuilder(SensorBuilder):
    name = "botdeflector"
    requires_challenge_js = False
    uses_js_runtime = False

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        fingerprint = build_fingerprint(ctx)
        blob = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
        return {
            "fingerprint": fingerprint,
            "headers": {HEADER_FINGERPRINT: blob},
        }


def build_fingerprint(ctx: BuildContext) -> dict[str, Any]:
    """Coherent FingerprintJS-shaped blob from the profile plus payload overrides.

    A profile is the normal source; the client may pass its own capture under
    extra["fingerprint"] and win every key. Missing keys fall back to the same
    deterministic values a desktop Chrome exposes, so replaying one task twice
    produces the same header.
    """
    provided = ctx.extra.get("fingerprint")
    source = ctx.fingerprint
    user_agent = str(_get(source, "user_agent", "userAgent") or ctx.extra.get("ua") or "")
    if isinstance(provided, dict):
        user_agent = str(provided.get("userAgent") or provided.get("user_agent") or user_agent)
    if not user_agent:
        raise SensorError(
            "botdeflector needs a browser fingerprint or at least a user agent",
            context={"site": ctx.site_url},
        )

    tags = _get(source, "tags") or {}
    language = str(_get(source, "accept_language", "language") or "en-US")
    languages = _languages(language)
    timezone = str(_get(source, "timezone") or "UTC")
    fingerprint: dict[str, Any] = {
        "userAgent": user_agent,
        "language": languages[0],
        "languages": languages,
        "platform": _platform(tags),
        "timezone": timezone,
        "timezoneOffset": _timezone_offset(timezone),
        "screen": _screen(provided),
        "canvas": str(_get(source, "canvas_hash", "canvas") or ""),
        "audio": str(_get(source, "audio_hash", "audio") or ""),
        "webgl": {
            "vendor": str(_get(source, "webgl_vendor", "webglVendor") or ""),
            "renderer": str(_get(source, "webgl_renderer", "webglRenderer") or ""),
        },
        "fonts": list(_get(source, "fonts") or []),
        "plugins": [],
        "hardwareConcurrency": 8,
        "deviceMemory": 8,
        "touchPoints": 0,
        "cookieEnabled": True,
        "doNotTrack": None,
    }
    if isinstance(provided, dict):
        fingerprint.update({str(k): v for k, v in provided.items() if v is not None})
        if "webgl" in provided and isinstance(provided["webgl"], dict):
            fingerprint["webgl"] = {**fingerprint.get("webgl", {}), **provided["webgl"]}
    return fingerprint


def _get(source: Any, *names: str) -> Any:
    if source is None:
        return None
    for name in names:
        value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
        if value not in (None, "", []):
            return value
    return None


def _languages(header: str) -> list[str]:
    out = []
    for part in header.split(","):
        tag = part.split(";")[0].strip()
        if tag and tag not in out:
            out.append(tag)
    return out or ["en-US"]


def _platform(tags: Any) -> str:
    os_name = str((tags or {}).get("os", "")).lower() if isinstance(tags, dict) else ""
    if os_name.startswith("win"):
        return "Win32"
    if os_name.startswith("mac"):
        return "MacIntel"
    if os_name.startswith("linux"):
        return "Linux x86_64"
    return "Win32"


def _screen(provided: Any) -> dict[str, int]:
    """Desktop default; a client capture owns the real numbers."""
    if isinstance(provided, dict) and isinstance(provided.get("screen"), dict):
        screen = provided["screen"]
        return {str(k): int(v) for k, v in screen.items() if isinstance(v, (int, float))}
    return {"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040, "colorDepth": 24, "pixelDepth": 24}


def _timezone_offset(timezone: str) -> int:
    """Minutes east of UTC, the sign JavaScript's getTimezoneOffset() flips."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return 0
    offset = datetime.now(zone).utcoffset()
    return int(offset.total_seconds() // 60) if offset else 0
