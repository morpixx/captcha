"""Binance bCAPTCHA2 slider.

getCaptcha -> background/piece -> slider CV -> encrypt trace+offset into
"data" -> validateCaptcha -> "captcha#..." token. A client that already
captured the page-encoded blob can pass it as payload["data"] and we use it
verbatim. A BOX/grid upgrade is a different challenge, so it is
UnsolvableError with captchaType in context, not a fabricated slide.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep
from . import wire

_HEADERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bnc-uuid", ("bnc_uuid", "bnc-uuid")),
    ("csrftoken", ("csrftoken", "csrf_token")),
    ("device-info", ("device_info", "device-info")),
    ("captcha-sdk-version", ("captcha_sdk_version", "captcha-sdk-version")),
    ("x-captcha-se", ("x_captcha_se", "x-captcha-se")),
    ("fvideo-id", ("fvideo_id", "fvideo-id")),
    ("x-trace-id", ("trace_id", "x-trace-id")),
    ("x-ui-request-trace", ("trace_id", "x-ui-request-trace")),
)


@register_solver
class BinanceSolver(InteractiveBase):
    """Binance slider. Payload needs bizScene; headers ride in the payload."""

    info = SolverInfo(
        name="binance",
        types=(CaptchaType.BINANCE,),
        http=True,
        needs_js_runtime=True,  # the page owns the answer envelope
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = task.payload
        biz_scene = _first(payload, "bizScene", "biz_scene", "bizType")
        if not biz_scene:
            raise UnsolvableError(
                "missing payload fields: ['bizScene']",
                context={"have": sorted(payload)},
            )
        if ctx.http is None:
            raise ConfigError("binance solver needs ctx.http", context={"solver": self.info.name})

        start = time.monotonic()
        headers = _headers(task)
        body = {
            "bizScene": str(biz_scene),
            "securityCheckResponseValidateId": str(_first(payload, "validateId", "validate_id") or ""),
        }
        resp = await ctx.http.post(ep.GET_CAPTCHA, json=body, headers=headers)
        root = wire.json_object(resp, "binance getCaptcha")
        wire.raise_for_failure(root, "binance getCaptcha")
        challenge = wire.parse_challenge(root)

        bg_body = await _image(ctx, challenge["bg"], headers)
        piece_url = challenge["piece"] or str(_first(payload, "piece_url", "pieceUrl") or "")
        if not piece_url:
            raise UnsolvableError(
                "binance challenge carries one image: pass piece_url with the slider slice",
                context={"bg": challenge["bg"], "keys": sorted(root)},
            )
        piece_body = await _image(ctx, piece_url, headers)
        cv = await self._solve_cv(
            CaptchaType.SLIDER,
            task,
            ctx,
            {
                "bg": wire.b64(bg_body),
                "piece": wire.b64(piece_body),
                "piece_width": challenge["piece_width"],
                "seed": payload.get("seed"),
            },
        )
        offset = float(cv.offset or 0.0)
        if offset <= 0:
            raise UnsolvableError("binance slider resolved to a non-positive offset", context={"offset": offset})

        prebuilt = _first(payload, "data", "answer_data")
        if prebuilt:
            encoded = str(prebuilt)
        else:
            track = wire.build_track(cv, payload.get("seed"))
            encoded = wire.encode_answer(
                challenge["sig"],
                challenge["salt"],
                offset,
                track,
                meta={
                    "bizScene": str(biz_scene),
                    "captchaType": challenge["captcha_type"],
                    "tag": challenge["tag"],
                },
            )

        verify_body: dict[str, Any] = {
            "securityCheckResponseValidateId": challenge["validate_id"],
            "validateId": challenge["validate_id"],
            "bizScene": str(biz_scene),
            "captchaType": challenge["captcha_type"],
            "sig": challenge["sig"],
            "data": encoded,
        }
        if challenge["salt"]:
            verify_body["salt"] = challenge["salt"]
        if challenge["tag"]:
            verify_body["tag"] = challenge["tag"]
        sign_key = _first(payload, "sign_key", "signKey")
        if sign_key:
            verify_body["s"] = wire.sign_answer(str(sign_key), encoded)

        vr = await ctx.http.post(ep.VALIDATE_CAPTCHA, json=verify_body, headers=headers)
        vroot = wire.json_object(vr, "binance validateCaptcha")
        wire.raise_for_failure(vroot, "binance validateCaptcha")
        token, data = wire.token_from(vroot)
        if not token:
            raise ChallengeExpiredError(
                "binance validateCaptcha returned no token (challenge consumed or rejected)",
                context={"keys": sorted(vroot), "validateId": challenge["validate_id"]},
            )

        extra: dict[str, Any] = {
            "validateId": challenge["validate_id"],
            "captchaType": challenge["captcha_type"],
        }
        session_id = _first(data, "sessionId", "session_id")
        if session_id:
            extra["sessionId"] = str(session_id)
        ua = _first(payload, "user_agent", "userAgent") or task.user_agent
        if ua:
            extra["userAgent"] = str(ua)
        return SolverResult(
            token=token,
            extra=extra,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )


async def _image(ctx: SolverContext, url: str, headers: dict[str, str]) -> bytes:
    """Fetch a challenge image; data: URIs are decoded without a round trip."""
    if url.startswith("data:"):
        try:
            return base64.b64decode(url.split(",", 1)[1])
        except Exception as exc:
            raise UnsolvableError("binance image is a broken data URI", context={"head": url[:48]}) from exc
    resp = await ctx.http.get(url, headers=headers)
    wire.raise_for_status(resp, "binance image")
    if not resp.body:
        raise UnsolvableError("binance image body is empty", context={"url": url})
    return resp.body


def _headers(task: Task) -> dict[str, str]:
    """Fingerprint headers the web client sends; the proxy rides on ctx.http."""
    payload = task.payload
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "clienttype": str(_first(payload, "clienttype") or "web"),
        "lang": str(_first(payload, "lang") or task.language or "en"),
    }
    for name, aliases in _HEADERS:
        value = _first(payload, *aliases)
        if value not in (None, ""):
            headers[name] = str(value)
    ua = _first(payload, "user_agent", "userAgent") or task.user_agent
    if ua:
        headers["user-agent"] = str(ua)
    page = str(_first(payload, "pageurl", "websiteURL", "site_url") or task.site_url or "")
    if page:
        headers["referer"] = page
        if "://" in page:
            headers["origin"] = page.split("/", 3)[0] + "//" + page.split("/", 3)[2]
    return headers


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
