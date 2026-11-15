"""Minimal protobuf encoder for the reCAPTCHA reload request.

The reload endpoint accepts `application/x-protobuffer` with field numbers
hard-coded in the public grecaptcha bundle. We only encode; a full protobuf
dependency would have to track Google's churn for five string fields.

Fields used by the minimal token exchange (see the v3 README captures):

    1  v (release slug)        2  c (anchor token)      4  bg (optional)
    5  integer seed            6  reason                7  prior reload token
    8  action                 14  k (sitekey)          16  canvas/audio blob
    20 perf JSON              22 JS probes             25 mouse/touch events
    28 anchor-ms              29 execute-ms
"""

from __future__ import annotations

from collections.abc import Mapping

#: Fingerprint fields the client sends as length-delimited strings.
_FP_STRINGS = frozenset({5, 7, 16, 20, 22, 25})
#: Fingerprint fields the client sends as varints.
_FP_INTS = frozenset({28, 29})


def _varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _tag(number: int, wire: int) -> bytes:
    return _varint((number << 3) | wire)


def _string_field(number: int, text: str) -> tuple[int, bytes]:
    raw = text.encode("utf-8")
    return number, _tag(number, 2) + _varint(len(raw)) + raw


def _int_field(number: int, value: int) -> tuple[int, bytes]:
    return number, _tag(number, 0) + _varint(value)


def encode_reload_body(
    *,
    version: str,
    token: str,
    sitekey: str,
    action: str | None = None,
    reason: str = "q",
    bg: str | None = None,
    fingerprint: Mapping[str | int, object] | None = None,
) -> bytes:
    """Encode the reload POST body. Field numbers are sorted canonically."""
    fields: list[tuple[int, bytes]] = [
        _string_field(1, version),
        _string_field(2, token),
        _string_field(6, reason),
        _string_field(14, sitekey),
    ]
    if action:
        fields.append(_string_field(8, action))
    if bg:
        fields.append(_string_field(4, bg))

    if fingerprint:
        for raw_key, raw_value in fingerprint.items():
            try:
                number = int(raw_key)
            except (TypeError, ValueError):
                continue
            if raw_value is None or raw_value == "":
                continue
            if number in _FP_STRINGS:
                fields.append(_string_field(number, str(raw_value)))
            elif number in _FP_INTS:
                try:
                    fields.append(_int_field(number, int(str(raw_value))))
                except (TypeError, ValueError):
                    continue

    return b"".join(chunk for _, chunk in sorted(fields, key=lambda item: item[0]))
