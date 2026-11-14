"""Sensor-builder base. One subclass per antibot engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from captcha.domain.enums import AntibotEngine


@dataclass(slots=True)
class BuildContext:
    """Everything a builder needs to assemble one sensor payload.

    Fields are intentionally loose-typed — fingerprint / behavior packages own
    their own structs; this layer just carries them through.
    """

    engine: AntibotEngine
    site_url: str
    fingerprint: Any
    """`captcha.adapters.profiles.Fingerprint` — resolved per session."""
    behavior: Any
    """`captcha.behavior.BehaviorTrack` — mouse/keyboard/touch payloads."""
    challenge_js: str | None = None
    """Raw challenge JS scraped from the target. Needed by engines whose
    payload is produced by executing obfuscated client code (Cloudflare,
    some Akamai modes)."""
    timestamp_ms: int | None = None
    """Server-provided or wall-clock ms. None means builder picks its own."""
    cookies: dict[str, str] = field(default_factory=dict)
    """Pre-existing cookies from the challenge page (bm_sz, _abck seed,
    datadome, ...)."""
    headers: dict[str, str] = field(default_factory=dict)
    """Headers from the challenge response — some engines mix them into the
    payload (ETag, x-dd-b)."""
    extra: dict[str, Any] = field(default_factory=dict)
    """Engine-specific knobs (abck_version, kpsdk_version, px_app_id, ...)."""


class SensorBuilder(ABC):
    """Abstract per-engine payload builder.

    Subclasses MUST set `engine` and implement `build`. Register with the
    `@register_builder(engine)` decorator in `registry` so the solver layer
    can look them up by `AntibotEngine`.
    """

    engine: AntibotEngine

    #: Human label for logging. Overridden by subclasses.
    name: str = "sensor-builder"

    #: True when `build` needs `challenge_js` in the context.
    requires_challenge_js: bool = False

    #: True when `build` executes JS (depends on captcha-js-runtime).
    uses_js_runtime: bool = False

    @abstractmethod
    async def build(self, ctx: BuildContext) -> dict[str, Any] | str:
        """Produce the payload the engine expects.

        Return type varies by engine:
        - Akamai: `str` (base64/urlencoded `sensor_data`).
        - DataDome: `dict` (JSON body for /js/ POST).
        - Kasada: `dict` with `x-kpsdk-cd` / `x-kpsdk-cr` / `cookies`.
        - PerimeterX: `dict` with `_px`, `_pxhd`, `_pxvid`.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} engine={self.engine.value}>"
