"""Smoke tests. Everything that touches torch/transformers is skipped when the
optional extras aren't installed, so CI without GPU extras still runs these.
"""

from __future__ import annotations

import base64
import importlib.util
import io

import pytest
from PIL import Image

from captcha_solver_classification._tiles import (
    decode_base64_image,
    slice_grid,
)


def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


HAS_TORCH = _has("torch")
HAS_HF = _has("transformers")


def _make_grid_b64(size: int = 300, color: tuple[int, int, int] = (128, 64, 32)) -> str:
    img = Image.new("RGB", (size, size), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_decode_data_uri_prefix() -> None:
    b64 = _make_grid_b64()
    with_prefix = f"data:image/png;base64,{b64}"
    img = decode_base64_image(with_prefix)
    assert img.size == (300, 300)
    assert img.mode == "RGB"


def test_decode_rejects_garbage() -> None:
    from captcha_common.errors import UnsolvableError

    with pytest.raises(UnsolvableError):
        decode_base64_image("not-base64!!!")


def test_slice_grid_row_major() -> None:
    img = Image.new("RGB", (300, 300), (0, 0, 0))
    tiles = slice_grid(img, rows=3, cols=3, tile_size=224)
    assert len(tiles) == 9
    assert all(t.size == (224, 224) for t in tiles)


def test_slice_grid_bad_shape() -> None:
    from captcha_common.errors import UnsolvableError

    img = Image.new("RGB", (10, 10), (0, 0, 0))
    with pytest.raises(UnsolvableError):
        slice_grid(img, rows=4, cols=4, tile_size=224)


@pytest.mark.skipif(not (HAS_TORCH and HAS_HF), reason="torch+hf extras not installed")
def test_backbone_encode_shape() -> None:
    import torch

    from captcha_solver_classification._tiles import tiles_to_batch
    from captcha_solver_classification.backbone import ViTBackbone, clear_backbone_cache

    clear_backbone_cache()
    img = Image.new("RGB", (672, 672), (100, 100, 100))
    tiles = slice_grid(img, rows=3, cols=3)
    batch = tiles_to_batch(tiles, device="cpu")
    assert batch.shape == (9, 3, 224, 224)
    assert batch.dtype == torch.float32

    bb = ViTBackbone.get(device="cpu")
    feats = bb.encode(batch)
    assert feats.shape[0] == 9
    assert feats.shape[1] == bb.feature_dim
    clear_backbone_cache()
