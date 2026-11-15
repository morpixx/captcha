"""S3/MinIO-backed model registry with disk cache and hot-reload watcher.

Bucket layout:
    <bucket>/<name>/<version>/meta.json
    <bucket>/<name>/<version>/weights.<ext>
    <bucket>/<name>/latest       object body = version string

The 'latest' pointer is read on every get_meta call so a new version in the
bucket is picked up without a client restart.
"""

from __future__ import annotations

import hashlib
import io
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import structlog
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from captcha.domain.errors import ConfigError, ModelInferenceError

from .loaders import load_hf, load_onnx, load_safetensors, load_torch
from .meta import ModelMeta, decode_meta

log = structlog.get_logger(__name__)

_META_FILE = "meta.json"
_LATEST_KEY = "latest"


class Registry:
    """Fetch + cache model weights. Thread-safe. Hot-reload optional."""

    def __init__(
        self,
        s3_endpoint: str,
        bucket: str,
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
        cache_dir: str | Path = "./models-cache",
        verify_tls: bool = True,
    ) -> None:
        self.bucket = bucket
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._s3 = boto3.client(
            "s3",
            endpoint_url=s3_endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
            verify=verify_tls,
        )

        self._meta_cache: dict[tuple[str, str], ModelMeta] = {}
        self._meta_lock = threading.RLock()
        self._watch_thread: threading.Thread | None = None
        self._watch_stop = threading.Event()
        self._watch_callbacks: list[Callable[[ModelMeta], None]] = []

    # ---- discovery ------------------------------------------------------

    def list_models(self) -> list[str]:
        """Top-level 'folders' in the bucket — one per model name."""
        paginator = self._s3.get_paginator("list_objects_v2")
        names: set[str] = set()
        try:
            for page in paginator.paginate(Bucket=self.bucket, Delimiter="/"):
                for pfx in page.get("CommonPrefixes") or ():
                    p = pfx["Prefix"].rstrip("/")
                    if p:
                        names.add(p)
        except (BotoCoreError, ClientError) as e:
            raise ConfigError(f"list_models failed: {e}", context={"bucket": self.bucket}) from e
        return sorted(names)

    def list_versions(self, name: str) -> list[str]:
        paginator = self._s3.get_paginator("list_objects_v2")
        versions: set[str] = set()
        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=f"{name}/", Delimiter="/"):
                for pfx in page.get("CommonPrefixes") or ():
                    v = pfx["Prefix"][len(name) + 1 :].rstrip("/")
                    if v and v != _LATEST_KEY:
                        versions.add(v)
        except (BotoCoreError, ClientError) as e:
            raise ConfigError(f"list_versions failed: {e}", context={"name": name}) from e
        return sorted(versions)

    # ---- resolution -----------------------------------------------------

    def _resolve_latest(self, name: str) -> str:
        key = f"{name}/{_LATEST_KEY}"
        try:
            obj = self._s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            raise ConfigError(f"'latest' pointer missing for {name}", context={"key": key}) from e
        version = obj["Body"].read().decode("utf-8").strip()
        if not version:
            raise ConfigError(f"'latest' pointer is empty for {name}", context={"key": key})
        return version

    def get_meta(self, name: str, version: str = "latest") -> ModelMeta:
        if version == "latest":
            version = self._resolve_latest(name)

        key = f"{name}/{version}/{_META_FILE}"
        try:
            obj = self._s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            raise ConfigError(f"meta.json missing: {name}@{version}", context={"key": key}) from e

        meta = decode_meta(obj["Body"].read())
        with self._meta_lock:
            self._meta_cache[(name, version)] = meta
        return meta

    def pick_version(self, name: str, *, traffic_id: str | bytes | None = None) -> ModelMeta:
        """Resolve 'latest' then honour canary_rollout.

        If `traffic_id` is given and `canary_rollout > 0`, hash the id and route
        that fraction of traffic to the canary version pointed at by the label
        `labels['canary']`. Fall back to latest otherwise.
        """
        latest = self.get_meta(name, "latest")
        canary_name = latest.labels.get("canary")
        if not canary_name or latest.canary_rollout <= 0 or traffic_id is None:
            return latest
        b = traffic_id.encode() if isinstance(traffic_id, str) else traffic_id
        h = int.from_bytes(hashlib.blake2b(b, digest_size=8).digest(), "big")
        slot = (h % 10_000) / 10_000.0
        if slot < latest.canary_rollout:
            return self.get_meta(name, canary_name)
        return latest

    # ---- download + cache ----------------------------------------------

    def download(self, name: str, version: str = "latest", *, force: bool = False) -> Path:
        """Download weights, verify sha256, return local path. Cache-hits are free."""
        meta = self.get_meta(name, version)
        local = self.cache_dir / name / meta.version / Path(meta.s3_key).name
        local.parent.mkdir(parents=True, exist_ok=True)

        if not force and local.exists() and _sha256_file(local) == meta.sha256:
            log.debug("models.cache.hit", name=name, version=meta.version, path=str(local))
            return local

        log.info("models.download.start", name=name, version=meta.version, key=meta.s3_key)
        buf = io.BytesIO()
        try:
            self._s3.download_fileobj(self.bucket, meta.s3_key, buf)
        except (BotoCoreError, ClientError) as e:
            raise ConfigError(
                f"download failed: {name}@{meta.version}",
                context={"key": meta.s3_key},
            ) from e

        blob = buf.getvalue()
        digest = hashlib.sha256(blob).hexdigest()
        if digest != meta.sha256:
            raise ModelInferenceError(
                "sha256 mismatch after download",
                context={"expected": meta.sha256, "got": digest, "name": name, "version": meta.version},
            )

        tmp = local.with_suffix(local.suffix + ".part")
        tmp.write_bytes(blob)
        tmp.replace(local)
        log.info("models.download.done", name=name, version=meta.version, bytes=len(blob))
        return local

    # ---- framework loaders ---------------------------------------------

    def load_pytorch(self, name: str, version: str = "latest", *, device: str = "cpu") -> Any:
        path = self.download(name, version)
        return load_torch(path, device=device)

    def load_onnx(self, name: str, version: str = "latest", *, providers: list[str] | None = None) -> Any:
        path = self.download(name, version)
        return load_onnx(path, providers=providers)

    def load_hf(self, name: str, version: str = "latest", *, device: str = "cpu", task: str | None = None) -> Any:
        path = self.download(name, version)
        return load_hf(path, device=device, task=task)

    def load_safetensors(self, name: str, version: str = "latest", *, device: str = "cpu") -> dict[str, Any]:
        path = self.download(name, version)
        return load_safetensors(path, device=device)

    # ---- publishing ------------------------------------------------------

    def publish(
        self,
        name: str,
        version: str,
        weights_path: str | Path,
        *,
        framework: str = "torch",
        labels: dict[str, str] | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        set_latest: bool = True,
    ) -> ModelMeta:
        """Upload one (name, version) pair: weights blob + meta.json + 'latest'.

        This is the only supported way to make a model visible to a solver. The
        object layout matches what `download()` expects:
            <name>/<version>/weights.<ext>
            <name>/<version>/meta.json
            <name>/latest                     (body = version string)
        """
        from .meta import encode_meta

        path = Path(weights_path)
        if not path.exists():
            raise ConfigError("weights file missing", context={"path": str(path)})

        blob = path.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        key = f"{name}/{version}/weights{path.suffix or '.bin'}"

        try:
            self._s3.put_object(Bucket=self.bucket, Key=key, Body=blob)
        except (BotoCoreError, ClientError) as e:
            raise ConfigError("weights upload failed", context={"key": key, "error": str(e)}) from e

        meta = ModelMeta(
            name=name,
            version=version,
            framework=framework,  # type: ignore[arg-type]
            s3_key=key,
            sha256=digest,
            size_bytes=len(blob),
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            labels=labels or {},
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        meta_key = f"{name}/{version}/{_META_FILE}"
        try:
            self._s3.put_object(Bucket=self.bucket, Key=meta_key, Body=encode_meta(meta))
        except (BotoCoreError, ClientError) as e:
            raise ConfigError("meta upload failed", context={"key": meta_key, "error": str(e)}) from e

        if set_latest:
            self.set_latest(name, version)

        with self._meta_lock:
            self._meta_cache[(name, version)] = meta
        log.info("models.published", name=name, version=version, key=key, sha256=digest)
        return meta

    def set_latest(self, name: str, version: str) -> None:
        """Point `<name>/latest` at `version`. Readers pick it up on their next call."""
        try:
            self._s3.put_object(Bucket=self.bucket, Key=f"{name}/{_LATEST_KEY}", Body=version.encode())
        except (BotoCoreError, ClientError) as e:
            raise ConfigError("latest pointer write failed", context={"name": name, "version": version}) from e
        log.info("models.latest", name=name, version=version)

    # ---- hot-reload -----------------------------------------------------

    def watch(
        self,
        names: Iterable[str] | None = None,
        *,
        interval: float = 60.0,
        on_change: Callable[[ModelMeta], None] | None = None,
    ) -> None:
        """Start a background thread that re-polls meta and fires on_change
        whenever the 'latest' pointer moves. Idempotent."""
        if on_change is not None:
            self._watch_callbacks.append(on_change)
        if self._watch_thread and self._watch_thread.is_alive():
            return

        watched = list(names) if names else None
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            args=(watched, interval),
            name="captcha-models-watch",
            daemon=True,
        )
        self._watch_thread.start()

    def stop_watch(self) -> None:
        self._watch_stop.set()
        if self._watch_thread:
            self._watch_thread.join(timeout=5.0)
            self._watch_thread = None

    def _watch_loop(self, names: list[str] | None, interval: float) -> None:
        current: dict[str, str] = {}
        while not self._watch_stop.is_set():
            targets = names if names is not None else self.list_models()
            for name in targets:
                try:
                    meta = self.get_meta(name, "latest")
                except ConfigError as e:
                    log.warning("models.watch.meta_fail", name=name, error=str(e))
                    continue
                prev = current.get(name)
                if prev is None:
                    current[name] = meta.version
                elif prev != meta.version:
                    log.info("models.watch.version_change", name=name, prev=prev, new=meta.version)
                    current[name] = meta.version
                    for cb in list(self._watch_callbacks):
                        try:
                            cb(meta)
                        except Exception as e:
                            log.error("models.watch.callback_fail", name=name, error=str(e))
            self._watch_stop.wait(interval)


def _sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for buf in iter(lambda: f.read(chunk), b""):
            h.update(buf)
    return h.hexdigest()
