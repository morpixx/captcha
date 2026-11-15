"""Custom hopscotch solver: pick the card the hint points at.

One wave is a row of 200x200 cards with a hint panel bottom-left; the answer is
the 1-based card number whose figure matches the hint. The trained network does
the matching (D:/hopscotch_v2); this solver owns the wire contract:

    payload  {image_b64, frames?, hint?}          (see .payload)
    result   {index: 1-based int, coordinates: [x, y] pixel foot of the card}

Everything the model needs to be reproducible - weights, architecture, input
geometry, calibration - arrives as one artifact triple (model.safetensors,
config.json, release.json). If that triple is absent or its release is not
ready, the solver refuses to guess: it raises rather than shipping a coin flip.

Registered experimental until the L3 canary proves 95% accuracy at 80% coverage.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.errors import ConfigError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo

from .artifact import HopscotchArtifact, load_artifact
from .backend import Backend, V2Backend
from .calibration import decide
from .payload import foot_to_pixels, parse_payload
from .v2runtime import ROOT_ENV, v2_root

ARTIFACT_ENV = "HOPSCOTCH_ARTIFACT_DIR"


class HopscotchSolver(Solver):
    info = SolverInfo(
        name="hopscotch",
        types=(CaptchaType.HOPSCOTCH,),
        http=True,
        needs_gpu=True,
        experimental=True,
        version="0.1.0",
    )

    def __init__(self) -> None:
        self._artifact: HopscotchArtifact | None = None
        self._backend: Backend | None = None

    async def prepare(self, ctx: SolverContext) -> None:
        """Validate the artifact once. The heavy model is built on first task."""
        self._artifact = load_artifact(_artifact_dir(ctx))
        self._backend = _injected_backend(ctx)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.perf_counter()
        artifact = self._artifact or load_artifact(_artifact_dir(ctx))
        wave = parse_payload(task.payload, artifact.config.input)

        backend = self._backend or _injected_backend(ctx) or _v2_backend(artifact, ctx)
        observation = backend.predict(wave)

        decision = decide(observation, artifact.release)
        if decision.abstained:
            # Beta policy: abstention is terminal, not a retry. The gateway maps
            # it to ERR_UNSOLVABLE; the lead decides if it should become ERR_QUOTA.
            raise UnsolvableError(
                "hopscotch abstained",
                context={
                    "reason": decision.reason,
                    "score": decision.score,
                    "release": artifact.release.status,
                    "model": artifact.config.model_name,
                },
            )

        x, y = foot_to_pixels(
            observation.foot,
            observation.index,
            artifact.config.input,
            width=wave.width,
            height=wave.height,
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return SolverResult(
            indices=[observation.index],
            coords=[(x, y)],
            extra={
                "index": observation.index + artifact.config.input.index_base,
                "coordinates": [x, y],
                "card_index": observation.index,
                "frames": wave.frames,
                "confidence": decision.score,
                "model": artifact.config.model_name,
                "version": artifact.config.version,
                "release": artifact.release.status,
                "hint": wave.hint,
            },
            solve_mode=SolveMode.HTTP,
            latency_ms=latency_ms,
        )


# ---- wiring -----------------------------------------------------------------


def _artifact_dir(ctx: SolverContext) -> Path:
    extra: dict[str, Any] = ctx.extra or {}
    value = (
        extra.get("hopscotch_artifact_dir")
        or getattr(ctx.config, "hopscotch_artifact_dir", None)
        or os.environ.get(ARTIFACT_ENV)
    )
    if not value:
        raise ConfigError(
            "hopscotch artifact directory is not configured",
            context={
                "hint": f"set {ARTIFACT_ENV} or pass SolverContext.extra['hopscotch_artifact_dir']",
                "artifact": "model.safetensors + config.json + release.json",
            },
        )
    return Path(str(value))


def _injected_backend(ctx: SolverContext) -> Backend | None:
    backend = (ctx.extra or {}).get("hopscotch_backend")
    return backend if backend is not None else None


def _v2_backend(artifact: HopscotchArtifact, ctx: SolverContext) -> Backend:
    root = getattr(ctx.config, "hopscotch_v2_root", None) or v2_root()
    if not root:
        raise ConfigError(
            "hopscotch inference runtime is not configured",
            context={"hint": f"set {ROOT_ENV} to the hopscotch_v2 checkout"},
        )
    device = str(getattr(ctx.config, "worker_cv_device", "cpu") or "cpu")
    return V2Backend(artifact, root=str(root), device=device)


__all__ = ["ARTIFACT_ENV", "HopscotchSolver"]
