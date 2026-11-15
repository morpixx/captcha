"""Legacy 2captcha form protocol: request parsing and answer formatting.

Pure translation between /in.php + /res.php and the internal task model: legacy
form params -> InRequest, solution dict -> legacy text answer. No I/O — the
router in routes/legacy.py owns FastAPI/DB/redis. Field names follow 2captcha's
docs: googlekey (reCAPTCHA), sitekey (hCaptcha/Turnstile/Friendly), publickey +
surl (FunCaptcha), gt + challenge + api_server (GeeTest), textcaptcha, body.
data is the Amazon WAF blob (sitekey/iv/context as JSON); flat fields also work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import partial
from typing import Any

from captcha.domain.catalog import SolutionKind, solution_kind
from captcha.domain.enums import CaptchaType

# The only two protocol errors this layer raises. Auth, balance and type errors
# already have public envelopes by the time the router calls in here.
ERROR_NO_SUCH_METHOD = "ERROR_NO_SUCH_METHOD"
ERROR_BAD_PARAMETERS = "ERROR_BAD_PARAMETERS"


class LegacyError(Exception):
    """A legacy-protocol failure carrying the exact 2captcha error code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


@dataclass(frozen=True, slots=True)
class InRequest:
    """A parsed /in.php request, ready to be turned into a domain Task."""

    captcha_type: CaptchaType
    payload: dict[str, Any]
    sitekey: str | None
    site_url: str | None
    user_agent: str | None


