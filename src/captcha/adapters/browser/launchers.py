"""Backend selection: camoufox > patchright > playwright.

Resolved once at `Pool.launch()`. Each launcher returns an awaitable factory producing
a `playwright.async_api.Browser`-compatible object. camoufox and patchright expose the
same `async_playwright()` entrypoint as playwright itself, so the downstream Session
code is backend-agnostic.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Literal

from captcha.domain.errors import ConfigError

BackendName = Literal["camoufox", "patchright", "playwright"]


@dataclass(frozen=True)
class Launcher:
    name: BackendName
    # async context manager: `async with factory() as pw: pw.chromium.launch(...)`
    factory: Any
    # 'chromium' or 'firefox' — camoufox is Firefox-based.
    engine: Literal["chromium", "firefox"]


def _try_import(mod: str) -> Any | None:
    try:
        return importlib.import_module(mod)
    except ImportError:
        return None


def pick_launcher(prefer: BackendName | None = None) -> Launcher:
    """Return the best available launcher.

    `prefer=None` → camoufox > patchright > playwright.
    `prefer="playwright"` → force vanilla (dev/testing).
    Raises `ConfigError` if even vanilla playwright is missing.
    """
    if prefer == "camoufox":
        camoufox = _try_import("camoufox.async_api") or _try_import("camoufox")
        if camoufox is None:
            raise ConfigError("camoufox requested but not installed")
        return _camoufox_launcher(camoufox)

    if prefer == "patchright":
        pat = _try_import("patchright.async_api")
        if pat is None:
            raise ConfigError("patchright requested but not installed")
        return Launcher(name="patchright", factory=pat.async_playwright, engine="chromium")

    if prefer == "playwright":
        pw = _try_import("playwright.async_api")
        if pw is None:
            raise ConfigError("playwright not installed")
        return Launcher(name="playwright", factory=pw.async_playwright, engine="chromium")

    # auto
    camoufox = _try_import("camoufox.async_api") or _try_import("camoufox")
    if camoufox is not None:
        return _camoufox_launcher(camoufox)

    pat = _try_import("patchright.async_api")
    if pat is not None:
        return Launcher(name="patchright", factory=pat.async_playwright, engine="chromium")

    pw = _try_import("playwright.async_api")
    if pw is None:
        raise ConfigError(
            "no browser backend available",
            context={"hint": "install playwright, patchright, or camoufox"},
        )
    return Launcher(name="playwright", factory=pw.async_playwright, engine="chromium")


def _camoufox_launcher(camoufox_mod: Any) -> Launcher:
    """camoufox ships its own async api; wrap it so callers see the same shape.

    camoufox's `AsyncCamoufox` is itself an async context manager that yields a Browser.
    We adapt to the `async with factory() as pw: pw.firefox.launch(...)` shape the Pool
    expects by wrapping in a tiny shim exposing `.firefox.launch()`.
    """
    AsyncCamoufox = getattr(camoufox_mod, "AsyncCamoufox", None)
    if AsyncCamoufox is None:
        raise ConfigError("camoufox module missing AsyncCamoufox entrypoint")

    class _CamoufoxFactory:
        def __init__(self) -> None:
            self._cam: Any = None

        async def __aenter__(self) -> _CamoufoxFactory:
            # camoufox's context manager yields a Browser directly; we hold the ref
            # and expose it through `.firefox.launch()` as a passthrough.
            self._cam = AsyncCamoufox(headless=True)
            self._browser = await self._cam.__aenter__()
            return self

        async def __aexit__(self, *exc: Any) -> None:
            if self._cam is not None:
                await self._cam.__aexit__(*exc)

        # Shape alias so Pool code can say `pw.firefox.launch()` uniformly.
        @property
        def firefox(self) -> Any:
            browser = self._browser

            class _Already:
                @staticmethod
                async def launch(**_kwargs: Any) -> Any:
                    return browser

            return _Already()

    return Launcher(name="camoufox", factory=_CamoufoxFactory, engine="firefox")
