"""Training kit, step 5: publish the artifact bundle through the weights registry.

Publishing goes through captcha.adapters.weights.Registry.publish_artifact, which
writes a real bundle manifest into meta.json:

    layout:    "bundle"
    artifact:  "hopscotch-v1"
    s3_key:    "<name>/<version>/model.safetensors"
    files:     {"model.safetensors": "<sha256>", "config.json": "...", ...}

Every uploaded file therefore carries its own sha256, `make check-model` can
verify each one (Registry.check_artifact), and a half-published or overwritten
artifact fails loudly instead of loading wrong weights. checksums.json is not
required: it is the training kit's own export receipt and an optional sidecar of
HOPSCOTCH_V1 (uploaded when present, verified when present).

--cache-dir materializes the bundle back out of the bucket with get_artifact
(round-trip through per-file sha256) so a solver has the whole directory:
<cache>/<name>/<version>/{model.safetensors,config.json,release.json,...}.

An artifact whose release is not ready is refused unless --allow-not-ready is
given. Beta builds abstain by design; shipping one silently as "the model" is
how a 70% model ends up answering customer tasks.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from captcha.adapters.weights import Registry
from captcha.adapters.weights.artifact import BUNDLE_META_FILE, read_bundle, spec_for, validate_bundle
from captcha.adapters.weights.meta import ModelMeta
from captcha.domain.errors import ConfigError
from captcha.solvers.hopscotch.artifact import HopscotchArtifact

DEFAULT_NAME = "hopscotch-v1"

#: Canned description of what the answer means, published with the bundle.
OUTPUT_SCHEMA: dict[str, Any] = {
    "index": "1-based card number",
    "coordinates": "[x, y] pixel foot point of the chosen card",
}


@dataclass(frozen=True)
class PublishResult:
    name: str
    version: str
    artifact: HopscotchArtifact
    keys: tuple[str, ...]
    files: Mapping[str, str]
    meta: ModelMeta | None
    cache_dir: Path | None
    dry_run: bool


def registry_for(
    *,
    client: Any = None,
    bucket: str | None = None,
    cache_dir: str | Path | None = None,
    endpoint: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    region: str | None = None,
) -> Registry:
    """Build a Registry from explicit args or the env names the service uses."""
    return Registry(
        endpoint or os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
        bucket or os.environ.get("S3_BUCKET", "captcha"),
        access_key=access_key if access_key is not None else os.environ.get("S3_ACCESS_KEY", "minioadmin"),
        secret_key=secret_key if secret_key is not None else os.environ.get("S3_SECRET_KEY", "minioadmin"),
        region=region or os.environ.get("S3_REGION", "us-east-1"),
        cache_dir=cache_dir if cache_dir is not None else "./models-cache",
        client=client,
    )


def artifact_input_schema(artifact: HopscotchArtifact) -> dict[str, Any]:
    geometry = artifact.config.input
    return {
        "image_height": geometry.image_height,
        "card_width": geometry.card_width,
        "card_height": geometry.card_height,
        "hint_box": list(geometry.hint_box),
        "frames": list(geometry.frames),
        "index_base": geometry.index_base,
    }


def publish(
    artifact_dir: str | Path,
    *,
    name: str = DEFAULT_NAME,
    version: str = "v1",
    registry: Registry | None = None,
    s3: Any = None,
    bucket: str | None = None,
    cache_dir: str | Path | None = None,
    set_latest: bool = True,
    allow_not_ready: bool = False,
    dry_run: bool = False,
) -> PublishResult:
    """Publish one exported artifact directory. Nothing is uploaded on dry_run."""
    from .export import verify_artifact

    root = Path(artifact_dir)
    artifact = verify_artifact(root)
    if artifact.release.status != "ready" and not allow_not_ready:
        raise ConfigError(
            "release is not ready; refusing to publish as a calibrated model",
            context={
                "status": artifact.release.status,
                "threshold": artifact.release.threshold,
                "hint": "pass --allow-not-ready for a beta artifact",
            },
        )
    if registry is not None and bucket is not None:
        raise ConfigError("pass either registry= or bucket=, not both")

    payload = read_bundle(root)
    spec = spec_for(name, payload)
    validate_bundle(payload, spec)
    shipped = tuple([*spec.files, *(f for f in spec.optional_files if f in payload)])
    digests = {fname: hashlib.sha256(payload[fname]).hexdigest() for fname in shipped}
    prefix = f"{name}/{version}"
    keys = [*(f"{prefix}/{fname}" for fname in shipped), f"{prefix}/{BUNDLE_META_FILE}"]
    if set_latest:
        keys.append(f"{name}/latest")

    target_cache: Path | None = None
    meta: ModelMeta | None = None
    if not dry_run:
        owner = registry or registry_for(client=s3, bucket=bucket, cache_dir=cache_dir)
        if (
            registry is not None
            and cache_dir is not None
            and Path(cache_dir).resolve() != owner.cache_dir.resolve()
        ):
            raise ConfigError(
                "cache_dir is owned by the registry when registry= is passed",
                context={"registry_cache": str(owner.cache_dir), "cache_dir": str(cache_dir)},
            )
        meta = owner.publish_artifact(
            name,
            version,
            payload,
            artifact=spec.artifact,
            labels={"role": "hopscotch", "release_status": artifact.release.status},
            input_schema=artifact_input_schema(artifact),
            output_schema=dict(OUTPUT_SCHEMA),
            set_latest=set_latest,
        )
        if cache_dir is not None:
            # Round-trip: only a bucket whose per-file sha256 all match lands here,
            # which is exactly the directory the solver is pointed at.
            target_cache = owner.get_artifact(name, version, force=True).directory

    return PublishResult(
        name=name,
        version=version,
        artifact=artifact,
        keys=tuple(keys),
        files=digests,
        meta=meta,
        cache_dir=target_cache,
        dry_run=dry_run,
    )


__all__ = [
    "DEFAULT_NAME",
    "OUTPUT_SCHEMA",
    "PublishResult",
    "artifact_input_schema",
    "publish",
    "registry_for",
]
