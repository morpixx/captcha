"""Unit tests for the GeeTest wire formats. No network, no solver run."""

from __future__ import annotations

import hashlib
import json
import random
from urllib.parse import unquote_plus

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from captcha.crypto import aes_cbc_decrypt
from captcha.domain.errors import ConfigError, UnsolvableError
from captcha.solvers.interactive.geetest import payload as p
from captcha.solvers.interactive.geetest import v3_payload as v3p

# Fixed vector: key is ASCII hex, IV is the constant the bundles hardcode.
FIXED_KEY = b"0123456789abcdef"
FIXED_AES_HEX = "db969b3a6dbc23676121c125c923870e"

V3_BODY = {
    "gt": "gt-1",
    "challenge": "c" * 32,
    "user_response": "100",
    "passtime_ms": 1200,
    "imgload_ms": 72,
}
V3_INPUT = {**V3_BODY, "rand_int": 42}
V3_AES_HEX = (
    "bc924518fcf2786d05115f01fc39ffb3b09e6aedd7734bd0874a8dd3761698d3434d355ab863eb0ff0d3deb4f4788198"
    "354ab0b881f234c2eca73fea5dfa8135721c8d8ef3dc910c1a8725498ca986cca4ccdbc506b5a7ec48b6afdb810feeb4"
    "5c806679299c215d3e896cb44b01ec25b38005f7dd1ca469bd4abe82902a2a469ac48dd35dfd18e48fd1853c43e549f3"
)
LOT_NUMBER = "f4744c44df4541b3be48c5c270ced20b"
CAPTCHA_ID = "captcha-id"
POW_DETAIL = {
    "version": "1",
    "bits": 0,
    "hashfunc": "md5",
    "datetime": "2025-02-03T21:47:56.826264+08:00",
}
CHALLENGE34 = "22ff2f326624b638791dfdef7892700d9w"


