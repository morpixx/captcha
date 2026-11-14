"""Classical slider gap detection.

Pipeline:
  1. Decode both images to grayscale numpy arrays.
  2. Canny-edge the background to highlight the hole outline.
  3. Build a binary piece mask from the piece's alpha (or luminance threshold
     when the piece is JPEG without alpha), then Canny its edges too.
  4. `cv2.matchTemplate(bg_edges, piece_edges, TM_CCOEFF_NORMED)`.
  5. Pick the argmax column as the gap x-offset.

A couple of safety nets layered on top:
  - A fallback that masks out the left strip where the piece itself sits,
    because on many vendors the piece is overlayed on the bg at x=0..~W_piece
    and would otherwise match itself.
  - A blur before Canny to kill JPEG mosquito noise, tunable via `blur_ksize`.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import cv2
import numpy as np
from PIL import Image

from captcha.domain.errors import UnsolvableError


def _strip_data_url(b64: str) -> str:
    if b64.startswith("data:"):
        _, _, tail = b64.partition(",")
        return tail
    return b64


def decode_image(b64: str, *, with_alpha: bool = False) -> np.ndarray:
    """Base64 → numpy array. RGB (H,W,3) or RGBA (H,W,4) when with_alpha."""
    raw = base64.b64decode(_strip_data_url(b64), validate=False)
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        raise UnsolvableError("could not decode image payload", context={"error": str(e)}) from e
    if with_alpha:
        img = img.convert("RGBA")
        return np.array(img, dtype=np.uint8)
    img = img.convert("RGB")
    return np.array(img, dtype=np.uint8)


def _to_gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _piece_mask(piece_rgba: np.ndarray) -> np.ndarray:
    """Binary mask of the piece's visible pixels.

    Prefers the alpha channel if non-trivial; falls back to a luminance
    threshold against the solid border colour so JPEG-flattened pieces still work.
    """
    _h, _w = piece_rgba.shape[:2]
    if piece_rgba.shape[2] == 4:
        alpha = piece_rgba[:, :, 3]
        if int(alpha.min()) < 250:
            return (alpha > 32).astype(np.uint8) * 255
    # Fallback: assume background-coloured corner is the fill.
    gray = cv2.cvtColor(piece_rgba[:, :, :3], cv2.COLOR_RGB2GRAY)
    corner = int(gray[0, 0])
    diff = np.abs(gray.astype(np.int16) - corner)
    mask = (diff > 12).astype(np.uint8) * 255
    # Morphology to close pin-prick holes from JPEG noise.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def _crop_piece(piece_rgba: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Tight-crop the piece to its content box. Returns (piece, mask, x0, y0)."""
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        raise UnsolvableError("piece mask is empty — threshold failed", context={})
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    return piece_rgba[y0:y1, x0:x1], mask[y0:y1, x0:x1], x0, y0


def detect_gap(
    bg_b64: str,
    piece_b64: str,
    *,
    canny_lo: int = 50,
    canny_hi: int = 150,
    blur_ksize: int = 3,
    piece_width_hint: int | None = None,
    search_x_min: int | None = None,
    debug: dict[str, Any] | None = None,
) -> int:
    """Return gap x-offset (pixels, from left of bg).

    `search_x_min` lets the caller skip the self-match on vendors that draw the
    piece onto the bg itself — default: the piece's own width.
    """
    bg_rgb = decode_image(bg_b64, with_alpha=False)
    piece_rgba = decode_image(piece_b64, with_alpha=True)

    bg_gray = _to_gray(bg_rgb)
    mask_full = _piece_mask(piece_rgba)
    piece_rgba_c, piece_mask_c, _, _ = _crop_piece(piece_rgba, mask_full)
    piece_gray_c = cv2.cvtColor(piece_rgba_c[:, :, :3], cv2.COLOR_RGB2GRAY)

    k = max(1, blur_ksize | 1)  # force odd
    bg_blur = cv2.GaussianBlur(bg_gray, (k, k), 0)
    piece_blur = cv2.GaussianBlur(piece_gray_c, (k, k), 0)

    bg_edges = cv2.Canny(bg_blur, canny_lo, canny_hi)
    piece_edges = cv2.Canny(piece_blur, canny_lo, canny_hi)
    # Restrict piece edges to where the piece actually exists.
    piece_edges = cv2.bitwise_and(piece_edges, piece_edges, mask=piece_mask_c)

    ph, pw = piece_edges.shape[:2]
    bh, bw = bg_edges.shape[:2]
    if pw >= bw or ph >= bh:
        raise UnsolvableError(
            "piece larger than bg after crop",
            context={"piece": (pw, ph), "bg": (bw, bh)},
        )

    result = cv2.matchTemplate(bg_edges, piece_edges, cv2.TM_CCOEFF_NORMED)
    # Kill self-match on the left where the piece is rendered onto the bg.
    cutoff = search_x_min
    if cutoff is None:
        cutoff = piece_width_hint if piece_width_hint is not None else pw
    cutoff = max(0, min(cutoff, result.shape[1] - 1))
    if cutoff > 0:
        result[:, :cutoff] = -1.0

    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    x_off = int(max_loc[0])

    if debug is not None:
        debug["score"] = float(max_val)
        debug["piece_bbox"] = (pw, ph)
        debug["bg_shape"] = (bw, bh)
        debug["cutoff"] = cutoff

    if max_val < 0.15:
        raise UnsolvableError(
            "gap-match confidence too low",
            context={"score": float(max_val)},
        )
    return x_off
