"""KeyCAPTCHA — click or drag puzzle; the answer is the widget's assembled key.

Flow:
  1. The host page embeds the challenge as a JSON blob: the session id, the four
     `s_s_c_*` values, the task kind and the puzzle image URLs. Callers pass it in
     `payload["data"]` (JSON string or object) or as flat payload fields.
  2. GET every image URL from the blob.
  3. `_solve_cv(CLICK, ...)` for a click task, `_solve_cv(SLIDER, ...)` otherwise.
  4. POST the assembled answer to the widget's check endpoint, take `cap_key`.
  5. token = `<cap_key>|<attempt>` — the value the page's key field receives.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep

_BLOB_KEYS = ("data", "challenge", "challenge_json", "blob")
_CLICK_KINDS = frozenset({"click", "points", "point", "clickcaptcha"})


@register_solver
class KeyCaptchaSolver(InteractiveBase):
    """KeyCAPTCHA click/drag puzzle. The payload carries the widget blob."""

    info = SolverInfo(
        name="keycaptcha",
        types=(CaptchaType.KEYCAPTCHA,),
        http=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        blob = parse_challenge(task.payload)
        session_id = _first(blob, "s_s_c_session_id", "sess_id", "session_id", "sessionId", "sess")
        if not session_id:
            raise UnsolvableError("keycaptcha blob has no session id", context={"keys": sorted(blob)})
        urls = _image_urls(blob)
        if not urls:
            raise UnsolvableError("keycaptcha blob has no puzzle images", context={"keys": sorted(blob)})
        if ctx.http is None:
            raise UnsolvableError("keycaptcha solver requires ctx.http", context={})

        headers, kind, start = _headers(task), _cv_kind(blob), time.monotonic()

        if kind is CaptchaType.SLIDER and len(urls) < 2:
            raise UnsolvableError("keycaptcha slider task carries one image", context={"images": len(urls)})
        bg = await ctx.http.get(urls[0], headers=headers)
        cv_payload: dict[str, Any] = {"bg": bg.body}
        if kind is CaptchaType.SLIDER:
            piece = await ctx.http.get(urls[1], headers=headers)
            cv_payload["piece"] = piece.body
        else:
            target = _first(blob, "target", "question", "comment", "instruction") or task.payload.get("target")
            if not target:
                raise UnsolvableError("keycaptcha click task has no target text", context={"keys": sorted(blob)})
            cv_payload["target"] = str(target)

        cv = await self._solve_cv(kind, task, ctx, cv_payload)
        answer = encode_answer(cv, kind)

        # FILL: check request. Network -> the POST right after a manual drag:
        # copy the form field names and whether the envelope carries the raw
        # `s_s_c_*` values or a single pre-signed blob.
        body = {
            "sess_id": str(session_id),
            "answer": answer,
            "sign": str(_first(blob, "s_s_c_web_server_sign", "web_server_sign") or ""),
            "sign2": str(_first(blob, "s_s_c_web_server_sign2", "web_server_sign2") or ""),
            "user_id": str(_first(blob, "s_s_c_user_id", "user_id") or ""),
            "type": "click" if kind is CaptchaType.CLICK else "slide",
        }
        resp = await ctx.http.post(ep.CHECK, data=body, headers=headers)
        reply = _json_object(resp, "keycaptcha check")
        cap_key = _first(reply, "cap_key", "capkey", "key", "token")
        if not cap_key:
            raise CaptchaError(
                "keycaptcha check returned no cap_key — see runbooks/keycaptcha.md",
                context={"keys": sorted(reply.keys())},
            )
        return SolverResult(
            token=assemble_token(str(cap_key), reply.get("attempt")),
            extra={"cap_key": str(cap_key), "session_id": str(session_id), "answer": answer},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )


def parse_challenge(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise the widget blob: JSON string, object, or the flat payload."""
    raw = next((payload[key] for key in _BLOB_KEYS if payload.get(key)), None)
    if raw is None:
        return dict(payload)
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise UnsolvableError("keycaptcha blob has unsupported type", context={"type": type(raw).__name__})
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise UnsolvableError("keycaptcha blob is not JSON", context={"head": raw[:120]}) from exc
    if not isinstance(parsed, dict):
        raise UnsolvableError("keycaptcha blob must be a JSON object", context={"type": type(parsed).__name__})
    return parsed


def _cv_kind(blob: dict[str, Any]) -> CaptchaType:
    """Click task unless the blob names a slider/drag task.

    FILL: in devtools capture a click blob and a drag blob (Network -> the widget
    bootstrap JSON) and copy the field carrying the kind; the set below is a guess.
    """
    raw = str(_first(blob, "cap_type", "task", "type", "kind") or "").lower()
    return CaptchaType.CLICK if raw in _CLICK_KINDS else CaptchaType.SLIDER


def _image_urls(blob: dict[str, Any]) -> list[str]:
    """Image URLs in widget order: the list first, then the named bg/piece fields."""
    urls: list[str] = []
    listed = _first(blob, "images", "imgs", "image_urls", "urls")
    if isinstance(listed, str):
        urls.append(listed)
    elif isinstance(listed, list):
        urls.extend(str(item) for item in listed if isinstance(item, str) and item)
    for key in ("bg", "background", "fullbg", "pz", "piece"):
        value = blob.get(key)
        if isinstance(value, str) and value and value not in urls:
            urls.append(value)
    return urls


def encode_answer(cv: SolverResult, kind: CaptchaType) -> str:
    """The `answer` field: `x,y` pairs for a click task, the drag end otherwise.

    FILL: Network -> the widget's check POST -> Form Data; confirm the separator
    (`;` here), the coordinate origin and whether a drag task sends a pointer track.
    """
    if kind is CaptchaType.CLICK:
        return ";".join(f"{int(x)},{int(y)}" for x, y in (cv.coords or []))
    return str(int(cv.offset or 0))


def assemble_token(cap_key: str, attempt: Any) -> str:
    """Build the value the host page submits.

    FILL: 2captcha publishes these tokens as `<40 hex>|<int>` (`84f42e...f79|1`).
    After a manual solve read the hidden input the widget fills and confirm whether
    the `|<int>` tail is the reply's `attempt`, the piece count, or absent.
    """
    if attempt in (None, ""):
        return cap_key
    return f"{cap_key}|{int(attempt)}"


def _headers(task: Task) -> dict[str, str]:
    """UA/language and page referer from the task; the proxy rides on `ctx.http`."""
    pageurl = str(task.payload.get("pageurl") or task.payload.get("site_url") or task.site_url or "")
    headers = {"Accept": "application/json, text/plain, */*"}
    ua = str(task.payload.get("user_agent") or task.user_agent or "")
    if ua:
        headers["User-Agent"] = ua
    lang = str(task.payload.get("lang") or task.language or "")
    if lang:
        headers["Accept-Language"] = lang
    if pageurl:
        parsed = urlparse(pageurl if "//" in pageurl else "https://" + pageurl)
        headers["Referer"] = pageurl
        if parsed.scheme and parsed.netloc:
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
    return headers


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _json_object(resp: Any, what: str) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception as exc:
        raise CaptchaError(f"{what} is not JSON", context={"status": getattr(resp, "status", None)}) from exc
    if not isinstance(data, dict):
        raise CaptchaError(f"{what} is not a JSON object", context={"type": type(data).__name__})
    return data
