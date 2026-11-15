from __future__ import annotations

import base64
import json
import random
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from helpers import PAGE_URL, SURL, make_task

from captcha.domain.errors import SensorError
from captcha.solvers import SolverContext
from captcha.solvers.funcaptcha import bda as bda_mod
from captcha.solvers.funcaptcha import envelope, sensor, settings
from captcha.solvers.funcaptcha.hashes import feature_hash, ife_hash

BUILD_ID = "3f8a1c92-1a2b-4c3d-8e4f-0123456789ab"
#: b64-decoded lengths of the four envelope segments: iv, tag, rsa-2048 block, ct.
SEGMENT_CHARS = (16, 24, 344)


def keypair() -> tuple[Any, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return key, base64.b64encode(der).decode()


def cfg_for(rsa_public_key: str = "") -> settings.ArkoseSettings:
    return settings.ArkoseSettings(
        surl=SURL,
        public_key="3EE79F8D",
        build_id=BUILD_ID,
        enforcement_hash="2d1c8a89671586563cb793ae2399b954",
        rsa_public_key=rsa_public_key,
    )


def sensor_cfg() -> sensor.SensorConfig:
    return sensor.SensorConfig(
        surl=SURL,
        language="en-US",
        title="Sign in",
        location_href=PAGE_URL,
        enforcement_hash="2d1c8a89671586563cb793ae2399b954",
    )


# --- sensor snapshot ---------------------------------------------------------


def test_snapshot_fields_are_complete_and_unique():
    snap = sensor.build_snapshot(sensor_cfg(), build_id=BUILD_ID, rng=random.Random(11))

    keys = [key for key, _value in snap.enhanced_fp]
    assert len(keys) == len(set(keys)) == 91
    assert {"webgl_hash_webgl", "webgl_unmasked_renderer", "screen_pixel_depth", "6a62b2a558"} <= set(keys)
    assert snap.f == feature_hash(snap.fe)
    assert snap.ife_hash == ife_hash(snap.fe)
    assert len(snap.f) == len(snap.ife_hash) == 32


def test_snapshot_derived_fields_depend_on_the_sampled_device():
    first = sensor.build_snapshot(sensor_cfg(), rng=random.Random(1))
    second = sensor.build_snapshot(sensor_cfg(), rng=random.Random(2))

    assert first.f != second.f
    # 6a62b2a558 is the enforcement hash we scraped, not a random value.
    enforced = dict(first.enhanced_fp)["6a62b2a558"]
    assert enforced == "2d1c8a89671586563cb793ae2399b954"


def test_snapshot_is_reproducible_for_a_seed():
    first = sensor.build_snapshot(sensor_cfg(), build_id=BUILD_ID, rng=random.Random(7))
    second = sensor.build_snapshot(sensor_cfg(), build_id=BUILD_ID, rng=random.Random(7))

    assert first.f == second.f
    assert first.fe == second.fe
    assert [key for key, _ in first.enhanced_fp] == [key for key, _ in second.enhanced_fp]


def test_jsbd_carries_the_page_title_and_fe_entries_are_prefixed():
    snap = sensor.build_snapshot(sensor_cfg(), rng=random.Random(3))

    assert json.loads(snap.jsbd)["DT"] == "Sign in"
    assert any(entry.startswith("S:") for entry in snap.fe)
    assert any(entry.startswith("PR:") for entry in snap.fe)
    assert snap.font_flag is False


def test_fingerprint_overrides_screen_and_renderer():
    class Fingerprint:
        screen_width = 2560
        screen_height = 1440
        hardware_concurrency = 16
        device_memory = 16
        webgl_renderer = "ANGLE (NVIDIA, NVIDIA RTX 4090)"
        webgl_vendor = "Google Inc. (NVIDIA)"

    snap = sensor.build_snapshot(sensor_cfg(), rng=random.Random(5), fingerprint=Fingerprint())
    enhanced = dict(snap.enhanced_fp)

    assert enhanced["webgl_unmasked_renderer"] == "ANGLE (NVIDIA, NVIDIA RTX 4090)"
    assert any(entry == "S:2560,1440" for entry in snap.fe)


def test_marshalled_bda_is_compact_json_with_the_expected_envelope_keys():
    snap = sensor.build_snapshot(sensor_cfg(), build_id=BUILD_ID, rng=random.Random(9))

    plaintext = sensor.marshal_bda(snap, timestamp=1_712_000_000)
    items = json.loads(plaintext)

    assert [item["key"] for item in items] == [
        "api_type",
        "f",
        "n",
        "wh",
        "enhanced_fp",
        "fe",
        "ife_hash",
        "jsbd",
        "c",
    ]
    assert items[0]["value"] == "js"
    assert '", ' not in plaintext
    assert '": ' not in plaintext
    assert json.loads(base64.b64decode(items[2]["value"])) == 1_712_000_000


# --- envelope ----------------------------------------------------------------


def test_envelope_layout_and_round_trip():
    key, spki = keypair()
    plaintext = '{"api_type":"js","f":"abc"}'

    sealed = envelope.encrypt_bda(plaintext, spki)

    iv_b64, tag_b64, rsa_b64 = (sealed[:16], sealed[16:40], sealed[40:384])
    cipher_b64 = sealed[384:]
    assert (len(iv_b64), len(tag_b64), len(rsa_b64)) == SEGMENT_CHARS
    assert base64.b64decode(rsa_b64)[0] == 0x00 or len(base64.b64decode(rsa_b64)) == 256

    wrapped = base64.b64decode(rsa_b64)
    content_key = key.decrypt(
        wrapped,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    assert len(content_key) == 32
    recovered = AESGCM(content_key).decrypt(
        base64.b64decode(iv_b64),
        base64.b64decode(cipher_b64) + base64.b64decode(tag_b64),
        None,
    )
    assert recovered.decode() == plaintext


def test_envelope_rejects_a_non_spki_key():
    with pytest.raises(SensorError) as exc:
        envelope.encrypt_bda("x", "bm90LWEta2V5")

    assert "SPKI" in exc.value.message


def test_envelope_rejects_an_empty_key():
    with pytest.raises(SensorError):
        envelope.encrypt_bda("x", "")


# --- builder paths -----------------------------------------------------------


async def test_recorded_bda_is_used_verbatim():
    _key, spki = keypair()
    task = make_task(payload={"bda_plaintext": '{"api_type":"js","recorded":true}'})

    produced = await bda_mod.build_bda(task, SolverContext(), cfg=cfg_for(spki))

    assert produced.source == "recorded"
    assert produced.field == "c"
    assert produced.plaintext == '{"api_type":"js","recorded":true}'
    assert produced.value and produced.value != produced.plaintext


async def test_js_hook_produces_the_plaintext():
    _key, spki = keypair()
    seen: dict[str, object] = {}

    async def hook(*, task, settings, ctx):
        seen["pk"] = settings.public_key
        return '{"api_type":"js","from":"jssandbox"}'

    produced = await bda_mod.build_bda(
        make_task(),
        SolverContext(extra={"funcaptcha_bda_builder": hook}),
        cfg=cfg_for(spki),
    )

    assert produced.source == "js"
    assert json.loads(produced.plaintext)["from"] == "jssandbox"
    assert seen["pk"] == "3EE79F8D"


async def test_js_hook_without_output_is_sensor_error():
    _key, spki = keypair()

    async def hook(*, task, settings, ctx):
        return ""

    with pytest.raises(SensorError):
        await bda_mod.build_bda(
            make_task(),
            SolverContext(extra={"funcaptcha_bda_builder": hook}),
            cfg=cfg_for(spki),
        )


async def test_sensor_path_is_the_default():
    _key, spki = keypair()

    produced = await bda_mod.build_bda(make_task(), SolverContext(), cfg=cfg_for(spki), rng=random.Random(4))

    assert produced.source == "sensor"
    parsed = {item["key"]: item["value"] for item in json.loads(produced.plaintext)}
    assert parsed["api_type"] == "js"
    enhanced = {item["key"]: item["value"] for item in parsed["enhanced_fp"]}
    assert enhanced["lfasdgs"] == BUILD_ID


async def test_unpinned_missing_rsa_is_reported_not_faked():
    task = make_task()

    with pytest.raises(SensorError) as exc:
        await bda_mod.build_bda(task, SolverContext(), cfg=cfg_for(""))

    assert "pin it" in exc.value.context["hint"]
