"""Kasada KPSDK: the /tl handshake in the HTTP lane, SDK VM in-process.

Flow (2026-05 and 2026-10 write-ups; docs/reverse-2026.md section 6):

    GET  <page> or <prefix>/fp   -> 429 with an HTML body naming ips.js
    GET  <prefix>/ips.js         -> the obfuscated challenge script
    POST <prefix>/tl             -> 200 {"reload": true} + x-kpsdk-ct, x-kpsdk-st
    GET  <prefix>/mfc            -> x-kpsdk-fc, x-kpsdk-h
    per protected request        -> a fresh x-kpsdk-cd (PoW, single use)

The engine builder runs the SDK script in the worker's V8 runtime; that is where
the /tl body and its x-kpsdk-* headers come from. This solver transports, enforces
the server verdict and returns the shape a client replays:

    solution.cookies        the jar after /tl (cookie names are per-tenant)
    solution.domains        host -> {cookies}, added by domain/solution.py
    solution.userAgent      the UA the tokens were minted for
    solution["x-kpsdk-*"]   ct / st / h / v / fc, plus cd when the payload carried
                            the VM answers (cd is single use, so it is forwarded
                            from a capture and never minted here)

Payload mode is primary: the transport raises ChallengeExpiredError on 429 before
a body is readable, so the script has to come from the task (or from a page the
edge actually served). Success is reload:true plus a ct header -- never a bare
200.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, ProxyError, UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase

from . import assembly, wire
from .endpoints import (
    HDR_CD,
    HDR_CT,
    HDR_DT,
    HDR_IM,
    HDR_ST,
    KPSDK_PATH_MFC,
    KPSDK_PATH_P,
    KPSDK_PATH_TL,
    STATUS_CHALLENGE,
    TL_CONTENT_TYPE,
)

TL_HEADER_KEYS = (HDR_CT, HDR_DT, HDR_IM)


class KasadaSolver(AntibotBase):
    """Kasada KPSDK /tl handshake."""

    engine = AntibotEngine.KASADA

    info: ClassVar[SolverInfo] = SolverInfo(
        name="kasada",
        types=(CaptchaType.KASADA,),
        http=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        self.require_ctx(ctx, "http", "fingerprint_store")
        payload = self.payload_or_fail(task)
        jar = wire.cookies_of(payload.get("cookies"))
        url = wire.page_url(task, payload)
        base = wire.origin_of(url)
        ua = wire.user_agent(task, ctx)

        boot = await wire.bootstrap(ctx, url, jar, ua)
        jar.update(boot.cookies)
        _script_url, script = await self._script(ctx, payload, boot, ua, jar)
        version = wire.first_str(payload.get("kpsdk_v"), payload.get("version"), wire.version_hint(script))

        builder, build_ctx = self.build_sensor(
            ctx,
            {**payload, "challenge_js": script, "site_url": url},
            cookies=jar,
            headers={"User-Agent": ua},
            timestamp_ms=wire.now_ms(),
            extra={**assembly.pow_extra(payload), "js_runtime": ctx.js_runtime, "kpsdk_v": version or ""},
        )
        built = await builder.build(build_ctx)

        tl_url = wire.first_str(payload.get("tl_url"), built.get("target"), base + KPSDK_PATH_TL)
        body = assembly.body_of(built)
        headers = {
            "User-Agent": ua,
            "Origin": base,
            "Referer": url,
            "Accept": "*/*",
            "Content-Type": TL_CONTENT_TYPE,
            **{key: value for key, value in (built.get("headers") or {}).items() if key in TL_HEADER_KEYS},
        }
        resp = await self._post(ctx, tl_url, body, headers=headers, jar=jar)
        jar.update(wire.merge_set_cookie(resp.headers_all("set-cookie")))

        ct = resp.header(HDR_CT)
        self._check(resp, ct=ct, sent=len(body))
        st = resp.header(HDR_ST)

        fc, h, note = await wire.mfc(
            ctx,
            wire.first_str(payload.get("mfc_url"), base + KPSDK_PATH_MFC),
            jar,
            ua,
            referer=url,
            ct=ct,
            version=version,
        )
        extra = assembly.solution_extra(jar, ua, ct, st=st, version=version, fc=fc, h=h, note=note)
        cd = assembly.cd_header(builder, build_ctx)
        if cd:
            extra[HDR_CD] = cd
        extra["headers"] = {key: value for key, value in extra.items() if key.startswith("x-kpsdk-")}
        return SolverResult(
            token=ct,
            extra=extra,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    # --- steps ----------------------------------------------------------

    async def _script(
        self, ctx: SolverContext, payload: dict[str, Any], boot: wire.Bootstrap, ua: str, jar: dict[str, str]
    ) -> tuple[str | None, str]:
        """Resolve the SDK script: payload source, payload URL, page, fixed prefix."""
        inline = wire.first_str(payload.get("script"), payload.get("ips_js"), payload.get("p_js"))
        if inline:
            return None, inline

        script_url = wire.first_str(payload.get("script_url"), payload.get("ips_url"), payload.get("p_js_url"))
        if script_url is None:
            script_url = wire.script_url(boot.html, boot.url) or (wire.origin_of(boot.url) + KPSDK_PATH_P)
        try:
            text = await wire.fetch_text(ctx, script_url, jar, ua, referer=boot.url)
        except ChallengeExpiredError as exc:
            raise UnsolvableError(
                "kasada: the SDK script could not be read; send script (source) or script_url",
                context={"reason": exc.code, "bootstrap_note": boot.note},
            ) from exc
        return script_url, text

    async def _post(
        self, ctx: SolverContext, url: str, body: bytes, *, headers: dict[str, str], jar: dict[str, str]
    ) -> Any:
        try:
            return await self.post_sensor(ctx, url, body, headers=headers, cookies=jar or None)
        except ProxyError:
            raise
        except CaptchaError as exc:
            raise ChallengeExpiredError(
                "kasada: /tl POST was rejected at the transport",
                context={"reason": exc.code, "bytes": len(body)},
            ) from exc

    @staticmethod
    def _check(resp: Any, *, ct: str | None, sent: int) -> None:
        """The server verdict, in the order it becomes a solution."""
        if resp.status == STATUS_CHALLENGE:
            raise ChallengeExpiredError(
                "kasada: /tl answered 429 -- the payload or the session was rejected",
                context={"status": resp.status, "bytes": sent},
            )
        if resp.status != 200:
            raise ChallengeExpiredError(
                "kasada: /tl rejected the payload",
                context={"status": resp.status, "bytes": sent},
            )
        if not assembly.reloaded(resp):
            raise ChallengeExpiredError(
                "kasada: /tl answered without reload:true",
                context={"status": resp.status},
            )
        if not ct:
            raise ChallengeExpiredError(
                "kasada: /tl answered but returned no x-kpsdk-ct",
                context={"status": resp.status},
            )
