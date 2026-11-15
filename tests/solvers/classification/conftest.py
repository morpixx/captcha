"""Pytest fixtures for classification tests."""

from __future__ import annotations

import base64
import io
from typing import Any

import pytest
from _support import FakeBackbone, FakeHead, solver_context
from PIL import Image

from captcha.solvers.classification import base_grid
from captcha.solvers.classification.grid import GridHeadContract, GridHeadSpec
from captcha.solvers.classification.head import clear_head_cache


@pytest.fixture(autouse=True)
def _clear_head_cache() -> Any:
    clear_head_cache()
    yield
    clear_head_cache()


@pytest.fixture
def grid_png_b64() -> Any:
    def make(rows: int = 3, cols: int = 3, tile: int = 112) -> str:
        image = Image.new("RGB", (cols * tile, rows * tile), (80, 80, 80))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    return make


@pytest.fixture
def fake_stack(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch the solver's heavy collaborators with contract-driven fakes."""

    def install(spec: GridHeadSpec, picked: dict[str, list[int]]) -> None:
        class _Loader:
            @classmethod
            def load(
                cls,
                registry: Any,
                *,
                name: str,
                backbone: Any,
                version: str = "latest",
                device: str = "cpu",
                contract: GridHeadContract | None = None,
            ) -> FakeHead:
                assert contract is not None
                assert contract.name == spec.name
                return FakeHead(spec, picked)

        monkeypatch.setattr(base_grid, "LoRAHead", _Loader)
        monkeypatch.setattr(base_grid, "tiles_to_batch", lambda tiles, device="cpu": tiles)
        monkeypatch.setattr(base_grid.ViTBackbone, "get", classmethod(lambda cls, **kwargs: FakeBackbone()))

    return install


@pytest.fixture
def context() -> Any:
    return solver_context
