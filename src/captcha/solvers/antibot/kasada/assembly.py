"""Kasada answer assembly: body decoding and the solution payload.

Kept out of the solver so the flow stays readable and under the file cap. Nothing
here decides success -- the solver checks reload:true and the ct header first;
these helpers only turn what the server returned into the keys a client replays.
"""

from __future__ import annotations

import base64
from typing import Any

from captcha.domain.errors import SensorError
from captcha.engines.kasada import pow

from .endpoints import HDR_CT, HDR_FC, HDR_H, HDR_ST, HDR_V


def body_of(built: dict[str, Any]) -> bytes:
    """Decode the builder's /tl body; refuse anything that is not base64."""
    encoded = built.get("payload_b64")
    if not isinstance(encoded, str) or not encoded:
        raise SensorError("kasada: builder returned no payload", context={"keys": sorted(built)})
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise SensorError("kasada: builder payload is not base64", context={"chars": len(encoded)}) from exc


def reloaded(resp: Any) -> bool:
    """The /tl success marker: a JSON body with reload:true."""
    try:
        data = resp.json()
    except Exception:
        return False
    return isinstance(data, dict) and data.get("reload") is True


def pow_extra(payload: dict[str, Any]) -> dict[str, Any]:
    """Collect the cd inputs from the payload: nested extra first, then top level."""
    nested = payload.get("extra")
    out: dict[str, Any] = dict(nested) if isinstance(nested, dict) else {}
    for key in (
        pow.EXTRA_ST,
        pow.EXTRA_ANSWERS,
        pow.EXTRA_WORK_TIME,
        pow.EXTRA_ID,
        pow.EXTRA_DURATION,
        pow.EXTRA_RST,
    ):
        if key in payload:
            out[key] = payload[key]
    return out


def cd_header(builder: Any, build_ctx: Any) -> str | None:
    """cd only when the payload carried the VM answers; never fabricated."""
    build_cd = getattr(builder, "build_cd", None)
    if not callable(build_cd):
        return None
    try:
        return build_cd(build_ctx)
    except SensorError:
        return None


def solution_extra(
    jar: dict[str, str],
    ua: str,
    ct: str,
    *,
    st: str | None,
    version: str | None,
    fc: str | None,
    h: str | None,
    note: str | None,
) -> dict[str, Any]:
    """The 2captcha cookie shape plus the headers a client has to replay.

    extra wins over every canonical key in domain/solution.py, so these land in
    the solution untouched: cookies, userAgent, x-kpsdk-ct/st/h/v/fc and a
    headers map with the SDK ones only.
    """
    extra: dict[str, Any] = {
        "cookies": {name: value for name, value in jar.items() if value},
        "userAgent": ua,
        HDR_CT: ct,
    }
    for key, value in ((HDR_ST, st), (HDR_V, version), (HDR_FC, fc), (HDR_H, h)):
        if value:
            extra[key] = value
    if note:
        extra["mfc"] = note
    return extra
