"""Lemin (leminnow) cropped puzzle — fetch the challenge, drag, pre-validate.

Flow:
  1. POST <dashboard>/challenge with the captcha id + embedding domain -> JSON
     carrying `challenge_id` and the scene/piece image URLs.
  2. GET the scene + piece.
  3. `_solve_cv(SLIDER, ...)` -> horizontal destination.
  4. POST /captcha/v1/cropped/pre-validate with {answer, challenge_id}
     -> `data.pv_token`.
  5. token = json({answer, challenge_id, pv_token}); the page needs the first two
     (`lemin_answer`, `lemin_challenge_id`).
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

_B32 = "0123456789abcdefghijklmnopqrstuv"
_DRAG_POINTS = 24


@register_solver
class LeminSolver(InteractiveBase):
    """Lemin cropped captcha. `sitekey` is the `CROPPED_...` captcha id."""

    info = SolverInfo(
        name="lemin",
        types=(CaptchaType.LEMIN,),
        http=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "sitekey", "pageurl")
        if ctx.http is None:
            raise UnsolvableError("lemin solver requires ctx.http", context={})

        headers = _headers(task)
        start = time.monotonic()

        # FILL: bootstrap body. Network -> the lemin script's first request after
        # load: confirm `captcha_id` vs `sitekey`, and whether the embedding
        # origin rides in `domain` (it must match the registered domain).
        body = {"captcha_id": str(task.payload["sitekey"]), "domain": _origin(task)}
        resp = await ctx.http.post(ep.CHALLENGE, json=body, headers=headers)
        data = _json_object(resp, "lemin challenge")

        # FILL: bootstrap reply keys. Network -> that request -> Response; copy
        # the JSON. The id is `challenge_id`, the images `background` + `piece`.
        root = data.get("data") if isinstance(data.get("data"), dict) else data
        challenge_id = _first(root, "challenge_id", "challengeId", "id")
        bg_url = _first(root, "background", "bg", "image", "main_image")
        piece_url = _first(root, "piece", "puzzle", "piece_image", "crop")
        if not (challenge_id and bg_url and piece_url):
            raise CaptchaError(
                "lemin challenge missing id or images — see runbooks/lemin.md",
                context={"keys": sorted(root.keys())},
            )

        bg = await ctx.http.get(str(bg_url), headers=headers)
        piece = await ctx.http.get(str(piece_url), headers=headers)
        cv = await self._solve_cv(CaptchaType.SLIDER, task, ctx, {"bg": bg.body, "piece": piece.body})

        answer = encode_answer(cv.offset or 0.0, float(task.payload.get("piece_y") or ep.HOME[1]))
        vresp = await ctx.http.post(
            ep.PRE_VALIDATE,
            json={"answer": answer, "challenge_id": str(challenge_id)},
            headers=headers,
        )
        vdata = _json_object(vresp, "lemin pre-validate")
        # FILL: pre-validate envelope. Network -> `pre-validate` -> Response:
        # confirm `success` + `data.pv_token`; a rejection carries `code`/`message`.
        if not vdata.get("success"):
            raise CaptchaError(
                "lemin pre-validate rejected",
                context={"code": vdata.get("code") or vdata.get("message")},
            )
        inner = vdata.get("data") if isinstance(vdata.get("data"), dict) else {}
        pv_token = _first(inner, "pv_token", "token")
        if not pv_token:
            raise CaptchaError("lemin pre-validate returned no pv_token", context={"keys": sorted(vdata.keys())})

        token = _assemble_token(answer, str(challenge_id), str(pv_token))
        return SolverResult(
            token=token,
            extra={"answer": answer, "challenge_id": str(challenge_id), "pv_token": str(pv_token)},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )


def encode_answer(offset: float, y: float = ep.HOME[1], points: int = _DRAG_POINTS) -> str:
    """Encode the drag as `<index>x<left>x<top>x` tokens, coordinates in base 32.

    The server only checks the last point, but rejects tracks shorter than
    `ep.MIN_POINTS`; a monotone drag from the piece's home reproduces that.
    FILL: solve one puzzle by hand, then read `lemin_answer` from the form POST in
    devtools: confirm the token shape, the snap grid, the home position and the
    minimum point count (`ep.GRID`/`ep.HOME`/`ep.MIN_POINTS`).
    """
    start_x, start_y = ep.HOME
    end_x, end_y = _snap(offset), _snap(y)
    path: list[tuple[int, int]] = []
    for step in range(1, points + 1):
        point = (
            _snap(start_x + (end_x - start_x) * step / points),
            _snap(start_y + (end_y - start_y) * step / points),
        )
        if not path or path[-1] != point:
            path.append(point)
    while len(path) < ep.MIN_POINTS:
        path.insert(0, ep.HOME)
    return "".join(f"0x{_b32(x)}x{_b32(yy)}x" for x, yy in path)


def _assemble_token(answer: str, challenge_id: str, pv_token: str) -> str:
    """Build the value the page submits.

    FILL: the page needs `lemin_answer` + `lemin_challenge_id`; the pv token is
    the pre-validation receipt the host backend may also want. Capture both
    hidden inputs after a manual solve and trim this JSON to what the client reads.
    """
    return json.dumps(
        {"answer": answer, "challenge_id": challenge_id, "pv_token": pv_token},
        separators=(",", ":"),
    )


def _snap(value: float) -> int:
    """Snap a natural-pixel coordinate onto the widget's drag grid (JS `Math.round`)."""
    return int(value / ep.GRID + 0.5) * ep.GRID


def _b32(value: int) -> str:
    """Base-32 the way `Number.prototype.toString(32)` renders it (digits 0-9, a-v)."""
    if value <= 0:
        return "0"
    out = ""
    while value:
        value, digit = divmod(value, 32)
        out = _B32[digit] + out
    return out


def _origin(task: Task) -> str:
    pageurl = str(task.payload.get("pageurl") or task.payload.get("site_url") or task.site_url or "")
    parsed = urlparse(pageurl if "//" in pageurl else "https://" + pageurl)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _headers(task: Task) -> dict[str, str]:
    """UA/language and page referer from the task; the proxy rides on `ctx.http`."""
    headers = {"Accept": "application/json, text/plain, */*"}
    ua = str(task.payload.get("user_agent") or task.user_agent or "")
    if ua:
        headers["User-Agent"] = ua
    lang = str(task.payload.get("lang") or task.language or "")
    if lang:
        headers["Accept-Language"] = lang
    origin = _origin(task)
    if origin:
        headers["Referer"] = origin + "/"
        headers["Origin"] = origin
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
