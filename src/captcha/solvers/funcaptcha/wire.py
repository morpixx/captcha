"""Arkose token wire: open a session, fetch the challenge, submit the answer.

Three endpoints, in the order the widget calls them:

    POST {surl}/fc/gt2/public_key/{pk}   -> {token, challenge_url?, game_type?, ...}
    GET  {surl}/fc/gfct/                 -> {challengeID, _challenge_imgs, instruction}
    POST {surl}/fc/ca/                   -> {solved, token?, _challenge_imgs?}

Shapes are from public RE (2026-07): form fields in this order, the
ark-build-id / x-ark-esync-value headers, the token as a '|'-joined blob whose
segments the challenge call re-sends, and guess/bio as opaque server-bound
values. What we do not fabricate:

  * guess — JSON of the answer we actually produced (indices / angle). Where a
            deployment demands the ciphertext form, pass ctx.extra
            ['arkose_guess_codec'] with .encode(answer, challenge) -> str;
            without it the plain JSON goes out and the server says so.
  * bio   — a client interaction trace, not derivable from an image. Only sent
            when the caller forwards a captured one in payload['bio'].

FILL: segment semantics of the token (which position is sid vs region) come from
public dumps; the split below is positional-best-effort and tolerant of extra
segments.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any

from captcha.domain import Task
from captcha.domain.errors import SensorError
from captcha.solvers import SolverContext

from .settings import ArkoseSettings, normalize_surl

ESYNC_QUANTUM = 21600
GT2_PATH = "/fc/gt2/public_key/{pk}"
GFCT_PATH = "/fc/gfct/"
CA_PATH = "/fc/ca/"
#: Percent-encoding set the widget's own encoder produces; field order is part
#: of the request shape, so we encode by hand instead of letting a lib sort.
_SAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~/()"


@dataclass(frozen=True, slots=True)
class TokenParts:
    token: str
    sid: str
    region: str
    segments: tuple[str, ...]
    suppressed: bool


def esync_value(now: float | None = None) -> str:
    """x-ark-esync-value: now quantised to a six-hour bucket."""
    return str(int(now if now is not None else time.time()) // ESYNC_QUANTUM * ESYNC_QUANTUM)


def split_token(token: str) -> TokenParts:
    segments = tuple(part for part in (token or "").split("|") if part)
    if not segments:
        raise SensorError("arkose token is empty")
    pairs: dict[str, str] = {}
    plain: list[str] = []
    for segment in segments:
        if "=" in segment:
            key, _, value = segment.partition("=")
            pairs[key] = value
        else:
            plain.append(segment)
    region = pairs.get("r", "")
    # sid is the session's region-ish id. An explicit sid= segment wins; failing
    # that we send the r= region rather than some positional guess (FILL: which
    # position, if any, carries sid on a build without the explicit pair).
    sid = pairs.get("sid") or region
    return TokenParts(
        token=plain[0] if plain else segments[0],
        sid=sid,
        region=region,
        segments=segments,
        suppressed=pairs.get("sup") == "1",
    )


def form_encode(pairs: list[tuple[str, str]]) -> str:
    """Ordered application/x-www-form-urlencoded body, widget-compatible quoting."""
    return "&".join(f"{_quote(key)}={_quote(value)}" for key, value in pairs)


def _quote(value: str) -> str:
    out: list[str] = []
    for byte in str(value).encode("utf-8"):
        char = chr(byte)
        out.append(char if char in _SAFE else f"%{byte:02X}")
    return "".join(out)


async def open_session(
    ctx: SolverContext,
    cfg: ArkoseSettings,
    task: Task,
    *,
    bda_value: str,
    bda_field: str,
    user_agent: str = "",
    blob: str = "",
) -> dict[str, Any]:
    """POST the BDA and get the session token back."""
    payload = task.payload or {}
    site = task.site_url or str(payload.get("websiteURL") or payload.get("pageurl") or "") or cfg.surl
    pairs: list[tuple[str, str]] = [
        (bda_field, bda_value),
        ("public_key", cfg.public_key),
        ("site", site),
        ("userbrowser", user_agent),
        ("capi_version", cfg.capi_version),
        ("capi_mode", str(payload.get("capi_mode") or "lightbox")),
        ("style_theme", str(payload.get("style_theme") or "default")),
        ("rnd", repr(random.random())),
        ("language", task.language or "en-US"),
    ]
    if blob:
        pairs.append(("data[blob]", blob))
    response = await ctx.http.post(
        cfg.surl + GT2_PATH.format(pk=cfg.public_key),
        data=form_encode(pairs),
        headers=_headers(task, cfg, user_agent=user_agent),
    )
    _require_ok(response, what="gt2/public_key")
    return _json_dict(response, what="gt2/public_key")


async def fetch_challenge(
    ctx: SolverContext,
    cfg: ArkoseSettings,
    task: Task,
    token: str,
    *,
    user_agent: str = "",
) -> dict[str, Any]:
    """GET the game payload for a token the endpoint did not suppress."""
    parts = split_token(token)
    params: dict[str, str] = {
        "token": parts.token,
        "sid": parts.sid,
        "render_type": "canvas",
        "analytics_iter": "40",
    }
    if parts.region:
        params["r"] = parts.region
    response = await ctx.http.get(
        cfg.surl + GFCT_PATH,
        params=params,
        headers=_headers(task, cfg, user_agent=user_agent),
    )
    _require_ok(response, what="fc/gfct")
    return _json_dict(response, what="fc/gfct")


async def submit_answer(
    ctx: SolverContext,
    cfg: ArkoseSettings,
    task: Task,
    *,
    session_token: str,
    game_token: str,
    guess: str,
    bio: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    """POST one round's answer; the reply either solves the session or re-challenges."""
    payload = task.payload or {}
    pairs: list[tuple[str, str]] = [
        ("session_token", session_token),
        ("game_token", game_token),
        ("guess", guess),
    ]
    if bio:
        pairs.append(("bio", bio))
    pairs.extend(_extra_form(payload))

    headers = _headers(task, cfg, user_agent=user_agent)
    headers["x-requested-id"] = str(payload.get("x_requested_id") or _requested_id())
    headers["x-newrelic-timestamp"] = str(int(time.time() * 1000))
    response = await ctx.http.post(cfg.surl + CA_PATH, data=form_encode(pairs), headers=headers)
    _require_ok(response, what="fc/ca")
    return _json_dict(response, what="fc/ca")


