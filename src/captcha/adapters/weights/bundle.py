"""Upload / materialize / verify multi-file artifacts (shapes live in artifact.py).

Every file carries its own sha256 in meta.json, so a half-published or partially
overwritten artifact fails loudly instead of loading wrong weights. check() is
the whole-bundle version of what download() does for a single blob.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from captcha.domain.errors import CaptchaError, ConfigError, ModelInferenceError

from .artifact import (
    BUNDLE_META_FILE,
    SPECS,
    Artifact,
    ArtifactCheck,
    FileCheck,
    json_object,
    load_json_object,
    read_bundle,
    sha256_file,
    spec_for,
    validate_bundle,
)
from .meta import ModelMeta, encode_meta


class BundleStore:
    """Publish / fetch / check multi-file artifacts inside one bucket.

    meta_reader(name, version) resolves latest; latest_writer(name, version) moves
    the pointer. Both are injected so the store never needs a back-reference to
    Registry (and tests drive it with a fake client).
    """

    def __init__(
        self,
        client: Any,
        bucket: str,
        *,
        cache_dir: str | Path,
        meta_reader: Callable[[str, str], ModelMeta],
        latest_writer: Callable[[str, str], None],
    ) -> None:
        self._s3 = client
        self.bucket = bucket
        self.cache_dir = Path(cache_dir)
        self._read_meta = meta_reader
        self._write_latest = latest_writer

    def publish(
        self,
        name: str,
        version: str,
        source: str | Path | Mapping[str, bytes | Path | str],
        *,
        artifact: str | None = None,
        labels: Mapping[str, str] | None = None,
        input_schema: Mapping[str, Any] | None = None,
        output_schema: Mapping[str, Any] | None = None,
        canary_rollout: float = 0.0,
        set_latest: bool = True,
    ) -> ModelMeta:
        """Upload every file in the bundle, then meta.json, then latest."""
        payload = read_bundle(source)
        spec = spec_for(artifact or name, payload)
        validate_bundle(payload, spec)

        prefix = f"{name}/{version}"
        digests = {fname: hashlib.sha256(blob).hexdigest() for fname, blob in payload.items()}
        for fname in spec.files:
            self._put(f"{prefix}/{fname}", payload[fname])

        meta = ModelMeta(
            name=name,
            version=version,
            framework=spec.framework,
            s3_key=f"{prefix}/{spec.primary}",
            sha256=digests[spec.primary],
            size_bytes=len(payload[spec.primary]),
            layout="bundle",
            artifact=spec.artifact,
            files={fname: digests[fname] for fname in spec.files},
            input_schema=dict(input_schema or {}),
            output_schema=dict(output_schema or {}),
            labels=dict(labels or {}),
            canary_rollout=float(canary_rollout),
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self._put(f"{prefix}/{BUNDLE_META_FILE}", encode_meta(meta))
        if set_latest:
            self._write_latest(name, version)
        return meta

    def fetch(self, meta: ModelMeta, *, force: bool = False) -> Artifact:
        """Materialize every file of meta under the cache dir, verifying sha256."""
        if meta.layout != "bundle":
            raise ConfigError(
                f"not a bundle artifact: {meta.name}@{meta.version}",
                context={"layout": meta.layout},
            )
        declared = dict(meta.files)
        if not declared:
            raise ConfigError(f"bundle manifest lists no files: {meta.name}@{meta.version}")
        primary = Path(meta.s3_key).name
        if primary not in declared:
            raise ConfigError(
                "primary file is not in the bundle manifest",
                context={"name": meta.name, "primary": primary, "files": sorted(declared)},
            )

        dest = self.cache_dir / meta.name / meta.version
        dest.mkdir(parents=True, exist_ok=True)
        paths = {fname: self._fetch_one(meta, fname, dest / fname, declared[fname], force=force) for fname in declared}
        return Artifact(
            name=meta.name,
            version=meta.version,
            directory=dest,
            meta=meta,
            files=paths,
            primary=primary,
            config=load_json_object(paths.get("config.json")),
            release=load_json_object(paths.get("release.json")),
        )

    def check(self, name: str, version: str = "latest", *, force: bool = True) -> ArtifactCheck:
        """Download + verify the whole bundle and report every file."""
        try:
            meta = self._read_meta(name, version)
        except CaptchaError as exc:
            return ArtifactCheck(name=name, version=version, ok=False, failures=[f"meta.json: {exc}"])

        failures: list[str] = []
        if meta.layout != "bundle":
            failures.append(f"layout is {meta.layout!r}, expected bundle")
        spec = SPECS.get(meta.artifact or name)
        if spec is not None:
            absent = [f for f in spec.files if f not in meta.files]
            if absent:
                failures.append(f"manifest is missing {', '.join(absent)}")

        declared = dict(meta.files)
        dest = self.cache_dir / meta.name / meta.version
        dest.mkdir(parents=True, exist_ok=True)
        checks: list[FileCheck] = []
        parsed: dict[str, Any] = {}
        for fname in sorted(declared):
            item = FileCheck(name=fname, expected_sha256=declared[fname])
            try:
                path = self._fetch_one(meta, fname, dest / fname, declared[fname], force=force)
            except CaptchaError as exc:
                item.error = str(exc)
                failures.append(f"{fname}: {exc}")
            else:
                item.actual_sha256 = sha256_file(path)
                item.size_bytes = path.stat().st_size
                item.ok = item.actual_sha256 == declared[fname]
                if not item.ok:
                    failures.append(f"{fname}: sha256 mismatch")
                if fname.endswith(".json"):
                    try:
                        parsed[fname] = json_object(path.read_bytes(), fname)
                    except ConfigError as exc:
                        item.ok = False
                        failures.append(f"{fname}: {exc}")
            checks.append(item)

        return ArtifactCheck(
            name=meta.name,
            version=meta.version,
            ok=not failures,
            meta=meta,
            failures=failures,
            files=checks,
            config=parsed.get("config.json", {}),
            release=parsed.get("release.json", {}),
        )

    def _fetch_one(self, meta: ModelMeta, fname: str, local: Path, want: str, *, force: bool) -> Path:
        if not force and local.exists() and sha256_file(local) == want:
            return local
        key = f"{meta.name}/{meta.version}/{fname}"
        buf = io.BytesIO()
        try:
            self._s3.download_fileobj(self.bucket, key, buf)
        except (BotoCoreError, ClientError) as exc:
            raise ConfigError(f"bundle download failed: {key}", context={"key": key}) from exc
        blob = buf.getvalue()
        got = hashlib.sha256(blob).hexdigest()
        if got != want:
            raise ModelInferenceError(
                "sha256 mismatch after download",
                context={"key": key, "expected": want, "got": got},
            )
        tmp = local.with_suffix(local.suffix + ".part")
        tmp.write_bytes(blob)
        tmp.replace(local)
        return local

    def _put(self, key: str, blob: bytes) -> None:
        try:
            self._s3.put_object(Bucket=self.bucket, Key=key, Body=blob)
        except (BotoCoreError, ClientError) as exc:
            raise ConfigError(f"upload failed: {key}", context={"key": key}) from exc
