"""Shared pytest configuration for the whole suite.

Marker registration lives here so a marker used by one directory does not turn
into a warning when that directory is collected on its own. The integration and
browser suites skip themselves when their infrastructure is absent; see
`tests/integration/conftest.py`.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: needs real Redis and Postgres (tests/integration/docker-compose.yml)",
    )
    config.addinivalue_line(
        "markers",
        "browser: needs a real Camoufox browser (the ci browser job)",
    )
