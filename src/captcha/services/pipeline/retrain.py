"""Retrain trigger — writes a marker JSON to S3 and POSTs a webhook.

Does not spin up ML training itself; the training cluster watches the
`retrain_requests/` prefix and the webhook. Keep the surface narrow: who,
when, why, and the pointer to the labelling slice."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from captcha.services.pipeline.s3client import S3Writer

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class RetrainRequest:
    type_: str
    reason: str
    requested_at: str
    hostname: str
    labelling_prefix: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_,
            "reason": self.reason,
            "requested_at": self.requested_at,
            "hostname": self.hostname,
            "labelling_prefix": self.labelling_prefix,
            "extra": dict(self.extra),
            "schema": 1,
        }


class RetrainTrigger:
    """Fire-and-forget marker + webhook. Idempotent per minute per type (key uses minute granularity)."""

    def __init__(
        self,
        *,
        s3: S3Writer,
        marker_prefix: str = "retrain_requests",
        labelling_prefix: str = "labelling",
        webhook_url: str = "",
        hostname: str | None = None,
    ) -> None:
        self.s3 = s3
        self.marker_prefix = marker_prefix.strip("/")
        self.labelling_prefix = labelling_prefix.strip("/")
        self.webhook_url = webhook_url
        self.hostname = hostname or socket.gethostname()

    @classmethod
    def from_env(cls, s3: S3Writer) -> RetrainTrigger:
        return cls(
            s3=s3,
            marker_prefix=os.getenv("PIPELINE_RETRAIN_PREFIX", "retrain_requests"),
            labelling_prefix=os.getenv("PIPELINE_S3_PREFIX", "labelling"),
            webhook_url=os.getenv("PIPELINE_RETRAIN_WEBHOOK", ""),
        )

    async def trigger(
        self,
        type_: str,
        reason: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Returns True iff the marker was written. Webhook failure is logged but not fatal."""
        now = datetime.now(UTC)
        stamp = now.strftime("%Y%m%d%H%M")
        safe_type = _safe(type_ or "all")
        key = f"{self.marker_prefix}/{stamp}_{safe_type}.json"
        req = RetrainRequest(
            type_=type_,
            reason=reason,
            requested_at=now.isoformat(),
            hostname=self.hostname,
            labelling_prefix=f"{self.labelling_prefix}/{type_}/" if type_ else self.labelling_prefix,
            extra=extra or {},
        )
        body = req.to_dict()

        wrote = await self.s3.put_json(key, body)
        if not wrote:
            logger.error("pipeline.retrain.marker_failed", key=key, type=type_)
            return False
        logger.info("pipeline.retrain.marker_written", key=key, type=type_, reason=reason)

        if self.webhook_url:
            await self._post_webhook({"source": "captcha-pipeline", "kind": "retrain", "marker": key, "request": body})
        return True

    async def _post_webhook(self, payload: dict[str, Any]) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(self.webhook_url, json=payload)
        except httpx.HTTPError as exc:
            logger.error("pipeline.retrain.webhook_transport", error=str(exc))
            return False
        if r.status_code >= 400:
            logger.error("pipeline.retrain.webhook_rejected", status=r.status_code, body=r.text[:300])
            return False
        logger.info("pipeline.retrain.webhook_sent", status=r.status_code)
        return True


def _safe(s: str) -> str:
    # S3 keys accept a lot, but keep them boring for ops tooling greps.
    return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in s)[:64] or "unknown"
