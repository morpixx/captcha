"""Artifact contract for the custom hopscotch type.

The training repo (D:/hopscotch_v2) owns the network; this module owns the
three files a worker is allowed to see:

    model.safetensors   full serving state_dict (DINO encoder included)
    config.json         architecture + input geometry the forward pass needs
    release.json        calibration/rejector: eight fixed features -> threshold

The rejector is a port of src/inference/rejector.py from hopscotch_v2, kept
here because serving owns the decision: a score below the calibrated threshold
means "do not answer", and that policy has to be auditable in this repo.

Nothing here imports torch. Loading weights happens in .v2runtime, which is the
only place allowed to touch the training checkout.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from captcha.domain.errors import ConfigError

#: Bumped when the preprocessing pipeline changes; the release is bound to it.
PREPROCESSING = "rgb-imagenet-bilinear-hint-neutral-padding-v1"
CONFIG_FORMAT = "hopscotch-v1-config"
RELEASE_FORMAT = "captcha-hopscotch-release-v1"
ARTIFACT_FORMAT = "captcha-hopscotch-artifact-v1"

CONFIG_FILE = "config.json"
RELEASE_FILE = "release.json"
WEIGHTS_FILE = "model.safetensors"
CHECKSUMS_FILE = "checksums.json"

#: Feature vector order. Frozen: the release stores mean/scale/weights in this
#: order, so reordering silently breaks calibration instead of raising.
FEATURES: tuple[str, ...] = (
    "answer_probability",
    "answer_margin",
    "hint_margin",
    "support_margin",
    "hint_cosine",
    "prototype_mass",
    "appearance_consistency",
    "eight_cards",
)

#: Structural guards a prediction must pass before calibration is consulted.
GUARDS: dict[str, Any] = {
    "presence": 0.5,
    "minimum_slots": 4,
    "maximum_slots": 8,
    "minimum_mass": 0.1,
    "minimum_norm": 1e-6,
    "unique_support": True,
}


@dataclass(frozen=True)
class InputGeometry:
    """How cards sit in the input image, in pixels.

    A wave image is one row of 'frames' 200x200 cards on top and a hint panel
    bottom-left; 8 cards make the image 1600x400, 5 cards 1000x400.
    """

    image_height: int
    card_width: int
    card_height: int
    hint_box: tuple[int, int, int, int]
    frames: tuple[int, ...]
    index_base: int = 1

    @classmethod
    def decode(cls, raw: Mapping[str, Any]) -> InputGeometry:
        try:
            geometry = cls(
                image_height=int(raw["image_height"]),
                card_width=int(raw["card_width"]),
                card_height=int(raw["card_height"]),
                hint_box=tuple(int(v) for v in raw["hint_box"]),
                frames=tuple(int(v) for v in raw["frames"]),
                index_base=int(raw.get("index_base", 1)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError("config.json: invalid input geometry", context={"error": str(exc)}) from exc
        geometry.validate()
        return geometry

    @property
    def width_for(self) -> dict[int, int]:
        return {n: n * self.card_width for n in self.frames}

    def validate(self) -> None:
        if self.image_height <= 0 or self.card_width <= 0 or self.card_height <= 0:
            raise ConfigError("config.json: non-positive image geometry")
        if self.card_height > self.image_height:
            raise ConfigError("config.json: cards taller than the image")
        if not self.frames or any(n <= 0 for n in self.frames):
            raise ConfigError("config.json: invalid frame counts")
        x0, y0, x1, y1 = self.hint_box
        if not (0 <= x0 < x1 <= self.frames[0] * self.card_width and 0 <= y0 < y1 <= self.image_height):
            raise ConfigError(
                "config.json: hint box outside the image",
                context={"hint_box": list(self.hint_box)},
            )
        if self.index_base != 1:
            raise ConfigError("config.json: index_base must be 1 (client-facing card numbers)")


@dataclass(frozen=True)
class HopscotchConfig:
    format: str
    model_name: str
    version: str
    preprocessing: str
    model: Mapping[str, Any]
    train: Mapping[str, Any]
    backbone_config: Mapping[str, Any]
    input: InputGeometry
    checkpoint_epoch: int = 0
    source: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def decode(cls, raw: Mapping[str, Any]) -> HopscotchConfig:
        if raw.get("format") != CONFIG_FORMAT:
            raise ConfigError(
                "config.json: unsupported format",
                context={"expected": CONFIG_FORMAT, "got": raw.get("format")},
            )
        if raw.get("preprocessing") != PREPROCESSING:
            raise ConfigError(
                "config.json: preprocessing mismatch",
                context={"expected": PREPROCESSING, "got": raw.get("preprocessing")},
            )
        model = raw.get("model")
        train = raw.get("train")
        backbone_config = raw.get("backbone_config")
        if not isinstance(model, Mapping) or not isinstance(train, Mapping) or not isinstance(backbone_config, Mapping):
            raise ConfigError("config.json: model/train/backbone_config must be objects")
        for key in ("architecture", "hidden_dim", "slots", "image_size", "crop_size", "dropout"):
            if key not in model:
                raise ConfigError("config.json: model is missing a field", context={"field": key})
        if "encoder_chunk" not in train:
            raise ConfigError("config.json: train.encoder_chunk is required for serving")
        return cls(
            format=str(raw["format"]),
            model_name=str(raw.get("model_name", "hopscotch-v1")),
            version=str(raw.get("version", "v1")),
            preprocessing=str(raw["preprocessing"]),
            model=model,
            train=train,
            backbone_config=backbone_config,
            input=InputGeometry.decode(raw["input"]),
            checkpoint_epoch=int(raw.get("checkpoint_epoch", 0)),
            source=raw.get("source") or {},
        )


@dataclass(frozen=True)
class HopscotchRelease:
    format: str
    model_name: str
    status: str
    preprocessing: str
    threshold: float | None
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float
    features: tuple[str, ...] = FEATURES
    guards: Mapping[str, Any] = field(default_factory=lambda: dict(GUARDS))
    metrics: Mapping[str, Any] = field(default_factory=dict)
    split_sha256: str = ""
    source: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.threshold is not None

    @classmethod
    def decode(cls, raw: Mapping[str, Any]) -> HopscotchRelease:
        if raw.get("format") != RELEASE_FORMAT:
            raise ConfigError(
                "release.json: unsupported format",
                context={"expected": RELEASE_FORMAT, "got": raw.get("format")},
            )
        status = raw.get("status")
        if status not in ("ready", "not_ready"):
            raise ConfigError("release.json: invalid status", context={"status": status})
        if raw.get("preprocessing") != PREPROCESSING:
            raise ConfigError(
                "release.json: preprocessing mismatch",
                context={"got": raw.get("preprocessing")},
            )

        features = tuple(str(name) for name in raw.get("features", FEATURES))
        if features != FEATURES:
            raise ConfigError(
                "release.json: rejector feature order mismatch",
                context={"expected": list(FEATURES), "got": list(features)},
            )
        mean = _vector(raw.get("mean"), "mean")
        scale = _vector(raw.get("scale"), "scale")
        weights = _vector(raw.get("weights"), "weights")
        if any(v <= 0 for v in scale):
            raise ConfigError("release.json: rejector scale must be positive")
        try:
            bias = float(raw["bias"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError("release.json: invalid bias") from exc
        if not math.isfinite(bias):
            raise ConfigError("release.json: non-finite bias")

        threshold = raw.get("threshold")
        if threshold is not None:
            try:
                threshold = float(threshold)
            except (TypeError, ValueError) as exc:
                raise ConfigError("release.json: invalid threshold") from exc
            if not 0.0 <= threshold <= 1.0:
                raise ConfigError("release.json: threshold outside [0,1]")
        if status == "ready" and threshold is None:
            raise ConfigError("release.json: ready release without a threshold")

        guards = raw.get("guards", GUARDS)
        if not isinstance(guards, Mapping):
            raise ConfigError("release.json: guards must be an object")
        return cls(
            format=str(raw["format"]),
            model_name=str(raw.get("model_name", "hopscotch-v1")),
            status=str(status),
            preprocessing=str(raw["preprocessing"]),
            threshold=threshold,
            mean=mean,
            scale=scale,
            weights=weights,
            bias=bias,
            features=features,
            guards=dict(guards),
            metrics=raw.get("metrics") or {},
            split_sha256=str(raw.get("split_sha256") or raw.get("manifest_hash") or ""),
            source=raw.get("source") or {},
            created_at=str(raw.get("created_at", "")),
        )


@dataclass(frozen=True)
class HopscotchArtifact:
    directory: Path
    config: HopscotchConfig
    release: HopscotchRelease

    @property
    def weights(self) -> Path:
        return self.directory / WEIGHTS_FILE


def _vector(raw: Any, name: str) -> tuple[float, ...]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        raise ConfigError(f"release.json: {name} must be a list")
    values = tuple(float(v) for v in raw)
    if len(values) != len(FEATURES):
        raise ConfigError(
            f"release.json: {name} must have {len(FEATURES)} entries",
            context={"got": len(values)},
        )
    if not all(math.isfinite(v) for v in values):
        raise ConfigError(f"release.json: non-finite value in {name}")
    return values


def load_artifact(directory: str | Path) -> HopscotchArtifact:
    """Read + validate an artifact directory. Raises ConfigError on any mismatch."""
    root = Path(directory)
    if not root.is_dir():
        raise ConfigError("hopscotch artifact directory is missing", context={"path": str(root)})
    config = HopscotchConfig.decode(_read_json(root / CONFIG_FILE))
    release = HopscotchRelease.decode(_read_json(root / RELEASE_FILE))
    weights = root / WEIGHTS_FILE
    if not weights.is_file() or weights.stat().st_size == 0:
        raise ConfigError("hopscotch weights file is missing or empty", context={"path": str(weights)})
    if config.preprocessing != release.preprocessing:
        raise ConfigError(
            "config/release preprocessing mismatch",
            context={"config": config.preprocessing, "release": release.preprocessing},
        )
    if config.model_name != release.model_name:
        raise ConfigError(
            "config/release model_name mismatch",
            context={"config": config.model_name, "release": release.model_name},
        )
    return HopscotchArtifact(directory=root, config=config, release=release)


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError("hopscotch artifact file is unreadable", context={"path": str(path)}) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError("hopscotch artifact file is not valid JSON", context={"path": str(path)}) from exc
    if not isinstance(raw, Mapping):
        raise ConfigError("hopscotch artifact file must be a JSON object", context={"path": str(path)})
    return raw


__all__ = [
    "ARTIFACT_FORMAT",
    "CHECKSUMS_FILE",
    "CONFIG_FILE",
    "CONFIG_FORMAT",
    "FEATURES",
    "GUARDS",
    "PREPROCESSING",
    "RELEASE_FILE",
    "RELEASE_FORMAT",
    "WEIGHTS_FILE",
    "HopscotchArtifact",
    "HopscotchConfig",
    "HopscotchRelease",
    "InputGeometry",
    "load_artifact",
]
