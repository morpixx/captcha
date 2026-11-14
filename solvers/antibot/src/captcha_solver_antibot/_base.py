"""Shared helpers for antibot-engine solvers.

Every engine does a variation on the same three steps:

    1. build a sensor payload (per-engine builder from `captcha_sensors`)
    2. POST it to the engine endpoint, usually with specific headers/cookies
    3. extract a token / cookie / integrity claim from the response

`AntibotBase` hides the plumbing so each solver module only owns the
engine-specific bits (endpoint URL, payload shape, response parsing).
"""

from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Any

from captcha_common import CaptchaError, Task
from captcha_common.enums import AntibotEngine
from captcha_common.errors import ConfigError, SensorError
from captcha_solver_base import Solver, SolverContext


class AntibotBase(Solver):
    """Thin mixin over `Solver` with engine helpers.

    Subclasses MUST define `engine: AntibotEngine`. They typically do not
    override `prepare`; the sensor registry and HTTP client come in through
    `SolverContext`.
    """

    engine: AntibotEngine

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def require_ctx(ctx: SolverContext, *fields: str) -> None:
        """Fail fast if the worker handed us an underpopulated context.

        A missing `http` or `sensors` is a worker misconfig, not a task
        failure; raising `ConfigError` lets the worker classify it that way.
        """
        missing = [f for f in fields if getattr(ctx, f, None) is None]
        if missing:
            raise ConfigError(
                f"SolverContext missing required fields: {', '.join(missing)}",
                context={"missing": missing},
            )

    def build_sensor(
        self,
        ctx: SolverContext,
        challenge_payload: dict[str, Any],
        *,
        timestamp_ms: int | None = None,
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Any:
        """Resolve the per-engine builder and return a ready `BuildContext`.

        The caller awaits ``builder.build(build_ctx)`` because builders are
        async — some run JS in a sidecar or hit a rendering service.
        """
        self.require_ctx(ctx, "sensors")

        try:
            builder = ctx.sensors.get(self.engine)
        except AttributeError as exc:  # pragma: no cover — registry contract
            raise ConfigError(
                "sensors registry does not expose .get(engine)",
                context={"engine": self.engine.value},
            ) from exc

        # Deferred import: captcha_sensors is a workspace dep but we don't
        # want its import graph on every solver load path.
        from captcha_sensors.base import BuildContext

        build_ctx = BuildContext(
            engine=self.engine,
            site_url=challenge_payload.get("site_url") or challenge_payload.get("url") or "",
            fingerprint=ctx.fingerprint_store.pick() if ctx.fingerprint_store else None,
            behavior=challenge_payload.get("behavior"),
            challenge_js=challenge_payload.get("challenge_js"),
            timestamp_ms=timestamp_ms,
            cookies=dict(cookies or challenge_payload.get("cookies") or {}),
            headers=dict(headers or challenge_payload.get("headers") or {}),
            extra=dict(extra or challenge_payload.get("extra") or {}),
        )
        return builder, build_ctx

    async def post_sensor(
        self,
        ctx: SolverContext,
        url: str,
        payload: dict[str, Any] | str | bytes,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> Any:
        """POST a built payload through the shared HTTP client.

        Dispatches on payload type so engines can pass JSON dicts, raw
        base64/urlencoded sensor strings, or pre-serialized bytes.
        """
        self.require_ctx(ctx, "http")
        kwargs: dict[str, Any] = {"headers": headers, "cookies": cookies}
        if isinstance(payload, dict):
            kwargs["json"] = payload
        else:
            kwargs["data"] = payload
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        return await ctx.http.post(url, **kwargs)

    @staticmethod
    def parse_cookies(set_cookie_headers: list[str]) -> dict[str, str]:
        """Flatten a list of Set-Cookie header values into {name: value}.

        Keeps the *last* value when a server re-sets the same cookie; that's
        the one the browser would apply, which is what the next verify-step
        request needs.
        """
        out: dict[str, str] = {}
        for raw in set_cookie_headers:
            if not raw:
                continue
            jar = SimpleCookie()
            try:
                jar.load(raw)
            except Exception:  # noqa: BLE001 — malformed cookies are common
                continue
            for name, morsel in jar.items():
                out[name] = morsel.value
        return out

    @staticmethod
    def payload_or_fail(task: Task) -> dict[str, Any]:
        """Pull `task.payload` as dict, raising a typed error on a bad shape."""
        if not isinstance(task.payload, dict):
            raise SensorError(
                "task.payload must be a dict",
                context={"got": type(task.payload).__name__, "task_id": str(task.id)},
            )
        return task.payload

    @staticmethod
    def not_implemented(engine: str, *, next_step: str) -> CaptchaError:
        """Uniform 'live RE needed' error. Carries the next-step pointer."""
        return CaptchaError(
            f"not yet implemented — live RE needed; see runbooks/{engine}.md",
            context={"engine": engine, "next_step": next_step},
        )
