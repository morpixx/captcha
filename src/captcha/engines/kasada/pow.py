"""x-kpsdk-cd: the per-request proof-of-work header.

Public shape (2026-05 and 2026-11 write-ups agree on the field set): cd is a JSON
object with workTime, id, answers, duration, st (some tenants add rst), sent as
the x-kpsdk-cd header, single use, valid for a few seconds, and always minted
from the latest x-kpsdk-ct plus x-kpsdk-fc from /mfc.

What cannot be reproduced statically: the seed mixes a value that never reaches
the wire, so the answers come out of the VM inside p.js. This module therefore
only assembles and encodes an envelope from values the caller already holds (a
VM capture, or the task payload when the client generated one). It never invents
answers: missing answers means the caller gets SensorError, and a solve that did
not need cd simply omits the header.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from captcha.domain.errors import SensorError

#: Field order the reference implementations serialize in.
CD_FIELDS: tuple[str, ...] = ("workTime", "id", "duration", "answers", "st", "rst")

#: extra[] keys payload mode can carry.
EXTRA_ST = "kpsdk_st"
EXTRA_ANSWERS = "kpsdk_answers"
EXTRA_WORK_TIME = "kpsdk_work_time"
EXTRA_ID = "kpsdk_id"
EXTRA_DURATION = "kpsdk_duration"
EXTRA_RST = "kpsdk_rst"

MAX_ANSWERS = 64


def envelope(
    *,
    st: int | str,
    answers: list[int] | tuple[int, ...] | str,
    work_time: int = 0,
    ident: str = "",
    duration: int = 0,
    rst: str = "",
) -> dict[str, Any]:
    """Assemble the documented cd object. Values are used verbatim."""
    return {
        "workTime": int(work_time),
        "id": str(ident),
        "duration": int(duration),
        "answers": _answers(answers),
        "st": int(st),
        "rst": str(rst),
    }


def encode(payload: dict[str, Any]) -> str:
    """Header value: base64 of the compact JSON object.

    Two transports exist in the wild (raw JSON and base64 JSON). The base64 form
    is what the previous builder documented and what the reference SDKs send, so
    it is the default; encode_json keeps the other one addressable.
    """
    return base64.b64encode(_json(payload).encode()).decode()


def encode_json(payload: dict[str, Any]) -> str:
    return _json(payload)


def _json(payload: dict[str, Any]) -> str:
    ordered = {key: payload[key] for key in CD_FIELDS if key in payload}
    ordered.update({key: value for key, value in payload.items() if key not in ordered})
    return json.dumps(ordered, separators=(",", ":"), sort_keys=False)


def envelope_from(extra: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build the envelope from payload-mode extras, or None when they are absent."""
    st = extra.get(EXTRA_ST)
    answers = extra.get(EXTRA_ANSWERS)
    if st in (None, "") or answers in (None, "", [], ()):
        return None
    return envelope(
        st=st,
        answers=answers,
        work_time=_int(extra.get(EXTRA_WORK_TIME)),
        ident=str(extra.get(EXTRA_ID) or ""),
        duration=_int(extra.get(EXTRA_DURATION)),
        rst=str(extra.get(EXTRA_RST) or ""),
    )


def require_envelope(extra: Mapping[str, Any]) -> dict[str, Any]:
    """envelope_from, but a missing pair is a typed error instead of None."""
    payload = envelope_from(extra)
    if payload is None:
        raise SensorError(
            "kasada: cd needs the VM PoW answers (kpsdk_st + kpsdk_answers); "
            "they cannot be derived from anything the client holds",
            context={"extra_keys": sorted(extra)},
        )
    return payload


def _answers(raw: Any) -> list[int] | str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list | tuple):
        if len(raw) > MAX_ANSWERS:
            raise SensorError("kasada: too many cd answers", context={"count": len(raw)})
        return [int(value) for value in raw]
    raise SensorError("kasada: cd answers must be a list or a string", context={"got": type(raw).__name__})


def _int(raw: Any) -> int:
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0
