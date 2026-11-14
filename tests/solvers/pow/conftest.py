from __future__ import annotations

import logging

import pytest

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
