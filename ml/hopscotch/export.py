"""Training kit, step 3: export a run into the serving artifact triple.

Input:  runs/<run>/best.pt (+ release.json from calibration, + metrics from eval)
Output: model.safetensors, config.json, release.json, checksums.json

What this does NOT do: train, calibrate, or touch the architecture. The checkpoint
is a hopscotch-v1-checkpoint written by hopscotch_v2; we freeze it into a format
the service can load and validate without importing the trainer.

Torch is imported lazily and only inside the default converter, so the rest of
this module (and its tests) run in a worker venv without --extra cv.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from captcha.domain.errors import ConfigError
from captcha.solvers.hopscotch.artifact import (
    ARTIFACT_FORMAT,
    CHECKSUMS_FILE,
    CONFIG_FILE,
    CONFIG_FORMAT,
    FEATURES,
    GUARDS,
    PREPROCESSING,
    RELEASE_FILE,
    RELEASE_FORMAT,
    WEIGHTS_FILE,
    HopscotchArtifact,
    load_artifact,
)

MODEL_NAME = "hopscotch-v1"

#: Pixel layout the service validates payloads against (contract section 6).
INPUT_GEOMETRY: dict[str, Any] = {
    "image_height": 400,
    "card_width": 200,
    "card_height": 200,
    "hint_box": [0, 200, 135, 400],
    "frames": [5, 8],
    "index_base": 1,
}

#: Serving-only slice of the training config. Everything else stays in the run.
SERVING_TRAIN_KEYS = ("encoder_chunk", "amp")

#: converter(checkpoint, weights_path) -> metadata dict
Converter = Callable[[Path, Path], dict[str, Any]]


@dataclass(frozen=True)
class ExportedArtifact:
    directory: Path
    artifact: HopscotchArtifact
    config: dict[str, Any]
    release: dict[str, Any]
    checksums: dict[str, Any]


def build_config(
    *,
    version: str,
    state_config: dict[str, Any],
    backbone_config: dict[str, Any],
    checkpoint_epoch: int,
    source: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the serving half of a training config into config.json."""
    model = state_config.get("model")
    train = state_config.get("train")
    if not isinstance(model, dict) or not isinstance(train, dict):
        raise ConfigError("checkpoint config has no model/train sections")
    missing = [key for key in ("architecture", "hidden_dim", "slots", "image_size", "crop_size") if key not in model]
    if missing:
        raise ConfigError("checkpoint config is missing model fields", context={"missing": missing})
    serving_train = {key: train[key] for key in SERVING_TRAIN_KEYS if key in train}
    if "encoder_chunk" not in serving_train:
        raise ConfigError("checkpoint config has no train.encoder_chunk")
    return {
        "format": CONFIG_FORMAT,
        "model_name": MODEL_NAME,
        "version": version,
        "preprocessing": PREPROCESSING,
        "model": model,
        "train": serving_train,
        "backbone_config": backbone_config,
        "input": INPUT_GEOMETRY,
        "checkpoint_epoch": int(checkpoint_epoch),
        "source": source,
    }


