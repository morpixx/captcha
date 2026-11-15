"""Classical slider gap detection: edges + template matching, no model.

Pipeline:
  1. Decode both images (piece with alpha when present).
  2. Build a binary piece mask from alpha, or from a corner-luminance threshold
     for JPEG-flattened pieces; tight-crop the piece to its content box.
  3. Edge-map both images (Canny when OpenCV is available, Sobel otherwise).
  4. Match the piece edges against the background with normalized
     cross-correlation; the argmax column is the gap offset.
  5. Ignore matches under the piece itself (vendors often composite the piece
     onto the background at x=0..piece_width).

OpenCV is an accelerator, not a requirement: the module imports without cv2 and
falls back to a numpy NCC. Errors are stable domain errors:
    ERR_UNSOLVABLE  bad image payload, empty mask, piece larger than bg, no match
    ERR_CONFIG      backend="cv2" requested but OpenCV is not installed
"""

from __future__ import annotations

import base64
import io
from typing import Any

import numpy as np
from PIL import Image

from captcha.domain.errors import ConfigError, UnsolvableError

BACKENDS = frozenset({"auto", "cv2", "numpy"})
MIN_MATCH_SCORE = 0.15


def _import_cv2(*, required: bool = False) -> Any | None:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        if required:
            raise ConfigError(
                "opencv required for the slider cv2 backend",
                context={"install": "pip install captcha[cv]"},
            ) from exc
        return None
    return cv2


def _strip_data_url(b64: str) -> str:
    if b64.startswith("data:"):
        _, _, tail = b64.partition(",")
        return tail
    return b64


def decode_image(b64: str, *, with_alpha: bool = False) -> np.ndarray:
    """Base64 -> numpy array. RGB (H,W,3) or RGBA (H,W,4) when with_alpha."""
    if not isinstance(b64, str) or not b64:
        raise UnsolvableError("slider image payload is empty", context={})
    try:
        raw = base64.b64decode(_strip_data_url(b64), validate=False)
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise UnsolvableError("could not decode image payload", context={"error": str(exc)}) from exc
    return np.array(image.convert("RGBA" if with_alpha else "RGB"), dtype=np.uint8)


def _to_gray(rgb: np.ndarray) -> np.ndarray:
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise UnsolvableError("slider image must be RGB", context={"shape": tuple(rgb.shape)})
    weights = np.asarray([0.299, 0.587, 0.114], dtype=np.float32)
    return (rgb[:, :, :3].astype(np.float32) @ weights).astype(np.uint8)


