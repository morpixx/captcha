"""Fingerprint profile struct. One record = one coherent browser/TLS identity."""

from __future__ import annotations

import msgspec


class Profile(msgspec.Struct, frozen=True, kw_only=True):
    """A single pinned browser+TLS fingerprint.

    Every field here is observable server-side and must agree with the others:
    a Firefox user-agent with a Chrome JA3 is a dead giveaway.
    """

    id: str
    name: str

    # TLS / HTTP transport layer
    ja3: str
    ja4: str
    http2_settings: dict[str, int]
    alpn: list[str]
    extensions: list[int]

    # Browser rendering layer
    canvas_hash: str
    webgl_renderer: str
    webgl_vendor: str
    audio_hash: str
    fonts: list[str]

    # Env / headers
    timezone: str
    webrtc_mode: str  # "default" | "disable" | "proxy"
    user_agent: str
    accept_language: str

    # Loose metadata used by picker filters (browser, os, version, locale...).
    tags: dict[str, str] = msgspec.field(default_factory=dict)
