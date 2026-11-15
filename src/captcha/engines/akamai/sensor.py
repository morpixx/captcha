"""Akamai `sensor_data`: section model, v3 serialization, encode seam.

What is public about the classic sensor flow (2026-06 write-up; the repo's
docs/reverse-2026.md has no Akamai section yet):

  body      POST {"sensor_data": "<encoded>"} -- form `sensor_data=...` on the
            older 2.x tenants
  layout    `3:<section>:<section>:...`; a section is a colon-delimited group
            of fields, fields inside a group are key=value pairs
  encode    v3 runs two PRNG stages over the serialized string: the *elements*
            (sections) are shuffled with a PRNG seeded from a hash of the live
            sensor script, then every character is substituted with a second
            PRNG seeded from a hash of bm_sz (seed 8888888 before a real
            cookie exists -- the first payload of a session)
  verdict   the answer is a new _abck; only its tilde tail is readable, see
            solvers/antibot/akamai/abck.py

What is *not* public: Akamai's PRNG constants and the exact section/field
table. shuffle_elements/substitute_chars below are a labelled **model** of the
two documented stages: deterministic and reversible, never claimed to match a
live release. The production path is the JS one -- builder.build evals the
tenant's own bmak script in the shared V8 runtime and takes the bytes it POSTs.
The model exists so the framing, the seeds and the solver can be exercised
without a live script, and it is opt-in (sensor_encoder="model"), never a
silent fallback.
"""

from __future__ import annotations

import hashlib
import string
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from captcha.engines.base import BuildContext

#: Leading field of the serialized payload, i.e. the sensor format generation.
VERSION = "3"

#: Documented default seed for the substitution stage before bm_sz exists.
DEFAULT_SEED = 8888888

#: Characters the model's substitution maps onto. The colon stays in the
#: alphabet on purpose: the encoded blob must remain parseable, which is what
#: makes decode() a real inverse rather than a guess.
ALPHABET = string.ascii_letters + string.digits + ":/+=._-~"

#: Hard caps so a hostile or buggy source cannot inflate one payload to megabytes.
MAX_EVENTS = 128
MAX_FIELD_CHARS = 512


# ---- seeds ------------------------------------------------------------------


def script_seed(script: str | bytes) -> int:
    """Seed of the shuffle stage: a hash of the live sensor script file."""
    raw = script.encode("utf-8", "replace") if isinstance(script, str) else script
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") & 0x7FFFFFFF


def bm_sz_seed(value: str | None) -> int:
    """Seed of the substitution stage: a hash derived from the bm_sz cookie."""
    text = (value or "").strip()
    if not text:
        return DEFAULT_SEED
    return int.from_bytes(hashlib.sha256(text.encode("utf-8", "replace")).digest()[:4], "big") & 0x7FFFFFFF


def _lcg(seed: int) -> Iterator[int]:
    """glibc rand() parameters. Swap these for the release's own PRNG once a
    live diff yields one; nothing else in this module depends on the choice."""
    state = seed & 0xFFFFFFFF
    while True:
        state = (state * 1103515245 + 12345) & 0xFFFFFFFF
        yield state >> 16


# ---- model transform --------------------------------------------------------