def encode_guess(answer: list[Any], *, challenge: dict[str, Any] | None = None, ctx: SolverContext | None = None) -> str:
    """Answer -> the guess field. A pluggable codec wins; else compact JSON."""
    extra = ctx.extra if ctx is not None and isinstance(ctx.extra, dict) else {}
    codec = extra.get("arkose_guess_codec")
    if codec is not None:
        encoded = codec.encode(answer, challenge or {})
        if not isinstance(encoded, str) or not encoded:
            raise SensorError("arkose guess codec returned no string")
        return encoded
    return json.dumps(answer, separators=(",", ":"))


def solved(reply: dict[str, Any]) -> bool:
    value = reply.get("solved")
    if isinstance(value, bool):
        return value
    return str(reply.get("answered") or "").lower() in ("true", "1", "answered")


def next_round_images(reply: dict[str, Any]) -> list[str]:
    """A re-challenge carries the next image set; a finished one carries none."""
    images: list[str] = []
    for key in ("_challenge_imgs", "challenge_imgs", "_challenge_imgs_2"):
        value = reply.get(key)
        if isinstance(value, list):
            images.extend(item for item in value if isinstance(item, str) and item)
    return images


def blob_from(payload: dict[str, Any]) -> str:
    for key in ("data[blob]", "blob", "data_blob"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("blob"), str):
        return data["blob"]
    return ""


def _extra_form(payload: dict[str, Any]) -> list[tuple[str, str]]:
    extra = payload.get("arkose_extra_form")
    if not isinstance(extra, dict):
        return []
    return [(str(key), str(value)) for key, value in extra.items()]


def _requested_id() -> str:
    """x-requested-id is a per-answer opaque id; derive one without pretending."""
    return random.getrandbits(64).to_bytes(8, "big", signed=False).hex()


def _headers(task: Task, cfg: ArkoseSettings, *, user_agent: str = "") -> dict[str, str]:
    origin = normalize_surl(task.site_url or cfg.surl)
    referer = (task.site_url or origin).rstrip("/") + "/"
    headers = {
        "accept": "*/*",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": origin,
        "referer": referer,
        "x-ark-esync-value": esync_value(),
    }
    if user_agent:
        headers["user-agent"] = user_agent
    if cfg.build_id:
        headers["ark-build-id"] = cfg.build_id
    return headers


def _require_ok(response: Any, *, what: str) -> None:
    if response.status != 200:
        raise SensorError(
            f"arkose {what} non-200",
            context={"status": response.status, "what": what, "head": response.text[:120]},
        )


def _json_dict(response: Any, *, what: str) -> dict[str, Any]:
    try:
        parsed = response.json()
    except Exception as exc:
        raise SensorError(
            f"arkose {what} body is not JSON",
            context={"what": what, "head": response.text[:120]},
        ) from exc
    if isinstance(parsed, dict) and isinstance(parsed.get("body"), str):
        try:
            parsed = json.loads(parsed["body"])
        except ValueError as exc:
            raise SensorError(
                f"arkose {what} wrapped body is not JSON",
                context={"what": what, "head": parsed["body"][:120]},
            ) from exc
    if not isinstance(parsed, dict):
        raise SensorError(f"arkose {what} body is not an object", context={"got": type(parsed).__name__})
    return parsed
