"""Multi-file artifacts in the weights registry (hopscotch-v1 and friends).

No network: a fake S3 client holds the objects in a dict. The properties that
matter are the ones that break a production roll-out — a manifest that disagrees
with what was uploaded, a half-present bundle, a moved 'latest' pointer — not
"did boto3 build the right signed request".
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from captcha.adapters.weights import Registry
from captcha.adapters.weights.artifact import HOPSCOTCH_V1, SPECS, spec_for
from captcha.adapters.weights.meta import ModelMeta, encode_meta
from captcha.domain.errors import ConfigError, ModelInferenceError

BUCKET = "models"
NAME = "hopscotch-v1"
VERSION = "v1"

CONFIG = {"model_type": "dinov3_vit_b16", "hidden_size": 768, "num_queries": 9}
RELEASE = {"threshold": 0.62, "calibration": {"method": "platt"}, "rejector": {"max_entropy": 1.4}}


def _missing(key: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": key}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "GetObject",
    )


class _Paginator:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def paginate(self, *, Bucket: str, Prefix: str = "", Delimiter: str = "") -> list[dict[str, Any]]:
        keys = sorted(key for key in self._objects if key.startswith(Prefix))
        if not Delimiter:
            return [{"Contents": [{"Key": key, "Size": len(self._objects[key])} for key in keys]}]
        prefixes: set[str] = set()
        for key in keys:
            tail = key[len(Prefix) :]
            if Delimiter in tail:
                prefixes.add(f"{Prefix}{tail.split(Delimiter, 1)[0]}{Delimiter}")
        return [{"CommonPrefixes": [{"Prefix": p} for p in sorted(prefixes)]}]


class FakeS3:
    """Just the three S3 calls the registry makes."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> dict[str, Any]:
        if Bucket != BUCKET:
            raise AssertionError(f"wrong bucket {Bucket}")
        self.objects[Key] = bytes(Body)
        self.puts.append(Key)
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise _missing(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def download_fileobj(self, Bucket: str, Key: str, buf: io.BytesIO, **kwargs: Any) -> None:
        if Key not in self.objects:
            raise _missing(Key)
        buf.write(self.objects[Key])

    def get_paginator(self, name: str) -> _Paginator:
        assert name == "list_objects_v2"
        return _Paginator(self.objects)


def _registry(tmp_path: Path, s3: FakeS3) -> Registry:
    return Registry(
        s3_endpoint="http://minio.invalid:9000",
        bucket=BUCKET,
        client=s3,
        cache_dir=tmp_path / "cache",
    )


def _bundle_dir(tmp_path: Path, *, config: Any = None, release: Any = None) -> Path:
    root = tmp_path / NAME
    root.mkdir(parents=True, exist_ok=True)
    (root / "model.safetensors").write_bytes(b"SAFETENSORS" + bytes(range(64)))
    (root / "config.json").write_text(json.dumps(CONFIG if config is None else config), encoding="utf-8")
    (root / "release.json").write_text(json.dumps(RELEASE if release is None else release), encoding="utf-8")
    return root


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


# ---- publish ---------------------------------------------------------------


def test_publish_artifact_writes_bundle_layout(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    bundle = _bundle_dir(tmp_path)

    meta = registry.publish_artifact(NAME, VERSION, bundle, labels={"role": "hopscotch"})

    assert meta.layout == "bundle"
    assert meta.artifact == HOPSCOTCH_V1.artifact == NAME
    assert meta.framework == "safetensors"
    assert meta.s3_key == f"{NAME}/{VERSION}/model.safetensors"
    assert meta.sha256 == _sha((bundle / "model.safetensors").read_bytes())
    assert meta.labels == {"role": "hopscotch"}
    assert set(meta.files) == {"model.safetensors", "config.json", "release.json"}
    assert s3.objects[f"{NAME}/{VERSION}/model.safetensors"] == (bundle / "model.safetensors").read_bytes()
    assert s3.objects[f"{NAME}/latest"] == VERSION.encode()
    assert f"{NAME}/{VERSION}/meta.json" in s3.objects


def test_publish_from_directory_through_plain_publish(tmp_path: Path) -> None:
    """make publish-model passes PATH straight through: a directory means a bundle."""
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)

    meta = registry.publish(NAME, VERSION, _bundle_dir(tmp_path))

    assert meta.layout == "bundle"
    assert registry.get_meta(NAME, "latest").version == VERSION


def test_publish_artifact_accepts_explicit_file_mapping(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)

    meta = registry.publish_artifact(
        "custom-visual",
        "v3",
        {
            "model.safetensors": b"W" * 32,
            "config.json": b"{}",
            "release.json": b"{\"threshold\": 0.5}",
        },
        set_latest=False,
    )

    assert meta.layout == "bundle"
    assert meta.artifact == "custom-visual"
    assert f"{NAME}/latest" not in s3.objects  # other model untouched
    assert "custom-visual/latest" not in s3.objects


def test_publish_rejects_incomplete_bundle(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    bundle = _bundle_dir(tmp_path)
    (bundle / "release.json").unlink()

    with pytest.raises(ConfigError, match=r"missing release\.json"):
        registry.publish_artifact(NAME, VERSION, bundle)

    assert s3.objects == {}, "nothing may be uploaded before the bundle is valid"


def test_publish_rejects_broken_config_json(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    bundle = _bundle_dir(tmp_path)
    (bundle / "config.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"config\.json is not valid JSON"):
        registry.publish_artifact(NAME, VERSION, bundle)


def test_publish_rejects_stray_files(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    bundle = _bundle_dir(tmp_path)
    (bundle / "notes.txt").write_text("scratch", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"unexpected files: notes\.txt"):
        registry.publish_artifact(NAME, VERSION, bundle)


# ---- check -----------------------------------------------------------------


def test_check_artifact_verifies_every_file(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    registry.publish_artifact(NAME, VERSION, _bundle_dir(tmp_path))

    check = registry.check_artifact(NAME, VERSION)

    assert check.ok, check.failures
    assert [f.name for f in check.files] == ["config.json", "model.safetensors", "release.json"]
    assert all(f.ok for f in check.files)
    assert check.config["hidden_size"] == 768
    assert check.release["calibration"] == {"method": "platt"}
    payload = check.to_dict()
    assert payload["ok"] is True
    assert payload["layout"] == "bundle"
    assert payload["failures"] == []


def test_check_artifact_flags_tampered_weights(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    registry.publish_artifact(NAME, VERSION, _bundle_dir(tmp_path))
    s3.objects[f"{NAME}/{VERSION}/model.safetensors"] += b"evil"

    check = registry.check_artifact(NAME, VERSION)

    assert not check.ok
    assert any("model.safetensors" in f for f in check.failures)
    assert any("sha256 mismatch" in f for f in check.failures)
    assert not next(f for f in check.files if f.name == "model.safetensors").ok


def test_check_artifact_flags_missing_file(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    registry.publish_artifact(NAME, VERSION, _bundle_dir(tmp_path))
    del s3.objects[f"{NAME}/{VERSION}/config.json"]

    check = registry.check_artifact(NAME, VERSION)

    assert not check.ok
    assert any(f.startswith("config.json") for f in check.failures)


def test_check_artifact_reports_missing_meta(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeS3())

    check = registry.check_artifact("nope", "v1")

    assert not check.ok
    assert check.failures and check.failures[0].startswith("meta.json")
    assert check.files == []


def test_check_artifact_rejects_single_file_layout(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    weights = tmp_path / "rotate-v1.pt"
    weights.write_bytes(b"TORCH")
    registry.publish("rotate-v1", "v1", weights, framework="torch")

    check = registry.check_artifact("rotate-v1", "v1")

    assert not check.ok
    assert any("not a bundle" in f for f in check.failures)
    assert check.files == []


# ---- fetch -----------------------------------------------------------------


def test_get_artifact_materializes_and_parses_the_bundle(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    registry.publish_artifact(NAME, VERSION, _bundle_dir(tmp_path))

    artifact = registry.get_artifact(NAME, "latest")

    assert artifact.version == VERSION
    assert artifact.primary_path.name == "model.safetensors"
    assert artifact.primary_path.read_bytes().startswith(b"SAFETENSORS")
    assert set(artifact.files) == {"model.safetensors", "config.json", "release.json"}
    assert all(p.exists() for p in artifact.files.values())
    assert artifact.config["model_type"] == "dinov3_vit_b16"
    assert artifact.release["rejector"] == {"max_entropy": 1.4}
    assert artifact.directory == tmp_path / "cache" / NAME / VERSION


def test_download_of_a_bundle_verifies_the_whole_artifact(tmp_path: Path) -> None:
    """check-model goes through download(): a broken release.json must not pass."""
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    registry.publish_artifact(NAME, VERSION, _bundle_dir(tmp_path))
    s3.objects[f"{NAME}/{VERSION}/release.json"] = b"{\"threshold\": 0.0}"

    with pytest.raises(ModelInferenceError, match="sha256 mismatch"):
        registry.download(NAME, VERSION, force=True)


def test_download_of_a_bundle_returns_the_primary_file(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    registry.publish_artifact(NAME, VERSION, _bundle_dir(tmp_path))

    path = registry.download(NAME, VERSION)

    assert path.name == "model.safetensors"
    assert path.parent.name == VERSION
    assert not list(path.parent.glob("*.part"))


def test_publish_keeps_optional_checksums_sidecar(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    bundle = _bundle_dir(tmp_path)
    (bundle / "checksums.json").write_text('{"model.safetensors": "abc"}', encoding="utf-8")

    meta = registry.publish_artifact(NAME, VERSION, bundle)

    assert set(meta.files) == {"model.safetensors", "config.json", "release.json", "checksums.json"}
    assert s3.objects[f"{NAME}/{VERSION}/checksums.json"] == b'{"model.safetensors": "abc"}'

    check = registry.check_artifact(NAME, VERSION)
    assert check.ok, check.failures
    sidecar = next(f for f in check.files if f.name == "checksums.json")
    assert sidecar.ok
    assert "optional file" in sidecar.note
    assert check.extra_files == []


# ---- manifest-less bundles (hopscotch training kit) ------------------------


def _publish_manifestless(s3: FakeS3, tmp_path: Path) -> ModelMeta:
    """What ml/hopscotch/publish.py writes: no layout, no per-file manifest."""
    bundle = _bundle_dir(tmp_path)
    weights = (bundle / "model.safetensors").read_bytes()
    meta = ModelMeta(
        name=NAME,
        version=VERSION,
        framework="safetensors",
        s3_key=f"{NAME}/{VERSION}/model.safetensors",
        sha256=_sha(weights),
        size_bytes=len(weights),
    )
    s3.objects[f"{NAME}/{VERSION}/model.safetensors"] = weights
    s3.objects[f"{NAME}/{VERSION}/config.json"] = (bundle / "config.json").read_bytes()
    s3.objects[f"{NAME}/{VERSION}/release.json"] = (bundle / "release.json").read_bytes()
    s3.objects[f"{NAME}/{VERSION}/checksums.json"] = b'{"model.safetensors": "x"}'
    s3.objects[f"{NAME}/{VERSION}/meta.json"] = encode_meta(meta)
    s3.objects[f"{NAME}/latest"] = VERSION.encode()
    return meta


def test_check_artifact_accepts_manifestless_bundle(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    _publish_manifestless(s3, tmp_path)

    check = registry.check_artifact(NAME)

    assert check.ok, check.failures
    assert check.manifest_present is False
    assert [f.name for f in check.files] == ["config.json", "model.safetensors", "release.json"]
    assert all(f.ok for f in check.files)
    assert check.files[0].expected_sha256 == ""
    assert "no manifest entry" in check.files[0].note
    assert check.config["hidden_size"] == 768, "sidecars are still parsed"
    assert check.extra_files == ["checksums.json"]
    assert any("no file manifest" in w for w in check.warnings)


def test_check_artifact_manifestless_verifies_the_primary_digest(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    _publish_manifestless(s3, tmp_path)
    s3.objects[f"{NAME}/{VERSION}/model.safetensors"] += b"evil"

    check = registry.check_artifact(NAME)

    assert not check.ok
    assert any("model.safetensors" in f and "sha256 mismatch" in f for f in check.failures)


def test_check_artifact_manifestless_flags_a_missing_sidecar(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    _publish_manifestless(s3, tmp_path)
    del s3.objects[f"{NAME}/{VERSION}/config.json"]

    check = registry.check_artifact(NAME)

    assert not check.ok
    assert any(f.startswith("config.json") for f in check.failures)
    assert not next(f for f in check.files if f.name == "config.json").ok


# ---- latest / regressions --------------------------------------------------


def test_latest_pointer_rolls_back(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    registry.publish_artifact(NAME, "v1", _bundle_dir(tmp_path))
    registry.publish_artifact(NAME, "v2", _bundle_dir(tmp_path))
    assert registry.get_meta(NAME, "latest").version == "v2"

    registry.set_latest(NAME, "v1")

    assert registry.get_meta(NAME, "latest").version == "v1"
    assert registry.pick_version(NAME, traffic_id="abc").version == "v1"


def test_single_file_publish_is_unchanged(tmp_path: Path) -> None:
    s3 = FakeS3()
    registry = _registry(tmp_path, s3)
    weights = tmp_path / "rotate-v1.pt"
    weights.write_bytes(b"TORCH-WEIGHTS")

    meta = registry.publish("rotate-v1", "v1", weights, framework="torch")

    assert meta.layout == "single"
    assert meta.files == {}
    assert meta.s3_key == "rotate-v1/v1/weights.pt"
    assert registry.download("rotate-v1", "v1").read_bytes() == b"TORCH-WEIGHTS"


def test_spec_for_known_and_derived_artifacts() -> None:
    assert spec_for(NAME, ["config.json", "model.safetensors", "release.json"]) is HOPSCOTCH_V1
    derived = spec_for("custom-v1", ["model.safetensors", "config.json"])
    assert derived.artifact == "custom-v1"
    assert derived.primary == "model.safetensors"
    with pytest.raises(ConfigError, match=r"no model\.safetensors"):
        spec_for("custom-v1", ["config.json"])
    assert "hopscotch-v1" in SPECS
