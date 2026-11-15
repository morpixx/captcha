"""Serving runtime: rebuild the network from config.json + model.safetensors.

The architecture lives in the training checkout (D:/hopscotch_v2). Copying those
modules here would fork a model whose weights are pinned to it, so this module
imports that checkout at task time instead and does only the part the trainer
does not do: build the module graph from config.json and load safetensors.

Nothing here imports torch at module import: a worker started without --extra cv
must boot, and a worker that never sees a hopscotch task must not pay for torch.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from captcha.domain.errors import ConfigError, ModelInferenceError

from .artifact import HopscotchArtifact
from .calibration import Observation
from .payload import Wave

#: Environment variable pointing at the hopscotch_v2 checkout.
ROOT_ENV = "HOPSCOTCH_V2_ROOT"


def v2_root() -> str | None:
    value = os.environ.get(ROOT_ENV)
    return value or None


def build(artifact: HopscotchArtifact, *, root: str | Path, device: str = "cpu") -> Any:
    """Load model.safetensors into the module graph described by config.json."""
    base = Path(root).expanduser().resolve()
    if not (base / "src" / "model" / "solver.py").is_file():
        raise ConfigError(
            "hopscotch_v2 checkout not found",
            context={"root": str(base), "hint": f"set {ROOT_ENV} to the training checkout"},
        )
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    try:
        from safetensors.torch import load_file
        from src.model.encoder import DinoEncoder
        from src.model.solver import Solver
    except ImportError as exc:  # torch/safetensors missing, or the checkout is broken
        raise ConfigError(
            "hopscotch serving needs torch + safetensors and a readable hopscotch_v2 checkout",
            context={"root": str(base), "error": str(exc)},
        ) from exc

    cfg = artifact.config
    try:
        state = load_file(str(artifact.weights), device="cpu")
    except Exception as exc:
        raise ModelInferenceError(
            "failed to read model.safetensors",
            context={"path": str(artifact.weights), "error": str(exc)},
        ) from exc

    try:
        encoder = DinoEncoder(
            str(cfg.model["encoder"]),
            local_files_only=True,
            chunk=int(cfg.train["encoder_chunk"]),
            backbone_config=dict(cfg.backbone_config),
        )
        model = Solver(
            encoder,
            float(cfg.model["dropout"]),
            str(cfg.model.get("architecture", "queries")),
            separate_readout=bool(cfg.model.get("separate_readout", False)),
            readout_dropout=float(cfg.model.get("readout_dropout", 0.1)),
        )
    except Exception as exc:
        raise ConfigError("config.json does not describe a loadable hopscotch model") from exc

    try:
        model.load_state_dict(state, strict=True)
    except RuntimeError as exc:
        raise ModelInferenceError(
            "weights do not fit config.json",
            context={"error": str(exc), "weights": artifact.weights.name},
        ) from exc
    return model.to(device).eval()


def predict(model: Any, artifact: HopscotchArtifact, wave: Wave) -> Observation:
    """One forward pass + the eight rejector features. Raises ModelInferenceError."""
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - prepare() reports this earlier
        raise ModelInferenceError("torch is required to run hopscotch inference") from exc
    try:
        import numpy as np
        from src.data.v1_dataset import image_inputs
        from src.inference.rejector import evidence
    except ImportError as exc:
        raise ConfigError("hopscotch_v2 runtime modules are unimportable", context={"error": str(exc)}) from exc

    frames, hint = image_inputs(np.asarray(wave.image, dtype=np.uint8))
    device = next(model.parameters()).device
    mask = torch.ones(1, wave.frames, dtype=torch.bool, device=device)
    amp = device.type == "cuda" and bool(artifact.config.train.get("amp", False))
    try:
        with torch.inference_mode(), torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
            output = model(frames[None].to(device), hint[None].to(device), mask)
        observed = evidence(output)
    except Exception as exc:
        raise ModelInferenceError("hopscotch forward pass failed", context={"error": str(exc)}) from exc

    candidate = observed.get("candidate")
    if candidate is None:
        return Observation(
            index=-1,
            foot=None,
            features=None,
            valid=False,
            reason=str(observed.get("reason") or "no_candidate"),
        )
    index = int(candidate)
    feet = output["final"]["feet"][0, mask][index].tolist()
    features = observed.get("features")
    return Observation(
        index=index,
        foot=(float(feet[0]), float(feet[1])),
        features=tuple(float(v) for v in features) if features is not None else None,
        valid=bool(observed.get("valid")),
        reason=str(observed.get("reason") or "unknown"),
    )


__all__ = ["ROOT_ENV", "build", "predict", "v2_root"]