def _rsa_modulus() -> str:
    """A throwaway public modulus so the RSA half can be exercised offline."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    return format(key.public_key().public_numbers().n, "x")


# ---- shared crypto ----------------------------------------------------------


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


def test_custom_encoder_matches_the_v3_modified_base64() -> None:
    # The bit masks reorder the 6-bit groups, so this is not standard base64.
    assert p.custom_encode(b"abc") == "xEob"
    assert p.custom_encode(b"ab") == "xEo."
    assert p.custom_encode(b"a") == "xA.."
    assert p.custom_encode(bytes(16)) == "AAAAAAAAAAAAAAAAAAAAAA.."
    assert p.custom_encode(b"") == ""


def test_custom_encoder_keeps_the_alphabet_and_padding_contract() -> None:
    encoded = p.custom_encode(bytes(range(48)))
    assert len(encoded) == 64
    assert set(encoded) <= set(p.V3_ENCODER_ALPHABET)
    tail = p.custom_encode(b"\x01\x02")
    assert tail.endswith(".")
    assert p.custom_encode(b"\x01").endswith("..")


# ---- v3 body / H / aa --------------------------------------------------------


def test_v3_body_carries_the_required_keys() -> None:
    body = p.v3_body(**V3_BODY)
    assert set(body) == {"lang", "userresponse", "passtime", "imgload", "aa", "ep", "rp"}
    assert body["userresponse"] == "100"
    assert body["passtime"] == 1200
    # rp = md5(gt + challenge[:32] + passtime)
    assert body["rp"] == "dbf52b63286625cbcb67df06be926212"


def test_v3_answer_rp_uses_the_full_challenge_and_last_track_t() -> None:
    body = p.v3_body(
        gt="gt-1",
        challenge=CHALLENGE34,
        user_response="UR",
        passtime_ms=1200,
        imgload_ms=72,
        aa="AA",
        last_t=938,
    )
    expected = hashlib.md5(f"gt-1{CHALLENGE34}938".encode()).hexdigest()
    assert body["rp"] == expected


def test_v3_aes_half_is_stable() -> None:
    body = p.v3_body(**V3_BODY)
    assert p.encrypt_body(body, p.session_key(42)) == V3_AES_HEX


def test_v3_first_w_is_custom_aes_plus_rsa(monkeypatch: pytest.MonkeyPatch) -> None:
    modulus = _rsa_modulus()
    monkeypatch.setattr(v3p, "V3_RSA_MODULUS_HEX", modulus)
    body = p.v3_body(**V3_BODY)
    prefix = p.custom_encode(p.encrypt_body_bytes(body, p.session_key(42)))
    w = p.build_w_v3(**V3_INPUT)
    assert w.startswith(prefix)
    tail = w[len(prefix) :]
    assert len(tail) == len(modulus) // 2 * 2
    int(tail, 16)  # the RSA half is hex, not base64


def test_v3_answer_w_has_no_rsa_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    modulus = _rsa_modulus()
    monkeypatch.setattr(v3p, "V3_RSA_MODULUS_HEX", modulus)
    body = p.v3_body(
        gt="gt-1",
        challenge=CHALLENGE34,
        user_response="UR",
        passtime_ms=1200,
        imgload_ms=72,
        aa="AA",
        last_t=938,
    )
    w = p.build_w_v3(
        "gt-1",
        CHALLENGE34,
        "UR",
        1200,
        42,
        imgload_ms=72,
        aa="AA",
        last_t=938,
        with_rsa=False,
    )
    assert w == p.custom_encode(p.encrypt_body_bytes(body, p.session_key(42)))
    assert not w.endswith(modulus)


def test_v3_missing_modulus_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v3p, "V3_RSA_MODULUS_HEX", "")
    with pytest.raises(ConfigError):
        p.build_w_v3("gt-1", "c" * 32, "", 100, 1)


def test_build_w_v3_rejects_missing_fields() -> None:
    with pytest.raises(UnsolvableError):
        p.build_w_v3("", "c" * 32, "", 100, 1)
    with pytest.raises(UnsolvableError):
        p.build_w_v3("gt-1", "", "", 100, 1)


def test_userresponse_follows_h_with_a_seeded_rng() -> None:
    # Reference H from the public 2020-2025 analyses; the random picks are
    # pinned by the seed so this doubles as a regression vector.
    assert v3p.v3_user_response(58, CHALLENGE34, random.Random(7)) == "4414144168f"


def test_userresponse_uses_only_challenge_head_chars() -> None:
    rng = random.Random(11)
    response = v3p.v3_user_response(200, CHALLENGE34, rng)
    assert response
    assert set(response) <= set(CHALLENGE34[:32])
    assert set(response) <= set("".join(dict.fromkeys(CHALLENGE34[:32])))


def test_userresponse_rejects_a_short_challenge() -> None:
    with pytest.raises(UnsolvableError, match="34"):
        v3p.v3_user_response(58, "c" * 32)


def test_track_encoding_is_stable_for_a_seeded_track() -> None:
    points = v3p.track_points(120.0, random.Random(3))
    assert points[0][0] < 0
    assert points[-1][0] == 120
    encoded = v3p.encode_track(points)
    assert encoded.count("!!") == 2
    assert encoded == ".-.././/////./..-.,,(!!~uyyy(()!)((((((!))()!)(((!)y)st(!!m_K__FF9A?:NUXYTEMPUWR:QJNA_$-Z"


def test_getaa_splices_the_s_bytes_into_the_track() -> None:
    assert v3p.apply_track_cipher("abcdef", [12, 58, 98, 36, 43, 95, 62, 15, 12], "4142") == "abcdBeAf"


def test_getaa_returns_the_track_when_coefficients_are_missing() -> None:
    assert v3p.apply_track_cipher("abcdef", None, "4142") == "abcdef"
    assert v3p.apply_track_cipher("abcdef", [1, 2], "4142") == "abcdef"


# ---- v4 ---------------------------------------------------------------------


def test_lot_fields_build_the_three_group_nested_key() -> None:
    assert p.lot_fields(LOT_NUMBER) == {"d2e4": {"c44df4": {"f300": "c270ce"}}}


def test_lot_fields_requires_a_full_lot_number() -> None:
    with pytest.raises(UnsolvableError, match="lot_number"):
        p.lot_fields("short")


def test_v4_userresponse_formula() -> None:
    assert p.v4_user_response(96) == pytest.approx(96 / 1.0059466666666665 + 2)
    assert p.v4_user_response(0) == 2


def test_v4_passtime_is_clamped_into_the_widget_range() -> None:
    assert p.v4_passtime(-1) == 600
    assert p.v4_passtime(900) == 900
    assert p.v4_passtime(99_999) == 1200


def test_solve_pow_satisfies_the_bit_prefix() -> None:
    msg, sign = p.solve_pow(LOT_NUMBER, CAPTCHA_ID, {**POW_DETAIL, "bits": 8}, seed=3)
    assert msg.startswith(f"1|8|md5|{POW_DETAIL['datetime']}|{CAPTCHA_ID}|{LOT_NUMBER}||")
    assert sign == hashlib.md5(msg.encode()).hexdigest()
    assert sign.startswith("00")


def test_solve_pow_rejects_unknown_hash() -> None:
    with pytest.raises(UnsolvableError):
        p.solve_pow(LOT_NUMBER, CAPTCHA_ID, {**POW_DETAIL, "hashfunc": "sha3"})


def test_v4_body_carries_the_public_schema_keys() -> None:
    msg, sign = p.solve_pow(LOT_NUMBER, CAPTCHA_ID, POW_DETAIL)
    body = p.v4_body(
        lot_number=LOT_NUMBER,
        captcha_id=CAPTCHA_ID,
        left=12.0,
        passtime_ms=1100,
        pow_msg=msg,
        pow_sign=sign,
    )
    for key in (
        "bzj5",
        "pow_msg",
        "pow_sign",
        "biht",
        "device_id",
        "em",
        "gee_guard",
        "ep",
        "geetest",
        "lang",
        "lot_number",
        "passtime",
        "setLeft",
        "userresponse",
    ):
        assert key in body
    assert body["d2e4"]["c44df4"]["f300"] == "c270ce"
    assert body["setLeft"] == 12.0
    assert body["userresponse"] == pytest.approx(12 / 1.0059466666666665 + 2)
    assert body["passtime"] == 1100


def test_v4_body_rejects_non_slide_risk_types() -> None:
    msg, sign = p.solve_pow(LOT_NUMBER, CAPTCHA_ID, POW_DETAIL)
    with pytest.raises(UnsolvableError, match="risk_type"):
        p.v4_body(
            lot_number=LOT_NUMBER,
            captcha_id=CAPTCHA_ID,
            left=12.0,
            passtime_ms=1100,
            pow_msg=msg,
            pow_sign=sign,
            risk_type="icon",
        )


def test_v4_pt0_is_the_urlencoded_raw_json() -> None:
    msg, sign = p.solve_pow(LOT_NUMBER, CAPTCHA_ID, POW_DETAIL)
    body = p.v4_body(
        lot_number=LOT_NUMBER,
        captcha_id=CAPTCHA_ID,
        left=12.0,
        passtime_ms=1100,
        pow_msg=msg,
        pow_sign=sign,
    )
    w = p.encrypt_w(body, "0")
    assert json.loads(unquote_plus(w)) == body
    assert not w.startswith("{")  # urlencoded, not raw JSON


def test_v4_pt2_is_rejected_as_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="pt"):
        p.encrypt_w({"a": 1}, "2")


def test_build_w_v4_pt1_is_aes_plus_rsa(monkeypatch: pytest.MonkeyPatch) -> None:
    modulus = _rsa_modulus()
    monkeypatch.setattr(p, "V4_RSA_MODULUS_HEX", modulus)
    msg, sign = p.solve_pow(LOT_NUMBER, CAPTCHA_ID, POW_DETAIL)
    body = p.v4_body(
        lot_number=LOT_NUMBER,
        captcha_id=CAPTCHA_ID,
        left=12.0,
        passtime_ms=1100,
        pow_msg=msg,
        pow_sign=sign,
    )
    aes_hex = p.encrypt_body(body, p.session_key(7))
    w = p.build_w_v4(LOT_NUMBER, CAPTCHA_ID, 12.0, 1100, 7, f"{msg}|{sign}", key=p.session_key(7))
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
