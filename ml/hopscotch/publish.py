"""Training kit, step 5: publish the artifact triple to the model bucket.

Layout (mirrors what captcha.adapters.weights.Registry reads, plus the two
sidecar files the hopscotch solver needs):

    <bucket>/<name>/<version>/model.safetensors
    <bucket>/<name>/<version>/config.json
    <bucket>/<name>/<version>/release.json
    <bucket>/<name>/<version>/checksums.json
    <bucket>/<name>/<version>/meta.json          ModelMeta, s3_key = the weights
    <bucket>/<name>/latest                       body = version

make check-model NAME=hopscotch-v1 works unchanged: meta.json carries the sha256
of model.safetensors and Registry.download verifies it. The solver itself is
handed a local artifact directory (--cache-dir), because a registry download only
fetches the weights blob.

An artifact whose release is not ready is refused unless --allow-not-ready is
given. Beta builds abstain by design; shipping one silently as "the model" is
how a 70% model ends up answering customer tasks.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from captcha.domain.errors import ConfigError
from captcha.solvers.hopscotch.artifact import (
    CHECKSUMS_FILE,
    CONFIG_FILE,
    RELEASE_FILE,
    WEIGHTS_FILE,
    HopscotchArtifact,
)

DEFAULT_NAME = "hopscotch-v1"
SIDECARS = (CONFIG_FILE, RELEASE_FILE, CHECKSUMS_FILE)


@dataclass(frozen=True)
class PublishResult:
    name: str
    version: str
    keys: tuple[str, ...]
    meta: dict[str, Any]
    artifact: HopscotchArtifact
    cache_dir: Path | None


def s3_client() -> Any:
    """MinIO/S3 client from the same env names the service uses."""
    import boto3
    from botocore.client import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY", "minioadmin"),
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        config=BotoConfig(signature_version="s3v4"),
    )


def build_meta(name: str, version: str, weights: Path, artifact: HopscotchArtifact) -> dict[str, Any]:
    """ModelMeta-compatible meta.json for the registry."""
    blob = weights.read_bytes()
    return {
        "name": name,
        "version": version,
        "framework": "safetensors",
        "s3_key": f"{name}/{version}/{weights.name}",
        "sha256": hashlib.sha256(blob).hexdigest(),
        "size_bytes": len(blob),
        "input_schema": {
            "image_height": artifact.config.input.image_height,
            "card_width": artifact.config.input.card_width,
            "card_height": artifact.config.input.card_height,
            "hint_box": list(artifact.config.input.hint_box),
            "frames": list(artifact.config.input.frames),
            "index_base": artifact.config.input.index_base,
        },
        "output_schema": {
            "index": "1-based card number",
            "coordinates": "[x, y] pixel foot point of the chosen card",
        },
        "canary_rollout": 0.0,
        "labels": {},
        "created_at": str(artifact.release.created_at or ""),
    }


def publish(
    artifact_dir: str | Path,
    *,
    name: str = DEFAULT_NAME,
    version: str = "v1",
    s3: Any = None,
    bucket: str | None = None,
    cache_dir: str | Path | None = None,
    set_latest: bool = True,
    allow_not_ready: bool = False,
    dry_run: bool = False,
) -> PublishResult:
    from .export import verify_artifact

    artifact = verify_artifact(artifact_dir)
    if artifact.release.status != "ready" and not allow_not_ready:
        raise ConfigError(
            "release is not ready; refusing to publish as a calibrated model",
            context={
                "status": artifact.release.status,
                "threshold": artifact.release.threshold,
                "hint": "pass --allow-not-ready for a beta artifact",
            },
        )

    root = Path(artifact_dir)
    weights = root / WEIGHTS_FILE
    meta = build_meta(name, version, weights, artifact)
    payloads: dict[str, bytes] = {WEIGHTS_FILE: weights.read_bytes()}
    for sidecar in SIDECARS:
        path = root / sidecar
        if path.is_file():
            payloads[sidecar] = path.read_bytes()
    payloads["meta.json"] = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")

    keys: list[str] = []
    for filename, body in payloads.items():
        key = f"{name}/{version}/{filename}"
        keys.append(key)
        if not dry_run:
            _put(s3, bucket, key, body)
    latest_key = f"{name}/latest"
    if set_latest:
        keys.append(latest_key)
        if not dry_run:
            _put(s3, bucket, latest_key, version.encode("utf-8"))

    target_cache = _cache(Path(cache_dir), name, version, root) if cache_dir is not None else None
    return PublishResult(
        name=name,
        version=version,
        keys=tuple(keys),
        meta=meta,
        artifact=artifact,
        cache_dir=target_cache,
    )


def _put(s3: Any, bucket: str | None, key: str, body: bytes) -> None:
    if s3 is None:
        raise ConfigError("no S3 client: pass s3= or run with credentials configured")
    if not bucket:
        raise ConfigError("no bucket configured")
    s3.put_object(Bucket=bucket, Key=key, Body=body)


def _cache(cache: Path, name: str, version: str, artifact_dir: Path) -> Path:
    target = cache / name / version
    target.mkdir(parents=True, exist_ok=True)
    for filename in (WEIGHTS_FILE, *SIDECARS):
        source = artifact_dir / filename
        if source.is_file():
            shutil.copy2(source, target / filename)
    return target


def artifact_sha256(directory: str | Path) -> Mapping[str, str]:
    root = Path(directory)
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.iterdir())
        if path.is_file()
    }


__all__ = [
    "DEFAULT_NAME",
    "PublishResult",
    "artifact_sha256",
    "build_meta",
    "publish",
    "s3_client",
]