def build_release(
    *,
    version: str,
    upstream: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
    checkpoint_sha256: str,
) -> dict[str, Any]:
    """Map a hopscotch_v2 release.json onto the service schema.

    Without an upstream release the artifact is exported uncalibrated: weights
    load, but decide() refuses to answer until a calibration run fills the
    rejector. That is the same rule the trainer enforces, restated here so an
    operator cannot publish a guessing model by accident.
    """
    source: dict[str, Any] = {"checkpoint_sha256": checkpoint_sha256}
    if upstream is None:
        threshold = None
        mean = [0.0] * len(FEATURES)
        scale = [1.0] * len(FEATURES)
        weights = [0.0] * len(FEATURES)
        bias = 0.0
        guards = dict(GUARDS)
        split_sha = ""
        status = "not_ready"
    else:
        status = str(upstream.get("status", "not_ready"))
        threshold = upstream.get("threshold")
        mean = _vector(upstream.get("mean"), "mean")
        scale = _vector(upstream.get("scale"), "scale")
        weights = _vector(upstream.get("weights"), "weights")
        bias = float(upstream.get("bias", 0.0))
        guards = dict(upstream.get("guards") or GUARDS)
        split_sha = str(upstream.get("manifest_hash") or "")
        source.update(
            upstream_release_id=upstream.get("release_id"),
            upstream_status=status,
            upstream_protocol=upstream.get("calibration_protocol"),
            upstream_model_id=upstream.get("model_id"),
        )
    if status not in ("ready", "not_ready"):
        raise ConfigError("upstream release has an unknown status", context={"status": status})
    return {
        "format": RELEASE_FORMAT,
        "model_name": MODEL_NAME,
        "version": version,
        "status": status,
        "preprocessing": PREPROCESSING,
        "features": list(FEATURES),
        "guards": guards,
        "threshold": threshold,
        "mean": mean,
        "scale": scale,
        "weights": weights,
        "bias": bias,
        "metrics": metrics or {},
        "split_sha256": split_sha,
        "source": source,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def convert_checkpoint(checkpoint: Path, weights_path: Path) -> dict[str, Any]:
    """Default converter: best.pt -> model.safetensors. Needs torch + safetensors."""
    try:
        import torch
        from safetensors.torch import save_file
    except ImportError as exc:
        raise ConfigError(
            "export needs torch + safetensors (run it in the training environment)",
            context={"hint": "D:/hopscotch_v2 uses the system Python 3.14, not the service venv"},
        ) from exc

    try:
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    except Exception as exc:
        raise ConfigError("cannot read checkpoint", context={"path": str(checkpoint), "error": str(exc)}) from exc
    if not isinstance(state, dict) or state.get("format") != "hopscotch-v1-checkpoint":
        raise ConfigError("unsupported checkpoint format", context={"path": str(checkpoint)})
    if state.get("preprocessing") != PREPROCESSING:
        raise ConfigError(
            "checkpoint preprocessing version mismatch",
            context={"expected": PREPROCESSING, "got": state.get("preprocessing")},
        )
    tensors = state.get("model")
    if not isinstance(tensors, dict) or not tensors:
        raise ConfigError("checkpoint carries no model state_dict", context={"path": str(checkpoint)})
    if "backbone_config" not in state:
        raise ConfigError("checkpoint carries no backbone_config; re-run training before export")

    weights_path.parent.mkdir(parents=True, exist_ok=True)
    save_file({key: value.contiguous() for key, value in tensors.items()}, str(weights_path))
    return {
        "config": state["config"],
        "backbone_config": state["backbone_config"],
        "epoch": int(state.get("epoch", 0)),
        "state_keys": len(tensors),
        "checkpoint_sha256": sha256_file(checkpoint),
    }


def export_run(
    run_dir: str | Path,
    out_dir: str | Path,
    *,
    version: str = "v1",
    checkpoint: str | Path | None = None,
    release: str | Path | None = None,
    metrics: str | Path | None = None,
    allow_mismatch: bool = False,
    converter: Converter = convert_checkpoint,
) -> ExportedArtifact:
    """Export one run. Raises on any inconsistent input instead of shipping it."""
    run = Path(run_dir)
    target = Path(out_dir)
    ckpt = Path(checkpoint) if checkpoint is not None else run / "best.pt"
    if not ckpt.is_file():
        raise FileNotFoundError(f"checkpoint not found: {ckpt}")

    upstream = _read_optional_json(Path(release)) if release is not None else _read_optional_json(run / "release.json")
    metrics_payload = _read_optional_json(Path(metrics)) if metrics is not None else None

    if upstream is not None and not allow_mismatch:
        # Verify identity before writing anything: a mismatch must not leave a
        # half-exported artifact that looks loadable.
        upstream_model = upstream.get("model_id")
        actual = sha256_file(ckpt)
        if upstream_model and upstream_model != actual:
            raise ConfigError(
                "release.json belongs to a different checkpoint",
                context={"release_model_id": upstream_model, "checkpoint": actual},
            )

    target.mkdir(parents=True, exist_ok=True)
    weights = target / WEIGHTS_FILE
    meta = converter(ckpt, weights)
    checkpoint_sha = str(meta.get("checkpoint_sha256") or sha256_file(ckpt))

    config = build_config(
        version=version,
        state_config=meta["config"],
        backbone_config=meta["backbone_config"],
        checkpoint_epoch=int(meta.get("epoch", 0)),
        source={
            "run": run.name,
            "checkpoint": ckpt.name,
            "checkpoint_sha256": checkpoint_sha,
            "state_keys": meta.get("state_keys"),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    release_payload = build_release(
        version=version,
        upstream=upstream,
        metrics=metrics_payload,
        checkpoint_sha256=checkpoint_sha,
    )
    _write_json(target / CONFIG_FILE, config)
    _write_json(target / RELEASE_FILE, release_payload)

    checksums = {
        "format": ARTIFACT_FORMAT,
        "model_name": MODEL_NAME,
        "version": version,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": {
            name: sha256_file(target / name)
            for name in (WEIGHTS_FILE, CONFIG_FILE, RELEASE_FILE)
        },
    }
    _write_json(target / CHECKSUMS_FILE, checksums)
    artifact = load_artifact(target)
    return ExportedArtifact(
        directory=target,
        artifact=artifact,
        config=config,
        release=release_payload,
        checksums=checksums,
    )


def verify_artifact(directory: str | Path) -> HopscotchArtifact:
    """Validate an existing artifact directory and its checksums."""
    root = Path(directory)
    artifact = load_artifact(root)
    checksums_path = root / CHECKSUMS_FILE
    if checksums_path.is_file():
        recorded = json.loads(checksums_path.read_text(encoding="utf-8")).get("files", {})
        for name, expected in recorded.items():
            frame = root / name
            if not frame.is_file():
                raise ConfigError("artifact is missing a checksummed file", context={"file": name})
            actual = sha256_file(frame)
            if actual != expected:
                raise ConfigError(
                    "artifact file changed after export",
                    context={"file": name, "expected": expected, "got": actual},
                )
    return artifact


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _vector(raw: Any, name: str) -> list[float]:
    if not isinstance(raw, list) or len(raw) != len(FEATURES):
        raise ConfigError(
            f"upstream release {name} must be a list of {len(FEATURES)} numbers",
            context={"got": raw},
        )
    return [float(value) for value in raw]


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("expected a JSON object", context={"path": str(path)})
    return raw


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "INPUT_GEOMETRY",
    "MODEL_NAME",
    "ExportedArtifact",
    "build_config",
    "build_release",
    "convert_checkpoint",
    "export_run",
    "sha256_file",
    "verify_artifact",
]
