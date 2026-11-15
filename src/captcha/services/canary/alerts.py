"""Webhook alerter. Posts JSON on failure threshold; cheap, best-effort, no retries."""

from __future__ import annotations

import socket
from typing import Any

import httpx
import structlog

from captcha.services.canary.suite import SuiteReport

logger = structlog.get_logger(__name__)


class WebhookAlerter:
    """Fires once per `run_once` if pass-rate < threshold. Silent on empty URL."""

    def __init__(
        self,
        *,
        webhook_url: str,
        pass_threshold: float = 0.9,
        gateway_url: str = "",
        hostname: str | None = None,
        timeout_sec: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.pass_threshold = pass_threshold
        self.gateway_url = gateway_url
        self.hostname = hostname or socket.gethostname()
        self.timeout_sec = timeout_sec
        #: injected by tests; production goes through the real network
        self.transport = transport

    async def maybe_alert(self, report: SuiteReport) -> bool:
        """Returns True iff an alert was fired."""
        if not self.webhook_url:
            return False
        if report.total == 0:
            payload = self._payload(report, reason="no_fixtures")
            return await self._post(payload)
        if report.pass_rate >= self.pass_threshold:
            return False
        payload = self._payload(report, reason="pass_rate_below_threshold")
        return await self._post(payload)

    def _payload(self, report: SuiteReport, *, reason: str) -> dict[str, Any]:
        return {
            "source": "captcha-canary",
            "reason": reason,
            "hostname": self.hostname,
            "gateway": self.gateway_url,
            "threshold": self.pass_threshold,
            "report": report.to_dict(),
        }

    async def _post(self, payload: dict[str, Any]) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec, transport=self.transport) as c:
                resp = await c.post(self.webhook_url, json=payload)
        except httpx.HTTPError as exc:
            logger.error("canary.alert.transport_failed", error=str(exc))
            return False
        if resp.status_code >= 400:
            logger.error(
                "canary.alert.rejected",
                status=resp.status_code,
                body=resp.text[:300],
            )
            return False
        logger.info("canary.alert.sent", status=resp.status_code)
        return True
