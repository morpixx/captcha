"""Runtime protocol + LocalRuntime backed by py-mini-racer.

py-mini-racer's `eval` is synchronous and holds the GIL only during JS compile. We push
every call through `asyncio.to_thread` so the event loop keeps turning while a 500 ms
interrogator script runs. Each `LocalRuntime` instance owns one MiniRacer context —
sharing a context across concurrent callers would serialize them under mini-racer's
internal V8 isolate lock anyway, so Pool N of them if you need parallelism.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol, runtime_checkable

from captcha.domain.errors import ConfigError, JSRuntimeError

from captcha.adapters.jssandbox.cache import ScriptCache


@runtime_checkable
class Runtime(Protocol):
    """Async JS runtime contract.

    `exec_script` evaluates `code` in a context seeded with `context_globals`. Returns
    whatever the script's trailing expression evaluates to (JSON-serializable only).
    Raises `JSRuntimeError` on timeout / compile / runtime failure.
    """

    async def exec_script(
        self,
        code: str,
        context_globals: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> Any: ...

    async def close(self) -> None: ...


def _inject_globals(code: str, context_globals: dict[str, Any] | None) -> str:
    """Prepend a `var name = <json>;` block for each global, then run `code`.

    IIFE wrap keeps the inner script from polluting the shared context across calls.
    """
    if not context_globals:
        return f"(function(){{ {code} }})()"
    header_lines = []
    for name, value in context_globals.items():
        if not name.isidentifier():
            raise JSRuntimeError(
                "invalid js identifier for context global",
                context={"name": name},
            )
        header_lines.append(f"var {name} = {json.dumps(value)};")
    header = "\n".join(header_lines)
    return f"(function(){{ {header}\n{code} }})()"


class LocalRuntime:
    """In-process V8 via py-mini-racer.

    Call `close()` when the worker shuts the runtime pool down. Safe to reuse the same
    instance across tasks — mini-racer isolates globals when we always wrap in an IIFE.
    """

    def __init__(
        self,
        *,
        timeout_ms: int = 5000,
        max_memory_bytes: int = 256 * 1024 * 1024,
        cache: ScriptCache | None = None,
    ) -> None:
        try:
            from py_mini_racer import MiniRacer  # type: ignore[import-untyped]
        except ImportError as e:  # pragma: no cover - env check
            raise ConfigError(
                "py-mini-racer not installed",
                context={"hint": "uv add py-mini-racer"},
            ) from e
        self._MiniRacer = MiniRacer
        self._ctx: Any = MiniRacer()
        self._timeout_ms = timeout_ms
        self._max_memory = max_memory_bytes
        self._cache = cache or ScriptCache(capacity=256)
        self._closed = False
        try:
            self._ctx.set_hard_memory_limit(self._max_memory)
        except Exception:  # pragma: no cover - older mini-racer lacks this
            pass

    async def exec_script(
        self,
        code: str,
        context_globals: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> Any:
        if self._closed:
            raise JSRuntimeError("runtime closed")
        wrapped = _inject_globals(code, context_globals)
        # cache wrapped form; context globals bake into the hash, which is what we want —
        # same script + different sitekey = different compile.
        self._cache.get_or_compute(wrapped, lambda s: s)
        effective_timeout = timeout_ms if timeout_ms is not None else self._timeout_ms
        return await asyncio.to_thread(self._eval_sync, wrapped, effective_timeout)

    def _eval_sync(self, wrapped: str, timeout_ms: int) -> Any:
        try:
            return self._ctx.eval(wrapped, timeout=timeout_ms)
        except Exception as e:
            cls_name = type(e).__name__
            # mini-racer raises JSEvalException / JSTimeoutException / JSOOMException —
            # we classify by class-name string to avoid a hard import dep.
            if "Timeout" in cls_name:
                raise JSRuntimeError(
                    "js execution timed out",
                    context={"timeout_ms": timeout_ms},
                ) from e
            if "OOM" in cls_name or "Memory" in cls_name:
                raise JSRuntimeError(
                    "js out of memory",
                    context={"limit": self._max_memory},
                ) from e
            raise JSRuntimeError(
                "js eval failed",
                context={"error": str(e), "class": cls_name},
            ) from e

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # py-mini-racer >=0.12 has .close(); older versions don't.
            close_fn = getattr(self._ctx, "close", None)
            if callable(close_fn):
                await asyncio.to_thread(close_fn)
        except Exception:
            pass
        self._cache.clear()


def pick_runtime(
    *,
    sidecar_url: str | None = None,
    prefer: str = "auto",
    timeout_ms: int = 5000,
) -> Runtime:
    """Pick a runtime by env.

    `prefer`:
      - `local`   : LocalRuntime; raises if py-mini-racer missing
      - `sidecar` : SidecarClient; requires `sidecar_url`
      - `auto`    : LocalRuntime if importable, else SidecarClient if `sidecar_url` set,
                    else ConfigError.
    """
    from captcha.adapters.jssandbox.sidecar import SidecarClient

    if prefer == "sidecar":
        if not sidecar_url:
            raise ConfigError("sidecar runtime requested but no sidecar_url given")
        return SidecarClient(sidecar_url, default_timeout_ms=timeout_ms)
    if prefer == "local":
        return LocalRuntime(timeout_ms=timeout_ms)
    # auto
    try:
        return LocalRuntime(timeout_ms=timeout_ms)
    except ConfigError:
        if sidecar_url:
            return SidecarClient(sidecar_url, default_timeout_ms=timeout_ms)
        raise
