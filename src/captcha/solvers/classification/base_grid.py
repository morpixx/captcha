"""Shared base for every grid-classification solver.

Each per-captcha solver (hcaptcha, recaptcha, funcaptcha, awswaf, shein)
subclasses `GridClassifier`, overrides `default_head_name`, and inherits the
full solve path: decode → slice → backbone.encode → head.pick_tiles_for_prompt.

Payload contract (same across every subclass; sites that wrap it differently
get adapted at the worker, not here):

    {
      "image_b64": str,   # base64 of the full grid, with or without data: URI
      "prompt":    str,   # object/scene name, e.g. "bus"
      "rows":      int,   # default 3
      "cols":      int,   # default 3
      "head":      str?,  # optional head-name override
      "threshold": float? # optional per-call sigmoid threshold
    }
"""

from __future__ import annotations

import time
from typing import ClassVar

from captcha.domain import SolveMode, SolverResult, Task
from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.solvers import Solver, SolverContext

from ._tiles import decode_base64_image, slice_grid, tiles_to_batch
from .backbone import ViTBackbone
from .head import LoRAHead


class GridClassifier(Solver):
    """Abstract grid solver — subclasses set `default_head_name` + `info`."""

    default_head_name: ClassVar[str] = ""
    default_rows: ClassVar[int] = 3
    default_cols: ClassVar[int] = 3
    backbone_model: ClassVar[str] = "google/vit-base-patch16-224-in21k"

    async def prepare(self, ctx: SolverContext) -> None:
        """Warm the shared backbone and this solver's default head.

        Fails-fast on misconfig: better to crash the worker at startup than at
        task time with half a grid half-classified.
        """
        if not self.default_head_name:
            raise ConfigError(
                f"{type(self).__name__} missing default_head_name",
                context={"solver": self.info.name},
            )
        device = self._pick_device(ctx)
        backbone = ViTBackbone.get(device=device, model_name=self.backbone_model)
        if ctx.models is not None:
            # Preload the default head so the first request doesn't eat an S3 fetch.
            try:
                LoRAHead.load(ctx.models, name=self.default_head_name, backbone=backbone, device=device)
            except (ConfigError, ModelInferenceError) as e:
                # FILL: once the training pipeline publishes real heads, downgrade
                # this to a hard failure. Until then, warm-load is best-effort so
                # the worker can still boot in dev with an empty model bucket.
                if ctx.logger is not None:
                    ctx.logger.warning(
                        "classification.head.warmup_failed",
                        head=self.default_head_name,
                        solver=self.info.name,
                        error=str(e),
                    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.perf_counter()
        payload = task.payload or {}

        image_b64 = payload.get("image_b64") or payload.get("image")
        prompt = payload.get("prompt") or payload.get("question") or payload.get("target")
        rows = int(payload.get("rows", self.default_rows))
        cols = int(payload.get("cols", self.default_cols))
        head_name = payload.get("head") or self.default_head_name
        threshold = payload.get("threshold")

        if not image_b64:
            raise UnsolvableError("payload missing image_b64", context={"solver": self.info.name})
        if not prompt or not isinstance(prompt, str):
            raise UnsolvableError("payload missing prompt", context={"solver": self.info.name})

        if ctx.models is None:
            raise ConfigError(
                "classification solver requires ctx.models (captcha.adapters.weights.Registry)",
                context={"solver": self.info.name},
            )

        device = self._pick_device(ctx)
        backbone = ViTBackbone.get(device=device, model_name=self.backbone_model)

        try:
            head = LoRAHead.load(ctx.models, name=head_name, backbone=backbone, device=device)
        except ModelInferenceError:
            raise
        except ConfigError as e:
            # No weights registered for this prompt/head yet → direct the op to
            # the runbook that describes how to train one. Don't silently guess.
            raise ModelInferenceError(
                f"no classification head registered for '{head_name}' — see runbooks/{self.info.name}.md",
                context={
                    "solver": self.info.name,
                    "head": head_name,
                    "prompt": prompt,
                    "hint": "train a LoRA head, push to captcha-models, bump 'latest'",
                    "underlying": e.to_dict() if hasattr(e, "to_dict") else str(e),
                },
            ) from e

        image = decode_base64_image(image_b64)
        tiles = slice_grid(image, rows=rows, cols=cols)
        batch = tiles_to_batch(tiles, device=device)
        feats = backbone.encode(batch)

        indices = head.pick_tiles_for_prompt(
            feats,
            prompt,
            threshold=float(threshold) if threshold is not None else None,
        )

        latency_ms = (time.perf_counter() - t0) * 1000.0
        return SolverResult(
            indices=indices,
            solve_mode=SolveMode.HTTP,
            latency_ms=latency_ms,
            extra={
                "head": head.name,
                "head_version": head.version,
                "rows": rows,
                "cols": cols,
                "backbone": backbone.model_name,
                "device": device,
            },
        )

    # ---- helpers ------------------------------------------------------------

    def _pick_device(self, ctx: SolverContext) -> str:
        """`cuda` if the context asks for GPU and torch has it; else cpu."""
        cfg = ctx.config
        preferred = None
        if cfg is not None:
            preferred = getattr(cfg, "classification_device", None) or getattr(cfg, "device", None)
        if preferred:
            return str(preferred)
        if self.info.needs_gpu:
            try:
                import torch  # local import — torch is optional at import time
                if torch.cuda.is_available():
                    return "cuda"
            except ImportError:
                pass
        return "cpu"
