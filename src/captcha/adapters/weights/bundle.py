"""Upload / materialize / verify multi-file artifacts (shapes live in artifact.py).

Every file carries its own sha256 in meta.json, so a half-published or partially
overwritten artifact fails loudly instead of loading wrong weights. check() is
the whole-bundle version of what download() does for a single blob.

Artifacts published by the hopscotch training kit (ml/hopscotch/publish.py) have
no manifest: meta.json only names the weights. check() still verifies those -
primary sha256 from meta.json, sidecars for presence and JSON shape - and says so
in warnings, so "publish through Registry.publish_artifact to get per-file
sha256" is visible in the report instead of being a silent gap.
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
    is_bundle,
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
        labels: Mapping[str, Any] | None = None,
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
        extra = [f for f in spec.optional_files if f in payload]
        shipped = (*spec.files, *extra)
        digests = {fname: hashlib.sha256(payload[fname]).hexdigest() for fname in shipped}
        for fname in shipped:
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
            files=dict(digests),
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
        paths = {fname: self.fetch_one(meta, fname, dest / fname, declared[fname], force=force) for fname in declared}
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

        declared = dict(meta.files)
        if not is_bundle(meta):
            return ArtifactCheck(
                name=meta.name,
                version=meta.version,
                ok=False,
                meta=meta,
                failures=[f"not a bundle: layout={meta.layout!r}, primary={meta.s3_key}"],
                manifest_present=bool(declared),
            )

        primary = Path(meta.s3_key).name
        spec = SPECS.get(meta.artifact or name)
        required = list(spec.files) if spec is not None else [primary]
        failures: list[str] = []
        warnings: list[str] = []
        if not declared:
            warnings.append("meta.json carries no file manifest: sidecar sha256 cannot be verified")
            warnings.append("publish via Registry.publish_artifact to get per-file sha256")

        dest = self.cache_dir / meta.name / meta.version
        dest.mkdir(parents=True, exist_ok=True)
        checks: list[FileCheck] = []
        parsed: dict[str, Any] = {}
        for fname in sorted(set(required) | set(declared)):
            expected = declared.get(fname, "") or (meta.sha256 if fname == primary else "")
            item = FileCheck(name=fname, expected_sha256=expected)
            try:
                path = self.fetch_one(meta, fname, dest / fname, expected, force=force)
            except CaptchaError as exc:
                item.error = str(exc)
                failures.append(f"{fname}: {exc}")
            else:
                item.actual_sha256 = sha256_file(path)
                item.size_bytes = path.stat().st_size
                item.ok = True
                if expected and item.actual_sha256 != expected:
                    item.ok = False
                    failures.append(f"{fname}: sha256 mismatch")
                elif not expected:
                    item.note = "no manifest entry: sha256 not verifiable"
                if fname.endswith(".json"):
                    try:
                        parsed[fname] = json_object(path.read_bytes(), fname)
                    except ConfigError as exc:
                        item.ok = False
                        failures.append(f"{fname}: {exc}")
                if fname not in required:
                    kind = "optional file" if spec is not None and fname in spec.optional_files else "extra file"
                    item.note = (item.note + " " if item.note else "") + f"{kind}, not required"
            checks.append(item)

        return ArtifactCheck(
            name=meta.name,
            version=meta.version,
            ok=not failures,
            meta=meta,
            failures=failures,
            warnings=warnings,
            files=checks,
            extra_files=self._list_extra(meta, set(declared) | set(required), warnings),
            config=parsed.get("config.json", {}),
            release=parsed.get("release.json", {}),
            manifest_present=bool(declared),
        )

    def fetch_one(self, meta: ModelMeta, fname: str, local: Path, want: str, *, force: bool) -> Path:
        """Download one bundle file; verify sha256 when a digest is known."""
        if not force and local.exists() and (not want or sha256_file(local) == want):
            return local
        key = f"{meta.name}/{meta.version}/{fname}"
        buf = io.BytesIO()
        try:
            self._s3.download_fileobj(self.bucket, key, buf)
        except (BotoCoreError, ClientError) as exc:
            raise ConfigError(f"bundle download failed: {key}", context={"key": key}) from exc
        blob = buf.getvalue()
        got = hashlib.sha256(blob).hexdigest()
        if want and got != want:
            raise ModelInferenceError(
                "sha256 mismatch after download",
                context={"key": key, "expected": want, "got": got},
            )
        tmp = local.with_suffix(local.suffix + ".part")
        tmp.write_bytes(blob)
        tmp.replace(local)
        return local

    def _list_extra(self, meta: ModelMeta, verified: set[str], warnings: list[str]) -> list[str]:
        """Objects under the artifact prefix that check() did not look at."""
        prefix = f"{meta.name}/{meta.version}/"
        try:
            paginator = self._s3.get_paginator("list_objects_v2")
            present: set[str] = set()
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents") or ():
                    tail = str(obj.get("Key", ""))[len(prefix) :]
                    if tail and "/" not in tail and tail != BUNDLE_META_FILE:
                        present.add(tail)
        except (BotoCoreError, ClientError) as exc:
            warnings.append(f"cannot list {prefix}: {exc}")
            return []
        return sorted(present - verified)

    def _put(self, key: str, blob: bytes) -> None:
        try:
            self._s3.put_object(Bucket=self.bucket, Key=key, Body=blob)
        except (BotoCoreError, ClientError) as exc:
            raise ConfigError(f"upload failed: {key}", context={"key": key}) from exc
