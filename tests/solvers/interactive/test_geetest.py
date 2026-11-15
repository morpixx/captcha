"""Unit tests for the GeeTest `w` envelope. No network, no solver run."""

from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from captcha.crypto import aes_cbc_decrypt
from captcha.domain.errors import UnsolvableError
from captcha.solvers.interactive.geetest import payload as p

# Fixed vector: key is ASCII hex, IV is the constant the bundles hardcode.
FIXED_KEY = b"0123456789abcdef"
FIXED_AES_HEX = "db969b3a6dbc23676121c125c923870e"

V3_INPUT = {
    "gt": "gt-1",
    "challenge": "c" * 32,
    "user_response": 100,
    "passtime_ms": 1200,
    "rand_int": 42,
}
V3_AES_HEX = (
    "bc924518fcf2786d05115f01fc39ffb30901a9cb1d6ebbfafe47b9afa52efe59a9a7ded7b5034e8c5a06bfb8f50a014b"
    "f72e81d3c4829693400a326167db85a648f5975b717277630a6ba8c112508ff96eb704ccfde7cba575e92654f73269c9c"
    "e868b6f480dd73eab8cc65ede614f3b75d10f3b3dae55c71c25b2132f7b086585199538b49d7a59c088e424219f2b39"
)
LOT_NUMBER = "f4744c44df4541b3be48c5c270ced20b"
CAPTCHA_ID = "captcha-id"
POW_DETAIL = {
    "version": "1",
    "bits": 0,
    "hashfunc": "md5",
    "datetime": "2025-02-03T21:47:56.826264+08:00",
}


def _rsa_modulus() -> str:
    """A throwaway public modulus so the RSA half can be exercised offline."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    return format(key.public_key().public_numbers().n, "x")


def test_encrypt_body_is_deterministic_and_matches_cryptojs() -> None:
    assert p.encrypt_body({"a": 1}, FIXED_KEY) == FIXED_AES_HEX


def test_encrypt_body_roundtrip_is_compact_json() -> None:
    hexed = p.encrypt_body({"b": 2, "a": [1, "x"]}, FIXED_KEY)
    plain = aes_cbc_decrypt(FIXED_KEY, p.AES_IV, bytes.fromhex(hexed))
    assert plain == b'{"b":2,"a":[1,"x"]}'


def test_session_key_is_sixteen_ascii_hex_chars() -> None:
    key = p.session_key(42)
    assert len(key) == 16
    assert key == b"000000000000002a"
    assert p.session_key(42) == p.session_key(42)


def test_v3_body_carries_the_required_keys() -> None:
    body = p.v3_body(**V3_INPUT)
    assert set(body) == {"lang", "userresponse", "passtime", "imgload", "aa", "ep", "rp"}
    assert body["userresponse"] == 100
    assert body["passtime"] == 1200
    # rp = md5(gt + challenge[:32] + passtime)
    assert body["rp"] == "dbf52b63286625cbcb67df06be926212"


def test_v3_aes_half_is_stable() -> None:
    body = p.v3_body(**V3_INPUT)
    assert p.encrypt_body(body, p.session_key(42)) == V3_AES_HEX


def test_build_w_v3_prefixes_the_aes_half(monkeypatch: pytest.MonkeyPatch) -> None:
    modulus = _rsa_modulus()
    monkeypatch.setattr(p, "V3_RSA_MODULUS_HEX", modulus)
    w = p.build_w_v3(**V3_INPUT)
    assert w.startswith(V3_AES_HEX)
    tail = w[len(V3_AES_HEX) :]
    assert len(tail) == len(modulus) // 2 * 2
    int(tail, 16)  # the RSA half is hex, not base64


def test_build_w_v3_rejects_missing_fields() -> None:
    with pytest.raises(UnsolvableError):
        p.build_w_v3("", "c" * 32, 10, 100, 1)
    with pytest.raises(UnsolvableError):
        p.build_w_v3("gt-1", "", 10, 100, 1)


def test_lot_fields_slices_the_lot_number() -> None:
    assert p.lot_fields(LOT_NUMBER) == {"d2e4.c44df4": "c270ce"}


def test_solve_pow_satisfies_the_bit_prefix() -> None:
    msg, sign = p.solve_pow(LOT_NUMBER, CAPTCHA_ID, {**POW_DETAIL, "bits": 8}, seed=3)
    assert msg.startswith(f"1|8|md5|{POW_DETAIL['datetime']}|{CAPTCHA_ID}|{LOT_NUMBER}||")
    assert sign == hashlib.md5(msg.encode()).hexdigest()
    assert sign.startswith("00")


def test_solve_pow_rejects_unknown_hash() -> None:
    with pytest.raises(UnsolvableError):
        p.solve_pow(LOT_NUMBER, CAPTCHA_ID, {**POW_DETAIL, "hashfunc": "sha3"})


def test_v4_body_carries_the_required_keys() -> None:
    msg, sign = p.solve_pow(LOT_NUMBER, CAPTCHA_ID, POW_DETAIL)
    body = p.v4_body(
        lot_number=LOT_NUMBER,
        captcha_id=CAPTCHA_ID,
        user_response=12,
        passtime_ms=1100,
        pow_msg=msg,
        pow_sign=sign,
    )
    for key in ("setLeft", "passtime", "userresponse", "lot_number", "pow_msg", "pow_sign", "geetest", "lang", "em"):
        assert key in body
    assert body["lot_number"] == LOT_NUMBER
    assert body["pow_msg"] == msg
    assert body["d2e4.c44df4"] == "c270ce"


def test_build_w_v4_prefixes_the_aes_half(monkeypatch: pytest.MonkeyPatch) -> None:
    modulus = _rsa_modulus()
    monkeypatch.setattr(p, "V4_RSA_MODULUS_HEX", modulus)
    msg, sign = p.solve_pow(LOT_NUMBER, CAPTCHA_ID, POW_DETAIL)
    aes_hex = p.encrypt_body(
        p.v4_body(
            lot_number=LOT_NUMBER,
            captcha_id=CAPTCHA_ID,
            user_response=12,
            passtime_ms=1100,
            pow_msg=msg,
            pow_sign=sign,
        ),
        p.session_key(7),
    )
    w = p.build_w_v4(LOT_NUMBER, CAPTCHA_ID, 12, 1100, 7, f"{msg}|{sign}")
    assert w.startswith(aes_hex)
    assert len(w) == len(aes_hex) + len(modulus) // 2 * 2


def test_build_w_v4_rejects_missing_fields() -> None:
    with pytest.raises(UnsolvableError):
        p.build_w_v4("", CAPTCHA_ID, 12, 1100, 7, "msg|sign")
    with pytest.raises(UnsolvableError):
        p.build_w_v4(LOT_NUMBER, CAPTCHA_ID, 12, 1100, 7, "")


def test_solvers_are_experimental_and_typed() -> None:
    from captcha.domain import CaptchaType
    from captcha.solvers.interactive.geetest import GeetestV3Solver, GeetestV4Solver

    assert GeetestV3Solver.info.types == (CaptchaType.GEETEST_V3,)
    assert GeetestV4Solver.info.types == (CaptchaType.GEETEST_V4,)
    assert GeetestV3Solver.info.experimental is True
    assert GeetestV4Solver.info.experimental is True
