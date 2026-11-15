"""check-model / expected-models for bundle artifacts.

A bundle (hopscotch-v1) is only usable if every file is present and intact, so
check-model has to print the per-file verdict, not just the weights digest. A
manifest-less artifact (published by the hopscotch training kit) must still be
checked, with the gap stated instead of silently passing.

No S3: the registry is faked at the CLI seam.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path
from typing import Any

import pytest

from captcha.adapters.weights.artifact import ArtifactCheck, FileCheck
from captcha.adapters.weights.meta import ModelMeta
from captcha.services import cli


def _meta(**overrides: Any) -> ModelMeta:
    base: dict[str, Any] = {
        "name": "hopscotch-v1",
        "version": "v1",
        "framework": "safetensors",
        "s3_key": "hopscotch-v1/v1/model.safetensors",
        "sha256": "a" * 64,
        "size_bytes": 4096,
    }
    base.update(overrides)
    return ModelMeta(**base)


def _file(name: str, *, ok: bool = True, **overrides: Any) -> FileCheck:
    base: dict[str, Any] = {
        "name": name,
        "expected_sha256": "b" * 64,
        "actual_sha256": "b" * 64,
        "size_bytes": 1024,
        "ok": ok,
    }
    base.update(overrides)
    return FileCheck(**base)


class FakeRegistry:
    def __init__(
        self,
        meta: ModelMeta,
        *,
        check: ArtifactCheck | None = None,
        download_path: Path | None = None,
    ) -> None:
        self.meta = meta
        self.check = check
        self.download_path = download_path
        self.checked: list[tuple[str, str]] = []
        self.loaded: list[tuple[str, str]] = []
        self.downloaded: list[str] = []
        self.meta_calls: list[tuple[str, str]] = []

    def get_meta(self, name: str, version: str = "latest") -> ModelMeta:
        self.meta_calls.append((name, version))
        return self.meta

    def check_artifact(self, name: str, version: str = "latest", *, force: bool = True) -> ArtifactCheck:
        self.checked.append((name, version))
        assert self.check is not None
        return self.check

    def download(self, name: str, version: str = "latest", *, force: bool = False) -> Path:
        self.downloaded.append(f"{name}@{version}")
        assert self.download_path is not None
        return self.download_path

    def load_safetensors(self, name: str, version: str = "latest", *, device: str = "cpu") -> dict[str, Any]:
        self.loaded.append((name, version))
        return {}


def _install(monkeypatch: pytest.MonkeyPatch, registry: FakeRegistry) -> FakeRegistry:
    monkeypatch.setattr(cli, "_weights_registry", lambda: registry)
    return registry


def _args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {"name": "hopscotch-v1", "version": "latest", "load": False}
    base.update(overrides)
    return argparse.Namespace(**base)


def test_check_model_bundle_prints_every_file(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    check = ArtifactCheck(
        name="hopscotch-v1",
        version="v1",
        ok=True,
        meta=_meta(layout="bundle", artifact="hopscotch-v1"),
        manifest_present=True,
        files=[_file("model.safetensors"), _file("config.json"), _file("release.json")],
        extra_files=["checksums.json"],
    )
    registry = _install(monkeypatch, FakeRegistry(_meta(layout="bundle"), check=check))

    code = cli.cmd_check_model(_args())

    out = capsys.readouterr().out
    assert code == 0
    assert "layout   bundle" in out
    assert "ok   model.safetensors" in out
    assert "ok   config.json" in out
    assert "ok   release.json" in out
    assert "bundle   ok  files=3/3" in out
    assert "checksums.json" in out
    assert registry.checked == [("hopscotch-v1", "v1")], "the resolved version is checked, not 'latest'"


def test_check_model_bundle_failure_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    check = ArtifactCheck(
        name="hopscotch-v1",
        version="v1",
        ok=False,
        meta=_meta(layout="bundle"),
        manifest_present=True,
        failures=["release.json: sha256 mismatch"],
        files=[_file("model.safetensors"), _file("release.json", ok=False, actual_sha256="c" * 64)],
    )
    _install(monkeypatch, FakeRegistry(_meta(layout="bundle"), check=check))

    code = cli.cmd_check_model(_args())

    captured = capsys.readouterr()
    assert code == 1
    assert "FAIL release.json" in captured.out
    assert "check failed: release.json: sha256 mismatch" in captured.err
    assert "bundle   ok" not in captured.out


def test_check_model_manifestless_bundle_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The training kit publishes meta.json without a file manifest."""
    meta = _meta()  # layout="single", s3_key .../model.safetensors
    check = ArtifactCheck(
        name="hopscotch-v1",
        version="v1",
        ok=True,
        meta=meta,
        manifest_present=False,
        warnings=["meta.json carries no file manifest: sidecar sha256 cannot be verified"],
        files=[
            _file("model.safetensors"),
            _file("config.json", expected_sha256="", note="no manifest entry: sha256 not verifiable"),
            _file("release.json", expected_sha256="", note="no manifest entry: sha256 not verifiable"),
        ],
    )
    _install(monkeypatch, FakeRegistry(meta, check=check))

    code = cli.cmd_check_model(_args())

    out = capsys.readouterr().out
    assert code == 0, "presence + primary sha256 is enough to call it checked"
    assert "note  meta.json carries no file manifest" in out
    assert out.count("no manifest entry") == 2


