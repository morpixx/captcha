"""Selective answering: the eight-feature rejector, ported for serving.

A hopscotch answer is only worth returning when a calibrated score says it is
probably right. The network predicts; this module decides whether to speak.
Feature order is frozen by .artifact.FEATURES: the release stores per-feature
mean/scale/weight in that order, so a reorder would corrupt scores silently.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from .artifact import HopscotchRelease


@dataclass(frozen=True)
class Observation:
    """What the network said about one wave, before the rejector decides."""

    index: int  # 0-based card index
    foot: tuple[float, float] | None
    features: tuple[float, ...] | None
    valid: bool
    reason: str
    confidence: float | None = None


@dataclass(frozen=True)
class Decision:
    accepted: bool
    score: float | None
    reason: str

    @property
    def abstained(self) -> bool:
        return not self.accepted


def correctness_score(features: Iterable[float], release: HopscotchRelease) -> float:
    """Logistic score over the eight standardized features."""
    standardized = [(float(x) - m) / s for x, m, s in zip(features, release.mean, release.scale, strict=True)]
    logit = release.bias + sum(w * x for w, x in zip(release.weights, standardized, strict=True))
    return 1.0 / (1.0 + math.exp(-max(-700.0, min(700.0, logit))))


def decide(observation: Observation, release: HopscotchRelease | None) -> Decision:
    """Port of hopscotch_v2 rejector.decide: no ready release means no answer."""
    if release is None or not release.ready:
        return Decision(False, None, "uncalibrated")
    if not observation.valid:
        return Decision(False, None, observation.reason or "invalid_observation")
    if observation.features is None:
        return Decision(False, None, "missing_features")
    score = correctness_score(observation.features, release)
    if score < float(release.threshold):
        return Decision(False, score, "low_correctness_score")
    return Decision(True, score, "accepted")


__all__ = ["Decision", "Observation", "correctness_score", "decide"]
