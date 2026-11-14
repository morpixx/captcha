"""reCAPTCHA v3 / v3 Enterprise solvers.

v3 is fully invisible: `grecaptcha.execute` returns a token whose score is set
by Google's grader. The grader reads:
  * JA3 / JA4 of the TLS handshake (impersonation layer handles this),
  * the `bg` parameter — an opaque, obfuscated sensor payload describing the
    session (fingerprint + light behavior),
  * Google-side cookies & IP reputation.

Without a valid `bg`, the endpoint still returns a token, but the score
collapses to 0.1 and the caller cannot use it. So we fail fast rather than
ship a dud. Once `captcha.engines` learns the recaptcha-v3 `bg` schema, drop
it under `AntibotEngine.CLOUDFLARE`-style entry and flip the guard here.

Enterprise variant uses `/recaptcha/enterprise/` instead of `/recaptcha/api2/`;
the token verification endpoint shape is the same. The `api_version` param
sometimes differs (`v=<build-hash>`); keep it configurable.
"""

from __future__ import annotations

import base64
import secrets
from typing import ClassVar
from urllib.parse import urlparse

import structlog

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import CaptchaError, SensorError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo, register_solver

_log = structlog.get_logger(__name__)

# Endpoints — rev when Google flips the `v=` build hash. Keep every URL in one
# block; a schema shift touches this constant set first.
API_HOST = "https://www.google.com"
ENT_API_HOST = "https://www.google.com"

ANCHOR_URL_FMT = API_HOST + "/recaptcha/api2/anchor"
RELOAD_URL_FMT = API_HOST + "/recaptcha/api2/reload"
USERVERIFY_URL_FMT = API_HOST + "/recaptcha/api2/userverify"

ANCHOR_URL_ENT_FMT = ENT_API_HOST + "/recaptcha/enterprise/anchor"
RELOAD_URL_ENT_FMT = ENT_API_HOST + "/recaptcha/enterprise/reload"
USERVERIFY_URL_ENT_FMT = ENT_API_HOST + "/recaptcha/enterprise/userverify"

# FILL: pin this to the current grecaptcha build; dump it from a fresh page
# with `grecaptcha.getPageId()` prefix or the `/recaptcha/api.js?render=...`
# redirect. Stale `v` triggers a 403 on reload.
API_VERSION = "DKw2Q4K7L4-p0e50T4ieYFkC"