def _get(params: dict[str, str], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    return str(value).strip() or None


def _require(params: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = _get(params, key)
        if value is not None:
            return value
    raise LegacyError(ERROR_BAD_PARAMETERS, f"missing required field: {keys[0]}")


def _flag(params: dict[str, str], key: str) -> bool:
    return (_get(params, key) or "").lower() in {"1", "true", "yes", "on"}


def _put(payload: dict[str, Any], params: dict[str, str], *keys: str) -> None:
    for key in keys:
        value = _get(params, key)
        if value is not None:
            payload[key] = value


def _int_param(params: dict[str, str], *keys: str) -> int | None:
    for key in keys:
        raw = _get(params, key)
        if raw is None:
            continue
        try:
            return int(raw)
        except ValueError:
            raise LegacyError(ERROR_BAD_PARAMETERS, f"{key} must be an integer") from None
    return None


def _json_object(raw: str, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        raise LegacyError(ERROR_BAD_PARAMETERS, f"{field} is not valid JSON") from None
    if not isinstance(parsed, dict):
        raise LegacyError(ERROR_BAD_PARAMETERS, f"{field} must be a JSON object")
    return parsed


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value).strip() or None


def _common(params: dict[str, str]) -> dict[str, Any]:
    """Fields every method carries when present: page, UA, proxy, language."""
    payload: dict[str, Any] = {}
    for source, target in (
        ("pageurl", "pageurl"),
        ("userAgent", "user_agent"),
        ("proxy", "proxy"),
        ("proxytypeaddr", "proxy_type"),
        ("lang", "lang"),
        ("soft_id", "soft_id"),
    ):
        value = _get(params, source)
        if value is not None:
            payload[target] = value
    return payload


def _finish(
    params: dict[str, str],
    captcha_type: CaptchaType,
    payload: dict[str, Any],
    *,
    sitekey: str | None = None,
    site_url: str | None = None,
) -> InRequest:
    page = site_url or _get(params, "pageurl")
    payload.setdefault("pageurl", page)
    payload.setdefault("sitekey", sitekey)
    return InRequest(
        captcha_type=captcha_type,
        payload={key: value for key, value in payload.items() if value is not None},
        sitekey=sitekey,
        site_url=page,
        user_agent=_get(params, "userAgent"),
    )


def _body_method(
    params: dict[str, str],
    captcha_type: CaptchaType,
    *,
    extras: tuple[str, ...] = (),
    instruction: bool = False,
) -> InRequest:
    """Shared shape for image/audio methods: base64 body + optional extras."""
    payload = _common(params)
    payload["body"] = _require(params, "body")
    _put(payload, params, *extras)
    if instruction:
        text = _get(params, "textinstructions")
        if text is not None:
            payload["comment"] = text
            payload["target"] = text
    return _finish(params, captcha_type, payload)


def _sitekey_method(params: dict[str, str], captcha_type: CaptchaType, *, extras: tuple[str, ...] = ()) -> InRequest:
    """Shared shape for sitekey + pageurl methods."""
    sitekey = _require(params, "sitekey")
    pageurl = _require(params, "pageurl")
    payload = _common(params)
    payload["sitekey"] = sitekey
    _put(payload, params, *extras)
    return _finish(params, captcha_type, payload, sitekey=sitekey, site_url=pageurl)


def _userrecaptcha(params: dict[str, str]) -> InRequest:
    googlekey = _require(params, "googlekey")
    pageurl = _require(params, "pageurl")
    enterprise = _flag(params, "enterprise")
    invisible = _flag(params, "invisible")
    # Enterprise wins when both flags are set: CaptchaType has no
    # enterprise+invisible member, and enterprise is the stricter contract.
    if enterprise:
        captcha_type = CaptchaType.RECAPTCHA_V2_ENTERPRISE
    else:
        captcha_type = CaptchaType.RECAPTCHA_V2_INVISIBLE if invisible else CaptchaType.RECAPTCHA_V2

    payload = _common(params)
    payload["googlekey"] = googlekey
    if enterprise:
        payload["enterprise"] = True
    if invisible:
        payload["invisible"] = True
    _put(payload, params, "action", "cdata")
    return _finish(params, captcha_type, payload, sitekey=googlekey, site_url=pageurl)


def _funcaptcha(params: dict[str, str]) -> InRequest:
    publickey = _require(params, "publickey")
    pageurl = _require(params, "pageurl")
    payload = _common(params)
    payload["publickey"] = publickey
    payload["public_key"] = publickey  # solver-side name; keep both for old payloads
    _put(payload, params, "surl")
    if "surl" in payload:
        payload["subdomain"] = payload["surl"]
    return _finish(params, CaptchaType.FUNCAPTCHA, payload, sitekey=publickey, site_url=pageurl)


def _geetest(params: dict[str, str]) -> InRequest:
    gt = _require(params, "gt")
    challenge = _require(params, "challenge")
    pageurl = _require(params, "pageurl")
    payload = _common(params)
    payload["gt"] = gt
    payload["challenge"] = challenge
    _put(payload, params, "api_server")
    return _finish(params, CaptchaType.GEETEST_V3, payload, sitekey=gt, site_url=pageurl)


def _textcaptcha(params: dict[str, str]) -> InRequest:
    question = _require(params, "textcaptcha")
    payload = _common(params)
    payload.update(textcaptcha=question, question=question, comment=question)
    return _finish(params, CaptchaType.TEXT, payload)


def _grid(params: dict[str, str]) -> InRequest:
    payload = _common(params)
    payload["body"] = _require(params, "body")
    prompt = _get(params, "textinstructions")
    if prompt is not None:
        payload["prompt"] = prompt
        payload["question"] = prompt
    for target, names in (("rows", ("rows", "recaptcharows")), ("cols", ("cols", "recaptchacols"))):
        value = _int_param(params, *names)
        if value is not None:
            payload[target] = value
    return _finish(params, CaptchaType.CLASSIFICATION_GRID, payload)


def _amazon_waf(params: dict[str, str]) -> InRequest:
    pageurl = _require(params, "pageurl")
    raw_data = _get(params, "data")
    data = _json_object(raw_data, "data") if raw_data is not None else {}
    sitekey = _get(params, "sitekey") or _as_text(data.get("sitekey"))
    if sitekey is None:
        raise LegacyError(ERROR_BAD_PARAMETERS, "amazon_waf: sitekey is required")

    payload = _common(params)
    payload["sitekey"] = sitekey
    for key in ("iv", "context"):
        value = _get(params, key) or _as_text(data.get(key))
        if value is not None:
            payload[key] = value
    # Keep the original blob: the worker may need fields we do not know about.
    payload["data"] = raw_data or json.dumps(data, separators=(",", ":"))
    return _finish(params, CaptchaType.AWSWAF, payload, sitekey=sitekey, site_url=pageurl)


def _altcha(params: dict[str, str]) -> InRequest:
    pageurl = _require(params, "pageurl")
    payload = _common(params)
    challenge_json = _get(params, "challenge_json")
    if challenge_json is not None:
        payload.update(_json_object(challenge_json, "challenge_json"))
    else:
        challenge_url = _get(params, "challenge_url")
        if challenge_url is not None:
            payload["challenge_url"] = challenge_url
        else:
            payload["challenge"] = _require(params, "challenge")
            _put(payload, params, "algorithm", "salt", "signature", "max", "maxnumber")
    sitekey = _get(params, "sitekey")
    return _finish(params, CaptchaType.ALTCHA, payload, sitekey=sitekey, site_url=pageurl)


# Simple methods are just the shared shapes with their type filled in.
_FRIENDLY_EXTRAS = ("puzzle", "signature", "salt", "difficulty", "workers")
_PARSERS = {
    "userrecaptcha": _userrecaptcha,
    "hcaptcha": partial(_sitekey_method, captcha_type=CaptchaType.HCAPTCHA, extras=("host",)),
    "turnstile": partial(_sitekey_method, captcha_type=CaptchaType.TURNSTILE, extras=("action", "cdata")),
    "funcaptcha": _funcaptcha,
    "geetest": _geetest,
    "textcaptcha": _textcaptcha,
    "base64": partial(_body_method, captcha_type=CaptchaType.IMAGE_TO_TEXT),
    "rotatecaptcha": partial(_body_method, captcha_type=CaptchaType.ROTATE, extras=("angle",)),
    "clickcaptcha": partial(_body_method, captcha_type=CaptchaType.CLICK, instruction=True),
    "coordinates": partial(_body_method, captcha_type=CaptchaType.COORDINATES, instruction=True),
    "grid": _grid,
    "amazon_waf": _amazon_waf,
    "audio": partial(_body_method, captcha_type=CaptchaType.AUDIO),
    "friendly": partial(_sitekey_method, captcha_type=CaptchaType.FRIENDLY, extras=_FRIENDLY_EXTRAS),
    # 2captcha's own docs spell this one out; accept both.
    "friendly_captcha": partial(_sitekey_method, captcha_type=CaptchaType.FRIENDLY, extras=_FRIENDLY_EXTRAS),
    "altcha": _altcha,
}


def parse_in_request(params: dict[str, str]) -> InRequest:
    """Map legacy /in.php fields onto an internal request.

    Unknown or missing method -> ERROR_NO_SUCH_METHOD; missing required field ->
    ERROR_BAD_PARAMETERS.
    """
    method = (_get(params, "method") or "").lower()
    parser = _PARSERS.get(method)
    if parser is None:
        raise LegacyError(ERROR_NO_SUCH_METHOD, f"unknown method: {method or '<missing>'}")
    return parser(params)


def format_solution_string(captcha_type: CaptchaType, solution: dict[str, Any]) -> str:
    """Render a JSON solution dict as the legacy /res.php answer string.

    Token types return the token; text the text; rotate/slider the number; grid
    click:3/8/9 (1-based — our JSON carries 0-based indices, so add one); click
    and coordinates the documented pairs; GeeTest v3 the three-field JSON object
    after OK|; cookie/data types compact JSON. Empty means nothing usable.
    """
    if not solution:
        return ""

    kind = solution_kind(captcha_type)
    if kind is SolutionKind.TEXT:
        return solution.get("text") or ""
    if kind is SolutionKind.ROTATE:
        return _number(solution.get("angle"))
    if kind is SolutionKind.SLIDER:
        return _number(solution.get("offset"))
    if kind is SolutionKind.GRID:
        return _grid_answer(solution.get("objects") or solution.get("indices"))
    if kind is SolutionKind.COORDS:
        return _coords_answer(captcha_type, solution.get("coordinates") or solution.get("coords"))
    if kind is SolutionKind.BBOX:
        bbox = solution.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            return ",".join(_number(value) for value in bbox[:4])
        return ""
    if kind in (SolutionKind.RECAPTCHA, SolutionKind.HCAPTCHA):
        return solution.get("gRecaptchaResponse") or solution.get("token") or ""
    if kind is SolutionKind.TURNSTILE or kind is SolutionKind.TOKEN:
        return solution.get("token") or ""
    if kind is SolutionKind.GEETEST_V3:
        return _json_answer(solution, ("challenge", "validate", "seccode"))
    if kind is SolutionKind.GEETEST_V4:
        return _json_answer(solution, ("captcha_id", "lot_number", "pass_token", "gen_time", "captcha_output"))
    if kind is SolutionKind.FRIENDLY or kind is SolutionKind.ALTCHA:
        nested = solution.get("data")
        if isinstance(nested, dict) and nested.get("token"):
            return str(nested["token"])
        return solution.get("token") or _compact_json(solution)

    # Cookie engines, AWS WAF, nested-data vendors and Cloudflare: the legacy
    # answer is the JSON payload the worker built, not a single token.
    return _compact_json(solution)


def _number(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _grid_answer(raw: Any) -> str:
    if not isinstance(raw, (list, tuple)):
        return ""
    cells: list[str] = []
    for item in raw:
        try:
            cells.append(str(int(item) + 1))
        except (TypeError, ValueError):
            continue
    return "click:" + "/".join(cells) if cells else ""


def _coords_answer(captcha_type: CaptchaType, raw: Any) -> str:
    if not isinstance(raw, (list, tuple)):
        return ""
    points = [
        (_number(point[0]), _number(point[1]))
        for point in raw
        if isinstance(point, (list, tuple)) and len(point) >= 2
    ]
    if not points:
        return ""
    if captcha_type is CaptchaType.COORDINATES:
        return "coordinate:" + ";".join(f"x={x},y={y}" for x, y in points)
    return "click:" + ";".join(f"{x},{y}" for x, y in points)


def _json_answer(solution: dict[str, Any], keys: tuple[str, ...]) -> str:
    picked = {key: solution[key] for key in keys if solution.get(key) not in (None, "")}
    return _compact_json(picked)


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)
