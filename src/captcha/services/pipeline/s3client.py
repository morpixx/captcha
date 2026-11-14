"""Lazy S3 wrapper. boto3 is sync; everything that touches it goes through
asyncio.to_thread so the scheduler stays responsive."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # boto3 is declared in pyproject; this branch is for import-time inspection only
    boto3 = None  # type: ignore[assignment]
    BotoConfig = None  # type: ignore[assignment]

    class BotoCoreError(Exception):  # type: ignore[no-redef]
        pass

    class ClientError(Exception):  # type: ignore[no-redef]
        pass


from captcha.config import Settings

logger = structlog.get_logger(__name__)


class S3Writer:
    """Thin wrapper around a single S3 bucket. Opens the client on first use."""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
    ) -> None:
        self.endpoint = endpoint or ""
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.region = region or "us-east-1"
        self._client: Any = None

    @classmethod
    def from_settings(cls, settings: Settings) -> S3Writer:
        return cls(
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
        )

    def _get(self) -> Any:
        if self._client is not None:
            return self._client
        if boto3 is None:
            # not installed — raise on first real use, not at import
            raise RuntimeError("boto3 is not installed; add it to dependencies")
        cfg = BotoConfig(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
            s3={"addressing_style": "path"},  # MinIO defaults to path-style
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=self.endpoint or None,
            aws_access_key_id=self.access_key or None,
            aws_secret_access_key=self.secret_key or None,
            region_name=self.region,
            config=cfg,
        )
        logger.info("pipeline.s3.ready", bucket=self.bucket, endpoint=self.endpoint or "aws")
        return self._client

    async def put_json(
        self,
        key: str,
        obj: Any,
        *,
        content_type: str = "application/json",
    ) -> bool:
        body = json.dumps(obj, default=_default, ensure_ascii=False).encode("utf-8")
        return await self.put_bytes(key, body, content_type=content_type)

    async def put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> bool:
        try:
            await asyncio.to_thread(
                self._get().put_object,
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("pipeline.s3.put_failed", key=key, error=str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 — final guard; we never want a sampler run to crash
            logger.exception("pipeline.s3.put_unexpected", key=key, error=str(exc))
            return False
        return True


def _default(o: Any) -> Any:
    # datetime/UUID/Decimal via str() — JSON-safe without pulling pydantic.
    return str(o)