def _co_param(site_url: str) -> str:
    """Google wants `co` = base64url(`origin:port`) without padding."""
    parsed = urlparse(site_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    origin = f"{host}:{port}".encode()
    return base64.urlsafe_b64encode(origin).rstrip(b"=").decode()


class _RecaptchaV3Base(Solver):
    """Shared v3 flow — Enterprise just overrides endpoints + the action label."""

    _ANCHOR: ClassVar[str] = ANCHOR_URL_FMT
    _RELOAD: ClassVar[str] = RELOAD_URL_FMT
    _USERVERIFY: ClassVar[str] = USERVERIFY_URL_FMT
    _IS_ENTERPRISE: ClassVar[bool] = False

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise CaptchaError("recaptcha_v3 requires ctx.http", context={"task": str(task.id)})
        if not task.sitekey:
            raise CaptchaError("recaptcha_v3 payload missing sitekey", context={"task": str(task.id)})
        if not task.site_url:
            raise CaptchaError("recaptcha_v3 payload missing site_url", context={"task": str(task.id)})

        payload = task.payload or {}
        action: str = str(payload.get("action", "verify"))
        min_score: float = float(payload.get("min_score", 0.3))

        # Sensor (bg) — the component we are missing.
        bg_payload = await self._build_bg(task=task, ctx=ctx, action=action)

        co = _co_param(task.site_url)
        language = task.language or "en"
        anchor_params = {
            "ar": "1",
            "k": task.sitekey,
            "co": co,
            "hl": language,
            "v": API_VERSION,
            "size": "invisible",
            "cb": _random_cb(),
        }

        anchor_resp = await ctx.http.get(self._ANCHOR, params=anchor_params)
        token = _extract_recaptcha_token(anchor_resp.text)
        if not token:
            raise SensorError(
                "recaptcha_v3 anchor returned no token — API_VERSION likely stale",
                context={"status": anchor_resp.status, "url": anchor_resp.final_url},
            )

        reload_body = {
            "v": API_VERSION,
            "k": task.sitekey,
            "c": token,
            "co": co,
            "hl": language,
            "size": "invisible",
            "reason": "q",
            "chr": "[0,0,0]",
            "bg": bg_payload,
        }
        if self._IS_ENTERPRISE:
            reload_body["cap"] = "fbg"

        reload_resp = await ctx.http.post(
            self._RELOAD,
            params={"k": task.sitekey},
            data=reload_body,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

        final_token = _extract_recaptcha_token(reload_resp.text)
        if not final_token:
            raise UnsolvableError(
                "recaptcha_v3 reload rejected sensor — score path closed",
                context={"status": reload_resp.status, "min_score": min_score, "action": action},
            )
        # FILL: v3 does not expose score in the reload body — scores come from
        # the site's server-side /siteverify. The gateway must forward token +
        # action untouched and let the caller grade it. Keep this comment as a
        # reminder: do not invent a score field here.
        return SolverResult(token=final_token, extra={"action": action})

    async def _build_bg(self, *, task: Task, ctx: SolverContext, action: str) -> str:
        """Assemble the `bg` sensor payload for reCAPTCHA v3.

        This is the single blocker. The payload is a base64-wrapped blob the
        grecaptcha client builds from navigator+timing+pointer evidence. No
        recaptcha-v3 builder lives in `captcha.engines` yet; when one lands,
        pull it here.
        """
        if ctx.sensors is None:
            raise CaptchaError(
                "recaptcha_v3 requires a sensor builder — ctx.sensors not provided",
                context={"task": str(task.id)},
            )
        # FILL: recaptcha v3 sensor builder — once captcha.engines ships
        # `RecaptchaV3SensorBuilder`, resolve it via
        #   ctx.sensors.get(AntibotEngine.<recaptcha-v3-slot>)
        # and run it with a BuildContext built from ctx.fingerprint_store +
        # ctx.behavior. Dump the current `bg` field shape from a live Google
        # page (DevTools, search for `bg:` in the reload POST).
        raise SensorError(
            "recaptcha_v3 `bg` sensor builder not yet available — see runbooks/recaptcha_v3.md",
            context={
                "action": action,
                "sitekey": task.sitekey,
                "site_url": task.site_url,
                "needs": ["recaptcha_v3_sensor_builder", "fingerprint_pin"],
            },
        )


@register_solver
class RecaptchaV3Solver(_RecaptchaV3Base):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="recaptcha_v3",
        types=(CaptchaType.RECAPTCHA_V3,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        experimental=True,
        version="0.1.0",
    )


@register_solver
class RecaptchaV3EnterpriseSolver(_RecaptchaV3Base):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="recaptcha_v3_enterprise",
        types=(CaptchaType.RECAPTCHA_V3_ENTERPRISE,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        experimental=True,
        version="0.1.0",
    )

    _ANCHOR = ANCHOR_URL_ENT_FMT
    _RELOAD = RELOAD_URL_ENT_FMT
    _USERVERIFY = USERVERIFY_URL_ENT_FMT
    _IS_ENTERPRISE = True


# ---- parsing helpers ------------------------------------------------------


def _random_cb() -> str:
    """Grecaptcha's `cb` is a 12-char lowercase+digits string."""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def _extract_recaptcha_token(html_or_js: str) -> str | None:
    """Pull the `recaptcha-token` hidden input (anchor HTML) or the reload
    response body's token field.

    anchor: `<input ... id="recaptcha-token" value="<token>">`
    reload: `)]}'["<token>",<score>,<...>]` — token sits at index 1 of the
            trimmed JSON-ish array.
    """
    # anchor HTML path
    marker = 'id="recaptcha-token"'
    idx = html_or_js.find(marker)
    if idx != -1:
        value_marker = 'value="'
        v = html_or_js.find(value_marker, idx)
        if v != -1:
            start = v + len(value_marker)
            end = html_or_js.find('"', start)
            if end > start:
                return html_or_js[start:end]
    # reload path — trim google's anti-XSSI prefix and read the first quoted literal
    body = html_or_js.lstrip()
    if body.startswith(")]}'"):
        body = body[4:]
    body = body.lstrip()
    if body.startswith("["):
        first_quote = body.find('"')
        if first_quote != -1:
            second_quote = body.find('"', first_quote + 1)
            if second_quote > first_quote:
                return body[first_quote + 1 : second_quote]
    return None
