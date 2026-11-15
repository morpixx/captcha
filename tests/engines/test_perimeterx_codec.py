"""PerimeterX collector codec: the four fields the bundle owns, byte for byte.

`en`, `payload`, `pc` and `sid` are the parts of a sensorless collector POST that
are not plain JSON. The writeup (docs/reverse-2026.md §7) gives the transforms but
not the constants' interaction, so every expected value here is computed
independently in the test -- from `hashlib`/`base64`/the documented shift -- and
never copied out of the implementation. A drift in the codec shows up as a
failing assertion instead of a quiet rejection in production.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import quote_from_bytes, unquote_to_bytes

import pytest

from captcha.crypto import xor_cipher
from captcha.domain.errors import SensorError
from captcha.engines.perimeterx import codec

FIRST = b"50"
SECOND = b"10"
SIGNALS = {"PX101": "Mozilla/5.0", "PX105": {"w": 1920, "h": 1080}, "PX109": True}


def pc_expected(*, uuid: str, tag: str, ft: int, payload_json: str, length: int = 10) -> str:
    """Independent re-derivation: HMAC-MD5 -> hex -> a-f remap -> even indices."""
    digest = hmac.new(f"{uuid}:{tag}:{ft}".encode(), payload_json.encode("utf-8"), hashlib.md5).hexdigest()
    remapped = digest.translate(str.maketrans("abcdef", "789012"))
    return remapped[::2][:length]


def swap(text: str) -> str:
    """The documented adjacent-pair swap, re-implemented here on purpose."""
    chars = list(text)
    for index in range(0, len(chars) - 1, 2):
        chars[index], chars[index + 1] = chars[index + 1], chars[index]
    return "".join(chars)


def ob_blob(plaintext: str) -> str:
    """Encode an `ob` answer: plaintext -> XOR "50" -> XOR "10" -> base64."""
    raw = xor_cipher(xor_cipher(plaintext.encode("latin-1"), FIRST), SECOND)
    return base64.b64encode(raw).decode("ascii")


def test_en_field_is_base64_of_the_key_name() -> None:
    assert codec.EN_KEY_NAME == "50"
    assert codec.en_field() == base64.b64encode(b"50").decode("ascii") == "NTA="


def test_payload_round_trips() -> None:
    encoded = codec.encode_payload(SIGNALS)
    assert "{" not in encoded
    assert codec.decode_payload(encoded) == SIGNALS


def test_payload_layers_are_applied_in_the_documented_order() -> None:
    encoded = codec.encode_payload({"a": 1})
    # outer base64 -> swap -> inner base64 -> xor "10" then "50" -> json
    outer = base64.b64decode(unquote_to_bytes(encoded))
    inner = base64.b64decode(codec.unswap_pairs(outer.decode("ascii")))
    raw = xor_cipher(xor_cipher(inner, SECOND), FIRST)
    assert raw == b'{"a":1}'


def test_payload_is_percent_encoded_for_a_form_post() -> None:
    payload = {"k": "value with spaces/and+plus"}
    expected = quote_from_bytes(
        base64.b64encode(
            swap(
                base64.b64encode(xor_cipher(xor_cipher(b'{"k":"value with spaces/and+plus"}', FIRST), SECOND)).decode(
                    "ascii"
                )
            ).encode("ascii")
        ),
        safe="",
    )
    encoded = codec.encode_payload(payload)
    assert encoded == expected
    # the three characters base64 can emit that break a form body are escaped
    assert not set("+/=") & set(encoded)
    assert codec.decode_payload(encoded) == payload


def test_decode_payload_rejects_garbage() -> None:
    with pytest.raises(SensorError):
        codec.decode_payload("")
    with pytest.raises(SensorError):
        codec.decode_payload(base64.b64encode(b"not json at all").decode())


def test_pc_matches_an_independent_hmac_md5_fold() -> None:
    payload_json = json.dumps(SIGNALS, separators=(",", ":"), ensure_ascii=True)
    token = codec.pc_token(uuid="visitor-1", tag="web", ft=1_700_000_000_000, payload_json=payload_json)
    assert token == pc_expected(uuid="visitor-1", tag="web", ft=1_700_000_000_000, payload_json=payload_json)
    assert len(token) == codec.DEFAULT_PC_LEN
    assert token.isdigit()


def test_pc_moves_with_the_payload_and_the_salt() -> None:
    base = dict(uuid="visitor-1", tag="web", ft=1, payload_json='{"a":1}')
    first = codec.pc_token(**base)
    assert codec.pc_token(**{**base, "payload_json": '{"a":2}'}) != first
    assert codec.pc_token(**{**base, "uuid": "visitor-2"}) != first
    assert codec.pc_token(**{**base, "length": 13}) == pc_expected(
        uuid="visitor-1", tag="web", ft=1, payload_json='{"a":1}', length=13
    )


def test_swap_pairs_keeps_a_trailing_odd_character() -> None:
    assert codec.swap_pairs("abcdef") == "badcfe"
    assert codec.swap_pairs("abcde") == "badce"
    assert codec.unswap_pairs(codec.swap_pairs("abcdef")) == "abcdef"


def test_sid_is_shifted_into_the_unicode_tag_block() -> None:
    source = "vid-1234"
    hidden = codec.sid_encode(source)
    assert all(0xE0000 <= ord(ch) <= 0xE007F for ch in hidden)
    assert len(hidden) == len(source)
    assert codec.sid_decode(hidden) == source
    # visible characters outside the printable ASCII range pass through untouched
    assert codec.sid_decode(codec.sid_encode("ж")) == "ж"


def test_ob_decodes_to_a_segment_list_and_cookies() -> None:
    plaintext = "~~~~".join(("_pxhd|verdict-value", "PX101|Mozilla/5.0", "cookie|_px=sensor; _pxvid=v1"))
    segments = codec.parse_ob(ob_blob(plaintext))
    assert [segment.name for segment in segments] == ["_pxhd", "PX101", "cookie"]
    assert segments[0].value == "verdict-value"
    assert codec.cookies_from_ob(segments) == {"_pxhd": "verdict-value", "_px": "sensor", "_pxvid": "v1"}


def test_ob_decoder_accepts_plaintext_or_bytes() -> None:
    plain = "_px2|v2~~~~_pxde|v3"
    assert [segment.name for segment in codec.parse_ob(plain)] == ["_px2", "_pxde"]
    assert codec.parse_ob(ob_blob(plain).encode("ascii"))[0].name == "_px2"


def test_cookies_from_ob_ignores_foreign_names() -> None:
    segments = codec.parse_ob(codec.decode_ob(ob_blob("foo|bar~~~~pxcts|ts-value")))
    assert codec.cookies_from_ob(segments) == {"pxcts": "ts-value"}


def test_decode_ob_of_garbage_returns_something_reviewable() -> None:
    blob = base64.b64encode(b"\x00\x01\x02").decode()
    assert isinstance(codec.decode_ob(blob), str)
    # no blob, no segments: an empty answer is not an error, it is an empty jar
    assert codec.decode_ob("") == ""
    assert codec.parse_ob("") == []
    assert codec.cookies_from_ob([]) == {}
