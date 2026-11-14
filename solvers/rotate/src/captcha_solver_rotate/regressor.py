"""ResNet50-backed angle regressor.

Lazy: importing this module does NOT import torch. The regressor is only
instantiated when the solver finds a 'rotate-v1' model in ctx.models. Missing
weights raises `ModelInferenceError` with a FILL note so the fallback path
triggers in solver.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import numpy as np

from captcha_common.errors import ModelInferenceError


class AngleRegressor:
    """ResNet50 head → 2-logit vector interpreted as (sin θ, cos θ).

    Weights produced by the training pipeline under packages/models. The
    checkpoint is expected to be a torch state_dict with the final fc layer's
    output = 2. If the loaded weights don't match this contract, inference
    raises `ModelInferenceError`.
    """

    def __init__(self, weights_path: str | Path, *, device: str = "cpu") -> None:
        self.weights_path = Path(weights_path)
        self.device = device
        self._model: Any = None  # torch.nn.Module when loaded
        self._torch: Any = None
        self._tv: Any = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch  # noqa: PLC0415
            from torchvision.models import ResNet50_Weights, resnet50  # noqa: PLC0415
        except ImportError as e:
            raise ModelInferenceError(
                "torch/torchvision not installed; rotate ML path unavailable",
                context={"install": "pip install captcha-solver-rotate[ml]"},
            ) from e

        if not self.weights_path.exists():
            raise ModelInferenceError(
                "rotate regressor weights file missing",
                context={
                    "path": str(self.weights_path),
                    # FILL: train rotate-v1 and publish weights to the model registry
                    #       under name='rotate-v1'; see packages/models README.
                    "hint": "model 'rotate-v1' not yet trained — see FILL in regressor._load",
                },
            )

        model = resnet50(weights=None)
        model.fc = torch.nn.Linear(model.fc.in_features, 2)
        try:
            state = torch.load(self.weights_path, map_location=self.device, weights_only=True)
            model.load_state_dict(state)
        except Exception as e:  # noqa: BLE001
            raise ModelInferenceError(
                "rotate regressor state_dict load failed",
                context={"path": str(self.weights_path), "error": str(e)},
            ) from e
        model.eval()
        model.to(self.device)
        self._model = model
        self._torch = torch
        self._tv = ResNet50_Weights.DEFAULT.transforms()

    def predict(self, rgb: np.ndarray) -> tuple[float, float]:
        """Return (angle_deg 0..360, confidence 0..1)."""
        self._load()
        torch = self._torch
        from PIL import Image  # noqa: PLC0415

        img = Image.fromarray(rgb, mode="RGB")
        x = self._tv(img).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            logits = self._model(x).squeeze(0).cpu().numpy()
        if logits.shape != (2,):
            raise ModelInferenceError(
                "rotate regressor returned unexpected shape",
                context={"shape": tuple(logits.shape)},
            )
        sin_, cos_ = float(logits[0]), float(logits[1])
        angle = (math.degrees(math.atan2(sin_, cos_)) + 360.0) % 360.0
        norm = math.hypot(sin_, cos_)
        conf = float(min(1.0, norm))  # well-trained net outputs ~unit-norm
        return (angle, conf)
