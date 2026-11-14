"""Classical angle estimation.

Combines two signals and picks the more confident:

  (a) Hough-line orientation. Finds the dominant line direction; rotation
      needed is that direction modulo 90°. Great when the image has obvious
      structure (buildings, horizons, text).
  (b) PCA on edge pixels. SVD on edge-pixel coordinates; the first principal
      axis is the dominant direction. Works when lines are too short/curved
      for Hough but structure still exists.

Returns 0..360 by using the image's radial-darkness asymmetry as the
180° disambiguator: natural photos have brighter sky up top, darker
foreground at the bottom. Only used when the caller treats the task as
full-rotation (0..360). For 90°-step captchas, the 0..90 result is enough.
"""

from __future__ import annotations

import math

import cv2
import numpy as np


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
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, canny_lo, canny_hi)

    hough_angle, hough_conf = _hough_angle(edges)
    pca_angle, pca_conf = _pca_angle(edges)

    if hough_conf >= pca_conf and hough_conf > 0.05:
        angle90 = hough_angle
        conf = hough_conf
    else:
        angle90 = pca_angle
        conf = pca_conf

    if not full_rotation:
        return (angle90 % 360.0, conf)

    # Disambiguate 180° using sky/ground brightness asymmetry after derotation.
    # Rotate by candidate and pick the one whose top half is brighter.
    candidates = [angle90 % 360.0, (angle90 + 180.0) % 360.0]
    best = candidates[0]
    best_score = -math.inf
    cx, cy = w / 2.0, h / 2.0
    for cand in candidates:
        M = cv2.getRotationMatrix2D((cx, cy), cand, 1.0)
        rot = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        top = float(rot[: h // 2].mean())
        bot = float(rot[h // 2 :].mean())
        score = top - bot
        if score > best_score:
            best_score = score
            best = cand
    return (best, conf)


def _hough_angle(edges: np.ndarray) -> tuple[float, float]:
    lines = cv2.HoughLinesP(edges, 1, math.pi / 180, 60, minLineLength=20, maxLineGap=6)
    if lines is None or len(lines) == 0:
        return (0.0, 0.0)
    angles = []
    weights = []
    for x1, y1, x2, y2 in lines[:, 0]:
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length < 8:
            continue
        a = math.degrees(math.atan2(dy, dx)) % 180.0
        # Fold to deviation from nearest axis (0 or 90).
        dev = min(a, 180.0 - a, abs(a - 90.0))
        angles.append(dev if a < 90 else -dev)  # signed deviation, left vs right of vertical
        weights.append(length)
    if not angles:
        return (0.0, 0.0)
    arr = np.asarray(angles, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    med = float(np.average(arr, weights=w))
    # Confidence: normalised line mass, clipped.
    conf = float(min(1.0, w.sum() / (edges.shape[0] * edges.shape[1] * 0.05)))
    return (med, conf)


def _pca_angle(edges: np.ndarray) -> tuple[float, float]:
    ys, xs = np.where(edges > 0)
    if xs.size < 50:
        return (0.0, 0.0)
    pts = np.stack([xs - xs.mean(), ys - ys.mean()], axis=1).astype(np.float64)
    cov = (pts.T @ pts) / pts.shape[0]
    eigvals, eigvecs = np.linalg.eigh(cov)
    # Largest eigenvalue's eigenvector = dominant direction.
    idx = int(np.argmax(eigvals))
    vx, vy = float(eigvecs[0, idx]), float(eigvecs[1, idx])
    a = math.degrees(math.atan2(vy, vx)) % 180.0
    # Fold to nearest axis; sign tells which side of vertical.
    dev = min(a, 180.0 - a, abs(a - 90.0))
    signed = dev if a < 90 else -dev
    ratio = float(eigvals[idx] / (eigvals.sum() + 1e-9))
    conf = max(0.0, (ratio - 0.5) * 2.0)  # 0.5=isotropic, 1.0=perfectly linear
    return (signed, conf)
