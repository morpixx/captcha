"""Shared solve path for every prompt-conditioned grid solver.

Payload contract (the same across subclasses; vendor wrappers adapt before the
worker, not here):

    image_b64  str    base64 of the full grid (aliases: image, body)
    prompt     str    object/scene name or full instruction (question/target)
    rows/cols  int?   grid shape; default is the head's published shape
    head       str?   head-name override; must be one of grid.HEADS
    threshold  float? per-call sigmoid threshold, 0..1

Path: resolve head contract -> load head -> resolve grid -> decode -> slice ->
ViT encode -> head.pick_tiles_for_prompt -> SolverResult(indices).

Failure shape, no fallback predictor:
    ERR_CONFIG           no ctx.models, unknown head, missing meta/labels
    ERR_MODEL_INFERENCE  unreadable weights, dimension mismatch, unknown prompt
    ERR_UNSOLVABLE       broken image/prompt/grid in the task payload
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.errors import CaptchaError, ConfigError, UnsolvableError
from captcha.solvers import Solver, SolverContext

from ._tiles import decode_base64_image, slice_grid, tiles_to_batch
from .backbone import ViTBackbone
from .grid import GridHeadContract, coerce_threshold, head_contract, resolve_grid
from .head import LoRAHead

#: Token types served by browser/HTTP wire flows, never by a grid classifier.
#: A grid answer is a list of tile indices; projecting it as a token is a lie.
_TOKEN_GRID_TYPES = frozenset({CaptchaType.HCAPTCHA, CaptchaType.HCAPTCHA_ENTERPRISE})


class GridClassifier(Solver):
    """Abstract grid solver. Subclasses set head = HEADS["..."] and info."""

    head: ClassVar[GridHeadContract]
    backbone_model: ClassVar[str] = "google/vit-base-patch16-224-in21k"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        info = getattr(cls, "info", None)
        head = getattr(cls, "head", None)
        if info is None:
            return
        banned = sorted(item.value for item in _TOKEN_GRID_TYPES.intersection(info.types))
        if banned:
            raise TypeError(
                f"{cls.__name__} is a grid classifier and cannot claim token type(s): {', '.join(banned)}"
            )
        if not isinstance(head, GridHeadContract):
            raise TypeError(f"{cls.__name__} must set head = HEADS['<name>']")

    async def prepare(self, ctx: SolverContext) -> None:
        """Warm the shared backbone and this solver's head.

        A worker must boot with an empty model bucket: a missing head is
        logged here and raised as ERR_CONFIG at task time, never faked.
        """
        device = self._pick_device(ctx)
        backbone = ViTBackbone.get(device=device, model_name=self.backbone_model)
        if ctx.models is None:
            return
        try:
            LoRAHead.load(
                ctx.models,
                name=self.head.name,
                backbone=backbone,
                device=device,
                contract=self.head,
            )
        except CaptchaError as exc:
            if ctx.logger is not None:
                ctx.logger.warning(
                    "classification.head.warmup_failed",
                    head=self.head.name,
                    solver=self.info.name,
                    error=str(exc),
                )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        payload = task.payload or {}

        image_b64 = _first(payload, "image_b64", "image", "body")
        prompt = _first(payload, "prompt", "question", "target", "textinstructions")
        if not image_b64:
            raise UnsolvableError("payload missing image_b64", context={"solver": self.info.name})
        if not isinstance(prompt, str) or not prompt.strip():
            raise UnsolvableError("payload missing prompt", context={"solver": self.info.name})

        head_name = str(payload.get("head") or self.head.name)
        contract = self.head if head_name == self.head.name else head_contract(head_name)

        if ctx.models is None:
            raise ConfigError(
                "classification solver requires ctx.models (captcha.adapters.weights.Registry)",
                context={"solver": self.info.name, "head": head_name},
            )

        device = self._pick_device(ctx)
        backbone = ViTBackbone.get(device=device, model_name=self.backbone_model)
        head = LoRAHead.load(
            ctx.models,
            name=head_name,
            backbone=backbone,
            device=device,
            contract=contract,
        )

        shape = resolve_grid(payload, contract, fallback=head.shape)
        raw_threshold = payload.get("threshold")
        threshold = coerce_threshold(raw_threshold, source="payload") if raw_threshold is not None else None

        image = decode_base64_image(image_b64)
        tiles = slice_grid(image, rows=shape.rows, cols=shape.cols)
        batch = tiles_to_batch(tiles, device=device)
        features = backbone.encode(batch)
        indices = head.pick_tiles_for_prompt(features, prompt, threshold=threshold, shape=shape)

        return SolverResult(
            indices=indices,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            extra={
                "head": head.name,
                "head_version": head.version,
                "rows": shape.rows,
                "cols": shape.cols,
                "threshold": head.threshold if threshold is None else threshold,
                "backbone": backbone.model_name,
                "device": device,
            },
        )

    # ---- helpers ------------------------------------------------------------

    def _pick_device(self, ctx: SolverContext) -> str:
        """cuda if the context asks for GPU and torch has it; else cpu."""
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


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


__all__ = ["GridClassifier"]
