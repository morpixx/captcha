"""BDA sensor: the browser fingerprint Arkose expects, assembled in Python.

Arkose's bda is not a token you can copy — it is a ~90-field telemetry object
the loader builds from live browser probes. This module builds the same object
from our own data: a sampled device (devices.py), a per-solve identity, and the
derived hashes the bundle recomputes (f, ife_hash, webgl_hash_webgl,
network_info_rtt_type, ...). The wired tables live in fields.py.

Provenance: public RE of the deobfuscated api.js / stack-VM (2026-07). Nothing
here claims to have run Arkose's loader: it is a synthesised sensor snapshot,
reported as such in SolverResult.extra['bda_source'].
"""

from __future__ import annotations

import base64
import json
import random
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

from . import fields
from .devices import Device, sample_device
from .hashes import feature_hash, ife_hash, md5_hex, salted

LANGUAGES = (
    ("en-US", "en-US,en", 55),
    ("en-GB", "en-GB,en", 12),
    ("en", "en", 8),
    ("es-ES", "es-ES,es", 4),
    ("pt-BR", "pt-BR,pt", 4),
    ("de-DE", "de-DE,de", 4),
    ("fr-FR", "fr-FR,fr", 4),
    ("it-IT", "it-IT,it", 3),
    ("ru-RU", "ru-RU,ru", 3),
    ("tr-TR", "tr-TR,tr", 3),
)
TZ_OFFSETS = (-180, -180, -120, -120, -60, 0, 0, 60, 300, 300, 360, 420, 420, 480, 480, -480, -540, -330, -240)
FONT_SETS = (
    "Arial,Arial Black,Arial Narrow,Calibri,Cambria,Comic Sans MS,Consolas,Courier,Courier New,Georgia,"
    "Impact,Lucida Console,Segoe UI,Segoe UI Symbol,Tahoma,Times,Times New Roman,Trebuchet MS,Verdana,Wingdings",
    "Arial,Arial Black,Calibri,Comic Sans MS,Consolas,Courier New,Georgia,Impact,Microsoft Sans Serif,"
    "MS Gothic,MS PGothic,Palatino Linotype,Segoe UI,Tahoma,Times New Roman,Verdana",
    "Arial,Arial Black,Calibri,Cambria,Comic Sans MS,Consolas,Courier New,Georgia,Impact,Lucida Console,"
    "Marlett,Microsoft Sans Serif,MS Gothic,Palatino Linotype,Segoe Print,Segoe UI,Sylfaen,Tahoma,Verdana",
)
VOICE_SETS = (
    (("Microsoft David - English (United States)", "en-US"), ("Microsoft Zira - English (United States)", "en-US")),
    (("Microsoft Zira - English (United States)", "en-US"), ("Microsoft David - English (United States)", "en-US")),
    (("Microsoft David - English (United States)", "en-US"), ("Microsoft Mark - English (United States)", "en-US")),
)
PDF_PLUGINS = "Chrome PDF Viewer,Chromium PDF Viewer,Microsoft Edge PDF Viewer,PDF Viewer,WebKit built-in PDF"


@dataclass(frozen=True, slots=True)
class SensorConfig:
    """What the BDA needs to know about the page the widget sits on."""

    surl: str
    language: str = "en-US"
    title: str = ""
    location_href: str = ""
    document_referrer: str | None = None
    enforcement_hash: str = ""


@dataclass(frozen=True, slots=True)
class SensorSnapshot:
    fe: list[str]
    f: str
    ife_hash: str
    enhanced_fp: list[tuple[str, Any]]
    jsbd: str
    #: fb is emitted when one of the font/extension probes flipped a flag.
    font_flag: bool = False


def build_snapshot(
    cfg: SensorConfig,
    *,
    build_id: str = "",
    rng: random.Random | None = None,
    fingerprint: Any = None,
) -> SensorSnapshot:
    """Sample one device + identity and emit the whole BDA sensor object."""
    rand = rng or random.Random()
    device = _device_for(sample_device(rand), fingerprint=fingerprint)
    identity = _identity(rand, cfg.language)
    fe = _fe_list(device, identity)
    enhanced = fields.enhanced_fp(
        identity=identity,
        device=device,
        cfg_surl=cfg.surl,
        language=cfg.language,
        location_href=cfg.location_href,
        document_referrer=cfg.document_referrer,
        enforcement_hash=cfg.enforcement_hash,
        build_id=build_id,
        rand=rand,
    )
    return SensorSnapshot(
        fe=fe,
        f=feature_hash(fe),
        ife_hash=ife_hash(fe),
        enhanced_fp=enhanced,
        jsbd=fields.jsbd(cfg.title),
        font_flag=_flag(fe, "FOS") or _flag(fe, "FB") or _flag(fe, "FR"),
    )


