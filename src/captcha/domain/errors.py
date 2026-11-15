"""Captcha-solver error hierarchy. All solver failures classify into these."""

from __future__ import annotations


class CaptchaError(Exception):
    """Base class. Carries structured context."""

    code: str = "ERR_GENERIC"

    def __init__(self, message: str, *, context: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "context": self.context}


class UnsolvableError(CaptchaError):
    """Task is structurally unsolvable (unsupported variant, broken payload)."""

    code = "ERR_UNSOLVABLE"


class TimeoutError(CaptchaError):
    """Solver timed out upstream before result."""

    code = "ERR_TIMEOUT"


class ProxyError(CaptchaError):
    """Proxy-layer failure (ban, DNS, tunnel)."""

    code = "ERR_PROXY"


class NeedsBrowserError(CaptchaError):
    """HTTP solver cannot finish in-process; the task belongs in the browser lane.

    Non-terminal by itself: the HTTP worker moves the envelope to the `browser`
    stream, acks the source entry and leaves billing untouched. Only a second
    hop past `TaskEnvelope.lane_hops` becomes terminal `ERR_UNSOLVABLE`.
    """

    code = "ERR_NEEDS_BROWSER"


class QuotaError(CaptchaError):
    """Account quota/rate limit would be exceeded (active, daily, spend)."""

    code = "ERR_QUOTA"


class UrlRejectedError(CaptchaError):
    """Client-supplied URL is not a public http(s) target (SSRF guard)."""

    code = "ERR_URL_REJECTED"


class SensorError(CaptchaError):
    """Sensor payload rejected by remote. Usually means schema drifted."""

    code = "ERR_SENSOR"


class JSRuntimeError(CaptchaError):
    """V8 / Node sidecar failure while running obfuscated challenge."""

    code = "ERR_JS_RUNTIME"


class ChallengeExpiredError(CaptchaError):
    """Challenge data from client is stale (cookies rotated, sitekey changed)."""

    code = "ERR_CHALLENGE_EXPIRED"


class ModelInferenceError(CaptchaError):
    """CV / audio model inference failed."""

    code = "ERR_MODEL_INFERENCE"


class ConfigError(CaptchaError):
    """Misconfiguration — resolved at startup, never at task time in prod."""

    code = "ERR_CONFIG"
