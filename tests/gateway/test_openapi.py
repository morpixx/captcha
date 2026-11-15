"""The HTTP surface is a contract with every installed 2captcha client.

This test is deliberately dumb: it only asserts that every documented path is
actually mounted, and that a route that disappears does so on purpose. It needs
no redis, no postgres, and no lifespan — creating the app is enough.
"""

from __future__ import annotations

from captcha.services.gateway.app import create_app

EXPECTED: dict[str, set[str]] = {
    "/createTask": {"post"},
    "/getTaskResult": {"get", "post"},
    "/getBalance": {"get", "post"},
    "/reportCorrect": {"post"},
    "/reportIncorrect": {"post"},
    "/in.php": {"get", "post"},
    "/res.php": {"get", "post"},
    "/healthz": {"get"},
    "/readyz": {"get"},
    "/admin/stats": {"get"},
    "/admin/solvers": {"get"},
}


def _paths() -> dict[str, set[str]]:
    schema = create_app().openapi()
    return {path: {verb.lower() for verb in ops} for path, ops in schema["paths"].items()}


def test_every_documented_path_is_mounted() -> None:
    paths = _paths()
    missing = sorted(set(EXPECTED) - set(paths))
    assert not missing, f"documented but not mounted: {missing}"


def test_documented_verbs_match() -> None:
    paths = _paths()
    wrong = {path: (sorted(verbs), sorted(EXPECTED[path])) for path, verbs in paths.items() if path in EXPECTED and verbs != EXPECTED[path]}
    assert not wrong, f"verb mismatch (mounted, documented): {wrong}"