def shuffle_elements(elements: Sequence[str], *, seed: int) -> list[str]:
    """Fisher-Yates over the section list, driven by the script-hash PRNG."""
    out = list(elements)
    rnd = _lcg(seed)
    for i in range(len(out) - 1, 0, -1):
        j = next(rnd) % (i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def unshuffle_elements(elements: Sequence[str], *, seed: int) -> list[str]:
    """Inverse of shuffle_elements: replay the same swaps in reverse order."""
    out = list(elements)
    rnd = _lcg(seed)
    swaps = [(i, next(rnd) % (i + 1)) for i in range(len(out) - 1, 0, -1)]
    for i, j in reversed(swaps):
        out[i], out[j] = out[j], out[i]
    return out


def substitute_chars(text: str, *, seed: int) -> str:
    """Per-character substitution; characters outside the alphabet pass through."""
    rnd = _lcg(seed)
    size = len(ALPHABET)
    out: list[str] = []
    for char in text:
        index = ALPHABET.find(char)
        if index < 0:
            out.append(char)
            continue
        out.append(ALPHABET[(index + next(rnd)) % size])
    return "".join(out)


def unsubstitute_chars(text: str, *, seed: int) -> str:
    rnd = _lcg(seed)
    size = len(ALPHABET)
    out: list[str] = []
    for char in text:
        index = ALPHABET.find(char)
        if index < 0:
            out.append(char)
            continue
        out.append(ALPHABET[(index - next(rnd)) % size])
    return "".join(out)


def encode_elements(elements: Sequence[str], *, seed: int) -> str:
    """Serialize + shuffle + substitute, in the order the public write-up gives."""
    return substitute_chars(":".join(shuffle_elements(elements, seed=seed)), seed=seed)


def decode_elements(encoded: str, *, seed: int) -> list[str]:
    """Inverse of encode_elements. Tooling/test side only; the solver never
    needs to read a payload back."""
    return unshuffle_elements(unsubstitute_chars(encoded, seed=seed).split(":"), seed=seed)


# ---- sections ---------------------------------------------------------------


def serialize(sections: Sequence[tuple[str, str]], *, version: str = VERSION) -> list[str]:
    """Turn named sections into the colon-delimited field list the encoder takes.

    Empty sections are dropped: an absent signal is not the string "-", it is a
    section that never made it into the payload.
    """
    fields = [version]
    for name, body in sections:
        if body:
            fields.append(f"{name}={body}")
    return fields


def encode_sections(
    sections: Sequence[tuple[str, str]],
    *,
    script: str | bytes | None = None,
    bm_sz: str | None = None,
    seed: int | None = None,
    version: str = VERSION,
) -> str:
    """Model-encode a section list. An explicit seed wins over the documented pair."""
    if seed is None:
        seed = (script_seed(script) if script else 0) ^ bm_sz_seed(bm_sz)
    return encode_elements(serialize(sections, version=version), seed=seed)


def sections(ctx: BuildContext) -> list[tuple[str, str]]:
    """Collect the documented signal set from the fingerprint and behavior packs.

    Every value comes from an injected collaborator: Profile fields (UA, the
    rendering hashes), BuildContext.extra (screen/hardware/perf the worker
    captured) and the behavior package (mouse/key/touch/scroll rows). A missing
    source drops its field instead of inventing a default -- a fabricated screen
    size is a signal mismatch, which is worse than a short payload.
    """
    fingerprint = ctx.fingerprint
    extra = ctx.extra if isinstance(ctx.extra, Mapping) else {}
    behavior = _mapping(ctx.behavior)

    out: list[tuple[str, str]] = []
    device = _fields(
        ("ua", _attr(fingerprint, "user_agent")),
        ("lang", _attr(fingerprint, "accept_language")),
        ("tz", _attr(fingerprint, "timezone")),
        ("canvas", _attr(fingerprint, "canvas_hash")),
        ("webglv", _attr(fingerprint, "webgl_vendor")),
        ("webglr", _attr(fingerprint, "webgl_renderer")),
        ("audio", _attr(fingerprint, "audio_hash")),
        ("fonts", _count(_attr(fingerprint, "fonts"))),
    )
    if device:
        out.append(("device", device))

    screen = _flat(extra.get("screen"))
    if screen:
        out.append(("screen", screen))

    hardware = _flat(extra.get("hardware"))
    if hardware:
        out.append(("hardware", hardware))

    perf = _flat(behavior.get("perf") or extra.get("perf"))
    if perf:
        out.append(("perf", perf))

    for name in ("mouse", "keys", "touch", "scroll"):
        rows = _events(behavior.get(name) or extra.get(name))
        if rows:
            out.append((name, rows))

    orientation = _flat(extra.get("orientation"))
    if orientation:
        out.append(("orientation", orientation))

    battery = _flat(extra.get("battery"))
    if battery:
        out.append(("battery", battery))

    timing = _fields(
        ("init", extra.get("init_ms")),
        ("submit", ctx.timestamp_ms),
        ("seq", extra.get("seq")),
    )
    if timing:
        out.append(("timing", timing))

    return out


# ---- field helpers ----------------------------------------------------------


def _fields(*pairs: tuple[str, Any]) -> str:
    out: list[str] = []
    for name, value in pairs:
        text = _text(value)
        if text is not None:
            out.append(f"{name}={text}")
    return ",".join(out)


def _flat(value: Any) -> str:
    """Flatten a mapping into a stable k=v,k=v string (keys sorted)."""
    if not isinstance(value, Mapping):
        return ""
    return _fields(*[(str(key), value[key]) for key in sorted(value, key=str)])


def _events(value: Any) -> str:
    """Event rows as t,x,y;t,x,y, capped at MAX_EVENTS."""
    if not isinstance(value, list | tuple):
        return ""
    rows: list[str] = []
    for row in list(value)[:MAX_EVENTS]:
        if isinstance(row, Mapping):
            cells = [str(row[key]) for key in ("t", "x", "y", "type") if row.get(key) is not None]
            if cells:
                rows.append(",".join(cells))
        elif isinstance(row, list | tuple):
            rows.append(",".join(_text(cell) or "" for cell in row))
    return ";".join(rows)


#: Keys a behaviour pack is read from. A dataclass, a plain object and a dict all
#: work; probing by name keeps this free of an import on the behavior package.
BEHAVIOR_KEYS = ("mouse", "keys", "touch", "scroll", "perf")


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    out: dict[str, Any] = {}
    data = getattr(value, "__dict__", None)
    if isinstance(data, Mapping):
        out.update(data)
    for key in BEHAVIOR_KEYS:
        attribute = getattr(value, key, None)
        if attribute is not None:
            out.setdefault(key, attribute)
    return out


def _attr(source: Any, name: str) -> Any:
    return getattr(source, name, None) if source is not None else None


def _count(value: Any) -> Any:
    if isinstance(value, list | tuple):
        return len(value)
    return value


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.6f}"
    if isinstance(value, str):
        text = value.strip()
        return text[:MAX_FIELD_CHARS] if text else None
    return None