def marshal_bda(snapshot: SensorSnapshot, *, timestamp: float | None = None) -> str:
    """Serialize the BDA the way the loader does: compact, ASCII-escaped."""
    now = int(timestamp if timestamp is not None else time.time())
    wh = uuid.uuid4().hex + "|" + md5_hex(str(now))
    items: list[tuple[str, Any]] = [
        ("api_type", "js"),
        ("f", snapshot.f),
        ("n", base64.b64encode(str(now).encode()).decode()),
        ("wh", wh),
        ("enhanced_fp", [_item(k, v) for k, v in snapshot.enhanced_fp]),
    ]
    if snapshot.font_flag:
        items.append(("fb", 1))
    items += [
        ("fe", snapshot.fe),
        ("ife_hash", snapshot.ife_hash),
        ("jsbd", snapshot.jsbd),
        ("c", "Copyright (c) 2026 Arkose Labs. All Rights Reserved."),
    ]
    return json.dumps([_item(k, v) for k, v in items], separators=(",", ":"), ensure_ascii=True)


def _item(key: str, value: Any) -> dict[str, Any]:
    return {"key": key, "value": value}


def _identity(rand: random.Random, language: str) -> dict[str, Any]:
    salt = rand.getrandbits(128).to_bytes(16, "big").hex()
    tag, full = _language(rand, language)
    voices = rand.choice(VOICE_SETS)
    default_voice = voices[0][0] + " || " + voices[0][1]
    voices_hash = md5_hex(",".join(f"{name},{lang}" for name, lang in voices))
    return {
        "salt": salt,
        "machine_hash": salted("machine_hash", salt),
        "7541c2s": salted("7541c2s", salt),
        "1f220c9": salted("1f220c9", salt),
        "c2d2015": salted("c2d2015", salt),
        "05d3d24": salted("05d3d24", salt),
        "83eb055": salted("83eb055", salt),
        "speech_default_voice": default_voice,
        "speech_voices_hash": voices_hash,
        "tz_offset": rand.choice(TZ_OFFSETS),
        "language_tag": tag,
        "languages": full,
        "font_list": rand.choice(FONT_SETS),
        "cfp": rand.randint(-2147483648, 2147483647),
    }


def _language(rand: random.Random, preferred: str) -> tuple[str, str]:
    for tag, full, _weight in LANGUAGES:
        if tag == preferred:
            return tag, full
    weight_total = sum(weight for _t, _f, weight in LANGUAGES)
    roll = rand.randrange(weight_total)
    for tag, full, weight in LANGUAGES:
        roll -= weight
        if roll < 0:
            return tag, full
    return LANGUAGES[0][0], LANGUAGES[0][1]


def _device_for(device: Device, *, fingerprint: Any) -> Device:
    """Overlay whatever the worker's fingerprint profile pins; keep the rest sampled."""
    if fingerprint is None:
        return device
    update: dict[str, Any] = {}
    for attr in ("screen_width", "screen_height", "hardware_concurrency", "device_memory"):
        value = getattr(fingerprint, attr, None)
        if isinstance(value, int) and value > 0:
            update[attr] = value
    renderer = getattr(fingerprint, "webgl_renderer", None)
    if isinstance(renderer, str) and renderer:
        update["gl_renderer"] = renderer
        update["unmasked_vendor"] = getattr(fingerprint, "webgl_vendor", device.unmasked_vendor)
    return replace(device, **update) if update else device


def _dpr(value: float) -> str:
    return str(int(value)) if float(value) == int(value) else repr(float(value))


def _fe_list(device: Device, identity: dict[str, Any]) -> list[str]:
    """The probe list the token endpoint re-hashes as f."""
    return [
        "DNT:unknown",
        "L:" + identity["language_tag"],
        f"D:{device.color_depth}",
        "PR:" + _dpr(device.device_pixel_ratio),
        f"S:{device.screen_width},{device.screen_height}",
        f"AS:{device.avail_width},{device.avail_height}",
        f"TO:{identity['tz_offset']}",
        "SS:true",
        "LS:true",
        "IDB:true",
        "B:false",
        "ODB:false",
        "CPUC:unknown",
        "PK:Win32",
        f"CFP:{identity['cfp']}",
        "FR:false",
        "FOS:false",
        "FB:false",
        "JSF:" + identity["font_list"],
        "P:" + PDF_PLUGINS,
        "T:0,false,false",
        f"H:{device.hardware_concurrency}",
        "SWF:false",
    ]


def _flag(fe: list[str], key: str) -> bool:
    prefix = key + ":"
    for entry in fe:
        if entry.startswith(prefix):
            return entry[len(prefix) :] != "false"
    return False