def test_check_model_single_file_model_keeps_old_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    meta = _meta(name="rotate-v1", s3_key="rotate-v1/v1/weights.pt", framework="torch")
    weights = tmp_path / "weights.pt"
    weights.write_bytes(b"TORCH")
    registry = _install(monkeypatch, FakeRegistry(meta, download_path=weights))

    code = cli.cmd_check_model(_args(name="rotate-v1"))

    out = capsys.readouterr().out
    assert code == 0
    assert f"verified {weights}" in out
    assert "layout   bundle" not in out
    assert registry.checked == [], "single-file models do not go through the bundle check"


def test_check_model_load_flag_uses_the_framework_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    check = ArtifactCheck(
        name="hopscotch-v1",
        version="v1",
        ok=True,
        meta=_meta(layout="bundle"),
        manifest_present=True,
        files=[_file("model.safetensors")],
    )
    registry = _install(monkeypatch, FakeRegistry(_meta(layout="bundle"), check=check))

    assert cli.cmd_check_model(_args(load=True)) == 0
    assert registry.loaded == [("hopscotch-v1", "v1")]


def test_expected_models_lists_hopscotch_as_bundle(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.cmd_expected_models(argparse.Namespace())

    out = capsys.readouterr().out
    assert code == 0
    assert "hopscotch-v1" in out
    assert "layout  bundle" in out
    assert "files   model.safetensors, config.json, release.json" in out
    assert "layout  single" in out, "the other artifacts are still single-file"


def test_expected_models_layout_matches_the_adapter_spec() -> None:
    from captcha.adapters.weights.artifact import SPECS

    for name, expectation in cli.EXPECTED_MODELS.items():
        if expectation.layout != "bundle":
            continue
        spec = SPECS.get(name)
        assert spec is not None, f"{name} is listed as a bundle but has no ArtifactSpec"
        assert tuple(spec.files) == expectation.files
        assert spec.primary in expectation.files


def test_cv_extra_carries_the_cv_runtimes() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    doc = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    cv = doc["project"]["optional-dependencies"]["cv"]
    packages = [item.split(">")[0].split("=")[0].strip() for item in cv]

    assert "onnxruntime" in packages, "RT-DETR / rotate ONNX cannot load without it"
    assert "safetensors" in packages, "classification heads and hopscotch weights are safetensors"
    assert "transformers" in packages, "the frozen ViT backbone needs it"
    assert "torch" in packages
    assert "opencv-python-headless" in packages