def _piece_mask(piece_rgba: np.ndarray, cv2: Any | None) -> np.ndarray:
    """Binary mask of the piece's visible pixels."""
    if piece_rgba.ndim != 3 or piece_rgba.shape[2] != 4:
        raise UnsolvableError("piece image must be RGBA for masking", context={"shape": tuple(piece_rgba.shape)})
    alpha = piece_rgba[:, :, 3]
    if int(alpha.min()) < 250:
        return ((alpha > 32).astype(np.uint8)) * 255
    gray = _to_gray(piece_rgba[:, :, :3])
    corner = int(gray[0, 0])
    diff = np.abs(gray.astype(np.int16) - corner)
    mask = ((diff > 12).astype(np.uint8)) * 255
    if cv2 is not None:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def _crop_piece(piece: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        raise UnsolvableError("piece mask is empty - threshold failed", context={})
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    if x1 - x0 < 3 or y1 - y0 < 3:
        raise UnsolvableError(
            "piece crop is too small to match",
            context={"crop": (x1 - x0, y1 - y0)},
        )
    return piece[y0:y1, x0:x1], mask[y0:y1, x0:x1]


def _box_blur(gray: np.ndarray, ksize: int) -> np.ndarray:
    if ksize <= 1:
        return gray
    pad = ksize // 2
    padded = np.pad(gray.astype(np.float32), pad, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (ksize, ksize))
    return windows.mean(axis=(2, 3))


def _edges(gray: np.ndarray, *, cv2: Any | None, canny_lo: int, canny_hi: int, blur_ksize: int) -> np.ndarray:
    ksize = max(1, int(blur_ksize) | 1)
    if cv2 is not None:
        blurred = cv2.GaussianBlur(gray, (ksize, ksize), 0)
        return cv2.Canny(blurred, int(canny_lo), int(canny_hi))
    blurred = _box_blur(gray, ksize)
    grad_x = np.gradient(blurred, axis=1)
    grad_y = np.gradient(blurred, axis=0)
    magnitude = np.hypot(grad_x, grad_y)
    threshold = max(1.0, float(min(canny_lo, canny_hi)))
    return ((magnitude > threshold).astype(np.uint8)) * 255


def _ncc_score_map(background: np.ndarray, piece: np.ndarray) -> np.ndarray:
    """Normalized cross-correlation, vectorized per row to bound memory use."""
    ph, pw = piece.shape[:2]
    height, width = background.shape[:2]
    if ph > height or pw > width:
        raise UnsolvableError(
            "piece larger than bg after crop",
            context={"piece": (pw, ph), "bg": (width, height)},
        )
    template = piece.astype(np.float32)
    template -= template.mean()
    template_norm = float(np.sqrt(float((template * template).sum())))
    if template_norm < 1e-6:
        raise UnsolvableError("piece edge template is flat - nothing to match", context={})

    score_map = np.full((height - ph + 1, width - pw + 1), -1.0, dtype=np.float32)
    for row in range(score_map.shape[0]):
        windows = np.lib.stride_tricks.sliding_window_view(background[row : row + ph], (ph, pw))[0]
        windows = windows.astype(np.float32)
        windows -= windows.mean(axis=(1, 2), keepdims=True)
        norms = np.sqrt((windows * windows).sum(axis=(1, 2)))
        scores = (windows * template).sum(axis=(1, 2)) / (norms * template_norm + 1e-6)
        scores[norms < 1e-6] = -1.0
        score_map[row] = scores
    return score_map


def _score_map(background: np.ndarray, piece: np.ndarray, *, cv2: Any | None, backend: str) -> tuple[np.ndarray, str]:
    if backend == "cv2" or (backend == "auto" and cv2 is not None):
        return cv2.matchTemplate(background, piece, cv2.TM_CCOEFF_NORMED), "cv2"
    return _ncc_score_map(background, piece), "numpy"


def detect_gap(
    bg_b64: str,
    piece_b64: str,
    *,
    canny_lo: int = 50,
    canny_hi: int = 150,
    blur_ksize: int = 3,
    piece_width_hint: int | None = None,
    search_x_min: int | None = None,
    min_score: float = MIN_MATCH_SCORE,
    backend: str = "auto",
    debug: dict[str, Any] | None = None,
) -> int:
    """Return gap x-offset in pixels from the left of the background image.

    search_x_min lets the caller skip the self-match on vendors that draw the
    piece onto the background itself; default is the piece's own width.
    """
    normalized_backend = str(backend or "auto").strip().lower()
    if normalized_backend not in BACKENDS:
        raise UnsolvableError(
            "unknown slider matching backend",
            context={"backend": backend, "allowed": sorted(BACKENDS)},
        )
    try:
        score_floor = float(min_score)
    except (TypeError, ValueError) as exc:
        raise UnsolvableError("min_score must be a number", context={"min_score": min_score}) from exc
    if not np.isfinite(score_floor) or not 0.0 <= score_floor <= 1.0:
        raise UnsolvableError("min_score must be in [0, 1]", context={"min_score": min_score})

    cv2 = _import_cv2(required=normalized_backend == "cv2")
    bg_rgb = decode_image(bg_b64, with_alpha=False)
    piece_rgba = decode_image(piece_b64, with_alpha=True)

    mask_full = _piece_mask(piece_rgba, cv2)
    piece_crop, piece_mask = _crop_piece(piece_rgba, mask_full)
    piece_edges = _edges(
        _to_gray(piece_crop[:, :, :3]),
        cv2=cv2,
        canny_lo=canny_lo,
        canny_hi=canny_hi,
        blur_ksize=blur_ksize,
    )
    if cv2 is not None:
        piece_edges = cv2.bitwise_and(piece_edges, piece_edges, mask=piece_mask)
    else:
        piece_edges = piece_edges * (piece_mask > 0)
    bg_edges = _edges(
        _to_gray(bg_rgb),
        cv2=cv2,
        canny_lo=canny_lo,
        canny_hi=canny_hi,
        blur_ksize=blur_ksize,
    )

    ph, pw = piece_edges.shape[:2]
    bh, bw = bg_edges.shape[:2]
    if pw >= bw or ph >= bh:
        raise UnsolvableError(
            "piece larger than bg after crop",
            context={"piece": (pw, ph), "bg": (bw, bh)},
        )

    scores, matched_with = _score_map(bg_edges, piece_edges, cv2=cv2, backend=normalized_backend)
    cutoff = search_x_min if search_x_min is not None else (piece_width_hint if piece_width_hint is not None else pw)
    cutoff = max(0, min(int(cutoff), scores.shape[1] - 1))
    if cutoff > 0:
        scores[:, :cutoff] = -1.0

    flat_index = int(np.argmax(scores))
    max_y, max_x = divmod(flat_index, scores.shape[1])
    max_val = float(scores[max_y, max_x])

    if debug is not None:
        debug.update(
            score=max_val,
            piece_bbox=(pw, ph),
            bg_shape=(bw, bh),
            cutoff=cutoff,
            backend=matched_with,
            match_xy=(max_x, max_y),
        )

    if max_val < score_floor:
        raise UnsolvableError(
            "gap-match confidence too low",
            context={"score": max_val, "min_score": float(min_score), "backend": matched_with},
        )
    return max_x


__all__ = ["BACKENDS", "MIN_MATCH_SCORE", "decode_image", "detect_gap"]
