"""Projection of a solver result onto the 2captcha-compatible solution shape.

A 2captcha client never reads "whatever keys you felt like returning": it reads one
specific key per captcha type. reCAPTCHA lives in `gRecaptchaResponse`, Turnstile and
FunCaptcha in `token`, GeeTest v3 in `challenge`/`validate`/`seccode`, the antibot
engines in cookie maps. Emit the wrong key and the client reports "token not found"
on a task that actually succeeded — the worst kind of bug, because it looks like a
solver failure and gets paid for twice.

Shapes below come from the two reference clients of this protocol: the
`@zennolab_com/capmonstercloud-client` response typings and CapSolver's per-task docs.
Both mirror 2captcha's JSON API. Where a family has more than one shape in the wild
(cookie engines: flat `cookies` vs `domains` map), we emit both — an extra key costs
nothing, a missing key breaks the integration.

Rules:
  - `SolverResult.extra` always wins. A solver that knows its engine can inject
    vendor-exact fields; this module only fills in canonical defaults.
  - Abstract geometry types (slider/rotate/click/grid/bbox/draw/drag) have no single
    2captcha shape — every engine invents its own. Those are our own stable keys,
    documented in README.
  - Unknown type falls back to every non-null field we have, so a new CaptchaType
    keeps working before its projection is written.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import structlog

from captcha_common.enums import CaptchaType

log = structlog.get_logger(__name__)

# ---- families ---------------------------------------------------------------

# Text-like answers: {"text": "abc"}
_TEXT_TYPES: frozenset[CaptchaType] = frozenset(
    {
        CaptchaType.TEXT,
        CaptchaType.MATH_IMAGE,
        CaptchaType.IMAGE_TO_TEXT,
        CaptchaType.AUDIO,
    }
)

# Google: {"gRecaptchaResponse": "..."} (+ userAgent/cookies when the engine returns them)
_RECAPTCHA_TYPES: frozenset[CaptchaType] = frozenset(
    {
        CaptchaType.RECAPTCHA_V2,
        CaptchaType.RECAPTCHA_V2_ENTERPRISE,
        CaptchaType.RECAPTCHA_V2_INVISIBLE,
        CaptchaType.RECAPTCHA_V2_CALLBACK,
        CaptchaType.RECAPTCHA_V3,
        CaptchaType.RECAPTCHA_V3_ENTERPRISE,
    }
)

# hCaptcha reuses the Google key and adds respKey + userAgent.
_HCAPTCHA_TYPES: frozenset[CaptchaType] = frozenset(
    {CaptchaType.HCAPTCHA, CaptchaType.HCAPTCHA_ENTERPRISE}
)

# Turnstile: {"token", "userAgent"}
_TURNSTILE_TYPES: frozenset[CaptchaType] = frozenset(
    {CaptchaType.TURNSTILE, CaptchaType.TURNSTILE_INVISIBLE}
)

# Plain token with an optional UA: Arkose, MTCaptcha, Prosopo, Yidun, Binance, VK,
# Yandex SmartCaptcha, CaptchaFox, Cutcaptcha, KeyCaptcha, Capy, Lemin, atbCAPTCHA,
# Twitch integrity, BotGuard, BotDeflector, CyberSiARA.
_TOKEN_TYPES: frozenset[CaptchaType] = frozenset(
    {
        CaptchaType.FUNCAPTCHA,
        CaptchaType.FUNCAPTCHA_BDA,
        CaptchaType.FUNCAPTCHA_ENTERPRISE,
        CaptchaType.MTCAPTCHA,
        CaptchaType.PROSOPO,
        CaptchaType.YIDUN,
        CaptchaType.BINANCE,
        CaptchaType.VK,
        CaptchaType.YANDEX,
        CaptchaType.CAPTCHAFOX,
        CaptchaType.CUTCAPTCHA,
        CaptchaType.KEYCAPTCHA,
        CaptchaType.CAPY,
        CaptchaType.LEMIN,
        CaptchaType.ATBCAPTCHA,
        CaptchaType.TWITCH_INTEGRITY,
        CaptchaType.BOTGUARD,
        CaptchaType.BOTDEFLECTOR,
        CaptchaType.CYBERSIARA,
    }
)

# Engines that hand back a cookie jar instead of a token.
_COOKIE_TYPES: frozenset[CaptchaType] = frozenset(
    {
        CaptchaType.DATADOME_FULL,
        CaptchaType.DATADOME_SLIDER,
        CaptchaType.AKAMAI_SENSOR,
        CaptchaType.AKAMAI_SBSD,
        CaptchaType.KASADA,
        CaptchaType.PERIMETERX,
        CaptchaType.IMPERVA,
        CaptchaType.TSPD,
        CaptchaType.TEMU,
    }
)

# Engines whose answer is nested under `data` (and sometimes a headers block):
# Hunt, Castle, Basilisk, Alibaba, Tencent TenDI.
_DATA_TYPES: frozenset[CaptchaType] = frozenset(
    {
        CaptchaType.HUNT,
        CaptchaType.CASTLE,
        CaptchaType.BASILISK,
        CaptchaType.ALIBABA,
        CaptchaType.TENCENT,
    }
)

_GEETEST_V3: frozenset[CaptchaType] = frozenset({CaptchaType.GEETEST_V3})
_GEETEST_V4: frozenset[CaptchaType] = frozenset({CaptchaType.GEETEST_V4})

# Abstract geometry — our own keys, no 2captcha standard exists.
_GRID_TYPES: frozenset[CaptchaType] = frozenset({CaptchaType.CLASSIFICATION_GRID})
_COORD_TYPES: frozenset[CaptchaType] = frozenset(
    {
        CaptchaType.CLICK,
        CaptchaType.COORDINATES,
        CaptchaType.DRAW_AROUND,
        CaptchaType.DRAG_DROP,
        # Shein is a "click the right tiles" task; the answer is click points.
        CaptchaType.SHEIN,
    }
)

_GENERIC_KEYS = ("token", "text", "coords", "offset", "angle", "bbox", "indices")


# ---- public -----------------------------------------------------------------


def build_solution(
    captcha_type: CaptchaType,
    result: dict[str, Any] | None,
    *,
    site_url: str | None = None,
) -> dict[str, Any] | None:
    """Turn a builtins-encoded `SolverResult` into the client-facing solution dict.

    Returns None when the result carries nothing to hand back (caller should treat
    that as a solver bug, not a successful solve).
    """
    if not result:
        return None

    sol: dict[str, Any] = {}
    extra = result.get("extra")
    if isinstance(extra, dict):
        # Vendor-exact fields from the solver land first and are never overwritten.
        sol.update({k: v for k, v in extra.items() if v is not None})

    ct = captcha_type
    token = result.get("token")

    if ct in _TEXT_TYPES:
        _put(sol, "text", result.get("text"))
    elif ct in _RECAPTCHA_TYPES:
        _put(sol, "gRecaptchaResponse", token)
        _put(sol, "userAgent", _ua(sol, result))
    elif ct in _HCAPTCHA_TYPES:
        _put(sol, "gRecaptchaResponse", token)
        _put(sol, "respKey", _first(sol, "respKey", "resp_key"))
        _put(sol, "userAgent", _ua(sol, result))
    elif ct in _GEETEST_V3:
        # challenge/validate identify the round, seccode is validate + "|jordan".
        _put(sol, "challenge", _first(sol, "challenge", "gt_challenge"))
        _put(sol, "validate", _first(sol, "validate"))
        seccode = _first(sol, "seccode")
        if seccode is None and sol.get("validate"):
            seccode = f"{sol['validate']}|jordan"
        _put(sol, "seccode", seccode)
    elif ct in _GEETEST_V4:
        for key in ("captcha_id", "lot_number", "pass_token", "gen_time", "captcha_output"):
            _put(sol, key, _first(sol, key))
    elif ct in _TURNSTILE_TYPES:
        _put(sol, "token", token)
        _put(sol, "userAgent", _ua(sol, result))
    elif ct is CaptchaType.CF_CHALLENGE:
        cookies = _cookies(sol)
        clearance = _first(sol, "cf_clearance") or cookies.get("cf_clearance") or token
        _put(sol, "cf_clearance", clearance)
        _put(sol, "token", clearance)
        _put(sol, "userAgent", _ua(sol, result))
        if cookies:
            _put(sol, "cookies", cookies)
    elif ct is CaptchaType.FRIENDLY:
        _put(sol, "data", {"token": token} if token else _first(sol, "data"))
    elif ct is CaptchaType.ALTCHA:
        data = _first(sol, "data")
        if not data and token:
            data = {"token": token}
            number = _first(sol, "number")
            if number is not None:
                data["number"] = number
        _put(sol, "data", data)
    elif ct in _COOKIE_TYPES:
        _cookie_solution(sol, ct, result, site_url=site_url)
    elif ct is CaptchaType.AWSWAF:
        _awswaf_solution(sol, result)
    elif ct in _DATA_TYPES:
        _data_solution(sol, ct, token, result)
    elif ct in _GRID_TYPES:
        _put(sol, "objects", result.get("indices"))
    elif ct in _COORD_TYPES:
        _put(sol, "coordinates", result.get("coords"))
    elif ct is CaptchaType.BOUNDING_BOX:
        _put(sol, "bbox", list(result["bbox"]) if result.get("bbox") else None)
    elif ct is CaptchaType.ROTATE:
        _put(sol, "angle", result.get("angle"))
    elif ct is CaptchaType.SLIDER:
        _put(sol, "offset", result.get("offset"))
    elif ct in _TOKEN_TYPES:
        _put(sol, "token", token)
        _put(sol, "userAgent", _ua(sol, result))
    else:
        log.warning("solution.unmapped_type", type=getattr(ct, "value", str(ct)))
        _generic(sol, result)

    # Bookkeeping knobs a solver passes in `extra` are not part of the answer.
    for control_key in ("domain",):
        sol.pop(control_key, None)

    cleaned = {k: v for k, v in sol.items() if v is not None and v != {} and v != []}
    if not cleaned:
        # Nothing projected — hand back whatever the solver produced rather than an
        # empty solution the client will choke on.
        _generic(cleaned, result)
        cleaned = {k: v for k, v in cleaned.items() if v is not None and v != {} and v != []}
    return cleaned or None


# ---- helpers ----------------------------------------------------------------


def _cookie_solution(
    sol: dict[str, Any],
    ct: CaptchaType,
    result: dict[str, Any],
    *,
    site_url: str | None,
) -> None:
    """Antibot engines answer with cookies, keyed by domain.

    Two shapes are in circulation: a flat `cookies` map (CapSolver) and a
    `domains -> host -> cookies` map (CapMonster/2captcha). We emit both.
    """
    cookies = _cookies(sol)
    if cookies:
        _put(sol, "cookies", dict(cookies))

    domain = _first(sol, "domain") or _host(site_url)
    if cookies and domain:
        _put(sol, "domains", {domain: {"cookies": dict(cookies)}})
        if ct is CaptchaType.TSPD:
            # Reference client types this one with a capital D. Harmless duplicate.
            _put(sol, "Domains", {domain: {"Cookies": dict(cookies)}})
    if ct is CaptchaType.IMPERVA:
        # CapSolver returns the ___utmvc blob directly; keep it addressable.
        _put(sol, "utmvc", cookies.get("___utmvc") or _first(sol, "utmvc"))
    _put(sol, "userAgent", _ua(sol, result))


def _awswaf_solution(sol: dict[str, Any], result: dict[str, Any]) -> None:
    """AWS WAF has two documented answers.

    Token mode: `captcha_voucher` + `existing_token`. Cookie mode: a cookie jar with
    `aws-waf-token`. Emit whichever the solver produced, and never invent a voucher
    out of a plain token — a wrong voucher is worse than an obvious gap.
    """
    voucher = _first(sol, "captcha_voucher")
    existing = _first(sol, "existing_token")
    if voucher or existing:
        _put(sol, "captcha_voucher", voucher)
        _put(sol, "existing_token", existing)
        _put(sol, "userAgent", _ua(sol, result))
        return
    waf = _cookies(sol).get("aws-waf-token") or _first(sol, "aws-waf-token") or result.get("token")
    if waf:
        _put(sol, "cookies", {"aws-waf-token": str(waf)})
        _put(sol, "userAgent", _ua(sol, result))


def _data_solution(
    sol: dict[str, Any],
    ct: CaptchaType,
    token: str | None,
    result: dict[str, Any],
) -> None:
    """Nested-`data` answers (Hunt, Castle, Basilisk, Alibaba, Tencent TenDI).

    The nesting is part of the contract for these types — a client reads
    `solution["data"]["token"]`, so flattening would break exactly the same way the
    flat-token bug did.
    """
    if ct is CaptchaType.HUNT:
        _put(sol, "data", _first(sol, "data") or ({"token": token} if token else None))
    elif ct is CaptchaType.CASTLE:
        tokens = _first(sol, "tokens")
        if tokens is None and token:
            tokens = [token]
        data = _first(sol, "data") or ({"tokens": tokens} if tokens else None)
        _put(sol, "data", data)
    elif ct is CaptchaType.BASILISK:
        response = _first(sol, "captcha_response") or token
        _put(sol, "data", _first(sol, "data") or ({"captcha_response": response} if response else None))
        _put(sol, "headers", _headers(sol, result))
    elif ct is CaptchaType.ALIBABA:
        tokens = _first(sol, "tokens") or token
        _put(sol, "data", _first(sol, "data") or ({"tokens": tokens} if tokens else None))
    elif ct is CaptchaType.TENCENT:
        data = _first(sol, "data")
        if not data:
            randstr = _first(sol, "randstr")
            ticket = _first(sol, "ticket")
            if randstr or ticket:
                data = {"randstr": randstr, "ticket": ticket}
        _put(sol, "data", data)
        _put(sol, "headers", _headers(sol, result))


def _headers(sol: dict[str, Any], result: dict[str, Any] | None) -> dict[str, str] | None:
    raw = sol.get("headers")
    if isinstance(raw, dict) and raw:
        return {str(k): str(v) for k, v in raw.items()}
    ua = _ua(sol, result)
    return {"User-Agent": ua} if ua else None


def _generic(sol: dict[str, Any], result: dict[str, Any]) -> None:
    for key in _GENERIC_KEYS:
        _put(sol, key, result.get(key))


def _cookies(sol: dict[str, Any]) -> dict[str, str]:
    raw = sol.get("cookies")
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    return {}


def _ua(sol: dict[str, Any], result: dict[str, Any] | None) -> str | None:
    ua = _first(sol, "userAgent", "user_agent", "user-agent")
    if ua is None and result:
        ua = result.get("user_agent")
    return ua


def _first(sol: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = sol.get(key)
        if value not in (None, "", {}, []):
            return value
    return None


def _put(sol: dict[str, Any], key: str, value: Any) -> None:
    """Set only when absent — solver-provided values and earlier keys win."""
    if value is None or value == "" or value == [] or value == {}:
        return
    sol.setdefault(key, value)


def _host(site_url: str | None) -> str | None:
    if not site_url:
        return None
    try:
        parsed = urlparse(site_url if "//" in site_url else "https://" + site_url)
    except ValueError:
        return None
    return parsed.hostname or None


def host_for_payload(payload: dict[str, Any] | None) -> str | None:
    """Best-effort site URL out of a stored task payload (DB fallback path)."""
    if not payload:
        return None
    for key in ("site_url", "websiteURL", "websiteUrl", "pageurl", "captcha_url"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None
