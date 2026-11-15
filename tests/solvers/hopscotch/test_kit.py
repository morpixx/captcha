"""Kit tests: frozen split, export, publish, and the commands they wrap.

No torch and no training checkout are needed: the converter, the S3 client and
the evaluator subprocess are all injected. A fake converter is enough to prove
that a run becomes a valid artifact triple, which is the part that can break
silently between training and serving.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import release_payload
from ml.hopscotch.dataset import (
    load_dataset,
    merge_sources,
    validate_annotation,
    verify_sources,
    write_manifest,
)
from ml.hopscotch.evaluate import evaluate_command, score_predictions, summarize_report
from ml.hopscotch.export import build_config, build_release, export_run, verify_artifact
from ml.hopscotch.publish import build_meta, publish
from ml.hopscotch.split import import_upstream_roles
from ml.hopscotch.train import train_plan

from captcha.domain.errors import ConfigError

# ---- synthetic sources ------------------------------------------------------


def _annotation(wave: str, frames: int = 5) -> dict[str, Any]:
    nodes = []
    for index in range(frames):
        on = "k1" if index == 2 else "k2"
        nodes.append(
            {
                "fig": {"foot": [index * 200 + 80, 90], "on": on},
                "nodes": {
                    "k1": {"pt": [index * 200 + 50, 60], "r": 12, "vis": 2},
                    "k2": {"pt": [index * 200 + 90, 70], "r": 14, "vis": 1},
                    "k3": {"pt": [index * 200 + 120, 40], "r": 10, "vis": 0},
                    "k4": {"pt": [index * 200 + 150, 30], "r": 11, "vis": 2},
                },
            }
        )
    return {
        "v": 2,
        "wave": wave,
        "hint": {"pt": None, "r": None, "key": "k1"},
        "frames": nodes,
        "answer": 2,
        "flags": [],
    }


def _write_sources(root: Path, *, manual: int = 150, client: int = 700) -> Path:
    dataset = root / "dataset"
    (dataset / "raw").mkdir(parents=True)
    (dataset / "splits").mkdir(parents=True)
    annotation_lines = [
        json.dumps(_annotation(f"m{index}:wave_0")) for index in range(manual)
    ]
    (dataset / "annotations.jsonl").write_text("\n".join(annotation_lines) + "\n", encoding="utf-8")
    client_lines = [
        json.dumps(
            {
                "session_id": f"c{index}",
                "wave": index % 7,
                "difficulty": 5,
                "submitted_index": 3,
                "image": f"sessions/c{index}/wave_{index % 7}.jpg",
            }
        )
        for index in range(client)
    ]
    (dataset / "raw" / "index.jsonl").write_text("\n".join(client_lines) + "\n", encoding="utf-8")
    return root


# ---- dataset / split --------------------------------------------------------


def test_annotation_validator_accepts_a_good_wave() -> None:
    validate_annotation(_annotation("m0:wave_0"))


def test_annotation_validator_rejects_answer_mismatch() -> None:
    broken = _annotation("m0:wave_0")
    broken["answer"] = 1
    with pytest.raises(ValueError):
        validate_annotation(broken)


def test_annotation_validator_rejects_flag() -> None:
    flagged = _annotation("m0:wave_0")
    flagged["flags"] = ["ambiguous"]
    with pytest.raises(ValueError):
        validate_annotation(flagged)


def test_merge_shifts_client_answers_to_zero_based() -> None:
    rows = [
        {
            "session_id": "s1",
            "wave": 0,
            "difficulty": 5,
            "submitted_index": 5,
            "image": "sessions/s1/wave_0.jpg",
        }
    ]
    merged = merge_sources(rows, [_annotation("m0:wave_0")])
    by_wave = {record.wave: record for record in merged}
    assert by_wave["s1:wave_0"].answer == 4
    assert by_wave["s1:wave_0"].label_source == "client"
    assert by_wave["m0:wave_0"].answer == 2
    assert by_wave["m0:wave_0"].label_source == "manual"


def test_derived_split_matches_the_protocol(tmp_path: Path) -> None:
    root = _write_sources(tmp_path)
    dataset = load_dataset(root, hash_images=False)
    counts = dataset.manifest["counts"]
    assert counts["gold_train"] == 110
    assert counts["gold_val"] == 30
    assert counts["honeypot_probe"] == 10
    assert counts["client_val"] == 400
    assert counts["calibration"] >= 20
    assert counts["silver_train"] == 700 - counts["calibration"] - 400


def test_manifest_is_frozen_and_verifiable(tmp_path: Path) -> None:
    root = _write_sources(tmp_path)
    dataset = load_dataset(root, hash_images=False)
    manifest_path = write_manifest(dataset, tmp_path / "split.json")
    assert verify_sources(manifest_path, root)["counts"]["gold_train"] == 110
    with pytest.raises(FileExistsError):
        write_manifest(dataset, manifest_path)
    with (root / "dataset" / "raw" / "index.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("\n")
    with pytest.raises(ValueError):
        verify_sources(manifest_path, root)


def test_upstream_roles_can_be_imported(tmp_path: Path) -> None:
    root = _write_sources(tmp_path)
    upstream = root / "dataset" / "splits" / "development.json"
    upstream.write_text(
        json.dumps(
            {
                "status": "frozen",
                "gold_train": ["m0:wave_0"],
                "gold_val": ["m1:wave_0"],
                "honeypot_probe": ["m2:wave_0"],
                "calibration": ["c0:wave_0"],
                "client_val": ["c1:wave_1"],
                "excluded": ["c2:wave_2"],
            }
        ),
        encoding="utf-8",
    )
    roles = import_upstream_roles(upstream)
    assert roles["c1:wave_1"] == "client_val"
    assert roles["c2:wave_2"] == "excluded"


def test_imported_roles_win_over_derivation(tmp_path: Path) -> None:
    root = _write_sources(tmp_path)
    upstream = root / "dataset" / "splits" / "development.json"
    dataset = load_dataset(root, hash_images=False)
    gold_train = [r.wave for r in dataset.records if r.role == "gold_train"][:3]
    upstream.write_text(
        json.dumps(
            {
                "status": "frozen",
                "gold_train": gold_train,
                "gold_val": ["m100:wave_0"],
                "honeypot_probe": [],
                "calibration": [],
                "client_val": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        # Imported roles cannot satisfy the 110/30/10 protocol, so the kit refuses.
        load_dataset(root, upstream=upstream, hash_images=False)


# ---- export -----------------------------------------------------------------


CHECKPOINT_BYTES = b"checkpoint-bytes"
CHECKPOINT_SHA = hashlib.sha256(CHECKPOINT_BYTES).hexdigest()


def _fake_converter(checkpoint: Path, weights: Path) -> dict[str, Any]:
    weights.write_bytes(b"fake-safetensors-bytes")
    return {
        "config": {
            "model": {
                "architecture": "spatial",
                "hidden_dim": 128,
                "slots": 8,
                "image_size": 384,
                "crop_size": 128,
                "dropout": 0.1,
                "encoder": "models/dinov3-b16",
            },
            "train": {"encoder_chunk": 32, "amp": True, "epochs": 90},
        },
        "backbone_config": {"hidden_size": 768, "patch_size": 16, "num_hidden_layers": 12},
        "epoch": 39,
        "state_keys": 294,
        "checkpoint_sha256": CHECKPOINT_SHA,
    }


def _run_dir(tmp_path: Path, release: dict[str, Any] | None) -> Path:
    run = tmp_path / "runs" / "unit"
    run.mkdir(parents=True)
    (run / "best.pt").write_bytes(CHECKPOINT_BYTES)
    if release is not None:
        (run / "release.json").write_text(json.dumps(release), encoding="utf-8")
    return run


def test_build_config_keeps_only_serving_fields() -> None:
    config = build_config(
        version="v1",
        state_config={
            "model": {
                "architecture": "spatial",
                "hidden_dim": 128,
                "slots": 8,
                "image_size": 384,
                "crop_size": 128,
                "dropout": 0.1,
            },
            "train": {"encoder_chunk": 32, "amp": True, "epochs": 90, "seed": 42},
        },
        backbone_config={"hidden_size": 768},
        checkpoint_epoch=39,
        source={"run": "unit"},
    )
    assert config["train"] == {"encoder_chunk": 32, "amp": True}
    assert config["input"]["frames"] == [5, 8]
    assert config["format"] == "hopscotch-v1-config"


def test_build_config_rejects_incomplete_model_settings() -> None:
    with pytest.raises(ConfigError):
        build_config(
            version="v1",
            state_config={"model": {"architecture": "spatial"}, "train": {}},
            backbone_config={},
            checkpoint_epoch=0,
            source={},
        )


def test_build_release_without_calibration_is_not_ready() -> None:
    release = build_release(version="v1", upstream=None, metrics=None, checkpoint_sha256=CHECKPOINT_SHA)
    assert release["status"] == "not_ready"
    assert release["threshold"] is None
    assert release["scale"] == [1.0] * 8


def test_build_release_rejects_a_short_feature_vector() -> None:
    upstream = release_payload()
    upstream["mean"] = upstream["mean"][:4]
    upstream["format"] = "captcha-hopscotch-release-v1"
    with pytest.raises(ConfigError):
        build_release(version="v1", upstream=upstream, metrics=None, checkpoint_sha256=CHECKPOINT_SHA)


def test_export_produces_a_loadable_artifact(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, release_payload(model_id=CHECKPOINT_SHA))
    result = export_run(
        run,
        tmp_path / "out",
        version="v1-test",
        converter=_fake_converter,
        metrics=None,
    )
    assert result.release["status"] == "ready"
    assert result.release["metrics"] == {}
    assert set(result.checksums["files"]) == {"model.safetensors", "config.json", "release.json"}
    assert verify_artifact(result.directory).release.ready


def test_export_refuses_a_release_from_another_checkpoint(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, release_payload(model_id="c" * 64))
    with pytest.raises(ConfigError):
        export_run(run, tmp_path / "out", converter=_fake_converter)
    assert not (tmp_path / "out" / "model.safetensors").exists()


def test_export_allow_mismatch_documents_the_override(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, release_payload(model_id="c" * 64))
    result = export_run(
        run, tmp_path / "out", converter=_fake_converter, allow_mismatch=True
    )
    assert result.release["source"]["checkpoint_sha256"] == CHECKPOINT_SHA


def test_verify_artifact_detects_tampering(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, release_payload(model_id=CHECKPOINT_SHA))
    result = export_run(run, tmp_path / "out", converter=_fake_converter)
    (result.directory / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError):
        verify_artifact(result.directory)


# ---- publish ----------------------------------------------------------------


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[f"{Bucket}/{Key}"] = Body


def test_publish_uploads_sidecars_and_latest(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, release_payload(model_id=CHECKPOINT_SHA))
    artifact = export_run(run, tmp_path / "out", version="v1", converter=_fake_converter)
    s3 = FakeS3()
    result = publish(
        artifact.directory,
        version="v1",
        s3=s3,
        bucket="captcha",
        cache_dir=tmp_path / "cache",
    )
    assert "captcha/hopscotch-v1/v1/model.safetensors" in s3.objects
    assert "captcha/hopscotch-v1/v1/config.json" in s3.objects
    assert "captcha/hopscotch-v1/v1/release.json" in s3.objects
    assert s3.objects["captcha/hopscotch-v1/latest"] == b"v1"
    assert result.cache_dir is not None and (result.cache_dir / "model.safetensors").is_file()
    meta = json.loads(s3.objects["captcha/hopscotch-v1/v1/meta.json"])
    assert meta["s3_key"] == "hopscotch-v1/v1/model.safetensors"
    assert meta["framework"] == "safetensors"


def test_publish_refuses_not_ready_release(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, release_payload(model_id=CHECKPOINT_SHA, status="not_ready", threshold=None))
    artifact = export_run(run, tmp_path / "out", converter=_fake_converter)
    with pytest.raises(ConfigError):
        publish(artifact.directory, s3=FakeS3(), bucket="captcha")
    allowed = publish(
        artifact.directory, s3=FakeS3(), bucket="captcha", allow_not_ready=True
    )
    assert allowed.artifact.release.status == "not_ready"


def test_meta_matches_registry_expectations(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, release_payload(model_id=CHECKPOINT_SHA))
    artifact = export_run(run, tmp_path / "out", version="v1", converter=_fake_converter)
    meta = build_meta("hopscotch-v1", "v1", artifact.directory / "model.safetensors", artifact.artifact)
    assert meta["size_bytes"] == len(b"fake-safetensors-bytes")
    assert meta["input_schema"]["frames"] == [5, 8]


# ---- commands ---------------------------------------------------------------


def _fake_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "v2"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "v1.py").write_text("", encoding="utf-8")
    return root


def test_train_plan_calls_the_upstream_trainer(tmp_path: Path) -> None:
    root = _fake_checkout(tmp_path)
    plan = train_plan(
        root=root,
        python="training-python",
        run="readout-v2",
        stage="readout",
        epochs=24,
        extra=["--warmup-epochs", "0"],
    )
    assert plan.command[:5] == [
        "training-python",
        str(root / "scripts" / "v1.py"),
        "--config",
        "configs/v1-spatial.yaml",
        "train",
    ]
    assert plan.command[-8:] == [
        "--run",
        "readout-v2",
        "--stage",
        "readout",
        "--epochs",
        "24",
        "--warmup-epochs",
        "0",
    ]
    assert plan.cwd == root


def test_evaluate_command_targets_a_role(tmp_path: Path) -> None:
    command = evaluate_command(
        tmp_path,
        checkpoint=tmp_path / "best.pt",
        role="gold_val",
        output=tmp_path / "report.json",
        python="training-python",
    )
    assert command[0] == "training-python"
    assert command[4:6] == ["evaluate", "--checkpoint"]


def test_summarize_report_keeps_comparable_numbers() -> None:
    summary = summarize_report(
        {
            "role": "gold_val",
            "metrics": {
                "total": 30,
                "correct": 27,
                "answer_accuracy": 0.9,
                "coverage": 0.8,
                "records": [1, 2, 3],
            },
        }
    )
    assert summary["answer_accuracy"] == 0.9
    assert summary["total"] == 30
    assert "records" not in summary


def test_score_predictions_uses_the_contract_target() -> None:
    result = score_predictions({"a": 2, "b": 2}, {"a": 2, "b": 1}, index_base=0)
    assert result["answer_accuracy"] == 0.5
    assert result["target_met"] is False


def test_score_predictions_handles_one_based_output() -> None:
    result = score_predictions({"a": 3, "b": 1}, {"a": 3, "b": 1}, index_base=1)
    assert result["correct"] == 2
    assert result["target_met"] is True
