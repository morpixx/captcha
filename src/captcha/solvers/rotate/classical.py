"""Classical angle estimation, no model.

Combines two signals and picks the more confident:

  (a) Hough-line orientation - dominant line direction, folded to 0..90.
  (b) PCA on edge pixels - first principal axis, for short/curved structure.

The 180 degree disambiguator uses the image's radial brightness asymmetry
(sky brighter than ground) only when full_rotation is requested.

OpenCV is required by this path but imported lazily, so the rotate package
still imports in a worker without the cv extra; calling it then raises
ERR_CONFIG instead of ImportError.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from captcha.domain.errors import ConfigError, UnsolvableError


def _import_cv2() -> Any:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ConfigError(
            "opencv required for the classical rotate path",
            context={"install": "pip install captcha[cv]"},
        ) from exc
    return cv2


def estimate_angle(
    rgb: np.ndarray,
    *,
    full_rotation: bool = True,
    canny_lo: int = 60,
    canny_hi: int = 180,
) -> tuple[float, float]:
    """Return (angle_deg, confidence 0..1).

    Angle is how much to ROTATE the image counter-clockwise to restore upright.
    """
    image = np.asarray(rgb)
    if image.ndim != 3 or image.shape[2] != 3:
        raise UnsolvableError("rotate input must be RGB HxWx3", context={"shape": tuple(image.shape)})
    height, width = image.shape[:2]
    if height < 8 or width < 8:
        raise UnsolvableError("rotate input is too small", context={"shape": (width, height)})

    cv2 = _import_cv2()
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, int(canny_lo), int(canny_hi))

    hough_angle, hough_conf = _hough_angle(edges, cv2)
    pca_angle, pca_conf = _pca_angle(edges)

    if hough_conf >= pca_conf and hough_conf > 0.05:
        angle90, confidence = hough_angle, hough_conf
    else:
        angle90, confidence = pca_angle, pca_conf
    if not math.isfinite(angle90):
        raise UnsolvableError("angle estimator produced a non-finite angle", context={})

    if not full_rotation:
        return (angle90 % 360.0, float(confidence))

    candidates = [angle90 % 360.0, (angle90 + 180.0) % 360.0]
    best = candidates[0]
    best_score = -math.inf
    center = (width / 2.0, height / 2.0)
    for candidate in candidates:
        matrix = cv2.getRotationMatrix2D(center, candidate, 1.0)
        rotated = cv2.warpAffine(gray, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        score = float(rotated[: height // 2].mean()) - float(rotated[height // 2 :].mean())
        if score > best_score:
            best_score = score
            best = candidate
    return (float(best), float(confidence))


def _hough_angle(edges: np.ndarray, cv2: Any) -> tuple[float, float]:
    lines = cv2.HoughLinesP(edges, 1, math.pi / 180, 60, minLineLength=20, maxLineGap=6)
    if lines is None or len(lines) == 0:
        return (0.0, 0.0)
    angles: list[float] = []
    weights: list[float] = []
    for x1, y1, x2, y2 in lines[:, 0]:
        dx = float(x2 - x1)
        dy = float(y2 - y1)
        length = math.hypot(dx, dy)
        if length < 8:
            continue
        angle = math.degrees(math.atan2(dy, dx)) % 180.0
        deviation = min(angle, 180.0 - angle, abs(angle - 90.0))
        angles.append(deviation if angle < 90 else -deviation)
        weights.append(length)
    if not angles:
        return (0.0, 0.0)
    values = np.asarray(angles, dtype=np.float64)
    weight_arr = np.asarray(weights, dtype=np.float64)
    median = float(np.average(values, weights=weight_arr))
    confidence = float(min(1.0, weight_arr.sum() / (edges.shape[0] * edges.shape[1] * 0.05)))
    return (median, confidence)


def _pca_angle(edges: np.ndarray) -> tuple[float, float]:
    ys, xs = np.where(edges > 0)
    if xs.size < 50:
        return (0.0, 0.0)
    points = np.stack([xs - xs.mean(), ys - ys.mean()], axis=1).astype(np.float64)
    covariance = (points.T @ points) / points.shape[0]
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    index = int(np.argmax(eigenvalues))
    vx, vy = float(eigenvectors[0, index]), float(eigenvectors[1, index])
    angle = math.degrees(math.atan2(vy, vx)) % 180.0
    deviation = min(angle, 180.0 - angle, abs(angle - 90.0))
    signed = deviation if angle < 90 else -deviation
    ratio = float(eigenvalues[index] / (eigenvalues.sum() + 1e-9))
    confidence = max(0.0, (ratio - 0.5) * 2.0)
    return (signed, confidence)


__all__ = ["estimate_angle"]
