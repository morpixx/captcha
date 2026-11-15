"""Inference backend seam.

The solver never builds torch objects itself: a backend is either injected by the
worker/tests (anything with .predict(wave)) or the v2 runtime behind the artifact.
Keeping the seam explicit is what lets the test suite run with a fake model and no
GPU, and what lets ops swap in a batched server later without touching the solver.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from captcha.domain.errors import ModelInferenceError

from . import v2runtime
from .artifact import HopscotchArtifact
from .calibration import Observation
from .payload import Wave


class Backend(Protocol):
    def predict(self, wave: Wave) -> Observation: ...


class V2Backend:
    """Loads model.safetensors and forwards through the hopscotch_v2 module graph."""

    def __init__(self, artifact: HopscotchArtifact, *, root: str | Path, device: str = "cpu") -> None:
        self._artifact = artifact
        self._root = root
        self._device = device
        self._model: Any = None

    def predict(self, wave: Wave) -> Observation:
        model = self._model
        if model is None:
            model = self._model = v2runtime.build(self._artifact, root=self._root, device=self._device)
        try:
            return v2runtime.predict(model, self._artifact, wave)
        except ModelInferenceError:
            raise
        except Exception as exc:  # keep the solver's error contract
            raise ModelInferenceError("hopscotch backend crashed", context={"error": str(exc)}) from exc


__all__ = ["Backend", "V2Backend"]
