"""Pytest fixtures for detection tests."""

from __future__ import annotations

from typing import Any

import pytest

from captcha.solvers.detection.detector_loader import clear_detector_cache


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    clear_detector_cache()
    yield
    clear_detector_cache()
