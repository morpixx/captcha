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
  - The type's `SolutionKind` comes from `captcha.domain.catalog` — add a new type
    there, not in a frozenset here.
  - Unknown kind (new enum value not yet in the catalog) falls back to every
    non-null field, so a half-added type keeps working.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import structlog

from captcha.domain.catalog import SolutionKind, solution_kind
from captcha.domain.enums import CaptchaType

log = structlog.get_logger(__name__)

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

    token = result.get("token")
    kind = solution_kind(captcha_type)

    if kind is SolutionKind.TEXT:
        _put(sol, "text", result.get("text"))
    elif kind is SolutionKind.RECAPTCHA:
        _put(sol, "gRecaptchaResponse", token)
        _put(sol, "userAgent", _ua(sol, result))
    elif kind is SolutionKind.HCAPTCHA:
        _put(sol, "gRecaptchaResponse", token)
        _put(sol, "respKey", _first(sol, "respKey", "resp_key"))
        _put(sol, "userAgent", _ua(sol, result))
    elif kind is SolutionKind.GEETEST_V3:
        # challenge/validate identify the round, seccode is validate + "|jordan".
        _put(sol, "challenge", _first(sol, "challenge", "gt_challenge"))
        _put(sol, "validate", _first(sol, "validate"))
        seccode = _first(sol, "seccode")
        if seccode is None and sol.get("validate"):
            seccode = f"{sol['validate']}|jordan"
        _put(sol, "seccode", seccode)
    elif kind is SolutionKind.GEETEST_V4:
        for key in ("captcha_id", "lot_number", "pass_token", "gen_time", "captcha_output"):
            _put(sol, key, _first(sol, key))
    elif kind is SolutionKind.TURNSTILE:
        _put(sol, "token", token)
        _put(sol, "userAgent", _ua(sol, result))
    elif kind is SolutionKind.CLOUDFLARE:
        cookies = _cookies(sol)
        clearance = _first(sol, "cf_clearance") or cookies.get("cf_clearance") or token
        _put(sol, "cf_clearance", clearance)
        _put(sol, "token", clearance)
        _put(sol, "userAgent", _ua(sol, result))
        if cookies:
            _put(sol, "cookies", cookies)
    elif kind is SolutionKind.FRIENDLY:
        _put(sol, "data", {"token": token} if token else _first(sol, "data"))
    elif kind is SolutionKind.ALTCHA:
        data = _first(sol, "data")
        if not data and token:
            data = {"token": token}
            number = _first(sol, "number")
            if number is not None:
                data["number"] = number
        _put(sol, "data", data)
    elif kind is SolutionKind.COOKIE_ENGINE:
        _cookie_solution(sol, captcha_type, result, site_url=site_url)
    elif kind is SolutionKind.AWSWAF:
        _awswaf_solution(sol, result)
    elif kind is SolutionKind.DATA_NEST:
        _data_solution(sol, captcha_type, token, result)
    elif kind is SolutionKind.GRID:
        _put(sol, "objects", result.get("indices"))
    elif kind is SolutionKind.COORDS:
        _put(sol, "coordinates", result.get("coords"))
    elif kind is SolutionKind.BBOX:
        _put(sol, "bbox", list(result["bbox"]) if result.get("bbox") else None)
    elif kind is SolutionKind.ROTATE:
        _put(sol, "angle", result.get("angle"))
    elif kind is SolutionKind.SLIDER:
        _put(sol, "offset", result.get("offset"))
    elif kind is SolutionKind.HOPSCOTCH:
        # Custom type: the solver answers with one 1-based card number and the
        # foot point (x, y) of that card's figure in input-image pixels. Both
        # arrive in `extra` because `indices` is the 0-based multi-select grid
        # contract, and a client that reads the wrong base loses a round.
        _put(sol, "index", _first(sol, "index"))
        _put(sol, "coordinates", _first(sol, "coordinates") or result.get("coords"))
    elif kind is SolutionKind.TOKEN:
        _put(sol, "token", token)
        _put(sol, "userAgent", _ua(sol, result))
    else:
        # Type absent from the catalog (half-added enum value). Hand back every
        # non-null generic field so the client sees something rather than nothing.
        log.warning("solution.unmapped_type", type=getattr(captcha_type, "value", str(captcha_type)))
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
