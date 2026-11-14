"""Contract tests for the per-type solution projection.

A 2captcha client reads one specific key per captcha type. If the gateway emits the
generic field name instead, the task succeeds and the client still fails — so these
assertions are pinned per family, not per implementation detail.
"""

from __future__ import annotations

import pytest

from captcha_common.enums import CaptchaType

from captcha_gateway.solution import build_solution

SITE = "https://shop.example.com/checkout"


def _token(**extra):
    return build_solution(CaptchaType.RECAPTCHA_V2, {"token": "T", "extra": extra}, site_url=SITE)


def test_all_types_project_something():
    """No type may fall through to an empty solution once a solver returns a value."""
    probe = {"token": "T", "text": "x", "indices": [1], "coords": [(1.0, 2.0)], "offset": 1.0,
             "angle": 1.0, "bbox": (1.0, 2.0, 3.0, 4.0), "extra": {"challenge": "c", "validate": "v"}}
    for ct in CaptchaType:
        out = build_solution(ct, probe, site_url=SITE)
        assert out, f"{ct.value} produced no solution"


def test_recaptcha_uses_google_key():
    assert _token()["gRecaptchaResponse"] == "T"
    assert "token" not in _token()


def test_hcaptcha_adds_respkey_and_ua():
    out = build_solution(
        CaptchaType.HCAPTCHA,
        {"token": "T", "extra": {"respKey": "R", "userAgent": "UA"}},
        site_url=SITE,
    )
    assert out == {"gRecaptchaResponse": "T", "respKey": "R", "userAgent": "UA"}


def test_geetest_v3_derives_seccode():
    out = build_solution(
        CaptchaType.GEETEST_V3,
        {"token": None, "extra": {"challenge": "c", "validate": "v"}},
        site_url=SITE,
    )
    assert out == {"challenge": "c", "validate": "v", "seccode": "v|jordan"}


def test_geetest_v4_emits_five_keys():
    extra = {"captcha_id": "a", "lot_number": "b", "pass_token": "c", "gen_time": "d", "captcha_output": "e"}
    out = build_solution(CaptchaType.GEETEST_V4, {"token": None, "extra": extra}, site_url=SITE)
    assert set(out) == set(extra)


@pytest.mark.parametrize(
    "ct",
    [CaptchaType.DATADOME_FULL, CaptchaType.AKAMAI_SENSOR, CaptchaType.KASADA, CaptchaType.PERIMETERX],
)
def test_cookie_engines_emit_both_shapes(ct):
    out = build_solution(ct, {"token": None, "extra": {"cookies": {"cid": "X"}}}, site_url=SITE)
    assert out["cookies"] == {"cid": "X"}
    assert out["domains"] == {"shop.example.com": {"cookies": {"cid": "X"}}}


def test_cf_challenge_emits_clearance():
    out = build_solution(
        CaptchaType.CF_CHALLENGE,
        {"token": None, "extra": {"cookies": {"cf_clearance": "C"}}},
        site_url=SITE,
    )
    assert out["cf_clearance"] == "C"
    assert out["token"] == "C"


def test_text_and_grid_keep_vendor_keys():
    assert build_solution(CaptchaType.IMAGE_TO_TEXT, {"token": None, "text": "a7"}, site_url=SITE) == {"text": "a7"}
    assert build_solution(CaptchaType.CLASSIFICATION_GRID, {"token": None, "indices": [1, 4]}, site_url=SITE) == {
        "objects": [1, 4]
    }


def test_nested_data_family_is_not_flattened():
    out = build_solution(CaptchaType.FRIENDLY, {"token": "T", "extra": {}}, site_url=SITE)
    assert out["data"]["token"] == "T"


def test_unknown_result_is_none():
    assert build_solution(CaptchaType.HCAPTCHA, None, site_url=SITE) is None
    assert build_solution(CaptchaType.HCAPTCHA, {"extra": {}}, site_url=SITE) is None
