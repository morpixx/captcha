"""Tile ops: base64 decode, grid slicing, tensor batching.

Pure PIL + numpy where possible; torch is imported inside the one function
that needs it, so the module stays importable without the cv extra. There is
no zero-shot or heuristic fallback path: a missing head is an error, not a
CLIP prompt guess.
"""

from __future__ import annotations

import base64
import binascii
import io
from typing import TYPE_CHECKING, Any

from captcha.domain.errors import ModelInferenceError, UnsolvableError

if TYPE_CHECKING:  # heavy deps resolved only when extras installed
    import numpy as np
    from PIL import Image


# ---- base64 ----------------------------------------------------------------


_B64_PREFIXES = (
    "data:image/png;base64,",
    "data:image/jpeg;base64,",
    "data:image/jpg;base64,",
    "data:image/webp;base64,",
)


def decode_base64_image(blob: str) -> Image.Image:
    """Decode a base64 string (with or without data: URI prefix) into a PIL Image.

    Raises UnsolvableError on malformed input — the task payload is broken, no
    amount of retry fixes it.
    """
    from PIL import Image, UnidentifiedImageError  # local import — Pillow is a hard dep

    if not isinstance(blob, str) or not blob:
        raise UnsolvableError("image_b64 missing or not a string")

    s = blob.strip()
    for p in _B64_PREFIXES:
        if s.startswith(p):
            s = s[len(p) :]
            break
    # strip whitespace/newlines some clients wrap at 76 chars
    s = "".join(s.split())

    try:
        raw = base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError) as e:
        raise UnsolvableError("image_b64 is not valid base64", context={"error": str(e)}) from e

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise UnsolvableError("decoded bytes are not an image", context={"error": str(e)}) from e

    return img.convert("RGB")


# ---- grid slicing ----------------------------------------------------------

#: A tile smaller than this is not a captcha tile, it is a handful of pixels.
_MIN_TILE_PX = 8


def slice_grid(
    image: Image.Image,
    rows: int,
    cols: int,
    *,
    tile_size: int = 224,
) -> list[Image.Image]:
    """Split a grid-captcha image into row*col PIL tiles, resized to `tile_size`.

    Row-major order: index = r*cols + c.
    """
    if rows < 1 or cols < 1:
        raise UnsolvableError("rows/cols must be >= 1", context={"rows": rows, "cols": cols})
    if rows > 16 or cols > 16:
        # sanity: no real grid captcha has more than 4x4 or 5x5
        raise UnsolvableError("rows/cols too large", context={"rows": rows, "cols": cols})

    from PIL import Image

    w, h = image.size
    tw, th = w // cols, h // rows
    if tw < _MIN_TILE_PX or th < _MIN_TILE_PX:
        # Below this a tile is a handful of pixels; upscaling it to 224 gives the
        # model noise, so refuse the task instead of burning inference on it.
        raise UnsolvableError(
            "image too small to slice",
            context={"w": w, "h": h, "rows": rows, "cols": cols, "tile": [tw, th]},
        )

    tiles: list[Image.Image] = []
    for r in range(rows):
        for c in range(cols):
            box = (c * tw, r * th, (c + 1) * tw, (r + 1) * th)
            tile = image.crop(box)
            if tile.size != (tile_size, tile_size):
                tile = tile.resize((tile_size, tile_size), Image.Resampling.BILINEAR)
            tiles.append(tile)
    return tiles


def tiles_to_batch(tiles: list[Image.Image], *, device: str = "cpu") -> Any:
    """Stack PIL tiles into a float32 CHW tensor batch, ImageNet-normalized.

    Returns a torch tensor on `device`. Importing torch here keeps the module
    optional for callers that only want slicing.
    """
    try:
        import numpy as np
        import torch
    except ImportError as e:  # pragma: no cover - env check
        raise ModelInferenceError(
            "torch extra not installed",
            context={"hint": "install with: uv pip install captcha-solver-classification[torch]"},
        ) from e

    # ImageNet normalization — matches HF ViT preprocessor defaults
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)

    arrs: list[np.ndarray] = []
    for t in tiles:
        a = np.asarray(t, dtype=np.float32) / 255.0  # HWC
        a = a.transpose(2, 0, 1)  # CHW
        arrs.append(a)
    batch = np.stack(arrs, axis=0)  # NCHW
    batch = (batch - mean) / std
    return torch.from_numpy(batch).to(device)
