"""Artifact contracts and local shapes for multi-file model bundles.

Single-slot models keep <name>/<version>/weights.<ext>. Visual artifacts ship
as an HF-style directory (production contract section 9 - hopscotch-v1 and the
ten docs/ml-tz.md artifacts):

    <name>/<version>/model.safetensors
    <name>/<version>/config.json
    <name>/<version>/release.json      calibration + rejector
    <name>/<version>/meta.json         ModelMeta(layout="bundle", files={name: sha256})
    <name>/latest                      version string

The upload/verify machinery lives in bundle.py; this module owns what a bundle
is and how one is read off disk.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from captcha.domain.errors import ConfigError

from .meta import Framework, ModelMeta

BUNDLE_META_FILE = "meta.json"
DEFAULT_JSON_FILES = ("config.json", "release.json")
PRIMARY_CANDIDATES = ("model.safetensors", "model.safetensors.index.json")


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    """Which files an artifact must carry and which one a loader opens first."""

    artifact: str
    files: tuple[str, ...]
    primary: str
    json_files: tuple[str, ...] = DEFAULT_JSON_FILES
    framework: Framework = "safetensors"


#: hopscotch-v1: DINOv3 ViT-B/16 backbone + query head, calibration and rejector.
HOPSCOTCH_V1 = ArtifactSpec(
    artifact="hopscotch-v1",
    files=("model.safetensors", "config.json", "release.json"),
    primary="model.safetensors",
)

#: Known bundle artifacts. Unknown names are still publishable - the file set in
#: the directory becomes the contract (see spec_for).
SPECS: dict[str, ArtifactSpec] = {spec.artifact: spec for spec in (HOPSCOTCH_V1,)}


@dataclass(slots=True)
class Artifact:
    """A fully materialized bundle on local disk."""

    name: str
    version: str
    directory: Path
    meta: ModelMeta
    files: dict[str, Path]
    primary: str
    config: dict[str, Any] = field(default_factory=dict)
    release: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_path(self) -> Path:
        return self.files[self.primary]


@dataclass(slots=True)
class FileCheck:
    name: str
    expected_sha256: str
    actual_sha256: str = ""
    size_bytes: int = 0
    ok: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "size_bytes": self.size_bytes,
            "ok": self.ok,
            "error": self.error,
        }


@dataclass(slots=True)
class ArtifactCheck:
    """Result of check-model for a bundle: what is in the bucket, and is it whole."""

    name: str
    version: str
    ok: bool
    meta: ModelMeta | None = None
    failures: list[str] = field(default_factory=list)
    files: list[FileCheck] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    release: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "ok": self.ok,
            "artifact": self.meta.artifact if self.meta else "",
            "layout": self.meta.layout if self.meta else "",
            "framework": self.meta.framework if self.meta else "",
            "primary_sha256": self.meta.sha256 if self.meta else "",
            "failures": list(self.failures),
            "files": [f.to_dict() for f in self.files],
            "config": dict(self.config),
            "release": dict(self.release),
        }

def spec_for(name: str, files: Iterable[str]) -> ArtifactSpec:
    """Resolve the contract for name; unknown artifacts derive one from files."""
    known = SPECS.get(name)
    if known is not None:
        return known
    present = tuple(files)
    primary = next((f for f in PRIMARY_CANDIDATES if f in present), None)
    if primary is None:
        raise ConfigError(
            f"unknown artifact {name!r} and no model.safetensors in the bundle",
            context={"files": list(present), "known": sorted(SPECS)},
        )
    return ArtifactSpec(artifact=name, files=present, primary=primary)


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for buf in iter(lambda: f.read(chunk), b""):
            h.update(buf)
    return h.hexdigest()


def read_bundle(source: str | Path | Mapping[str, bytes | Path | str]) -> dict[str, bytes]:
    """Load a bundle from a directory or an explicit {name: bytes|path} mapping."""
    if isinstance(source, Mapping):
        out: dict[str, bytes] = {}
        for fname, value in source.items():
            name = str(fname)
            if name != Path(name).name:
                raise ConfigError(f"bundle file name must be flat: {name!r}")
            try:
                out[name] = bytes(value) if isinstance(value, bytes | bytearray) else Path(value).read_bytes()
            except OSError as exc:
                raise ConfigError(f"cannot read bundle file {name}: {exc}", context={"path": str(value)}) from exc
        return out

    root = Path(source)
    if not root.is_dir():
        raise ConfigError("bundle source must be a directory", context={"path": str(root)})
    try:
        files = {p.name: p.read_bytes() for p in sorted(root.iterdir()) if p.is_file() and not p.name.startswith(".")}
    except OSError as exc:
        raise ConfigError(f"cannot read bundle directory: {exc}", context={"path": str(root)}) from exc
    if not files:
        raise ConfigError("bundle directory is empty", context={"path": str(root)})
    return files


def validate_bundle(payload: Mapping[str, bytes], spec: ArtifactSpec) -> None:
    """Fail before uploading anything: wrong file set, empty weights, broken JSON."""
    missing = [f for f in spec.files if f not in payload]
    if missing:
        raise ConfigError(
            f"bundle is missing {', '.join(missing)}",
            context={"artifact": spec.artifact, "have": sorted(payload)},
        )
    unexpected = sorted(set(payload) - set(spec.files) - {BUNDLE_META_FILE})
    if unexpected:
        raise ConfigError(
            f"bundle has unexpected files: {', '.join(unexpected)}",
            context={"artifact": spec.artifact, "expected": list(spec.files)},
        )
    if not payload[spec.primary]:
        raise ConfigError(f"primary file is empty: {spec.primary}", context={"artifact": spec.artifact})
    for fname in spec.json_files:
        if fname in payload:
            json_object(payload[fname], fname)


def json_object(blob: bytes, fname: str) -> dict[str, Any]:
    try:
        doc = json.loads(blob)
    except ValueError as exc:
        raise ConfigError(f"{fname} is not valid JSON: {exc}", context={"file": fname}) from exc
    if not isinstance(doc, dict):
        raise ConfigError(f"{fname} must be a JSON object", context={"file": fname, "type": type(doc).__name__})
    return doc


def load_json_object(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json_object(path.read_bytes(), path.name)
    except ConfigError:
        return {}
